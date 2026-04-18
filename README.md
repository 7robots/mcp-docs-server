# mcp-docs-server

A single FastMCP 3.1.x server that fronts multiple documentation MCP servers
(FastMCP, Google Developer, Cloudflare, AWS Knowledge, Microsoft Learn) and
exposes them to LLMs through **Code Mode** — so the client only sees three
discovery tools (`search`, `get_schema`, `execute`) regardless of how many
doc tools the backends expose.

Auth is Okta OIDC via `MultiAuth` (interactive OAuth + bearer introspection),
inlined from the `mcp-deploy-utils` pattern so there is no external package
dependency at deploy time.

## Why

Documentation lookups are usually multi-step: resolve a library / service,
fetch a page, maybe chase a cross-reference. Code Mode lets the LLM write a
short Python block that does all of that inside a sandbox and returns only the
answer — keeping both the tool catalog and the intermediate results out of the
LLM's context window.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  mcp-docs-server  (FastMCP 3.1.x on fastmcp.cloud)          │
│                                                             │
│    MultiAuth (Okta OIDCProxy + Introspection, optional)     │
│        │                                                    │
│        ▼                                                    │
│    CodeMode transform                                       │
│      exposes: search / get_schema / execute                 │
│        │                                                    │
│        ▼                                                    │
│    ProxyProvider (from backends.yaml)                       │
│      prefixes: fastmcp_*, google_*, cloudflare_*, aws_*,    │
│                mslearn_*                                    │
└────────────┬──────────────┬──────────────┬──────────────────┘
             ▼              ▼              ▼
      FastMCP Docs    Cloudflare Docs  AWS Knowledge   …
```

The `SkillProvider` also exposes `skill://docs-router/SKILL.md` as an MCP
resource so any client — Claude Code, Claude Desktop, Claude Web, Copilot,
VSCode extensions, Codex — can read the usage guide directly.

## Development

```bash
uv sync
uv run pytest
uv run python server.py   # runs on http://127.0.0.1:8000/mcp
```

Auth is opt-in: without `OKTA_CLIENT_SECRET` the server runs unauthenticated,
which is convenient for local development.

## Deployment (fastmcp.cloud)

1. Push this repo to GitHub.
2. Connect it to fastmcp.cloud. The `fastmcp.json` manifest tells fastmcp.cloud
   which env vars to expose.
3. Set Okta env vars in the fastmcp.cloud dashboard (same tenant as
   `moon-d1-mcp`): `OKTA_CLIENT_ID`, `OKTA_CLIENT_SECRET`, `OKTA_DOMAIN`,
   `OKTA_ISSUER`, `MCP_BASE_URL`, `JWT_SIGNING_KEY`.
4. Optional per-backend credentials (only if a backend requires bearer auth):
   `AWS_KNOWLEDGE_BEARER_TOKEN`, `GITHUB_DOCS_BEARER_TOKEN`, …

Backend credentials resolve lazily. A missing env var skips *that* backend and
logs a warning — the server still starts with whatever backends are
configured.

## Adding a backend

Edit `backends.yaml`:

```yaml
- id: mybackend
  name: My Docs
  url: https://example.com/mcp
  transport: http
  auth: { type: bearer, token_env: MYBACKEND_BEARER_TOKEN }
  tags: [custom]
  enabled: true
```

Add `MYBACKEND_BEARER_TOKEN` to `fastmcp.json` and to the fastmcp.cloud
dashboard. Tools from the backend automatically appear prefixed as
`mybackend_*`.

## Auto-discovery from mcp-marketplace

Set `MCP_MARKETPLACE_URL` to an `mcp-marketplace` discovery endpoint
(e.g. `https://<host>/api/discovery/servers`) to automatically pull
shared MCP servers tagged `docs` at startup and merge them with
`backends.yaml`. File entries win on `id` conflict; marketplace
results are cached for the process lifetime (restart to re-fetch).

For a marketplace-discovered server with bearer auth, set
`<ID>_BEARER_TOKEN` (id uppercased, dashes → underscores) in the
environment and in `fastmcp.json`. OAuth-protected marketplace servers
are skipped — mcp-docs-server has no path to acquire those tokens at
runtime.

Leave `MCP_MARKETPLACE_URL` unset to disable marketplace discovery
entirely.

## v2 ideas (not implemented)

- In-process result cache for repeated doc queries (guarded by an env flag).
- Per-backend health dashboard.
