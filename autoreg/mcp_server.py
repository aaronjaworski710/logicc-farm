# -*- coding: utf-8 -*-
"""
mcp_server.py — MCP-сервер (stdio, JSON-RPC, newline-delimited) для Logicc farm.
Работает на stdlib, проксирует в dashboard control-plane (:8560).

Запуск:  python mcp_server.py
Регистрация (Hermes config.yaml):
  mcp_servers:
    logicc:
      command: C:/Users/User/AppData/Local/Programs/Python/Python311/python.exe
      args: ["C:/Users/User/tmp/logicc_autoreg/mcp_server.py"]
Claude Desktop (claude_desktop_config.json):
  "mcpServers": {"logicc": {"command": "python", "args": ["<path>/mcp_server.py"]}}

Тулзы: logicc_status, pc_stats, list_services, bank_list, wave_start, wave_stop,
       gateway_import, chat
"""
import json
import os
import sys
import urllib.request
import urllib.error

DASH = os.environ.get("LG_DASH", "http://127.0.0.1:8560")

TOOLS = [
    {"name": "logicc_status", "description": "Logicc farm status: bank counters, gateway models, mail pool, disk, PC load, services health", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "pc_stats", "description": "PC metrics: CPU %, RAM used/total GB, disk free GB", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_services", "description": "Stack services health: captcha sidecar :8877, LLM gateway :18789, dashboard :8560, reg wave", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "bank_list", "description": "Account bank rows: email(masked-able), status, activated, org, session", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "max rows, default 50"}}}},
    {"name": "wave_start", "description": "Start autoreg wave in background (free captcha + mail chain + auto activation + auto gateway import)", "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "description": "accounts to register, default 5"}}, "required": []}},
    {"name": "wave_stop", "description": "Stop running autoreg wave", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "gateway_import", "description": "Import all activated bank accounts into OpenAI-compatible gateway (per-account, tolerates duplicates)", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "chat", "description": "Send a chat completion through the Logicc gateway (models: gpt-6-luna, gpt-6.1-sol, claude-5.5-sonnet, gemini-3.8-flash, ...)", "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["model", "prompt"]}},
]


def dash(path, method="GET", body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(DASH + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return 0, {"error": repr(e)[:200]}


def call(name, args):
    if name == "logicc_status":
        c, s = dash("/api/status")
        if c != 200:
            return {"dashboard": "DOWN (%s)" % c, "detail": s}
        return {"bank": s.get("bank"), "gateway_up": (s.get("gateway") or {}).get("up"),
                "models": (s.get("gateway") or {}).get("models"),
                "mail_pool_left": s.get("pool_left"), "disk_free_gb": s.get("disk_free_gb"),
                "pc": s.get("pc"), "services": s.get("services"),
                "wave": s.get("wave"), "captcha": s.get("captcha")}
    if name == "pc_stats":
        c, s = dash("/api/status")
        return {"pc": s.get("pc"), "disk_free_gb": s.get("disk_free_gb")} if c == 200 else s
    if name == "list_services":
        c, s = dash("/api/status")
        return s.get("services") if c == 200 else s
    if name == "bank_list":
        c, b = dash("/api/bank")
        if c == 200 and isinstance(b, list):
            lim = int(args.get("limit", 50))
            return {"count": len(b), "rows": b[-lim:]}
        return b
    if name == "wave_start":
        c, r = dash("/api/wave/start", "POST", {"n": int(args.get("n", 5))}, timeout=30)
        return r
    if name == "wave_stop":
        c, r = dash("/api/wave/stop", "POST", {}, timeout=30)
        return r
    if name == "gateway_import":
        c, r = dash("/api/gateway/import", "POST", {}, timeout=300)
        return r
    if name == "chat":
        c, r = dash("/api/chat/test", "POST",
                    {"model": args.get("model", "gpt-6-luna"),
                     "prompt": args.get("prompt", "say FARM-OK")}, timeout=200)
        return r
    return {"error": "unknown tool: %s" % name}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        method = req.get("method", "")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "logicc-farm", "version": "1.1.0"}}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            p = req.get("params") or {}
            name = p.get("name", "")
            args = p.get("arguments") or {}
            try:
                out = call(name, args)
            except Exception as e:
                out = {"error": repr(e)[:300]}
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                "isError": bool(isinstance(out, dict) and out.get("error"))}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": "method not found: %s" % method}})


if __name__ == "__main__":
    main()
