const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  DEFAULT_LIMITS,
  SIGNATURE_VERSION,
  SourceSuggestionController,
  bindSourceSuggestion,
  detectImportSource,
  inspectImportFile,
} = require("../../memory/ui/connect/static/import_source_suggestions.js");
const {
  PARSER,
  SAMPLE_KIND,
  SIGNATURES,
} = require("../../memory/ui/connect/static/import_source_signatures.js");
const {
  ImportSourceDetector,
} = require("../../memory/ui/connect/static/import_source_detector.js");
const {
  CSRF_HEADER,
  ImportClient,
  MAX_FILE_BYTES,
  MAX_RECEIPTS,
  errorMessage,
  summarizeImport,
  validateSelection,
} = require("../../memory/ui/connect/static/import.js");

const fixtures = path.join(__dirname, "..", "fixtures", "importers");

const expectedFixtures = {
  "claude_code_session_anonymized.jsonl": "claude-code-session-jsonl",
  "codex_rollout_anonymized.jsonl": "codex-rollout-jsonl",
  "codex_rollout_rich_anonymized.jsonl": "codex-rollout-jsonl",
  "copilot_activity_anonymized.csv": "copilot-activity-csv",
  "copilot_cli_events_resumed_anonymized.jsonl": "copilot-cli-events-jsonl",
  "copilot_cli_events_v1_anonymized.jsonl": "copilot-cli-events-jsonl",
  "deepseek_harness_session_v0_descendant_anonymized.jsonl":
    "deepseek-harness-session-jsonl",
  "deepseek_harness_session_v4_anonymized.jsonl":
    "deepseek-harness-session-jsonl",
  "deepseek_share_anonymized.json": "deepseek-share-json",
  "droid_exec_one_shot_anonymized.json": "droid-exec-json",
  "droid_exec_stream_anonymized.json": "droid-exec-json",
  "gemini_cli_export_session_anonymized.json": "gemini-cli-session-json",
  "hermes_sessions_anonymized.jsonl": "hermes-session-jsonl",
  "oh_my_pi_session_anonymized.jsonl": "pi-session-jsonl",
  "openclaw_session_anonymized.jsonl": "pi-session-jsonl",
  "opencode_session_anonymized.json": "opencode-session-json",
  "pi_session_anonymized.jsonl": "pi-session-jsonl",
  "qwen_code_session_anonymized.json": "qwen-code-session-export",
  "qwen_code_session_anonymized.jsonl": "qwen-code-session-export",
};

test("signature catalog is declarative and has unique IDs", () => {
  const definitions = Object.values(SIGNATURES).flat();
  const ids = definitions.map(({ id }) => id);

  assert.equal(new Set(ids).size, ids.length);
  assert.ok(
    definitions.every(({ parser, matches }) => parser && typeof matches === "function")
  );
  assert.ok(
    Object.values(PARSER).every((parser) =>
      definitions.some((item) => item.parser === parser)
    )
  );
});

test("detector accepts an injected signature matcher", () => {
  const calls = [];
  const detector = new ImportSourceDetector({
    findMatches(kind) {
      calls.push(kind);
      return kind === SAMPLE_KIND.JSON
        ? [{ parser: "custom-parser", signature: "custom-v1" }]
        : [];
    },
  });

  const result = detector.detect("{}");

  assert.equal(result.suggestedParser, "custom-parser");
  assert.deepEqual(calls, [SAMPLE_KIND.CSV, SAMPLE_KIND.JSON]);
});

for (const [fixture, parser] of Object.entries(expectedFixtures)) {
  test(`verified fixture suggests ${parser}: ${fixture}`, () => {
    const text = fs.readFileSync(path.join(fixtures, fixture), "utf8");
    const result = detectImportSource(text);

    assert.equal(result.status, "suggested");
    assert.equal(result.suggestedParser, parser);
    assert.equal(result.candidates.length, 1);
    assert.equal(result.signatureVersion, SIGNATURE_VERSION);
    assert.equal(typeof result.signature, "string");
  });
}

test("generic JSON and a filename-like extension do not produce a suggestion", () => {
  const result = detectImportSource(
    JSON.stringify({ filename: "session.json", messages: [{ role: "user" }] })
  );

  assert.equal(result.status, "unknown");
  assert.equal(result.suggestedParser, null);
  assert.deepEqual(result.candidates, []);
});

