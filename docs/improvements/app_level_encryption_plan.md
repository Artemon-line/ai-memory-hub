# App-Level Memory Encryption Plan

## Why This Is P0

ai-memory-hub protects memory access through API auth, owner/project scoping,
and secret-safe logging. That is not enough when an attacker gets direct access
to a database dump, a Postgres volume, a backup archive, or a running storage
credential. Local-first deployments on a Raspberry Pi, K3s cluster, NAS, or
always-on home server need a storage-layer failure to expose as little memory
content as possible.

App-level encryption protects canonical memory payloads before they are written
to metadata storage. Disk encryption still matters, but app-level encryption
keeps raw database access from being equivalent to raw memory access.

## Threat Model

In scope:

- Stolen database dumps or copied Postgres/SQLite volumes.
- Leaked database credentials without the encryption master key.
- Backups copied from the local server.
- Curious or compromised storage-provider operators for hosted-adjacent
  backends.
- Accidental plaintext leakage from schema migrations or provider exports.

Out of scope for the first implementation:

- A fully compromised running hub process.
- A host root compromise while keys are loaded in memory.
- Hiding query patterns, result counts, timestamps, owner IDs, or project IDs.
- Preventing semantic leakage from embeddings themselves.

## Design

Use envelope encryption with authenticated encryption:

- `encryption.enabled`: gates write-path encryption.
- `encryption.provider`: starts with `local`.
- `encryption.master_key_env`: names the environment variable containing the
  base64-encoded master key.
- `encryption.key_id`: records the active key version for rotation.
- `encryption.algorithm`: starts with `aes-256-gcm`.

Canonical conversation payloads are encrypted as JSON bytes. Each encrypted
record stores an envelope with ciphertext, nonce, algorithm, key ID, and enough
metadata for deterministic decryption. Authenticated associated data should bind
the ciphertext to stable row context such as table name, memory ID, owner ID,
project ID, and key ID.

## Search Privacy Tradeoff

Semantic search requires embeddings and usually stores indexed chunk text for
result snippets. The first implementation should encrypt canonical conversation
payloads and sensitive metadata, while explicitly documenting that vector index
text and embeddings may still leak partial meaning.

Follow-up hardening should add:

- `encryption.encrypt_index_text`
- encrypted vector chunk text
- result rendering that decrypts canonical payloads after auth checks
- tests proving database dumps do not contain inserted phrases in canonical
  payloads

## Phase 1: Config And Key Loading

- [ ] Add `encryption` config model with disabled defaults.
- [ ] Require a base64 32-byte master key when encryption is enabled.
- [ ] Fail closed when the key is missing, malformed, or too short.
- [ ] Add config validation errors that never print key material.
- [ ] Add startup health fields that report encryption enabled/key ID without
      exposing secrets.

## Phase 2: Encryption Service

- [ ] Add `memory/security/encryption.py`.
- [ ] Implement JSON encrypt/decrypt helpers around AES-GCM.
- [ ] Include authenticated associated data for row binding.
- [ ] Return structured envelope objects rather than ad hoc dictionaries.
- [ ] Add log-redaction tests for keys, ciphertext envelopes, and decrypted
      payload fragments.

## Phase 3: Metadata Store Integration

- [ ] Add encrypted payload columns to SQLite metadata storage.
- [ ] Add encrypted payload columns to Postgres metadata storage.
- [ ] Preserve legacy plaintext reads for existing rows.
- [ ] Encrypt new writes when enabled.
- [ ] Decrypt only after auth, owner, and project checks pass.
- [ ] Keep provider contract tests shared across SQLite and Postgres.

## Phase 4: Migration And Operations

- [ ] Add `aim encryption keygen`.
- [ ] Add `aim encryption status`.
- [ ] Add `aim encryption encrypt-existing` for explicit migration.
- [ ] Add `aim encryption rotate` for key rotation without plaintext export.
- [ ] Document K3s/Helm secret wiring for the master key.
- [ ] Document encrypted backup and restore procedures.

## Phase 5: Search Index Hardening

- [ ] Add optional encrypted indexed chunk text.
- [ ] Keep embeddings searchable without plaintext chunk text when possible.
- [ ] Reconstruct snippets from decrypted canonical payloads after authorization.
- [ ] Add a privacy-mode test fixture that verifies inserted phrases are absent
      from metadata payload storage and optional index text storage.

## Acceptance Criteria

- Enabling encryption causes new canonical memory payloads to be stored as
  ciphertext in SQLite and Postgres.
- Existing plaintext rows remain readable until explicitly migrated.
- A wrong or missing key fails closed and does not return corrupt plaintext.
- Search, retrieve, ask, facts, owner scoping, and project scoping keep their
  existing API/MCP behavior.
- Logs, health output, config display, and errors never expose plaintext memory
  content, master keys, data keys, authorization tokens, or database secrets.
- Backup guidance makes clear that encrypted app payloads complement, not
  replace, disk encryption and private-network deployment.

## PR Breakdown

1. Config model, key loading, encryption service, and unit tests.
2. SQLite encrypted canonical payload storage.
3. Postgres encrypted canonical payload storage.
4. CLI migration, status, keygen, and rotation commands.
5. Docs, K3s/Helm secret wiring, and backup guidance.
6. Optional search index text hardening.
