"""Load and validate specialist persona contracts.

A persona is a Markdown file with simple YAML frontmatter and five required
sections.  The contract is intentionally readable by both humans and agents:
role, tools, output schema, blocker schema, and refusal handling.
"""

from __future__ import annotations

import json
import re
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
