import assert from "node:assert/strict"
import test from "node:test"

import {
  adfToText,
  buildJiraFormFields,
  collectJiraForm,
  createHttpFormApi,
  fetchJiraIssue,
  formatJiraContext,
  getJiraFormApi,
  parseJiraTarget,
  readLocalServiceConfig,
  redactSecrets,
  registerJiraContextTool,
  sanitizeError,
  summarizeJiraIssue,
} from "../../.opencode/plugins/assay/jira-context.ts"

const TOKEN = "jira_secret_token_123456"

test("buildJiraFormFields returns the four native Jira inputs in order", () => {
  const fields = buildJiraFormFields()
  assert.deepEqual(
    fields.map((field) => field.key),
    ["jira_site", "jira_issue_key", "jira_email", "jira_token"],
  )
  assert.match(fields[0].description, /https:\/\/ is added if omitted/)
  assert.equal(fields[2].format, "email")
  assert.equal(fields[3].required, true)
  assert.match(fields[3].description, /in-memory/)
  assert.match(fields[3].description, /not written to harness state/i)
  assert.match(fields[3].description, /local form store/i)
  assert.match(fields[3].description, /scoped, revocable token/i)
})

test("buildJiraFormFields uses a token reference instead of a token field", () => {
  const fields = buildJiraFormFields({ tokenRef: "JIRA_API_TOKEN", site: "https://acme.atlassian.net" })
  assert.deepEqual(
    fields.map((field) => field.key),
    ["jira_site", "jira_issue_key", "jira_email"],
  )
  assert.equal(fields[0].default, "https://acme.atlassian.net")
})

test("parseJiraTarget normalizes a site, key, and full issue URL", () => {
  assert.deepEqual(parseJiraTarget("acme.atlassian.net", "proj-123"), {
    site: "https://acme.atlassian.net",
    issueKey: "PROJ-123",
  })
  assert.deepEqual(parseJiraTarget("https://acme.atlassian.net/", "https://acme.atlassian.net/browse/PROJ-9"), {
    site: "https://acme.atlassian.net",
    issueKey: "PROJ-9",
  })
  assert.deepEqual(parseJiraTarget("https://jira.example.com", "12345"), {
    site: "https://jira.example.com",
    issueKey: "12345",
  })
  assert.throws(() => parseJiraTarget("", "PROJ-1"), /Jira site is required/)
  assert.throws(() => parseJiraTarget("https://acme.atlassian.net", ""), /Jira issue key is required/)
})

test("adfToText converts Jira document format to readable text", () => {
  const text = adfToText({
    type: "doc",
    content: [
      { type: "heading", content: [{ type: "text", text: "Acceptance criteria" }] },
      {
        type: "bulletList",
        content: [
          { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "First" }] }] },
          { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "Second" }] }] },
        ],
      },
    ],
  })
  assert.match(text, /Acceptance criteria/)
  assert.match(text, /- First/)
  assert.match(text, /- Second/)
})

test("redactSecrets removes the token and inline secret assignments", () => {
  const redacted = redactSecrets(
    `token=${TOKEN} password: hunter2 Authorization: Bearer abc123`,
    [TOKEN],
  )
  assert.ok(!redacted.includes(TOKEN))
  assert.ok(!redacted.includes("hunter2"))
  assert.ok(!redacted.includes("abc123"))
  assert.match(redacted, /\[REDACTED\]/)
})

