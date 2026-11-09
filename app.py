"""Work Hours Tracker - Flask + SQLite. Run: python app.py  ->  http://127.0.0.1:5000"""
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from functools import wraps

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import calc
import rules as R

BASE = os.path.dirname(os.path.abspath(__file__))
INSTANCE = os.environ.get("WH_INSTANCE") or os.path.join(BASE, "instance")   # database + secret key live here
os.makedirs(INSTANCE, exist_ok=True)
DB_PATH = os.environ.get("WH_DB") or os.path.join(INSTANCE, "workhours.db")   # empty counts as unset
ALLOW_REGISTRATION = os.environ.get("WH_ALLOW_REGISTRATION", "1") == "1"
REGISTER_CODE = os.environ.get("WH_REGISTER_CODE", "")   # if set, sign-up also needs this invitation code
BEHIND_PROXY = os.environ.get("WH_BEHIND_PROXY", "0") == "1"
# Header that carries the real visitor address, e.g. CF-Connecting-IP behind a Cloudflare Tunnel. Only set this when
# the app is reachable ONLY through Cloudflare/your proxy, because the header is trusted as given.
CLIENT_IP_HEADER = os.environ.get("WH_CLIENT_IP_HEADER", "")


def _secret():
    path = os.path.join(INSTANCE, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return open(path).read().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("WH_SECRET_KEY") or _secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("WH_HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    MAX_CONTENT_LENGTH=256 * 1024,
)

def apply_proxy_fix(flask_app):
    """Trust ONE proxy hop (nginx) for the client IP, scheme and host. Only turn this on when the app is reachable
    exclusively through that proxy, otherwise clients could forge these headers."""
    flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=1, x_proto=1, x_host=1)


