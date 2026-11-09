import json, os, re, sqlite3, tempfile
TMP = tempfile.mkdtemp()
os.environ["WH_DB"] = os.path.join(TMP, "t.db")

# --- Build an "old" database (before rule sets existed) to prove the upgrade works on your existing data.
OLD = os.path.join(TMP, "old.db")
c = sqlite3.connect(OLD)
c.executescript("""
CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE settings(user_id INTEGER PRIMARY KEY, company_name TEXT DEFAULT '', hourly_rate TEXT DEFAULT '0.00', fed_pct TEXT DEFAULT '10.00', ss_pct TEXT DEFAULT '6.20', med_pct TEXT DEFAULT '1.45');
CREATE TABLE paystubs(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, pay_date TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL, rate TEXT NOT NULL, fed_pct TEXT, ss_pct TEXT, med_pct TEXT, total_hours TEXT, gross TEXT, deductions_total TEXT, net TEXT, days_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO users(id,username,password_hash) VALUES(1,'old','x');
INSERT INTO settings(user_id,company_name,hourly_rate) VALUES(1,'Arkema','42.08');
INSERT INTO paystubs(user_id,pay_date,period_start,period_end,rate,days_json,total_hours,gross,deductions_total,net)
  VALUES(1,'2026-11-20','2026-11-02','2026-11-15','42.08','%s','80','3366.40','500','2866.40');
""" % json.dumps([{"hours": "8", "ot": False, "dt": False, "vac": False, "hol": i == 0} for i in range(14)]))
c.commit(); c.close()

import app as A
import rules as R

def client():
    return A.app.test_client()

