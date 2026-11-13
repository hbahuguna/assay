/**
 * Pure argument builders for the OpenCode `/assay*` commands.
 *
 * Keeping this logic free of plugin/runtime imports lets it be unit-tested
 * with `node --test` and avoids regressing the browser command back into a
 * `--no-browser` static scan.
 */

export const SUBCOMMANDS = new Set([
  "run",
  "status",
  "report",
  "abort",
  "personas",
  "verify-evidence",
  "auth",
  "quota",
  "ledger",
  "signup",
  "billing",
  "calibrate",
  "sarif",
  "junit",
  "sign-report",
  "verify-report",
  "prune",
])

const BROWSER_BOOLEAN_FLAGS = new Set([
  "--headed",
  "--headless",
  "--mobile",
  "--mobile-pack",
  "--responsive",
  "--no-mobile",
  "--crawl",
  "--no-crawl",
  "--comprehensive",
  "--active",
  "--no-active",
  "--browser-har",
  "--browser-trace",
  "--browser-video",
])

const BROWSER_VALUE_FLAGS = new Set([
  "--viewport",
  "--mobile-viewport",
  "--timeout",
  "--kill-on",
  "--max-checks",
  "--max-pages",
  "--fail-on",
  "--min-score",
  "--seed",
])

export interface Invocation {
  args: string[]
  error?: string
}

/** Return the first http(s) URL in a free-form request, if any. */
export function extractUrl(text: string): string | null {
  const match = /\bhttps?:\/\/[^\s"'`<>]+/i.exec(text || "")
  if (!match) {
    return null
  }
  return match[0].replace(/[),.;:!?]+$/g, "")
}

/** Return true when the value is an absolute http(s) URL. */
export function isUrl(value: string): boolean {
  return /^https?:\/\//i.test((value || "").trim())
}

function browserExtraArgs(text: string, url: string): string[] {
  const index = text.indexOf(url)
  const remainder = index >= 0 ? text.slice(index + url.length) : ""
  const tokens = remainder.split(/\s+/).filter(Boolean)
  const args: string[] = []
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i]
    if (BROWSER_BOOLEAN_FLAGS.has(token)) {
      args.push(token)
    } else if (
      BROWSER_VALUE_FLAGS.has(token) &&
      i + 1 < tokens.length &&
      !tokens[i + 1].startsWith("-")
    ) {
      args.push(token, tokens[i + 1])
      i += 1
    }
  }
  return args
}

/**
 * Build a live browser run. Unlike the static category wrappers this must
 * never pass `--no-browser`, and it refuses to run without a real URL so a
 * missing target cannot masquerade as browser coverage.
 */
export function browserInvocation(text: string, personasDir: string): Invocation {
  const trimmed = (text || "").trim()
  const url = extractUrl(trimmed)
  if (!url) {
    return {
      args: [],
      error:
        "`/assay-browser` runs the live Playwright Operate checks and needs a reachable URL.\n\n" +
        "Example:\n\n```\n/assay-browser http://localhost:3000\n```\n\n" +
        "A directory target only records static scope. If Playwright or Chromium is missing, " +
        "the browser checks are reported as `blocked` coverage gaps, never as a pass.",
    }
  }
  return {
    args: [
      "run",
      "--target",
      url,
      "--personas-dir",
      personasDir,
      "--intent",
      "Assay browser assessment",
      ...browserDefaults(trimmed, url),
      ...browserExtraArgs(trimmed, url),
    ],
  }
}

/**
 * A bare `/assay-browser <url>` should feel like a full browser assessment:
 * open a window, crawl the same-origin surface, and include the mobile pack.
 * Explicit user flags always win over these defaults.
 */
function browserDefaults(text: string, url: string): string[] {
  const extra = browserExtraArgs(text, url)
  const defaults: string[] = []
  const hasCrawlChoice =
    extra.includes("--crawl") ||
    extra.includes("--no-crawl") ||
    extra.includes("--comprehensive")
  const hasMobileChoice =
    extra.includes("--mobile") ||
    extra.includes("--mobile-pack") ||
    extra.includes("--responsive") ||
    extra.includes("--no-mobile") ||
    extra.includes("--comprehensive")
  const hasHeadChoice = extra.includes("--headed") || extra.includes("--headless")
  const hasActiveChoice = extra.includes("--active") || extra.includes("--no-active")
  if (!hasCrawlChoice) {
    defaults.push("--crawl")
  }
  if (!extra.includes("--max-pages")) {
    defaults.push("--max-pages", "8")
  }
  if (!hasMobileChoice) {
    defaults.push("--mobile")
  }
  if (!hasHeadChoice) {
    defaults.push("--headed")
  }
  if (!hasActiveChoice) {
    defaults.push("--active")
  }
  return defaults
}

/**
 * `/assay-demo` is a demo-surface inventory by default. If the operator
 * supplies a URL, run the real browser checks against it and let those checks
 * represent the demo category; never claim behavioral evidence without a
 * target.
 */
export function demoInvocation(text: string, personasDir: string): Invocation {
  const trimmed = (text || "").trim()
  const url = extractUrl(trimmed)
  if (url) {
    return {
      args: [
        "run",
        "--target",
        url,
        "--personas-dir",
        personasDir,
        "--intent",
        "Assay demo assessment",
        ...browserDefaults(trimmed, url),
        ...browserExtraArgs(trimmed, url),
      ],
    }
  }
  return {
    args: [
      "run",
      "--target",
      ".",
      "--no-browser",
      "--personas-dir",
      personasDir,
      "--intent",
      "Assay demo assessment",
    ],
  }
}

/** Build the generic `/assay` invocation from CLI-style or free-form input. */
export function assayInvocation(text: string, personasDir: string): Invocation {
  const trimmed = (text || "").trim()
  const tokens = trimmed.split(/\s+/).filter(Boolean)
  if (tokens.length === 0) {
    return {
      args: ["run", "--target", ".", "--no-browser", "--personas-dir", personasDir],
    }
  }
  if (tokens[0] === "--version" || tokens[0] === "-v") {
    return { args: ["--version"] }
  }
  if (tokens[0] === "help" || tokens[0] === "--help" || tokens[0] === "-h") {
    return { args: ["--help"] }
  }
  if (SUBCOMMANDS.has(tokens[0])) {
    return { args: tokens }
  }
  const url = extractUrl(trimmed)
  if (url) {
    return {
      args: ["run", "--target", url, "--personas-dir", personasDir, "--intent", trimmed],
    }
  }
  return {
    args: [
      "run",
      "--target",
      ".",
      "--no-browser",
      "--personas-dir",
      personasDir,
      "--intent",
      trimmed,
    ],
  }
}
