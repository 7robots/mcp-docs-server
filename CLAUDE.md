# CLAUDE.md

Development notes for contributors to `mcp-docs-server`. Companion to
[README.md](README.md) (user-facing overview) and
[skills/docs-router/SKILL.md](skills/docs-router/SKILL.md) (LLM-facing usage
guide). Read this first when making changes.

## What this server is

A FastMCP 3.1.x server that proxies several documentation MCP backends
(FastMCP, Google Developer, Cloudflare, AWS Knowledge, Microsoft Learn)
behind a single endpoint, exposed to LLMs through Code Mode. The LLM sees
four meta-tools — `list_sources`, `search`, `get_schema`, `execute` —
regardless of how many doc tools the backends publish. Runs on fastmcp.cloud
with Okta OIDC.

## Architecture in 30 seconds

```
HTTP request
  ↓ MultiAuth (OIDCProxy + IntrospectionTokenVerifier)   ← auth is opt-in
  ↓ CodeMode transform  (collapses catalog to 4 meta-tools)
  ↓ ProxyProvider       (fans out to backends from backends.yaml)
  ↓ SkillProvider       (exposes SKILL.md as skill://docs-router/SKILL.md)
```

Two non-obvious shape choices:

1. **`create_proxy(…)` returns a `FastMCPProxy`** — a FastMCP subclass.
   Kwargs like `auth`, `transforms`, `instructions` pass through to the
   underlying FastMCP constructor.
2. **`SkillProvider` exposes SKILL.md as both a marketplace-plugin skill
   *and* a plain MCP resource**. That's how non-Claude clients (VSCode,
   Copilot, Codex) can read the usage guide.

## Deployment model

- fastmcp.cloud auto-deploys from `main` on push. There's no Dockerfile,
  no Modal entrypoint — `fastmcp.json` is the deploy manifest.
- Auth is **inlined** in `server.py::_create_auth()` per the
  `mcp-deploy-utils` skill. Do *not* add `mcp-deploy-utils` as a pip
  dependency; fastmcp.cloud installs from `pyproject.toml` and that
  package isn't on PyPI.
- All env vars fastmcp.cloud should pass through must be declared in
  `fastmcp.json` *and* set in the fastmcp.cloud dashboard. Missing → not
  forwarded.
- Okta app is shared with `moon-d1-mcp-server` (same tenant, same OIDC
  app). Adding a new redirect URI there is a manual Okta admin step.

## Non-obvious gotchas

### `MCP_BASE_URL` must be the bare host

On fastmcp.cloud, set `MCP_BASE_URL=https://<subdomain>.fastmcp.app` — do
*not* include `/mcp`. `mcp.http_app(transport="streamable-http")` mounts
the MCP endpoint at `/mcp` internally; if `MCP_BASE_URL` already contains
`/mcp`, the `resource_metadata` URL doubles to
`/.well-known/oauth-protected-resource/mcp/mcp` and MCP clients 404 on
discovery.

Verify after deploy:

```bash
curl -i -X POST https://<host>/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  | grep resource_metadata
```

Expect a single `/mcp` segment in the URL. Two segments = misconfig.

### The Okta redirect URI is `/auth/callback` (no `/mcp` prefix)

Since `MCP_BASE_URL` is the bare host, OIDCProxy's callback path
`/auth/callback` resolves at the root — not under `/mcp`. In Okta, whitelist
`https://<subdomain>.fastmcp.app/auth/callback`.

### Claude MCP clients cache `tools/list`

When you add/remove/rename a tool and redeploy, existing Claude Desktop /
Claude Code sessions keep the old catalog. Tell the user to **disconnect
and reconnect the MCP server in their client** before assuming a server-side
bug. This bit us once already; verify reconnect before debugging wiring.

### Claude Desktop re-prompts for auth on first tool call (FastMCPProxy-specific)

On first tool use after OAuth-at-connect, Claude Desktop shows a
"Claude wants to use `<tool>` from Documentation Server / Request {} /
**Authentication required to use this tool**" dialog. User must click
through once; subsequent tool calls in the same session are silent.
This does *not* happen on `moon-d1-mcp-server`, which is a plain
`FastMCP` with the same Okta wiring. The trigger is specific to
`FastMCPProxy` / `ProxyProvider`.

The prompt is client-side and pre-emptive — fastmcp.cloud logs show no
incoming request at the moment it appears. Disconnect + quit + reopen +
reconnect does *not* eliminate it.

