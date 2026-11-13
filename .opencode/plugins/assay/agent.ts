import { assayInvocation, browserInvocation, demoInvocation } from "./command-args.ts"
import type { Invocation } from "./command-args.ts"

export interface AgentDeps {
  runHarness: (args: string[], env?: NodeJS.ProcessEnv) => Promise<string>
  askLlm: (prompt: string) => Promise<string>
  progress?: (message: string) => void
  sendFinal: (text: string) => Promise<void>
}

export interface AgentOptions {
  goal: string
  personasDir: string
  initialArgs?: string[]
  initialEnv?: NodeJS.ProcessEnv
  maxIterations?: number
  /** Minimum number of harness runs before the agent may finish. Defaults to 3. */
  minHarnessRuns?: number
}

const DEFAULT_MAX_ITERATIONS = 7
const DEFAULT_MIN_HARNESS_RUNS = 3

function extractRunIds(output: string): string[] {
  const ids: string[] = []
  const pattern = /"run_id"\s*:\s*"([^"]+)"/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(output)) !== null) {
    if (!ids.includes(match[1])) ids.push(match[1])
  }
  return ids
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

export async function runAgentLoop(options: AgentOptions, deps: AgentDeps): Promise<void> {
  const { goal, personasDir } = options
  const maxIterations = options.maxIterations ?? DEFAULT_MAX_ITERATIONS
  const minHarnessRuns = Math.max(1, Math.min(options.minHarnessRuns ?? DEFAULT_MIN_HARNESS_RUNS, maxIterations))
  const transcript: string[] = []
  const runIds: string[] = []
  const log = deps.progress || ((message: string) => process.stderr.write(`[assay-agent] ${message}\n`))

  log(`Starting autonomous assay agent (max ${maxIterations} iterations, min ${minHarnessRuns} harness runs)`)
  log(`Goal: ${goal}`)

  log("Iteration 1: running initial assessment...")
  const initialArgs =
    options.initialArgs ||
    ["run", "--target", ".", "--no-browser", "--personas-dir", personasDir, "--intent", goal]
  const initialOutput = await deps.runHarness(initialArgs, options.initialEnv)
  transcript.push(`=== Initial run ===\n${initialOutput}`)
  runIds.push(...extractRunIds(initialOutput))
  let currentReport = initialOutput
  let testPlan = ""

  for (let i = 1; i < maxIterations; i++) {
    log(`Iteration ${i + 1}: consulting LLM for next step...`)

    let decision: any = null
    let earlyFinishRejected = false
    for (let attempt = 0; attempt < 2; attempt++) {
      const mustContinue = runIds.length < minHarnessRuns
      const needsPlan = i === 1 && !testPlan
      const planSection = testPlan
        ? `\nTest plan (created from source context; follow it and run the next evidence-adding step from it):\n${testPlan}\n`
        : ""
      const decisionPrompt = `You are a strict assay judge for the quality-harness agent. Goal: ${goal}

Latest harness report:
${currentReport}
${planSection}
Your job is to be skeptical and maximize useful coverage before finishing. Do not summarize after the first run unless there is genuinely nothing more to check.

${needsPlan ? "In this iteration you MUST create a concise test plan from the source context (use goal, intent, target, and inventory/risks/checks/findings/limitations in the latest report), then choose the NEXT command to execute from that plan. The plan should identify concrete verification steps and stay aligned with the same intent/persona scope; include browser/live checks when a URL is known or relevant." : "Follow the existing test plan when present and choose the next evidence-adding command from it."}

Review the latest report and decide the NEXT SINGLE harness command to run, or finish:
- Treat failed, errored, blocked, skipped, or low-confidence/coverage-gap checks as requiring scrutiny. Blocked/skipped/errored checks are coverage gaps, never passes.
- Prefer follow-up runs that stay aligned with the same assessment intent/persona scope as the goal (reuse the same intent/category wording when running another assessment). You may rerun with different flags or targets only when they add evidence (e.g. add browser checks for a known URL, inspect report/status/root state, run a more specific intent, or re-check after gathering context).
- Do not invent evidence, do not weaken findings, and do not claim a check passed if it was blocked/skipped/errored.
- You must complete at least ${minHarnessRuns} harness runs before finishing. Completed harness runs so far: ${runIds.length}. ${mustContinue ? "You may NOT finish yet; choose another evidence-adding command with the same intent/scope." : "Finish only when key risks are evidenced, remaining gaps are explicitly explained as uncheckable/blocked with reasons, and another allowed command would not add meaningful evidence."}
${earlyFinishRejected ? "- Your previous response tried to finish too early and was rejected. Return a command now." : ""}
${needsPlan ? "- A test_plan is required now; do not return done." : ""}

Allowed commands:
- run --target <path-or-url> --no-browser --personas-dir ${personasDir} --intent "<intent>"
- run --target <url> --personas-dir ${personasDir} --intent "<intent>" (browser checks)
- assay-browser <url>
- assay-demo <url>
- report --root <root>
- status --root <root>

Output ONLY one JSON object, no markdown:
${needsPlan ? '{"test_plan": "<concise plan from source context>", "command": "<full command from plan>", "reason": "<why this adds evidence / what gap it addresses>"}' : '{"command": "<full command>", "reason": "<why this adds evidence / what gap it addresses>"}'}
or
{"done": true, "summary": "<final narrative with evidence-backed assessment, gaps, and confidence>", "fixes": ["<concrete developer fix>", ...]}`

      let llmText = ""
      try {
        llmText = await deps.askLlm(decisionPrompt)
      } catch (e: any) {
        log(`LLM call failed: ${e?.message || e}`)
        llmText = ""
      }

      if (!llmText) {
        decision = null
        break
      }
      transcript.push(`=== LLM decision (iter ${i + 1}, attempt ${attempt + 1}) ===\n${llmText}`)
      decision = parseDecision(llmText)
      if (!decision) {
        break
      }
      if (typeof decision.test_plan === "string" && decision.test_plan.trim()) {
        testPlan = decision.test_plan.trim()
        log("Test plan created from source context.")
        transcript.push(`=== Test plan ===\n${testPlan}`)
      }
      if (decision.done && runIds.length < minHarnessRuns) {
        log(`Rejected early finish: ${runIds.length}/${minHarnessRuns} harness runs completed.`)
        transcript.push(`Rejected early finish at ${runIds.length}/${minHarnessRuns} harness runs.`)
        earlyFinishRejected = true
        decision = null
        continue
      }
      if (i === 1 && !testPlan && !decision.done) {
        // allow command-only response, but prefer plan; do not block if LLM complied partially
      }
      break
    }

    if (!decision) {
      log("Could not obtain a valid next decision; stopping.")
      break
    }

    if (decision.done) {
      log("Agent decided it is done.")
      const fixes = Array.isArray(decision.fixes) ? decision.fixes : []
      const planMsg = testPlan ? `\n\nTest plan:\n${testPlan}` : ""
      const finalMsg = `${decision.summary || "Assessment complete."}${planMsg}\n\nRun IDs: ${runIds.join(", ")}\n\nSuggested fixes:\n${fixes.map((f: string) => `- ${f}`).join("\n")}`
      await deps.sendFinal(finalMsg)
      return
    }

    const cmd = String(decision.command || "").trim()
    if (!cmd) {
      log("Empty command; stopping.")
      break
    }
    if (!isAllowedCommand(cmd)) {
      log(`Rejected disallowed command: ${cmd}`)
      transcript.push(`Rejected: ${cmd}`)
      continue
    }

    log(`Executing: ${cmd}`)
    const invocation = commandToArgs(cmd, personasDir)
    if (invocation.error) {
      log(invocation.error)
      transcript.push(`Rejected: ${invocation.error}`)
      continue
    }

    const output = await deps.runHarness(invocation.args, browserEnvFor(invocation.args))
    transcript.push(`=== Command: ${cmd} ===\n${output}`)
    runIds.push(...extractRunIds(output).filter((id) => !runIds.includes(id)))
    currentReport = output
  }

  const planMsg = testPlan ? `\n\nTest plan:\n${testPlan}` : ""
  const finalMsg = `Agent reached iteration cap (${maxIterations}). Review the transcript above.${planMsg}\n\nRun IDs: ${runIds.join(", ")}`
  await deps.sendFinal(finalMsg)
}
