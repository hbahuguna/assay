import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { createHash } from "node:crypto"

import { assayInvocation, browserInvocation, demoInvocation } from "./command-args.ts"
import type { Invocation } from "./command-args.ts"

export interface AgentDeps {
  runHarness: (args: string[], env?: NodeJS.ProcessEnv) => Promise<string>
  askLlm: (prompt: string) => Promise<string>
  progress?: (message: string) => void
  sendFinal: (text: string) => Promise<void>
  listMcpResources?: () => Promise<string[]>
  /** Ask the user for context in-session and resolve with their reply text (no rerun). */
  requestUserInput?: (prompt: string) => Promise<string>
}

export interface AgentOptions {
  goal: string
  personasDir: string
  initialArgs?: string[]
  initialEnv?: NodeJS.ProcessEnv
  maxIterations?: number
  /** Minimum number of harness runs before the agent may finish. Defaults to 1. */
  minHarnessRuns?: number
  cwd?: string
  /** Raw user input for this invocation (used to resume with context/skip/method). */
  userInput?: string
  /** Stable key for persisted agent state across invocations. */
  stateKey?: string
  /** Session ID for interactive resume in chat. */
  sessionID?: string
  /** Keep running and wait for in-session replies instead of returning on context requests. */
  interactive?: boolean
}

const DEFAULT_MAX_ITERATIONS = 7
const DEFAULT_MIN_HARNESS_RUNS = 1

