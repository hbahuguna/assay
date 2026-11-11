"""Load and validate specialist persona contracts.

A persona is a Markdown file with simple YAML frontmatter and five required
sections.  The contract is intentionally readable by both humans and agents:
role, tools, output schema, blocker schema, and refusal handling.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import PersonaError


REQUIRED_SECTIONS = [
    "role",
    "available tools",
    "output schema",
    "blocker schema",
    "refusal handling",
]

FRONTMATTER_KEYS = ["id", "name", "version", "domains", "tools", "checks"]

PERSONA_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
CHECK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
DOMAIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")

CORE_FINDING_FIELDS = (
    "title",
    "severity",
    "domain",
    "strength",
    "consequence",
    "location",
    "reproduction",
    "remediation",
    "verification",
)
CORE_BLOCKER_FIELDS = (
    "persona",
    "check_id",
    "attempted_action",
    "boundary",
    "unblock_evidence",
    "covered_elsewhere",
)
SUPPORTED_SCHEMA_TYPES = {
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
}


@dataclass
class Persona:
    id: str
    name: str
    version: str
    domains: List[str]
    tools: List[str]
    checks: List[str]
    path: Path
    role: str
    tools_section: str
    output_schema: Dict[str, Any]
    blocker_schema: Dict[str, Any]
    refusal: str
    body: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "domains": list(self.domains),
            "tools": list(self.tools),
            "checks": list(self.checks),
            "path": str(self.path),
            "schema_title": self.output_schema.get("title", ""),
            "blocker_schema_title": self.blocker_schema.get("title", ""),
        }


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if re.match(r"^-?\d+$", value):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def parse_frontmatter(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PersonaError("Persona must start with a YAML frontmatter block delimited by ---")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise PersonaError("Persona frontmatter is missing a closing ---")
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if raw.lstrip().startswith("- "):
            if current_key is None:
                raise PersonaError("Frontmatter list item without a key: {}".format(raw.strip()))
            item = _parse_scalar(raw.lstrip()[2:])
            if not isinstance(data.get(current_key), list):
                data[current_key] = []
            data[current_key].append(item)
            continue
        if ":" not in raw:
            raise PersonaError("Invalid frontmatter line: {}".format(raw.strip()))
        key, value = raw.split(":", 1)
        current_key = key.strip()
        parsed = _parse_scalar(value)
        if parsed == "":
            data[current_key] = []
        else:
            data[current_key] = parsed
    return data


def _split_sections(body: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE))
    for index, match in enumerate(matches):
        title = match.group(1).strip().lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def _extract_json_schema(
    section: str, path: Path, label: str = "output schema"
) -> Dict[str, Any]:
    match = re.search(r"```json\s*\n(.*?)```", section, re.DOTALL | re.IGNORECASE)
    if not match:
        raise PersonaError("{}: {} section needs a ```json fenced block".format(path, label))
    try:
        schema = json.loads(match.group(1))
    except ValueError as exc:
        raise PersonaError("{}: {} JSON is invalid: {}".format(path, label, exc))
    if not isinstance(schema, dict):
        raise PersonaError("{}: {} must be a JSON object".format(path, label))
    if schema.get("type") != "object":
        raise PersonaError("{}: {} type must be 'object'".format(path, label))
    if not isinstance(schema.get("required"), list) or not schema["required"]:
        raise PersonaError("{}: {} needs a non-empty required array".format(path, label))
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise PersonaError("{}: {} needs a properties object".format(path, label))
    return schema


def load_persona(path: Path) -> Persona:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PersonaError("Could not read persona {}: {}".format(path, exc))
    meta = parse_frontmatter(text)
    for key in ("id", "name", "domains", "tools", "checks"):
        if key not in meta or meta[key] in ("", []):
            raise PersonaError("{}: missing frontmatter key '{}'".format(path, key))
    if not isinstance(meta["domains"], list):
        raise PersonaError("{}: domains must be a list".format(path))
    if not isinstance(meta["tools"], list):
        raise PersonaError("{}: tools must be a list".format(path))
    if not isinstance(meta["checks"], list):
        raise PersonaError("{}: checks must be a list".format(path))
    body_start = text.find("\n---", text.find("---") + 3)
    body = text[body_start + 4 :] if body_start != -1 else text
    sections = _split_sections(body)
    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise PersonaError(
            "{}: missing required section(s): {}".format(path, ", ".join("## " + m.title() for m in missing))
        )
    schema = _extract_json_schema(sections["output schema"], path, "output schema")
    blocker_schema = _extract_json_schema(
        sections["blocker schema"], path, "blocker schema"
    )
    persona = Persona(
        id=str(meta["id"]),
        name=str(meta["name"]),
        version=str(meta.get("version", "1.0")),
        domains=[str(item) for item in meta["domains"]],
        tools=[str(item) for item in meta["tools"]],
        checks=[str(item) for item in meta["checks"]],
        path=path,
        role=sections["role"],
        tools_section=sections["available tools"],
        output_schema=schema,
        blocker_schema=blocker_schema,
        refusal=sections["refusal handling"],
        body=body,
        metadata=dict(meta),
    )
    if not persona.role:
        raise PersonaError("{}: role section is empty".format(path))
    if not persona.refusal:
        raise PersonaError("{}: refusal handling section is empty".format(path))
    return persona


def load_personas(
    directory: Path,
    selected: Optional[Iterable[str]] = None,
    require_minimum: int = 3,
) -> List[Persona]:
    directory = Path(directory)
    if not directory.is_dir():
        raise PersonaError("Personas directory not found: {}".format(directory))
    personas: List[Persona] = []
    seen = set()
    for path in sorted(directory.glob("*.md")):
        if path.name.lower().startswith("readme"):
            continue
        persona = load_persona(path)
        if persona.id in seen:
            raise PersonaError("Duplicate persona id '{}' in {}".format(persona.id, directory))
        seen.add(persona.id)
        personas.append(persona)
    if not personas:
        raise PersonaError("No persona files found in {}".format(directory))
    if selected:
        wanted = [item.strip() for item in selected if item.strip()]
        missing = [item for item in wanted if item not in seen]
        if missing:
            raise PersonaError(
                "Unknown persona id(s): {}. Available: {}".format(
                    ", ".join(missing), ", ".join(sorted(seen))
                )
            )
        personas = [persona for persona in personas if persona.id in wanted]
    if require_minimum and len(personas) < require_minimum and not selected:
        raise PersonaError(
            "Expected at least {} personas, found {}. Add specialist files to personas/.".format(
                require_minimum, len(personas)
            )
        )
    return personas


def select_personas(
    personas: Iterable[Persona], selected: Optional[Iterable[str]] = None
) -> List[Persona]:
    """Return the requested persona subset, preserving contract order."""

    persona_list = list(personas)
    if not selected:
        return persona_list
    wanted = [item.strip() for item in selected if item.strip()]
    if not wanted:
        return persona_list
    available = {persona.id for persona in persona_list}
    missing = [item for item in wanted if item not in available]
    if missing:
        raise PersonaError(
            "Unknown persona id(s): {}. Available: {}".format(
                ", ".join(missing), ", ".join(sorted(available))
            )
        )
    wanted_set = set(wanted)
    return [persona for persona in persona_list if persona.id in wanted_set]


def check_ownership(personas: Iterable[Persona]) -> Dict[str, List[str]]:
    """Map every declared check id to the persona ids that claim it."""

    owners: Dict[str, List[str]] = {}
    for persona in personas:
        for check_id in persona.checks:
            owners.setdefault(check_id, []).append(persona.id)
    return {
        check_id: sorted(persona_ids)
        for check_id, persona_ids in owners.items()
    }


def ownership_error_message(check_id: str, owners: Iterable[str]) -> str:
    """Return a precise, deterministic ownership configuration error."""

    owner_list = sorted(owners)
    if not owner_list:
        return (
            "Check '{}' has no declared persona owner; it must be listed by "
            "exactly one persona contract.".format(check_id)
        )
    return (
        "Check '{}' is claimed by multiple personas ({}); it must be listed "
        "by exactly one persona contract.".format(check_id, ", ".join(owner_list))
    )


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_payload(payload: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate a small, useful subset of JSON Schema.

    The harness intentionally avoids a runtime dependency.  This covers object
    shape, required keys, primitive types, arrays and enums, which is enough for
    specialist output contracts.
    """

    errors: List[str] = []
    expected_type = schema.get("type")
    if expected_type and not _type_matches(payload, expected_type):
        errors.append("{}: expected type {}, got {}".format(path, expected_type, type(payload).__name__))
        return errors
    if "enum" in schema and payload not in schema["enum"]:
        errors.append("{}: value {!r} is not one of {}".format(path, payload, schema["enum"]))
    if isinstance(payload, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in payload:
                errors.append("{}: missing required property '{}'".format(path, key))
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in payload:
                errors.extend(validate_payload(payload[key], child_schema, "{}.{}".format(path, key)))
    if isinstance(payload, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(payload):
            errors.extend(validate_payload(item, schema["items"], "{}[{}]".format(path, index)))
    return errors


def validate_blocker(payload: Any, persona: Persona) -> List[str]:
    """Validate a blocker payload against the persona's blocker contract."""

    return validate_payload(payload, persona.blocker_schema)


def unknown_persona_check_ids(
    personas: Iterable[Persona], known_check_ids: Iterable[str]
) -> List[Tuple[str, str]]:
    """Return (persona id, unknown check id) pairs in deterministic order."""

    known = set(known_check_ids)
    missing: List[Tuple[str, str]] = []
    for persona in personas:
        for check_id in persona.checks:
            if check_id not in known:
                missing.append((persona.id, check_id))
    return missing


def validate_persona_checks(personas: Iterable[Persona], known_check_ids: Iterable[str]) -> None:
    missing = unknown_persona_check_ids(personas, known_check_ids)
    if missing:
        persona_id = missing[0][0]
        check_ids = sorted({check_id for _, check_id in missing})
        raise PersonaError(
            "Persona '{}' references unknown check id(s): {}".format(
                persona_id, ", ".join(check_ids)
            )
        )


# --------------------------------------------------------------------- tooling


def _relative_file(path: Path, directory: Path) -> str:
    try:
        return path.relative_to(directory).as_posix()
    except ValueError:
        return str(path)


def _lint_error_code(message: str) -> str:
    text = message.lower()
    if "missing required section" in text:
        return "missing_section"
    if "frontmatter" in text:
        return "frontmatter"
    if "output schema" in text or "blocker schema" in text:
        return "schema"
    if "section is empty" in text:
        return "empty_section"
    return "invalid_persona"


def _schema_issues(persona: Persona) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    contracts = (
        ("output", persona.output_schema, CORE_FINDING_FIELDS),
        ("blocker", persona.blocker_schema, CORE_BLOCKER_FIELDS),
    )
    for label, schema, core_fields in contracts:
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field_name in core_fields:
            if field_name not in required:
                issues.append(
                    {
                        "code": "schema_required_field_missing",
                        "schema": label,
                        "field": field_name,
                        "persona": persona.id,
                        "message": "{} schema must require field '{}'.".format(
                            label.title(), field_name
                        ),
                    }
                )
        for field_name in required:
            if field_name not in properties:
                issues.append(
                    {
                        "code": "schema_property_not_defined",
                        "schema": label,
                        "field": field_name,
                        "persona": persona.id,
                        "message": "{} schema requires '{}' but does not define it.".format(
                            label.title(), field_name
                        ),
                    }
                )
        for field_name, child in properties.items():
            if not isinstance(child, dict):
                issues.append(
                    {
                        "code": "schema_property_invalid",
                        "schema": label,
                        "field": field_name,
                        "persona": persona.id,
                        "message": "{} schema property '{}' must be an object.".format(
                            label.title(), field_name
                        ),
                    }
                )
                continue
            expected_type = child.get("type")
            if expected_type and expected_type not in SUPPORTED_SCHEMA_TYPES:
                issues.append(
                    {
                        "code": "schema_type_unsupported",
                        "schema": label,
                        "field": field_name,
                        "persona": persona.id,
                        "message": "{} schema property '{}' uses unsupported type '{}'.".format(
                            label.title(), field_name, expected_type
                        ),
                    }
                )
            if "enum" in child and not isinstance(child["enum"], list):
                issues.append(
                    {
                        "code": "schema_enum_invalid",
                        "schema": label,
                        "field": field_name,
                        "persona": persona.id,
                        "message": "{} schema property '{}' enum must be an array.".format(
                            label.title(), field_name
                        ),
                    }
                )
            if "items" in child and not isinstance(child["items"], dict):
                issues.append(
                    {
                        "code": "schema_items_invalid",
                        "schema": label,
                        "field": field_name,
                        "persona": persona.id,
                        "message": "{} schema property '{}' items must be an object.".format(
                            label.title(), field_name
                        ),
                    }
                )
    return issues


def _is_scaffold_placeholder(persona: Persona, check_id: str) -> bool:
    return check_id.startswith("plugin.{}.".format(persona.id))


def lint_personas(
    directory: Path, known_check_ids: Optional[Iterable[str]] = None
) -> Dict[str, Any]:
    """Lint every persona contract without stopping at the first failure."""

    directory = Path(directory)
    known = set(known_check_ids or [])
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    personas: List[Persona] = []
    persona_files: Dict[str, str] = {}
    files_scanned = 0

    if not directory.is_dir():
        errors.append(
            {
                "code": "directory_missing",
                "message": "Personas directory not found: {}".format(directory),
            }
        )
    else:
        files = sorted(
            path
            for path in directory.glob("*.md")
            if not path.name.lower().startswith("readme")
        )
        if not files:
            errors.append(
                {
                    "code": "no_persona_files",
                    "message": "No persona files found in {}".format(directory),
                }
            )
        for path in files:
            file_name = _relative_file(path, directory)
            files_scanned += 1
            try:
                persona = load_persona(path)
            except PersonaError as exc:
                errors.append(
                    {
                        "code": _lint_error_code(str(exc)),
                        "file": file_name,
                        "message": str(exc),
                    }
                )
                continue
            if persona.id in persona_files:
                errors.append(
                    {
                        "code": "duplicate_persona_id",
                        "file": file_name,
                        "persona": persona.id,
                        "message": "Duplicate persona id '{}' (also declared in {}).".format(
                            persona.id, persona_files[persona.id]
                        ),
                    }
                )
                continue
            persona_files[persona.id] = file_name
            personas.append(persona)
            for issue in _schema_issues(persona):
                issue["file"] = file_name
                errors.append(issue)

        ownership = check_ownership(personas)
        persona_map = {persona.id: persona for persona in personas}
        for check_id, owners in sorted(ownership.items()):
            if len(owners) > 1:
                errors.append(
                    {
                        "code": "duplicate_ownership",
                        "check_id": check_id,
                        "owners": owners,
                        "message": ownership_error_message(check_id, owners),
                    }
                )
                continue
            if check_id in known:
                continue
            persona = persona_map.get(owners[0]) if owners else None
            if persona is not None and _is_scaffold_placeholder(persona, check_id):
                warnings.append(
                    {
                        "code": "plugin_check_placeholder",
                        "file": persona_files.get(persona.id, ""),
                        "persona": persona.id,
                        "check_id": check_id,
                        "message": (
                            "Scaffold check '{}' has no installed plugin yet; install a "
                            "signed plugin or replace the check id before running.".format(
                                check_id
                            )
                        ),
                    }
                )
            else:
                errors.append(
                    {
                        "code": "unknown_check",
                        "check_id": check_id,
                        "owners": owners,
                        "message": (
                            "Check '{}' is not in the check registry; it must be a "
                            "built-in check or an installed plugin check.".format(check_id)
                        ),
                    }
                )

    loaded = [
        {
            "id": persona.id,
            "file": persona_files.get(persona.id, ""),
            "domains": list(persona.domains),
            "checks": list(persona.checks),
        }
        for persona in personas
    ]
    return {
        "schema_version": "quality-harness.personas.lint/v1",
        "directory": str(directory),
        "status": "invalid" if errors else "valid",
        "known_check_count": len(known),
        "personas": loaded,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "files": files_scanned,
            "valid_personas": len(personas),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="{}.".format(path.name), suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validated_scaffold_values(
    persona_id: str,
    name: Optional[str],
    domains: Optional[Iterable[str]],
    checks: Optional[Iterable[str]],
) -> Tuple[str, List[str], List[str]]:
    persona_id = (persona_id or "").strip()
    if not PERSONA_ID_PATTERN.match(persona_id):
        raise PersonaError(
            "Persona id must be lowercase letters, numbers, '-', or '_' and start with "
            "a letter."
        )
    clean_name = (name or persona_id.replace("-", " ").replace("_", " ").title()).strip()
    if not clean_name or any(character in clean_name for character in "\r\n"):
        raise PersonaError("Persona name must be non-empty single-line text.")
    domain_values = [str(item).strip() for item in (domains or [persona_id]) if str(item).strip()]
    if not domain_values or any(not DOMAIN_ID_PATTERN.match(item) for item in domain_values):
        raise PersonaError(
            "Persona domains must be lowercase words using letters, numbers, '-', or '_'."
        )
    check_values = [
        str(item).strip() for item in (checks or ["plugin.{}.placeholder".format(persona_id)]) if str(item).strip()
    ]
    if not check_values or any(not CHECK_ID_PATTERN.match(item) for item in check_values):
        raise PersonaError(
            "Persona checks must be non-empty ids using lowercase letters, numbers, "
            "'.', '-', or '_'."
        )
    if len(set(check_values)) != len(check_values):
        raise PersonaError("Persona checks must not contain duplicate ids.")
    return clean_name, domain_values, check_values


def _scaffold_template(
    persona_id: str,
    name: str,
    domains: List[str],
    checks: List[str],
) -> str:
    output_schema = {
        "type": "object",
        "title": "{}Finding".format(name.replace(" ", "")),
        "required": list(CORE_FINDING_FIELDS),
        "properties": {
            "title": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low", "info"],
            },
            "domain": {"type": "string", "enum": domains},
            "strength": {
                "type": "string",
                "enum": ["observed", "source_proven", "suspected"],
            },
            "consequence": {"type": "string"},
            "location": {"type": "string"},
            "reproduction": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
            "remediation": {"type": "string"},
            "verification": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    blocker_schema = {
        "type": "object",
        "title": "{}Blocker".format(name.replace(" ", "")),
        "required": list(CORE_BLOCKER_FIELDS),
        "properties": {
            "persona": {"type": "string"},
            "check_id": {"type": "string"},
            "attempted_action": {"type": "string"},
            "boundary": {"type": "string"},
            "unblock_evidence": {"type": "array", "items": {"type": "string"}},
            "covered_elsewhere": {"type": "boolean"},
            "covered_by": {"type": "array", "items": {"type": "string"}},
        },
    }
    frontmatter = [
        "---",
        "id: {}".format(persona_id),
        "name: {}".format(name),
        "version: 1.0",
        "domains:",
    ]
    frontmatter.extend("  - {}".format(item) for item in domains)
    frontmatter.extend(["tools:", "  - source_inventory", "checks:"])
    frontmatter.extend("  - {}".format(item) for item in checks)
    frontmatter.append("---")
    return "\n".join(frontmatter) + "\n\n" + (
        "## Role\n\n"
        "State the release-risk judgment this specialist owns. Replace this text with\n"
        "the concrete decision the persona makes.\n\n"
        "## Available tools\n\n"
        "| Tool | Use | Boundary |\n"
        "| --- | --- | --- |\n"
        "| `source_inventory` | Read the inspected source inventory. | Read-only. |\n\n"
        "Tools are capabilities, not suggestions. Remove or replace this table before\n"
        "the persona participates in a run.\n\n"
        "## Output schema\n\n"
        "Every finding must validate against this contract.\n\n"
        "```json\n{}\n```\n\n".format(json.dumps(output_schema, indent=2))
        + "## Blocker schema\n\n"
        "Every refusal must validate against this contract.\n\n"
        "```json\n{}\n```\n\n".format(json.dumps(blocker_schema, indent=2))
        + "## Refusal handling\n\n"
        "Refuse and return a structured blocker when the evidence needed for a\n"
        "conclusion is outside the declared tools or scope. State what was attempted,\n"
        "the boundary that stopped it, the evidence that would unblock it, and whether\n"
        "another check covers the gap. Never convert a refusal into a pass.\n"
    )


def scaffold_persona(
    persona_id: str,
    directory: Path,
    name: Optional[str] = None,
    domains: Optional[Iterable[str]] = None,
    checks: Optional[Iterable[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Write a valid starter persona contract atomically."""

    directory = Path(directory)
    clean_name, domain_values, check_values = _validated_scaffold_values(
        persona_id, name, domains, checks
    )
    path = directory / "{}.md".format(persona_id)
    next_steps = [
        "Edit the role and tool boundaries for the specialist.",
        "Replace the scaffold check id with an installed plugin check or an unowned built-in check.",
        "Run 'python -m harness personas lint --personas-dir {}'.".format(directory),
    ]
    if path.exists() and not force:
        return {
            "schema_version": "quality-harness.personas.scaffold/v1",
            "status": "refused",
            "persona_id": persona_id,
            "path": str(path),
            "reason": "Persona file already exists; pass --force to overwrite it.",
            "force": False,
            "checks": check_values,
            "next_steps": ["Re-run with --force to overwrite the existing contract."],
        }
    if path.is_dir():
        raise PersonaError("Persona path is a directory: {}".format(path))
    existed = path.exists()
    template = _scaffold_template(persona_id, clean_name, domain_values, check_values)
    _atomic_write_text(path, template)
    return {
        "schema_version": "quality-harness.personas.scaffold/v1",
        "status": "overwritten" if existed else "created",
        "persona_id": persona_id,
        "path": str(path),
        "force": force,
        "domains": domain_values,
        "checks": check_values,
        "next_steps": next_steps,
    }
