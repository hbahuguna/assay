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

test("runAgentLoop stops when the LLM reports done after minimum runs", async () => {
  const calls = []
  const finalMessages = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: `run-${calls.length}` })
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

  await runAgentLoop({ goal: "test goal", personasDir: PERSONAS, minHarnessRuns: 1 }, deps)

  assert.equal(calls.length, 1)
  assert.match(finalMessages.at(-1), /All good/)
  assert.match(finalMessages.at(-1), /run-1/)
  assert.match(finalMessages.at(-1), /Fix the XSS/)
})

test("runAgentLoop executes an allowed browser command", async () => {
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

  await runAgentLoop({ goal: "goal", personasDir: PERSONAS, minHarnessRuns: 2 }, deps)
  const browserCall = calls.find((args) => args.includes("http://localhost:3000"))
  assert.ok(browserCall, "browser command should be executed")
  assert.equal(browserCall.includes("--no-browser"), false)
  assert.match(finalMessages.at(-1), /Run IDs: run-1, run-2/)
})

test("runAgentLoop respects initial invocation for demo", async () => {
  const calls = []
  const deps = {
    runHarness: async (args) => {
      calls.push(args)
      return JSON.stringify({ run_id: "run-demo" })
    },
    askLlm: async () => JSON.stringify({ done: true, summary: "Demo checked", fixes: [] }),
    sendFinal: async () => {},
  }

  await runAgentLoop(
    {
      goal: "Assay demo assessment",
      personasDir: PERSONAS,
      minHarnessRuns: 1,
      initialArgs: ["run", "--target", "http://localhost:3000", "--personas-dir", PERSONAS, "--intent", "Assay demo assessment"],
    },
    deps,
  )

  assert.equal(calls[0][calls[0].indexOf("--target") + 1], "http://localhost:3000")
  assert.equal(calls[0].includes("--no-browser"), false)
})

test("runAgentLoop rejects early done and continues until minimum runs", async () => {
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
      // first ask tries to finish after initial run -> should be rejected
      if (asks === 1) {
        return JSON.stringify({ done: true, summary: "Too soon", fixes: [] })
      }
      // second ask must provide a command
      if (asks === 2) {
        return JSON.stringify({ command: "status --root .harness", reason: "check state" })
      }
      // after second harness run, finishing is allowed
      return JSON.stringify({ done: true, summary: "Thorough enough", fixes: [] })
    },
    sendFinal: async (text) => {
      finalMessages.push(text)
    },
  }

  await runAgentLoop({ goal: "goal", personasDir: PERSONAS, minHarnessRuns: 2, maxIterations: 5 }, deps)

  assert.equal(calls.length, 2, "should run initial + one follow-up before finishing")
  assert.equal(calls[1][0], "status")
  assert.match(finalMessages.at(-1), /Thorough enough/)
  assert.match(finalMessages.at(-1), /run-1, run-2/)
})
