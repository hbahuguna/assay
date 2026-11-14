/**
 * Native Jira context form + fetch flow for the Assay agent.
 *
 * The form is rendered by OpenCode's native Form API, so the user gets a
 * single four-field dialog (site, issue key, email, API token) instead of a
 * sequence of prose questions. The token is used only inside this module to
 * call Jira; it is never returned to the model, written to harness state, or
 * included in tool output. Prefer a scoped token or `tokenRef` for production.
 */

import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export interface JiraFormPrefill {
  site?: string
  issueKey?: string
  email?: string
  tokenRef?: string
}

export interface JiraFormApi {
  create(sessionID: string, payload: JiraFormPayload): Promise<JiraFormInfo>
  get(sessionID: string, formID: string): Promise<JiraFormDetail>
}

export interface JiraFormPayload {
  title: string
  metadata: Record<string, unknown>
  fields: JiraFormField[]
}

export interface JiraFormField {
  key: string
  type: "string"
  title: string
  description?: string
  placeholder?: string
  required?: boolean
  format?: "email" | "uri"
  default?: string
}

export interface JiraFormInfo {
  id: string
  sessionID?: string
  title?: string
  fields?: JiraFormField[]
}

export interface JiraFormDetail extends JiraFormInfo {
  state?: {
    status: "pending" | "answered" | "cancelled"
    answer?: Record<string, unknown>
    message?: string
  }
}

export interface JiraIssueContext {
  source: "jira"
  site: string
  issueKey: string
  url: string
  summary: string
  description: string
  status?: string
  priority?: string
  type?: string
  labels: string[]
  assignee?: string
  reporter?: string
  created?: string
  updated?: string
  resolution?: string
  fixVersions: string[]
  components: string[]
  parent?: { key?: string; summary?: string }
  subtasks: Array<{ key?: string; summary?: string; status?: string }>
  links: Array<{ type?: string; inward?: { key?: string; summary?: string }; outward?: { key?: string; summary?: string } }>
  customFields: Record<string, unknown>
}

const JIRA_FORM_TIMEOUT_MS = 10 * 60 * 1000

export function buildJiraFormFields(prefill: JiraFormPrefill = {}): JiraFormField[] {
  const fields: JiraFormField[] = [
    {
      key: "jira_site",
      type: "string",
      title: "Jira site",
      description: "Base URL, e.g. https://yourorg.atlassian.net (https:// is added if omitted)",
      placeholder: "https://yourorg.atlassian.net",
      required: true,
    },
    {
      key: "jira_issue_key",
      type: "string",
      title: "Jira issue key",
      description: "Issue key or full issue URL, e.g. PROJ-123",
      placeholder: "PROJ-123",
      required: true,
    },
    {
      key: "jira_email",
      type: "string",
      format: "email",
      title: "Jira email",
      description: "Email for your Jira Cloud account. Used only for this request.",
      placeholder: "you@example.com",
      required: true,
    },
  ]
  if (prefill.site) fields[0].default = prefill.site
  if (prefill.issueKey) fields[1].default = prefill.issueKey
  if (prefill.email) fields[2].default = prefill.email
  if (!prefill.tokenRef) {
    fields.push({
      key: "jira_token",
      type: "string",
      title: "Jira API token",
      description:
        "Used only in-memory for this Jira request and not written to harness state, evidence, or logs. OpenCode keeps form answers in its local form store, so use a scoped, revocable token.",
      placeholder: "Paste API token",
      required: true,
    })
  }
  return fields
}

const DEFAULT_OPENCODE_URL = "http://127.0.0.1:49374"

export interface LocalServiceConfig {
  baseUrl: string
  auth: string
}

/**
 * Locate the local OpenCode server so the plugin can call the native Form API
 * when the plugin runtime does not expose `ctx.session.form`.
 *
 * The server password is read from the user-private service config (or the
 * ASSAY_OPENCODE_PASSWORD override) and is only ever used for the
 * Authorization header. It is never logged, returned, or persisted.
 */
export function readLocalServiceConfig(): LocalServiceConfig | null {
  const configuredPassword = process.env.ASSAY_OPENCODE_PASSWORD || ""
  let password = configuredPassword
  if (!password) {
    try {
      const path = join(homedir(), ".config", "opencode", "service.json")
      const parsed = JSON.parse(readFileSync(path, "utf8"))
      if (typeof parsed?.password === "string") password = parsed.password
    } catch {
      return null
    }
  }
  if (!password) return null

  const configuredPort = process.env.ASSAY_OPENCODE_PORT || ""
  const baseUrl = (
    process.env.ASSAY_OPENCODE_URL ||
    (configuredPort ? `http://127.0.0.1:${configuredPort}` : DEFAULT_OPENCODE_URL)
  ).replace(/\/+$/, "")

  return {
    baseUrl,
    auth: "Basic " + Buffer.from(`opencode:${password}`).toString("base64"),
  }
}

