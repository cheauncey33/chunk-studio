"""Small, bounded MCP client for Agent tool discovery and invocation.

Configuration comes from ``MCP_SERVERS_JSON`` or the ``mcp.servers`` setting.
Each configured server is discovered with ``tools/list`` and invoked with
``tools/call`` over either stdio or Streamable HTTP.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from . import db
from .agent_runtime.models import ToolDefinition
from .tool_registry import ToolContext


MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "chunk-studio"
MCP_CLIENT_VERSION = "0.1.0"
_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str
    command: tuple[str, ...] = ()
    url: str = ""
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    allow_tools: tuple[str, ...] = ()
    readonly_only: bool = True
    timeout_seconds: float = 20.0

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "MCPServerConfig":
        name = str(raw.get("name") or "").strip()
        transport = str(raw.get("transport") or "").strip().lower()
        command_value = raw.get("command")
        if isinstance(command_value, str):
            command = (command_value,)
        elif isinstance(command_value, list):
            command = tuple(str(item) for item in command_value if str(item).strip())
        else:
            command = ()
        if not name or transport not in {"stdio", "streamable_http", "http"}:
            raise ValueError("MCP server requires name and stdio/streamable_http transport")
        if transport == "stdio" and not command:
            raise ValueError("stdio MCP server requires a command")
        if transport in {"streamable_http", "http"} and not str(raw.get("url") or "").strip():
            raise ValueError("HTTP MCP server requires a url")
        env = {
            str(key): str(value)
            for key, value in (raw.get("env") or {}).items()
        }
        headers = {
            str(key): str(value)
            for key, value in (raw.get("headers") or {}).items()
        }
        allow_tools = tuple(
            str(item).strip()
            for item in (raw.get("allow_tools") or [])
            if str(item).strip()
        )
        timeout = max(1.0, min(float(raw.get("timeout_seconds") or 20), 120.0))
        return cls(
            name=name,
            transport="streamable_http" if transport == "http" else transport,
            command=command,
            url=str(raw.get("url") or "").strip(),
            env=env,
            headers=headers,
            allow_tools=allow_tools,
            readonly_only=bool(raw.get("readonly_only", True)),
            timeout_seconds=timeout,
        )


def load_server_configs() -> list[MCPServerConfig]:
    raw_text = os.environ.get("MCP_SERVERS_JSON") or db.get_setting("mcp.servers", "[]")
    try:
        raw = json.loads(raw_text or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("MCP_SERVERS_JSON must be valid JSON") from exc
    if not isinstance(raw, list):
        raise ValueError("MCP server configuration must be a JSON array")
    configs: list[MCPServerConfig] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        try:
            configs.append(MCPServerConfig.from_mapping(item))
        except (TypeError, ValueError):
            continue
    return configs


def _meta() -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {
            "name": MCP_CLIENT_NAME,
            "version": MCP_CLIENT_VERSION,
        },
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _request(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"_meta": _meta(), **(params or {})},
    }


def _notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": {"_meta": _meta(), **(params or {})},
    }


def _raise_rpc_error(response: dict[str, Any]) -> None:
    error = response.get("error")
    if isinstance(error, dict):
        raise RuntimeError(
            f"MCP error {error.get('code', 'unknown')}: {error.get('message', 'unknown error')}"
        )


def _response_result(response: dict[str, Any]) -> dict[str, Any]:
    _raise_rpc_error(response)
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("MCP returned an invalid JSON-RPC result")
    return result


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    def _initialize_stdio(self, process: subprocess.Popen[str]) -> None:
        self._write(process, _request(1, "initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": MCP_CLIENT_NAME, "version": MCP_CLIENT_VERSION},
        }))
        self._read_response(process, 1)
        self._write(process, _notification("notifications/initialized"))

    @staticmethod
    def _write(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP stdio stdin is unavailable")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_response(self, process: subprocess.Popen[str], request_id: int) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("MCP stdio stdout is unavailable")
        while True:
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("MCP stdio server closed before replying")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(response, dict) or response.get("id") != request_id:
                continue
            return response

    def _stdio_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        env = os.environ.copy()
        env.update(self.config.env or {})
        process = subprocess.Popen(
            list(self.config.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
            shell=False,
        )
        try:
            self._initialize_stdio(process)
            self._write(process, _request(2, method, params))
            return _response_result(self._read_response(process, 2))
        finally:
            if process.stdin:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    @staticmethod
    def _http_payload(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("MCP HTTP returned a non-object JSON response")
            return payload
        latest: dict[str, Any] | None = None
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                parsed = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                latest = parsed
        if latest is None:
            raise RuntimeError("MCP HTTP returned neither JSON nor an SSE JSON event")
        return latest

    def _http_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            **(self.config.headers or {}),
        }
        with httpx.Client(timeout=self.config.timeout_seconds, headers=headers) as client:
            initialize = client.post(
                self.config.url,
                json=_request(1, "initialize", {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": MCP_CLIENT_NAME, "version": MCP_CLIENT_VERSION},
                }),
            )
            initialize.raise_for_status()
            session_id = initialize.headers.get("Mcp-Session-Id")
            if session_id:
                client.headers["Mcp-Session-Id"] = session_id
            init_result = _response_result(self._http_payload(initialize))
            selected_version = init_result.get("protocolVersion")
            if selected_version:
                client.headers["MCP-Protocol-Version"] = str(selected_version)
            initialized = client.post(
                self.config.url,
                json=_notification("notifications/initialized"),
            )
            initialized.raise_for_status()
            response = client.post(
                self.config.url,
                json=_request(2, method, params),
            )
            response.raise_for_status()
            return _response_result(self._http_payload(response))

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.config.transport == "stdio":
            return self._stdio_request(method, params)
        return self._http_request(method, params)

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            page = result.get("tools") or []
            if isinstance(page, list):
                tools.extend(item for item in page if isinstance(item, dict))
            cursor = str(result.get("nextCursor") or "").strip() or None
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def _tool_allowed(config: MCPServerConfig, raw: dict[str, Any]) -> bool:
    name = str(raw.get("name") or "").strip()
    if not name:
        return False
    if config.allow_tools and name not in set(config.allow_tools):
        return False
    if not config.readonly_only:
        return True
    annotations = raw.get("annotations")
    if isinstance(annotations, dict) and annotations.get("readOnlyHint") is True:
        return True
    return name in set(config.allow_tools)


def _safe_tool_name(server: str, tool: str) -> str:
    return "mcp_" + _NAME_RE.sub("_", server) + "_" + _NAME_RE.sub("_", tool)


def _normalize_result(server: str, tool: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError") is True:
        return {"ok": False, "error": f"MCP tool {server}/{tool} returned an error", "mcp_result": result}
    content = result.get("content") or []
    texts = [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    normalized: dict[str, Any] = {
        "summary": " ".join(texts)[:4000] or f"MCP tool {server}/{tool} completed",
        "mcp_server": server,
        "mcp_tool": tool,
    }
    structured = result.get("structuredContent")
    if structured is not None:
        normalized["structured_content"] = structured
    if texts:
        normalized["content"] = "\n".join(texts)[:8000]
    return normalized


def build_mcp_tools(context: ToolContext) -> list[ToolDefinition]:
    """Discover configured MCP tools and wrap them in the local ToolDefinition."""
    del context
    tools: list[ToolDefinition] = []
    for server in load_server_configs():
        client = MCPClient(server)
        try:
            discovered = client.list_tools()
        except Exception:
            continue
        for raw in sorted(discovered, key=lambda item: str(item.get("name") or "")):
            if not _tool_allowed(server, raw):
                continue
            remote_name = str(raw["name"])
            input_schema = raw.get("inputSchema")
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "additionalProperties": False}

            def execute(
                arguments: dict[str, Any],
                *,
                _client=client,
                _server=server.name,
                _tool=remote_name,
            ) -> dict[str, Any]:
                return _normalize_result(
                    _server,
                    _tool,
                    _client.call_tool(_tool, arguments),
                )

            tools.append(ToolDefinition(
                name=_safe_tool_name(server.name, remote_name),
                description=(
                    f"MCP server {server.name}: "
                    f"{str(raw.get('description') or raw.get('title') or remote_name)[:600]}"
                ),
                input_schema=input_schema,
                execute=execute,
                category="read",
            ))
    return tools