const CONTEXT_TYPES = ["jira", "slack", "github", "doc", "url", "text"] as const
const METHODS = ["paste", "mcp", "token"] as const
const SENSITIVE_ASSIGNMENT = /\b(authorization|cookie|token|secret|password|api[_-]?key|session)\b\s*[:=]\s*([^\s,;"']+)/gi
const PRIVATE_KEY_BLOCK = /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----/g

function redactTranscriptText(value: string): string {
  return String(value || "")
    .replace(PRIVATE_KEY_BLOCK, "[REDACTED PRIVATE KEY]")
    .replace(SENSITIVE_ASSIGNMENT, (_m, key) => `${key}=[REDACTED]`)
}

type Pending =
  | { kind: "need_context"; type?: string; question?: string; requested?: string[]; reason?: string }
  | { kind: "need_method"; type: string; question?: string; requested?: string[]; reason?: string }

interface AgentState {
  version: 1
  key: string
  goal: string
  sessionID?: string
  initialArgs?: string[]
  testPlan: string
  executedCommands: string[]
  runIds: string[]
  runSummaries: string[]
  currentReport: string
  contextLog: string[]
  pending: Pending | null
  step: number
  initialDone: boolean
}

function statePath(cwd: string | undefined, key: string): string | null {
  if (!cwd) return null
  const hash = createHash("sha256").update(key).digest("hex").slice(0, 16)
  return join(cwd, ".harness", "assay-agent", `${hash}.json`)
}

function loadState(cwd: string | undefined, key: string): AgentState | null {
  const p = statePath(cwd, key)
  if (!p) return null
  try {
    const raw = JSON.parse(readFileSync(p, "utf8"))
    if (raw && raw.version === 1 && raw.key === key) return raw as AgentState
  } catch {}
  return null
}

function saveState(cwd: string | undefined, state: AgentState): void {
  const p = statePath(cwd, state.key)
  if (!p) return
  try {
    mkdirSync(dirname(p), { recursive: true })
    writeFileSync(p, JSON.stringify(state, null, 2))
  } catch {}
}

function formatNeedContext(pending: Pending, mcpResources: string[], goal: string): string {
  const requestedType = String(pending.type || "jira").toLowerCase()
  const type = (CONTEXT_TYPES as readonly string[]).includes(requestedType) ? requestedType : "text"
  const question = String(pending.question || "Additional context is needed to refine the test plan and decide the next unique test.")
  const requested = Array.isArray(pending.requested) && pending.requested.length
    ? pending.requested.map((x: any) => `- ${String(x)}`)
    : ["- Relevant acceptance criteria, user flows, expected behavior, or constraints"]
  const reason = String(pending.reason || "Current evidence is insufficient to plan precise functional verification.")
  const mcpSection = mcpResources.length
    ? `\n## Available MCP resources (if relevant)\n${mcpResources.map((r) => `- ${r}`).join("\n")}`
    : `\n## Available MCP resources\n- None discovered in this session.`

  if (pending.kind === "need_method") {
    return `## How would you like to provide ${type} context?

**Goal/intent:** ${goal}
**Source selected:** ${type}
**Why:** ${reason}

**Question:** ${question}

## Suggested details
${requested.join("\n")}
${mcpSection}

## Choose a method (reply in this chat)
1. **Plain text paste (recommended):** reply with \`[source:${type} method:paste] <paste the context here>\`
2. **Connect via MCP:** reply with \`[source:${type} method:mcp server:<name> id:<identifier>]\` (confirm which MCP resource/server to use; provide exact ID/URL)
3. **Safely submitted API token (opt-in):** reply with \`[source:${type} method:token env:<ENV_VAR_OR_SECRET_REF> id:<identifier>]\` — do **not** paste tokens/secrets into chat. Use a secure local env/secret reference configured for the connector. If not configured, choose paste or MCP.
4. **Skip this context:** reply with \`[context:skip]\`

> Reply here to continue in this session. You can also rerun \`/assay ${goal} ...\` with the same tag if needed. After your response I will proceed to the next unique test step and ask for context again. No previously executed test/command will be repeated.`
  }

  return `## Context requested before next iteration (same intent)

**Goal/intent:** ${goal}
**Suggested source (priority):** ${type} (order: Jira → Slack → GitHub → Doc → URL/Text)
**Why:** ${reason}

**Question:** ${question}

## Suggested details
${requested.join("\n")}
${mcpSection}

## Your choice (reply in this chat)
1. **Skip this context:** reply with \`[context:skip]\` — proceed without this context; gaps/assumptions will be recorded explicitly.
2. **Provide context** (choose source: Jira/Slack/GitHub/Doc/URL/Text). Reply with the source and I’ll ask how to proceed (plain text paste, MCP, or safely submitted API token), e.g.:
   - \`[source:jira] <brief note>\`
   - \`[source:jira method:paste] <paste AC/story>\`
   - \`[source:slack method:mcp server:<name> id:<thread>]\`

## Method options when providing third-party context
- **Plain text paste (recommended):** paste relevant story/AC/thread/notes directly.
- **Connect via MCP:** provide exact identifier and confirm MCP resource/server.
- **Safely submitted API token (opt-in):** provide a secure env/secret reference only (never paste secrets).

> Reply here to continue in this session (or rerun \`/assay ${goal} ...\` with the same tag if needed). After your response I will proceed to the next unique test step (or record skip) and ask for context again. No previously executed test/command will be repeated.`
}

function extractRunIds(output: string): string[] {
  const ids: string[] = []
  const pattern = /"run_id"\s*:\s*"([^"]+)"/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(output)) !== null) {
    if (!ids.includes(match[1])) ids.push(match[1])
  }
  return ids
}

function parseJsonOutput(output: string): any | null {
  const trimmed = (output || "").trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    const m = trimmed.match(/\{[\s\S]*\}/)
    if (!m) return null
    try {
      return JSON.parse(m[0])
    } catch {
      return null
    }
  }
}

async function discoverMcpResources(deps: AgentDeps): Promise<string[]> {
  try {
    if (deps.listMcpResources) {
      const items = await deps.listMcpResources()
      return (items || []).map((x) => redactTranscriptText(String(x))).slice(0, 30)
    }
  } catch {}
  return []
}