export function createHttpFormApi(
  baseUrl: string,
  auth: string,
  fetchImpl: typeof fetch = fetch,
): JiraFormApi {
  const root = baseUrl.replace(/\/+$/, "")
  const request = async (path: string, init: any = {}) => {
    const response = await fetchImpl(`${root}${path}`, {
      ...init,
      headers: {
        Authorization: auth,
        "Content-Type": "application/json",
        ...(init.headers || {}),
      },
    })
    if (!response.ok) {
      throw new Error(`native Form API request failed with HTTP ${response.status}`)
    }
    const payload = await response.json()
    return (payload?.data ?? payload) as any
  }

  return {
    create: async (sessionID, payload) => {
      const data = await request(`/api/session/${encodeURIComponent(sessionID)}/form`, {
        method: "POST",
        body: JSON.stringify(payload),
      })
      return data as JiraFormInfo
    },
    get: async (sessionID, formID) => {
      const data = await request(
        `/api/session/${encodeURIComponent(sessionID)}/form/${encodeURIComponent(formID)}`,
      )
      return data as JiraFormDetail
    },
  }
}

export function getJiraFormApi(ctx: any): JiraFormApi {
  const sessionForm = ctx?.session?.form
  if (typeof sessionForm?.create === "function" && typeof sessionForm?.get === "function") {
    return {
      create: async (sessionID, payload) => {
        const res = await sessionForm.create({ sessionID, ...payload })
        return (res?.data ?? res) as JiraFormInfo
      },
      get: async (sessionID, formID) => {
        const res = await sessionForm.get({ sessionID, formID })
        return (res?.data ?? res) as JiraFormDetail
      },
    }
  }

  // Fallback: the plugin context also exposes the raw SDK client, which can
  // call the experimental native Form API. The client carries the server
  // authentication and base URL.
  const raw = ctx?.client?._client
  if (typeof raw?.post === "function" && typeof raw?.get === "function") {
    return {
      create: async (sessionID, payload) => {
        const res = await raw.post({
          url: "/api/session/{sessionID}/form",
          path: { sessionID },
          headers: { "Content-Type": "application/json" },
          body: payload,
        })
        return (res?.data?.data ?? res?.data ?? res) as JiraFormInfo
      },
      get: async (sessionID, formID) => {
        const res = await raw.get({
          url: "/api/session/{sessionID}/form/{formID}",
          path: { sessionID, formID },
        })
        return (res?.data?.data ?? res?.data ?? res) as JiraFormDetail
      },
    }
  }

  // Some server plugin builds do not attach a form client to the context. The
  // local server still exposes the same Form API over HTTP, so use the
  // user-private service config when it is available.
  const local = readLocalServiceConfig()
  if (local) return createHttpFormApi(local.baseUrl, local.auth)

  throw new Error("native Form API is unavailable in this OpenCode runtime")
}

export async function collectJiraForm(
  api: JiraFormApi,
  sessionID: string,
  prefill: JiraFormPrefill = {},
  signal?: AbortSignal,
): Promise<Record<string, unknown> | null> {
  const created = await api.create(sessionID, {
    title: "Jira context",
    metadata: { kind: "assay.jira" },
    fields: buildJiraFormFields(prefill),
  })
  const formID = created?.id
  if (!formID) throw new Error("Jira form creation returned no form id")

  const deadline = Date.now() + JIRA_FORM_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (signal?.aborted) return null
    const detail = await api.get(sessionID, formID)
    const state = detail?.state
    if (state?.status === "answered") return state.answer || {}
    if (state?.status === "cancelled") return null
    await sleep(500, signal)
  }
  return null
}