if BEHIND_PROXY:
    apply_proxy_fix(app)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings(
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  company_name TEXT DEFAULT '', hourly_rate TEXT DEFAULT '0.00',
  fed_pct TEXT DEFAULT '10.00', ss_pct TEXT DEFAULT '6.20', med_pct TEXT DEFAULT '1.45',
  ruleset_id INTEGER REFERENCES rulesets(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS rulesets(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, description TEXT DEFAULT '',
  rules_json TEXT NOT NULL, builtin INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS banks(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deductions(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN('pre','post')),
  mode TEXT NOT NULL CHECK(mode IN('flat','percent')), amount TEXT NOT NULL,
  reduces_fica INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS paystubs(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  pay_date TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  rate TEXT NOT NULL, fed_pct TEXT, ss_pct TEXT, med_pct TEXT,
  total_hours TEXT, gross TEXT, deductions_total TEXT, net TEXT,
  days_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  ruleset_id INTEGER REFERENCES rulesets(id) ON DELETE SET NULL, ruleset_name TEXT, rules_json TEXT,
  streak_before INTEGER NOT NULL DEFAULT 0,
  fed_amt TEXT, ss_amt TEXT, med_amt TEXT, pre_total TEXT, post_total TEXT, buckets_json TEXT);
CREATE INDEX IF NOT EXISTS ix_paystubs_user_date ON paystubs(user_id, pay_date);
CREATE TABLE IF NOT EXISTS paystub_deductions(
  id INTEGER PRIMARY KEY, paystub_id INTEGER NOT NULL REFERENCES paystubs(id) ON DELETE CASCADE,
  deduction_id INTEGER, name TEXT, kind TEXT, amount TEXT);
CREATE TABLE IF NOT EXISTS paystub_splits(
  id INTEGER PRIMARY KEY, paystub_id INTEGER NOT NULL REFERENCES paystubs(id) ON DELETE CASCADE,
  bank_id INTEGER, bank_name TEXT, amount TEXT);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def _ensure_column(c, table, column, ddl):
    if column not in [r[1] for r in c.execute("PRAGMA table_info(%s)" % table)]:
        c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))


def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA)
        # Upgrade databases created before employer rule sets existed.
        _ensure_column(c, "settings", "ruleset_id", "INTEGER REFERENCES rulesets(id) ON DELETE SET NULL")
        _ensure_column(c, "paystubs", "ruleset_id", "INTEGER REFERENCES rulesets(id) ON DELETE SET NULL")
        _ensure_column(c, "paystubs", "ruleset_name", "TEXT")
        _ensure_column(c, "paystubs", "rules_json", "TEXT")
        _ensure_column(c, "paystubs", "streak_before", "INTEGER NOT NULL DEFAULT 0")
        for col in ("fed_amt", "ss_amt", "med_amt", "pre_total", "post_total", "buckets_json"):
            _ensure_column(c, "paystubs", col, "TEXT")
        for name, desc, rules in R.BUILTINS:
            cleaned = json.dumps(R.validate_rules(rules))
            # Built-ins are maintained by the app, so refresh them on every start; saved paystubs keep their own copy.
            c.execute("UPDATE rulesets SET description=?, rules_json=? WHERE name=? AND builtin=1", (desc, cleaned, name))
            c.execute("INSERT OR IGNORE INTO rulesets(name,description,rules_json,builtin) VALUES(?,?,?,1)",
                      (name, desc, cleaned))
        # Existing users (from before rule sets) were all on the Arkema rules.
        c.execute("""UPDATE settings SET ruleset_id=(SELECT id FROM rulesets WHERE name='Arkema' AND builtin=1)
                     WHERE ruleset_id IS NULL
                       AND EXISTS (SELECT 1 FROM paystubs p WHERE p.user_id=settings.user_id)""")


init_db()

# ---------------------------------------------------------------- security helpers

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Bad CSRF token")


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if app.config["SESSION_COOKIE_SECURE"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "uid" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="Not signed in"), 401
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


_fails = {}  # (ip, username) -> [count, locked_until]


def client_ip():
    """The visitor's address for lockouts. Uses CLIENT_IP_HEADER when configured and valid, else the connection's."""
    if CLIENT_IP_HEADER:
        raw = (request.headers.get(CLIENT_IP_HEADER) or "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
    return request.remote_addr


def locked(key):
    f = _fails.get(key)
    return bool(f and f[1] > time.time())


def record_fail(key, limit=5):
    f = _fails.setdefault(key, [0, 0])
    f[0] += 1
    if f[0] >= limit:
        f[1] = time.time() + 300
        f[0] = 0


# ---------------------------------------------------------------- auth pages

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        key = (client_ip(), u.lower())
        acct = ("account", u.lower())   # guessing spread over many addresses still adds up against one account
        if locked(key) or locked(acct):
            error = "Too many attempts. Try again in a few minutes."
        else:
            row = db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                _fails.pop(key, None)
                _fails.pop(acct, None)
                session.clear()
                session["uid"] = row["id"]
                session["username"] = row["username"]
                session.permanent = True
                return redirect(url_for("index"))
            record_fail(key)
            record_fail(acct, limit=15)
            error = "Invalid username or password."
    return render_template("login.html", error=error, can_register=ALLOW_REGISTRATION)


@app.route("/register", methods=["GET", "POST"])
def register():
    if not ALLOW_REGISTRATION:
        abort(404)
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        reg_key = (client_ip(), "register")
        if locked(reg_key):
            error = "Too many attempts. Try again in a few minutes."
        elif REGISTER_CODE and not secrets.compare_digest(request.form.get("code", "").encode(), REGISTER_CODE.encode()):
            record_fail(reg_key)
            error = "That invitation code is not valid."
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", u):
            error = "Username: 3-32 letters, numbers, _ . -"
        elif len(p) < 10:
            error = "Password must be at least 10 characters."
        elif p != request.form.get("confirm", ""):
            error = "Passwords do not match."
        else:
            try:
                cur = db().execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                                   (u, generate_password_hash(p)))
                db().execute("INSERT INTO settings(user_id) VALUES(?)", (cur.lastrowid,))
                db().commit()
                session.clear()
                session["uid"] = cur.lastrowid
                session["username"] = u
                session.permanent = True
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                error = "That username is taken."
    return render_template("register.html", error=error, need_code=bool(REGISTER_CODE))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html", username=session["username"])


# ---------------------------------------------------------------- helpers

def uid():
    return session["uid"]


def jbody():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "JSON body required")
    return data


def num(v, lo=None, hi=None, field="value"):
    try:
        x = D(str(v).replace("$", "").replace(",", "").strip())
    except Exception:
        raise ValueError("%s must be a number" % field)
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError("%s is out of range" % field)
    return x


def iso(v, field):
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("%s must be a date (YYYY-MM-DD)" % field)


