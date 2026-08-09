# Agent token streaming and MCP adapter

## Token streaming

`POST /api/assistants/{assistant_id}/agent-chat/stream` returns SSE events:

- `conversation`: the server-owned conversation id
- `agent`: lifecycle payloads such as `turn_started`, `token`, `tool_call`, and `tool_result`
- `final`: the same answer/citation/chart shape as the non-streaming endpoint
- `error` and `done`

The provider adapter aggregates streamed `content` and native `tool_calls` deltas
before the next Agent turn. Token events are delivery-only; the persisted event
log still stores complete assistant/tool messages.

## MCP configuration

Configure `MCP_SERVERS_JSON` or the `mcp.servers` database setting with a JSON
array. Example:

```json
[
  {
    "name": "my-readonly-server",
    "transport": "stdio",
    "command": ["npx", "-y", "my-mcp-server"],
    "allow_tools": ["search"],
    "readonly_only": true,
    "timeout_seconds": 20
  },
  {
    "name": "remote-readonly-server",
    "transport": "streamable_http",
    "url": "http://127.0.0.1:9000/mcp",
    "headers": {},
    "allow_tools": ["query"],
    "readonly_only": true
  }
]
```

The host performs `initialize`, `tools/list`, and `tools/call`, prefixes model
tool names with `mcp_{server}_`, and exposes only tools marked read-only or
explicitly allow-listed. Tool calls remain inside the Agent budget and inherit
the same timeout/error handling as built-in tools.
