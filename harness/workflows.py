"""Versioned workflow definitions and the dependency-free YAML loader.

The OSS core must not pull in a YAML dependency.  Workflow files therefore use a
deliberately small YAML subset: nested mappings, lists of mappings, and scalar
values.  Anything outside that subset fails with a typed ``WorkflowError``
rather than being silently ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import WorkflowError
from .models import PhaseName

WORKFLOW_DIR = Path(".harness") / "workflows"

PHASE_ORDER: Tuple[str, ...] = (
    PhaseName.RISK_ANALYSIS.value,
    PhaseName.TEST_PLANNING.value,
    PhaseName.EXECUTION.value,
    PhaseName.EVIDENCE_COLLECTION.value,
    PhaseName.CONFIDENCE_SCORING.value,
)

DEFAULT_PHASE_TITLES = {
    PhaseName.RISK_ANALYSIS.value: "Risk Analysis",
    PhaseName.TEST_PLANNING.value: "Test Planning",
    PhaseName.EXECUTION.value: "Execution",
    PhaseName.EVIDENCE_COLLECTION.value: "Evidence Collection",
    PhaseName.CONFIDENCE_SCORING.value: "Confidence Scoring",
}

DEFAULT_PHASE_DESCRIPTIONS = {
    PhaseName.RISK_ANALYSIS.value: (
        "Inspect available artifacts, map intent to consequences, and rank risks."
    ),
    PhaseName.TEST_PLANNING.value: (
        "Select intent-linked checks owned by specialist personas within budget."
    ),
    PhaseName.EXECUTION.value: (
        "Run static Inspect checks and browser Operate checks; halt on a kill-switch finding."
    ),
    PhaseName.EVIDENCE_COLLECTION.value: (
        "Finalize the evidence manifest, hashes, and coverage roll-up."
    ),
    PhaseName.CONFIDENCE_SCORING.value: (
        "Compute a weighted, gap-aware release confidence score and gate result."
    ),
}


@dataclass(frozen=True)
class PhaseDefinition:
    name: str
    title: str
    description: str


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    version: str
    phases: List[PhaseDefinition]
    description: str = ""


def _built_in_workflow() -> Workflow:
    return Workflow(
        id="release-confidence",
        name="Release Confidence",
        version="1.0",
        description="Default five-phase release-confidence workflow.",
        phases=[
            PhaseDefinition(
                name=name,
                title=DEFAULT_PHASE_TITLES[name],
                description=DEFAULT_PHASE_DESCRIPTIONS[name],
            )
            for name in PHASE_ORDER
        ],
    )


RELEASE_CONFIDENCE = _built_in_workflow()
WORKFLOWS: Dict[str, Workflow] = {RELEASE_CONFIDENCE.id: RELEASE_CONFIDENCE}


# --------------------------------------------------------------------------- yaml
_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\d*\.\d+)$")


def _strip_comment(line: str) -> str:
    """Remove an unquoted ``#`` comment from a single line."""

    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif character == quote:
                quote = ""
        elif character in ("'", '"'):
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _tokenize(text: str, path: Path) -> List[Tuple[int, str, int]]:
    tokens: List[Tuple[int, str, int]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        if raw.startswith("\t"):
            raise WorkflowError("{}:{}: tabs are not allowed for indentation".format(path, line_number))
        indent = len(raw) - len(raw.lstrip(" "))
        content = _strip_comment(raw[indent:]).strip()
        if not content or content in ("---", "..."):
            continue
        if raw[indent : indent + 1] == "\t":
            raise WorkflowError("{}:{}: tabs are not allowed for indentation".format(path, line_number))
        tokens.append((indent, content, line_number))
    return tokens


def _parse_scalar(value: str, path: Path, line_number: int) -> Any:
    text = value.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text.startswith(("|", ">", "&", "*", "!", "{", "[")):
        raise WorkflowError(
            "{}:{}: unsupported YAML feature in value {!r}; "
            "use simple mappings, lists, and scalars".format(path, line_number, text)
        )
    if text.startswith('"'):
        if not text.endswith('"') or len(text) < 2:
            raise WorkflowError("{}:{}: unterminated double-quoted string".format(path, line_number))
        try:
            parsed = json.loads(text)
        except ValueError:
            raise WorkflowError("{}:{}: invalid double-quoted string".format(path, line_number))
        return parsed
    if text.startswith("'"):
        if not text.endswith("'") or len(text) < 2:
            raise WorkflowError("{}:{}: unterminated single-quoted string".format(path, line_number))
        return text[1:-1].replace("''", "'")
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _parse_node(
    tokens: List[Tuple[int, str, int]], index: int, indent: int, path: Path
) -> Tuple[Any, int]:
    _, content, _ = tokens[index]
    if content.startswith("- "):
        return _parse_list(tokens, index, indent, path)
    return _parse_map(tokens, index, indent, path)


def _parse_map(
    tokens: List[Tuple[int, str, int]], index: int, indent: int, path: Path
) -> Tuple[Dict[str, Any], int]:
    result: Dict[str, Any] = {}
    while index < len(tokens):
        current_indent, content, line_number = tokens[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise WorkflowError(
                "{}:{}: unexpected indentation; expected {} spaces".format(
                    path, line_number, indent
                )
            )
        if content.startswith("- "):
            raise WorkflowError(
                "{}:{}: a list item cannot appear inside a mapping without a key".format(
                    path, line_number
                )
            )
        match = _KEY_RE.match(content)
        if match is None:
            raise WorkflowError(
                "{}:{}: expected 'key: value', found {!r}".format(path, line_number, content)
            )
        key = match.group(1)
        remainder = match.group(2).strip()
        if key in result:
            raise WorkflowError("{}:{}: duplicate key '{}'".format(path, line_number, key))
        index += 1
        if remainder:
            result[key] = _parse_scalar(remainder, path, line_number)
        elif index < len(tokens) and tokens[index][0] > indent:
            child_indent = tokens[index][0]
            result[key], index = _parse_node(tokens, index, child_indent, path)
        else:
            result[key] = None
    return result, index


def _parse_list(
    tokens: List[Tuple[int, str, int]], index: int, indent: int, path: Path
) -> Tuple[List[Any], int]:
    items: List[Any] = []
    while index < len(tokens):
        current_indent, content, line_number = tokens[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise WorkflowError(
                "{}:{}: unexpected indentation; expected {} spaces".format(
                    path, line_number, indent
                )
            )
        if not content.startswith("- "):
            break
        remainder = content[2:].strip()
        index += 1

        if not remainder:
            if index < len(tokens) and tokens[index][0] > indent:
                child_indent = tokens[index][0]
                item, index = _parse_node(tokens, index, child_indent, path)
            else:
                item = None
            items.append(item)
            continue

        match = None if remainder.startswith(("'", '"')) else _KEY_RE.match(remainder)
        if match is None:
            items.append(_parse_scalar(remainder, path, line_number))
            if index < len(tokens) and tokens[index][0] > indent:
                raise WorkflowError(
                    "{}:{}: a scalar list item cannot contain a nested block".format(
                        path, line_number
                    )
                )
            continue

        key = match.group(1)
        value_text = match.group(2).strip()
        item: Dict[str, Any] = {}
        if value_text:
            item[key] = _parse_scalar(value_text, path, line_number)
        elif index < len(tokens) and tokens[index][0] > indent:
            child_indent = tokens[index][0]
            item[key], index = _parse_node(tokens, index, child_indent, path)
        else:
            item[key] = None

        if index < len(tokens) and tokens[index][0] > indent:
            child_indent = tokens[index][0]
            block, index = _parse_map(tokens, index, child_indent, path)
            for block_key, block_value in block.items():
                if block_key in item:
                    raise WorkflowError(
                        "{}:{}: duplicate key '{}'".format(path, line_number, block_key)
                    )
                item[block_key] = block_value
        items.append(item)
    return items, index


def _parse_yaml(text: str, path: Path) -> Dict[str, Any]:
    tokens = _tokenize(text, path)
    if not tokens:
        raise WorkflowError("{}: workflow file is empty".format(path))
    root_indent = tokens[0][0]
    data, index = _parse_node(tokens, 0, root_indent, path)
    if index != len(tokens):
        _, _, line_number = tokens[index]
        raise WorkflowError("{}:{}: could not parse the rest of the file".format(path, line_number))
    if not isinstance(data, dict):
        raise WorkflowError("{}: the workflow document must be a mapping".format(path))
    return data


# ----------------------------------------------------------------------- validation
_TOP_LEVEL_KEYS = {"schema_version", "id", "name", "version", "description", "phases"}
_PHASE_KEYS = {"name", "title", "description"}
_WORKFLOW_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _require_text(data: Dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("{}: '{}' must be a non-empty string".format(path, key))
    return value.strip()


def _optional_text(data: Dict[str, Any], key: str, path: Path) -> str:
    if key not in data or data[key] is None:
        return ""
    value = data[key]
    if not isinstance(value, str):
        raise WorkflowError("{}: '{}' must be a string".format(path, key))
    return value


def _validate_workflow(data: Dict[str, Any], path: Path) -> Workflow:
    unknown = sorted(set(data) - _TOP_LEVEL_KEYS)
    if unknown:
        raise WorkflowError(
            "{}: unknown workflow key(s): {}".format(path, ", ".join(unknown))
        )

    workflow_id = _require_text(data, "id", path)
    if not _WORKFLOW_ID_RE.match(workflow_id):
        raise WorkflowError(
            "{}: workflow id must match {} (got {!r})".format(
                path, _WORKFLOW_ID_RE.pattern, workflow_id
            )
        )
    name = _require_text(data, "name", path)

    raw_version = data.get("version")
    if isinstance(raw_version, bool) or raw_version is None:
        raise WorkflowError("{}: 'version' must be a non-empty string or number".format(path))
    if isinstance(raw_version, float):
        version = ("%s" % raw_version).strip()
    else:
        version = str(raw_version).strip()
    if not version:
        raise WorkflowError("{}: 'version' must be a non-empty string or number".format(path))

    raw_phases = data.get("phases")
    if not isinstance(raw_phases, list):
        raise WorkflowError("{}: 'phases' must be a list".format(path))
    if len(raw_phases) != len(PHASE_ORDER):
        raise WorkflowError(
            "{}: workflow must define exactly {} phases, found {}".format(
                path, len(PHASE_ORDER), len(raw_phases)
            )
        )

    phases: List[PhaseDefinition] = []
    for position, expected_name in enumerate(PHASE_ORDER):
        item = raw_phases[position]
        if not isinstance(item, dict):
            raise WorkflowError(
                "{}: phase {} must be a mapping".format(path, position + 1)
            )
        unknown_phase_keys = sorted(set(item) - _PHASE_KEYS)
        if unknown_phase_keys:
            raise WorkflowError(
                "{}: phase {} has unknown key(s): {}".format(
                    path, position + 1, ", ".join(unknown_phase_keys)
                )
            )
        phase_name = _require_text(item, "name", path)
        if phase_name not in PHASE_ORDER:
            raise WorkflowError(
                "{}: unknown phase '{}'; allowed phases are: {}".format(
                    path, phase_name, ", ".join(PHASE_ORDER)
                )
            )
        if phase_name != expected_name:
            raise WorkflowError(
                "{}: invalid phase order at position {}: expected '{}', found '{}'".format(
                    path, position + 1, expected_name, phase_name
                )
            )
        title = _require_text(item, "title", path)
        description = _optional_text(item, "description", path)
        phases.append(PhaseDefinition(name=phase_name, title=title, description=description))

    return Workflow(
        id=workflow_id,
        name=name,
        version=version,
        phases=phases,
        description=_optional_text(data, "description", path),
    )


# -------------------------------------------------------------------------- loading
def load_workflow_file(path: Path) -> Workflow:
    """Load and validate one YAML workflow file."""

    workflow_path = Path(path)
    try:
        text = workflow_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError("Could not read workflow file {}: {}".format(workflow_path, exc))
    return _validate_workflow(_parse_yaml(text, workflow_path), workflow_path)


def load_workflows(directory: Optional[Path]) -> Dict[str, Workflow]:
    """Load every ``*.yaml``/``*.yml`` file in a directory.

    A missing directory is not an error: it simply means there is no override.
    A present-but-malformed file is an error so operators do not unknowingly run
    the built-in workflow after a bad edit.
    """

    if directory is None:
        return {}
    workflow_dir = Path(directory)
    if not workflow_dir.exists():
        return {}
    if not workflow_dir.is_dir():
        raise WorkflowError("Workflow path is not a directory: {}".format(workflow_dir))

    loaded: Dict[str, Workflow] = {}
    for path in sorted(workflow_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".yaml", ".yml"):
            continue
        workflow = load_workflow_file(path)
        if workflow.id in loaded:
            raise WorkflowError(
                "Duplicate workflow id '{}' in {} and {}".format(
                    workflow.id, loaded[workflow.id], path
                )
            )
        loaded[workflow.id] = workflow
    return loaded


def get_workflow(workflow_id: str = RELEASE_CONFIDENCE.id, workflows_dir: Optional[Path] = None) -> Workflow:
    """Resolve a workflow, preferring a versioned override when one exists."""

    overrides = load_workflows(workflows_dir)
    if workflow_id in overrides:
        return overrides[workflow_id]
    if workflow_id in WORKFLOWS:
        return WORKFLOWS[workflow_id]

    available = sorted(set(WORKFLOWS) | set(overrides))
    raise WorkflowError(
        "Unknown workflow '{}'. Available: {}".format(workflow_id, ", ".join(available))
    )
