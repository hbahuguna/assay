import { assayInvocation, browserInvocation, demoInvocation } from "./command-args.ts"
import type { Invocation } from "./command-args.ts"

export interface AgentDeps {
  runHarness: (args: string[], env?: NodeJS.ProcessEnv) => Promise<string>
  askLlm: (prompt: string) => Promise<string>
  progress?: (message: string) => void
  sendFinal: (text: string) => Promise<void>
}

const MAX_ITERATIONS = 7

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

export async function runAgentLoop(goal: string, personasDir: string, deps: AgentDeps): Promise<void> {
  const transcript: string[] = []
  const runIds: string[] = []
  const log = deps.progress || ((message: string) => process.stderr.write(`[assay-agent] ${message}\n`))

  log(`Starting autonomous assay agent (max ${MAX_ITERATIONS} iterations)`)
  log(`Goal: ${goal}`)

  log("Iteration 1: running initial assessment...")
  const initialArgs = ["run", "--target", ".", "--no-browser", "--personas-dir", personasDir, "--intent", goal]
  const initialOutput = await deps.runHarness(initialArgs)
  transcript.push(`=== Initial run ===\n${initialOutput}`)
  runIds.push(...extractRunIds(initialOutput))
  let currentReport = initialOutput

  for (let i = 1; i < MAX_ITERATIONS; i++) {
    log(`Iteration ${i + 1}: consulting LLM for next step...`)

    const decisionPrompt = `You are the assay agent. You have the latest harness report.

${currentReport}

Decide the NEXT SINGLE harness command to run, or finish.

Allowed commands:
- run --target <path-or-url> --no-browser --personas-dir ${personasDir} --intent "<intent>"
- run --target <url> --personas-dir ${personasDir} --intent "<intent>" (browser checks)
- assay-browser <url>
- assay-demo <url>
- report --root <root>
- status --root <root>

If a URL is known, you may run browser checks.

Output ONLY one JSON object, no markdown:
{"command": "<full command>", "reason": "<why>"}
or
{"done": true, "summary": "<final narrative>", "fixes": ["<fix>", ...]}`

    let llmText = ""
    try {
      llmText = await deps.askLlm(decisionPrompt)
    } catch (e: any) {
      log(`LLM call failed: ${e?.message || e}`)
      break
    }

    transcript.push(`=== LLM decision (iter ${i + 1}) ===\n${llmText}`)

    const decision = parseDecision(llmText)
    if (!decision) {
      log("Could not parse LLM decision; stopping.")
      break
    }

    if (decision.done) {
      log("Agent decided it is done.")
      const fixes = Array.isArray(decision.fixes) ? decision.fixes : []
      const finalMsg = `${decision.summary || "Assessment complete."}\n\nRun IDs: ${runIds.join(", ")}\n\nSuggested fixes:\n${fixes.map((f: string) => `- ${f}`).join("\n")}`
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
      continue
    }

    const output = await deps.runHarness(invocation.args, browserEnvFor(invocation.args))
    transcript.push(`=== Command: ${cmd} ===\n${output}`)
    runIds.push(...extractRunIds(output).filter((id) => !runIds.includes(id)))
    currentReport = output
  }

  const finalMsg = `Agent reached iteration cap (${MAX_ITERATIONS}). Review the transcript above.\n\nRun IDs: ${runIds.join(", ")}`
  await deps.sendFinal(finalMsg)
}
