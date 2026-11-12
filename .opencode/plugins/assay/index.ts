import { Plugin } from "@opencode/plugin"
import { execFileSync } from "node:child_process"

const PYTHON = process.env.ASSAY_PYTHON || "/usr/local/bin/python3.11"

function runAssay(args: string[]): string {
  try {
    return execFileSync(PYTHON, ["-m", "harness", ...args], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
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
          const args = (prompt.text || "").trim().split(/\s+/).filter(Boolean)
          const output = runAssay(args)
          await ctx.session.synthetic({ sessionID, text: output })
        },
      })
    })
  },
})
