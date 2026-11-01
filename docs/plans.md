# Documentation And Planning

The public documentation is organized around shipped behavior. Start with:

| Page | Purpose |
| --- | --- |
| [Current features](features.md) | What is implemented now. |
| [Project status](project_status.md) | Evidence-based done, partial, and not-started classification. |
| [Planned features](planned_features.md) | Short product-level view of remaining work. |
| [Technical overview](overview.md) | Runtime, API, MCP, CLI, configuration, and project structure. |
| [Agent integration](agents.md) | MCP tools, resources, prompts, and client behavior. |
| [Architecture](architecture.md) | Implemented system and provider boundaries. |

## Where Work Is Tracked

GitHub issues are the source of truth for unfinished implementation work. Use
the [`enhancement` issue list](https://github.com/Artemon-line/ai-memory-hub/issues?q=is%3Aissue%20is%3Aopen%20label%3Aenhancement)
for active epics and child issues.

The repository still contains older plan and improvement Markdown files because
some preserve useful design decisions, validation evidence, or compatibility
history. They are no longer presented as the public navigation or treated as an
authoritative backlog. Their unchecked boxes may be stale; use
[Project status](project_status.md) and linked GitHub issues instead.

## Documentation Rule

New product work should have a GitHub issue with acceptance criteria. Add a
Markdown design record only when the design remains useful after the work is
complete. User-facing docs should describe behavior that actually ships.
