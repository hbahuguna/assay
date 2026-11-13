import { Plugin } from "@opencode/plugin"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { existsSync, readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

import { browserInvocation, demoInvocation } from "./command-args.ts"
import { runAgentLoop } from "./agent.ts"
import { browserEnv, runAssay } from "./runner.ts"

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = process.env.ASSAY_ROOT || resolve(PLUGIN_DIR, "../../..")
const PERSONAS = resolve(REPO_ROOT, "personas")

function findPendingStateForSession(projectDirectory: string, sessionID: string): { stateKey: string; goal: string; initialArgs?: string[]; command?: string } | null {
  try {
    const dir = join(projectDirectory, ".harness", "assay-agent")
    if (!existsSync(dir)) return null
    for (const name of readdirSync(dir)) {
      if (!name.endsWith(".json")) continue
      try {
        const state = JSON.parse(readFileSync(join(dir, name), "utf8"))
        if (state?.sessionID === sessionID && state?.pending) {
          return {
            stateKey: state.key,
            goal: state.goal || "Assay assessment",
            command: typeof state.key === "string" ? state.key.split("|")[0] : undefined,
            initialArgs: Array.isArray(state.initialArgs) ? state.initialArgs : undefined,
          }
        }
      } catch {}
    }
  } catch {}
  return null
}

function extractUserTextFromEvent(event: any): string {
  if (!event) return ""
  const candidates = [
    event?.properties?.part?.text,
    event?.properties?.message?.content,
    event?.properties?.content,
    event?.part?.text,
    event?.message?.content,
    event?.text,
  ]
  for (const c of candidates) {
    if (typeof c === "string" && c.trim()) return c
  }
  const parts = event?.properties?.parts || event?.parts || event?.message?.parts
  if (Array.isArray(parts)) {
    const t = parts.map((p: any) => (p?.type === "text" ? p.text : "")).filter(Boolean).join("\n").trim()
    if (t) return t
  }
  return ""
}

function isContextReply(text: string): boolean {
  return /\[context:skip\]/i.test(text) || /\[source:(jira|slack|github|doc|url|text)\]/i.test(text) || /\[method:(paste|mcp|token)\]/i.test(text)
}

const assayCommands = [
  { name: "assay-manager", label: "manager" },
  { name: "assay-full", label: "full" },
  { name: "assay-test", label: "test" },
  { name: "assay-functionality", label: "functionality" },
  { name: "assay-browser", label: "browser" },
  { name: "assay-issues", label: "issues" },
  { name: "assay-demo", label: "demo" },
  { name: "assay-more", label: "more" },
  { name: "assay-help", label: "help" },
  { name: "assay-accessibility", label: "accessibility" },
  { name: "assay-ui", label: "ui" },
  { name: "assay-ux", label: "ux" },
  { name: "assay-usability", label: "usability" },
  { name: "assay-personas", label: "personas" },
  { name: "assay-compatibility", label: "compatibility" },
  { name: "assay-intl", label: "intl" },
  { name: "assay-localization", label: "localization" },
  { name: "assay-content", label: "content" },
  { name: "assay-geo", label: "geo" },
  { name: "assay-security", label: "security" },
  { name: "assay-security-review", label: "security-review" },
  { name: "assay-privacy", label: "privacy" },
  { name: "assay-state", label: "state" },
  { name: "assay-reliability", label: "reliability" },
  { name: "assay-performance", label: "performance" },
  { name: "assay-load", label: "load" },
  { name: "assay-stress", label: "stress" },
  { name: "assay-networking", label: "networking" },
  { name: "assay-api", label: "api" },
  { name: "assay-data", label: "data" },
  { name: "assay-failure", label: "failure" },
  { name: "assay-proof", label: "proof" },
  { name: "assay-confidence", label: "confidence" },
  { name: "assay-confidence-init", label: "confidence-init" },
  { name: "assay-confidence-plan", label: "confidence-plan" },
  { name: "assay-review", label: "review" },
  { name: "assay-skeptical-review", label: "skeptical-review" },
  { name: "assay-release", label: "release" },
  { name: "assay-release-review", label: "release-review" },
  { name: "assay-incident", label: "incident" },
  { name: "assay-eval", label: "eval" },
  { name: "assay-eval-design-review", label: "eval-design-review" },
  { name: "assay-statistical-review", label: "statistical-review" },
  { name: "assay-agentic", label: "agentic" },
  { name: "assay-chatbot", label: "chatbot" },
  { name: "assay-bias", label: "bias" },
  { name: "assay-tests", label: "tests" },
  { name: "assay-generate", label: "generate" },
  { name: "assay-map", label: "map" },
  { name: "assay-full-report", label: "full-report" },
  { name: "assay-stats", label: "stats" },
  { name: "assay-integrations", label: "integrations" },
  { name: "assay-ai-upgrade", label: "ai-upgrade" },
  { name: "assay-auto", label: "auto" },
  { name: "assay-background", label: "background" },
  { name: "assay-forever", label: "forever" },
  { name: "assay-settings", label: "settings" },
  { name: "assay-human", label: "human" },
  { name: "assay-pro", label: "pro" },
]

async function sendPrompt(ctx: any, sessionID: string, text: string, noReply = false): Promise<any> {
  try {
    if (ctx.client?.session?.prompt) {
      return await ctx.client.session.prompt({
        path: { id: sessionID },
        body: { noReply, parts: [{ type: "text", text }] },
      })
    }
  } catch {}
  try {
    if (ctx.session?.prompt) {
      return await ctx.session.prompt({ sessionID, text, noReply })
    }
  } catch {}
  return null
}

export default Plugin.define({
  id: "assay",
  async setup(ctx: any) {
    await ctx.command.transform((editor: any) => {
      editor.add({
        name: "assay",
        description: "Run quality-harness autonomous agent: /assay <goal>",
        execute: async ({ sessionID, prompt }: { sessionID: string; prompt: { text?: string } }) => {
          const projectDirectory =
            ctx.location?.project?.canonical ||
            ctx.location?.project?.directory ||
            ctx.location?.directory ||
            process.cwd()
          const raw = (prompt.text || "").trim()
          if (/^(commands|list|help|--help|-h)$/i.test(raw)) {
            const names = ["assay", ...assayCommands.map((c) => c.name)].sort()
            await sendPrompt(ctx, sessionID, "Available assay commands:\n\n" + names.map((n) => "- /" + n).join("\n"))
            return
          }
          const goal = raw || "Assess release readiness of the target."

          const progress = (message: string) => {
            process.stderr.write(`[assay-agent] ${message}\n`)
            void sendPrompt(ctx, sessionID, `[assay-agent] ${message}`, true)
          }

          const askLlm = async (text: string): Promise<string> => {
            try {
              const gen = ctx.experimental?.generate?.text || ctx.generate?.text
              if (gen) {
                const out = await gen({ prompt: text })
                if (typeof out === "string") return out
                if (out?.text) return out.text
                if (out?.content) return out.content
              }
            } catch {}
            const resp = await sendPrompt(ctx, sessionID, text, false)
            const parts = resp?.parts || resp?.data?.parts || []
            const textOut = parts.map((p: any) => p.text || "").join("\n")
            if (textOut) return textOut
            throw new Error("LLM response unavailable")
          }

          const listMcpResources = async (): Promise<string[]> => {
            try {
              const list =
                ctx.client?.mcp?.resources?.list ||
                ctx.client?.mcp?.listResources ||
                ctx.mcp?.resources?.list ||
                ctx.mcp?.listResources
              if (!list) return []
              const out = await list({})
              const resources = out?.resources || out?.data?.resources || []
              const templates = out?.resourceTemplates || out?.templates || out?.data?.resourceTemplates || []
              const names = [
                ...resources.map((r: any) => `resource: ${r?.server || "?"}/${r?.name || r?.uri || "?"}`),
                ...templates.map((r: any) => `template: ${r?.server || "?"}/${r?.name || r?.uriTemplate || "?"}`),
              ]
              return names
            } catch {
              return []
            }
          }

          const baseGoal = raw.replace(/\[(?:context|source|method|env|secret_ref|secret-ref|id|server|mcp_server|mcp-server)\s*:[^\]]+\]/gi, "").replace(/\bskip\s+context\b/gi, "").trim() || goal
          await runAgentLoop(
            { goal: baseGoal, personasDir: PERSONAS, cwd: projectDirectory, userInput: raw, stateKey: `assay|${baseGoal}`, sessionID },
            {
              runHarness: (args, env) => runAssay(args, projectDirectory, env),
              askLlm,
              progress,
              sendFinal: async (text) => {
                await sendPrompt(ctx, sessionID, text, false)
              },
              listMcpResources,
            },
          )
        },
      })

      for (const command of assayCommands) {
        const isBrowser = command.name === "assay-browser"
        const isDemo = command.name === "assay-demo"
        editor.add({
          name: command.name,
          description: isBrowser
            ? "Run quality-harness live browser assessment (requires a URL)"
            : isDemo
              ? "Run quality-harness demo assessment (optional URL for live checks)"
              : `Run quality-harness ${command.label} assessment`,
          execute: async ({ sessionID, prompt }: { sessionID: string; prompt: { text?: string } }) => {
            const projectDirectory =
              ctx.location?.project?.canonical ||
              ctx.location?.project?.directory ||
              ctx.location?.directory ||
              process.cwd()

            const progress = (message: string) => {
              process.stderr.write(`[assay-agent] ${message}\n`)
              void sendPrompt(ctx, sessionID, `[assay-agent] ${message}`, true)
            }
            const askLlm = async (text: string): Promise<string> => {
              try {
                const gen = ctx.experimental?.generate?.text || ctx.generate?.text
                if (gen) {
                  const out = await gen({ prompt: text })
                  if (typeof out === "string") return out
                  if (out?.text) return out.text
                  if (out?.content) return out.content
                }
              } catch {}
              const resp = await sendPrompt(ctx, sessionID, text, false)
              const parts = resp?.parts || resp?.data?.parts || []
              const textOut = parts.map((p: any) => p.text || "").join("\n")
              if (textOut) return textOut
              throw new Error("LLM response unavailable")
            }
            const listMcpResources = async (): Promise<string[]> => {
              try {
                const list =
                  ctx.client?.mcp?.resources?.list ||
                  ctx.client?.mcp?.listResources ||
                  ctx.mcp?.resources?.list ||
                  ctx.mcp?.listResources
                if (!list) return []
                const out = await list({})
                const resources = out?.resources || out?.data?.resources || []
                const templates = out?.resourceTemplates || out?.templates || out?.data?.resourceTemplates || []
                return [
                  ...resources.map((r: any) => `resource: ${r?.server || "?"}/${r?.name || r?.uri || "?"}`),
                  ...templates.map((r: any) => `template: ${r?.server || "?"}/${r?.name || r?.uriTemplate || "?"}`),
                ]
              } catch {
                return []
              }
            }
            const deps = {
              runHarness: (args: string[], env?: NodeJS.ProcessEnv) => runAssay(args, projectDirectory, env),
              askLlm,
              progress,
              sendFinal: async (text: string) => {
                await sendPrompt(ctx, sessionID, text, false)
              },
              listMcpResources,
            }

            const raw = (prompt.text || "").trim()
            const stateKeyBase = `${command.name}|${raw.replace(/\[(?:context|source|method|env|secret_ref|secret-ref|id|server|mcp_server|mcp-server)\s*:[^\]]+\]/gi, "").replace(/\bskip\s+context\b/gi, "").trim()}`
            if (isBrowser) {
              const invocation = browserInvocation(prompt.text || "", PERSONAS)
              if (invocation.error) {
                await sendPrompt(ctx, sessionID, invocation.error)
                return
              }
              await runAgentLoop(
                {
                  goal: "Assay browser assessment",
                  personasDir: PERSONAS,
                  initialArgs: invocation.args,
                  initialEnv: browserEnv(),
                  cwd: projectDirectory,
                  userInput: raw,
                  stateKey: stateKeyBase,
                  sessionID,
                },
                deps,
              )
              return
            }
            if (isDemo) {
              const invocation = demoInvocation(prompt.text || "", PERSONAS)
              const env = invocation.args.includes("--no-browser") ? undefined : browserEnv()
              await runAgentLoop(
                {
                  goal: "Assay demo assessment",
                  personasDir: PERSONAS,
                  initialArgs: invocation.args,
                  initialEnv: env,
                  cwd: projectDirectory,
                  userInput: raw,
                  stateKey: stateKeyBase,
                  sessionID,
                },
                deps,
              )
              return
            }

            const intent = `Assay ${command.label} assessment`
            await runAgentLoop(
              {
                goal: intent,
                personasDir: PERSONAS,
                initialArgs: [
                  "run",
                  "--target",
                  ".",
                  "--no-browser",
                  "--personas-dir",
                  PERSONAS,
                  "--intent",
                  intent,
                ],
                cwd: projectDirectory,
                userInput: raw,
                stateKey: stateKeyBase,
                sessionID,
              },
              deps,
            )
          },
        })
      }

      const tryResume = async (sessionID: string, userText: string) => {
        if (!sessionID || !isContextReply(userText)) return
        const projectDirectory =
          ctx.location?.project?.canonical ||
          ctx.location?.project?.directory ||
          ctx.location?.directory ||
          process.cwd()
        const pending = findPendingStateForSession(projectDirectory, sessionID) as any
        if (!pending) return
        // Resume with stored key/goal/args when available.
        const cmd = pending.command || "assay"
        let initialArgs: string[] | undefined = Array.isArray(pending.initialArgs) ? pending.initialArgs : undefined
        let initialEnv: NodeJS.ProcessEnv | undefined
        let goal = pending.goal || "Assay assessment"
        let stateKey = pending.stateKey
        if (!initialArgs) {
          if (cmd === "assay-browser") {
            // Cannot infer original URL/flags; ask user to rerun browser command with tag.
            await sendPrompt(ctx, sessionID, "Please rerun the original `/assay-browser <url> ...` command with the same context tag (e.g. `[context:skip]`) so the browser target and flags are preserved.")
            return
          }
          if (cmd === "assay-demo") {
            initialArgs = ["run", "--target", ".", "--no-browser", "--personas-dir", PERSONAS, "--intent", "Assay demo assessment"]
            goal = "Assay demo assessment"
          } else if (cmd !== "assay") {
            const label = cmd.replace(/^assay-/, "")
            goal = `Assay ${label} assessment`
            initialArgs = ["run", "--target", ".", "--no-browser", "--personas-dir", PERSONAS, "--intent", goal]
          }
        }
        if (initialArgs && !initialArgs.includes("--no-browser")) {
          initialEnv = browserEnv()
        }
        const progress = (message: string) => {
          process.stderr.write(`[assay-agent] ${message}\n`)
          void sendPrompt(ctx, sessionID, `[assay-agent] ${message}`, true)
        }
        const askLlm = async (text: string): Promise<string> => {
          try {
            const gen = ctx.experimental?.generate?.text || ctx.generate?.text
            if (gen) {
              const out = await gen({ prompt: text })
              if (typeof out === "string") return out
              if (out?.text) return out.text
              if (out?.content) return out.content
            }
          } catch {}
          const resp = await sendPrompt(ctx, sessionID, text, false)
          const parts = resp?.parts || resp?.data?.parts || []
          const textOut = parts.map((p: any) => p.text || "").join("\n")
          if (textOut) return textOut
          throw new Error("LLM response unavailable")
        }
        const listMcpResources = async (): Promise<string[]> => {
          try {
            const list =
              ctx.client?.mcp?.resources?.list ||
              ctx.client?.mcp?.listResources ||
              ctx.mcp?.resources?.list ||
              ctx.mcp?.listResources
            if (!list) return []
            const out = await list({})
            const resources = out?.resources || out?.data?.resources || []
            const templates = out?.resourceTemplates || out?.templates || out?.data?.resourceTemplates || []
            return [
              ...resources.map((r: any) => `resource: ${r?.server || "?"}/${r?.name || r?.uri || "?"}`),
              ...templates.map((r: any) => `template: ${r?.server || "?"}/${r?.name || r?.uriTemplate || "?"}`),
            ]
          } catch {
            return []
          }
        }
        await runAgentLoop(
          { goal, personasDir: PERSONAS, cwd: projectDirectory, userInput: userText, stateKey, sessionID, initialArgs, initialEnv },
          {
            runHarness: (args, env) => runAssay(args, projectDirectory, env),
            askLlm,
            progress,
            sendFinal: async (text) => {
              await sendPrompt(ctx, sessionID, text, false)
            },
            listMcpResources,
          },
        )
      }

      try {
        const onEvent = async (event: any) => {
          try {
            const t = event?.type || event?.name || ""
            if (!/message/i.test(String(t))) return
            const sessionID = event?.properties?.sessionID || event?.sessionID || event?.properties?.info?.sessionID || event?.properties?.message?.sessionID
            const role = event?.properties?.info?.role || event?.properties?.message?.role || event?.role
            if (role && role !== "user") return
            const text = extractUserTextFromEvent(event)
            if (!text) return
            await tryResume(String(sessionID || ""), text)
          } catch {}
        }
        if (typeof ctx.event?.subscribe === "function") ctx.event.subscribe(onEvent)
        else if (typeof ctx.bus?.subscribe === "function") ctx.bus.subscribe(onEvent)
        else if (typeof ctx.subscribe === "function") ctx.subscribe(onEvent)
      } catch {}
    })
  },
})
