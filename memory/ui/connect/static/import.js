(function (root, factory) {
  const suggestions =
    typeof module === "object" && module.exports
      ? require("./import_source_suggestions.js")
      : root.AmhImportSourceSuggestions;
  const api = factory(suggestions, root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AmhConversationImport = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (suggestions, platform) {
  "use strict";

  const MAX_FILE_BYTES = 20_000_000;
  const MAX_RECEIPTS = 100;
  const SUPPORTED_EXTENSIONS = Object.freeze([".json", ".jsonl", ".ndjson", ".csv", ".txt"]);
  const CSRF_HEADER = "X-Ai-Memory-Hub-CSRF";

  function validateSelection({ parser, file }) {
    if (!parser) return "Choose the conversation source before uploading.";
    if (!file) return "Select one conversation export to upload.";
    const name = String(file.name || "").toLowerCase();
    if (!SUPPORTED_EXTENSIONS.some((extension) => name.endsWith(extension))) {
      return "Choose a JSON, JSONL, NDJSON, CSV, or text file.";
    }
    if (Number(file.size) > MAX_FILE_BYTES) {
      return "The selected file is larger than the 20 MB limit.";
    }
    if (Number(file.size) === 0) return "The selected file is empty.";
    return null;
  }

  function summarizeImport(payload) {
    const results = Array.isArray(payload && payload.results)
      ? payload.results.slice(0, MAX_RECEIPTS)
      : [];
    const safeResults = results.map((result, index) => ({
      index: Number.isInteger(result && result.index) ? result.index : index,
      id: typeof (result && result.id) === "string" ? result.id : null,
      status: typeof (result && result.status) === "string" ? result.status : "unknown",
      deduplicated: result && result.deduplicated === true,
    }));
    const failed = safeResults.filter((result) => result.status === "error").length;
    return {
      imported: safeResults.length - failed,
      deduplicated: safeResults.filter((result) => result.deduplicated).length,
      failed,
      results: safeResults,
      partial: payload && payload.status === "partial",
    };
  }

  function errorMessage(status) {
    if (status === 401) return "Sign in again or provide a valid bearer token.";
    if (status === 403) return "This credential does not have the required memory:write scope.";
    if (status === 413) return "The upload is larger than the 20 MB file limit.";
    if (status === 415) return "The hub does not accept this file type. Use JSON, JSONL, CSV, or text.";
    if (status === 400) {
      return "The selected source could not parse this file. Check the source guidance and export again.";
    }
    return "The import could not be completed. Check that the hub is available and try again.";
  }

  class ImportClient {
    constructor({
      fetchImpl = platform.fetch.bind(platform),
      formDataFactory = () => new platform.FormData(),
    } = {}) {
      this.fetchImpl = fetchImpl;
      this.formDataFactory = formDataFactory;
    }

    async upload({ parser, file, token, csrfToken }) {
      const body = this.formDataFactory();
      body.append("parser", parser);
      body.append("file", file);
      const headers = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      else if (csrfToken) headers[CSRF_HEADER] = csrfToken;
      return this.fetchImpl("/memory/import", {
        method: "POST",
        body,
        headers,
        credentials: "same-origin",
      });
    }
  }

  class ImportPageController {
    constructor(elements, { client = new ImportClient() } = {}) {
      this.elements = elements;
      this.client = client;
    }

    connect() {
      const { form, fileInput, sourceSelect, dropZone } = this.elements;
      form.addEventListener("submit", (event) => this.submit(event));
      fileInput.addEventListener("change", () => this.showFile());
      sourceSelect.addEventListener("change", () => this.showGuidance());
      dropZone.addEventListener("dragover", (event) => {
        event.preventDefault();
        dropZone.classList.add("is-dragging");
      });
      dropZone.addEventListener("dragleave", () => dropZone.classList.remove("is-dragging"));
      dropZone.addEventListener("drop", (event) => this.drop(event));
      return this;
    }

    drop(event) {
      event.preventDefault();
      this.elements.dropZone.classList.remove("is-dragging");
      if (event.dataTransfer && event.dataTransfer.files.length === 1) {
        this.elements.fileInput.files = event.dataTransfer.files;
        this.elements.fileInput.dispatchEvent(new Event("change"));
      } else {
        this.showError("Drop exactly one file.");
      }
    }

    showFile() {
      const file = this.elements.fileInput.files && this.elements.fileInput.files[0];
      this.elements.fileSummary.textContent = file
        ? `${file.name} · ${formatBytes(file.size)}`
        : "JSON, JSONL, NDJSON, CSV, or text · up to 20 MB";
    }

    showGuidance() {
      const option = this.elements.sourceSelect.selectedOptions[0];
      this.elements.sourceGuidance.textContent =
        (option && option.dataset.guidance) ||
        "Choose the app or export format you obtained the file from.";
    }

    async submit(event) {
      event.preventDefault();
      this.hideMessages();
      const file = this.elements.fileInput.files && this.elements.fileInput.files[0];
      const parser = this.elements.sourceSelect.value;
      const validationError = validateSelection({ parser, file });
      if (validationError) return this.showError(validationError);

      this.elements.submit.disabled = true;
      this.elements.submit.textContent = "Importing…";
      try {
        const token = this.elements.tokenInput ? this.elements.tokenInput.value.trim() : "";
        const response = await this.client.upload({
          parser,
          file,
          token,
          csrfToken: this.elements.csrfInput.value,
        });
        if (this.elements.tokenInput) this.elements.tokenInput.value = "";
        if (!response.ok) return this.showError(errorMessage(response.status));
        this.showResult(summarizeImport(await response.json()));
      } catch (_error) {
        this.showError(errorMessage(0));
      } finally {
        this.elements.submit.disabled = false;
        this.elements.submit.textContent = "Import conversations";
      }
    }

    hideMessages() {
      this.elements.error.hidden = true;
      this.elements.result.hidden = true;
    }

    showError(message) {
      this.elements.error.textContent = message;
      this.elements.error.hidden = false;
    }

    showResult(summary) {
      const { counts, receipts, result, resultStatus } = this.elements;
      counts.textContent = `Imported ${summary.imported} · Deduplicated ${summary.deduplicated} · Failed ${summary.failed}`;
      resultStatus.textContent = summary.partial
        ? "Some conversations could not be imported."
        : "The hub finished processing the file.";
      receipts.replaceChildren();
      summary.results.forEach((receipt) => {
        const item = document.createElement("li");
        const deduplicated = receipt.deduplicated ? " · deduplicated" : "";
        const identifier = receipt.id ? ` · memory ${receipt.id}` : "";
        item.textContent = `Conversation ${receipt.index + 1}: ${receipt.status}${deduplicated}${identifier}`;
        receipts.appendChild(item);
      });
      result.hidden = false;
      result.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function formatBytes(size) {
    if (size < 1000) return `${size} B`;
    return `${(size / 1_000_000).toFixed(1)} MB`;
  }

  function pageElements(documentRef) {
    const byId = (id) => documentRef.getElementById(id);
    return {
      form: byId("import-form"),
      fileInput: byId("import-file"),
      fileSummary: byId("file-summary"),
      sourceSelect: byId("import-source"),
      sourceGuidance: byId("source-guidance"),
      dropZone: byId("import-drop-zone"),
      tokenInput: byId("import-token"),
      csrfInput: byId("import-csrf"),
      error: byId("import-error"),
      submit: byId("import-submit"),
      result: byId("import-result"),
      resultStatus: byId("import-result-status"),
      counts: byId("import-counts"),
      receipts: byId("import-receipts"),
    };
  }

  if (typeof document !== "undefined") {
    const elements = pageElements(document);
    if (elements.form) {
      suggestions.bindSourceSuggestion({
        fileInput: elements.fileInput,
        sourceSelect: elements.sourceSelect,
        statusElement: document.getElementById("source-suggestion"),
      });
      new ImportPageController(elements).connect();
    }
  }

  return Object.freeze({
    CSRF_HEADER,
    ImportClient,
    ImportPageController,
    MAX_FILE_BYTES,
    MAX_RECEIPTS,
    errorMessage,
    summarizeImport,
    validateSelection,
  });
});