test("an overlapping JSON object returns candidates without selecting one", () => {
  const result = detectImportSource(
    JSON.stringify({
      format: "ai-memory-hub.droid-exec-capture",
      version: 1,
      kind: "one-shot",
      info: { id: "ses_overlap" },
      messages: [],
    })
  );

  assert.equal(result.status, "ambiguous");
  assert.equal(result.suggestedParser, null);
  assert.deepEqual(result.candidates, ["droid-exec-json", "opencode-session-json"]);
});

test("overlapping JSONL records return candidates without selecting one", () => {
  const result = detectImportSource(
    [
      { type: "session", version: 3, id: "overlap", delegationDepth: 0 },
      { type: "user/message", data: { content: "private" } },
      { type: "message", id: "m1", parentId: null, message: { role: "user" } },
    ]
      .map(JSON.stringify)
      .join("\n")
  );

  assert.equal(result.status, "ambiguous");
  assert.equal(result.suggestedParser, null);
  assert.deepEqual(result.candidates, [
    "deepseek-harness-session-jsonl",
    "pi-session-jsonl",
  ]);
});

test("detection fails closed at byte, record, depth, and time limits", () => {
  assert.equal(
    detectImportSource("x".repeat(DEFAULT_LIMITS.maxBytes + 1)).reason,
    "byte_limit"
  );
  assert.equal(
    detectImportSource("x".repeat(DEFAULT_LIMITS.maxBytes + 1), {
      limits: { maxBytes: Number.MAX_SAFE_INTEGER },
    }).reason,
    "byte_limit"
  );

  const delayedSignature = Array.from({ length: DEFAULT_LIMITS.maxRecords }, (_, index) => ({
    index,
  }));
  delayedSignature.push({
    type: "session_meta",
    payload: { originator: "codex-tui" },
  });
  delayedSignature.push({ type: "response_item" });
  assert.equal(
    detectImportSource(delayedSignature.map(JSON.stringify).join("\n")).status,
    "unknown"
  );

  assert.equal(
    detectImportSource("[".repeat(DEFAULT_LIMITS.maxDepth + 1)).reason,
    "depth_limit"
  );

  let clock = 0;
  assert.equal(
    detectImportSource("{}", { now: () => (clock += 100) }).reason,
    "time_limit"
  );
});

test("file inspection reads only the configured prefix", async () => {
  const calls = [];
  const file = {
    slice(start, end) {
      calls.push([start, end]);
      return { text: async () => "Conversation,Time,Author,Message\n" };
    },
  };

  const result = await inspectImportFile(file);

  assert.deepEqual(calls, [[0, DEFAULT_LIMITS.maxBytes]]);
  assert.equal(result.suggestedParser, "copilot-activity-csv");
});

test("file read failures fail closed without exposing an error", async () => {
  const result = await inspectImportFile({
    slice() {
      return {
        text: async () => {
          throw new Error("private operating system path");
        },
      };
    },
  });

  assert.equal(result.status, "unknown");
  assert.equal(result.reason, "file_read_failed");
  assert.equal(JSON.stringify(result).includes("private operating system path"), false);
});

test("bound controls let the user change or clear a suggestion", async () => {
  const fileInput = new FakeControl();
  const sourceSelect = new FakeControl();
  const statusElement = { textContent: "" };
  const privateMarker = "must-not-be-rendered";
  fileInput.files = [fakeFile(`Conversation,Time,Author,Message\nrow,now,Human,${privateMarker}`)];
  bindSourceSuggestion({ fileInput, sourceSelect, statusElement });

  await fileInput.dispatch("change");
  assert.equal(sourceSelect.value, "copilot-activity-csv");
  assert.equal(sourceSelect.dataset.suggested, "true");
  assert.equal(statusElement.textContent.includes(privateMarker), false);

  sourceSelect.value = "manual";
  await sourceSelect.dispatch("change");
  assert.equal(sourceSelect.dataset.suggested, undefined);

  await fileInput.dispatch("change");
  assert.equal(sourceSelect.value, "manual");
  assert.match(statusElement.textContent, /choice was kept/);

  sourceSelect.value = "";
  await sourceSelect.dispatch("change");
  fileInput.files = [];
  await fileInput.dispatch("change");
  assert.equal(sourceSelect.value, "");
});

