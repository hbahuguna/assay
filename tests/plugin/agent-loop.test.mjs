import assert from "node:assert/strict"
import test from "node:test"

import {
  commandToArgs,
  isAllowedCommand,
  parseDecision,
  runAgentLoop,
} from "../../.opencode/plugins/assay/agent.ts"

const PERSONAS = "/tmp/personas"

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

test("runAgentLoop stops when the LLM reports done", async () => {
  const calls = []
  const finalMessages = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: "20260101T000000Z-test" })
    },
    askLlm: async () =>
      JSON.stringify({
        done: true,
        summary: "All good.",
        fixes: ["Fix the XSS"],
      }),
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  await runAgentLoop("test goal", PERSONAS, deps)

  assert.equal(calls.length, 1)
  assert.match(finalMessages.at(-1), /All good/)
  assert.match(finalMessages.at(-1), /20260101T000000Z-test/)
  assert.match(finalMessages.at(-1), /Fix the XSS/)
})

test("runAgentLoop executes an allowed browser command", async () => {
  const calls = []
  let iteration = 0
  const finalMessages = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: `run-${calls.length}` })
    },
    askLlm: async () => {
      iteration += 1
      if (iteration === 1) {
        return JSON.stringify({ command: "assay-browser http://localhost:3000", reason: "check UI" })
      }
      return JSON.stringify({ done: true, summary: "Done", fixes: [] })
    },
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  await runAgentLoop("goal", PERSONAS, deps)
  const browserCall = calls.find((args) => args.includes("http://localhost:3000"))
  assert.ok(browserCall, "browser command should be executed")
  assert.equal(browserCall.includes("--no-browser"), false)
  assert.match(finalMessages.at(-1), /Run IDs: run-1, run-2/)
})
