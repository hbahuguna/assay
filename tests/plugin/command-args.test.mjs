import assert from "node:assert/strict"
import test from "node:test"

import {
  assayInvocation,
  browserInvocation,
  extractUrl,
  isUrl,
} from "../../.opencode/plugins/assay/command-args.ts"

const PERSONAS = "/tmp/personas"

test("extractUrl finds a URL in a free-form request", () => {
  assert.equal(
    extractUrl("please check https://example.com/app?x=1 now"),
    "https://example.com/app?x=1",
  )
  assert.equal(extractUrl("no url here"), null)
})

test("isUrl only accepts absolute http(s) targets", () => {
  assert.equal(isUrl("http://localhost:3000"), true)
  assert.equal(isUrl("https://example.com"), true)
  assert.equal(isUrl("ftp://example.com"), false)
  assert.equal(isUrl("."), false)
})

test("browserInvocation refuses to run without a URL", () => {
  const invocation = browserInvocation("", PERSONAS)
  assert.equal(invocation.args.length, 0)
  assert.match(invocation.error, /needs a reachable URL/)
})

test("browserInvocation refuses a directory-only target", () => {
  const invocation = browserInvocation("./my-app", PERSONAS)
  assert.equal(invocation.args.length, 0)
  assert.ok(invocation.error)
})

test("browserInvocation targets the URL without --no-browser", () => {
  const invocation = browserInvocation("http://localhost:3000", PERSONAS)
  assert.deepEqual(invocation.args, [
    "run",
    "--target",
    "http://localhost:3000",
    "--personas-dir",
    PERSONAS,
    "--intent",
    "Assay browser assessment",
    "--crawl",
    "--max-pages",
    "8",
    "--mobile",
    "--headed",
  ])
  assert.equal(invocation.args.includes("--no-browser"), false)
})

test("browserInvocation defaults to a comprehensive visible assessment", () => {
  const invocation = browserInvocation("http://localhost:3000", PERSONAS)
  assert.ok(invocation.args.includes("--crawl"))
  assert.equal(invocation.args[invocation.args.indexOf("--max-pages") + 1], "8")
  assert.ok(invocation.args.includes("--mobile"))
  assert.ok(invocation.args.includes("--headed"))
})

test("browserInvocation lets explicit flags override the defaults", () => {
  const invocation = browserInvocation(
    "http://localhost:3000 --no-crawl --max-pages 3 --no-mobile --headless",
    PERSONAS,
  )
  assert.ok(invocation.args.includes("--no-crawl"))
  assert.equal(invocation.args[invocation.args.indexOf("--max-pages") + 1], "3")
  assert.ok(invocation.args.includes("--no-mobile"))
  assert.ok(invocation.args.includes("--headless"))
  assert.equal(invocation.args.includes("--crawl"), false)
  assert.equal(invocation.args.includes("--mobile"), false)
  assert.equal(invocation.args.includes("--headed"), false)
})

test("browserInvocation passes safe browser flags through", () => {
  const invocation = browserInvocation(
    "http://localhost:3000 --headed --mobile --viewport 390x844 --no-browser --target https://evil.example",
    PERSONAS,
  )
  assert.deepEqual(invocation.args.slice(-4), [
    "--headed",
    "--mobile",
    "--viewport",
    "390x844",
  ])
  assert.equal(invocation.args.includes("--no-browser"), false)
  assert.equal(invocation.args.filter((arg) => arg === "--target").length, 1)
})

test("assayInvocation uses an explicit URL as a browser target", () => {
  const invocation = assayInvocation("check https://example.com", PERSONAS)
  assert.equal(invocation.args.includes("--no-browser"), false)
  assert.equal(invocation.args[invocation.args.indexOf("--target") + 1], "https://example.com")
})

test("assayInvocation keeps the static default when no URL is present", () => {
  const invocation = assayInvocation("review release readiness", PERSONAS)
  assert.ok(invocation.args.includes("--no-browser"))
  assert.equal(invocation.args[invocation.args.indexOf("--target") + 1], ".")
})

test("assayInvocation passes CLI subcommands through unchanged", () => {
  const invocation = assayInvocation("status --root .harness", PERSONAS)
  assert.deepEqual(invocation.args, ["status", "--root", ".harness"])
})
