# Project Promotion Plan

This plan turns the general GitHub star-growth guidance from
`iostyle/github-stars-guide` into a repeatable promotion workflow for
ai-memory-hub. The goal is not one launch post. The goal is a regular cadence
that keeps the project visible while preserving engineering credibility.

Source reference:
<https://github.com/iostyle/github-stars-guide>

## Positioning

Primary message:

> ai-memory-hub is local-first, MCP-native memory for AI agents. It lets Codex,
> opencode, Claude, Copilot, and other clients share one searchable memory
> backend through HTTP and MCP.

Use this message consistently, then tailor the detail for each audience:

| Audience | Emphasis |
| --- | --- |
| AI agent users | Cross-client memory handoff, local control, searchable past conversations |
| MCP developers | Streamable HTTP MCP tools, protocol compatibility, black-box Bruno tests |
| Local-first/open-source users | Self-hosted storage, SQLite/LanceDB default, Postgres/PGVector option |
| Infra/backend developers | Pluggable storage, deterministic ingestion, CI reports, container support |
| Privacy-sensitive users | Local-first design, bring-your-own model/storage, no hosted memory service |

## Promotion Principles

- Promote shipped behavior, not roadmap promises.
- Every post should point to one concrete user outcome.
- Prefer demos, screenshots, command snippets, and short runbooks over abstract
  claims.
- Keep posts honest about tradeoffs: ai-memory-hub is a backend/service, not a
  hosted embedding model or chat UI.
- Treat stars as a visibility signal, not the product goal. The product goal is
  adoption by people who need agent memory.

## Readiness Checklist

Run this checklist before every larger promotion push:

- [ ] `README.md` has a clear first-screen value proposition.
- [ ] Quick start works from a clean checkout.
- [ ] GitHub Actions are green on `main`.
- [ ] The current promoted feature has docs and at least one copy-paste command.
- [ ] The feature has a short demo path: local server, Compose stack, or Bruno
      command.
- [ ] Issues are enabled and triage labels exist.
- [ ] One or two starter issues are available for contributors.
- [ ] The latest known limitations are documented instead of hidden.

## Weekly Cadence

Repeat every week, preferably on the same day.

1. Pick one shipped feature or workflow.
2. Write a short demo note:
   - problem
   - command or screenshot
   - result
   - link to docs or README
3. Share it in two or three relevant places.
4. Watch comments for one day and reply quickly.
5. Capture useful feedback in an issue or improvement plan.

Good weekly topics:

- Save a conversation through MCP and ask about it from another client.
- Run the Postgres/PGVector Compose stack locally.
- Use Bruno to smoke-test the live API/MCP surface.
- Show fact-backed `memory_ask` answers with citations.
- Show JUnit CI reporting for pytest and Bruno runs.
- Explain when to use SQLite/LanceDB versus Postgres/PGVector.
- Show bearer-token auth before exposing a LAN service.

## Monthly Cadence

Once per month, publish a more substantial update.

- Write a release-style progress post even if there is no tagged release.
- Include three sections:
  - what shipped
  - what users can try today
  - what feedback would be most useful
- Add one demo asset:
  - terminal transcript
  - short screen recording
  - architecture diagram
  - before/after response example
- Update `README.md` if the project positioning changed.
- Review GitHub topics, description, pinned issue, and docs links.

First-release copy, repository settings, demo transcript, launch-day post, and
follow-up technical post are prepared in `release_promotion_assets.md`.

## Release Cadence

For tagged releases, use a larger promotion loop:

1. Publish release notes with upgrade notes and demo commands.
2. Post a short announcement on launch day.
3. Post a technical follow-up within a week.
4. Ask for specific feedback, not generic attention.
5. Convert repeated questions into docs or issues.

Release announcement structure:

```text
ai-memory-hub vX.Y.Z is out.

It adds <specific capability>.

Why it matters:
- <outcome 1>
- <outcome 2>
- <outcome 3>

Try it:
<one command block or docs link>

Repo: https://github.com/Artemon-line/ai-memory-hub
```

## Channels

Prioritize places where the project topic is directly relevant.

| Channel | Frequency | Content |
| --- | --- | --- |
| GitHub repository | Continuous | README, topics, releases, issues, discussions |
| LinkedIn/X/Mastodon/Bluesky | Weekly | Short demos, release notes, implementation lessons |
| Reddit | Monthly or feature-based | Practical walkthroughs in relevant communities |
| Hacker News | Only for notable releases | Clear launch/demo post, no hype wording |
| DEV.to/personal blog | Monthly | Deep dives and tutorials |
| MCP/agent communities | Weekly or biweekly | MCP-specific demos and compatibility notes |
| YouTube/short video | Monthly if sustainable | 2-5 minute workflow demos |

Do not cross-post identical text everywhere. Keep the same claim, but adapt the
example and tone to the audience.

## Content Backlog

### Short Posts

- "Cross-client memory handoff with MCP: save from Codex, ask from opencode."
- "Why ai-memory-hub stores raw conversations, facts, and generated summaries
  separately."
- "Using Postgres + PGVector as one durable memory backend."
- "Bruno as black-box API/MCP integration testing for an agent memory service."
- "Bearer-token auth for LAN-hosted local-first memory."
- "Choosing embeddings for multilingual AI conversation memory."

### Tutorials

- Build a local Codex/opencode memory handoff with Docker Compose.
- Add ai-memory-hub to an MCP client.
- Run the Bruno integration suite locally.
- Configure Postgres/PGVector and inspect readiness.
- Use fact search/profile views to answer direct user-profile questions.

### Technical Deep Dives

- Deterministic ingestion and deduplication.
- MCP streamable HTTP compatibility lessons.
- Fact-backed answers versus chunk-backed RAG answers.
- Storage abstraction: SQLite/LanceDB, Postgres/PGVector, and in-memory vectors.
- CI visibility with pytest/Bruno JUnit reporting.

## Community Workflow

- Keep response time under 48 hours for new issues and questions.
- Label issues as `bug`, `docs`, `good first issue`, `enhancement`, or
  `question`.
- Thank contributors in release notes when they report issues or improve docs.
- Close the loop publicly when feedback leads to a fix.
- Prefer small, reviewable first contributions: docs corrections, examples,
  Bruno cases, config recipes, and provider notes.

## Metrics

Track weekly:

- GitHub stars
- unique cloners
- unique visitors
- issue count and response time
- discussion/comment themes
- docs pages that get linked most often
- releases or posts that produced noticeable traffic

Track monthly:

- star growth rate
- contributor count
- repeat users or external mentions
- most common setup failures
- which examples drive adoption

Use metrics to choose the next post topic. If users repeatedly ask the same
question, promote the answer as a tutorial.

## Operating Calendar

Example four-week loop:

| Week | Action |
| --- | --- |
| 1 | Ship or polish one demoable workflow, then post a short demo. |
| 2 | Publish a technical note explaining a design choice. |
| 3 | Share a local runbook or integration tutorial. |
| 4 | Publish monthly progress notes and refresh README/docs if needed. |

## Acceptance Criteria

- [x] A reusable project pitch exists in README and docs.
- [ ] GitHub topics and repository description match the current positioning.
- [x] At least one demoable workflow is always linked from README.
- [ ] Weekly promotion produces one concrete post or tutorial note.
- [x] Monthly promotion produces one longer update or release-style summary.
- [ ] Feedback from promotion is captured as issues or improvement-plan updates.
- [x] Promotion never depends on unreleased behavior.