def token(c, path="/login"):
    return re.search(r'name="csrf" content="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)

def register(c, name):
    t = token(c, "/register")
    r = c.post("/register", data={"username": name, "password": "correct-horse-1", "confirm": "correct-horse-1", "csrf_token": t})
    assert r.status_code == 302, r.get_data(as_text=True)
    return token(c, "/")

def days(hours, flags=None):
    out = [{"hours": str(h), "flags": []} for h in hours] + [{"hours": "0", "flags": []} for _ in range(14 - len(hours))]
    for i, f in (flags or {}).items():
        out[i]["flags"] = f.split()
    return out

def J(c, t, method, url, body=None):
    return getattr(c, method)(url, json=body, headers={"X-CSRF-Token": t})

# ---------------- upgrade of an old database
import shutil
upg = os.path.join(TMP, "upg.db"); shutil.copy(OLD, upg)
A.DB_PATH = upg; A.init_db()
with sqlite3.connect(upg) as u:
    row = u.execute("SELECT s.ruleset_id, r.name FROM settings s JOIN rulesets r ON r.id=s.ruleset_id").fetchone()
    assert row[1] == "Arkema", row
    assert u.execute("SELECT rules_json FROM paystubs").fetchone()[0] is None   # legacy stub has no snapshot
with sqlite3.connect(upg) as u:
    u.execute("UPDATE rulesets SET rules_json='{}' WHERE name='Arkema'")   # simulate a stale built-in
A.DB_PATH = upg; A.init_db()
with sqlite3.connect(upg) as u:
    fresh = json.loads(u.execute("SELECT rules_json FROM rulesets WHERE name='Arkema'").fetchone()[0])
    assert fresh["consecutive"]["restart"] is True and fresh["codes"][3]["needs_work"] is True
A.DB_PATH = os.environ["WH_DB"]; A.init_db()

a = client(); ta = register(a, "alice")
b = client(); tb = register(b, "bob")

# ---------------- auth / csrf
assert client().get("/api/paystubs").status_code == 401
assert client().get("/api/rulesets").status_code == 401
assert client().get("/").status_code == 302
assert a.post("/api/banks", json={"name": "x"}).status_code == 400

# ---------------- shared library
lib = J(a, ta, "get", "/api/rulesets").get_json()
names = {r["name"]: r for r in lib}
assert "Arkema" in names and names["Arkema"]["builtin"] and not names["Arkema"]["mine"]
ark = J(a, ta, "get", f"/api/rulesets/{names['Arkema']['id']}").get_json()
assert any("7th consecutive day" in s for s in ark["summary"])

# built-ins can't be edited by anyone
assert J(a, ta, "put", f"/api/rulesets/{ark['id']}", {"name": "Hack", "rules": ark["rules"]}).status_code == 403
assert J(a, ta, "delete", f"/api/rulesets/{ark['id']}").status_code == 403

# alice creates one; bob sees it but cannot change it; bob can copy it
mine = {"name": "Acme Corp", "description": "Daily OT", "rules": dict(R.BLANK, daily_ot={"after": 8, "multiplier": 1.5},
        codes=[{"label": "Hol", "behavior": "fixed_hours", "multiplier": 1, "hours": 8, "counts": False}])}
acme = J(a, ta, "post", "/api/rulesets", mine).get_json()["id"]
bob_view = {r["name"]: r for r in J(b, tb, "get", "/api/rulesets").get_json()}
assert bob_view["Acme Corp"]["owner"] == "alice" and not bob_view["Acme Corp"]["mine"]
assert J(b, tb, "put", f"/api/rulesets/{acme}", mine).status_code == 403
assert J(b, tb, "delete", f"/api/rulesets/{acme}").status_code == 403
assert J(b, tb, "post", "/api/rulesets", mine).status_code == 400            # duplicate name
assert J(b, tb, "post", "/api/rulesets", dict(mine, name="Acme Corp (Bob)")).status_code == 200
assert J(a, ta, "post", "/api/rulesets", dict(mine, name="Bad", rules={"week_start": 12})).status_code == 400

# preview endpoint
pv = J(a, ta, "post", "/api/rulesets/preview", {"rules": mine["rules"]}).get_json()
assert any("Daily" not in s and "beyond 8" in s for s in pv["summary"])

# ---------------- paystub with a rule set
J(a, ta, "post", "/api/banks", {"name": "Checking 1111"})
bank = J(a, ta, "get", "/api/banks").get_json()[0]["id"]
J(a, ta, "post", "/api/deductions", {"name": "Health", "kind": "pre", "mode": "flat", "amount": "50", "reduces_fica": True})
ded = [d["id"] for d in J(a, ta, "get", "/api/deductions").get_json()]

body = {"pay_date": "2026-11-20", "period_start": "2026-11-02", "period_end": "2026-11-15", "ruleset_id": ark["id"],
        "rate": "42.08", "fed_pct": "20", "ss_pct": "6.2", "med_pct": "1.45", "deduction_ids": [],
        "days": days([0, 0, 12, 12, 12, 12, 12] + [12] * 7, {2: "ot", 3: "ot", 8: "dt", 9: "ot", 10: "ot"})}
r = J(a, ta, "post", "/api/calculate", body).get_json()
assert r["gross"] == 7742.72 and r["threshold"] == 40 and r["ruleset_name"] == "Arkema"

assert J(a, ta, "post", "/api/calculate", dict(body, ruleset_id=None)).status_code == 400   # must choose rules
assert J(a, ta, "post", "/api/calculate", dict(body, ruleset_id=99999)).status_code == 400
# unknown / forged code keys are ignored, not trusted
forged = dict(body, days=days([8], {0: "hacker ot"}))
assert J(a, ta, "post", "/api/calculate", forged).status_code == 200

# save with split must equal net
body["deduction_ids"] = ded
net = J(a, ta, "post", "/api/calculate", body).get_json()["net"]
assert J(a, ta, "post", "/api/paystubs", dict(body, splits=[{"bank_id": bank, "amount": "100"}])).status_code == 400
sid = J(a, ta, "post", "/api/paystubs", dict(body, splits=[{"bank_id": bank, "amount": str(net)}])).get_json()["id"]
lst = J(a, ta, "get", "/api/paystubs").get_json()
assert len(lst) == 1 and lst[0]["ruleset_name"] == "Arkema"
full = J(a, ta, "get", f"/api/paystubs/{sid}").get_json()
assert full["rules"]["weekly"]["threshold"] == 40 and full["days"][2]["flags"] == ["ot"]

# ---------------- streak carries from the previous paystub
sg = J(a, ta, "get", "/api/streak?period_start=2026-11-16").get_json()
assert sg["streak"] == 5 and sg["source"] == "2026-11-20"         # Tue was the 7th day (restart), then Wed-Sun worked
assert J(a, ta, "get", "/api/streak?period_start=2026-12-01").get_json() == {"source": None, "streak": 0}
assert J(a, ta, "get", "/api/streak?period_start=nope").status_code == 400
assert J(b, tb, "get", "/api/streak?period_start=2026-11-16").get_json()["streak"] == 0   # Bob can't see Alice's
nb = dict(body, deduction_ids=[], days=days([8]), streak_before=6, period_start="2026-11-16", period_end="2026-11-29")
assert J(a, ta, "post", "/api/calculate", nb).get_json()["gross"] == 8 * 2 * 42.08            # 7th day in a row: 2x
assert J(a, ta, "post", "/api/calculate", dict(nb, streak_before=5)).get_json()["gross"] == 8 * 42.08
for bad in (-1, 2.5, "x", 99):
    assert J(a, ta, "post", "/api/calculate", dict(nb, streak_before=bad)).status_code == 400, bad
sv = J(a, ta, "post", "/api/paystubs", dict(nb, splits=[])).get_json()["id"]
assert J(a, ta, "get", f"/api/paystubs/{sv}").get_json()["streak_before"] == 6
J(a, ta, "delete", f"/api/paystubs/{sv}")

# ---------------- snapshot: editing/deleting the library entry never changes saved paystubs
snap_body = dict(body, ruleset_id="snapshot", stub_id=sid)
assert J(a, ta, "post", "/api/calculate", snap_body).get_json()["gross"] == 7742.72
# alice's own rule set: save a stub, then change and delete the rule set
ab = dict(body, ruleset_id=acme, days=days([10, 10], {}), deduction_ids=[])
s2 = J(a, ta, "post", "/api/paystubs", ab).get_json()["id"]
g0 = J(a, ta, "post", "/api/calculate", dict(ab, ruleset_id="snapshot", stub_id=s2)).get_json()["gross"]
upd = dict(mine, rules=dict(mine["rules"], daily_ot=None))
assert J(a, ta, "put", f"/api/rulesets/{acme}", upd).status_code == 200
assert J(a, ta, "post", "/api/calculate", dict(ab, ruleset_id="snapshot", stub_id=s2)).get_json()["gross"] == g0
assert J(a, ta, "post", "/api/calculate", dict(ab, ruleset_id=acme)).get_json()["gross"] != g0   # live rules differ
assert J(a, ta, "delete", f"/api/rulesets/{acme}").status_code == 200
assert J(a, ta, "post", "/api/calculate", dict(ab, ruleset_id="snapshot", stub_id=s2)).get_json()["gross"] == g0
assert J(a, ta, "put", f"/api/paystubs/{s2}", dict(ab, ruleset_id="snapshot", splits=[])).status_code == 200

# ---------------- yearly summary
sm = J(a, ta, "get", "/api/summary?year=2026").get_json()
assert sm["year"] == 2026 and sm["years"] == [2026] and sm["count"] == 2 and sm["missing_breakdown"] == 0
T = sm["totals"]
assert round(T["gross"] - (T["fed"] + T["ss"] + T["med"] + T["pre"] + T["post"]), 2) == round(T["net"], 2)
assert round(T["deductions"], 2) == round(T["gross"] - T["net"], 2)
assert round(sum(m["gross"] for m in sm["by_month"]), 2) == round(T["gross"], 2)
assert round(sum(m["net"] for m in sm["by_month"]), 2) == round(T["net"], 2)
assert sm["by_month"][10]["count"] == 2 and sm["by_month"][0]["count"] == 0       # both paid in November
assert round(sum(x["mult"] * x["hours"] for x in sm["hours_by_rate"]), 2) > 0
assert round(sum(x["hours"] for x in sm["hours_by_rate"]), 2) == round(T["hours"], 2)
assert [d["name"] for d in sm["deductions"]] == ["Health"] and sm["deductions"][0]["kind"] == "pre"
assert {e["name"] for e in sm["by_employer"]} == {"Arkema", "Acme Corp"}
assert J(a, ta, "get", "/api/summary?year=2025").get_json()["count"] == 0
assert J(a, ta, "get", "/api/summary?year=abc").status_code == 400
assert J(b, tb, "get", "/api/summary?year=2026").get_json()["count"] == 0          # Bob sees none of Alice's
# an older paystub (saved before the breakdown existed) still counts in totals and is flagged
with sqlite3.connect(os.environ["WH_DB"]) as raw:
    uid_a = raw.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    raw.execute("""INSERT INTO paystubs(user_id,pay_date,period_start,period_end,rate,days_json,total_hours,gross,deductions_total,net)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""", (uid_a, "2026-03-06", "2026-02-16", "2026-03-01", "42.08", "[]", "80", "3366.40", "500.00", "2866.40"))
old = J(a, ta, "get", "/api/summary?year=2026").get_json()
assert old["count"] == 3 and old["missing_breakdown"] == 1 and round(old["totals"]["gross"] - T["gross"], 2) == 3366.40
with sqlite3.connect(os.environ["WH_DB"]) as raw:
    raw.execute("DELETE FROM paystubs WHERE pay_date='2026-03-06'")

# ---------------- settings store the chosen rule set
st = J(a, ta, "put", "/api/settings", {"hourly_rate": "42.08", "fed_pct": "10", "ss_pct": "6.2", "med_pct": "1.45", "ruleset_id": ark["id"]}).get_json()
assert st["ruleset_id"] == ark["id"]
assert J(a, ta, "put", "/api/settings", {"hourly_rate": "1", "fed_pct": "1", "ss_pct": "1", "med_pct": "1", "ruleset_id": 424242}).status_code == 400

# ---------------- per-user isolation still holds
assert J(b, tb, "get", "/api/paystubs").get_json() == []
assert J(b, tb, "get", f"/api/paystubs/{sid}").status_code == 404
assert J(b, tb, "put", f"/api/paystubs/{sid}", body).status_code == 404
J(b, tb, "delete", f"/api/paystubs/{sid}")
assert len(J(a, ta, "get", "/api/paystubs").get_json()) == 2
assert J(b, tb, "post", "/api/calculate", dict(body, ruleset_id="snapshot", stub_id=sid)).status_code == 400  # can't borrow alice's snapshot
assert J(b, tb, "post", "/api/calculate", body).status_code == 400  # alice's deduction ids
clean = dict(body, deduction_ids=[], ruleset_id="snapshot", stub_id=sid)
rb = J(b, tb, "post", "/api/calculate", clean)
assert rb.status_code == 400 and "Choose an employer" in rb.get_json()["error"]   # Bob has no snapshot for that id

# ---------------- printable summary
assert client().get("/summary/print?year=2026").status_code == 302                      # must be signed in
pr = a.get("/summary/print?year=2026")
html = pr.get_data(as_text=True)
assert pr.status_code == 200 and "Yearly pay summary 2026" in html and "Prepared for alice" in html
assert "{:,.2f}".format(sm["totals"]["gross"]) in html and "{:,.2f}".format(sm["totals"]["net"]) in html
assert "Paystub detail" in html and "Arkema" in html
assert "autoprint" not in html and 'name="autoprint"' in a.get("/summary/print?year=2026&autoprint=1").get_data(as_text=True)
assert a.get("/summary/print?year=abc").status_code == 400
empty = a.get("/summary/print?year=2019").get_data(as_text=True)
assert "No paystubs with a pay date in 2019" in empty
bp = b.get("/summary/print?year=2026").get_data(as_text=True)
assert "Prepared for bob" in bp and "Arkema" not in bp and "Paystub detail" not in bp   # Bob sees none of Alice's data
# names are escaped, never injected as HTML
evil = J(a, ta, "post", "/api/rulesets", dict(mine, name="X<i>&Co", rules=dict(mine["rules"]))).get_json()["id"]
J(a, ta, "post", "/api/paystubs", dict(ab, ruleset_id=evil, pay_date="2026-12-04", period_start="2026-11-16", period_end="2026-11-29", splits=[]))
h2 = a.get("/summary/print?year=2026").get_data(as_text=True)
assert "X&lt;i&gt;&amp;Co" in h2 and "<i>" not in h2
assert 'src="/static/print.js"' in h2 and "<style" not in h2 and " onclick" not in h2     # fits the strict CSP

# ---------------- optional invitation code for sign-ups, with lockout
A.REGISTER_CODE = "letmein"
c1 = client(); t1 = token(c1, "/register")
assert b"Invitation code" in c1.get("/register").data
good = {"username": "carol", "password": "correct-horse-1", "confirm": "correct-horse-1", "csrf_token": t1}
assert b"not valid" in c1.post("/register", data=dict(good, code="nope")).data
assert c1.post("/register", data=dict(good, code="letmein")).status_code == 302
c2 = client(); t2 = token(c2, "/register")
for _ in range(5):
    c2.post("/register", data=dict(good, username="dave", csrf_token=t2, code="guess"))
assert b"Too many attempts" in c2.post("/register", data=dict(good, username="dave", csrf_token=t2, code="letmein")).data
A.REGISTER_CODE = ""

# ---------------- validation + lockout
assert J(a, ta, "post", "/api/calculate", dict(body, days=days([25]))).status_code == 400
assert J(a, ta, "post", "/api/calculate", dict(body, rate="abc")).status_code == 400
c = client(); t = token(c)
for _ in range(5):
    c.post("/login", data={"username": "alice", "password": "nope", "csrf_token": t})
assert b"Too many attempts" in c.post("/login", data={"username": "alice", "password": "correct-horse-1", "csrf_token": t}).data
# ---------------- HTTPS header only when cookies are Secure
assert "Strict-Transport-Security" not in client().get("/login").headers
A.app.config["SESSION_COOKIE_SECURE"] = True
assert "max-age" in client().get("/login").headers["Strict-Transport-Security"]
A.app.config["SESSION_COOKIE_SECURE"] = False

# ---------------- behind nginx: lockout is per real client address (X-Forwarded-For), not per proxy
A.apply_proxy_fix(A.app)
def attempt(ip, pw):
    cc = client(); tt = token(cc)
    return cc.post("/login", data={"username": "alice", "password": pw, "csrf_token": tt}, headers={"X-Forwarded-For": ip})
for _ in range(5):
    attempt("203.0.113.9", "wrong")
assert b"Too many attempts" in attempt("203.0.113.9", "correct-horse-1").data           # attacker locked out
assert attempt("198.51.100.7", "correct-horse-1").status_code == 302                    # a different visitor is fine
# ---------------- real visitor address from the Cloudflare header
A.CLIENT_IP_HEADER = "CF-Connecting-IP"
def attempt_cf(ip, pw, user="alice"):
    cc = client(); tt = token(cc)
    h = {"CF-Connecting-IP": ip} if ip is not None else {}
    return cc.post("/login", data={"username": user, "password": pw, "csrf_token": tt}, headers=h)
for _ in range(5):
    attempt_cf("192.0.2.50", "wrong")
assert b"Too many attempts" in attempt_cf("192.0.2.50", "correct-horse-1").data         # that visitor is locked out
assert attempt_cf("192.0.2.51", "correct-horse-1").status_code == 302                    # a different visitor is not
for junk in ("not-an-ip", "999.1.1.1", "", "1.2.3.4, 5.6.7.8"):                          # junk never crashes it
    assert attempt_cf(junk, "correct-horse-1").status_code in (200, 302), junk
with A.app.test_request_context("/", headers={"CF-Connecting-IP": "2001:db8::1"}):
    assert A.client_ip() == "2001:db8::1"                                                # IPv6 works
with A.app.test_request_context("/", headers={"CF-Connecting-IP": "evil"}, environ_overrides={"REMOTE_ADDR": "10.1.1.1"}):
    assert A.client_ip() == "10.1.1.1"                                                   # invalid header: fall back

# ---------------- per-account lockout: guessing from many addresses still gets stopped
A._fails.pop(("127.0.0.1", "register"), None)   # clear the lock the invitation-code test caused on purpose
register(client(), "erin")
for i in range(15):
    attempt_cf("198.18.0.%d" % (i + 1), "guess-%d" % i, user="erin")
blocked = attempt_cf("198.18.9.9", "correct-horse-1", user="erin")
assert b"Too many attempts" in blocked.data                                              # even the right password, new address
assert attempt_cf("198.18.9.9", "correct-horse-1", user="alice").status_code == 302      # other accounts are unaffected
A._fails[("account", "erin")][1] = 0                                                     # lock expires
assert attempt_cf("198.18.9.10", "correct-horse-1", user="erin").status_code == 302
assert ("account", "erin") not in A._fails                                               # success clears the counter
A.CLIENT_IP_HEADER = ""

# ---------------- data folder is configurable (for a Docker volume)
import subprocess, sys
r = subprocess.run([sys.executable, "-c", "import app, os; print(app.INSTANCE, app.DB_PATH, os.path.exists(os.path.join(app.INSTANCE,'secret_key')))"],
                   cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True, text=True,
                   env={**os.environ, "WH_INSTANCE": os.path.join(TMP, "vol"), "WH_DB": ""})
inst, dbp, has_key = r.stdout.split()
assert inst == os.path.join(TMP, "vol") and dbp == os.path.join(TMP, "vol", "workhours.db") and has_key == "True", r.stdout + r.stderr
print("all backend checks passed")
