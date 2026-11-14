import assert from "node:assert/strict"
import test from "node:test"

import {
  CONTEXT_PROTOCOL,
  buildBrowserAgentPrompt,
  shellQuote,
} from "../../.opencode/plugins/assay/browser-agent-prompt.ts"

const INPUT = {
  url: "https://testradius.dev",
  args: [
    "run",
    "--target",
    "https://testradius.dev",
    "--personas-dir",
    "/repo/personas",
    "--intent",
    "Assay browser assessment",
    "--crawl",
    "--max-pages",
    "8",
    "--mobile",
    "--headed",
    "--active",
  ],
  projectDirectory: "/work/project",
  repoRoot: "/repo",
  python: "/usr/local/bin/python3.11",
  ssrfAllowlist: "127.0.0.0/8,::1,localhost",
}

test("context protocol requires the native question tool and forbids placeholder waits", () => {
  assert.match(CONTEXT_PROTOCOL, /native `question` tool/)
  assert.match(CONTEXT_PROTOCOL, /Never run `echo`/)
  assert.match(CONTEXT_PROTOCOL, /Never ask for or accept a raw token/)
  assert.match(CONTEXT_PROTOCOL, /Skip/)
  assert.match(CONTEXT_PROTOCOL, /Paste/)
  assert.match(CONTEXT_PROTOCOL, /MCP/)
  assert.match(CONTEXT_PROTOCOL, /Secure env\/secret ref/)
  assert.match(CONTEXT_PROTOCOL, /one decision per question/)
})

test("context protocol routes Jira through the native four-field form tool", () => {
  assert.match(CONTEXT_PROTOCOL, /Jira form/)
  assert.match(CONTEXT_PROTOCOL, /assay_jira_context/)
  assert.match(CONTEXT_PROTOCOL, /site, issue key, email, API token/)
  assert.match(CONTEXT_PROTOCOL, /token is consumed by the tool in memory/i)
  assert.match(CONTEXT_PROTOCOL, /do not ask the user to paste it into chat/i)
})

test("browser agent prompt embeds the target, command, and context flow", () => {
  const prompt = buildBrowserAgentPrompt(INPUT)
  assert.match(prompt, /https:\/\/testradius\.dev/)
  assert.match(prompt, /python3\.11 -P -m harness/)
  assert.match(prompt, /--crawl/)
  assert.match(prompt, /--max-pages/)
  assert.match(prompt, /--mobile/)
  assert.match(prompt, /--headed/)
  assert.match(prompt, /--active/)
  assert.match(prompt, /Context gathering UX/)
  assert.match(prompt, /question/)
  assert.match(prompt, /Skip/)
  assert.match(prompt, /no repeated commands/i)
  assert.match(prompt, /coverage gaps, never passes/i)
  assert.match(prompt, /No secrets/)
})

test("shellQuote escapes single quotes for shell safety", () => {
  assert.equal(shellQuote("plain"), "'plain'")
  assert.equal(shellQuote("it's"), "'it'\\''s'")
})
