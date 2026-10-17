(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AmhImportSourceSuggestions = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_LIMITS = Object.freeze({
    maxBytes: 64 * 1024,
    maxRecords: 32,
    maxDepth: 12,
    maxMilliseconds: 25,
  });

  const SIGNATURE_VERSION = 1;
  const CSV_HEADER = "Conversation,Time,Author,Message";

  function boundedLimits(options) {
    const requested = (options && options.limits) || {};
    return Object.fromEntries(
      Object.entries(DEFAULT_LIMITS).map(([name, maximum]) => {
        const value = requested[name];
        return [
          name,
          Number.isFinite(value) && value > 0
            ? Math.min(Math.floor(value), maximum)
            : maximum,
        ];
      })
    );
  }

  function outcome(status, candidates, signature, reason) {
    return Object.freeze({
      status,
      suggestedParser: status === "suggested" ? candidates[0] : null,
      candidates: Object.freeze(candidates),
      signature: signature || null,
      signatureVersion: SIGNATURE_VERSION,
      reason,
    });
  }

  function detectImportSource(text, options) {
    const limits = boundedLimits(options);
    const now = (options && options.now) || (() => performance.now());
    const startedAt = now();
    const deadlineExceeded = () => now() - startedAt > limits.maxMilliseconds;

    if (typeof text !== "string") {
      return outcome("unknown", [], null, "unsupported_input");
    }
    if (new TextEncoder().encode(text).length > limits.maxBytes) {
      return outcome("unknown", [], null, "byte_limit");
    }

    const normalized = text.replace(/^\uFEFF/, "");
    const firstLine = normalized.split(/\r?\n/, 1)[0].trimEnd();
    if (firstLine === CSV_HEADER) {
      return outcome(
        "suggested",
        ["copilot-activity-csv"],
        "copilot-activity-csv-v1",
        "required_csv_header"
      );
    }

    const depth = boundedJsonDepth(normalized, limits.maxDepth, deadlineExceeded);
    if (depth === "time_limit") return outcome("unknown", [], null, "time_limit");
    if (depth === "depth_limit") return outcome("unknown", [], null, "depth_limit");

    const json = parseJson(normalized);
    if (json.ok) {
      if (deadlineExceeded()) return outcome("unknown", [], null, "time_limit");
      return evaluateMatches(jsonSignatures(json.value), "json_structure");
    }

    const records = parseJsonLines(
      normalized,
      limits.maxRecords,
      deadlineExceeded
    );
    if (!records.ok) return outcome("unknown", [], null, records.reason);
    return evaluateMatches(jsonlSignatures(records.value), "jsonl_records");
  }

  async function inspectImportFile(file, options) {
    const limits = boundedLimits(options);
    if (!file || typeof file.slice !== "function") {
      return outcome("unknown", [], null, "unsupported_file");
    }
    try {
      const prefix = file.slice(0, limits.maxBytes);
      return detectImportSource(await prefix.text(), { ...options, limits });
    } catch (_error) {
      return outcome("unknown", [], null, "file_read_failed");
    }
  }

  function evaluateMatches(matches, reason) {
    const byParser = new Map();
    for (const match of matches) {
      if (!byParser.has(match.parser)) byParser.set(match.parser, match.signature);
    }
    const candidates = [...byParser.keys()].sort();
    if (candidates.length === 1) {
      return outcome("suggested", candidates, byParser.get(candidates[0]), reason);
    }
    if (candidates.length > 1) {
      return outcome("ambiguous", candidates, null, "multiple_signatures");
    }
    return outcome("unknown", [], null, "no_high_confidence_signature");
  }

  function parseJson(text) {
    try {
      return { ok: true, value: JSON.parse(text) };
    } catch (_error) {
      return { ok: false };
    }
  }

  function parseJsonLines(text, maxRecords, deadlineExceeded) {
    const records = [];
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      if (deadlineExceeded()) return { ok: false, reason: "time_limit" };
      if (records.length >= maxRecords) break;
      const parsed = parseJson(line);
      if (!parsed.ok || !isObject(parsed.value)) {
        return { ok: false, reason: "invalid_jsonl_prefix" };
      }
      records.push(parsed.value);
    }
    return records.length
      ? { ok: true, value: records }
      : { ok: false, reason: "empty_input" };
  }

  function boundedJsonDepth(text, maxDepth, deadlineExceeded) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let index = 0; index < text.length; index += 1) {
      if ((index & 1023) === 0 && deadlineExceeded()) return "time_limit";
      const character = text[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (character === "\\") escaped = true;
        else if (character === '"') inString = false;
        continue;
      }
      if (character === '"') inString = true;
      else if (character === "{" || character === "[") {
        depth += 1;
        if (depth > maxDepth) return "depth_limit";
      } else if (character === "}" || character === "]") {
        depth = Math.max(0, depth - 1);
      }
    }
    return "ok";
  }

  function jsonSignatures(value) {
    const matches = [];
    if (!isObject(value)) return matches;

    if (
      value.format === "ai-memory-hub.droid-exec-capture" &&
      value.version === 1 &&
      ["one-shot", "stream-jsonrpc"].includes(value.kind)
    ) {
      matches.push({ parser: "droid-exec-json", signature: "droid-capture-v1" });
    }
    if (
      value.code === 0 &&
      isObject(value.data) &&
      isObject(value.data.biz_data) &&
      typeof value.data.biz_data.model_type === "string" &&
      Array.isArray(value.data.biz_data.messages)
    ) {
      matches.push({ parser: "deepseek-share-json", signature: "deepseek-share-v1" });
    }
    if (
      isObject(value.info) &&
      typeof value.info.id === "string" &&
      value.info.id.startsWith("ses_") &&
      Array.isArray(value.messages)
    ) {
      matches.push({ parser: "opencode-session-json", signature: "opencode-session-v1" });
    }
    if (
      typeof value.sessionId === "string" &&
      typeof value.projectHash === "string" &&
      Array.isArray(value.directories) &&
      Array.isArray(value.messages)
    ) {
      matches.push({ parser: "gemini-cli-session-json", signature: "gemini-export-v1" });
    }
    if (
      typeof value.sessionId === "string" &&
      Array.isArray(value.messages) &&
      isObject(value.metadata) &&
      typeof value.metadata.exportTime === "string" &&
      value.metadata.channel === "cli" &&
      value.messages.some(
        (message) =>
          typeof message.uuid === "string" &&
          typeof message.type === "string" &&
          isObject(message.message)
      )
    ) {
      matches.push({ parser: "qwen-code-session-export", signature: "qwen-export-v1" });
    }
    return matches;
  }

  function jsonlSignatures(records) {
    const matches = [];
    const first = records[0];
    const types = new Set(records.map((record) => record.type));
    const piSession = records.find(
      (record) =>
        record.type === "session" &&
        record.version === 3 &&
        typeof record.id === "string"
    );

    if (
      first.type === "session_meta" &&
      isObject(first.payload) &&
      first.payload.originator === "codex-tui" &&
      types.has("response_item")
    ) {
      matches.push({ parser: "codex-rollout-jsonl", signature: "codex-rollout-v1" });
    }
    if (
      ["session.start", "session.resume"].includes(first.type) &&
      isObject(first.data) &&
      typeof first.data.sessionId === "string" &&
      typeof first.data.copilotVersion === "string" &&
      (types.has("user.message") || types.has("assistant.message"))
    ) {
      matches.push({
        parser: "copilot-cli-events-jsonl",
        signature: "copilot-cli-events-v1",
      });
    }
    if (
      first.type === "session" &&
      Number.isInteger(first.version) &&
      Number.isInteger(first.delegationDepth) &&
      (types.has("user/message") || types.has("assistant/message"))
    ) {
      matches.push({
        parser: "deepseek-harness-session-jsonl",
        signature: "deepseek-harness-v1",
      });
    }
    if (
      first.type === "session_metadata" &&
      typeof first.sessionId === "string" &&
      typeof first.exportTime === "string" &&
      types.has("user")
    ) {
      matches.push({
        parser: "qwen-code-session-export",
        signature: "qwen-jsonl-export-v1",
      });
    }
    if (
      records.some(
        (record) =>
          record.type === "system" &&
          typeof record.uuid === "string" &&
          typeof record.sessionId === "string"
      ) &&
      records.some(
        (record) =>
          ["user", "assistant"].includes(record.type) &&
          typeof record.uuid === "string" &&
          isObject(record.message)
      )
    ) {
      matches.push({
        parser: "claude-code-session-jsonl",
        signature: "claude-code-session-v1",
      });
    }
    if (
      piSession &&
      records.some(
        (record) =>
          record.type === "message" &&
          typeof record.id === "string" &&
          Object.hasOwn(record, "parentId") &&
          isObject(record.message)
      )
    ) {
      matches.push({ parser: "pi-session-jsonl", signature: "pi-session-v3" });
    }
    if (
      records.every(
        (record) =>
          typeof record.id === "string" &&
          typeof record.source === "string" &&
          Array.isArray(record.messages)
      )
    ) {
      matches.push({ parser: "hermes-session-jsonl", signature: "hermes-session-v1" });
    }
    return matches;
  }

  function bindSourceSuggestion({ fileInput, sourceSelect, statusElement, options }) {
    if (!fileInput || !sourceSelect || !statusElement) {
      throw new TypeError("fileInput, sourceSelect, and statusElement are required");
    }

    let automaticValue = null;
    const clearAutomaticState = () => {
      automaticValue = null;
      delete sourceSelect.dataset.suggested;
    };
    sourceSelect.addEventListener("change", clearAutomaticState);

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) {
        if (sourceSelect.value === automaticValue) sourceSelect.value = "";
        clearAutomaticState();
        statusElement.textContent = "Choose a source to continue.";
        return;
      }

      const result = await inspectImportFile(file, options);
      if (sourceSelect.value === automaticValue) sourceSelect.value = "";
      clearAutomaticState();

      if (result.status === "suggested" && sourceSelect.value === "") {
        sourceSelect.value = result.suggestedParser;
        sourceSelect.dataset.suggested = "true";
        automaticValue = result.suggestedParser;
        statusElement.textContent = "Suggested from a verified file signature. You can change or clear it.";
      } else if (result.status === "suggested") {
        statusElement.textContent = "A verified signature matched, but your source choice was kept.";
      } else if (result.status === "ambiguous") {
        statusElement.textContent = "Several sources may match. Choose the source explicitly.";
      } else {
        statusElement.textContent = "No verified signature matched. Choose the source explicitly.";
      }
    });

    return { clearAutomaticState };
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  return Object.freeze({
    DEFAULT_LIMITS,
    SIGNATURE_VERSION,
    bindSourceSuggestion,
    detectImportSource,
    inspectImportFile,
  });
});
