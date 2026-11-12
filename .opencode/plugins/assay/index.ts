import { Plugin } from "@opencode/plugin"
import { execFileSync } from "node:child_process"

const PYTHON = process.env.ASSAY_PYTHON || "/usr/local/bin/python3.11"

function runAssay(args: string[]): string {
  try {
    return execFileSync(PYTHON, ["-P", "-m", "harness", ...args], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYTHONPATH: "/Users/skaparwan/github/assay" },
    })
  } catch (error) {
    const failure = error as { stderr?: string; message?: string }
    return `assay failed:\n${failure.stderr || failure.message || String(error)}`
  }
}

export default Plugin.define({
  id: "assay",
  async setup(ctx) {
    await ctx.command.transform((editor) => {
      editor.add({
        name: "assay",
        description: "Run quality-harness: /assay <command> [args...]",
        execute: async ({ sessionID, prompt }) => {
          const text = (prompt.text || "").trim()
          const tokens = text.split(/\s+/).filter(Boolean)
          const subcommands = new Set(["run", "status", "report", "abort", "personas", "verify-evidence", "auth", "quota", "ledger", "signup", "billing", "calibrate", "sarif", "junit", "sign-report", "verify-report", "prune"])
          let args: string[]
          if (tokens.length === 0) {
            args = ["run", "--target", ".", "--no-browser", "--personas-dir", "/Users/skaparwan/github/assay/personas"]
          } else if (subcommands.has(tokens[0])) {
            args = tokens
          } else {
            // Treat a plain-language request as a risk-based run with that intent.
            args = ["run", "--target", ".", "--no-browser", "--personas-dir", "/Users/skaparwan/github/assay/personas", "--intent", text]
          }
          const output = runAssay(args)
          await ctx.session.prompt({
            sessionID,
            text: `Assay output:\n\n\`\`\`\n${output}\n\`\`\`\n\nReport this output only. Do not run any additional commands or tools.`,
          })
        },
      })
    })
  },
})
