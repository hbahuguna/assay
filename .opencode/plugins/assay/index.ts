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
  { name: "assay-carbon", label: "carbon" },
  { name: "assay-jay", label: "jay" },
  { name: "assay-full", label: "full" },
  { name: "assay-carbon-test", label: "carbon-test" },
  { name: "assay-carbon-functionality", label: "carbon-functionality" },
  { name: "assay-carbon-browser", label: "carbon-browser" },
  { name: "assay-carbon-issues", label: "carbon-issues" },
  { name: "assay-carbon-demo", label: "carbon-demo" },
  { name: "assay-carbon-more", label: "carbon-more" },
  { name: "assay-carbon-help", label: "carbon-help" },
  { name: "assay-carbon-accessibility", label: "carbon-accessibility" },
  { name: "assay-carbon-ui", label: "carbon-ui" },
  { name: "assay-carbon-ux", label: "carbon-ux" },
  { name: "assay-carbon-usability", label: "carbon-usability" },
  { name: "assay-carbon-personas", label: "carbon-personas" },
  { name: "assay-carbon-compatibility", label: "carbon-compatibility" },
  { name: "assay-carbon-intl", label: "carbon-intl" },
  { name: "assay-carbon-localization", label: "carbon-localization" },
  { name: "assay-carbon-content", label: "carbon-content" },
  { name: "assay-carbon-geo", label: "carbon-geo" },
  { name: "assay-carbon-security", label: "carbon-security" },
  { name: "assay-carbon-security-review", label: "carbon-security-review" },
  { name: "assay-carbon-privacy", label: "carbon-privacy" },
  { name: "assay-carbon-state", label: "carbon-state" },
  { name: "assay-carbon-reliability", label: "carbon-reliability" },
  { name: "assay-carbon-performance", label: "carbon-performance" },
  { name: "assay-carbon-load", label: "carbon-load" },
  { name: "assay-carbon-stress", label: "carbon-stress" },
  { name: "assay-carbon-networking", label: "carbon-networking" },
  { name: "assay-carbon-api", label: "carbon-api" },
  { name: "assay-carbon-data", label: "carbon-data" },
  { name: "assay-failure", label: "failure" },
  { name: "assay-proof", label: "proof" },
  { name: "assay-carbon-confidence", label: "carbon-confidence" },
  { name: "assay-carbon-confidence-init", label: "carbon-confidence-init" },
  { name: "assay-carbon-confidence-plan", label: "carbon-confidence-plan" },
  { name: "assay-carbon-review", label: "carbon-review" },
  { name: "assay-carbon-skeptical-review", label: "carbon-skeptical-review" },
  { name: "assay-carbon-release", label: "carbon-release" },
  { name: "assay-carbon-release-review", label: "carbon-release-review" },
  { name: "assay-carbon-incident", label: "carbon-incident" },
  { name: "assay-carbon-eval", label: "carbon-eval" },
  { name: "assay-carbon-eval-design-review", label: "carbon-eval-design-review" },
  { name: "assay-carbon-statistical-review", label: "carbon-statistical-review" },
  { name: "assay-carbon-agentic", label: "carbon-agentic" },
  { name: "assay-chatbot", label: "chatbot" },
  { name: "assay-bias", label: "bias" },
  { name: "assay-carbon-tests", label: "carbon-tests" },
  { name: "assay-carbon-generate", label: "carbon-generate" },
  { name: "assay-carbon-map", label: "carbon-map" },
  { name: "assay-carbon-full-report", label: "carbon-full-report" },
  { name: "assay-carbon-stats", label: "carbon-stats" },
  { name: "assay-carbon-integrations", label: "carbon-integrations" },
  { name: "assay-ai-upgrade", label: "ai-upgrade" },
  { name: "assay-carbon-auto", label: "carbon-auto" },
  { name: "assay-carbon-background", label: "carbon-background" },
  { name: "assay-carbon-forever", label: "carbon-forever" },
  { name: "assay-carbon-settings", label: "carbon-settings" },
  { name: "assay-carbon-human", label: "carbon-human" },
  { name: "assay-carbon-pro", label: "carbon-pro" },
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
