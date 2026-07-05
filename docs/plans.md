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
| [MCP authorization compliance plan](bearer_api_key_auth_plan.md) | Bearer token and OAuth resource metadata work. |
| [Bruno integration test plan](bruno_integration_test_plan.md) | Bruno API/MCP smoke coverage. |

## Feature Plans

| Page | Scope |
| --- | --- |
| [Storage BYOA plan](storage_agnostic_byoa_plan.md) | Storage abstraction and provider behavior. |
| [Deterministic ingestion plan](deterministic_ingestion_plan.md) | Schema-first ingestion, dedupe, and deterministic behavior. |
| [Browser extension capture plan](browser_extension_capture_plan.md) | Browser capture boundary and future extension direction. |
| [Token budget plan](token_budget_plan.md) | Token accounting and context construction. |
| [CLI implementation plan](cli_implementation_plan.md) | CLI commands and behavior. |
| [Project workspace collaboration plan](project_workspace_collaboration_plan.md) | Workspace collaboration model. |
| [Project promotion plan](project_promotion_plan.md) | Promotion assets and external-facing positioning. |
| [Release promotion assets](release_promotion_assets.md) | Release announcement and demo assets. |
| [Repository governance settings](repository_governance_settings.md) | Repository settings and governance checklist. |
| [Recurring codebase cleanup plan](recurring_codebase_cleanup_plan.md) | Ongoing engineering-health review. |

## Improvement Backlog

The [improvement plans](improvements.md) collect focused follow-up work for
client feedback, MCP result shapes, retrieval precision, context building,
conversation grouping, memory quality, edge-case coverage, explicit save intent,
and vector database evaluation.