@app.errorhandler(ValueError)
def bad_value(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(400)
def bad_request(e):
    if request.path.startswith("/api/"):
        return jsonify(error=getattr(e, "description", "Bad request")), 400
    return e


# ---------------------------------------------------------------- settings / banks / deductions

@app.get("/api/settings")
@login_required
def get_settings():
    return jsonify(dict(db().execute("SELECT * FROM settings WHERE user_id=?", (uid(),)).fetchone()))


@app.put("/api/settings")
@login_required
def put_settings():
    b = jbody()
    rs = b.get("ruleset_id")
    if rs in (None, ""):
        rs = None
    else:
        rs = int(rs)
        if not db().execute("SELECT 1 FROM rulesets WHERE id=?", (rs,)).fetchone():
            raise ValueError("That rule set does not exist")
    vals = (str(num(b.get("hourly_rate"), 0, 10000, "Hourly rate")),
            str(num(b.get("fed_pct"), 0, 100, "Federal %")),
            str(num(b.get("ss_pct"), 0, 100, "Social Security %")),
            str(num(b.get("med_pct"), 0, 100, "Medicare %")), rs, uid())
    db().execute("UPDATE settings SET hourly_rate=?,fed_pct=?,ss_pct=?,med_pct=?,ruleset_id=? WHERE user_id=?", vals)
    db().commit()
    return get_settings()


@app.get("/api/banks")
@login_required
def list_banks():
    rows = db().execute("SELECT id,name FROM banks WHERE user_id=? ORDER BY name", (uid(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/banks")
@login_required
def add_bank():
    name = str(jbody().get("name", "")).strip()[:80]
    if not name:
        raise ValueError("Name is required")
    db().execute("INSERT INTO banks(user_id,name) VALUES(?,?)", (uid(), name))
    db().commit()
    return list_banks()


@app.delete("/api/banks/<int:i>")
@login_required
def del_bank(i):
    db().execute("DELETE FROM banks WHERE id=? AND user_id=?", (i, uid()))
    db().commit()
    return list_banks()


def ded_clean(b):
    name = str(b.get("name", "")).strip()[:80]
    if not name:
        raise ValueError("Name is required")
    if b.get("kind") not in ("pre", "post") or b.get("mode") not in ("flat", "percent"):
        raise ValueError("Invalid deduction type")
    amt = num(b.get("amount"), 0, 100000 if b["mode"] == "flat" else 100, "Amount")
    return name, b["kind"], b["mode"], str(amt), 1 if b.get("reduces_fica") and b["kind"] == "pre" else 0


@app.get("/api/deductions")
@login_required
def list_deductions():
    rows = db().execute("SELECT * FROM deductions WHERE user_id=? AND active=1 ORDER BY kind,name", (uid(),)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/deductions")
@login_required
def add_deduction():
    db().execute("INSERT INTO deductions(user_id,name,kind,mode,amount,reduces_fica) VALUES(?,?,?,?,?,?)",
                 (uid(), *ded_clean(jbody())))
    db().commit()
    return list_deductions()


@app.put("/api/deductions/<int:i>")
@login_required
def edit_deduction(i):
    db().execute("UPDATE deductions SET name=?,kind=?,mode=?,amount=?,reduces_fica=? WHERE id=? AND user_id=?",
                 (*ded_clean(jbody()), i, uid()))
    db().commit()
    return list_deductions()


@app.delete("/api/deductions/<int:i>")
@login_required
def del_deduction(i):
    # Soft delete so old paystubs that used it can still be reopened.
    db().execute("UPDATE deductions SET active=0 WHERE id=? AND user_id=?", (i, uid()))
    db().commit()
    return list_deductions()


# ---------------------------------------------------------------- employer rule sets (shared library)

def _rs_meta(r):
    return {"id": r["id"], "name": r["name"], "description": r["description"], "builtin": bool(r["builtin"]),
            "owner": "Built-in" if r["builtin"] else (r["owner"] or "Removed user"),
            "mine": (not r["builtin"]) and r["created_by"] == uid()}


RS_SELECT = "SELECT r.*, u.username AS owner FROM rulesets r LEFT JOIN users u ON u.id=r.created_by"


@app.get("/api/rulesets")
@login_required
def list_rulesets():
    rows = db().execute(RS_SELECT + " ORDER BY r.builtin DESC, r.name").fetchall()
    return jsonify([_rs_meta(r) for r in rows])


@app.get("/api/rulesets/<int:i>")
@login_required
def get_ruleset(i):
    r = db().execute(RS_SELECT + " WHERE r.id=?", (i,)).fetchone()
    if not r:
        abort(404)
    rules = R.validate_rules(json.loads(r["rules_json"]))
    return jsonify({**_rs_meta(r), "rules": rules, "summary": R.describe(rules)})


@app.post("/api/rulesets/preview")
@login_required
def preview_ruleset():
    rules = R.validate_rules(jbody().get("rules"))
    return jsonify(rules=rules, summary=R.describe(rules))


def _rs_fields(b):
    name = str(b.get("name", "")).strip()
    if not name or len(name) > 60:
        raise ValueError("Give the rule set a name (up to 60 characters)")
    return name, str(b.get("description", "")).strip()[:200], R.validate_rules(b.get("rules"))


@app.post("/api/rulesets")
@login_required
def create_ruleset():
    name, desc, rules = _rs_fields(jbody())
    try:
        cur = db().execute("INSERT INTO rulesets(name,description,rules_json,created_by) VALUES(?,?,?,?)",
                           (name, desc, json.dumps(rules), uid()))
    except sqlite3.IntegrityError:
        raise ValueError('A rule set named "%s" already exists in the library' % name)
    db().commit()
    return jsonify(id=cur.lastrowid)


def _own_ruleset(i):
    r = db().execute("SELECT * FROM rulesets WHERE id=?", (i,)).fetchone()
    if not r:
        abort(404)
    if r["builtin"] or r["created_by"] != uid():
        return None
    return r


@app.put("/api/rulesets/<int:i>")
@login_required
def update_ruleset(i):
    if not _own_ruleset(i):
        return jsonify(error="Only the person who created a rule set can change it. Duplicate it to make your own."), 403
    name, desc, rules = _rs_fields(jbody())
    try:
        db().execute("UPDATE rulesets SET name=?,description=?,rules_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (name, desc, json.dumps(rules), i))
    except sqlite3.IntegrityError:
        raise ValueError('A rule set named "%s" already exists in the library' % name)
    db().commit()
    return jsonify(id=i)


@app.delete("/api/rulesets/<int:i>")
@login_required
def delete_ruleset(i):
    if not _own_ruleset(i):
        return jsonify(error="Only the person who created a rule set can delete it."), 403
    # Paystubs keep their own snapshot of the rules, so they are unaffected.
    db().execute("DELETE FROM rulesets WHERE id=?", (i,))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- paystubs

def load_rules(b, stub_id):
    """Pick the rules for a paystub: its saved snapshot, or a rule set from the shared library.
    Returns (ruleset_id, ruleset_name, cleaned_rules)."""
    rs = b.get("ruleset_id")
    if rs == "snapshot":
        row = None
        if stub_id:
            row = db().execute("SELECT ruleset_id, ruleset_name, rules_json FROM paystubs WHERE id=? AND user_id=?",
                               (stub_id, uid())).fetchone()
        if row and row["rules_json"]:
            return row["ruleset_id"], row["ruleset_name"], R.validate_rules(json.loads(row["rules_json"]))
        rs = db().execute("SELECT ruleset_id FROM settings WHERE user_id=?", (uid(),)).fetchone()["ruleset_id"]
    try:
        rs = int(rs)
    except (TypeError, ValueError):
        raise ValueError("Choose an employer rule set first")
    row = db().execute("SELECT id,name,rules_json FROM rulesets WHERE id=?", (rs,)).fetchone()
    if not row:
        raise ValueError("That rule set no longer exists")
    return row["id"], row["name"], R.validate_rules(json.loads(row["rules_json"]))


def day_flags(d):
    if isinstance(d.get("flags"), list):
        return d["flags"]
    return [k for k in ("ot", "dt", "vac", "hol") if d.get(k)]  # paystubs saved before rule sets existed


def parse_stub(b, stub_id=None):
    """Validate payload and run the calculation. Returns (clean_fields, result)."""
    days = b.get("days")
    if not isinstance(days, list) or len(days) != 14:
        raise ValueError("Expected 14 days of hours")
    rs_id, rs_name, rules = load_rules(b, stub_id)
    keys = {c["key"] for c in rules["codes"]}
    clean_days = []
    for d in days:
        if not isinstance(d, dict):
            raise ValueError("Bad day entry")
        clean_days.append({"hours": str(num(d.get("hours") or 0, 0, 24, "Hours")),
                           "flags": [f for f in day_flags(d) if isinstance(f, str) and f in keys]})
    rate = num(b.get("rate"), 0, 10000, "Hourly rate")
    fed, ss, med = (num(b.get(k), 0, 100, k) for k in ("fed_pct", "ss_pct", "med_pct"))
    ids = [int(x) for x in (b.get("deduction_ids") or [])]
    deds = []
    for i in ids:
        r = db().execute("SELECT * FROM deductions WHERE id=? AND user_id=?", (i, uid())).fetchone()
        if not r:
            raise ValueError("Unknown deduction selected")
        deds.append(dict(r))
    streak = num(b.get("streak_before") or 0, 0, 60, "Days worked in a row before this period")
    if streak != int(streak):
        raise ValueError("Days worked in a row must be a whole number")
    streak = int(streak)
    res = calc.calc_paystub(clean_days, rate, fed, ss, med, deds, rules, streak)
    for d, row in zip(deds, res["deduction_rows"]):
        row["deduction_id"] = d["id"]
    res["threshold"] = rules["weekly"]["threshold"] if rules["weekly"] else None
    res["ruleset_name"] = rs_name
    return {"days": clean_days, "rate": rate, "fed": fed, "ss": ss, "med": med,
            "ruleset_id": rs_id, "ruleset_name": rs_name, "rules": rules, "streak_before": streak}, res


def jsonable(o):
    if isinstance(o, D):
        return float(o)
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [jsonable(v) for v in o]
    return o


@app.post("/api/calculate")
@login_required
def api_calculate():
    b = jbody()
    sid = b.get("stub_id")
    _, res = parse_stub(b, int(sid) if sid not in (None, "") else None)
    return jsonify(jsonable(res))


def save_stub(stub_id=None):
    if stub_id is not None and not db().execute(
            "SELECT 1 FROM paystubs WHERE id=? AND user_id=?", (stub_id, uid())).fetchone():
        abort(404)
    b = jbody()
    fields, res = parse_stub(b, stub_id)
    pay_date = iso(b.get("pay_date"), "Pay date")
    ps, pe = iso(b.get("period_start"), "Period start"), iso(b.get("period_end"), "Period end")
    if pe < ps:
        raise ValueError("Period end is before period start")
    splits = b.get("splits") or []
    clean_splits, total = [], D(0)
    for s in splits:
        amt = calc.money(num(s.get("amount"), 0, 1000000, "Deposit amount"))
        bank = db().execute("SELECT id,name FROM banks WHERE id=? AND user_id=?",
                            (s.get("bank_id"), uid())).fetchone()
        if not bank:
            raise ValueError("Choose a bank for every split")
        clean_splits.append((bank["id"], bank["name"], str(amt)))
        total += amt
    if clean_splits and total != res["net"]:
        raise ValueError("Bank splits total $%s but net pay is $%s" % (total, res["net"]))

    c = db()
    cols = {
        "pay_date": pay_date, "period_start": ps, "period_end": pe, "rate": str(fields["rate"]),
        "fed_pct": str(fields["fed"]), "ss_pct": str(fields["ss"]), "med_pct": str(fields["med"]),
        "total_hours": str(res["total_hours"]), "gross": str(res["gross"]),
        "deductions_total": str(res["total_deductions"]), "net": str(res["net"]),
        "days_json": json.dumps(fields["days"]), "ruleset_id": fields["ruleset_id"],
        "ruleset_name": fields["ruleset_name"], "rules_json": json.dumps(fields["rules"]),
        "streak_before": fields["streak_before"],
        # breakdown kept so the yearly summary reports exactly what was saved
        "fed_amt": str(res["fed"]), "ss_amt": str(res["ss"]), "med_amt": str(res["med"]),
        "pre_total": str(res["pre_total"]), "post_total": str(res["post_total"]),
        "buckets_json": json.dumps([[str(x["mult"]), str(x["hours"])] for x in res["buckets"]]),
    }
    names = list(cols)
    if stub_id is None:
        stub_id = c.execute("INSERT INTO paystubs(%s,user_id) VALUES(%s)" % (",".join(names), ",".join("?" * (len(names) + 1))),
                            (*cols.values(), uid())).lastrowid
    else:
        c.execute("UPDATE paystubs SET %s WHERE id=? AND user_id=?" % ",".join(n + "=?" for n in names),
                  (*cols.values(), stub_id, uid()))
        c.execute("DELETE FROM paystub_deductions WHERE paystub_id=?", (stub_id,))
        c.execute("DELETE FROM paystub_splits WHERE paystub_id=?", (stub_id,))
    for r in res["deduction_rows"]:
        c.execute("INSERT INTO paystub_deductions(paystub_id,deduction_id,name,kind,amount) VALUES(?,?,?,?,?)",
                  (stub_id, r["deduction_id"], r["name"], r["kind"], str(r["amount"])))
    for s in clean_splits:
        c.execute("INSERT INTO paystub_splits(paystub_id,bank_id,bank_name,amount) VALUES(?,?,?,?)", (stub_id, *s))
    c.commit()
    return jsonify(id=stub_id)


@app.post("/api/paystubs")
@login_required
def create_stub():
    return save_stub()


@app.put("/api/paystubs/<int:i>")
@login_required
def update_stub(i):
    return save_stub(i)


@app.get("/api/paystubs")
@login_required
def list_stubs():
    q, args = "SELECT * FROM paystubs WHERE user_id=?", [uid()]
    for key, op in (("start", ">="), ("end", "<=")):
        if request.args.get(key):
            q += " AND period_start %s ?" % op
            args.append(iso(request.args[key], key))
    rows = db().execute(q + " ORDER BY pay_date DESC", args).fetchall()
    keep = ("id", "pay_date", "period_start", "period_end", "rate", "total_hours", "gross", "deductions_total", "net",
            "ruleset_name")
    return jsonify([{k: r[k] for k in keep} for r in rows])


def compute_summary(year_arg):
    """Totals for one tax year (by pay date, which is how wages are reported). Reads the breakdown saved with each paystub."""
    c = db()
    years = [int(r[0]) for r in c.execute(
        "SELECT DISTINCT substr(pay_date,1,4) FROM paystubs WHERE user_id=? ORDER BY 1 DESC", (uid(),))]
    try:
        year = int(year_arg or (years[0] if years else date.today().year))
    except ValueError:
        raise ValueError("Year must be a number")
    if not 1990 <= year <= 2200:
        raise ValueError("Year is out of range")
    rows = c.execute("SELECT * FROM paystubs WHERE user_id=? AND substr(pay_date,1,4)=? ORDER BY pay_date",
                     (uid(), "%04d" % year)).fetchall()
    tot = {k: D(0) for k in ("hours", "gross", "fed", "ss", "med", "pre", "post", "net")}
    months = {m: {"month": m, "count": 0, "hours": D(0), "gross": D(0), "deductions": D(0), "net": D(0)}
              for m in range(1, 13)}
    rate_hours, deds, emps, missing, stub_rows = {}, {}, {}, 0, []
    for r in rows:
        gross, net, hours = D(r["gross"]), D(r["net"]), D(r["total_hours"])
        stub_rows.append({"pay_date": r["pay_date"], "period_start": r["period_start"], "period_end": r["period_end"],
                          "employer": r["ruleset_name"] or "", "hours": hours, "gross": gross,
                          "deductions": gross - net, "net": net})
        tot["hours"] += hours; tot["gross"] += gross; tot["net"] += net
        m = months[int(r["pay_date"][5:7])]
        m["count"] += 1; m["hours"] += hours; m["gross"] += gross; m["deductions"] += gross - net; m["net"] += net
        e = emps.setdefault(r["ruleset_name"] or "Unknown", {"name": r["ruleset_name"] or "Unknown", "count": 0,
                                                             "hours": D(0), "gross": D(0), "net": D(0)})
        e["count"] += 1; e["hours"] += hours; e["gross"] += gross; e["net"] += net
        if r["fed_amt"] is None:
            missing += 1  # saved before the breakdown was kept
        else:
            for k, col in (("fed", "fed_amt"), ("ss", "ss_amt"), ("med", "med_amt"), ("pre", "pre_total"), ("post", "post_total")):
                tot[k] += D(r[col] or 0)
            for mult, h in json.loads(r["buckets_json"] or "[]"):
                key = format(D(mult).normalize(), "f")
                rate_hours[key] = rate_hours.get(key, D(0)) + D(h)
        for d in c.execute("SELECT name,kind,amount FROM paystub_deductions WHERE paystub_id=?", (r["id"],)):
            deds[(d["name"], d["kind"])] = deds.get((d["name"], d["kind"]), D(0)) + D(d["amount"])
    out = {
        "year": year, "years": years or [year], "count": len(rows), "missing_breakdown": missing,
        "totals": {**tot, "deductions": tot["gross"] - tot["net"]},
        "by_month": list(months.values()),
        "hours_by_rate": [{"mult": D(k), "hours": v} for k, v in sorted(rate_hours.items(), key=lambda kv: D(kv[0]))],
        "deductions": [{"name": n, "kind": k, "total": v} for (n, k), v in sorted(deds.items(), key=lambda kv: (kv[0][1] != "pre", kv[0][0]))],
        "by_employer": sorted(emps.values(), key=lambda e: -e["gross"]),
        "stubs": stub_rows,
    }
    return out


@app.get("/api/summary")
@login_required
def yearly_summary():
    return jsonify(jsonable(compute_summary(request.args.get("year"))))


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]


@app.template_filter("usd")
def _f_usd(v):
    v = D(str(v))
    return ("-$" if v < 0 else "$") + "{:,.2f}".format(abs(v))


@app.template_filter("hrs")
def _f_hrs(v):
    return format(D(str(v)).normalize(), "f")


@app.template_filter("mdy")
def _f_mdy(s):
    y, m, d = s.split("-")
    return "%d/%d/%s" % (int(m), int(d), y)


@app.get("/summary/print")
@login_required
def summary_print():
    """A clean, paper-sized version of the yearly summary. The browser's Print dialog can also Save as PDF."""
    try:
        s = compute_summary(request.args.get("year"))
    except ValueError:
        abort(400)
    return render_template("summary_print.html", s=s, username=session["username"], months=MONTH_NAMES,
                           today=date.today().isoformat(), autoprint=request.args.get("autoprint") == "1")


@app.get("/api/streak")
@login_required
def suggest_streak():
    """Days worked in a row at the end of the paystub that finishes the day before `period_start`."""
    start = iso(request.args.get("period_start"), "period_start")
    prev_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    row = db().execute("SELECT * FROM paystubs WHERE user_id=? AND period_end=? ORDER BY pay_date DESC LIMIT 1",
                       (uid(), prev_end)).fetchone()
    if not row:
        return jsonify(streak=0, source=None)
    rules = R.validate_rules(json.loads(row["rules_json"])) if row["rules_json"] else R.validate_rules(R.ARKEMA)
    days = [{"hours": d["hours"], "flags": day_flags(d)} for d in json.loads(row["days_json"])]
    return jsonify(streak=calc.end_streak(days, rules, row["streak_before"] or 0), source=row["pay_date"])


@app.get("/api/paystubs/<int:i>")
@login_required
def get_stub(i):
    r = db().execute("SELECT * FROM paystubs WHERE id=? AND user_id=?", (i, uid())).fetchone()
    if not r:
        abort(404)
    out = dict(r)
    out["days"] = [{"hours": d["hours"], "flags": day_flags(d)} for d in json.loads(out.pop("days_json"))]
    snap = out.pop("rules_json")
    out["rules"] = R.validate_rules(json.loads(snap)) if snap else None  # None: saved before rule sets existed
    out["deduction_ids"] = [x["deduction_id"] for x in db().execute(
        "SELECT deduction_id FROM paystub_deductions WHERE paystub_id=? AND deduction_id IS NOT NULL", (i,))]
    out["splits"] = [dict(x) for x in db().execute(
        "SELECT bank_id,bank_name,amount FROM paystub_splits WHERE paystub_id=?", (i,))]
    # Include deductions this stub used even if since removed, so editing still works.
    out["extra_deductions"] = [dict(x) for x in db().execute(
        """SELECT d.* FROM deductions d JOIN paystub_deductions p ON p.deduction_id=d.id
           WHERE p.paystub_id=? AND d.user_id=? AND d.active=0""", (i, uid()))]
    return jsonify(out)


@app.delete("/api/paystubs/<int:i>")
@login_required
def delete_stub(i):
    db().execute("DELETE FROM paystubs WHERE id=? AND user_id=?", (i, uid()))
    db().commit()
    return jsonify(ok=True)


if __name__ == "__main__":
    # Bound to localhost only. Put it behind HTTPS (and set WH_HTTPS=1) before exposing it to a network.
    app.run(host=os.environ.get("WH_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
