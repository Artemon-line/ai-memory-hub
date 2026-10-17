(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AmhImportSourceSignatures = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SIGNATURE_VERSION = 1;

  const SAMPLE_KIND = Object.freeze({
    CSV: "csv",
    JSON: "json",
    JSONL: "jsonl",
  });

  const PARSER = Object.freeze({
    CLAUDE_CODE: "claude-code-session-jsonl",
    CODEX_ROLLOUT: "codex-rollout-jsonl",
    COPILOT_ACTIVITY: "copilot-activity-csv",
    COPILOT_CLI: "copilot-cli-events-jsonl",
    DEEPSEEK_HARNESS: "deepseek-harness-session-jsonl",
    DEEPSEEK_SHARE: "deepseek-share-json",
    DROID_EXEC: "droid-exec-json",
    GEMINI_CLI: "gemini-cli-session-json",
    HERMES: "hermes-session-jsonl",
    OPENCODE: "opencode-session-json",
    PI: "pi-session-jsonl",
    QWEN_CODE: "qwen-code-session-export",
  });

  const RECORD_TYPE = Object.freeze({
    ASSISTANT: "assistant",
    ASSISTANT_MESSAGE: "assistant.message",
    ASSISTANT_SLASH_MESSAGE: "assistant/message",
    MESSAGE: "message",
    RESPONSE_ITEM: "response_item",
    SESSION: "session",
    SESSION_META: "session_meta",
    SESSION_METADATA: "session_metadata",
    SESSION_RESUME: "session.resume",
    SESSION_START: "session.start",
    SYSTEM: "system",
    USER: "user",
    USER_MESSAGE: "user.message",
    USER_SLASH_MESSAGE: "user/message",
  });

  const MARKER = Object.freeze({
    COPILOT_ACTIVITY_HEADER: "Conversation,Time,Author,Message",
    DROID_CAPTURE_FORMAT: "ai-memory-hub.droid-exec-capture",
    DROID_ONE_SHOT: "one-shot",
    DROID_STREAM_JSONRPC: "stream-jsonrpc",
    QWEN_CHANNEL: "cli",
    CODEX_ORIGINATOR: "codex-tui",
    OPENCODE_SESSION_PREFIX: "ses_",
  });

  const FORMAT_VERSION = Object.freeze({
    DROID_CAPTURE: 1,
    PI_SESSION: 3,
  });

  function definition(id, parser, matches) {
    return Object.freeze({ id, parser, matches });
  }

  const SIGNATURES = Object.freeze({
    [SAMPLE_KIND.CSV]: Object.freeze([
      definition(
        "copilot-activity-csv-v1",
        PARSER.COPILOT_ACTIVITY,
        ({ firstLine }) => firstLine === MARKER.COPILOT_ACTIVITY_HEADER
      ),
    ]),
    [SAMPLE_KIND.JSON]: Object.freeze([
      definition("droid-capture-v1", PARSER.DROID_EXEC, matchesDroidCapture),
      definition("deepseek-share-v1", PARSER.DEEPSEEK_SHARE, matchesDeepSeekShare),
      definition("opencode-session-v1", PARSER.OPENCODE, matchesOpenCodeSession),
      definition("gemini-export-v1", PARSER.GEMINI_CLI, matchesGeminiExport),
      definition("qwen-export-v1", PARSER.QWEN_CODE, matchesQwenExport),
    ]),
    [SAMPLE_KIND.JSONL]: Object.freeze([
      definition("codex-rollout-v1", PARSER.CODEX_ROLLOUT, matchesCodexRollout),
      definition("copilot-cli-events-v1", PARSER.COPILOT_CLI, matchesCopilotCli),
      definition(
        "deepseek-harness-v1",
        PARSER.DEEPSEEK_HARNESS,
        matchesDeepSeekHarness
      ),
      definition("qwen-jsonl-export-v1", PARSER.QWEN_CODE, matchesQwenJsonl),
      definition("claude-code-session-v1", PARSER.CLAUDE_CODE, matchesClaudeCode),
      definition("pi-session-v3", PARSER.PI, matchesPiSession),
      definition("hermes-session-v1", PARSER.HERMES, matchesHermesSession),
    ]),
  });

  function findSignatureMatches(kind, sample) {
    return (SIGNATURES[kind] || [])
      .filter((signature) => signature.matches(sample))
      .map(({ id, parser }) => Object.freeze({ signature: id, parser }));
  }

  function matchesDroidCapture({ value }) {
    return (
      isObject(value) &&
      value.format === MARKER.DROID_CAPTURE_FORMAT &&
      value.version === FORMAT_VERSION.DROID_CAPTURE &&
      [MARKER.DROID_ONE_SHOT, MARKER.DROID_STREAM_JSONRPC].includes(value.kind)
    );
  }

  function matchesDeepSeekShare({ value }) {
    const payload = nestedObject(value, "data", "biz_data");
    return (
      isObject(value) &&
      value.code === 0 &&
      isObject(payload) &&
      typeof payload.model_type === "string" &&
      Array.isArray(payload.messages)
    );
  }

  function matchesOpenCodeSession({ value }) {
    return (
      isObject(value) &&
      isObject(value.info) &&
      typeof value.info.id === "string" &&
      value.info.id.startsWith(MARKER.OPENCODE_SESSION_PREFIX) &&
      Array.isArray(value.messages)
    );
  }

  function matchesGeminiExport({ value }) {
    return (
      isObject(value) &&
      typeof value.sessionId === "string" &&
      typeof value.projectHash === "string" &&
      Array.isArray(value.directories) &&
      Array.isArray(value.messages)
    );
  }

  function matchesQwenExport({ value }) {
    return (
      isObject(value) &&
      typeof value.sessionId === "string" &&
      Array.isArray(value.messages) &&
      isObject(value.metadata) &&
      typeof value.metadata.exportTime === "string" &&
      value.metadata.channel === MARKER.QWEN_CHANNEL &&
      value.messages.some(isTypedQwenMessage)
    );
  }

  function matchesCodexRollout({ first, types }) {
    return (
      first.type === RECORD_TYPE.SESSION_META &&
      isObject(first.payload) &&
      first.payload.originator === MARKER.CODEX_ORIGINATOR &&
      types.has(RECORD_TYPE.RESPONSE_ITEM)
    );
  }

  function matchesCopilotCli({ first, types }) {
    const sessionTypes = [RECORD_TYPE.SESSION_START, RECORD_TYPE.SESSION_RESUME];
    const messageTypes = [RECORD_TYPE.USER_MESSAGE, RECORD_TYPE.ASSISTANT_MESSAGE];
    return (
      sessionTypes.includes(first.type) &&
      isObject(first.data) &&
      typeof first.data.sessionId === "string" &&
      typeof first.data.copilotVersion === "string" &&
      messageTypes.some((type) => types.has(type))
    );
  }

  function matchesDeepSeekHarness({ first, types }) {
    const messageTypes = [
      RECORD_TYPE.USER_SLASH_MESSAGE,
      RECORD_TYPE.ASSISTANT_SLASH_MESSAGE,
    ];
    return (
      first.type === RECORD_TYPE.SESSION &&
      Number.isInteger(first.version) &&
      Number.isInteger(first.delegationDepth) &&
      messageTypes.some((type) => types.has(type))
    );
  }

  function matchesQwenJsonl({ first, types }) {
    return (
      first.type === RECORD_TYPE.SESSION_METADATA &&
      typeof first.sessionId === "string" &&
      typeof first.exportTime === "string" &&
      types.has(RECORD_TYPE.USER)
    );
  }

  function matchesClaudeCode({ records }) {
    return records.some(isClaudeSystemRecord) && records.some(isClaudeMessageRecord);
  }

  function matchesPiSession({ records }) {
    return records.some(isPiSessionRecord) && records.some(isPiMessageRecord);
  }

  function matchesHermesSession({ records }) {
    return records.every(
      (record) =>
        typeof record.id === "string" &&
        typeof record.source === "string" &&
        Array.isArray(record.messages)
    );
  }

  function isTypedQwenMessage(message) {
    return (
      typeof message.uuid === "string" &&
      typeof message.type === "string" &&
      isObject(message.message)
    );
  }

  function isClaudeSystemRecord(record) {
    return (
      record.type === RECORD_TYPE.SYSTEM &&
      typeof record.uuid === "string" &&
      typeof record.sessionId === "string"
    );
  }

  function isClaudeMessageRecord(record) {
    return (
      [RECORD_TYPE.USER, RECORD_TYPE.ASSISTANT].includes(record.type) &&
      typeof record.uuid === "string" &&
      isObject(record.message)
    );
  }

  function isPiSessionRecord(record) {
    return (
      record.type === RECORD_TYPE.SESSION &&
      record.version === FORMAT_VERSION.PI_SESSION &&
      typeof record.id === "string"
    );
  }

  function isPiMessageRecord(record) {
    return (
      record.type === RECORD_TYPE.MESSAGE &&
      typeof record.id === "string" &&
      Object.hasOwn(record, "parentId") &&
      isObject(record.message)
    );
  }

  function nestedObject(value, ...path) {
    return path.reduce(
      (current, key) => (isObject(current) ? current[key] : undefined),
      value
    );
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  return Object.freeze({
    PARSER,
    SAMPLE_KIND,
    SIGNATURES,
    SIGNATURE_VERSION,
    findSignatureMatches,
    isObject,
  });
});
