(function (root, factory) {
  const signatures =
    typeof module === "object" && module.exports
      ? require("./import_source_signatures.js")
      : root.AmhImportSourceSignatures;
  const api = factory(signatures);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AmhImportSourceDetector = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (signatures) {
  "use strict";

  if (!signatures) throw new Error("Import source signatures must be loaded first");

  const { SAMPLE_KIND, SIGNATURE_VERSION, findSignatureMatches, isObject } = signatures;

  const KIBIBYTE = 1024;
  const DEADLINE_CHECK_INTERVAL = KIBIBYTE;
  const DEFAULT_LIMITS = Object.freeze({
    maxBytes: 64 * KIBIBYTE,
    maxRecords: 32,
    maxDepth: 12,
    maxMilliseconds: 25,
  });

  const STATUS = Object.freeze({
    AMBIGUOUS: "ambiguous",
    SUGGESTED: "suggested",
    UNKNOWN: "unknown",
  });

  const REASON = Object.freeze({
    BYTE_LIMIT: "byte_limit",
    DEPTH_LIMIT: "depth_limit",
    EMPTY_INPUT: "empty_input",
    FILE_READ_FAILED: "file_read_failed",
    INVALID_JSONL_PREFIX: "invalid_jsonl_prefix",
    JSON_STRUCTURE: "json_structure",
    JSONL_RECORDS: "jsonl_records",
    MULTIPLE_SIGNATURES: "multiple_signatures",
    NO_SIGNATURE: "no_high_confidence_signature",
    REQUIRED_CSV_HEADER: "required_csv_header",
    TIME_LIMIT: "time_limit",
    UNSUPPORTED_FILE: "unsupported_file",
    UNSUPPORTED_INPUT: "unsupported_input",
  });

  class DetectionBudget {
    constructor(options = {}) {
      this.limits = clampLimits(options.limits);
      this.now = options.now || (() => performance.now());
      this.startedAt = this.now();
    }

    exceeded() {
      return this.now() - this.startedAt > this.limits.maxMilliseconds;
    }
  }

  class ImportSourceDetector {
    constructor({ findMatches = findSignatureMatches } = {}) {
      this.findMatches = findMatches;
    }

    detect(text, options = {}) {
      const budget = new DetectionBudget(options);
      const inputFailure = validateInput(text, budget.limits);
      if (inputFailure) return unknown(inputFailure);

      const normalized = stripByteOrderMark(text);
      const csvMatches = this.findMatches(SAMPLE_KIND.CSV, {
        firstLine: firstLineOf(normalized),
      });
      if (csvMatches.length) {
        return evaluateMatches(csvMatches, REASON.REQUIRED_CSV_HEADER);
      }

      const scanFailure = scanJsonDepth(normalized, budget);
      if (scanFailure) return unknown(scanFailure);

      const json = parseJson(normalized);
      if (json.ok) {
        if (budget.exceeded()) return unknown(REASON.TIME_LIMIT);
        return evaluateMatches(
          this.findMatches(SAMPLE_KIND.JSON, { value: json.value }),
          REASON.JSON_STRUCTURE
        );
      }

      const jsonl = parseJsonLines(normalized, budget);
      if (!jsonl.ok) return unknown(jsonl.reason);
      const context = {
        records: jsonl.value,
        first: jsonl.value[0],
        types: new Set(jsonl.value.map((record) => record.type)),
      };
      return evaluateMatches(
        this.findMatches(SAMPLE_KIND.JSONL, context),
        REASON.JSONL_RECORDS
      );
    }
  }

  class ImportFileInspector {
    constructor(detector = new ImportSourceDetector()) {
      this.detector = detector;
    }

    async inspect(file, options = {}) {
      const limits = clampLimits(options.limits);
      if (!file || typeof file.slice !== "function") {
        return unknown(REASON.UNSUPPORTED_FILE);
      }
      try {
        const prefix = file.slice(0, limits.maxBytes);
        return this.detector.detect(await prefix.text(), { ...options, limits });
      } catch (_error) {
        return unknown(REASON.FILE_READ_FAILED);
      }
    }
  }

  function clampLimits(requested = {}) {
    return Object.freeze(
      Object.fromEntries(
        Object.entries(DEFAULT_LIMITS).map(([name, maximum]) => [
          name,
          clampPositiveInteger(requested[name], maximum),
        ])
      )
    );
  }

  function clampPositiveInteger(value, maximum) {
    return Number.isFinite(value) && value > 0
      ? Math.min(Math.floor(value), maximum)
      : maximum;
  }

  function validateInput(text, limits) {
    if (typeof text !== "string") return REASON.UNSUPPORTED_INPUT;
    return new TextEncoder().encode(text).length > limits.maxBytes
      ? REASON.BYTE_LIMIT
      : null;
  }

  function scanJsonDepth(text, budget) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let index = 0; index < text.length; index += 1) {
      if (shouldCheckDeadline(index) && budget.exceeded()) return REASON.TIME_LIMIT;
      const character = text[index];
      if (inString) {
        ({ inString, escaped } = advanceStringState(character, escaped));
        continue;
      }
      if (character === '"') inString = true;
      if (character === "{" || character === "[") depth += 1;
      if (character === "}" || character === "]") depth = Math.max(0, depth - 1);
      if (depth > budget.limits.maxDepth) return REASON.DEPTH_LIMIT;
    }
    return null;
  }

  function shouldCheckDeadline(index) {
    return index % DEADLINE_CHECK_INTERVAL === 0;
  }

  function advanceStringState(character, escaped) {
    if (escaped) return { inString: true, escaped: false };
    if (character === "\\") return { inString: true, escaped: true };
    return { inString: character !== '"', escaped: false };
  }

  function parseJsonLines(text, budget) {
    const records = [];
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      if (budget.exceeded()) return failure(REASON.TIME_LIMIT);
      if (records.length >= budget.limits.maxRecords) break;
      const parsed = parseJson(line);
      if (!parsed.ok || !isObject(parsed.value)) {
        return failure(REASON.INVALID_JSONL_PREFIX);
      }
      records.push(parsed.value);
    }
    return records.length ? success(records) : failure(REASON.EMPTY_INPUT);
  }

  function parseJson(text) {
    try {
      return success(JSON.parse(text));
    } catch (_error) {
      return failure();
    }
  }

  function evaluateMatches(matches, matchedReason) {
    const matchesByParser = new Map();
    for (const match of matches) {
      if (!matchesByParser.has(match.parser)) {
        matchesByParser.set(match.parser, match.signature);
      }
    }
    const candidates = [...matchesByParser.keys()].sort();
    if (candidates.length === 1) {
      return outcome({
        status: STATUS.SUGGESTED,
        candidates,
        signature: matchesByParser.get(candidates[0]),
        reason: matchedReason,
      });
    }
    if (candidates.length > 1) {
      return outcome({
        status: STATUS.AMBIGUOUS,
        candidates,
        reason: REASON.MULTIPLE_SIGNATURES,
      });
    }
    return unknown(REASON.NO_SIGNATURE);
  }

  function unknown(reason) {
    return outcome({ status: STATUS.UNKNOWN, candidates: [], reason });
  }

  function outcome({ status, candidates, signature = null, reason }) {
    return Object.freeze({
      status,
      suggestedParser: status === STATUS.SUGGESTED ? candidates[0] : null,
      candidates: Object.freeze(candidates),
      signature,
      signatureVersion: SIGNATURE_VERSION,
      reason,
    });
  }

  function success(value) {
    return { ok: true, value };
  }

  function failure(reason) {
    return { ok: false, reason };
  }

  function stripByteOrderMark(text) {
    return text.replace(/^\uFEFF/, "");
  }

  function firstLineOf(text) {
    return text.split(/\r?\n/, 1)[0].trimEnd();
  }

  const defaultDetector = new ImportSourceDetector();
  const defaultInspector = new ImportFileInspector(defaultDetector);

  return Object.freeze({
    DEFAULT_LIMITS,
    DetectionBudget,
    ImportFileInspector,
    ImportSourceDetector,
    REASON,
    SIGNATURE_VERSION,
    STATUS,
    detectImportSource: defaultDetector.detect.bind(defaultDetector),
    inspectImportFile: defaultInspector.inspect.bind(defaultInspector),
  });
});
