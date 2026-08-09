from __future__ import annotations

import sys

from app import mcp_client
from app.tool_registry import ToolContext


def test_mcp_config_filters_readonly_tools_and_wraps_results(monkeypatch) -> None:
    monkeypatch.setenv(
        "MCP_SERVERS_JSON",
        '[{"name":"demo","transport":"streamable_http","url":"http://demo",'
        '"allow_tools":["search"],"readonly_only":true}]',
    )

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def list_tools(self):
            return [
                {
                    "name": "search",
                    "description": "search records",
                    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "delete",
                    "description": "delete records",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": False},
                },
            ]

        def call_tool(self, name, arguments):
            assert name == "search"
            assert arguments == {"q": "x"}
            return {"content": [{"type": "text", "text": "found"}]}

    monkeypatch.setattr(mcp_client, "MCPClient", FakeClient)
    tools = mcp_client.build_mcp_tools(ToolContext(
        assistant_id="a1",
        file_ids=[],
        retrieval_config={},
        model="m1",
    ))

    assert [tool.name for tool in tools] == ["mcp_demo_search"]
    assert tools[0].execute({"q": "x"})["summary"] == "found"


def test_mcp_allowlist_can_expose_explicit_non_readonly_tool(monkeypatch) -> None:
    monkeypatch.setenv(
        "MCP_SERVERS_JSON",
        '[{"name":"demo","transport":"stdio","command":["demo"],'
        '"allow_tools":["write"],"readonly_only":true}]',
    )

    class FakeClient:
        def __init__(self, _config):
            pass

        def list_tools(self):
            return [{"name": "write", "inputSchema": {"type": "object"}}]

        def call_tool(self, _name, _arguments):
            return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(mcp_client, "MCPClient", FakeClient)
    tools = mcp_client.build_mcp_tools(ToolContext(
        assistant_id="a1", file_ids=[], retrieval_config={}, model="m1"
    ))
    assert [tool.name for tool in tools] == ["mcp_demo_write"]


def test_mcp_stdio_transport_performs_initialize_list_and_call() -> None:
    server_code = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " if r.get('id') == 1: out={'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}}\n"
        " elif r.get('method') == 'tools/list': out={'jsonrpc':'2.0','id':2,'result':{'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}}\n"
        " elif r.get('method') == 'tools/call': out={'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':'ok'}]}}\n"
        " else: continue\n"
        " print(json.dumps(out),flush=True)\n"
    )
    config = mcp_client.MCPServerConfig(
        name="demo",
        transport="stdio",
        command=(sys.executable, "-u", "-c", server_code),
    )
    client = mcp_client.MCPClient(config)
    assert client.list_tools()[0]["name"] == "echo"
    assert client.call_tool("echo", {})["content"][0]["text"] == "ok"
