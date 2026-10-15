# -*- coding: utf-8 -*-
"""
start_all.py — one-click запуск всего стека Logicc farm:
  1. dashboard :8560 (control plane + Web UI)
  2. gateway :18789 (OpenAI-compat LLM endpoint, если node доступен и logicc2api рядом)
  3. проверка captcha-sidecar :8877 (внешний процесс, только ping)

Работает у всех: пути из config.json / env (LG_HOME, LG_GW_DIR, LG_DASH_PORT).
Запуск:  python start_all.py          (Windows: pythonw start_all.py без консоли)
Стоп:    python start_all.py --stop
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DETACHED = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def cfg():
    try:
        return json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    except Exception:
        return {}


def ping(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception as e:
        return getattr(e, "code", 0) is not None and getattr(e, "code", 0) < 500


def py():
    return sys.executable


def start_dashboard():
    port = os.environ.get("LG_DASH_PORT", "8560")
    if ping("http://127.0.0.1:%s/api/status" % port):
        print("[dashboard] already UP :%s" % port)
        return
    log = open(os.path.join(HERE, "dash.log"), "ab")
    subprocess.Popen([py(), os.path.join(HERE, "dashboard.py")],
                     stdout=log, stderr=log, cwd=HERE, creationflags=DETACHED,
                     env={**os.environ, "PYTHONPATH": ""})
    for _ in range(15):
        time.sleep(1)
        if ping("http://127.0.0.1:%s/api/status" % port):
            print("[dashboard] UP http://127.0.0.1:%s" % port)
            return
    print("[dashboard] FAILED — see dash.log")


def start_gateway():
    gw_dir = os.environ.get("LG_GW_DIR", "")
    if not gw_dir:
        cand = os.path.join(os.path.dirname(HERE), "logicc2api")
        gw_dir = cand if os.path.isdir(cand) else ""
    if not gw_dir:
        print("[gateway] logicc2api dir not found — skip (set LG_GW_DIR)")
        return
    if ping("http://127.0.0.1:18789/health"):
        print("[gateway] already UP :18789")
        return
    try:
        log = open(os.path.join(gw_dir, "gw.log"), "ab")
        subprocess.Popen(["node", "server.mjs", "18789"], stdout=log, stderr=log,
                         cwd=gw_dir, creationflags=DETACHED)
        for _ in range(10):
            time.sleep(1)
            if ping("http://127.0.0.1:18789/health"):
                print("[gateway] UP http://127.0.0.1:18789/v1 (OpenAI-compat)")
                return
        print("[gateway] FAILED — see gw.log")
    except FileNotFoundError:
        print("[gateway] node not installed — skip")


def check_sidecar():
    ok = ping("http://127.0.0.1:8877/health")
    print("[sidecar] free captcha :8877 %s" % ("UP" if ok else "DOWN (2captcha fallback only)"))


def stop_all():
    import ctypes
    killed = []
    if os.name == "nt":
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%' or Name='node.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60).stdout
        try:
            rows = json.loads(out or "[]")
            if isinstance(rows, dict):
                rows = [rows]
        except Exception:
            rows = []
        for p in rows:
            cl = (p.get("CommandLine") or "")
            if any(k in cl for k in ("dashboard.py", "server.mjs", "logicc_reg.py")) and "start_all" not in cl:
                try:
                    ctypes.windll.kernel32.TerminateProcess(
                        ctypes.windll.kernel32.OpenProcess(1, False, p["ProcessId"]), 1)
                    killed.append(p["ProcessId"])
                except Exception:
                    pass
    print("stopped pids:", killed or "none")


if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop_all()
    else:
        start_dashboard()
        start_gateway()
        check_sidecar()
        print("\nUI: http://127.0.0.1:8560 | LLM: http://127.0.0.1:18789/v1 | MCP: python mcp_server.py")