**Ruled out** (don't re-run these tests):

- CodeMode transform — disabled on a diagnostic branch, prompt still fired
  on direct `cloudflare_*` tool calls.
- fastmcp version — symmetric test: bumped moon-d1 to 3.2.4 (still silent)
  and downgraded docs-server to 3.1.1 (still prompted). Version is not the
  cause in either direction.
- Tool metadata — `uv run fastmcp inspect --format mcp` output is
  functionally identical between the two servers (`annotations: null`,
  `_meta: { fastmcp: { tags: [] } }` throughout).
- Claude Desktop per-tool consent UX — "Always allow" does not suppress
  the prompt, so it isn't standard permission consent.

**Upstream:** filed at <!-- TODO: paste issue URL --> in the FastMCP
repo. Track that thread before attempting more debugging here.

**Related, separately fixed:** the per-backend OAuth dance (AWS
Knowledge / Cloudflare Docs each demanding their own browser flow) was
caused by `ProxyClient` setting `transport.forward_incoming_headers =
True`. Fixed in `mcp_docs/proxy.py::NoForwardMCPConfigTransport`. That
is a different symptom than this one, now cleanly resolved — don't
conflate.

### `@mcp.tool` tools get hidden behind Code Mode

`CodeMode.transform_tools` collapses the entire tool catalog into
`[…discovery_tools…, execute]`. A regular `@mcp.tool` function is still
callable (via `call_tool` from inside `execute()`), but it **does not
appear in the top-level tool list** — only through `search()`. If you want
a tool visible to the LLM alongside `search` / `get_schema` / `execute`, it
must be a **Code Mode discovery tool** — a `DiscoveryToolFactory`, not a
regular tool. See `mcp_docs/discovery.py::ListSources` for the pattern.

## Adding a backend

1. Append an entry to [backends.yaml](backends.yaml) with `id`, `name`,
   `url`, `transport`, `auth`, `tags`, `enabled: true`.
2. If `auth: {type: bearer, token_env: MY_TOKEN}`:
   - Declare `MY_TOKEN` in [fastmcp.json](fastmcp.json) under
     `deployment.env` (value `"${MY_TOKEN}"`).
   - Set the token in the fastmcp.cloud dashboard.
3. Add `MY_TOKEN` to [.env.example](.env.example) as a commented hint.
4. Push. After redeploy, call `list_sources()` to confirm the new
   backend shows `"loaded": true` (reconnect first — see gotcha above).

Tool-name collisions between backends are handled automatically: FastMCP
prefixes every proxied tool with `{backend_id}_`.

## Adding a Code Mode discovery tool

Use this pattern (not `@mcp.tool`) when you want a tool visible at the top
level. See `mcp_docs/discovery.py` for a complete example:

```python
class MyDiscoveryTool:
    def __init__(self, *, name: str = "my_tool"): …
    def __call__(self, get_catalog: GetToolCatalog) -> Tool:
        async def my_tool(...): …
        return Tool.from_function(fn=my_tool, name=self._name)
```

Register in `server.py`:

```python
CodeMode(discovery_tools=[
    Search(),
    GetSchemas(),
    ListSources(BACKENDS_PATH),
    MyDiscoveryTool(),   # new
])
```

Add a test class `TestMyDiscoveryTool` in a dedicated `tests/test_*.py` and
at least one assertion in `tests/test_server.py::TestCodeMode` that
confirms the factory is registered.

Keep discovery tools **cheap**. They're visible to every LLM on every
session and weigh against the context budget that Code Mode is supposed to
save. Static metadata (like `list_sources`) is fine; anything that triggers
network calls to backends belongs behind `execute()`.

## Testing conventions

Follows the [`mcp-marketplace`](../mcp-marketplace/tests/) standard:

- One file per subject: `test_backends`, `test_server`, `test_auth`,
  `test_discovery`, `test_manifest`, `test_skill`.
- Module docstring at the top: `"""Tests for X."""`
- Tests organized under `class TestSubject:` with `# --- section ---`
  comments between classes.
- Shared helpers live in [tests/conftest.py](tests/conftest.py):
  `repo_root`, `clean_okta_env`, `clean_backend_env`, `write_backends`,
  `backend_entry(...)` (the factory mirror of marketplace's `_server(...)`).
- Descriptive method names (`test_explicit_issuer_overrides_default`, not
  `test_2`).
- Run: `uv sync --extra dev && uv run pytest`. Target <1s locally.

Import-time side effects: `server.py` calls `_create_auth()` at import
time, which dials Okta if `OKTA_CLIENT_SECRET` is set. Tests that exercise
the auth-enabled path must import with Okta env **unset**, then set env
vars and call `_create_auth()` manually under patched constructors —
otherwise the import blows up trying to reach the real Okta discovery URL.
See [tests/test_auth.py::TestAuthEnabled](tests/test_auth.py) for the
pattern.

## Commit conventions

- Imperative mood, lowercase first word (`add list_sources…`, not
  `Added list_sources…`).
- Body explains *why*, not *what* — the diff shows what.
- Agent-assisted commits get a `Co-Authored-By: Claude Opus 4.7…` trailer;
  no other "generated by" attribution in message bodies.
- Don't commit without being asked. Don't push without being asked.

## External references

- [`$HOME/GitHub/mcp-deploy-utils`](../mcp-deploy-utils/) — canonical Okta
  auth inlining pattern. When FastMCP's auth API changes, update
  `server.py::_create_auth()` from the skill there.
- [`$HOME/GitHub/moon-d1-mcp-server`](../moon-d1-mcp-server/) — reference
  FastMCP 3.1.x deployment (fastmcp.json shape, `__main__` block,
  `SkillProvider` usage).
- [`$HOME/GitHub/mcp-marketplace`](../mcp-marketplace/) — test-suite
  convention we follow (one file per subject, class-based organization,
  shared `conftest.py` helpers).
- fastmcp.cloud dashboard — deploy logs, env vars, server URL
  (`concrete-silver-mongoose.fastmcp.app`).

## Package management

Use `uv`, not `pip`. Install: `uv sync --extra dev`. Run scripts:
`uv run …`. Lockfile is `uv.lock` (committed). Never edit `pyproject.toml`
and forget to rerun `uv lock`.