function summarizeRunOutput(output: string): string {
  const report = parseJsonOutput(output)
  if (!report || typeof report !== "object") {
    return (output || "").slice(0, 1200)
  }
  const runId = report.run_id || "unknown"
  const status = report.status || "unknown"
  const decision = report.decision?.decision || report.decision || "n/a"
  const score = report.confidence?.score
  const coverage = report.confidence?.coverage
  const lines: string[] = [`Run ${runId}: status=${status}, decision=${decision}`]
  if (score !== undefined || coverage !== undefined) {
    lines.push(`  confidence=${score ?? "n/a"} coverage=${coverage ?? "n/a"}`)
  }
  const checks = Array.isArray(report.checks) ? report.checks : []
  if (checks.length) {
    const counts: Record<string, number> = {}
    for (const c of checks) {
      const s = String(c?.status || "unknown")
      counts[s] = (counts[s] || 0) + 1
    }
    lines.push(`  checks=${checks.length} (${Object.entries(counts).map(([k, v]) => `${k}:${v}`).join(", ")})`)
    for (const c of checks) {
      const meta = c?.metadata || {}
      const routes = meta.routes || meta.route || meta.crawled ? ` routes=${meta.page_count || (Array.isArray(meta.routes) ? meta.routes.length : "") || 1}` : ""
      const ev = Array.isArray(c?.evidence) ? ` evidence=${c.evidence.length}` : ""
      const lim = c?.skip_reason ? ` note=${String(c.skip_reason).slice(0, 120)}` : ""
      lines.push(`  - ${c?.id || "check"}: ${c?.status || "unknown"}${routes}${ev}${lim}`)
      if (c?.actual) lines.push(`    actual: ${String(c.actual).slice(0, 240)}`)
    }
  }
  const findings = Array.isArray(report.findings) ? report.findings : []
  if (findings.length) {
    lines.push(`  findings=${findings.length}`)
    for (const f of findings.slice(0, 10)) {
      lines.push(`  - [${f?.severity || "?"}] ${f?.title || f?.id || "finding"} (${f?.check_id || "n/a"})`)
    }
  }
  if (report.evidence?.directory) lines.push(`  evidence_dir=${report.evidence.directory}`)
  if (Array.isArray(report.limitations) && report.limitations.length) {
    lines.push(`  limitations: ${report.limitations.slice(0, 5).map((x: any) => String(x)).join(" | ")}`)
  }
  return lines.join("\n")
}

export function parseDecision(text: string): any | null {
  const m = text.match(/\{[\s\S]*\}/)
  if (!m) return null
  try {
    return JSON.parse(m[0])
  } catch {
    return null
  }
}

export function isAllowedCommand(cmd: string): boolean {
  return /^(run|report|status|abort|assay-browser|assay-demo)\b/.test(cmd.trim())
}

export function commandToArgs(cmd: string, personasDir: string): Invocation {
  const trimmed = cmd.trim()
  if (trimmed === "assay-browser") return browserInvocation("", personasDir)
  if (trimmed === "assay-demo") return demoInvocation("", personasDir)
  if (trimmed.startsWith("assay-browser ")) {
    return browserInvocation(trimmed.replace(/^assay-browser\s+/, "").trim(), personasDir)
  }
  if (trimmed.startsWith("assay-demo ")) {
    return demoInvocation(trimmed.replace(/^assay-demo\s+/, "").trim(), personasDir)
  }
  if (trimmed === "assay") return assayInvocation("", personasDir)
  if (trimmed.startsWith("assay ")) {
    return assayInvocation(trimmed.replace(/^assay\s+/, "").trim(), personasDir)
  }
  return { args: trimmed.split(/\s+/).filter(Boolean) }
}

function browserEnvFor(args: string[]): NodeJS.ProcessEnv | undefined {
  if (args.includes("--no-browser")) return undefined
  if (process.env.QH_SSRF_ALLOWLIST) return process.env
  return { ...process.env, QH_SSRF_ALLOWLIST: "127.0.0.0/8,::1,localhost" }
}

function commandSignature(args: string[], env?: NodeJS.ProcessEnv): string {
  const relevantEnv = env?.QH_SSRF_ALLOWLIST ? `|QH_SSRF_ALLOWLIST=${env.QH_SSRF_ALLOWLIST}` : ""
  return `${args.join(" ")}${relevantEnv}`
}

