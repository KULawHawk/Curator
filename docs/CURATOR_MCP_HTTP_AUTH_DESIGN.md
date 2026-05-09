# `curator-mcp` HTTP authentication — Design

**Status:** v0.2 — RATIFIED 2026-05-08. All six DMs ratified by Jake's affirmative reply ("1") on 2026-05-08. P1 implementation cleared to start.
**Date:** 2026-05-08
**Authority:** Subordinate to Atrium `CONSTITUTION.md` v0.3. Implements Aim 1 (Accuracy — auth state must be correct), Aim 8 (Auditability — auth events must be audited), and Article II Principle 4 (No Silent Failures — auth refusals must be surfaced to the caller, not dropped).

**Companion documents:**
- `docs/CURATOR_MCP_SERVER_DESIGN.md` v0.2 IMPLEMENTED — the v1.2.0 design that established stdio + HTTP transports and explicitly deferred HTTP auth to a future minor release. §3 DM-5 ratified the deferral with the no-auth-for-loopback-only constraint.
- `Atrium\CONSTITUTION.md` v0.3 — the supreme governance document this design subordinates to.
- `BUILD_TRACKER.md` — the v1.5.0 candidate item for MCP HTTP-auth (per Tracer Phase 4 v0.2 RATIFIED DM-6, deferred from earlier minors).

---

## 1. Scope

### 1.1 The problem

`curator-mcp` v1.2.0+ ships an HTTP transport that has **no authentication**. The current code refuses to bind to non-loopback addresses (`--host` other than `127.0.0.1` / `localhost` / `::1` exits 2 with an error). This means:

- Users CAN run `curator-mcp --http` for local-machine LLM clients (Claude Desktop on the same machine, locally-running scripts).
- Users CANNOT expose the MCP server to other machines on their network or to the internet.
- "Local-machine HTTP" is a relatively narrow use case — Claude Desktop already supports stdio transport which is preferred for local single-machine setups.

The HTTP transport's main value proposition is **multi-machine access**: a Curator instance running on a workstation can be queried by an LLM client running on a laptop, a phone (via VPN/tailscale), a CI runner, or a teammate's machine. None of those work today because we refuse to bind beyond loopback.

### 1.2 What this design adds

API key authentication for the HTTP transport. After v1.5.0:

1. **Default behavior changes:** HTTP transport requires authentication. Connections without a valid API key receive a 401 Unauthorized response.
2. **Non-loopback binding becomes legal.** With auth in place, users can `curator-mcp --http --host 0.0.0.0` (binding to all interfaces) or `--host 192.168.1.10` (specific LAN IP) without the v1.2.0 hard refusal.
3. **Per-integration keys.** User generates named API keys via a new `curator mcp` subcommand. Each key has a name (e.g., `claude-desktop-laptop`, `scripts-prod`), a creation timestamp, and can be revoked individually.
4. **Audit trail for auth events.** Every successful + failed auth attempt lands in Curator's existing audit log under `actor='curator-mcp'`. Failed attempts are immediate security signals; successful attempts are the "who is querying my Curator" introspection record.
5. **stdio transport unchanged.** stdio doesn't need auth — process boundaries are the security model. Claude Desktop / Claude Code stdio integrations work identically before and after.

### 1.3 What this design is NOT

- **Not OAuth.** OAuth flows are designed for delegated access (an app accessing user data on a third-party service); MCP is a single-user single-trust-domain scenario. Bringing OAuth in would multiply complexity without serving the use case.
- **Not TLS termination.** v1.5.0 ships HTTP-with-auth, not HTTPS. Users who need encryption (e.g., over the public internet) put the MCP server behind nginx/Caddy/Traefik for TLS. Curator stays focused on auth; TLS is an infrastructure concern.
- **Not multi-tenant authorization.** All keys grant identical access to the same Curator instance. v0.2+ may add per-key scopes (e.g., a read-only key vs. a key with future write tools), but v1.5.0 is single-permission.
- **Not key rotation automation.** v1.5.0 ships generate/list/revoke. Rotation is "revoke old, generate new, update integrations" — manual orchestration, by design. Automation can come later if the volume warrants.
- **Not a replacement for the loopback-only restriction.** `curator-mcp --http` without `--require-auth` (or equivalent) still refuses non-loopback. The restriction lifts only when auth is configured.

