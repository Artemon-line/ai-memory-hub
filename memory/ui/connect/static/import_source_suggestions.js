(function (root, factory) {
  const detector =
    typeof module === "object" && module.exports
      ? require("./import_source_detector.js")
      : root.AmhImportSourceDetector;
  const api = factory(detector);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AmhImportSourceSuggestions = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (detector) {
  "use strict";

  if (!detector) throw new Error("Import source detector must be loaded first");

  const {
    DEFAULT_LIMITS,
    REASON,
    SIGNATURE_VERSION,
    STATUS,
    detectImportSource,
    inspectImportFile,
  } = detector;

  const COPY = Object.freeze({
    AMBIGUOUS: "Several sources may match. Choose the source explicitly.",
    EMPTY: "Choose a source to continue.",
    MANUAL_KEPT: "A verified signature matched, but your source choice was kept.",
    SUGGESTED:
      "Suggested from a verified file signature. You can change or clear it.",
    UNKNOWN: "No verified signature matched. Choose the source explicitly.",
  });

  const DATA_ATTRIBUTE = Object.freeze({
    SUGGESTED: "suggested",
  });

  const DATA_VALUE = Object.freeze({
    TRUE: "true",
  });

  const EMPTY_SELECTION = "";

  const EVENT = Object.freeze({
    CHANGE: "change",
  });

  class SourceSuggestionController {
    constructor({
      fileInput,
      sourceSelect,
      statusElement,
      options = {},
      inspectFile = inspectImportFile,
    }) {
      assertControls(fileInput, sourceSelect, statusElement);
      this.fileInput = fileInput;
      this.sourceSelect = sourceSelect;
      this.statusElement = statusElement;
      this.options = options;
      this.inspectFile = inspectFile;
      this.automaticValue = null;
    }

    connect() {
      this.sourceSelect.addEventListener(EVENT.CHANGE, () => this.clearAutomaticState());
      this.fileInput.addEventListener(EVENT.CHANGE, () => this.handleFileChange());
      return this;
    }

    clearAutomaticState() {
      this.automaticValue = null;
      delete this.sourceSelect.dataset[DATA_ATTRIBUTE.SUGGESTED];
    }

    async handleFileChange() {
      const file = this.fileInput.files && this.fileInput.files[0];
      if (!file) {
        this.clearRemovedFileSuggestion();
        return;
      }
      const result = await this.inspectFile(file, this.options);
      this.removePreviousSuggestion();
      this.render(result);
    }

    clearRemovedFileSuggestion() {
      if (this.sourceSelect.value === this.automaticValue) {
        this.sourceSelect.value = EMPTY_SELECTION;
      }
      this.clearAutomaticState();
      this.setStatus(COPY.EMPTY);
    }

    removePreviousSuggestion() {
      if (this.sourceSelect.value === this.automaticValue) {
        this.sourceSelect.value = EMPTY_SELECTION;
      }
      this.clearAutomaticState();
    }

    render(result) {
      const renderer = RENDERERS[result.status] || renderUnknown;
      renderer(this, result);
    }

    applySuggestion(parser) {
      this.sourceSelect.value = parser;
      this.sourceSelect.dataset[DATA_ATTRIBUTE.SUGGESTED] = DATA_VALUE.TRUE;
      this.automaticValue = parser;
    }

    setStatus(message) {
      this.statusElement.textContent = message;
    }
  }

  const RENDERERS = Object.freeze({
    [STATUS.AMBIGUOUS]: (controller) => controller.setStatus(COPY.AMBIGUOUS),
    [STATUS.SUGGESTED]: renderSuggested,
    [STATUS.UNKNOWN]: renderUnknown,
  });

  function renderSuggested(controller, result) {
    if (controller.sourceSelect.value) {
      controller.setStatus(COPY.MANUAL_KEPT);
      return;
    }
    controller.applySuggestion(result.suggestedParser);
    controller.setStatus(COPY.SUGGESTED);
  }

  function renderUnknown(controller) {
    controller.setStatus(COPY.UNKNOWN);
  }

  function assertControls(fileInput, sourceSelect, statusElement) {
    if (!fileInput || !sourceSelect || !statusElement) {
      throw new TypeError("fileInput, sourceSelect, and statusElement are required");
    }
  }

  function bindSourceSuggestion(controls) {
    const controller = new SourceSuggestionController(controls).connect();
    return {
      clearAutomaticState: controller.clearAutomaticState.bind(controller),
    };
  }

  return Object.freeze({
    COPY,
    DEFAULT_LIMITS,
    REASON,
    SIGNATURE_VERSION,
    STATUS,
    SourceSuggestionController,
    bindSourceSuggestion,
    detectImportSource,
    inspectImportFile,
  });
});
