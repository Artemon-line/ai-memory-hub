# Project Status And Remaining Work

Last reviewed: 31 October 2026

This page is the current evidence-based view of ai-memory-hub. GitHub issues,
not Markdown checklists, own unfinished work. Historical plan documents may
explain design decisions, but their unchecked boxes are not the backlog.

The classification below was reconciled against runtime code, tests, workflows,
the current GitHub issue history, and repository settings. A capability is:

- **Done** when it is implemented, covered by representative tests, and exposed
  through the documented surface.
- **Partial** when a useful slice ships but a material part of the intended
  outcome remains.
- **Not started** when only design text, compatibility scaffolding, or adjacent
  infrastructure exists.

## Executive Summary

The service foundation is substantially complete. ai-memory-hub already ships
schema-first ingestion, deterministic deduplication, HTTP/MCP/CLI workflows,
pluggable metadata and vector storage, auth and project isolation, importers,
fact and temporal projections, handoffs, observability, quality gates, container
and documentation automation, and a tested Connect UI.

The remaining work is concentrated in productization and advanced capabilities:
release enforcement, plugin packaging, message-level model provenance,
provider-login hardening, app-level encryption, the remaining import/capture
matrix, user-facing memory management, SDKs, advanced intelligence and graph
features, Neo4j/provider expansion, and measured operational efficiency.

## Done

| Capability | Evidence |
| --- | --- |
| Core ingestion and deterministic storage | HTTP/MCP/CLI insert paths, schema validation, hashing, append/re-import reconciliation, chunk manifests, and unit/integration coverage. |
| Search, retrieve, ask, and response shaping | Hybrid retrieval, filters, rank fusion/reranking, token budgeting, citations, concise/detailed formats, and retrieval quality benchmarks. |
| Fact, profile, and temporal foundations | Derived facts, supersession, latest-value projection, timelines, generated summaries, provenance, and profile/fact APIs and MCP tools. |
| Handoff memory | Retrieval-only and stored handoffs, lineage, source/workspace provenance, resume hints, HTTP/MCP/CLI surfaces, and safety coverage. |
| Auth and collaboration baseline | Bearer and OAuth resource-server modes, external OIDC validation, owner/project isolation, roles, consent, durable OAuth state, and audit events. |
| Storage abstraction baseline | SQLite/Postgres/MongoDB metadata and fourteen advertised vector providers with capability checks, fallback behavior, dry run, and provider tests. |
| Import platform | Registry-backed import API/CLI/UI, compatibility reporting, safe latest discovery, stable imported-session identity, and importers for major local-agent formats. |
| Observability baseline | Structured logs, request/trace correlation, health/readiness/observability endpoints, OpenTelemetry instrumentation, metrics, and a local observability stack. |
| Engineering and release automation baseline | Ruff, Pyright, pytest, Bruno, CodeQL, dependency review, container smoke, image scanning/SBOM, docs publishing, and Docker publish workflows. |
| Completed focused plans | Token budgeting, CLI, Bruno, retrieval precision, context building, conversation grouping, MCP response format, edge-case coverage, and beta governance. |

## Partially Done

