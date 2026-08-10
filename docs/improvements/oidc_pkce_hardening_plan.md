# OIDC And PKCE Hardening Plan

## Goal

Raise the Connect UI Google sign-in flow from early functional OAuth/OIDC support
to a production-defensible OIDC login path.

The hub should verify Google identity tokens cryptographically, bind callback
responses to the original browser login attempt, and use PKCE for provider
authorization requests in addition to the existing MCP authorization-code flow.

## Current State

The Connect UI currently:

- Generates and checks OAuth `state`.
- Sends a `nonce` to the provider.
- Exchanges the provider authorization code with the token endpoint.
- Extracts claims from the returned identity payload.
- Checks issuer, audience, expiry, subject, hosted-domain allowlists, and email
  allowlists.
- Issues hub-owned MCP bearer tokens after provider login.
- Requires PKCE for the MCP client authorization-code flow.

Known gaps:

- Google ID tokens are verified against provider JWKS for OIDC-configured
  providers.
- The returned ID token `nonce` is compared with the nonce saved at login start.
- Provider login does not send PKCE parameters.
- Provider metadata and JWKS behavior are modeled for OIDC-configured providers.
- Tests prove valid signed ID-token handling, rejected unsigned tokens, nonce
  mismatch, replayed state, expired state, and bounded state storage. Broader
  provider metadata/JWKS failure matrix and provider PKCE coverage remain.

## Phase 1: OIDC Provider Metadata

- [x] Add provider config fields for OIDC discovery:
      `issuer`, `discovery_url`, and optional `jwks_url`.
- [x] Default Google to `https://accounts.google.com` and
      `https://accounts.google.com/.well-known/openid-configuration`.
- [x] Load provider metadata through the `oauth` extra using HTTPX.
- [x] Cache discovered metadata and JWKS in memory with conservative expiry.
- [x] Reject insecure metadata URLs outside loopback test fixtures.
- [ ] Add tests for metadata discovery success, missing JWKS URI, wrong issuer,
      HTTP failure, and cache reuse.

## Phase 2: ID Token Verification

- [x] Replace local base64-only ID token decoding with JOSE verification.
- [x] Verify the ID token signature against the provider JWKS.
- [x] Require an allowed signing algorithm, initially `RS256` for Google.
- [x] Validate `iss`, `aud`, `exp`, `iat`, and `sub` after signature
      verification.
- [x] Add small clock-skew tolerance for time claims.
- [x] Continue enforcing hosted-domain and email allowlists after token
      verification.
- [x] Return a 403 for invalid identity tokens without leaking raw token details.
- [ ] Add tests for valid signed token, wrong key, wrong algorithm, wrong
      issuer, wrong audience, expired token, future-issued token, and missing
      subject.

## Phase 3: Nonce Binding

- [x] Store the generated provider `nonce` with the OAuth state.
- [x] Require the verified ID token `nonce` to match the stored nonce.
- [x] Treat missing nonce as invalid for providers configured as OIDC.
- [x] Consume the state record once, including on failed callback validation.
- [ ] Add tests for matching nonce, missing nonce, wrong nonce, replayed state,
      and expired state.

## Phase 4: Provider PKCE

- [ ] Generate a high-entropy `code_verifier` when starting provider login.
- [ ] Store only the verifier in the server-side state record.
- [ ] Send `code_challenge` and `code_challenge_method=S256` to Google.
- [ ] Include `code_verifier` in the provider token exchange.
- [ ] Keep the existing confidential-client `client_secret` exchange for Google
      unless provider requirements change.
- [ ] Add tests proving the authorization URL contains S256 PKCE parameters and
      the token request includes the matching verifier.

## Phase 5: Dependency And Docs Cleanup

- [x] Make the `oauth` extra description mention HTTPX plus JOSE/OIDC token
      verification dependencies, not only Authlib.
- [ ] Remove or use any stale Authlib client helper code so the dependency story
      matches implementation.
- [ ] Update `docs/connect_ui.md` to distinguish:
      rendering `/connect`, provider OAuth/OIDC login, hub token issuance, and
      MCP bearer-token validation.
- [ ] Update the Google OAuth Connect UI plan checkboxes so completed items only
      describe behavior that is implemented and tested.
- [ ] Document that public deployments require HTTPS, stable secrets, and
      provider allowlists where appropriate.

## Phase 6: Operational Hardening

- [ ] Add structured audit events for login start, callback success, callback
      denial, token issuance, logout, and token revocation.
- [ ] Ensure logs never include authorization codes, ID tokens, access tokens,
      refresh tokens, hub bearer tokens, session IDs, or raw callback query
      strings.
- [ ] Add negative log-capture tests for token and code leakage.
- [ ] Add rate limiting for login start, callback, token exchange, and OAuth
      token issuance once a shared rate-limit mechanism exists.
- [ ] Add readiness output that reports OIDC metadata/JWKS availability without
      exposing secrets.

## Acceptance Criteria

- [ ] A Google sign-in succeeds only when the returned ID token has a valid
      Google signature, expected issuer, expected audience, valid time claims,
      matching nonce, and allowed user identity.
- [ ] Provider authorization requests use PKCE S256 and token exchanges submit
      the matching verifier.
- [ ] Invalid ID tokens, nonce failures, replayed states, and PKCE mismatches are
      covered by tests.
- [ ] `/connect` still renders without the `oauth` extra, but provider login and
      hub token issuance clearly fail closed with 503 errors when required
      dependencies are unavailable.
- [ ] Docs no longer imply cryptographic OIDC validation is complete until the
      tests prove it.