test("summarizeJiraIssue extracts planning context and redacts the token", () => {
  const issue = {
    key: "PROJ-123",
    self: "https://acme.atlassian.net/rest/api/2/issue/10001",
    names: {
      customfield_10016: "Acceptance Criteria",
      customfield_10017: "Story Points",
      customfield_10018: "Unrelated Notes",
    },
    fields: {
      summary: "Checkout fails for returning users",
      description: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: `token=${TOKEN}` }] }] },
      status: { name: "In Progress" },
      priority: { name: "High" },
      issuetype: { name: "Bug" },
      labels: ["release-blocker"],
      assignee: { displayName: "Ada Lovelace" },
      reporter: { displayName: "Grace Hopper" },
      created: "2026-01-01T00:00:00.000+0000",
      updated: "2026-01-02T00:00:00.000+0000",
      resolution: null,
      fixVersions: [{ name: "1.2.0" }],
      components: [{ name: "Checkout" }],
      parent: { key: "PROJ-100", fields: { summary: "Checkout epic" } },
      subtasks: [{ key: "PROJ-124", fields: { summary: "Add regression test", status: { name: "To Do" } } }],
      issuelinks: [
        {
          type: { name: "Blocks" },
          outwardIssue: { key: "PROJ-200", fields: { summary: "Payment retry" } },
        },
      ],
      customfield_10016: "User can complete checkout with a saved card",
      customfield_10017: 3,
      customfield_10018: "not planning context",
    },
  }

  const context = summarizeJiraIssue(issue, TOKEN, "https://acme.atlassian.net")
  assert.equal(context.issueKey, "PROJ-123")
  assert.equal(context.status, "In Progress")
  assert.equal(context.priority, "High")
  assert.deepEqual(context.labels, ["release-blocker"])
  assert.deepEqual(context.fixVersions, ["1.2.0"])
  assert.equal(context.parent?.key, "PROJ-100")
  assert.equal(context.subtasks[0].key, "PROJ-124")
  assert.equal(context.links[0].outward?.key, "PROJ-200")
  assert.equal(context.customFields["Acceptance Criteria"], "User can complete checkout with a saved card")
  assert.equal(context.customFields["Story Points"], 3)
  assert.equal(context.customFields["Unrelated Notes"], undefined)
  assert.ok(!context.description.includes(TOKEN))
  assert.match(context.description, /\[REDACTED\]/)
})

