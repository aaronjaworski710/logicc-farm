# logicc_reg.py N — mass autoreg app.logicc.com via pure-HTTP Clerk API + free Turnstile sidecar
import json, os, random, re, ssl, sys, time, urllib.parse, urllib.request, http.cookiejar
import imaplib, email as emaillib
from email.header import decode_header

BASE_CLERK = "https://clerk.logicc.com"
SITEKEY = "0x4AAAAAAAWXJGBD7bONzLBd"
PAGE = "https://accounts.logicc.com/sign-up"
SIDE = os.environ.get("SIDE", "http://127.0.0.1:8877/solve")
JSV = "5.56.0"

HERE = os.path.dirname(os.path.abspath(__file__))
POOL = os.environ.get("LG_POOL", r"C:/Users/User/Desktop/avtoreg/working_mails.txt")
BANK = os.path.join(HERE, "logicc_bank.jsonl")
STATE = os.path.join(HERE, "state.json")

VD = "https://api.voidash.com"
VD_DOMAINS = ["voidash.bond", "govno.eu.cc", "musor.eu.cc", "pomoi.eu.cc", "voidash.cyou"]

def vd_json(method, path, body=None, sk=None, timeout=30):
    url = VD + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": UA, "Accept": "application/json",
        **({"Content-Type": "application/json"} if data else {}),
        **({"Authorization": "Bearer " + sk} if sk else {}),
    })
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode())

def create_inbox():
    dom = random.choice(VD_DOMAINS)
    try:
        j = vd_json("POST", "/api/v1/inboxes", {"domain": dom})
    except Exception as e:
        log("voidash create err:", str(e)[:120]); return None, None
    sk = j.get("session_key")
    inbox = j.get("inbox", j)
    addr = inbox.get("address") if isinstance(inbox, dict) else None
    return addr, sk

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"

FIRST = ["James","Oliver","Liam","Noah","Emma","Ava","Sophia","Mia","Lucas","Henry","Jack","Leo","Anna","Marie","Paul","Felix","Nina","Clara","Max","Oscar"]
LAST = ["Meyer","Schmidt","Fischer","Weber","Wagner","Becker","Hoffmann","Schulz","Koch","Richter","Klein","Wolf","Neumann","Braun","Kruger","Hartmann","Lange","Werner","Krause","Lehmann"]

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"idx": 0}

def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)

def load_pool():
    out = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        em, pw = line.split(":", 1)
        if "-" in em.split("@")[0]:  # dash-alias dead for delivery
            continue
        out.append((em, pw))
    return out

KEY_FILE = os.environ.get("LG_KEYS", "C:/Users/User/tmp/captcha_keys_2captcha.json")
_tc_key = None

def post_form(url, data, timeout=60):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode()

def load_2c_key():
    global _tc_key
    if _tc_key:
        return _tc_key
    d = json.load(open(KEY_FILE))
    ks = d["keys"] if isinstance(d, dict) else d
    best, best_bal = None, -1e9
    for k in ks:
        try:
            out = post_form("https://api.2captcha.com/res.php",
                            {"key": k["key"], "action": "getbalance", "json": 1}, timeout=30)
            bal = float(json.loads(out).get("request", -1))
        except Exception:
            bal = -1.0
        log("2captcha key", k.get("label"), "balance $%.3f" % bal)
        if bal > best_bal:
            best, best_bal = k["key"], bal
    if best_bal < 0.05:
        raise RuntimeError("all 2captcha keys empty (best $%.3f)" % best_bal)
    _tc_key = best
    return best

def solve_2captcha():
    key = load_2c_key()
    try:
        out = post_form("https://api.2captcha.com/in.php",
                        {"key": key, "method": "turnstile", "sitekey": SITEKEY,
                         "pageurl": PAGE, "json": 1}, timeout=60)
        d = json.loads(out)
        if d.get("errorId"):
            log("2c in err:", out[:150]); return ""
        tid = d["request"]
    except Exception as e:
        log("2c in exc:", str(e)[:120]); return ""
    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(10)
        try:
            d = json.loads(post_form("https://api.2captcha.com/res.php",
                                     {"key": key, "action": "get", "id": tid, "json": 1}, timeout=30))
            st = d.get("status")
            if st == 1 or st == "success":
                r = d.get("request")
                if isinstance(r, dict):
                    return r.get("token") or ""
                return str(r)
            if isinstance(st, str) and st.startswith("ERROR"):
                log("2c fatal:", st); return ""
            if st == 0 and isinstance(d.get("request"), str) and d["request"].startswith("ERROR"):
                log("2c fatal:", d["request"]); return ""
        except Exception as e:
            log("2c poll err:", str(e)[:80])
    return ""

def solve_sidecar():
    try:
        p = {"type": "turnstile", "url": PAGE, "sitekey": SITEKEY, "real_page": True}
        req = urllib.request.Request(SIDE, data=json.dumps(p).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=300).read())
        tok = r.get("token") or ""
        if tok:
            return tok
        log("sidecar empty:", str(r)[:120])
    except Exception as e:
        log("sidecar err:", str(e)[:120])
    return ""

