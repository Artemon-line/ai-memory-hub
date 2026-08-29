# Documentation Map

The public docs are now split into three kinds of pages:

- **Start here pages** explain what exists and how to use it.
- **Planning pages** describe future work and implementation sequencing.
- **Reference pages** preserve detailed decisions, compatibility notes, and
  checklists.

## Start Here

| Page | Use it for |
| --- | --- |
| [Current features](features.md) | Fast inventory of shipped capabilities. |
| [Technical overview](overview.md) | API, MCP, CLI, configuration, containers, testing, and project structure. |
| [Agent integration](agents.md) | MCP tools, resources, prompts, and recommended agent workflows. |
| [Storage provider examples](storage_provider_examples.md) | Local and provider-specific setup examples. |
| [Observability](observability.md) | Logging, readiness, tracing, metrics, and local observability compose setup. |
| [Security](../SECURITY.md) | Vulnerability reporting and security expectations. |

## Planning Pages

| Page | Scope |
| --- | --- |
| [Roadmap](roadmap.md) | Ordered capability phases from MVP through optional sync/cloud work. |
| [Planned features](planned_features.md) | Human-friendly summary of what is not shipped yet. |
| [Prioritized feature plan](prioritized_feature_plan.md) | Near-term implementation queue and priority notes. |
| [First release readiness plan](first_release_readiness_plan.md) | Release hardening checklist. |
| [P0 beta governance checklist](improvements/p0_beta_governance_checklist.md) | Immutable-history, audit, quarantine, auth/project, clean install, and docs-matching checklist for beta. |
| [Release, container, and docs publishing plan](release_container_docs_plan.md) | Packaging and publishing work. |
| [Plugin readiness plan](plugin_readiness_plan.md) | Client/plugin setup and verification work. |
| [Observability, logging, and telemetry plan](observability_logging_telemetry_plan.md) | Detailed observability implementation plan. |

## Integration And Compliance Plans

| Page | Scope |
| --- | --- |
| [MCP plan](mcp_plan.md) | MCP feature direction. |
| [MCP client smoke plan](mcp_client_smoke_plan.md) | Client smoke strategy. |
| [Real-client MCP smoke plan](real_client_mcp_smoke_plan.md) | Real-client verification strategy. |
| [OpenClaw native MCP setup](openclaw_native_mcp_setup.md) | OpenClaw-specific setup. |
| [MCP utility compliance plan](mcp_utility_compliance_plan.md) | MCP utility behavior and compliance. |
| [MCP response format plan](improvements/mcp_response_format_plan.md) | P0 pre-release enum-based concise and detailed MCP read responses. |
| [Codex CLI MCP findings coverage plan](improvements/codex_cli_findings_coverage_plan.md) | Regression plan for the August 28, 2026 Codex CLI MCP QA findings. |
| [Agent model footprint plan](improvements/agent_model_footprint_plan.md) | P0 pre-release agent, client, model, and subagent provenance for saved memory. |
| [MCP authorization compliance plan](bearer_api_key_auth_plan.md) | Bearer token and OAuth resource metadata work. |
| [Google OAuth Connect UI plan](improvements/google_oauth_connect_ui_plan.md) | Google sign-in, web sessions, hub-issued MCP tokens, and client setup UI. |
| [Bruno integration test plan](bruno_integration_test_plan.md) | Bruno API/MCP smoke coverage. |

## Feature Plans

| Page | Scope |
| --- | --- |
| [Storage BYOA plan](storage_agnostic_byoa_plan.md) | Storage abstraction and provider behavior. |
| [Neo4j Aura graph memory plan](improvements/neo4j_aura_graph_memory_plan.md) | Neo4j AuraDB vector provider, graph record mirror, and graph-aware retrieval path. |
| [Deterministic ingestion plan](deterministic_ingestion_plan.md) | Schema-first ingestion, dedupe, and deterministic behavior. |
| [Browser extension capture plan](browser_extension_capture_plan.md) | Browser capture boundary, API contract, and future extension repos. |
| [Token budget plan](token_budget_plan.md) | Token accounting and context construction. |
| [CLI implementation plan](cli_implementation_plan.md) | CLI commands and behavior. |
| [Project workspace collaboration plan](project_workspace_collaboration_plan.md) | Workspace collaboration model. |
| [Handoff memory plan](improvements/handoff_memory_plan.md) | Cross-agent and cross-environment task continuity when context or budget runs out. |
| [Project promotion plan](project_promotion_plan.md) | Promotion assets and external-facing positioning. |
| [Release promotion assets](release_promotion_assets.md) | Release announcement and demo assets. |
| [Repository governance settings](repository_governance_settings.md) | Repository settings and governance checklist. |
| [Recurring codebase cleanup plan](recurring_codebase_cleanup_plan.md) | Ongoing engineering-health review. |

## Improvement Backlog

The [improvement plans](improvements.md) collect focused follow-up work for
client feedback, MCP result shapes, response formats, agent/model footprint, retrieval precision, context building,
conversation grouping, memory quality, edge-case coverage, explicit save intent,
handoff memory, Codex CLI MCP findings, Google OAuth connect UX, vector
database evaluation, and Neo4j Aura graph memory.
