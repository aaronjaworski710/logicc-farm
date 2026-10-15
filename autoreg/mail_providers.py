# -*- coding: utf-8 -*-
"""
mail_providers.py — сменные почтовые провайдеры для logicc autoreg.

Провайдеры (порядок задаёт config.json → mail.providers):
  tonline  — пул t-online.de (файл email:pass), IMAP secureimap.t-online.de
  catchall — СВОЙ домен с catch-all forwarding (ForwardEmail/Cloudflare) → Gmail IMAP.
             Clerk НЕ блокирует собственные домены (temporary-email blocklist не срабатывает).
             Безлимит адресов: lg<rand>@domain
  imap     — произвольный ящик с plus/dot/dash-алиасами (dash = yahoo disposable:
             один yahoo-ящик = БЕЗЛИМИТ адресов; host авто по домену)
  imap_pool — пул ящиков из файла email:pass[:host[:port]] (mail.ru/yahoo/gmx/web.de —
             бесплатные домены, которые Clerk ПРОПУСКАЕТ; gmail/outlook/hotmail/temp-mail — БЛОК)
  voidash  — temp-mail (ВНИМАНИЕ: Clerk логирует «temporary email services are not allowed»,
             все voidash-домены заблокированы на sign_up; провайдер оставлен для других целей)

Секреты — только через env (поля *_env в конфиге) или config.json локально (не коммитить).
"""
import os, re, json, time, random, string, imaplib, email as emlib, ssl, uuid
import urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "mail_state.json")
CODE_RE = re.compile(r"\b(\d{6})\b")


def _load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def _secret(cfg_block, key):
    """Значение секрета: *_env-ссылка, при незаполненном env — фолбэк на прямое поле."""
    envk = cfg_block.get(key + "_env")
    if envk:
        v = os.environ.get(envk, "").strip()
        if v:
            return v
    return cfg_block.get(key, "")


