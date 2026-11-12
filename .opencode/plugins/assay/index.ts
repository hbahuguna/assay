import { Plugin } from "@opencode/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"

const execFileAsync = promisify(execFile)
const PYTHON = process.env.ASSAY_PYTHON || "/usr/local/bin/python3.11"
const PERSONAS = "/Users/skaparwan/github/assay/personas"

async function runAssay(args: string[], cwd: string): Promise<string> {
  try {
    const { stdout, stderr } = await execFileAsync(PYTHON, ["-P", "-m", "harness", ...args], {
      encoding: "utf-8",
      cwd,
      env: { ...process.env, PYTHONPATH: "/Users/skaparwan/github/assay" },
    })
    return stdout || stderr || "(no output)"
  } catch (error) {
    const failure = error as { stderr?: string; message?: string; stdout?: string }
    return `assay failed:\n${failure.stderr || failure.stdout || failure.message || String(error)}`
  }
}

const assayCommands = [
  { name: "assay-jay", label: "jay" },
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

export default Plugin.define({
  id: "assay",
  async setup(ctx) {
    await ctx.command.transform((editor) => {
      editor.add({
        name: "assay",
        description: "Run quality-harness: /assay <command> [args...]",
        execute: async ({ sessionID, prompt }) => {
          const projectDirectory = ctx.location.project?.canonical || ctx.location.project?.directory || ctx.location.directory || process.cwd()
          const text = (prompt.text || "").trim()
          const tokens = text.split(/\s+/).filter(Boolean)
          const subcommands = new Set(["run", "status", "report", "abort", "personas", "verify-evidence", "auth", "quota", "ledger", "signup", "billing", "calibrate", "sarif", "junit", "sign-report", "verify-report", "prune"])
          let args: string[]
          if (tokens.length === 0) {
            args = ["run", "--target", ".", "--no-browser", "--personas-dir", PERSONAS]
          } else if (tokens[0] === "--version" || tokens[0] === "-v") {
            args = ["--version"]
          } else if (tokens[0] === "help" || tokens[0] === "--help" || tokens[0] === "-h") {
            args = ["--help"]
          } else if (subcommands.has(tokens[0])) {
            args = tokens
          } else {
            args = ["run", "--target", ".", "--no-browser", "--personas-dir", PERSONAS, "--intent", text]
          }
          const output = await runAssay(args, projectDirectory)
          await ctx.session.prompt({
            sessionID,
            text: `Assay output:\n\n\`\`\`\n${output}\n\`\`\`\n\nSummarize these results. Additional commands are allowed and expected when needed.`,
          })
        },
      })
      for (const command of assayCommands) {
        editor.add({
          name: command.name,
          description: `Run quality-harness ${command.label} assessment`,
          execute: async ({ sessionID }) => {
            const output = await runAssay(["run", "--target", ".", "--no-browser", "--personas-dir", PERSONAS, "--intent", `Assay ${command.label} assessment`], ctx.location.project?.canonical || ctx.location.project?.directory || ctx.location.directory || process.cwd())
            await ctx.session.prompt({
              sessionID,
              text: `Assay output:\n\n\`\`\`\n${output}\n\`\`\`\n\nSummarize these results. Additional commands are allowed and expected when needed.`,
            })
          },
        })
      }
    })
  },
})
