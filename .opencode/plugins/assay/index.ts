import { Plugin } from "@opencode/plugin"
import { execFile } from "node:child_process"
import { promises as fs } from "node:fs"
import { promisify } from "node:util"

const execFileAsync = promisify(execFile)

const PYTHON = process.env.ASSAY_PYTHON || "/usr/local/bin/python3.11"

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

async function runFollowUp(cwd: string): Promise<string> {
  const sections: string[] = []
  try {
    const { stdout } = await execFileAsync("git", ["diff", "--stat"], { cwd, encoding: "utf-8" })
    if (stdout.trim()) sections.push(`git diff --stat:\n${stdout.trim()}`)
  } catch {
    // Not a git repository or no changes.
  }

  try {
    const packageJsonPath = `${cwd}/package.json`
    const packageJson = JSON.parse(await fs.readFile(packageJsonPath, "utf8"))
    if (packageJson?.scripts?.test) {
      const command = (await fs.access(`${cwd}/pnpm-lock.yaml`).then(() => "pnpm").catch(() => "npm"))
      const { stdout, stderr } = await execFileAsync(command, ["test"], {
        cwd,
        encoding: "utf-8",
        env: { ...process.env, CI: "1" },
      })
      sections.push(`${command} test:\n${(stdout || stderr || "").trim()}`)
    }
  } catch {
    // No package test script or package manager failure.
  }

  try {
    const hasPytestConfig =
      (await fs.access(`${cwd}/pytest.ini`).then(() => true).catch(() => false)) ||
      (await fs.readFile(`${cwd}/pyproject.toml`, "utf8").then((text) => text.includes("[tool.pytest")).catch(() => false))
    if (hasPytestConfig) {
      const { stdout, stderr } = await execFileAsync(PYTHON, ["-m", "pytest", "-q"], {
        cwd,
        encoding: "utf-8",
        env: { ...process.env, PYTHONPATH: "/Users/skaparwan/github/assay" },
      })
      sections.push(`pytest:\n${(stdout || stderr || "").trim()}`)
    }
  } catch {
    // No Python test configuration.
  }

  if (sections.length === 0) return ""
  const combined = sections.join("\n\n")
  return combined.length > 8000 ? `${combined.slice(0, 8000)}\n... (truncated)` : combined
}

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
            args = ["run", "--target", ".", "--no-browser", "--personas-dir", "/Users/skaparwan/github/assay/personas"]
          } else if (tokens[0] === "--version" || tokens[0] === "-v") {
            args = ["--version"]
          } else if (tokens[0] === "help" || tokens[0] === "--help" || tokens[0] === "-h") {
            args = ["--help"]
          } else if (subcommands.has(tokens[0])) {
            args = tokens
          } else {
            // Treat a plain-language request as a risk-based run with that intent.
            args = ["run", "--target", ".", "--no-browser", "--personas-dir", "/Users/skaparwan/github/assay/personas", "--intent", text]
          }
          const output = await runAssay(args, projectDirectory)
          await ctx.session.prompt({
            sessionID,
            text: `Assay output:\n\n\`\`\`\n${output}\n\`\`\`\n\nSummarize these results, then investigate the highest-severity findings. Inspect the cited files and run the most relevant tests or checks. Continue until you have a concrete verification result or a clear blocker. Additional commands are allowed and expected when needed.`,
          })
        },
      })
    })
  },
})
