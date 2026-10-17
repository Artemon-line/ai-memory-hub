const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  DEFAULT_LIMITS,
  SIGNATURE_VERSION,
  bindSourceSuggestion,
  detectImportSource,
  inspectImportFile,
} = require("../../memory/ui/connect/static/import_source_suggestions.js");

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