function parseUserContext(input: string): { skip?: boolean; source?: string; method?: string; envRef?: string; id?: string; server?: string; text?: string } {
  const raw = String(input || "")
  const lower = raw.trim().toLowerCase()
  const out: any = {}
  if (/\[context:skip\]/i.test(raw) || /\bskip\s+context\b/i.test(lower) || /^(skip|none|no|n\/a|na)$/i.test(raw.trim())) out.skip = true
  const sourceM = raw.match(/\[source:(jira|slack|github|doc|url|text)\]/i) || lower.match(/\bsource\s*[:=]\s*(jira|slack|github|doc|url|text)\b/i)
  if (sourceM) out.source = sourceM[1].toLowerCase()
  const methodM = raw.match(/\[method:(paste|mcp|token)\]/i) || lower.match(/\bmethod\s*[:=]\s*(paste|mcp|token)\b/i)
  if (methodM) out.method = methodM[1].toLowerCase()
  const envM = raw.match(/\[(?:env|secret_ref|secret-ref)\s*:\s*([^\]]+)\]/i)
  if (envM) out.envRef = envM[1].trim()
  const idM = raw.match(/\[id\s*:\s*([^\]]+)\]/i)
  if (idM) out.id = idM[1].trim()
  const serverM = raw.match(/\[(?:server|mcp_server|mcp-server)\s*:\s*([^\]]+)\]/i)
  if (serverM) out.server = serverM[1].trim()
  // treat remaining text after tags/keywords as context text (redacted when stored)
  const text = raw
    .replace(/\[(?:context|source|method|env|secret_ref|secret-ref|id|server|mcp_server|mcp-server)\s*:[^\]]+\]/gi, "")
    .replace(/\bskip\s+context\b/gi, "")
    .trim()
  if (text) out.text = text
  return out
}

