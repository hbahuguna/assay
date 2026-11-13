import { execFile } from "node:child_process"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { promisify } from "node:util"

const execFileAsync = promisify(execFile)
const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = process.env.ASSAY_ROOT || resolve(PLUGIN_DIR, "../../..")

export const PYTHON = process.env.ASSAY_PYTHON || "/usr/local/bin/python3.11"

export async function runAssay(
  args: string[],
  cwd: string,
  env?: NodeJS.ProcessEnv,
): Promise<string> {
  try {
    const { stdout, stderr } = await execFileAsync(PYTHON, ["-P", "-m", "harness", ...args], {
      encoding: "utf-8",
      cwd,
      env: { ...(env || process.env), PYTHONPATH: REPO_ROOT },
    })
    return stdout || stderr || ""
  } catch (error: any) {
    const failure = error as { stdout?: string; stderr?: string; message?: string }
    return `assay failed:\n${failure.stderr || failure.stdout || failure.message || String(error)}`
  }
}

export function browserEnv(): NodeJS.ProcessEnv {
  const env = { ...process.env }
  if (!env.QH_SSRF_ALLOWLIST) {
    env.QH_SSRF_ALLOWLIST = "127.0.0.0/8,::1,localhost"
  }
  return env
}