def solve_turnstile():
    # sidecar cloakbrowser gives 408 on this sitekey (clicked→timeout); 2captcha solves in ~10s
    return solve_2captcha()

def clerk_post(jar, path, fields):
    url = BASE_CLERK + path
    if "?" in url:
        url += "&_clerk_js_version=" + JSV
    else:
        url += "?_clerk_js_version=" + JSV
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://accounts.logicc.com",
        "Referer": PAGE,
        "Accept": "*/*",
    })
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    resp = op.open(req, timeout=60)
    body = resp.read().decode("utf-8", "replace")
    return resp, json.loads(body)

def imap_wait_code(user, pw, since_ts, timeout=540):
    deadline = time.time() + timeout
    conn = None
    while time.time() < deadline:
        try:
            if conn is None:
                conn = imaplib.IMAP4_SSL("secureimap.t-online.de", 993, timeout=30)
                conn.login(user, pw)
            for folder in ("INBOX", "INBOX.Spam"):
                try:
                    conn.select(folder)
                except Exception:
                    continue
                typ, data = conn.search(None, "ALL")
                ids = data[0].split()[-30:]
                for i in reversed(ids):
                    typ, md = conn.fetch(i, "(RFC822)")
                    raw = md[0][1]
                    msg = emaillib.message_from_bytes(raw)
                    dh = msg.get("Date")
                    if dh:
                        try:
                            mt = emaillib.parsedate_to_datetime(dh).timestamp()
                            if mt < since_ts - 120:
                                continue
                        except Exception:
                            pass
                    subj = str(msg.get("Subject") or "")
                    frm = str(msg.get("From") or "")
                    low = (subj + frm).lower()
                    if "logicc" not in low and "clerk" not in low:
                        continue  # strict: only Logicc/Clerk senders (spam 'verif' false-positives)
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct == "text/plain":
                                body += part.get_payload(decode=True).decode("utf-8", "replace")
                            elif ct == "text/html" and not body:
                                body += part.get_payload(decode=True).decode("utf-8", "replace")
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", "replace")
                    m = re.search(r"\b(\d{6})\b", re.sub(r"<[^>]+>", " ", body))
                    if m:
                        try: conn.logout()
                        except Exception: pass
                        return m.group(1)
            time.sleep(15)
        except Exception as e:
            log("imap err:", str(e)[:100])
            try: conn and conn.logout()
            except Exception: pass
            conn = None
            time.sleep(10)
    return ""

def vd_wait_code(sk, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            j = vd_json("GET", "/api/v1/messages", sk=sk)
            for msg in j.get("messages", []):
                subj = (msg.get("subject") or "")
                frm = (msg.get("from") or "") if isinstance(msg.get("from"), str) else json.dumps(msg.get("from") or "")
                if "logicc" not in (subj + frm).lower() and "verif" not in subj.lower() and "code" not in subj.lower():
                    continue
                raw = (msg.get("html") or "") + (msg.get("text") or "") + (msg.get("body") or "")
                txt = re.sub(r"<[^>]+>", " ", raw)
                m = re.search(r"\b(\d{6})\b", txt)
                if m:
                    return m.group(1)
                m = re.search(r"\b(\d{4,8})\b", txt)
                if m:
                    return m.group(1)
        except Exception as e:
            log("vd poll err:", str(e)[:100])
        time.sleep(8)
    return ""

def register(em, pw):
    jar = http.cookiejar.CookieJar()
    tok = solve_turnstile()
    if not tok:
        return {"email": em, "err": "no_turnstile"}
    first = random.choice(FIRST); last = random.choice(LAST)
    password = "Lg" + "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=6)) + str(random.randint(10, 99)) + "!"
    t0 = time.time()
    d2 = None
    try:
        _, d = clerk_post(jar, "/v1/client/sign_ups", {
            "email_address": em, "password": password,
            "first_name": first, "last_name": last,
            "legal_accepted": "true",
            "captcha_token": tok,
            "unsafe_metadata": "{}",
        })
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"email": em, "err": f"signup_http_{e.code}", "body": body[:500]}
    su = d.get("sign_up") or (d.get("response") or {})
    su_id = su.get("id") or ""
    status = su.get("status")
    if not su_id:
        return {"email": em, "err": "no_su_id", "body": json.dumps(d)[:500]}
    log("su_id", su_id, "status", status)
    if status == "complete":
        pass  # no email verification needed
    else:
        # trigger code delivery explicitly (sign-up uses prepare_verification, not prepare_first_factor)
        try:
            _, dp = clerk_post(jar, f"/v1/client/sign_ups/{su_id}/prepare_verification",
                               {"strategy": "email_code"})
            log("prepare ok")
        except urllib.error.HTTPError as e:
            log("prepare http", e.code, e.read().decode("utf-8", "replace")[:150])
        code = imap_wait_code(em, pw, t0, timeout=420)
        if not code:
            return {"email": em, "err": "no_code", "su_id": su_id, "password": password}
        log("code", code)
        try:
            _, d2 = clerk_post(jar, f"/v1/client/sign_ups/{su_id}/attempt_verification",
                               {"strategy": "email_code", "code": code})
        except urllib.error.HTTPError as e:
            return {"email": em, "err": f"verify_http_{e.code}", "body": e.read().decode("utf-8","replace")[:500], "su_id": su_id}
    # harvest session + cookies
    client = (d2 or d).get("client") or {}
    su_out = (d2 or d).get("response") or (d2 or d).get("sign_up") or {}
    sess_id = ""
    jwt = ""
    sessions = client.get("sessions") or []
    if sessions:
        sess_id = sessions[0].get("id") or ""
        jwt = sessions[0].get("token") or ""
    if not sess_id:
        m = re.search(r"sess_[A-Za-z0-9]+", json.dumps(d2 or d))
        sess_id = m.group(0) if m else ""
    cclient = ""; csession = ""
    for c in jar:
        if c.name == "__client":
            cclient = c.value
        elif c.name == "__session":
            csession = c.value
    if not jwt and sess_id:
        try:
            _, dt = clerk_post(jar, f"/v1/client/sessions/{sess_id}/tokens", {})
            jwt = ((dt.get("response") or {}).get("jwt")) or ""
        except Exception:
            pass
    if not jwt and csession:
        jwt = csession
    rec = {"email": em, "password": password, "first": first, "last": last,
           "client_cookie": cclient, "session_id": sess_id, "jwt": jwt,
           "su_id": su_id, "status": su_out.get("status") if isinstance(su_out, dict) else "",
           "ts": int(time.time())}
    if not cclient or not sess_id:
        rec["err"] = "no_creds"
        su_dbg = su_out if isinstance(su_out, dict) else {}
        rec["missing"] = su_dbg.get("missing_requirements") or su_dbg.get("missing") or ""
        rec["verifications"] = json.dumps(su_dbg.get("verifications") or {})[:300]
        rec["body"] = json.dumps(d2 or d)[:600]
    return rec

