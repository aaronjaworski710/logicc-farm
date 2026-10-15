# -*- coding: utf-8 -*-
"""
mail_providers.py — сменные почтовые провайдеры для logicc autoreg.

Провайдеры (порядок задаёт config.json → mail.providers):
  tonline  — пул t-online.de (файл email:pass), IMAP secureimap.t-online.de
  catchall — СВОЙ домен с catch-all forwarding (ForwardEmail/Cloudflare) → Gmail IMAP.
             Clerk НЕ блокирует собственные домены (temporary-email blocklist не срабатывает).
             Безлимит адресов: lg<rand>@domain
  imap     — произвольный ящик (Gmail/Outlook/свой сервер) с plus- или dot-алиасами
  voidash  — temp-mail (ВНИМАНИЕ: Clerk логирует «temporary email services are not allowed»,
             все voidash-домены заблокированы на sign_up; провайдер оставлен для других целей)

Секреты — только через env (поля *_env в конфиге) или config.json локально (не коммитить).
"""
import os, re, json, time, random, string, imaplib, email as emlib, ssl

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
    """Значение секрета: прямое поле или *_env-ссылка."""
    envk = cfg_block.get(key + "_env")
    if envk:
        return os.environ.get(envk, "")
    return cfg_block.get(key, "")


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
        self.mode = cfg.get("alias", "plus")  # plus | dot | none

    def next_address(self):
        if not (self.host and self.user and self.pw):
            return None
        tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        base, dom = self.user.split("@", 1)
        if self.mode == "plus":
            addr = "%s+%s@%s" % (base, tag, dom)
        elif self.mode == "dot":
            addr = base + "." + tag[:6] + "@" + dom
        else:
            addr = self.user
        h, p, u, pw = self.host, self.port, self.user, self.pw

        def wait_code(since_ts, timeout=420):
            return _imap_read_code(h, p, u, pw, addr if self.mode != "dot" else u, since_ts, timeout)

        return addr, None, wait_code


BUILDERS = {"tonline": TOnline, "catchall": CatchAll, "imap": GenericIMAP}


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