---

## 2. Invariants the design must preserve

1. **stdio transport is unchanged.** Existing Claude Desktop / Claude Code integrations using stdio see no behavior change. No new dependencies, no new error paths, no new config required.
2. **Auth is opt-out, not opt-in.** `curator-mcp --http` with no flags requires auth. Users who explicitly want unauthenticated local-loopback HTTP (e.g., for development) pass `--no-auth` and accept the same v1.2.0 loopback-only restriction.
3. **Auth state is queryable.** A `curator mcp keys list` command shows registered keys (names + creation timestamps; not the actual secret values). Audit log shows when each key was last used.
4. **Auth refusals never crash the server.** A 401 is returned to the caller; the server keeps running and ready for the next request. Server-side exceptions during auth (e.g., DB unavailable for audit emission) gracefully degrade — auth proceeds, audit emission fails silently with logger.warning.
5. **Audit emission is best-effort.** Per Atrium Constitution Principle 4 (No Silent Failures), auth refusals must surface in the response to the caller. Audit log is supplementary; missing audit doesn't change the user-facing outcome.
6. **No new persistent state schema.** Keys live in a single JSON file at `~/.curator/mcp/api-keys.json`. SQLite schema is untouched. (Audit log uses Curator's existing audit_log table.)
7. **Keys never appear in audit log details.** Failed-auth audit entries record the key prefix (e.g., first 6 chars) for forensics, never the full key. Successful-auth entries record the key name.

---

## 3. Decisions Jake needs to make

### DM-1 — Auth mechanism

**Question.** What's the wire-protocol shape of authentication?

Options:

- (a) **API key in HTTP header.** Standard pattern. Either `Authorization: Bearer <key>` (RFC 6750) or `X-Curator-API-Key: <key>` (custom). Simple, widely supported by HTTP clients.
- (b) **API key in query parameter** (`?api_key=...`). Easy for browser-based clients; bad practice (logged in server access logs, shared URLs leak credentials).
- (c) **OAuth 2.0 with auth code or client credentials grant.** Overkill for a single-user single-trust-domain scenario. Adds a token-issuance endpoint, refresh logic, and a third-party concept (the auth server) for a single-tenant use case.
- (d) **TLS client certificates (mutual TLS).** Strongest cryptographically; high operational overhead (certificate authorities, expiration, deployment). Overkill for v1.5.0.

**Recommendation: (a) `Authorization: Bearer <key>`** (RFC 6750 standard).

Rationale: Bearer tokens are the standard HTTP API auth mechanism. Every HTTP client (curl, Python requests, fetch, Claude Desktop's MCP client) supports the `Authorization` header natively. Custom header names like `X-Curator-API-Key` are slightly easier to reason about ("this is a Curator-specific thing") but trade off ecosystem compatibility for negligible clarity gain. (b) is a security antipattern. (c) and (d) overshoot the use case.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Authentication is conveyed via the `Authorization: Bearer <key>` HTTP header per RFC 6750. Missing or malformed headers produce a 401 Unauthorized response.

### DM-2 — Key storage

**Question.** Where do API keys live?

Options:

- (a) **Single JSON file** at `~/.curator/mcp/api-keys.json`. Curator reads on every auth attempt (cheap; file is tiny). Permissions set to `0600` on Unix; on Windows, ACL via `icacls`. Simple, durable, easy to inspect, easy to back up.
- (b) **SQLite table** in Curator's existing DB. Atomic with audit log, schema-managed. Slightly more complex (migration to add the table); less portable (DB might be on a different filesystem than where MCP server runs).
- (c) **OS keyring** via `keyring` library (uses Windows Credential Manager / macOS Keychain / Linux Secret Service). Most secure at rest; harder to inspect, harder to back up, requires a system-level dependency.
- (d) **Environment variable** for a single key. Simplest possible; loses multi-key support, loses persistence across reboots-without-shell-init.

**Recommendation: (a) JSON file at `~/.curator/mcp/api-keys.json`** with `0600` permissions on Unix.

Rationale: Curator already uses `~/.curator/` for its config and Drive credentials, so adding `mcp/api-keys.json` under that tree is consistent with existing convention. JSON is human-inspectable (good for debugging), easy to back up (just copy the file), supports multiple keys natively (it's a list/dict), and doesn't require a new dependency. The file's permissions are the security boundary; this is the same model as SSH keys, AWS credentials, gpg keyrings, and most CLI tools. (b) couples auth to DB schema migrations unnecessarily. (c) makes inspection harder and adds a system-level dependency that complicates the install. (d) loses multi-key support which is core to the design.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. API keys live in a single JSON file at `~/.curator/mcp/api-keys.json` (resolves to `C:\Users\jmlee\.curator\mcp\api-keys.json` on Windows). On Unix, file permissions are set to `0600`; on Windows, ACLs are tightened to current-user-only via `icacls`. The file stores `key_hash` (sha256 of the key), never the plaintext key.

### DM-3 — Key format

**Question.** What does an API key actually look like?

Options:

- (a) **44-char URL-safe base64 random string** (`secrets.token_urlsafe(32)` — 32 random bytes). Format: `aBcDeF...` (no separators, no structure). Indistinguishable from random.
- (b) **Two-part (key_id + secret)**, like AWS access keys: `curator_a1b2c3d4` + `s3cr3t...`. Allows revoking by key_id without exposing the secret in logs. More complex.
- (c) **JWT**. Self-describing token with embedded claims (e.g., creation time, name). No state on the server — verify signature, accept. Loses revocation (can't invalidate an issued JWT without a separate blocklist).
- (d) **Format-prefixed** like GitHub tokens: `curm_<random>` where `curm_` identifies the source ("Curator MCP"). Helps users + secret-scanners recognize Curator keys in source code or logs.

**Recommendation: (d) `curm_` prefix + 40 chars URL-safe base64**, e.g., `curm_a1B2c3D4e5F6...` (44 total chars).

Rationale: Format prefixing follows the established pattern of GitHub (`ghp_`), Stripe (`sk_`), OpenAI (`sk-`), Anthropic (`sk-ant-`). It costs us nothing and provides material value: secret-scanners (truffleHog, gitleaks, GitHub's own secret scanning) can be configured to flag accidentally-committed Curator MCP keys. Users grepping their own logs can identify Curator-key leaks. The prefix is not security-relevant (the entropy is in the random portion); it's identification metadata. (a) lacks identification. (b) adds two-part complexity for revocation that's already solved by storing keys server-side. (c) loses revocation, which is a hard requirement for credential management.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Keys are formatted as `curm_<40-char-random>` where the random portion is `secrets.token_urlsafe(30)` (which yields ~40 URL-safe-base64 chars, depending on stripping). Total key length is 44–46 characters.

### DM-4 — Multi-key support

**Question.** How many keys can a user have?

Options:

- (a) **Multiple named keys**, each with `name`, `created_at`, `last_used_at`, optional `description`. Users generate one per integration (e.g., `claude-desktop-home`, `claude-desktop-work`, `scripts-prod`). Revoke individually.
- (b) **Single key.** If the user wants multiple integrations, they share the same key (and rotate by changing it everywhere when revoking). Much simpler.
- (c) **Multiple keys but no metadata.** Each is an opaque token; no way to attribute "which key is being used by which integration."

**Recommendation: (a) Multiple named keys** with `name`, `created_at`, `last_used_at`, optional `description`.

Rationale: Per-integration keys are how every modern API works (GitHub PATs, AWS IAM keys, Stripe keys, OpenAI keys). The marginal complexity is small (a JSON dict instead of a single string), and the value is large: when a teammate's laptop is lost, the user revokes the laptop's key without breaking the home setup. Without multi-key support, revoking ANY integration breaks ALL integrations. (b) creates an obvious antipattern; (c) makes the audit log less useful (auth events would just say "key X used" with no friendly name).

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. The keys file holds a list of named keys; each entry has `name` (unique), `key_hash`, `created_at`, `last_used_at`, optional `description`. Names are namespaced per-user (this is single-user software); collisions on `generate` produce an error before any key material is created.

### DM-5 — Audit emission for auth events

**Question.** Which auth-related events get logged to Curator's audit log?

Options:

- (a) **Successful + failed auth.** Every authenticated request gets an `mcp.auth_success` event with `actor='curator-mcp'`, key name in details. Every refused request gets `mcp.auth_failure` with key prefix (first 6 chars) + reason in details.
- (b) **Failed auth only.** Successful authentication is the normal case; audit-logging it is volume noise. Failures are security signals.
- (c) **Failed auth + first-use of each key.** Volume-friendly: log when a key is used for the first time after generation, then go quiet until the next failure or revocation.
- (d) **No audit emission.** Auth state is internal to the MCP server; surfacing it in the main audit log is overreach.

**Recommendation: (a) Successful + failed auth, with throttling for successful events.**

Rationale: Auth events are exactly the kind of activity Atrium Constitution Aim 8 (Auditability) is for — "every fact about every file at every moment in history is recoverable." The audit log already supports this kind of activity. The volume concern is real (a heavily-used MCP integration could emit thousands of auth_success events per day), but it's solvable by emitting a `mcp.auth_success` event no more than once per key per minute (the `last_used_at` field is updated continuously; the audit emission is throttled). Failed auth is always emitted (no throttling — security signal). (b) loses the "who used what when" capability that's useful both for the user (introspection) and for incident response (post-compromise auditing). (c) is too clever. (d) violates the audit-everything principle.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. Both successful and failed auth attempts emit audit events under `actor='curator-mcp'`. Successful-auth events are throttled to no more than 1 per key per minute (state held in-memory; resets on server restart). Failed-auth events are NEVER throttled — they're security signals. Failed events record only the key prefix (first 10 chars) in details; full key material never reaches the audit log.

### DM-6 — Auth toggle + bypass for local development

**Question.** What's the mental model for "auth required vs. not"?

Options:

- (a) **Auth required by default; `--no-auth` flag opts out.** Default behavior is secure: `curator-mcp --http` requires auth. Users who explicitly want to develop locally can pass `--no-auth`, which still applies the v1.2.0 loopback-only restriction. The flag's name is intentionally noisy.
- (b) **Auth required for non-loopback only.** `--http --host 127.0.0.1` (default) doesn't require auth; `--http --host 0.0.0.0` does. Loopback HTTP is treated as equivalent to stdio for security purposes.
- (c) **Auth always required, no bypass.** Users who want unauthenticated local HTTP run their own reverse proxy that strips auth. Removes a foot-gun completely.
- (d) **Auth state determined by config file presence.** If `~/.curator/mcp/api-keys.json` exists with at least one key, auth is required; otherwise it's permitted unauthenticated.

**Recommendation: (a) Auth required by default; `--no-auth` opts out (and still loopback-only).**

Rationale: This is the secure-by-default convention. Users who want to bind to non-loopback are forced to have auth configured (no `--no-auth` + `--host 0.0.0.0`). The `--no-auth` flag's existence makes the choice explicit at every invocation — there's no "did I configure this securely?" ambiguity. (b) creates the foot-gun where users misconfigure their setup and don't notice the loopback restriction got bypassed silently. (c) is rigid for local development (every dev change requires the full key generation flow). (d) creates magic implicit behavior that users have to remember.

**RATIFICATION STATUS:** ✅ RATIFIED 2026-05-08 by Jake. HTTP transport requires authentication by default. `curator-mcp --http` without `--no-auth` requires at least one configured key in `~/.curator/mcp/api-keys.json` and rejects unauthenticated requests with 401. `--no-auth` opts out and still applies the v1.2.0 loopback-only restriction (`--host 127.0.0.1` / `localhost` / `::1` only). Combination `--no-auth --host 0.0.0.0` exits 2 with a clear refusal message.

---

## 4. Architecture

### 4.1 New files

```
src/curator/mcp/
├── auth.py        # NEW. Key generation, validation, file I/O.
├── server.py      # MODIFIED. Add --no-auth flag, wire auth middleware.
└── tools.py       # UNCHANGED. Tools are auth-agnostic.

src/curator/cli/
├── main.py        # MODIFIED. Add `mcp keys` subcommand group.
└── mcp_keys.py    # NEW. CLI commands: generate/list/revoke/show.

tests/unit/
├── test_mcp_auth.py     # NEW. Key generation, validation, format checks.
├── test_mcp_keys_cli.py # NEW. CLI command tests.

tests/integration/
└── test_mcp_http_auth.py # NEW. End-to-end HTTP auth via real FastMCP server.
```

### 4.2 Key file format

`~/.curator/mcp/api-keys.json`:

```json
{
  "version": 1,
  "keys": [
    {
      "name": "claude-desktop-home",
      "key_hash": "0a1b2c3d...",
      "created_at": "2026-05-08T12:34:56Z",
      "last_used_at": "2026-05-08T13:45:22Z",
      "description": "Home laptop Claude Desktop"
    },
    {
      "name": "scripts-prod",
      "key_hash": "9f8e7d6c...",
      "created_at": "2026-05-09T09:00:00Z",
      "last_used_at": null,
      "description": null
    }
  ]
}
```

**`key_hash`, not `key`.** The full key is shown to the user once (at generation time) and then forgotten by Curator. The file stores `sha256(key)` so a leaked file doesn't grant access. This matches the "don't store passwords; store hashes" pattern. The user's actual key lives in their integration's config (Claude Desktop's MCP config, a script's env var, a notebook's secret manager).

### 4.3 Auth flow

```
HTTP request arrives
   ↓
FastMCP middleware extracts Authorization header
   ↓
Header missing or malformed?  → 401 + audit emit (mcp.auth_failure, reason=missing)
   ↓ (header present)
Extract token, compute sha256
   ↓
Match against ~/.curator/mcp/api-keys.json hashes?
   ↓
No match  → 401 + audit emit (mcp.auth_failure, reason=invalid, key_prefix=first 6 chars)
   ↓
Match found → update last_used_at + audit emit (mcp.auth_success, key_name=…) [throttled]
   ↓
Forward request to FastMCP tools
```

Implementation: a FastMCP middleware function (FastMCP supports per-server middleware via the `middleware=` parameter). The middleware checks the header BEFORE the request reaches any tool handler.

### 4.4 CLI commands

```
curator mcp keys generate <name> [--description <text>]
   - Generates a new key with format `curm_<40-char-random>`.
   - Adds entry to api-keys.json with key_hash + created_at.
   - Prints the FULL key to stdout (only chance to copy it).
   - Returns exit 0 on success.

curator mcp keys list
   - Reads api-keys.json.
   - Prints table: name | created_at | last_used_at | description.
   - Does NOT print key_hash or any secret material.

curator mcp keys revoke <name>
   - Removes the entry matching <name> from api-keys.json.
   - Confirms with the user unless --yes is passed.
   - Returns exit 0 on success, 1 if name not found.

curator mcp keys show <name>
   - Prints metadata for one key (name, created_at, last_used_at, description).
   - Does NOT print key_hash or any secret material.
   - Returns exit 0 on success, 1 if name not found.
```

### 4.5 Server-side changes

```python
# server.py modifications

parser.add_argument(
    "--no-auth",
    action="store_true",
    help=(
        "Disable authentication for HTTP transport. ONLY valid with "
        "--host 127.0.0.1 / localhost / ::1. Use only for local development."
    ),
)

# After args parsing:
if args.http and not args.no_auth:
    keys_file = Path.home() / ".curator" / "mcp" / "api-keys.json"
    if not keys_file.exists() or _no_keys_in(keys_file):
        logger.error(
            "HTTP transport requires authentication. Generate a key with "
            "'curator mcp keys generate <name>' first, or pass --no-auth "
            "for unauthenticated local-loopback development."
        )
        return 2
    middleware = build_auth_middleware(keys_file, runtime.audit_repo)
    server.add_middleware(middleware)
```

### 4.6 Audit event schemas

```python
# Successful auth (throttled to 1/key/minute):
audit_repo.log(
    actor="curator-mcp",
    action="mcp.auth_success",
    details={
        "key_name": "claude-desktop-home",
        "remote_addr": "192.168.1.42",  # if available; loopback omitted
        "request_method": "POST",
        "request_path": "/messages",
    }
)

# Failed auth (always logged):
audit_repo.log(
    actor="curator-mcp",
    action="mcp.auth_failure",
    details={
        "reason": "invalid_key" | "missing_header" | "malformed_header",
        "key_prefix": "curm_a1B...",  # first 10 chars only, never full key
        "remote_addr": "192.168.1.42",
        "request_method": "POST",
        "request_path": "/messages",
    }
)
```

---

## 5. Testing strategy

### 5.1 Unit tests (`tests/unit/test_mcp_auth.py`)

- Key generation produces the right prefix + length.
- Key generation produces enough entropy (>10 bits per char).
- Key hash computation is consistent (same key → same hash).
- File I/O round-trip preserves all fields.
- `0600` permissions set on Unix (skipped on Windows).
- Validation accepts a key whose hash matches; rejects a key whose hash doesn't.
- `last_used_at` update is atomic (write-temp-then-rename).

### 5.2 CLI tests (`tests/unit/test_mcp_keys_cli.py`)

- `curator mcp keys generate <name>` creates the file if absent.
- Generate prints the full key once, never again.
- Duplicate name on generate: exits 1 with clear error.
- List shows registered keys without secrets.
- Revoke removes the entry; confirms again with same name not found.
- Revoke with `--yes` skips confirmation.
- Show prints metadata without secrets.

### 5.3 Integration tests (`tests/integration/test_mcp_http_auth.py`)

- HTTP request with valid Bearer key returns the tool result.
- HTTP request with invalid Bearer key returns 401.
- HTTP request without Authorization header returns 401.
- HTTP request with malformed Authorization header returns 401.
- Auth events land in audit log under `actor='curator-mcp'`.
- Successful-auth audit emission throttling works (no more than 1 per key per minute).
- Failed-auth audit emission is NOT throttled.
- `--no-auth --host 0.0.0.0` exits 2 with clear refusal.
- `--no-auth --host 127.0.0.1` works without auth (loopback dev mode preserved).

---

## 6. Implementation plan

Three sessions, ~3.5h total:

### P1 — Curator v1.5.0a1 (~90 min)

* Create `src/curator/mcp/auth.py` with key generation, validation, file I/O.
* Add `[project.optional-dependencies]` entry if needed (only if we want a separate library for HMAC-style verification; probably not, stdlib is enough).
* Unit tests for `auth.py` (the 7 in §5.1).
* No CLI yet; no server changes yet. The auth module is testable in isolation.

### P2 — Curator v1.5.0a2 (~75 min)

* Create `src/curator/cli/mcp_keys.py` with `generate` / `list` / `revoke` / `show` subcommands.
* Wire into `src/curator/cli/main.py` under a new `curator mcp keys` group.
* CLI tests (§5.2).
* Verify the keys file is created with correct permissions on a real run.

### P3 — Curator v1.5.0 (~75 min)

* Modify `src/curator/mcp/server.py`: add `--no-auth` flag, build auth middleware, wire into FastMCP.
* Modify the loopback-only refusal: now lifts when auth is configured.
* Audit emission via `audit_repo.log()` directly (not through the pluggy hook — this is core Curator code, not a plugin).
* Integration tests (§5.3).
* CHANGELOG entry under `## [1.5.0]` ### Added.
* Update `docs/CURATOR_MCP_SERVER_DESIGN.md` to v0.3 noting auth was added in v1.5.0.
* Bump version 1.4.1 → 1.5.0.
* Commit + tag `v1.5.0` + push.

---

## 7. Compatibility

### 7.1 What breaks (deliberately)

- `curator-mcp --http --host 0.0.0.0` previously exited 2 with "no auth available." Now it requires a configured key, which is a soft break — the command still exits 2 if no keys exist, but now offers a path forward (`generate a key first`).

### 7.2 What doesn't break

- `curator-mcp` (default stdio) — unchanged.
- `curator-mcp --http --host 127.0.0.1 --no-auth` — unchanged (loopback-only dev mode preserved).
- All existing v1.4.x test suites — pass without modification.

---

## 8. Document log

* **2026-05-08 v0.1 — DRAFT.** Initial design authored after reading `src/curator/mcp/server.py` v1.2.0 + the v1.2.0 design's §3 DM-5 ratification of "no auth in v1.2.0; defer to a future minor release." Six DMs raised for ratification (DM-1 through DM-6). Recommended decisions are conservative: standard Bearer auth, JSON file storage, `curm_` prefix, multi-key, audit-everything-with-throttling, secure-by-default. Implementation deferred until ratification.
* **2026-05-08 v0.2 — RATIFIED.** Jake replied "1" affirming all six DMs as recommended. P1 implementation cleared to start. No design changes between v0.1 and v0.2 — the recommended options are now the binding decisions.

---

*All six DMs ratified 2026-05-08. P1 implementation cleared.*
