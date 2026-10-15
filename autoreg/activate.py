# -*- coding: utf-8 -*-
"""Activate one Logicc farm account: create tenant (org) via app API,
   set Clerk active organization, re-mint JWT, verify ent claims + models."""
import json, base64, sys, os
from curl_cffi import requests as cr

BANK = "C:/Users/User/tmp/logicc_autoreg/logicc_bank.jsonl"
UA_HDRS = {"Origin": "https://app.logicc.com", "Referer": "https://app.logicc.com/"}
AUTH = "Bea" + "rer "

def load_bank():
    rows = []
    with open(BANK, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def save_bank(rows):
    tmp = BANK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, BANK)

def mint_jwt(s, rec):
    url = "https://clerk.logicc.com/v1/client/sessions/%s/tokens" % rec["session_id"]
    resp = s.post(url, headers={**UA_HDRS, "Cookie": "__client=" + rec["client_cookie"]}, timeout=30)
    if resp.status_code != 200:
        print("clerk tokens FAIL", resp.status_code, resp.text[:150])
        return None
    for c in s.cookies.jar:
        if c.name == "__client" and c.value != rec["client_cookie"]:
            rec["client_cookie"] = c.value
    return resp.json().get("jwt")

def decode_claims(jwt):
    p = jwt.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))

def main():
    email = sys.argv[1] if len(sys.argv) > 1 else None
    rows = load_bank()
    rec = None
    for r in rows:
        if r.get("status") != "complete" or not r.get("client_cookie"):
            continue
        if email and r["email"] != email:
            continue
        if r.get("activated") or r.get("dead"):
            continue
        rec = r
    if rec is None:
        print("NO CANDIDATE"); return
    print("acct:", rec["email"])
    s = cr.Session(impersonate="chrome131")
    jwt = mint_jwt(s, rec)
    if not jwt:
        rec["dead"] = True
        save_bank(rows); return
    H = {**UA_HDRS, "Authorization": AUTH + jwt, "Accept": "application/json"}
    # 0. current tenant state
    r0 = s.get("https://app.logicc.com/api/tenants", headers=H, timeout=30)
    print("GET tenants:", r0.status_code, r0.text[:200])
    # 1. create tenant
    r1 = s.post("https://app.logicc.com/api/tenants", headers={**H, "Content-Type": "application/json"},
                json={"name": "Research Workspace", "industry": "information-technology", "size": "medium", "lastAdAttribution": None}, timeout=45)
    print("POST tenants:", r1.status_code, r1.text[:250])
    org_id = None
    if r1.status_code == 200:
        body = r1.text.strip().strip('"')
        org_id = body if body.startswith("org_") else None
        try:
            j = r1.json()
            if isinstance(j, dict):
                org_id = j.get("id") or j.get("organizationId") or org_id
            elif isinstance(j, str) and j.startswith("org_"):
                org_id = j
        except Exception:
            pass
    if not org_id:
        r0b = s.get("https://app.logicc.com/api/tenants", headers=H, timeout=30)
        print("GET tenants again:", r0b.status_code, r0b.text[:300])
        try:
            tl = r0b.json()
            if isinstance(tl, list) and tl:
                org_id = tl[0].get("id") or tl[0].get("organizationId")
        except Exception:
            pass
    save_bank(rows)
    if not org_id:
        print("NO ORG ID - stop"); return
    print("org_id:", org_id)
    # 2. clerk setActive org: touch session
    t = s.post("https://clerk.logicc.com/v1/client/sessions/%s/touch" % rec["session_id"],
               headers={**UA_HDRS, "Cookie": "__client=" + rec["client_cookie"],
                        "Content-Type": "application/x-www-form-urlencoded"},
               data={"active_organization_id": org_id}, timeout=30)
    print("touch:", t.status_code)
    for c in s.cookies.jar:
        if c.name == "__client":
            rec["client_cookie"] = c.value
    save_bank(rows)
    # 3. re-mint JWT + claims
    jwt2 = mint_jwt(s, rec)
    save_bank(rows)
    if not jwt2:
        return
    claims = decode_claims(jwt2)
    print("claims ent:", claims.get("ent"), "| per:", claims.get("per"), "| o:", claims.get("o"))
    # 4. models test
    r4 = s.get("https://app.logicc.com/api/models?language=en", headers={**H, "Authorization": AUTH + jwt2}, timeout=60)
    print("MODELS:", r4.status_code, r4.text[:220])
    if r4.status_code == 200:
        rec["activated"] = True
        rec["org_id"] = org_id
        save_bank(rows)
        print("ACTIVATED OK")

if __name__ == "__main__":
    main()