| Program | Shipped | Remaining outcome | Tracking |
| --- | --- | --- | --- |
| Release and repository governance | Release validation, Pages, container publish, scans, templates, and branch review rules exist. | Required checks are not configured; admin enforcement, release drill, external secrets/repository setup, and launch verification remain. | [#345](https://github.com/Artemon-line/ai-memory-hub/issues/345). |
| Agent/model provenance and temporal semantics | Conversation-level source/model metadata and robust temporal projections ship. | Per-message agent/model/provider attribution, propagation into derived records, generic extraction cleanup, and latest/timeline fact search modes remain. | [#346](https://github.com/Artemon-line/ai-memory-hub/issues/346). |
| OIDC, SQL, and exposure hardening | Signed OIDC tokens, nonce/state controls, OAuth consent/state durability, parameterized SQL, and secret-safe logging ship. | Provider-login PKCE, broader negative matrices, shared rate limiting, explicit SQL identifier guardrails, and unsafe-bind enforcement remain. | [#347](https://github.com/Artemon-line/ai-memory-hub/issues/347). |
| Capture and import coverage | Many local-agent/consumer export importers and the import UI ship; DeepSeek web share and account-export contracts are explicitly separated. | Gemini Takeout, Claude HTML, optional local-LLM logs, browser-extension contract hardening, account-export support, and compatibility maintenance remain. | [#349](https://github.com/Artemon-line/ai-memory-hub/issues/349), with account-export work in [#197](https://github.com/Artemon-line/ai-memory-hub/issues/197). |
| MCP clients and Codex plugin | Stable tools/resources/prompts, client profiles, real-client harness, and Connect setup guidance ship. | Installable plugin wrapper, skills, marketplace metadata, first-run health checks, strict real-client promotion, and need-driven MCP completion remain. | [#350](https://github.com/Artemon-line/ai-memory-hub/issues/350). |
| Product UI and SDKs | Connect and import UI exist. | Search/conversation/timeline/fact-review/storage-health UI, published OpenAPI examples, Python/TypeScript SDKs, and packaging polish remain. | [#351](https://github.com/Artemon-line/ai-memory-hub/issues/351). |
| Memory intelligence and lifecycle | Deterministic facts, summaries, retrieval evaluation, graph record foundations, quarantine/review, and immutable history ship. | Hosted extraction, digests, clustering, consolidation, admin archive/restore and mutation policy, decay/pinning/forgetting, and shared/agent views remain. | [#352](https://github.com/Artemon-line/ai-memory-hub/issues/352). |
| Graph and provider expansion | Provider contracts and graph quality gates exist. | Neo4j Aura adapter/mirror/retrieval and evidence-based decisions for Meilisearch, DuckDB VSS, or sqlite-vec remain. | [#353](https://github.com/Artemon-line/ai-memory-hub/issues/353). |
| Efficiency | Reproducible corpora, concurrency profiles, work ledgers, reporting, and several request-path optimizations ship. | Provider lifecycle, bounded concurrency, amplification reduction, context/model cost, deadlines, overload behavior, and multi-profile targets remain. | [#344](https://github.com/Artemon-line/ai-memory-hub/issues/344). |

## Not Started

| Capability | Boundary |
| --- | --- |
| App-level payload encryption and key rotation | No encryption service, encrypted metadata columns, migration commands, or encrypted-backup operating model exists. This must remain disabled by default and must not be confused with disk encryption. Tracked in [#348](https://github.com/Artemon-line/ai-memory-hub/issues/348). |
| Installable Codex plugin package | The service is plugin-ready at the MCP boundary, but no `.codex-plugin/plugin.json`, personal marketplace entry, bundled skills, or plugin lifecycle exists. Tracked in [#350](https://github.com/Artemon-line/ai-memory-hub/issues/350). |
| Neo4j Aura provider | Design and quality-gate scaffolding exist, but there is no Neo4j vector adapter, graph mirror, factory wiring, or live provider test. Tracked in [#353](https://github.com/Artemon-line/ai-memory-hub/issues/353). |
| General end-user memory dashboard and SDKs | Connect/import pages are not a search, browse, review, diagnostics, or developer SDK product surface. Tracked in [#351](https://github.com/Artemon-line/ai-memory-hub/issues/351). |
| Encrypted multi-device sync | Backup/sync/conflict handling remains a later optional program and must preserve local-first defaults. |

## Plan Document Reconciliation

The old plan set mixed four different things: completed implementation records,
current backlog, operational runbooks, and speculative product ideas. That made
checkbox counts actively misleading. Use these dispositions when cleaning or
interpreting those files:

| Disposition | Documents |
| --- | --- |
| Completed plan removed after reconciliation | Token budgeting, CLI implementation, Bruno integration, browser capture boundary, bearer/OAuth authorization baseline, deterministic ingestion, retrieval precision, context building, conversation grouping, MCP response formatting, edge-case coverage, graph quality gate, and beta governance. Shipped behavior remains in current feature, architecture, agent, overview, security, and quality documentation. |
| Mostly complete; remaining work moved to an epic | `observability_logging_telemetry_plan.md`, `mcp_utility_compliance_plan.md`, `project_workspace_collaboration_plan.md`, `mcp_client_smoke_plan.md`, `real_client_mcp_smoke_plan.md`, `release_container_docs_plan.md`, `first_release_readiness_plan.md`, `improvements/client_feedback_improvement_plan.md`, `improvements/explicit_memory_save_intent_plan.md`, `improvements/google_oauth_connect_ui_plan.md`, `improvements/handoff_memory_plan.md`, `improvements/memory_quality_fact_layer_plan.md`, `improvements/temporal_memory_index_plan.md`, `improvements/codex_cli_findings_coverage_plan.md`, `improvements/p0_live_mcp_retest_followup_plan.md`. |
| Partial; epic becomes the implementation source of truth | `plugin_readiness_plan.md`, `improvements/agent_model_footprint_plan.md`, `improvements/oidc_pkce_hardening_plan.md`, `improvements/advanced_memory_plan.md`, and `improvements/efficiency_revival_plan.md`. |
| Not started; epic becomes the implementation source of truth | `improvements/app_level_encryption_plan.md`, `improvements/neo4j_aura_graph_memory_plan.md`, `improvements/p1_sql_hardening_plan.md`, `improvements/weekly_self_healing_plan.md`. |
| Operational/reference material, not backlog | `release_ci_gap_analysis.md`, `release_security_scan_notes.md`, `repository_governance_settings.md`, `postgres_driver_decision.md`, `quality_evaluation.md`, `improvements/vector_dbs.md`, and client setup guides. |

## Rules Going Forward

- Put unfinished work in a GitHub issue with acceptance criteria and explicit
  compatibility boundaries.
- Keep public docs focused on shipped behavior, setup, operations, architecture,
  and a short status summary.
- Do not add new checkbox-based implementation plans under `docs/`.
- Close an epic only after its shipped docs and tests match the implementation.
- Preserve detailed design decisions in an ADR or architecture page when they
  remain useful after the work is complete.
