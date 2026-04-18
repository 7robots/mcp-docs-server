"""mcp-docs-server — unified front-end for documentation MCP servers.

Proxies multiple backend documentation MCPs (FastMCP, Google Developer,
Cloudflare, AWS Knowledge, Microsoft Learn, …) behind a single endpoint and
exposes them to LLMs through Code Mode (search / get_schema / execute),
keeping context small while supporting multi-step doc lookups.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastmcp.experimental.transforms.code_mode import CodeMode  # noqa: E402
from fastmcp.server import create_proxy  # noqa: E402
from fastmcp.server.providers.skills import SkillProvider  # noqa: E402

from mcp_docs.backends import build_proxy_config  # noqa: E402

HERE = Path(__file__).parent


def _create_auth():
    """Build Okta OIDC auth with bearer token support, or None if not configured.

    Inlined from the mcp-deploy-utils skill so no external package dependency is
    needed at deploy time. When OKTA_CLIENT_SECRET is unset the server runs
    without auth — handy for local dev and tests.
    """
    okta_client_secret = os.environ.get("OKTA_CLIENT_SECRET")
    if not okta_client_secret:
        return None

    from fastmcp.server.auth import MultiAuth
    from fastmcp.server.auth.oidc_proxy import OIDCProxy
    from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier

    okta_client_id = os.environ.get("OKTA_CLIENT_ID")
    okta_domain = os.environ.get("OKTA_DOMAIN")
    okta_issuer = os.environ.get("OKTA_ISSUER", f"{okta_domain}/oauth2/default")
    base_url = os.environ.get("MCP_BASE_URL")
    jwt_signing_key = os.environ.get("JWT_SIGNING_KEY", "")

    oidc_proxy = OIDCProxy(
        config_url=f"{okta_issuer}/.well-known/openid-configuration",
        client_id=okta_client_id,
        client_secret=okta_client_secret,
        base_url=base_url,
        jwt_signing_key=jwt_signing_key or None,
        enable_cimd=False,
        extra_authorize_params={"scope": "openid profile email"},
        allowed_client_redirect_uris=[
            "http://localhost:*",
            "http://127.0.0.1:*",
            "https://claude.ai/*",
        ],
    )

    introspection_verifier = IntrospectionTokenVerifier(
        introspection_url=f"{okta_issuer}/v1/introspect",
        client_id=okta_client_id,
        client_secret=okta_client_secret,
        cache_ttl_seconds=300,
    )

    return MultiAuth(
        server=oidc_proxy,
        verifiers=[introspection_verifier],
    )


INSTRUCTIONS = (
    "Unified front-end for documentation MCP servers. "
    "Backends (FastMCP, Google Developer, Cloudflare, AWS, Microsoft Learn) "
    "are proxied and prefixed by id (e.g. cloudflare_*). Use the Code Mode "
    "discovery tools: search() to find docs tools by keyword, get_schema() "
    "for signatures, then execute a short Python block that calls "
    "`call_tool(name, params)` — chain resolve/fetch calls in one block and "
    "return only the answer. See the skill://docs-router/SKILL.md resource "
    "for examples."
)


# Build the proxy from backends.yaml. This is the FastMCP server we hand to
# fastmcp.cloud; create_proxy() returns a FastMCPProxy (a FastMCP subclass)
# with a ProxyProvider already wired in, and our kwargs (auth, transforms,
# instructions) are forwarded to the FastMCP constructor.
mcp = create_proxy(
    build_proxy_config(HERE / "backends.yaml"),
    name="mcp-docs-server",
    instructions=INSTRUCTIONS,
    auth=_create_auth(),
    transforms=[CodeMode()],
)

# Bundle the usage skill. SkillProvider exposes SKILL.md at
# skill://docs-router/SKILL.md (a standard MCP resource any client can read)
# AND participates in marketplace plugin-bundle downloads for Claude clients.
mcp.add_provider(SkillProvider(HERE / "skills" / "docs-router"))


if __name__ == "__main__":
    import uvicorn

    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    uvicorn.run(app, host="0.0.0.0", port=8000)
