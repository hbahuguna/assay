import assert from "node:assert/strict"
import test from "node:test"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

import {
  commandToArgs,
  isAllowedCommand,
  parseDecision,
  runAgentLoop,
} from "../../.opencode/plugins/assay/agent.ts"

const PERSONAS = "/tmp/personas"

function tempCwd() {
  return mkdtempSync(join(tmpdir(), "assay-agent-"))
}

async function runInteractive(options, deps, inputs = []) {
  const cwd = options.cwd || tempCwd()
  const base = { ...options, cwd }
  await runAgentLoop(base, deps)
  for (const input of inputs) {
    await runAgentLoop({ ...base, userInput: input }, deps)
  }
  return cwd
}

test("parseDecision extracts the JSON object", () => {
  assert.deepEqual(parseDecision('{"command":"run --target ."}'), {
    command: "run --target .",
  })
  assert.equal(parseDecision("no json here"), null)
})

test("isAllowedCommand enforces the harness allowlist", () => {
  assert.equal(isAllowedCommand("run --target ."), true)
  assert.equal(isAllowedCommand("assay-browser http://localhost:3000"), true)
  assert.equal(isAllowedCommand("rm -rf /"), false)
})

test("commandToArgs maps assay commands to harness args", () => {
  const demo = commandToArgs("assay-demo http://localhost:3000", PERSONAS)
  assert.equal(demo.args[demo.args.indexOf("--target") + 1], "http://localhost:3000")
  assert.equal(demo.args.includes("--no-browser"), false)

  const staticDemo = commandToArgs("assay-demo", PERSONAS)
  assert.ok(staticDemo.args.includes("--no-browser"))
})

test("runAgentLoop asks for context after initial run and can finish after skip", async () => {
  const calls = []
  const finalMessages = []
  let asks = 0
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: `run-${calls.length}` })
    },
    askLlm: async () => {
      asks += 1
      return JSON.stringify({
        done: true,
        summary: "All good.",
        fixes: ["Fix the XSS"],
      })
    },
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  const cwd = await runInteractive({ goal: "test goal", personasDir: PERSONAS, minHarnessRuns: 1 }, deps, ["[context:skip]"])
  try {
    assert.equal(calls.length, 1)
    assert.match(finalMessages[0], /Context requested/i)
    assert.match(finalMessages.at(-1), /All good/)
    assert.match(finalMessages.at(-1), /run-1/)
    assert.match(finalMessages.at(-1), /Fix the XSS/)
  } finally {
    rmSync(cwd, { recursive: true, force: true })
  }
})

test("runAgentLoop executes an allowed browser command after context skip", async () => {
  const calls = []
  let asks = 0
  const finalMessages = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: `run-${calls.length}` })
    },
    askLlm: async () => {
      asks += 1
      if (asks === 1) {
        return JSON.stringify({ command: "assay-browser http://localhost:3000", reason: "check UI" })
      }
      return JSON.stringify({ done: true, summary: "Done", fixes: [] })
    },
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  const cwd = await runInteractive({ goal: "goal", personasDir: PERSONAS, minHarnessRuns: 2 }, deps, ["[context:skip]", "[context:skip]"])
  try {
    const browserCall = calls.find((args) => args.includes("http://localhost:3000"))
    assert.ok(browserCall, "browser command should be executed")
    assert.equal(browserCall.includes("--no-browser"), false)
    assert.match(finalMessages.at(-1), /Done/)
    assert.match(finalMessages.at(-1), /## Run IDs\nrun-1, run-2/)
  } finally {
    rmSync(cwd, { recursive: true, force: true })
  }
})

test("runAgentLoop respects initial invocation for demo and requests context", async () => {
  const calls = []
  const finalMessages = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: "run-demo" })
    },
    askLlm: async () => JSON.stringify({ done: true, summary: "Demo checked", fixes: [] }),
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  const cwd = await runInteractive(
    {
      goal: "Assay demo assessment",
      personasDir: PERSONAS,
      minHarnessRuns: 1,
      initialArgs: ["run", "--target", "http://localhost:3000", "--personas-dir", PERSONAS, "--intent", "Assay demo assessment"],
    },
    deps,
  )
  try {
    assert.equal(calls[0][calls[0].indexOf("--target") + 1], "http://localhost:3000")
    assert.equal(calls[0].includes("--no-browser"), false)
    assert.match(finalMessages.at(-1), /Context requested/i)
  } finally {
    rmSync(cwd, { recursive: true, force: true })
  }
})

test("runAgentLoop rejects early done and continues until minimum runs after context skips", async () => {
  const calls = []
  let asks = 0
  const finalMessages = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: `run-${calls.length}` })
    },
    askLlm: async () => {
      asks += 1
      // first ask (after initial+skip) tries to finish after initial run -> should be rejected and ask for context
      if (asks === 1) {
        return JSON.stringify({ done: true, summary: "Too soon", fixes: [] })
      }
      // second ask (after skip again) must provide a command
      if (asks === 2) {
        return JSON.stringify({ command: "status --root .harness", reason: "check state" })
      }
      // after second harness run + skip, finishing is allowed
      return JSON.stringify({ done: true, summary: "Thorough enough", fixes: [] })
    },
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  const cwd = await runInteractive(
    { goal: "goal", personasDir: PERSONAS, minHarnessRuns: 2, maxIterations: 5 },
    deps,
    ["[context:skip]", "[context:skip]", "[context:skip]"],
  )

  try {
    assert.equal(calls.length, 2, "should run initial + one follow-up before finishing")
    assert.equal(calls[1][0], "status")
    assert.match(finalMessages.at(-1), /Thorough enough/)
    assert.match(finalMessages.at(-1), /run-1, run-2/)
  } finally {
    rmSync(cwd, { recursive: true, force: true })
  }
})
