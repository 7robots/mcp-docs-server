---
name: docs-router
description: "How to look up programming documentation through mcp-docs-server, a unified front-end for multiple backend documentation MCP servers (FastMCP, Google Developer, Cloudflare, AWS Knowledge, Microsoft Learn). Use this when you need up-to-date, version-specific docs for a library, cloud service, or framework."
---

# docs-router — unified documentation lookup

## What this server is

`mcp-docs-server` is a thin front-end that proxies several documentation MCP
servers behind a single endpoint. Instead of wiring every docs MCP into every
client, you add this one server and get them all.

Tools from each backend are automatically namespaced by the backend id.
Today's backends:

| Backend id | Covers |
|---|---|
| `fastmcp` | FastMCP framework (Python, MCP protocol) |
| `google` | Google developer docs (GCP, Android, Firebase, …) |
| `cloudflare` | Cloudflare (Workers, D1, R2, Pages, …) |
| `aws` | AWS services |
| `mslearn` | Microsoft Learn (Azure, .NET, TypeScript, …) |

A tool named `search_documentation` on the Cloudflare backend becomes
`cloudflare_search_documentation` here.

## How to use it

This server exposes **Code Mode** discovery tools, not raw backend tools. That
keeps the tool catalog in your context small even as more backends get added.

The three meta-tools are:

1. **`search(query, tags=None)`** — keyword-search the combined catalog. Returns
   tool names + brief descriptions. Start here.
2. **`get_schema(tools=[...])`** — fetch JSON schemas for the tools you picked.
   Call this once, for only the tools you will actually use.
3. **`execute(code)`** — run a short async Python block where `call_tool(name, params)`
   is in scope. This is where real work happens.

### The golden path

```text
1. search("<topic>")                     → pick 1–3 tool names
2. get_schema(tools=[...chosen...])      → read required params
3. execute(code="...")                   → do the multi-step lookup in one block,
                                            return just the final answer
```

### Example: "Explain Cloudflare Durable Objects alarms"

```python
# step 3 — code passed to execute()
# call_tool is pre-injected in scope
async def main():
    hits = await call_tool(
        "cloudflare_search_documentation",
        {"query": "Durable Objects alarms"},
    )
    # Most docs MCPs return a list of candidate doc refs. Pull the top one.
    first = hits[0] if isinstance(hits, list) else hits
    page = await call_tool(
        "cloudflare_get_documentation",
        {"id": first["id"]},
    )
    return page
```

Return the *final* page content — intermediate search hits don't need to flow
back through the LLM's context.

### Example: cross-backend check

If the user asks about a library without saying which cloud, search broadly and
let the top result decide:

```python
async def main():
    hits = await call_tool("search", {"query": "vector database indexing"})
    # search() is the meta-tool; call it via call_tool like any other
    # ...
```

## Guidelines

- **Prefer one `execute()` per user question.** Chain multiple `call_tool`
  invocations inside it. Each round-trip to the LLM costs tokens and latency;
  the sandbox exists so you don't pay them.
- **Don't assume a backend's tool schema** — call `get_schema` once per new
  tool. Backends add/remove tools independently.
- **Filter by tag when obvious.** `search(query, tags=["aws"])` avoids pulling
  in unrelated backends when the user's question is clearly AWS-flavored.
- **Fail soft.** If one backend 500s or times out, the others still work.
  Report which one failed and continue.
- **Return answers, not tool transcripts.** The LLM orchestrating this server
  should summarize the docs for the user, including a link/reference when the
  backend provides one.

## When *not* to use this server

- The user is asking about source code in their repo — that's a code-search
  task, not a docs-lookup task.
- The user is asking about a product that none of the backends covers
  (e.g. Stripe API). Say so rather than forcing a doc search through an
  unrelated backend.
- The user needs interactive exploration (live examples, playgrounds). This
  server returns static documentation content.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `search` returns zero results | Query too specific, or topic is covered by a backend that isn't enabled. Try broader keywords. |
| `call_tool` raises a timeout | Backend is slow; retry once. If it persists, mention it in the answer. |
| Tool name doesn't exist | Backends change. Re-run `search` — don't memorize tool names across sessions. |
| 401/403 on a specific backend | The server's credential for that backend is missing on fastmcp.cloud. Report the backend id; an admin can fix. |