test("controller accepts an injected file inspector", async () => {
  const fileInput = new FakeControl();
  const sourceSelect = new FakeControl();
  const statusElement = { textContent: "" };
  const inspected = [];
  const file = fakeFile("ignored");
  fileInput.files = [file];
  new SourceSuggestionController({
    fileInput,
    sourceSelect,
    statusElement,
    async inspectFile(received) {
      inspected.push(received);
      return {
        status: "suggested",
        suggestedParser: "injected-parser",
      };
    },
  }).connect();

  await fileInput.dispatch("change");

  assert.deepEqual(inspected, [file]);
  assert.equal(sourceSelect.value, "injected-parser");
});

test("import client uses the existing multipart contract with bearer auth", async () => {
  const fields = [];
  const requests = [];
  const client = new ImportClient({
    formDataFactory: () => ({ append: (name, value) => fields.push([name, value]) }),
    async fetchImpl(url, options) {
      requests.push({ url, options });
      return { ok: true, json: async () => ({ status: "ok", results: [] }) };
    },
  });
  const file = { name: "history.jsonl", size: 42 };

  await client.upload({
    parser: "codex-rollout-jsonl",
    file,
    token: "private-bearer-token",
    csrfToken: "ignored-csrf",
  });

  assert.deepEqual(fields, [
    ["parser", "codex-rollout-jsonl"],
    ["file", file],
  ]);
  assert.equal(requests[0].url, "/memory/import");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.headers.Authorization, "Bearer private-bearer-token");
  assert.equal(requests[0].options.headers[CSRF_HEADER], undefined);
  assert.equal(requests[0].options.credentials, "same-origin");
});

test("import client uses Connect session CSRF without an authorization header", async () => {
  let request;
  const client = new ImportClient({
    formDataFactory: () => ({ append() {} }),
    async fetchImpl(_url, options) {
      request = options;
      return { ok: true };
    },
  });

  await client.upload({
    parser: "manual",
    file: { name: "chat.txt", size: 8 },
    token: "",
    csrfToken: "browser-csrf",
  });

  assert.deepEqual(request.headers, { [CSRF_HEADER]: "browser-csrf" });
});

test("file validation rejects missing sources, unsupported files, and oversized uploads", () => {
  assert.match(
    validateSelection({ parser: "", file: { name: "chat.txt", size: 10 } }),
    /Choose the conversation source/
  );
  assert.match(
    validateSelection({ parser: "manual", file: { name: "chat.zip", size: 10 } }),
    /JSON, JSONL, NDJSON, CSV, or text/
  );
  assert.match(
    validateSelection({
      parser: "manual",
      file: { name: "chat.txt", size: MAX_FILE_BYTES + 1 },
    }),
    /20 MB/
  );
});

test("receipt summaries are bounded and never copy conversation or error content", () => {
  const privateText = "private conversation text must not render";
  const results = Array.from({ length: MAX_RECEIPTS + 5 }, (_, index) => ({
    index,
    id: `memory-${index}`,
    status: index === 2 ? "error" : "inserted",
    deduplicated: index === 1,
    text: privateText,
    error_message: privateText,
  }));

  const summary = summarizeImport({ status: "partial", results });

  assert.equal(summary.results.length, MAX_RECEIPTS);
  assert.equal(summary.imported, MAX_RECEIPTS - 1);
  assert.equal(summary.deduplicated, 1);
  assert.equal(summary.failed, 1);
  assert.equal(summary.partial, true);
  assert.equal(JSON.stringify(summary).includes(privateText), false);
});

test("HTTP errors map to actionable messages without rendering server details", () => {
  assert.match(errorMessage(401), /valid bearer token/);
  assert.match(errorMessage(403), /memory:write/);
  assert.match(errorMessage(413), /20 MB/);
  assert.match(errorMessage(415), /file type/);
  assert.match(errorMessage(400), /selected source/);
});

class FakeControl {
  constructor() {
    this.dataset = {};
    this.files = [];
    this.listeners = new Map();
    this.value = "";
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  async dispatch(name) {
    await this.listeners.get(name)();
  }
}

function fakeFile(text) {
  return {
    slice(_start, end) {
      return { text: async () => text.slice(0, end) };
    },
  };
}