function applyContextResponse(state: AgentState, input: string): { progressed: boolean; message?: string } {
  if (!state.pending) return { progressed: false }
  const resp = parseUserContext(input)
  const raw = String(input || "").trim()
  if (state.pending.kind === "need_method") {
    if (resp.skip) {
      state.contextLog.push(`step ${state.step}: ${state.pending.type} context skipped by user`)
      state.pending = null
      state.step += 1
      return { progressed: true }
    }
    let method = resp.method
    if (!method) {
      const lower = raw.toLowerCase()
      if (/^(paste|text)$/.test(lower)) method = "paste"
      else if (/^(mcp)$/.test(lower)) method = "mcp"
      else if (/^(token|api token)$/.test(lower)) method = "token"
      else if (resp.text || (raw && !/^\[/.test(raw))) method = "paste"
    }
    if (!method) {
      return { progressed: false, message: "Please choose a method: paste, mcp, or token (secure env ref), or reply `skip`." }
    }
    if (method === "token") {
      state.contextLog.push(`step ${state.step}: ${state.pending.type} context via token/secret-ref (${resp.envRef || "env ref not provided"}${resp.id ? ", id provided" : ""})`)
    } else if (method === "mcp") {
      state.contextLog.push(`step ${state.step}: ${state.pending.type} context via MCP (server=${resp.server || "?"}, id=${resp.id || "?"})${resp.text ? ` note=${redactTranscriptText(resp.text).slice(0, 200)}` : ""}`)
    } else {
      const snippet = redactTranscriptText(resp.text || raw).slice(0, 800)
      state.contextLog.push(`step ${state.step}: ${state.pending.type} context via paste${snippet ? `: ${snippet}` : ""}`)
    }
    state.pending = null
    state.step += 1
    return { progressed: true }
  }
  // need_context
  if (resp.skip) {
    state.contextLog.push(`step ${state.step}: context skipped by user`)
    state.pending = null
    state.step += 1
    return { progressed: true }
  }
  const source = resp.source || (state.pending.type as string) || "text"
  // If user provided substantial free text without explicit method, treat as paste for this source.
  const hasFreeText = !!(resp.text && !resp.method)
  if (!resp.method && hasFreeText) {
    state.contextLog.push(`step ${state.step}: ${source} context via paste: ${redactTranscriptText(resp.text || "").slice(0, 800)}`)
    state.pending = null
    state.step += 1
    return { progressed: true }
  }
  if (!resp.method) {
    // ask for method next
    state.pending = { kind: "need_method", type: source, question: state.pending.question, requested: state.pending.requested, reason: state.pending.reason }
    return { progressed: true }
  }
  // source + method provided together
  state.pending = { kind: "need_method", type: source, question: state.pending.question, requested: state.pending.requested, reason: state.pending.reason }
  return applyContextResponse(state, input)
}

function buildExecutionSummary(state: AgentState): string {
  const planMsg = state.testPlan ? `\n\n## Test plan\n${state.testPlan}` : ""
  const details = state.runSummaries.length ? `\n\n## What was executed (all runs/checks)\n${state.runSummaries.join("\n\n")}` : ""
  const ctx = state.contextLog.length ? `\n\n## Context decisions\n${state.contextLog.map((c) => `- ${c}`).join("\n")}` : ""
  return `${planMsg}${details}${ctx}\n\n## Run IDs\n${state.runIds.join(", ") || "(none)"}`
}

async function requestContextAndMaybeWait(
  state: AgentState,
  cwd: string | undefined,
  goal: string,
  deps: AgentDeps,
  options: AgentOptions,
): Promise<boolean> {
  const mcpResources = await discoverMcpResources(deps)
  const requestMsg = formatNeedContext(state.pending!, mcpResources, goal)
  await deps.sendFinal(`${requestMsg}${buildExecutionSummary(state)}`)
  saveState(cwd, state)
  if (options.interactive && deps.requestUserInput) {
    while (state.pending) {
      const reply = await deps.requestUserInput("awaiting assay context")
      const applied = applyContextResponse(state, String(reply || ""))
      saveState(cwd, state)
      if (applied.message) {
        await deps.sendFinal(applied.message)
        saveState(cwd, state)
        continue
      }
      if (applied.progressed) return true
      // If not progressed and no message (unexpected), re-ask
      const mcp2 = await discoverMcpResources(deps)
      await deps.sendFinal(formatNeedContext(state.pending!, mcp2, goal))
      saveState(cwd, state)
    }
    return true
  }
  return false
}

export async function runAgentLoop(options: AgentOptions, deps: AgentDeps): Promise<void> {
  const { goal, personasDir } = options
  const cwd = options.cwd
  const key = options.stateKey || `${goal}|${JSON.stringify(options.initialArgs || [])}`
  const maxIterations = options.maxIterations ?? DEFAULT_MAX_ITERATIONS
  const minHarnessRuns = Math.max(1, Math.min(options.minHarnessRuns ?? DEFAULT_MIN_HARNESS_RUNS, maxIterations))
  const log = deps.progress || ((message: string) => process.stderr.write(`[assay-agent] ${message}\n`))

  let state = loadState(cwd, key)
  const userInput = String(options.userInput || "")
  if (state && userInput) {
    const applied = applyContextResponse(state, userInput)
    saveState(cwd, state)
    if (applied.message) {
      await deps.sendFinal(applied.message)
      return
    }
    if (applied.progressed) {
      log("Recorded context/skip response.")
    }
  }

  if (!state) {
    state = {
      version: 1,
      key,
      goal,
      sessionID: options.sessionID,
      initialArgs: options.initialArgs,
      testPlan: "",
      executedCommands: [],
      runIds: [],
      runSummaries: [],
      currentReport: "",
      contextLog: [],
      pending: null,
      step: 1,
      initialDone: false,
    }
  } else {
    if (options.sessionID) state.sessionID = options.sessionID
    if (!state.initialArgs && options.initialArgs) state.initialArgs = options.initialArgs
  }

  log(`Starting autonomous assay agent (interactive, max ${maxIterations} iterations)`)
  log(`Goal: ${goal}`)

  const executedCommands = new Set<string>(state.executedCommands)

  if (!state.initialDone) {
    log("Iteration 1: running initial assessment...")
    const initialArgs =
      options.initialArgs ||
      ["run", "--target", ".", "--no-browser", "--personas-dir", personasDir, "--intent", goal]
    const sig = commandSignature(initialArgs, options.initialEnv)
    if (!executedCommands.has(sig)) {
      executedCommands.add(sig)
      state.executedCommands = Array.from(executedCommands)
      const initialOutput = await deps.runHarness(initialArgs, options.initialEnv)
      state.currentReport = initialOutput
      for (const id of extractRunIds(initialOutput)) if (!state.runIds.includes(id)) state.runIds.push(id)
      state.runSummaries.push(summarizeRunOutput(initialOutput))
      state.initialDone = true
    } else {
      state.initialDone = true
    }
    // After initial assessment, always request context/skip before next iteration
    state.pending = {
      kind: "need_context",
      type: "jira",
      question: "Initial assessment complete. Provide context for the next test iteration or skip this context.",
      requested: ["Acceptance criteria / expected behavior", "Relevant Jira/Slack/GitHub/doc/thread or notes", "Safe test environment details/credentials reference if interactive flows are needed (no secrets pasted)"],
      reason: "Interactive checkpoint before iteration 2; a comprehensive test plan will be created from source/report context.",
    }
    saveState(cwd, state)
  }

  // If waiting for user context/method, ask and either wait in-session or stop for resume
  if (state.pending) {
    const continued = await requestContextAndMaybeWait(state, cwd, goal, deps, options)
    if (!continued) return
  }

  let iterations = 0
  while (iterations < maxIterations) {
    iterations += 1
    // if pending at loop top (e.g. after resume), resolve it
    if (state.pending) {
      const continued = await requestContextAndMaybeWait(state, cwd, goal, deps, options)
      if (!continued) return
    }
    const needsPlan = !state.testPlan
    log(`Iteration ${state.step + 1}: consulting LLM for next step...`)

    const planSection = state.testPlan
      ? `\nTest plan (follow it; execute next UNIQUE step only after context/skip is resolved):\n${state.testPlan}\n`
      : ""
    const contextSection = state.contextLog.length
      ? `\nContext decisions so far:\n${state.contextLog.map((c) => `- ${c}`).join("\n")}\n`
      : "\nContext decisions so far: (none)\n"

    const decisionPrompt = `You are a strict assay judge for the quality-harness agent. Goal: ${goal}

Latest harness report:
${redactTranscriptText(state.currentReport)}
${planSection}${contextSection}
Executed commands so far (DO NOT repeat any of these): ${Array.from(executedCommands).join(" || ") || "(none)"}

Rules:
- NEVER run the same test/command again. No two iterations may execute the same command/args.
- Each iteration must be interactive: before running any next unique test step, request user context (Jira, Slack, GitHub, Doc, URL, Text in that priority) and include an explicit option to SKIP this context. If the user chooses to provide third-party context, ask how to proceed: plain text paste, connect via MCP, or safely submitted API token (secure env/secret reference only, never pasted). Then wait for the user's response in the next run.
- Return need_user_context to ask for context/skip (and method choice is handled interactively). Do not execute another test in the same response when asking for context.
- You may choose ONE NEW unique command from the plan only when appropriate after context/skip has been recorded for the current step and the command adds evidence. Prefer functional/active browser checks for URL targets (including safe toggles and form validation probes without submit) as distinct steps.
- Do not submit real forms/transactions, create accounts, brute-force, or access other tenants' data. Authenticated/multi-step flows and real submissions require explicit safe test environment, credentials/storage state, and authorization.
- If source is unavailable (URL-only, source: null), note it as a limitation; source/deploy inspection needs --source or a repo target.
${needsPlan ? "- Create a COMPREHENSIVE test plan from source context (journeys, routes, forms/inputs/validation, CTAs/buttons/toggles, auth/stateful flows, search/filters, errors/empty states, a11y/responsive/security; note what requires credentials/submission/interaction beyond safe probes), then request context/skip for the next step." : "- Follow the plan, request context/skip before the next unique step, or run one unique step if justified."}

Allowed commands (must be unique/not previously executed): run, assay-browser, assay-demo, report, status, abort.

Output ONLY one JSON object, no markdown:
${needsPlan ? '{"test_plan":"<comprehensive plan>","need_user_context":{"type":"jira|slack|github|doc|url|text","question":"<what to provide or skip>","requested":["<specific items>"]},"reason":"<why>"}' : '{"need_user_context":{"type":"jira|slack|github|doc|url|text","question":"<what to provide or skip>","requested":["<specific items>"]},"reason":"<why>"}'}
or
{"command":"<full NEW command from plan>","reason":"<why this unique step adds evidence>"}
or
{"done":true,"summary":"<evidence-backed assessment with gaps/assumptions and context decisions>","fixes":["<concrete fix>", ...]}`

    let llmText = ""
    try {
      llmText = await deps.askLlm(decisionPrompt)
    } catch (e: any) {
      log(`LLM call failed: ${e?.message || e}`)
      break
    }
    const decision = parseDecision(llmText)
    if (!decision) {
      log("Could not parse LLM decision; stopping.")
      break
    }
    if (typeof decision.test_plan === "string" && decision.test_plan.trim()) {
      state.testPlan = decision.test_plan.trim()
      log("Test plan created from source context:")
      for (const line of state.testPlan.split("\n")) log(`  ${line}`)
      process.stderr.write(`\n[assay-agent] TEST PLAN\n${state.testPlan}\n\n`)
    }

    if (decision.need_user_context) {
      state.pending = {
        kind: "need_context",
        type: String(decision.need_user_context.type || "jira"),
        question: String(decision.need_user_context.question || "What context can you provide for the next step?"),
        requested: Array.isArray(decision.need_user_context.requested) ? decision.need_user_context.requested : [],
        reason: String(decision.reason || ""),
      }
      saveState(cwd, state)
      const continued = await requestContextAndMaybeWait(state, cwd, goal, deps, options)
      if (!continued) return
      continue
    }

    if (decision.done) {
      const harnessRuns = state.runSummaries.length
      if (harnessRuns < minHarnessRuns) {
        log(`Rejecting early completion (${harnessRuns}/${minHarnessRuns} runs). Requesting context/skip to continue.`)
        state.pending = {
          kind: "need_context",
          type: "jira",
          question: "More evidence is required before finishing. Provide context for the next unique test step or skip this context.",
          requested: ["Acceptance criteria / expected behavior", "Areas/flows to prioritize next", "Safe environment/credentials reference if needed (no secrets pasted)"],
          reason: `Completion rejected: only ${harnessRuns} of ${minHarnessRuns} required harness run(s) completed.`,
        }
        saveState(cwd, state)
        const continued = await requestContextAndMaybeWait(state, cwd, goal, deps, options)
        if (!continued) return
        continue
      }
      log("Agent decided it is done.")
      saveState(cwd, state)
      const fixes = Array.isArray(decision.fixes) ? decision.fixes : []
      await deps.sendFinal(`${decision.summary || "Assessment complete."}${buildExecutionSummary(state)}\n\n## Suggested fixes\n${fixes.map((f: string) => `- ${f}`).join("\n") || "- (none)"}`)
      return
    }

    const cmd = String(decision.command || "").trim()
    if (!cmd) {
      log("Empty command; stopping.")
      break
    }
    if (!isAllowedCommand(cmd)) {
      log(`Rejected disallowed command: ${cmd}`)
      continue
    }
    const invocation = commandToArgs(cmd, personasDir)
    if (invocation.error) {
      log(invocation.error)
      continue
    }
    const env = browserEnvFor(invocation.args)
    const sig = commandSignature(invocation.args, env)
    if (executedCommands.has(sig)) {
      log(`Skipping duplicate command (already executed): ${cmd}`)
      state.currentReport = `${state.currentReport}\n\nNOTE: Command skipped (duplicate): ${cmd}`
      continue
    }

    log(`Executing unique step: ${cmd}`)
    executedCommands.add(sig)
    state.executedCommands = Array.from(executedCommands)
    const output = await deps.runHarness(invocation.args, env)
    state.currentReport = output
    for (const id of extractRunIds(output)) if (!state.runIds.includes(id)) state.runIds.push(id)
    state.runSummaries.push(`Command: ${cmd}\n${summarizeRunOutput(output)}`)

    // After executing a unique step, always ask for context/skip before next iteration
    state.pending = {
      kind: "need_context",
      type: "jira",
      question: "Provide context for the next test step or skip this context.",
      requested: ["Acceptance criteria / expected behavior", "Relevant Jira/Slack/GitHub/doc/thread", "Credentials or safe test instructions if needed (use secure reference, not pasted secrets)"],
      reason: "Context/skip checkpoint after executing a unique test step.",
    }
    saveState(cwd, state)
    const continued = await requestContextAndMaybeWait(state, cwd, goal, deps, options)
    if (!continued) return
    continue
  }

  saveState(cwd, state)
  await deps.sendFinal(`Agent reached iteration cap (${maxIterations}).${buildExecutionSummary(state)}`)
}
