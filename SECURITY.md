# Security Policy

## Supported Versions

Before `v1.0.0`, security fixes are provided for the latest released version of
ai-memory-hub. Older prereleases and release candidates are not supported unless
explicitly called out in release notes.

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

Out of scope for the first release:

- hosted multi-tenant memory service behavior, because ai-memory-hub does not
  ship as a hosted service;
- unreleased browser extensions, SDKs, or UI dashboards;
- vulnerabilities that require intentionally disabling documented security
  controls without a product bug.
