---
description: Run quality-harness live browser assessment (requires a URL)
---

Run an **agent-driven, interactive** quality-harness browser assessment for: $ARGUMENTS

Act as the Assay Browser agent in this session and continue iteratively without reruns:

1. **Validate target:** require a reachable `http(s)` URL. If missing, explain that `/assay-browser` needs a URL (e.g. `/assay-browser http://localhost:3000`) and that a directory-only run is not browser coverage; stop.
2. **Initial assessment (iteration 1):** from the project directory run the harness with comprehensive browser defaults (crawl, max-pages 8, mobile, headed, active) unless explicitly overridden:
   - `PYTHONPATH=/Users/skaparwan/github/assay QH_SSRF_ALLOWLIST="${QH_SSRF_ALLOWLIST:-127.0.0.0/8,::1,localhost}" /usr/local/bin/python3.11 -P -m harness run --target "<URL>" --personas-dir /Users/skaparwan/github/assay/personas --intent "Assay browser assessment" --crawl --max-pages 8 --mobile --headed --active [extra flags]`
   - Parse JSON stdout as the report; record run_id, status/decision/confidence/coverage, checks (id/status/evidence/routes/notes), findings, evidence dir, limitations.
3. **Test plan:** produce a comprehensive test plan from report/source context (journeys, routes, forms/inputs/validation, CTAs/toggles, auth/stateful flows, search/filters, errors/empty/responsive/a11y/security; note needs for credentials/submission beyond safe probes and URL-only/source-unavailable gaps).
4. **Context checkpoint before every next unique step:** ask the user via OpenCode’s native interactive question flow (priority Jira → Slack → GitHub → Doc → URL/Text) with an explicit **Skip** option. If providing third-party context, ask method (plain text paste, MCP with explicit server/id, or secure env/secret reference for token — never paste secrets). Wait for the answer in-session, record decision/gap, then continue (no rerun).
5. **Next iterations:** execute ONE new unique harness command/step that adds evidence (never repeat commands/args), report what was executed, then return to step 4. Prefer functional/active browser checks for URL targets; safe probes may include toggles/tabs and client-side form fill/validation without submit. Do not submit real forms/transactions, create accounts, brute-force, or access other tenants; auth/submission/multi-step flows require explicit safe test env + authorized credentials/storage-state and allowlisted non-destructive actions or must be reported as limitations.
6. **Finish:** when evidence/gaps are explained, deliver synthesized narrative, test plan, detailed execution (all runs/checks/evidence/limitations), all run IDs, context decisions/assumptions, and developer fix suggestions.

**Invariants:** deterministic harness owns execution/evidence/scoring and phase order; `run` stdout is JSON report; blocked/skipped/errored/refused = coverage gaps (never pass); no repeated commands/tests; no secrets in chat/logs/evidence; URL-only runs may have null source; automated a11y ≠ WCAG; security checks bounded (not pentest). Keep going in this session after each user answer.