export function parseJiraTarget(siteRaw: string, issueRaw: string): { site: string; issueKey: string } {
  let site = String(siteRaw || "").trim()
  let issue = String(issueRaw || "").trim()

  if (/^https?:\/\//i.test(issue)) {
    const url = new URL(issue)
    site = `${url.protocol}//${url.host}`
    const pathMatch = url.pathname.match(/\/browse\/([^/?#]+)/i) || url.pathname.match(/\/issues\/([^/?#]+)/i)
    if (pathMatch) issue = decodeURIComponent(pathMatch[1])
  }

  site = normalizeJiraSite(site)
  if (!issue) throw new Error("Jira issue key is required")

  // Accept "PROJ-123" (with surrounding text) or a numeric issue id.
  const keyMatch = issue.match(/\b([A-Z][A-Z0-9_]+-\d+)\b/i)
  issue = keyMatch ? keyMatch[1].toUpperCase() : issue.replace(/^\/+|\/+$/g, "")
  if (!issue) throw new Error("Jira issue key is required")

  return { site, issueKey: issue }
}

export function normalizeJiraSite(raw: string): string {
  let site = String(raw || "").trim()
  if (!site) throw new Error("Jira site is required")
  if (!/^https?:\/\//i.test(site)) site = `https://${site}`
  const url = new URL(site)
  if (!url.host) throw new Error("Jira site is not a valid URL")
  return `${url.protocol}//${url.host}`
}

export async function fetchJiraIssue(input: {
  site: string
  issueKey: string
  email: string
  token: string
  signal?: AbortSignal
  fetchImpl?: typeof fetch
}): Promise<JiraIssueContext> {
  const { site, issueKey } = parseJiraTarget(input.site, input.issueKey)
  const email = String(input.email || "").trim()
  const token = String(input.token || "").trim()
  if (!email) throw new Error("Jira email is required")
  if (!token) throw new Error("Jira API token is required")

  const url = `${site}/rest/api/2/issue/${encodeURIComponent(issueKey)}?expand=names&fields=*all`
  const auth = Buffer.from(`${email}:${token}`).toString("base64")
  const fetcher = input.fetchImpl || fetch
  const response = await fetcher(url, {
    headers: { Authorization: `Basic ${auth}`, Accept: "application/json" },
    signal: input.signal,
  })

  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      throw new Error(`Jira authentication failed (HTTP ${response.status}); check site, email, token, and permissions`)
    }
    if (response.status === 404) {
      throw new Error(`Jira issue not found (HTTP ${response.status}); check the issue key and site`)
    }
    throw new Error(`Jira request failed (HTTP ${response.status})`)
  }

  const data = await response.json()
  return summarizeJiraIssue(data, token, site)
}

export function summarizeJiraIssue(data: any, token: string, site: string): JiraIssueContext {
  const fields = data?.fields || {}
  const names = data?.names || {}
  const secrets = [token]
  const text = (value: unknown) => redactSecrets(adfToText(value), secrets)
  const customFields: Record<string, unknown> = {}

  for (const [id, value] of Object.entries(fields)) {
    if (!id.startsWith("customfield_")) continue
    const name = String((names as any)[id] || id)
    if (!/acceptance|criteria|story point|estimate|epic|sprint|team|environment|severity|impact/i.test(name)) continue
    if (value == null) continue
    if (typeof value === "number" || typeof value === "boolean") {
      customFields[name] = value
    } else if (typeof value === "string") {
      customFields[name] = redactSecrets(value, secrets)
    } else if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
      customFields[name] = value.map((item) => redactSecrets(String(item), secrets))
    } else if (typeof value === "object" && typeof (value as any).value === "string") {
      customFields[name] = redactSecrets(String((value as any).value), secrets)
    }
  }

  return {
    source: "jira",
    site,
    issueKey: String(data?.key || ""),
    url: `${site}/browse/${encodeURIComponent(String(data?.key || ""))}`,
    summary: text(fields.summary),
    description: text(fields.description),
    status: fields.status?.name,
    priority: fields.priority?.name,
    type: fields.issuetype?.name,
    labels: Array.isArray(fields.labels) ? fields.labels.map((label: unknown) => String(label)) : [],
    assignee: fields.assignee?.displayName,
    reporter: fields.reporter?.displayName,
    created: fields.created,
    updated: fields.updated,
    resolution: fields.resolution?.name,
    fixVersions: (fields.fixVersions || []).map((item: any) => item?.name).filter(Boolean),
    components: (fields.components || []).map((item: any) => item?.name).filter(Boolean),
    parent: fields.parent
      ? { key: fields.parent.key, summary: text(fields.parent.fields?.summary) }
      : undefined,
    subtasks: (fields.subtasks || []).map((item: any) => ({
      key: item?.key,
      summary: text(item?.fields?.summary),
      status: item?.fields?.status?.name,
    })),
    links: (fields.issuelinks || []).map((item: any) => ({
      type: item?.type?.name,
      inward: item?.inwardIssue
        ? { key: item.inwardIssue.key, summary: text(item.inwardIssue.fields?.summary) }
        : undefined,
      outward: item?.outwardIssue
        ? { key: item.outwardIssue.key, summary: text(item.outwardIssue.fields?.summary) }
        : undefined,
    })),
    customFields,
  }
}

export function formatJiraContext(context: JiraIssueContext): string {
  return `Jira context fetched (token redacted). Use it to refine the test plan and coverage.\n\n\`\`\`json\n${JSON.stringify(context, null, 2)}\n\`\`\``
}

export function adfToText(value: unknown): string {
  if (value == null) return ""
  if (typeof value === "string") return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (Array.isArray(value)) return value.map(adfToText).join("")
  if (typeof value !== "object") return ""

  const node = value as any
  if (node.type === "text") return String(node.text || "")
  if (node.type === "hardBreak") return "\n"
  if (node.type === "paragraph") return `${adfToText(node.content)}\n`
  if (node.type === "heading") return `\n${adfToText(node.content)}\n`
  if (node.type === "listItem") return `- ${adfToText(node.content).trim()}\n`
  if (node.type === "codeBlock") return `\n\`\`\`\n${adfToText(node.content)}\n\`\`\`\n`
  return adfToText(node.content)
}

export function redactSecrets(value: string, secrets: string[] = []): string {
  let output = String(value ?? "")
  for (const secret of secrets) {
    if (!secret || secret.length < 4) continue
    output = output.split(secret).join("[REDACTED]")
  }
  output = output.replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [REDACTED]")
  output = output.replace(/\bBasic\s+[A-Za-z0-9+/=._-]+/gi, "Basic [REDACTED]")
  output = output.replace(
    /(\b(?:api[_-]?key|token|password|secret|authorization|bearer)\b\s*[:=]\s*)([^\s,;]+)/gi,
    "$1[REDACTED]",
  )
  return output
}

export function sanitizeError(error: unknown, secrets: string[] = []): string {
  const message = error instanceof Error ? error.message : String(error)
  return redactSecrets(message, secrets)
}

export async function registerJiraContextTool(ctx: any): Promise<boolean> {
  if (typeof ctx?.tool?.transform !== "function") {
    process.stderr.write(
      `[assay-agent] Jira context tool: tool.transform unavailable (tool keys: ${Object.keys(ctx?.tool || {}).join(",") || "none"})\n`,
    )
    return false
  }

  await ctx.tool.transform((editor: any) => {
    editor.add({
      name: "assay_jira_context",
      description:
        "Open a native four-field Jira form (site, issue key, email, API token), fetch the issue, and return redacted Jira context for test planning. Use this for Jira context instead of asking for a token in chat.",
      input: {
        type: "object",
        properties: {
          site: { type: "string", description: "Optional Jira base URL to prefill." },
          issueKey: { type: "string", description: "Optional issue key or URL to prefill." },
          email: { type: "string", description: "Optional Jira email to prefill." },
          tokenRef: {
            type: "string",
            description: "Optional environment variable name holding the API token. Skips the token field.",
          },
        },
        additionalProperties: false,
      },
      execute: async (input: JiraFormPrefill = {}, context: any = {}) => {
        const sessionID = context.sessionID
        if (!sessionID) return { content: "Jira context unavailable: no active session. Record a context gap and continue." }

        // Resolve the Form API at execution time. At plugin setup the native
        // form client may not be attached yet; deferring keeps the tool
        // registered and lets a missing client surface as a coverage gap.
        let api: JiraFormApi
        try {
          api = getJiraFormApi(ctx)
        } catch (error) {
          return {
            content: `Jira context unavailable: ${sanitizeError(error)}. Record a context gap and continue with safe unique steps.`,
          }
        }

        let answer: Record<string, unknown> | null
        try {
          answer = await collectJiraForm(api, sessionID, input || {}, context.signal)
        } catch (error) {
          return {
            content: `Jira context unavailable: ${sanitizeError(error)}. Record a context gap and continue with safe unique steps.`,
          }
        }
        if (!answer) {
          return { content: "Jira context cancelled. Record a context gap and continue with safe unique steps." }
        }

        const token = input.tokenRef ? process.env[input.tokenRef] : String(answer.jira_token || "")
        if (!token) {
          const ref = input.tokenRef ? `environment variable ${input.tokenRef}` : "API token"
          return { content: `Jira context unavailable: ${ref} is missing. Record a context gap and continue.` }
        }

        try {
          const issue = await fetchJiraIssue({
            site: String(answer.jira_site || input.site || ""),
            issueKey: String(answer.jira_issue_key || input.issueKey || ""),
            email: String(answer.jira_email || input.email || ""),
            token,
            signal: context.signal,
          })
          return { content: formatJiraContext(issue) }
        } catch (error) {
          return {
            content: `Jira context unavailable: ${sanitizeError(error, [token])}. Record a context gap and continue with safe unique steps.`,
          }
        }
      },
    })
  })

  return true
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve()
    const onAbort = () => {
      clearTimeout(timer)
      resolve()
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort)
      resolve()
    }, ms)
    signal?.addEventListener("abort", onAbort, { once: true })
  })
}
