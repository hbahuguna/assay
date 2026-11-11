# Persona contracts

Each Markdown file in this directory is a specialist contract loaded by the
harness. A persona is not a prompt fragment: it declares which domains it owns,
which tools it may use, which checks it can own, the JSON shape of its findings,
and when it must refuse.

## Required shape

1. YAML frontmatter delimited by `---` with non-empty `id`, `name`, `version`,
   `domains`, `tools`, and `checks`.
2. A `## Role` section that states the judgment the specialist makes.
3. A `## Available tools` section with a tool table and explicit boundaries.
4. A `## Output schema` section containing one fenced `json` block. The schema
   must be an object with a non-empty `required` array and `properties`.
5. A `## Blocker schema` section containing one fenced `json` block for the
   structured refusal record. It must include the persona, check id, attempted
   action, boundary, unblock evidence, and coverage status.
6. A `## Refusal handling` section that lists concrete refusal conditions and
   requires a structured blocker instead of a pass.

Every built-in check ID in frontmatter must exist in `harness/checks.py`; a
plugin check ID is resolved through the signed `CheckRegistry` instead. Validate
the whole directory with:

```bash
python -m harness personas --personas-dir personas
```

## Rules

- Ownership lives here, not in the check catalog. Every built-in check must be
  listed by exactly one persona. An unowned check fails planning; duplicate
  ownership is a configuration error rejected before execution.
- One persona owns one check. Do not duplicate a check across personas.
- A plugin check must also be listed by exactly one persona. An unowned or
  multiply owned plugin becomes a blocked coverage gap, never a pass.
- A finding that does not satisfy its persona's output schema is quarantined in
  `invalid_findings` with a redacted payload. It cannot affect scoring or trigger
  the kill switch, and three consecutive invalid findings mark the persona as
  degraded in the report.
- A persona must never convert a blocked or refused check into a passing score.
- A refusal is written as one structured blocker, validated against the
  persona's blocker schema and redacted before it reaches state or the report.
  A blocked check never carries findings; blocker evidence says what would
  unblock it and `covered_elsewhere` says whether another check covers the gap.
- Tools are capabilities, not suggestions. If a tool is not listed, the persona
  may not use it.
- Add a persona only when it owns a distinct risk domain and has a distinct
  output contract. A new file must not exist just to increase the count.
