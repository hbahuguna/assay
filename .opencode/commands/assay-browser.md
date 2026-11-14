---
description: Run quality-harness live browser assessment (requires a URL)
---

Run an **agent-driven, interactive** quality-harness browser assessment for: $ARGUMENTS

Act as the Assay Browser agent in this session and continue iteratively without reruns.

## 1) Validate target
Require a reachable `http(s)` URL. If missing, explain that `/assay-browser` needs a URL (e.g. `/assay-browser http://localhost:3000`) and that a directory-only run is not browser coverage; stop.

## 2) Initial assessment (iteration 1)
From the project directory run the harness with comprehensive browser defaults (crawl, max-pages 8, mobile, headed, active) unless explicitly overridden:

```
PYTHONPATH=/Users/skaparwan/github/assay QH_SSRF_ALLOWLIST="${QH_SSRF_ALLOWLIST:-127.0.0.0/8,::1,localhost}" /usr/local/bin/python3.11 -P -m harness run --target "<URL>" --personas-dir /Users/skaparwan/github/assay/personas --intent "Assay browser assessment" --crawl --max-pages 8 --mobile --headed --active [extra flags]
```

Parse JSON stdout as the report. Record run_id, status/decision/confidence/coverage, checks (id/status/evidence/routes/notes), findings, evidence directory, and limitations.

## 3) Test plan
Produce a comprehensive test plan from report/source context (journeys, routes, forms/inputs/validation, CTAs/toggles, auth/stateful flows, search/filters, errors/empty/responsive/a11y/security; note needs for credentials/submission beyond safe probes and URL-only/source-unavailable gaps).

## 4) Context gathering UX (native `question` tool — required)
Use OpenCode's native `question` tool for **every** context request. The tool renders the native question dialog (options/tabs plus a custom answer box); use it and do not recreate it with shell or prose. Never ask for context in plain prose. Never run `echo`, `true`, `sleep`, `cat <<EOF`, or any other shell command to wait, print separators, or pause. Never block on stdin. After the user answers, immediately ask the next question in the same tool flow or continue executing; do not end the turn until you either need another answer via `question` or have completed the step. A response that asks the user for context without a `question` tool call is invalid; retry by calling the tool.

Sequential flow, one decision per question:

1. **Source** — ask with options `Jira`, `Slack`, `GitHub`, `Doc`, `URL/Text`, `Skip`.
   - If `Skip`: record "context skipped" and continue with the next unique safe harness step.
2. **Method** (only if a source was chosen) — ask with options `Paste` (recommended), `MCP`, `Secure env/secret ref`.
3. **Collect details** (one question at a time):
   - **Paste:** ask the user to paste the relevant story/AC/thread/notes in the custom answer box. Redact secrets before storing, logging, or summarizing.
   - **MCP:** ask for the exact MCP server name and resource/identifier in the custom answer box. Confirm the resource before use.
   - **Secure env/secret ref:** ask for the environment variable or secret reference name **only**. Never ask for or accept a raw token/secret. Include a `Skip` option if not configured. If the user pastes a raw secret/token anyway, do not store or echo it; ask them to provide an env/secret reference instead.
4. **Identifier** — when relevant, ask for the specific ID/URL (Jira key/URL, Slack thread, GitHub issue/PR, doc URL) in a separate question.
5. **Confirm** — ask `Continue with the next unique safe harness step?` with options `Continue`, `Add another context source`, `Skip context`. If `Add another context source`, repeat from step 1.
6. **Record a redacted decision** — source, method, identifier, short summary, and whether it was skipped. Never store secrets.
7. **If the question tool is unavailable or errors**, state that blocker explicitly, record the gap, and continue with safe unique steps. Never simulate a user answer.

Do not stop after asking a question. Wait for the tool result, consume the answer, and continue in the same session. The user must not need to rerun a command or restate the goal.

## 5) Next iterations
Execute ONE new unique harness command/step that adds evidence (never repeat commands/args), report what was executed, then return to the context checkpoint. Prefer functional/active browser checks for URL targets; safe probes may include toggles/tabs and client-side form fill/validation without submit. Do not submit real forms/transactions, create accounts, brute-force, or access other tenants; auth/submission/multi-step flows require explicit safe test env + authorized credentials/storage-state and allowlisted non-destructive actions or must be reported as limitations.

## 6) Finish
When evidence/gaps are explained, deliver a synthesized narrative, the test plan, detailed execution (all runs/checks/evidence/limitations), all run IDs, context decisions/assumptions, and developer fix suggestions.

## Invariants
Deterministic harness owns execution/evidence/scoring and phase order; `run` stdout is JSON report; blocked/skipped/errored/refused = coverage gaps (never pass); no repeated commands/tests; no secrets in chat/logs/evidence; URL-only runs may have null source; automated a11y ≠ WCAG; security checks bounded (not pentest). Keep going in this session after each user answer.
