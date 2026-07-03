# Security Policy

## Supported Versions

Before `v1.0.0`, security fixes are provided for the latest released version of
ai-memory-hub. Older prereleases and release candidates are not supported unless
explicitly called out in release notes.

## Container Image Support

The first release publishes one primary Docker image from the checked-in
`Containerfile`. The latest stable tag and the latest immutable version tag are
the supported image lines for security fixes.

Release candidates are for validation only. They may receive replacement release
candidates, but they should not be treated as long-lived supported images.

The release workflow records the published image digest in the GitHub Actions
summary. Release notes should copy that digest so users can pin an exact image.

## Reporting A Vulnerability

Please report suspected vulnerabilities privately through GitHub Security
Advisories when available. If private advisories are not enabled, contact the
maintainer privately before opening a public issue.

Do not include secrets, API keys, bearer tokens, private conversation data,
embeddings, database dumps, or full production logs in a vulnerability report.
Use the smallest redacted reproduction that demonstrates the issue.

## Response Expectations

The project aims to acknowledge reports within 7 days and provide an initial
triage result within 14 days. Fix timelines depend on severity, exploitability,
and release impact.

## Security Scope

In scope:

- authentication and authorization bypasses;
- project workspace isolation failures;
- sensitive data exposure through logs, errors, or APIs;
- unsafe default behavior for LAN/container exposure;
- dependency or container vulnerabilities that affect supported runtime paths.

## Security-Fix Policy

Critical vulnerabilities in supported runtime paths should be fixed in the next
patch release when a practical fix is available. High-severity findings are
triaged based on exploitability, affected configuration, and whether the issue
applies to default local-first operation.

Supply-chain checks run in warning mode at first release so findings are visible
without blocking all PRs during baseline cleanup. The project should tighten
blocking thresholds after the initial vulnerability baseline is understood.

Out of scope for the first release:

- hosted multi-tenant memory service behavior, because ai-memory-hub does not
  ship as a hosted service;
- unreleased browser extensions, SDKs, or UI dashboards;
- vulnerabilities that require intentionally disabling documented security
  controls without a product bug.
