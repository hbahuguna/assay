"""Workspace, scoped API-key, and cross-workspace isolation tests."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import cli
from harness.auth import (
    SCOPE_ADMIN,
    SCOPE_EXPORT,
    SCOPE_READ,
    SCOPE_RUN,
    AuthContext,
    AuthError,
    AuthStore,
    normalize_scopes,
)


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.store = AuthStore(self.root)

    def tearDown(self):
        self._temporary.cleanup()

    def _workspace(self, name="Acme", workspace_id=None):
        return self.store.create_workspace(name, workspace_id=workspace_id)

    def _key(self, workspace_id, scopes=(SCOPE_RUN, SCOPE_READ), **kwargs):
        return self.store.create_key(workspace_id, scopes, **kwargs)

    def _assert_no_workspace_data(self, error, *workspace_ids):
        payload = json.dumps(error.to_dict(), sort_keys=True)
        for workspace_id in workspace_ids:
            self.assertNotIn(workspace_id, payload)
        self.assertNotIn("workspace_id", payload)

    def test_key_secret_is_returned_once_and_only_a_hash_is_persisted(self):
        workspace = self._workspace("Acme", "ws_acme")
        issued = self._key(workspace["id"], label="ci")

        self.assertTrue(issued["secret"].startswith(issued["prefix"] + "."))
        self.assertEqual(issued["secret"].split(".", 1)[0], issued["prefix"])
        self.assertNotIn("secret", self.store.list_keys(workspace["id"])[0])

        raw_store = self.store.store_path.read_text(encoding="utf-8")
        raw_audit = self.store.audit_path.read_text(encoding="utf-8")
        self.assertNotIn(issued["secret"], raw_store)
        self.assertNotIn(issued["secret"], raw_audit)
        self.assertNotIn("key_hash", self.store.list_keys(workspace["id"])[0])

        stored = json.loads(raw_store)["keys"][issued["prefix"]]
        self.assertEqual(stored["hash_algorithm"], "sha256")
        self.assertIn("key_hash", stored)
        self.assertIn("salt", stored)
        self.assertNotEqual(stored["key_hash"], issued["secret"])
        self.assertNotIn("secret", stored)

        events = self.store.load_audit()
        created = [event for event in events if event["event"] == "api_key_created"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["key_id"], issued["id"])
        self.assertEqual(created[0]["prefix"], issued["prefix"])
        self.assertNotIn("secret", created[0])
        self.assertNotIn("key_hash", created[0])

        # A second store instance authenticates with the returned secret and
        # still cannot recover it from persisted state.
        restarted = AuthStore(self.root)
        context = restarted.authenticate(issued["secret"], required_scope=SCOPE_RUN)
        self.assertEqual(context.workspace_id, "ws_acme")
        self.assertEqual(context.key_id, issued["id"])
        self.assertNotIn(issued["secret"], restarted.store_path.read_text(encoding="utf-8"))

    def test_missing_expired_revoked_and_wrong_scope_are_distinct_and_data_free(self):
        workspace = self._workspace("Acme", "ws_private")
        active = self._key(workspace["id"], scopes=(SCOPE_RUN,))
        expired = self._key(
            workspace["id"],
            scopes=(SCOPE_RUN,),
            expires_at="2000-01-01T00:00:00Z",
        )
        revoked = self._key(workspace["id"], scopes=(SCOPE_RUN,))
        self.store.revoke_key(revoked["id"], reason="test")

        failures = []
        with self.assertRaises(AuthError) as missing:
            self.store.authenticate("")
        failures.append(missing.exception)
        with self.assertRaises(AuthError) as expired_error:
            self.store.authenticate(expired["secret"])
        failures.append(expired_error.exception)
        with self.assertRaises(AuthError) as revoked_error:
            self.store.authenticate(revoked["secret"])
        failures.append(revoked_error.exception)
        with self.assertRaises(AuthError) as wrong_scope:
            self.store.authenticate(active["secret"], required_scope=SCOPE_EXPORT)
        failures.append(wrong_scope.exception)
        with self.assertRaises(AuthError) as malformed:
            self.store.authenticate("not-a-quality-harness-key")
        failures.append(malformed.exception)
        with self.assertRaises(AuthError) as invalid:
            self.store.authenticate("qh_000000000000.not-the-right-secret")
        failures.append(invalid.exception)

        self.assertEqual(missing.exception.status_code, 401)
        self.assertEqual(missing.exception.code, "missing_api_key")
        self.assertEqual(expired_error.exception.status_code, 401)
        self.assertEqual(expired_error.exception.code, "expired_api_key")
        self.assertEqual(revoked_error.exception.status_code, 401)
        self.assertEqual(revoked_error.exception.code, "revoked_api_key")
        self.assertEqual(wrong_scope.exception.status_code, 403)
        self.assertEqual(wrong_scope.exception.code, "insufficient_scope")
        self.assertEqual(malformed.exception.code, "malformed_api_key")
        self.assertEqual(invalid.exception.code, "invalid_api_key")

        response_keys = {
            (failure.status_code, failure.code) for failure in failures
        }
        self.assertEqual(
            response_keys,
            {
                (401, "missing_api_key"),
                (401, "expired_api_key"),
                (401, "revoked_api_key"),
                (403, "insufficient_scope"),
                (401, "malformed_api_key"),
                (401, "invalid_api_key"),
            },
        )
        for failure in failures:
            self._assert_no_workspace_data(failure, workspace["id"])
            self.assertNotIn("workspace", json.dumps(failure.to_dict(), sort_keys=True))
        for failure in failures:
            expected_auth_header = failure.status_code == 401
            self.assertEqual(
                "WWW-Authenticate" in failure.headers, expected_auth_header
            )

        context = self.store.authenticate(active["secret"])
        with self.assertRaises(AuthError) as context_scope:
            context.require(SCOPE_EXPORT)
        self.assertEqual(context_scope.exception.status_code, 403)
        self.assertNotIn(workspace["id"], json.dumps(context_scope.exception.to_dict()))

    def test_cross_workspace_run_access_is_denied_and_guessed_ids_are_opaque(self):
        first = self._workspace("First", "ws_first")
        second = self._workspace("Second", "ws_second")
        first_key = self._key(first["id"], scopes=(SCOPE_READ,))
        second_key = self._key(second["id"], scopes=(SCOPE_READ,))
        first_context = self.store.authenticate(first_key["secret"])
        second_context = self.store.authenticate(second_key["secret"])

        self.store.claim_run(first["id"], "run-first")
        self.store.claim_run(second["id"], "run-second")

        self.assertEqual(
            self.store.authorize_run(first_context, "run-first")["run_id"], "run-first"
        )
        self.assertEqual(
            self.store.authorize_run(second_context, "run-second")["run_id"],
            "run-second",
        )

        with self.assertRaises(AuthError) as foreign_run:
            self.store.authorize_run(second_context, "run-first")
        with self.assertRaises(AuthError) as guessed_run:
            self.store.authorize_run(second_context, "run-guessed")
        self.assertEqual(foreign_run.exception.status_code, 404)
        self.assertEqual(guessed_run.exception.status_code, 404)
        self.assertEqual(foreign_run.exception.code, "run_not_found")
        self.assertEqual(guessed_run.exception.code, "run_not_found")
        # The caller cannot distinguish another tenant's real run id from a
        # guessed id by status, code, message, or response body.
        self.assertEqual(foreign_run.exception.to_dict(), guessed_run.exception.to_dict())
        self._assert_no_workspace_data(foreign_run.exception, first["id"], second["id"])
        self._assert_no_workspace_data(guessed_run.exception, first["id"], second["id"])

        with self.assertRaises(AuthError) as stolen_claim:
            self.store.claim_run(second["id"], "run-first")
        self.assertEqual(stolen_claim.exception.status_code, 409)
        self.assertEqual(stolen_claim.exception.code, "run_already_claimed")
        self._assert_no_workspace_data(
            stolen_claim.exception, first["id"], second["id"]
        )

        self.assertEqual(self.store.resolve_workspace(second_context), second["id"])
        with self.assertRaises(AuthError) as wrong_workspace:
            self.store.resolve_workspace(second_context, first["id"])
        self.assertEqual(wrong_workspace.exception.status_code, 404)
        self.assertEqual(wrong_workspace.exception.code, "workspace_not_found")
        self._assert_no_workspace_data(
            wrong_workspace.exception, first["id"], second["id"]
        )
        with self.assertRaises(AuthError) as foreign_workspace:
            second_context.require_workspace(first["id"])
        self.assertEqual(foreign_workspace.exception.status_code, 404)
        self._assert_no_workspace_data(
            foreign_workspace.exception, first["id"], second["id"]
        )

    def test_rotation_revokes_old_key_and_returns_new_secret_once(self):
        workspace = self._workspace("Acme", "ws_rotate")
        original = self._key(
            workspace["id"], scopes=(SCOPE_RUN, SCOPE_READ), label="primary"
        )

        replacement = self.store.rotate_key(original["id"])

        self.assertNotEqual(replacement["id"], original["id"])
        self.assertNotEqual(replacement["prefix"], original["prefix"])
        self.assertEqual(replacement["rotated_from"], original["id"])
        self.assertEqual(replacement["workspace_id"], workspace["id"])
        self.assertEqual(replacement["scopes"], [SCOPE_RUN, SCOPE_READ])
        self.assertIn("secret", replacement)

        keys = {record["id"]: record for record in self.store.list_keys(workspace["id"])}
        self.assertIsNotNone(keys[original["id"]]["revoked_at"])
        self.assertEqual(keys[original["id"]]["revoked_reason"], "rotated")
        self.assertNotIn("secret", keys[original["id"]])

        with self.assertRaises(AuthError) as old_key:
            self.store.authenticate(original["secret"])
        self.assertEqual(old_key.exception.code, "revoked_api_key")
        self.assertEqual(
            self.store.authenticate(replacement["secret"]).workspace_id,
            workspace["id"],
        )

        raw_store = self.store.store_path.read_text(encoding="utf-8")
        raw_audit = self.store.audit_path.read_text(encoding="utf-8")
        self.assertNotIn(original["secret"], raw_store)
        self.assertNotIn(replacement["secret"], raw_store)
        self.assertNotIn(original["secret"], raw_audit)
        self.assertNotIn(replacement["secret"], raw_audit)
        events = [event["event"] for event in self.store.load_audit()]
        self.assertEqual(events.count("api_key_created"), 2)
        self.assertEqual(events.count("api_key_revoked"), 1)
        self.assertEqual(events.count("api_key_rotated"), 1)

        with self.assertRaises(AuthError) as rotated_twice:
            self.store.rotate_key(original["id"])
        self.assertEqual(rotated_twice.exception.status_code, 409)
        self.assertEqual(rotated_twice.exception.code, "key_revoked")
        self._assert_no_workspace_data(rotated_twice.exception, workspace["id"])

    def test_scopes_are_validated_and_admin_covers_specific_actions(self):
        workspace = self._workspace("Acme", "ws_scopes")
        with self.assertRaises(ValueError):
            self.store.create_key(workspace["id"], scopes=())
        with self.assertRaises(ValueError):
            self.store.create_key(workspace["id"], scopes=("superuser",))
        with self.assertRaises(ValueError):
            normalize_scopes("run,unknown")

        admin = self._key(workspace["id"], scopes=(SCOPE_ADMIN,))
        admin_context = self.store.authenticate(admin["secret"])
        self.assertTrue(admin_context.has_scope(SCOPE_RUN))
        self.assertTrue(admin_context.has_scope(SCOPE_EXPORT))
        self.assertEqual(
            self.store.authenticate(admin["secret"], required_scope=SCOPE_EXPORT).workspace_id,
            workspace["id"],
        )

    def test_workspace_and_run_ownership_persist_across_store_instances(self):
        first = self._workspace("First", "ws_persist_first")
        second = self._workspace("Second", "ws_persist_second")
        key = self._key(first["id"], scopes=(SCOPE_READ,))
        self.store.claim_run(first["id"], "run-persisted")

        restarted = AuthStore(self.root)
        context = restarted.authenticate(key["secret"])
        self.assertEqual(context.workspace_id, first["id"])
        self.assertEqual(
            restarted.authorize_run(context, "run-persisted")["run_id"], "run-persisted"
        )
        self.assertEqual(restarted.workspace_for_run("run-persisted"), first["id"])
        self.assertEqual({record["id"] for record in restarted.list_keys()}, {key["id"]})
        self.assertEqual(
            {record["id"] for record in restarted.list_workspaces()},
            {first["id"], second["id"]},
        )

    def test_unknown_workspace_and_key_never_return_a_secret(self):
        with self.assertRaises(AuthError) as missing_workspace:
            self.store.create_key("ws_missing", scopes=(SCOPE_RUN,))
        self.assertEqual(missing_workspace.exception.status_code, 404)
        self.assertEqual(missing_workspace.exception.code, "workspace_not_found")
        with self.assertRaises(AuthError) as missing_key:
            self.store.revoke_key("key-missing")
        self.assertEqual(missing_key.exception.status_code, 404)
        self.assertEqual(missing_key.exception.code, "key_not_found")


class AuthCliTests(unittest.TestCase):
    def test_cli_issues_one_secret_and_enforces_scope_and_run_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = str(Path(directory))

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "create-workspace",
                    "--name",
                    "CLI workspace",
                    "--id",
                    "ws_cli",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["workspace"]["id"], "ws_cli")

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "create-key",
                    "--workspace",
                    "ws_cli",
                    "--scope",
                    "run",
                    "--scope",
                    "read",
                    "--label",
                    "cli",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 0, stderr)
            issued = json.loads(stdout)["key"]
            secret = issued["secret"]
            self.assertIn("secret", issued)

            code, stdout, stderr = run_cli(
                ["auth", "list-keys", "--workspace", "ws_cli", "--root", root]
            )
            self.assertEqual(code, 0, stderr)
            listed = json.loads(stdout)["keys"]
            self.assertEqual(len(listed), 1)
            self.assertNotIn("secret", listed[0])
            self.assertNotIn(secret, stdout)

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "authenticate",
                    "--token",
                    secret,
                    "--scope",
                    "run",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["context"]["workspace_id"], "ws_cli")

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "authorize-run",
                    "--token",
                    secret,
                    "--run",
                    "run-cli",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 1)
            payload = json.loads(stdout)
            self.assertEqual(payload["http_status"], 404)
            self.assertEqual(payload["error"]["code"], "run_not_found")

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "claim-run",
                    "--workspace",
                    "ws_cli",
                    "--run",
                    "run-cli",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["run"]["run_id"], "run-cli")

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "authorize-run",
                    "--token",
                    secret,
                    "--run",
                    "run-cli",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["run"]["run_id"], "run-cli")

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "authorize-run",
                    "--token",
                    secret,
                    "--run",
                    "run-guessed",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 1)
            payload = json.loads(stdout)
            self.assertEqual(payload["http_status"], 404)
            self.assertNotIn("ws_cli", stdout)

            code, stdout, stderr = run_cli(
                [
                    "auth",
                    "authenticate",
                    "--token",
                    secret,
                    "--scope",
                    "export",
                    "--root",
                    root,
                ]
            )
            self.assertEqual(code, 1)
            payload = json.loads(stdout)
            self.assertEqual(payload["http_status"], 403)
            self.assertEqual(payload["error"]["code"], "insufficient_scope")
            self.assertNotIn("ws_cli", stdout)

            code, stdout, stderr = run_cli(
                ["auth", "rotate-key", "--key", issued["id"], "--root", root]
            )
            self.assertEqual(code, 0, stderr)
            replacement = json.loads(stdout)["key"]
            self.assertIn("secret", replacement)
            self.assertNotEqual(replacement["secret"], secret)

            code, stdout, stderr = run_cli(
                ["auth", "authenticate", "--token", secret, "--root", root]
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stdout)["error"]["code"], "revoked_api_key")

            code, stdout, stderr = run_cli(
                ["auth", "revoke-key", "--key", replacement["id"], "--root", root]
            )
            self.assertEqual(code, 0, stderr)
            self.assertIn("revoked_at", json.loads(stdout)["key"])
            self.assertNotIn(replacement["secret"], stdout)

    def test_cli_reads_token_from_environment_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = str(Path(directory))
            store = AuthStore(Path(directory))
            workspace = store.create_workspace("Env", workspace_id="ws_env")
            issued = store.create_key(workspace["id"], scopes=(SCOPE_READ,))
            with mock.patch.dict(os.environ, {"QH_TEST_TOKEN": issued["secret"]}):
                code, stdout, stderr = run_cli(
                    [
                        "auth",
                        "authenticate",
                        "--token",
                        "env:QH_TEST_TOKEN",
                        "--root",
                        root,
                    ]
                )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["context"]["workspace_id"], "ws_env")
            self.assertNotIn(issued["secret"], stdout)


class AuthContextTests(unittest.TestCase):
    def test_context_is_redacted_and_scope_aware(self):
        context = AuthContext(
            workspace_id="ws_context",
            key_id="key-context",
            prefix="qh_context",
            scopes=(SCOPE_RUN,),
            authenticated_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual(
            context.to_dict(),
            {
                "workspace_id": "ws_context",
                "key_id": "key-context",
                "prefix": "qh_context",
                "scopes": [SCOPE_RUN],
                "authenticated_at": "2026-01-01T00:00:00Z",
            },
        )
        context.require(SCOPE_RUN)
        with self.assertRaises(AuthError) as error:
            context.require(SCOPE_ADMIN)
        self.assertEqual(error.exception.status_code, 403)
        self.assertNotIn("ws_context", json.dumps(error.exception.to_dict()))


if __name__ == "__main__":
    unittest.main()