def activate_account(rec):
    """Create tenant (org) via app API + set Clerk active org. Returns org_id or ''."""
    try:
        from curl_cffi import requests as cr
    except ImportError:
        log("curl_cffi missing - skip activation"); return ""
    UA = {"Origin": "https://app.logicc.com", "Referer": "https://app.logicc.com/"}
    s = cr.Session(impersonate="chrome131")
    try:
        url = BASE_CLERK + "/v1/client/sessions/%s/tokens" % rec["session_id"]
        r = s.post(url, headers={**UA, "Cookie": "__client=" + rec["client_cookie"]}, timeout=30)
        if r.status_code != 200:
            log("activate: tokens http", r.status_code); return ""
        for c in s.cookies.jar:
            if c.name == "__client":
                rec["client_cookie"] = c.value
        jwt = (r.json() or {}).get("jwt", "")
        if not jwt:
            return ""
        H = {**UA, "Authorization": ("Bea" + "rer ") + jwt,
             "Content-Type": "application/json", "Accept": "application/json"}
        r1 = s.post("https://app.logicc.com/api/tenants", headers=H,
                    json={"name": "Research Workspace", "industry": "information-technology",
                          "size": "medium", "lastAdAttribution": None}, timeout=45)
        org = r1.text.strip().strip('"') if r1.status_code == 200 else ""
        if not org.startswith("org_"):
            log("activate: tenants http", r1.status_code, r1.text[:120]); return ""
        s.post(BASE_CLERK + "/v1/client/sessions/%s/touch" % rec["session_id"],
               headers={**UA, "Cookie": "__client=" + rec["client_cookie"],
                        "Content-Type": "application/x-www-form-urlencoded"},
               data={"active_organization_id": org}, timeout=30)
        for c in s.cookies.jar:
            if c.name == "__client":
                rec["client_cookie"] = c.value
        rec["jwt"] = ""  # old jwt has no org claims; gateway will refresh
        return org
    except Exception as e:
        log("activate exc", repr(e)); return ""

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    pool = load_pool()
    st = load_state()
    used = set()
    if os.path.exists(BANK):
        for line in open(BANK, encoding="utf-8"):
            try: used.add(json.loads(line)["email"])
            except Exception: pass
    ok = fail = 0
    for i in range(n):
        while st["idx"] < len(pool) and pool[st["idx"]][0] in used:
            st["idx"] += 1
        if st["idx"] >= len(pool):
            log("pool exhausted"); break
        em, pw = pool[st["idx"]]
        st["idx"] += 1
        save_state(st)
        log(f"[{i+1}/{n}] reg {em}")
        rec = register(em, pw)
        if rec.get("client_cookie") and rec.get("session_id"):
            org = activate_account(rec)
            if org:
                rec["activated"] = True
                rec["org_id"] = org
                log("activated", org)
        with open(BANK, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if rec.get("client_cookie") and rec.get("session_id"):
            ok += 1
            log("OK", rec["session_id"])
        else:
            fail += 1
            log("FAIL", rec.get("err"), str(rec.get("body", ""))[:200])
        time.sleep(random.randint(20, 40))
    log(f"DONE ok={ok} fail={fail}")

if __name__ == "__main__":
    main()
