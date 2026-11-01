# Roadmap

ai-memory-hub has completed its core local-first memory-service foundation. The
roadmap now focuses on making that foundation easier to release, safer to
operate, simpler to adopt from agent clients, and more useful as a memory
product without weakening immutable history, authorization, provenance, or
provider-neutral behavior.

GitHub owns roadmap scheduling and implementation detail:

- [All GitHub milestones](https://github.com/Artemon-line/ai-memory-hub/milestones)
- [Current `1.0.1` milestone](https://github.com/Artemon-line/ai-memory-hub/milestone/1)
- [Open roadmap epics](https://github.com/Artemon-line/ai-memory-hub/issues?q=is%3Aissue%20is%3Aopen%20%22%5BEpic%5D%22)

Milestones answer *when a reviewed set of work is targeted*. Epics describe the
larger outcome and its compatibility boundaries. Child issues should contain
the PR-sized implementation work.

## Current Direction

### Stabilize and release

Complete repository governance, release rehearsal, security-boundary hardening,
and the remaining verified import compatibility work. This is the shortest path
from the implemented service to a repeatable, supportable release.

- [Release governance and distribution #345](https://github.com/Artemon-line/ai-memory-hub/issues/345)
- [OIDC, SQL, and exposure hardening #347](https://github.com/Artemon-line/ai-memory-hub/issues/347)
- [Capture and import coverage #349](https://github.com/Artemon-line/ai-memory-hub/issues/349)
- [Efficiency and operational hardening #344](https://github.com/Artemon-line/ai-memory-hub/issues/344)

### Improve provenance and adoption

Preserve exact agent/model provenance, package the existing MCP surface for
Codex, and make setup and failure recovery clear across supported clients.

- [Agent provenance and temporal semantics #346](https://github.com/Artemon-line/ai-memory-hub/issues/346)
- [Codex plugin and MCP client experience #350](https://github.com/Artemon-line/ai-memory-hub/issues/350)

### Build the memory product

Add a user-facing workspace and supported SDKs, then expand cited and
rebuildable intelligence over immutable source memories. Lifecycle controls
must remain explicit, audited, and permission-aware.

- [Memory workspace UI and SDKs #351](https://github.com/Artemon-line/ai-memory-hub/issues/351)
- [Advanced intelligence and lifecycle #352](https://github.com/Artemon-line/ai-memory-hub/issues/352)

### Expand privacy and storage deliberately

Add opt-in application encryption and graph/provider capabilities only with
migration, recovery, isolation, quality, and operational evidence.

- [App-level encryption and key operations #348](https://github.com/Artemon-line/ai-memory-hub/issues/348)
- [Neo4j and provider expansion #353](https://github.com/Artemon-line/ai-memory-hub/issues/353)

## Roadmap Constraints

Every milestone and epic must preserve:

- local-first operation and explicit opt-in for hosted or sync behavior;
- immutable source-memory evidence and cited, rebuildable derived records;
- owner, project, auth-scope, quarantine, and audit boundaries;
- stable HTTP/MCP contracts or an explicit compatibility plan;
- provider-neutral behavior, bounded fallback, and secret-safe diagnostics;
- measurable quality, latency, storage, and model-cost guardrails.

See [Project status](project_status.md) for the evidence-based classification of
what is done, partial, or not started. See
[Documentation and planning](plans.md) for the repository planning policy.
