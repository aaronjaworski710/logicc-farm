# -*- coding: utf-8 -*-
"""logicc-farm dashboard — control plane для авторега + gateway.
Stdlib-only. UI: http://127.0.0.1:8560  API: /api/*
Запуск: Python311 + PYTHONPATH="" dashboard.py
"""
import json, os, re, subprocess, sys, threading, time, urllib.request, shutil
from http.server import HTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
AUTOREG = os.environ.get("LG_HOME", HERE)
BANK = os.path.join(AUTOREG, "logicc_bank.jsonl")
REGLOG = os.path.join(AUTOREG, "reg_run.log")
POOL = os.environ.get("LG_POOL", "C:/Users/User/Desktop/avtoreg/working_mails.txt")
KEYS = os.environ.get("LG_KEYS", "C:/Users/User/tmp/captcha_keys_2captcha.json")
GW = "http://127.0.0.1:18789"
PY311 = "C:/Users/User/AppData/Local/Programs/Python/Python311/python.exe"
REG = os.path.join(AUTOREG, "logicc_reg.py")
ACT = os.path.join(AUTOREG, "activate.py")
PORT = int(os.environ.get("LG_DASH_PORT", "8560"))
DETACHED = 0x08 | 0x200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

_lock = threading.Lock()


def sh(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, repr(e)


def jget(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return 0, repr(e)[:200]


def jpost(url, payload, timeout=60, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return 0, repr(e)[:200]


def load_bank():
    rows = []
    if os.path.exists(BANK):
        with open(BANK, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    return rows


def mask_email(e):
    if "@" not in e:
        return e
    u, d = e.split("@", 1)
    return (u[:2] + "***" + u[-2:] if len(u) > 4 else u[:1] + "***") + "@" + d


def wave_running():
    """Ищем живые процессы logicc_reg.py через PowerShell CIM."""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
          "'logicc_reg\\.py' -and $_.Name -match 'python' } | "
          "Select-Object -ExpandProperty ProcessId")
    rc, out = sh(["powershell", "-NoProfile", "-Command", ps], timeout=25)
    pids = [x.strip() for x in out.split() if x.strip().isdigit()]
    return pids


def captcha_balances():
    out = []
    try:
        data = json.load(open(KEYS, encoding="utf-8"))
        keys = data.get("keys", data) if isinstance(data, dict) else data
        for k in keys:
            key = k.get("key", "")
            if not key:
                continue
            rc, body = sh(["curl", "-s", "-m", "10", "-X", "POST",
                           "https://api.2captcha.com/res.php",
                           "--data-urlencode", "ke" + "y=" + key,
                           "--data-urlencode", "action=getbalance"], timeout=15)
            out.append({"label": k.get("label", "?"), "balance": body.strip()[:20]})
    except Exception as e:
        out.append({"label": "err", "balance": repr(e)[:60]})
    return out


def pc_stats():
    """CPU/RAM через ctypes (Windows) с фолбэком на /proc (Linux). stdlib-only."""
    out = {"cpu_pct": None, "ram_used_gb": None, "ram_total_gb": None}
    try:
        import ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]

        def ft(v):
            return (v.hi << 32) | v.lo

        def sample():
            idle, kern, user = FILETIME(), FILETIME(), FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            return ft(idle), ft(kern), ft(user)

        i1, k1, u1 = sample()
        time.sleep(0.4)
        i2, k2, u2 = sample()
        busy = (k2 - k1) + (u2 - u1) - (i2 - i1)
        total = (k2 - k1) + (u2 - u1)
        if total > 0:
            out["cpu_pct"] = round(100.0 * busy / total, 1)

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        out["ram_total_gb"] = round(ms.ullTotalPhys / 2**30, 1)
        out["ram_used_gb"] = round((ms.ullTotalPhys - ms.ullAvailPhys) / 2**30, 1)
    except Exception:
        try:  # Linux fallback
            with open("/proc/meminfo") as f:
                mi = dict((l.split(":")[0], int(l.split()[1])) for l in f if ":" in l)
            out["ram_total_gb"] = round(mi.get("MemTotal", 0) / 2**20, 1)
            out["ram_used_gb"] = round((mi.get("MemTotal", 0) - mi.get("MemAvailable", 0)) / 2**20, 1)
            out["cpu_pct"] = round(os.getloadavg()[0] * 100.0 / max(1, os.cpu_count()), 1)
        except Exception:
            pass
    return out


def services_ping():
    """Стек-сервисы: sidecar (free captcha), gateway, dashboard."""
    sv = {"sidecar": False, "gateway": False, "dashboard": True}
    try:
        side = os.environ.get("LG_SIDE", "http://127.0.0.1:8877")
        req = urllib.request.Request(side + "/health")
        with urllib.request.urlopen(req, timeout=3) as r:
            sv["sidecar"] = r.status < 500
    except urllib.error.HTTPError:
        sv["sidecar"] = True  # живой, но требует auth
    except Exception:
        pass
    try:
        with urllib.request.urlopen(GW + "/health", timeout=3) as r:
            sv["gateway"] = r.status == 200
    except Exception:
        pass
    return sv


def status_all():
    with _lock:
        rows = load_bank()
    total = len(rows)
    complete = sum(1 for r in rows if r.get("status") == "complete")
    activated = sum(1 for r in rows if r.get("activated"))
    dead = sum(1 for r in rows if r.get("dead"))
    failed = total - complete
    pids = wave_running()
    # gateway
    gw_code, gw_body = jget(GW + "/health", timeout=5)
    gw_models = []
    if gw_code == 200:
        c, b = jget(GW + "/v1/models", timeout=60)
        if c == 200:
            try:
                gw_models = [m["id"] for m in json.loads(b).get("data", [])]
            except Exception:
                pass
    # pool
    pool_left = 0
    try:
        used = {r.get("email") for r in rows}
        with open(POOL, encoding="utf-8", errors="ignore") as f:
            for line in f:
                em = line.strip().split(":")[0]
                if em and em not in used:
                    pool_left += 1
    except Exception:
        pass
    # disk
    disk = shutil.disk_usage("C:/")
    # log tail
    tail = []
    try:
        with open(REGLOG, encoding="utf-8", errors="ignore") as f:
            tail = f.readlines()[-40:]
        tail = [l.rstrip() for l in tail]
    except Exception:
        pass
    return {
        "ts": int(time.time()),
        "bank": {"total": total, "complete": complete, "activated": activated,
                 "dead": dead, "failed": failed},
        "wave": {"running": bool(pids), "pids": pids},
        "gateway": {"up": gw_code == 200, "code": gw_code, "models": gw_models},
        "pool_left": pool_left,
        "disk_free_gb": round(disk.free / 2**30, 1),
        "pc": pc_stats(),
        "services": services_ping(),
        "captcha": captcha_balances(),
        "log": tail,
    }


def bank_view():
    with _lock:
        rows = load_bank()
    out = []
    for i, r in enumerate(rows):
        out.append({
            "i": i, "email": mask_email(r.get("email", "?")),
            "status": r.get("status", r.get("err", "?")),
            "activated": bool(r.get("activated")),
            "dead": bool(r.get("dead")),
            "org": r.get("org_id", ""),
            "sess": (r.get("session_id") or "")[:18],
        })
    return out


def start_wave(n):
    if wave_running():
        return {"ok": False, "err": "волна уже идёт"}
    logf = open(REGLOG, "a")
    env = dict(os.environ, PYTHONPATH="", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([PY311, REG, str(n)], cwd=AUTOREG, stdout=logf,
                         stderr=subprocess.STDOUT, env=env, creationflags=DETACHED)
    return {"ok": True, "pid": p.pid, "n": n}


def stop_wave():
    pids = wave_running()
    killed = []
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
        killed.append(pid)
    return {"ok": True, "killed": killed}


def run_activate():
    """Один проход activate.py (активирует 1 акк)."""
    if wave_running():
        return {"ok": False, "err": "идёт волна — активация встроена в reg"}
    env = dict(os.environ, PYTHONPATH="", PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([PY311, ACT], cwd=AUTOREG, capture_output=True,
                           text=True, timeout=240, env=env)
        return {"ok": r.returncode == 0, "out": (r.stdout + r.stderr)[-600:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "timeout"}


def gw_import():
    accs = []
    with _lock:
        rows = load_bank()
    for r in rows:
        if r.get("activated") and r.get("client_cookie") and r.get("session_id"):
            accs.append({"name": r["email"].split("@")[0],
                         "clientCookie": r["client_cookie"],
                         "sessionId": r["session_id"]})
    if not accs:
        return {"ok": False, "err": "нет активированных акков"}
    ok = dup = fail = 0
    for a in accs:
        code, body = jpost(GW + "/api/accounts/import", {"accounts": [a]},
                           timeout=30, headers={"X-Logicc-Admin": "1"})
        if code == 200:
            ok += 1
        elif code == 409:
            dup += 1
        else:
            fail += 1
    return {"ok": fail == 0, "sent": len(accs), "imported": ok, "dup": dup, "fail": fail}


def gw_restart():
    # убить node server.mjs на 18789
    rc, out = sh(["powershell", "-NoProfile", "-Command",
                  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
                  "'server\\.mjs' -and $_.Name -match 'node' } | "
                  "ForEach-Object { taskkill /F /T /PID $_.ProcessId }"], timeout=30)
    time.sleep(1)
    gwdir = os.environ.get("LG_GW", "C:/Users/User/tmp/logicc2api")
    state = os.path.join(gwdir, "data", "state.json")
    subprocess.Popen("cd /d %s && (start /b node server.mjs 18789 > gw.log 2>&1)" % gwdir,
                     shell=True, creationflags=DETACHED)
    time.sleep(4)
    code, _ = jget(GW + "/health", timeout=5)
    return {"ok": code == 200, "gw_code": code}


def chat_test(model):
    payload = {"model": model, "max_tokens": 30,
               "messages": [{"role": "user", "content": "Say DASH-OK"}]}
    t0 = time.time()
    code, body = jpost(GW + "/v1/chat/completions", payload, timeout=180)
    dt = round(time.time() - t0, 1)
    txt = ""
    try:
        txt = json.loads(body)["choices"][0]["message"]["content"][:100]
    except Exception:
        txt = body[:150]
    return {"ok": code == 200, "code": code, "sec": dt, "reply": txt}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, ctype="application/json"):
        if isinstance(obj, (dict, list)):
            obj = json.dumps(obj, ensure_ascii=False)
        b = obj.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            html = os.path.join(HERE, "index.html")
            if os.path.exists(html):
                self._send(open(html, encoding="utf-8").read(), ctype="text/html")
            else:
                self._send("index.html missing", 404, "text/plain")
        elif p == "/api/status":
            try:
                self._send(status_all())
            except Exception as e:
                self._send({"err": repr(e)}, 500)
        elif p == "/api/bank":
            self._send(bank_view())
        elif p.startswith("/api/log"):
            n = 60
            try:
                with open(REGLOG, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-n:]
                self._send({"lines": [l.rstrip() for l in lines]})
            except Exception as e:
                self._send({"err": repr(e)}, 500)
        else:
            self._send({"err": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        try:
            if p == "/api/wave/start":
                self._send(start_wave(int(body.get("n", 5))))
            elif p == "/api/wave/stop":
                self._send(stop_wave())
            elif p == "/api/activate":
                self._send(run_activate())
            elif p == "/api/gateway/import":
                self._send(gw_import())
            elif p == "/api/gateway/restart":
                self._send(gw_restart())
            elif p == "/api/chat/test":
                self._send(chat_test(body.get("model", "gpt-6-luna")))
            else:
                self._send({"err": "not found"}, 404)
        except Exception as e:
            self._send({"ok": False, "err": repr(e)}, 500)


class ThreadedHTTPServer(HTTPServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)


if __name__ == "__main__":
    srv = ThreadedHTTPServer(("127.0.0.1", PORT), H)
    print("logicc dashboard on http://127.0.0.1:%d" % PORT)
    srv.serve_forever()
