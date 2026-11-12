"""Evidence storage with hashes and a manifest.

Every artifact is written below ``.harness/evidence/<run_id>/``.  Paths in the
report are relative to the project root so reports remain portable when the
project directory moves.  The manifest is the audit surface: a reviewer can
re-hash every artifact and detect silent edits.  During execution the manifest
is checkpointed atomically after each completed check so resume can retain
evidence from work that already finished.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import CheckStatus, utc_now
from .metering import EVENT_EVIDENCE_BYTE_STORED


def _slug(value: str, fallback: str = "artifact") -> str:
    value = value.strip().replace("\\", "/")
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = value.strip("._-")
    return value or fallback


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceStore:
    def __init__(
        self,
        root: Path,
        run_id: str,
        metering: Any = None,
        signing_key: Optional[Union[bytes, str, Path]] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.run_id = run_id
        self.metering = metering
        self.metering_errors: List[str] = []
        self.run_dir = self.root / ".harness" / "evidence" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.run_dir / "manifest.json"
        self.entries: List[Dict[str, Any]] = []
        self._finalized = False
        self.signing_key_path = self.root / ".harness" / "evidence-signing.key"
        self.signing_key = self._resolve_signing_key(signing_key)
        self._load_existing_manifest()

    def _resolve_signing_key(self, signing_key: Optional[Union[bytes, str, Path]]) -> bytes:
        if signing_key is None:
            # The OSS core must remain deterministic across runs with the same
            # run id.  The default key is therefore derived from the run id
            # rather than from OS entropy; callers can still inject a secret key.
            return hashlib.sha256(
                b"quality-harness-evidence:" + str(self.run_id).encode("utf-8")
            ).digest()
        if isinstance(signing_key, (str, Path)):
            path = Path(signing_key)
            if path.exists():
                return path.read_bytes()
            return str(signing_key).encode("utf-8")
        return bytes(signing_key)

    def _sign_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        payload = {key: value for key, value in manifest.items() if key not in ("signature", "signature_algorithm", "signed_at")}
        signature = hmac.new(self.signing_key, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"), hashlib.sha256).hexdigest()
        signed = dict(payload)
        signed["signature_algorithm"] = "HMAC-SHA256"
        signed["signed_at"] = utc_now()
        signed["signature"] = signature
        return signed

    def _verify_signature(self, manifest: Dict[str, Any]) -> bool:
        signature = manifest.get("signature")
        if not isinstance(signature, str) or not signature:
            return False
        payload = {key: value for key, value in manifest.items() if key not in ("signature", "signature_algorithm", "signed_at")}
        expected = hmac.new(self.signing_key, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def _load_existing_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            entries = data.get("evidence", [])
            if isinstance(entries, list):
                self.entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("path")]
        except (OSError, ValueError):
            self.entries = []

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def _target(self, name: str, default_suffix: str = "") -> Path:
        safe = _slug(name)
        if default_suffix and not safe.endswith(default_suffix):
            safe = safe + default_suffix
        candidate = self.run_dir / safe
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while True:
            alternate = self.run_dir / "{}-{}{}".format(stem, index, suffix)
            if not alternate.exists():
                return alternate
            index += 1

    def _record(
        self,
        path: Path,
        kind: str,
        description: str = "",
        check_id: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        data = path.read_bytes()
        entry = {
            "path": self._relative(path),
            "kind": kind,
            "description": description,
            "check_id": check_id,
            "bytes": len(data),
            "sha256": sha256_bytes(data),
            "content_type": mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            "created_at": utc_now(),
        }
        if extra:
            entry.update(extra)
        # Replacing the same logical artifact keeps the manifest small.
        self.entries = [item for item in self.entries if item.get("path") != entry["path"]]
        self.entries.append(entry)
        if self.metering is not None:
            try:
                self.metering.emit(
                    EVENT_EVIDENCE_BYTE_STORED,
                    quantity=int(entry.get("bytes", 0)),
                    unit="byte",
                    idempotency_key="{}:{}:{}:{}".format(
                        self.run_id,
                        EVENT_EVIDENCE_BYTE_STORED,
                        entry.get("path", ""),
                        entry.get("sha256", ""),
                    ),
                    metadata={
                        "kind": entry.get("kind", ""),
                        "path": entry.get("path", ""),
                        "sha256": entry.get("sha256", ""),
                        "check_id": entry.get("check_id", ""),
                    },
                )
            except Exception as exc:  # Metering must not discard stored evidence.
                self.metering_errors.append(str(exc))
        return entry["path"]

    def save_text(
        self,
        name: str,
        text: str,
        kind: str = "text",
        description: str = "",
        check_id: str = "",
        suffix: str = ".txt",
    ) -> str:
        path = self._target(name, suffix)
        path.write_text(text, encoding="utf-8")
        return self._record(path, kind, description, check_id)

    def save_json(
        self,
        name: str,
        payload: Any,
        kind: str = "json",
        description: str = "",
        check_id: str = "",
    ) -> str:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        return self.save_text(name, text, kind=kind, description=description, check_id=check_id, suffix=".json")

    def save_bytes(
        self,
        name: str,
        data: bytes,
        kind: str = "binary",
        description: str = "",
        check_id: str = "",
        suffix: str = ".bin",
    ) -> str:
        path = self._target(name, suffix)
        path.write_bytes(data)
        return self._record(path, kind, description, check_id)

    def save_screenshot(
        self, name: str, data: bytes, description: str = "", check_id: str = ""
    ) -> str:
        suffix = ".png"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            suffix = ".png"
        elif data[:3] == b"\xff\xd8\xff":
            suffix = ".jpg"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            suffix = ".webp"
        return self.save_bytes(
            name,
            data,
            kind="screenshot",
            description=description,
            check_id=check_id,
            suffix=suffix,
        )

    def save_console_log(self, entries: List[Dict[str, Any]], check_id: str = "") -> str:
        return self.save_json(
            "console-log",
            entries,
            kind="console_log",
            description="Browser console messages and page errors",
            check_id=check_id,
        )

    def save_network_log(self, entries: List[Dict[str, Any]], check_id: str = "") -> str:
        return self.save_json(
            "network-log",
            entries,
            kind="network_log",
            description="Browser network requests and responses",
            check_id=check_id,
        )

    def summary(self) -> Dict[str, Any]:
        by_kind: Dict[str, int] = {}
        total_bytes = 0
        for entry in self.entries:
            by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
            total_bytes += int(entry.get("bytes", 0))
        return {
            "directory": self._relative(self.run_dir),
            "count": len(self.entries),
            "by_kind": by_kind,
            "total_bytes": total_bytes,
            "manifest": self._relative(self.run_dir / "manifest.json"),
        }

    def checkpoint(self) -> None:
        """Persist the current evidence index after a completed check.

        The manifest is rewritten atomically so a crash cannot leave a partial
        index.  The evidence phase later replaces this provisional manifest with
        the final one containing run metadata.
        """

        if self._finalized:
            return
        manifest = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "generated_at": utc_now(),
            "checkpoint": True,
            "evidence": list(self.entries),
            "summary": self.summary(),
        }
        manifest = self._sign_manifest(manifest)
        self._write_manifest(manifest)

    def reconcile(self, checks: List[Any]) -> None:
        """Align the loaded evidence index with the checkpointed run state.

        A crash can happen between the evidence checkpoint and the state write,
        or between the state write and the evidence checkpoint.  Entries owned
        by a check that will rerun are discarded; artifacts referenced by a
        completed check but missing from a damaged manifest are re-registered
        from the file on disk.
        """

        rerun_ids = {
            check.id
            for check in checks
            if check.status
            in (
                CheckStatus.PLANNED.value,
                CheckStatus.RUNNING.value,
                CheckStatus.ERROR.value,
            )
        }
        self.entries = [
            entry
            for entry in self.entries
            if str(entry.get("check_id", "")) not in rerun_ids
        ]
        known_paths = {str(entry.get("path", "")) for entry in self.entries}
        for check in checks:
            if check.id in rerun_ids:
                continue
            for path in check.evidence:
                if not path or path in known_paths:
                    continue
                recovered = self._recover_entry(path, check.id)
                if recovered is not None:
                    self.entries.append(recovered)
                    known_paths.add(path)
        self.checkpoint()

    def _recover_entry(self, path: str, check_id: str) -> Optional[Dict[str, Any]]:
        artifact = Path(path)
        if not artifact.is_absolute():
            artifact = self.root / artifact
        if not artifact.exists() or not artifact.is_file():
            return None
        try:
            data = artifact.read_bytes()
        except OSError:
            return None
        return {
            "path": self._relative(artifact),
            "kind": "recovered",
            "description": "Recovered from the last completed-check checkpoint.",
            "check_id": check_id,
            "bytes": len(data),
            "sha256": sha256_bytes(data),
            "content_type": mimetypes.guess_type(str(artifact))[0] or "application/octet-stream",
            "created_at": utc_now(),
        }

    def _write_manifest(self, manifest: Dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest, indent=2, ensure_ascii=False)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.manifest_path.name + ".",
            suffix=".tmp",
            dir=str(self.manifest_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, str(self.manifest_path))
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def finalize(self, run_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._finalized:
            # Manifest excludes itself to avoid a recursive hash.
            manifest = {
                "schema_version": "1.0",
                "run_id": self.run_id,
                "generated_at": utc_now(),
                "run": run_metadata or {},
                "evidence": list(self.entries),
                "summary": self.summary(),
            }
            manifest = self._sign_manifest(manifest)
            self._write_manifest(manifest)
            self._finalized = True
            manifest["manifest_path"] = self._relative(self.manifest_path)
            manifest["manifest_sha256"] = sha256_file(self.manifest_path)
            return manifest
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["manifest_path"] = self._relative(self.manifest_path)
        manifest["manifest_sha256"] = sha256_file(self.manifest_path)
        return manifest

    def verify(self) -> Dict[str, Any]:
        """Re-hash artifacts, verify the manifest signature, and report tampering."""

        manifest = {}
        signature_valid = False
        signature_error = ""
        if self.manifest_path.exists():
            try:
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                signature_valid = self._verify_signature(manifest)
                if not signature_valid:
                    signature_error = "manifest signature is missing or invalid"
            except (OSError, ValueError) as exc:
                signature_error = "manifest is unreadable: {}".format(exc)
        else:
            signature_error = "manifest is missing"

        # Prefer the persisted manifest entries when verifying.
        entries = manifest.get("evidence", self.entries)
        if not isinstance(entries, list):
            entries = self.entries

        checked = 0
        mismatches: List[Dict[str, Any]] = []
        missing: List[str] = []
        affected: List[Dict[str, Any]] = []
        path_to_entry = {
            str(entry.get("path", "")): entry for entry in entries if isinstance(entry, dict)
        }
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            path_text = str(entry["path"])
            path = self.root / path_text
            if not path.exists():
                missing.append(path_text)
                affected.append({"path": path_text, "check_id": entry.get("check_id", ""), "reason": "missing"})
                continue
            checked += 1
            actual = sha256_file(path)
            if actual != entry.get("sha256"):
                mismatches.append(
                    {
                        "path": path_text,
                        "check_id": entry.get("check_id", ""),
                        "expected": entry.get("sha256"),
                        "actual": actual,
                    }
                )
                affected.append({"path": path_text, "check_id": entry.get("check_id", ""), "reason": "hash_mismatch"})

        if signature_error:
            affected.append({"path": self._relative(self.manifest_path), "check_id": "", "reason": "signature_invalid"})

        tampered = bool(signature_error or missing or mismatches)
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "checked": checked,
            "missing": missing,
            "mismatches": mismatches,
            "signature_valid": signature_valid,
            "signature_error": signature_error,
            "tampered": tampered,
            "affected": affected,
            "alert": {
                "level": "error" if tampered else "ok",
                "message": (
                    "Evidence tampering detected: {} affected artifact(s).".format(len(affected))
                    if tampered
                    else "Evidence verification passed."
                ),
            },
        }