test("formatJiraContext never includes the token", () => {
  const context = summarizeJiraIssue(
    {
      key: "PROJ-1",
      fields: { summary: "Safe summary", description: `token=${TOKEN}` },
      names: {},
    },
    TOKEN,
    "https://acme.atlassian.net",
  )
  const output = formatJiraContext(context)
  assert.ok(!output.includes(TOKEN))
  assert.match(output, /Jira context fetched/)
  assert.match(output, /```json/)
})

test("collectJiraForm creates the four-field form and returns the answer", async () => {
  const created = []
  let polls = 0
  const api = {
    create: async (sessionID, payload) => {
      created.push({ sessionID, payload })
      return { id: "frm_test" }
    },
    get: async () => {
      polls += 1
      if (polls === 1) return { id: "frm_test", state: { status: "pending" } }
      return {
        id: "frm_test",
        state: {
          status: "answered",
          answer: {
            jira_site: "https://acme.atlassian.net",
            jira_issue_key: "PROJ-123",
            jira_email: "qa@example.com",
            jira_token: TOKEN,
          },
        },
      }
    },
  }

  const answer = await collectJiraForm(api, "ses_test", {})
  assert.equal(created.length, 1)
  assert.equal(created[0].payload.title, "Jira context")
  assert.equal(created[0].payload.fields.length, 4)
  assert.equal(answer?.jira_issue_key, "PROJ-123")
  assert.equal(answer?.jira_token, TOKEN)
})

test("collectJiraForm returns null when the user cancels", async () => {
  const api = {
    create: async () => ({ id: "frm_cancel" }),
    get: async () => ({ id: "frm_cancel", state: { status: "cancelled" } }),
  }
  const answer = await collectJiraForm(api, "ses_test", {})
  assert.equal(answer, null)
})

test("getJiraFormApi uses the native session.form client when available", async () => {
  const calls = []
  const ctx = {
    session: {
      form: {
        create: async (input) => {
          calls.push(["create", input])
          return { id: "frm_native" }
        },
        get: async (input) => {
          calls.push(["get", input])
          return { id: "frm_native", state: { status: "pending" } }
        },
      },
    },
  }
  const api = getJiraFormApi(ctx)
  const info = await api.create("ses_1", { title: "Jira context", metadata: {}, fields: [] })
  const detail = await api.get("ses_1", "frm_native")
  assert.equal(info.id, "frm_native")
  assert.equal(detail.state.status, "pending")
  assert.deepEqual(calls[0][1], { sessionID: "ses_1", title: "Jira context", metadata: {}, fields: [] })
  assert.deepEqual(calls[1][1], { sessionID: "ses_1", formID: "frm_native" })
})

test("getJiraFormApi falls back to the raw Form API client", async () => {
  const calls = []
  const ctx = {
    client: {
      _client: {
        post: async (input) => {
          calls.push(["post", input])
          return { data: { data: { id: "frm_raw" } } }
        },
        get: async (input) => {
          calls.push(["get", input])
          return { data: { data: { id: "frm_raw", state: { status: "answered", answer: { jira_token: TOKEN } } } } }
        },
      },
    },
  }
  const api = getJiraFormApi(ctx)
  const info = await api.create("ses_2", { title: "Jira context", metadata: {}, fields: [] })
  const detail = await api.get("ses_2", "frm_raw")
  assert.equal(info.id, "frm_raw")
  assert.equal(detail.state.status, "answered")
  assert.equal(calls[0][1].url, "/api/session/{sessionID}/form")
  assert.deepEqual(calls[0][1].path, { sessionID: "ses_2" })
  assert.deepEqual(calls[0][1].headers, { "Content-Type": "application/json" })
  assert.equal(calls[1][1].url, "/api/session/{sessionID}/form/{formID}")
  assert.deepEqual(calls[1][1].path, { sessionID: "ses_2", formID: "frm_raw" })
})

test("fetchJiraIssue sends basic auth and returns redacted context", async () => {
  let request
  const fakeFetch = async (url, init) => {
    request = { url, init }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        key: "PROJ-123",
        self: "https://acme.atlassian.net/rest/api/2/issue/10001",
        names: { customfield_10016: "Acceptance Criteria" },
        fields: {
          summary: "Checkout bug",
          description: `token=${TOKEN}`,
          status: { name: "Open" },
          customfield_10016: "User can pay",
        },
      }),
    }
  }

  const context = await fetchJiraIssue({
    site: "https://acme.atlassian.net",
    issueKey: "PROJ-123",
    email: "qa@example.com",
    token: TOKEN,
    fetchImpl: fakeFetch,
  })

  assert.match(request.url, /\/rest\/api\/2\/issue\/PROJ-123/)
  assert.equal(request.init.headers.Authorization, `Basic ${Buffer.from(`qa@example.com:${TOKEN}`).toString("base64")}`)
  assert.equal(context.customFields["Acceptance Criteria"], "User can pay")
  assert.ok(!context.description.includes(TOKEN))
})

test("fetchJiraIssue maps auth failures to an actionable, token-free error", async () => {
  const fakeFetch = async () => ({ ok: false, status: 401 })
  await assert.rejects(
    fetchJiraIssue({
      site: "https://acme.atlassian.net",
      issueKey: "PROJ-123",
      email: "qa@example.com",
      token: TOKEN,
      fetchImpl: fakeFetch,
    }),
    (error) => {
      assert.match(error.message, /Jira authentication failed/)
      assert.ok(!error.message.includes(TOKEN))
      return true
    },
  )
})

test("sanitizeError removes the token from unexpected errors", () => {
  const message = sanitizeError(new Error(`upstream rejected ${TOKEN}`), [TOKEN])
  assert.ok(!message.includes(TOKEN))
  assert.match(message, /\[REDACTED\]/)
})

test("createHttpFormApi posts and gets the native form over HTTP", async () => {
  const calls = []
  const fakeFetch = async (url, init) => {
    calls.push({ url, init })
    const isCreate = init?.method === "POST"
    return {
      ok: true,
      status: 200,
      json: async () => ({
        data: isCreate
          ? { id: "frm_http", title: "Jira context", fields: [] }
          : { id: "frm_http", state: { status: "answered", answer: { jira_token: TOKEN } } },
      }),
    }
  }

  const api = createHttpFormApi("http://127.0.0.1:1234/", "Basic test", fakeFetch)
  const info = await api.create("ses_1", { title: "Jira context", metadata: {}, fields: [] })
  const detail = await api.get("ses_1", "frm_http")

  assert.equal(info.id, "frm_http")
  assert.equal(detail.state.status, "answered")
  assert.equal(calls[0].url, "http://127.0.0.1:1234/api/session/ses_1/form")
  assert.equal(calls[0].init.method, "POST")
  assert.equal(calls[0].init.headers.Authorization, "Basic test")
  assert.deepEqual(JSON.parse(calls[0].init.body), { title: "Jira context", metadata: {}, fields: [] })
  assert.equal(calls[1].url, "http://127.0.0.1:1234/api/session/ses_1/form/frm_http")
})

test("createHttpFormApi surfaces a token-free HTTP error", async () => {
  const fakeFetch = async () => ({ ok: false, status: 401 })
  const api = createHttpFormApi("http://127.0.0.1:1234", "Basic test", fakeFetch)
  await assert.rejects(
    () => api.create("ses_1", { title: "x", metadata: {}, fields: [] }),
    /native Form API request failed with HTTP 401/,
  )
})

test("getJiraFormApi falls back to the local HTTP Form API", () => {
  const previousUrl = process.env.ASSAY_OPENCODE_URL
  const previousPassword = process.env.ASSAY_OPENCODE_PASSWORD
  process.env.ASSAY_OPENCODE_URL = "http://127.0.0.1:9999/"
  process.env.ASSAY_OPENCODE_PASSWORD = "test-password"
  try {
    const config = readLocalServiceConfig()
    assert.equal(config.baseUrl, "http://127.0.0.1:9999")
    assert.equal(config.auth, `Basic ${Buffer.from("opencode:test-password").toString("base64")}`)
    const api = getJiraFormApi({})
    assert.equal(typeof api.create, "function")
    assert.equal(typeof api.get, "function")
  } finally {
    if (previousUrl === undefined) delete process.env.ASSAY_OPENCODE_URL
    else process.env.ASSAY_OPENCODE_URL = previousUrl
    if (previousPassword === undefined) delete process.env.ASSAY_OPENCODE_PASSWORD
    else process.env.ASSAY_OPENCODE_PASSWORD = previousPassword
  }
})

test("registerJiraContextTool registers the four-field tool and handles cancellation", async () => {
  const added = []
  let createdPayload
  const ctx = {
    session: {
      form: {
        create: async (input) => {
          createdPayload = input
          return { id: "frm_registered" }
        },
        get: async () => ({ id: "frm_registered", state: { status: "cancelled" } }),
      },
    },
    tool: {
      transform: async (editorFn) => {
        editorFn({ add: (tool) => added.push(tool) })
      },
    },
  }

  const registered = await registerJiraContextTool(ctx)
  assert.equal(registered, true)
  assert.equal(added.length, 1)
  assert.equal(added[0].name, "assay_jira_context")
  assert.deepEqual(Object.keys(added[0].input.properties), ["site", "issueKey", "email", "tokenRef"])
  assert.match(added[0].description, /four-field Jira form/i)

  const result = await added[0].execute({}, { sessionID: "ses_1" })
  assert.match(result.content, /Jira context cancelled/)
  assert.equal(createdPayload.fields.length, 4)
  assert.equal(createdPayload.fields[3].key, "jira_token")
})

test("registerJiraContextTool skips the token field when tokenRef is supplied", async () => {
  const added = []
  let createdPayload
  const ctx = {
    session: {
      form: {
        create: async (input) => {
          createdPayload = input
          return { id: "frm_token_ref" }
        },
        get: async () => ({ id: "frm_token_ref", state: { status: "cancelled" } }),
      },
    },
    tool: {
      transform: async (editorFn) => editorFn({ add: (tool) => added.push(tool) }),
    },
  }

  await registerJiraContextTool(ctx)
  const result = await added[0].execute({ tokenRef: "JIRA_API_TOKEN" }, { sessionID: "ses_2" })
  assert.match(result.content, /Jira context cancelled/)
  assert.deepEqual(
    createdPayload.fields.map((field) => field.key),
    ["jira_site", "jira_issue_key", "jira_email"],
  )
})

test("registerJiraContextTool returns false when the runtime has no tool editor", async () => {
  assert.equal(await registerJiraContextTool({}), false)
})