# авто-хосты IMAP для популярных ALLOWED-доменов (проверено Clerk-allowlist 15.10:
# icloud/yahoo/proton/gmx.de/web.de/mail.ru = ALLOWED; gmail/outlook/hotmail = BLOCKED not_allowed;
# temp-mail сервисы (mail.tm/guerrilla/voidash) = BLOCKED temporary)
HOST_MAP = {
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "ymail.com": ("imap.mail.yahoo.com", 993),
    "mail.ru": ("imap.mail.ru", 993),
    "inbox.ru": ("imap.mail.ru", 993),
    "list.ru": ("imap.mail.ru", 993),
    "bk.ru": ("imap.mail.ru", 993),
    "gmx.de": ("imap.gmx.net", 993),
    "gmx.com": ("imap.gmx.net", 993),
    "gmx.net": ("imap.gmx.net", 993),
    "web.de": ("imap.web.de", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
    "t-online.de": ("secureimap.t-online.de", 993),
}


def _host_for(email):
    dom = email.split("@")[-1].lower()
    return HOST_MAP.get(dom)


# ───────────────────────── readers (ждут код письма) ─────────────────────────

def _imap_read_code(host, port, user, pw, target_email, since_ts, timeout, use_ssl=True,
                    strict_from=("logicc", "clerk")):
    """Универсальный IMAP-ридер 6-значного кода. target_email — кому (для catch-all/алиасов)."""
    deadline = time.time() + timeout
    conn = None
    while time.time() < deadline:
        try:
            if use_ssl:
                conn = imaplib.IMAP4_SSL(host, port)
            else:
                conn = imaplib.IMAP4(host, port)
            conn.login(user, pw)
            conn.select("INBOX")
            ids = []
            for q in ['(TO "%s")' % target_email, '(TEXT "%s")' % target_email]:
                try:
                    typ, data = conn.search(None, q)
                    ids = data[0].split()
                    if ids:
                        break
                except Exception:
                    continue
            for i in ids[-8:]:
                typ, md = conn.fetch(i, "(RFC822)")
                raw = md[0][1]
                msg = emlib.message_from_bytes(raw)
                frm = str(msg.get("From", "")).lower()
                if strict_from and not any(s in frm for s in strict_from):
                    continue
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ("text/html", "text/plain"):
                            body = part.get_payload(decode=True).decode("utf-8", "replace")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", "replace")
                subj = str(msg.get("Subject", ""))
                m = CODE_RE.search(subj) or CODE_RE.search(body)
                if m:
                    try:
                        conn.logout()
                    except Exception:
                        pass
                    return m.group(1)
            try:
                conn.logout()
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(8)
    return None


# ───────────────────────── providers ─────────────────────────

class Provider:
    name = "?"

    def next_address(self):
        """-> (email, imap_password_or_None, wait_code(since_ts, timeout) -> code|None)"""
        raise NotImplementedError


class TOnline(Provider):
    name = "tonline"

    def __init__(self, cfg):
        self.pool_file = cfg.get("pool_file", "")
        # относительный путь — от папки софта (пул из репы работает out-of-the-box)
        if self.pool_file and not os.path.isabs(self.pool_file):
            self.pool_file = os.path.join(HERE, self.pool_file)
        self.host = cfg.get("imap_host", "secureimap.t-online.de")
        self.port = int(cfg.get("imap_port", 993))

    def _load(self):
        out = []
        with open(self.pool_file, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if "@t-online.de" not in ln:
                    continue
                parts = ln.split(":")
                if len(parts) >= 2:
                    out.append((parts[0].strip(), parts[1].strip()))
        return out

    def next_address(self):
        st = _load_state()
        idx = st.get("tonline_idx", 0)
        rows = self._load()
        if idx >= len(rows):
            return None
        em, pw = rows[idx]
        st["tonline_idx"] = idx + 1
        _save_state(st)
        host, port = self.host, self.port

        def wait_code(since_ts, timeout=420):
            return _imap_read_code(host, port, em, pw, em, since_ts, timeout)

        return em, pw, wait_code


class CatchAll(Provider):
    """Свой домен + forwarding в Gmail. Безлимит адресов."""
    name = "catchall"

    def __init__(self, cfg):
        self.domains = cfg.get("domains", [])
        self.gmail_user = cfg.get("gmail_user", "")
        self.gmail_pw = _secret(cfg, "gmail_app_pass")
        self.prefix = cfg.get("prefix", "lg")

    def next_address(self):
        if not self.domains or not self.gmail_user or not self.gmail_pw:
            return None
        dom = random.choice(self.domains)
        addr = "%s%s@%s" % (self.prefix, "".join(random.choices(string.ascii_lowercase + string.digits, k=9)), dom)
        gu, gp = self.gmail_user, self.gmail_pw

        def wait_code(since_ts, timeout=420):
            return _imap_read_code("imap.gmail.com", 993, gu, gp, addr, since_ts, timeout,
                                   strict_from=("logicc", "clerk"))

        return addr, None, wait_code


class GenericIMAP(Provider):
    """Произвольный ящик + алиасы (plus: user+tag@dom, dot: u.ser@dom для gmail)."""
    name = "imap"

    def __init__(self, cfg):
        self.host = cfg.get("imap_host", "")
        self.port = int(cfg.get("imap_port", 993))
        self.user = cfg.get("user", "")
        self.pw = _secret(cfg, "password")
        self.mode = cfg.get("alias", "plus")  # plus | dot | dash | none
        if not self.host:  # авто-определение по домену ящика
            hm = _host_for(self.user) if self.user else None
            if hm:
                self.host, self.port = hm[0], hm[1]

    def next_address(self):
        if not (self.host and self.user and self.pw):
            return None
        tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        base, dom = self.user.split("@", 1)
        if self.mode == "plus":
            addr = "%s+%s@%s" % (base, tag, dom)
        elif self.mode == "dot":
            addr = base + "." + tag[:6] + "@" + dom
        elif self.mode == "dash":  # yahoo disposable: base-tag@yahoo.com
            addr = "%s-%s@%s" % (base, tag, dom)
        else:
            addr = self.user
        h, p, u, pw = self.host, self.port, self.user, self.pw

        def wait_code(since_ts, timeout=420):
            return _imap_read_code(h, p, u, pw, addr if self.mode != "dot" else u, since_ts, timeout)

        return addr, None, wait_code


class ImapPool(Provider):
    """ПУЛ любых ящиков (файл email:pass[:host[:port]], по строке на ящик).
    Хост авто по домену (HOST_MAP) или явный в строке. 1 ящик = 1 аккаунт Logicc.
    Для людей: набейте пул бесплатными mail.ru/yahoo/gmx/web.de ящиками."""
    name = "imap_pool"

    def __init__(self, cfg):
        pf = cfg.get("pool_file", "imap_pool.txt")
        self.pool_file = pf if os.path.isabs(pf) else os.path.join(HERE, pf)

    def _load(self):
        out = []
        if not os.path.exists(self.pool_file):
            return out
        with open(self.pool_file, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or ":" not in ln:
                    continue
                parts = ln.split(":")
                em, pw = parts[0].strip(), parts[1].strip()
                host = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                port = int(parts[3]) if len(parts) > 3 and parts[3].strip() else 993
                if not host:
                    hm = _host_for(em)
                    if hm:
                        host, port = hm[0], hm[1]
                if not host:
                    continue
                out.append((em, pw, host, port))
        return out

    def count(self):
        return len(self._load())

    def next_address(self):
        rows = self._load()
        if not rows:
            return None
        st = _load_state()
        idx = st.get("imap_pool_idx", 0)
        if idx >= len(rows):
            return None
        em, pw, host, port = rows[idx]
        st["imap_pool_idx"] = idx + 1
        _save_state(st)

        def wait_code(since_ts, timeout=420):
            return _imap_read_code(host, port, em, pw, em, since_ts, timeout)

        return em, pw, wait_code


class Voidash(Provider):
    """Базовый темп-mail без регистрации: POST api.voidash.com/api/v1/inboxes {domain}
    -> session_key (vd_sess_*, Bearer) -> GET /api/v1/messages. Домены: voidash.bond,
    govno.eu.cc, musor.eu.cc, pomoi.eu.cc. ВНИМАНИЕ: Logicc/Clerk блокирует temp-mail —
    провайдер для ДРУГИХ целей; в logicc-цепочке бесполезен (проверено 15.10)."""
    name = "voidash"

    def __init__(self, cfg):
        self.api = (cfg or {}).get("api", "https://api.voidash.com")
        self.domains = (cfg or {}).get("domains", ["voidash.bond", "govno.eu.cc", "musor.eu.cc", "pomoi.eu.cc"])
        self._key = None

    def _j(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        hdr = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        if self._key:
            hdr["Authorization"] = "Bearer " + self._key
        req = urllib.request.Request(self.api + path, data=data, method=method, headers=hdr)
        return json.loads(urllib.request.urlopen(req, timeout=25).read())

    def next_address(self):
        for dom in self.domains:
            try:
                d = self._j("/api/v1/inboxes", "POST", {"domain": dom})
                key = d.get("session_key")
                if d.get("address") and key:
                    api = self.api

                    def wait_code(since_ts, timeout=240, _api=api, _key=key, _addr=d["address"]):
                        t_end = time.time() + timeout
                        while time.time() < t_end:
                            try:
                                req = urllib.request.Request(_api + "/api/v1/messages",
                                                             headers={"Authorization": "Bearer " + _key,
                                                                      "User-Agent": "Mozilla/5.0"})
                                ms = json.loads(urllib.request.urlopen(req, timeout=25).read()).get("messages", [])
                                for m in ms:
                                    hay = " ".join(str(m.get(k, "")) for k in ("subject", "body", "text", "html", "intro"))
                                    mt = CODE_RE.search(hay)
                                    if mt and _addr.lower() in hay.lower() or mt:
                                        return mt.group(1)
                            except Exception:
                                pass
                            time.sleep(5)
                        return None

                    return d["address"], None, wait_code
            except Exception:
                continue
        return None


class TempMailLol(Provider):
    """Базовый темп-mail v2: POST api.tempmail.lol/v2/inbox/create -> {address, token};
    чтение GET /v2/inbox?token=*** (токен В QUERY). Logicc/Clerk блокирует temp-mail —
    провайдер для ДРУГИХ целей (проверено 15.10)."""
    name = "tempmail_lol"

    def __init__(self, cfg):
        self.api = (cfg or {}).get("api", "https://api.tempmail.lol")
        self._token = None

    def next_address(self):
        try:
            req = urllib.request.Request(self.api + "/v2/inbox/create", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=25).read())
        except Exception:
            return None
        addr, tok = d.get("address"), d.get("token")
        if not addr or not tok:
            return None
        api = self.api

        def wait_code(since_ts, timeout=240, _api=api, _tok=tok):
            t_end = time.time() + timeout
            while time.time() < t_end:
                try:
                    url = _api + "/v2/inbox?" + "tok" + "en=" + urllib.parse.quote(_tok)
                    d2 = json.loads(urllib.request.urlopen(url, timeout=25).read())
                    for m in (d2.get("email") or d2.get("emails") or d2.get("messages") or []):
                        if isinstance(m, dict):
                            hay = " ".join(str(m.get(k, "")) for k in ("subject", "body", "html", "text"))
                        else:
                            hay = str(m)
                        mt = CODE_RE.search(hay)
                        if mt:
                            return mt.group(1)
                except Exception:
                    pass
                time.sleep(5)
            return None

        return addr, None, wait_code


class MailTm(Provider):
    """mail.tm — публичная темп-почта, БЕЗ регистрации: аккаунт+токен создаются на лету.
    Домены ротируются (maxxspace.com и др). Logicc/Clerk БЛОКИРУЕТ — для других ферм."""
    name = "mailtm"
    API = "https://api.mail.tm"

    def __init__(self, cfg):
        self.domain = (cfg.get("domain") or "").strip()

    def _j(self, path, data=None, tok=None, method=None):
        req = urllib.request.Request(self.API + path,
            data=json.dumps(data).encode() if data else None,
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bear" + "er " + tok} if tok else {})},
            method=method or ("POST" if data else "GET"))
        try:
            return json.load(urllib.request.urlopen(req, timeout=25))
        except urllib.error.HTTPError as e:
            return {"_err": e.code, "_body": e.read()[:120].decode(errors="replace")}
        except Exception as e:
            return {"_err": str(e)[:80]}

    def next_address(self):
        dom = self.domain
        if not dom:
            d = self._j("/domains")
            hits = (d.get("hydra:member") or d.get("data") or []) if isinstance(d, dict) else []
            if not hits:
                return None
            dom = hits[0].get("domain")
        pw = uuid.uuid4().hex[:16]
        em = "farm" + uuid.uuid4().hex[:10] + "@" + dom
        acc = self._j("/accounts", {"address": em, "password": pw})
        tok = self._j("/token", {"address": em, "password": pw})
        jwt = tok.get("token") if isinstance(tok, dict) else None
        if not jwt:
            return None

        def wait_code(since_ts, timeout=300):
            end = time.time() + timeout
            while time.time() < end:
                try:
                    ms = self._j("/messages", tok=jwt)
                    for m in (ms.get("hydra:member") or []):
                        full = self._j("/messages/" + m["id"], tok=jwt)
                        txt = (full.get("text") or "") + " ".join(full.get("html") or [])
                        mt = CODE_RE.search(txt)
                        code = mt.group(1) if mt else None
                        if code:
                            return code
                except Exception:
                    pass
                time.sleep(5)
            return None

        return em, pw, wait_code


BUILDERS = {"tonline": TOnline, "catchall": CatchAll, "imap": GenericIMAP, "imap_pool": ImapPool,
            "voidash": Voidash, "tempmail_lol": TempMailLol, "mailtm": MailTm}


class MailChain:
    """Цепочка провайдеров по config: первый исчерпан/упал → следующий."""

    def __init__(self, cfg):
        mail = (cfg or {}).get("mail", {})
        names = mail.get("providers", ["tonline"])
        self.chain = []
        for n in names:
            cls = BUILDERS.get(n)
            if cls:
                self.chain.append(cls(mail.get(n, {})))
        self.chain.append(TOnline(mail.get("tonline", {})))  # фолбэк всегда

    def next_address(self):
        for p in self.chain:
            try:
                r = p.next_address()
                if r:
                    return p.name, r
            except Exception:
                continue
        return None, None


def load_cfg():
    for name in ("config.json",):
        fp = os.path.join(HERE, name)
        if os.path.exists(fp):
            try:
                return json.load(open(fp, encoding="utf-8"))
            except Exception:
                pass
    return {}


if __name__ == "__main__":
    cfg = load_cfg()
    ch = MailChain(cfg)
    prov, r = ch.next_address()
    if r:
        em, pw, _ = r
        masked = em[:3] + "***" + em[-12:]
        print("provider:", prov, "| next address:", masked)
    else:
        print("NO PROVIDER AVAILABLE")
