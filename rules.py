"""Employer rule sets: validation, plain-English description, and built-in presets.

A rule set is plain data (stored as JSON), so adding an employer never needs code changes:

{
  "week_start":  0,                                    # 0=Monday ... 6=Sunday (used for day labels / period check)
  "weekly":      {"threshold": 40, "multiplier": 1.5}  # or null: paid hours beyond threshold pay the multiplier
  "daily_ot":    {"after": 8,  "multiplier": 1.5}      # or null: on plain days, hours beyond `after`
  "daily_dt":    {"after": 12, "multiplier": 2}        # or null
  "consecutive": {"day": 7, "multiplier": 2, "counts": false, "restart": true},   # or null: Nth consecutive worked day
  "codes": [                                           # the checkboxes shown on each day
    {"key": "ot", "label": "OT", "behavior": "multiplier",  "multiplier": 1.5, "hours": 0, "counts": false},
    {"key": "vac","label": "Vac","behavior": "leave",       "multiplier": 1,   "hours": 0, "counts": true},
    {"key": "hol","label": "Hol","behavior": "fixed_hours", "multiplier": 1,   "hours": 8, "counts": true}
  ]
}

Code behaviors:
  multiplier   - all hours typed that day pay at the multiplier
  leave        - the hours typed are paid leave at the multiplier (the day is not a "worked" day)
  fixed_hours  - adds a fixed number of hours at the multiplier; hours typed that day pay normally
`counts` = whether those hours count toward the weekly threshold.
`needs_work` (fixed_hours only) = the fixed hours count toward the threshold only on a day hours were worked.
`restart` (consecutive) = after any day paid at the rule's rate or higher, the day count starts over, so the rule
fires on every Nth day (7, 14, 21...). Without it, only the Nth day in an unbroken run fires.
"""
import re
from decimal import Decimal as D

BEHAVIORS = ("multiplier", "leave", "fixed_hours")
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_CODES = 8
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")


def _num(v, lo, hi, label):
    try:
        x = D(str(v).strip())
    except Exception:
        raise ValueError("%s must be a number" % label)
    if not x.is_finite() or x < D(str(lo)) or x > D(str(hi)):
        raise ValueError("%s must be between %s and %s" % (label, lo, hi))
    return float(x)


def _slug_key(label, taken):
    base = re.sub(r"[^a-z0-9]", "", label.lower())[:8]
    if not base or not base[0].isalpha():
        base = "c" + base
    key, n = base, 2
    while key in taken or key == "hours":
        key = "%s%d" % (base, n)
        n += 1
    return key


def validate_rules(raw):
    """Return a cleaned copy of `raw` or raise ValueError with a message meant for the user."""
    try:
        return _validate(raw)
    except (TypeError, KeyError, AttributeError):
        raise ValueError("The rules are not in a valid format")


def _validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("The rules are not in a valid format")
    ws = raw.get("week_start", 0)
    if isinstance(ws, bool) or not isinstance(ws, int) or not 0 <= ws <= 6:
        raise ValueError("Week start must be a day of the week")

    weekly = raw.get("weekly")
    if weekly:
        weekly = {"threshold": _num(weekly["threshold"], 0.25, 168, "Weekly threshold"),
                  "multiplier": _num(weekly["multiplier"], 0, 5, "Weekly overtime rate")}
    else:
        weekly = None

    daily_ot = raw.get("daily_ot")
    if daily_ot:
        daily_ot = {"after": _num(daily_ot["after"], 0.25, 24, "Daily overtime hours"),
                    "multiplier": _num(daily_ot["multiplier"], 0, 5, "Daily overtime rate")}
    else:
        daily_ot = None

    daily_dt = raw.get("daily_dt")
    if daily_dt:
        daily_dt = {"after": _num(daily_dt["after"], 0.25, 24, "Daily double-time hours"),
                    "multiplier": _num(daily_dt["multiplier"], 0, 5, "Daily double-time rate")}
        if daily_ot and daily_dt["after"] <= daily_ot["after"]:
            raise ValueError("Daily double time must start after daily overtime does")
    else:
        daily_dt = None

    cons = raw.get("consecutive")
    if cons:
        day = _num(cons["day"], 2, 14, "Consecutive day number")
        if day != int(day):
            raise ValueError("Consecutive day number must be a whole number")
        cons = {"day": int(day), "multiplier": _num(cons["multiplier"], 0, 5, "Consecutive-day rate"),
                "counts": bool(cons.get("counts")), "restart": bool(cons.get("restart"))}
    else:
        cons = None

    raw_codes = raw.get("codes") or []
    if not isinstance(raw_codes, list) or len(raw_codes) > MAX_CODES:
        raise ValueError("A rule set can have at most %d day codes" % MAX_CODES)
    codes, taken_keys, taken_labels = [], set(), set()
    for c in raw_codes:
        label = str(c.get("label", "")).strip()
        if not label or len(label) > 12:
            raise ValueError("Each day code needs a label of 1 to 12 characters")
        if label.lower() in taken_labels:
            raise ValueError('Two day codes are both named "%s"' % label)
        taken_labels.add(label.lower())
        behavior = c.get("behavior")
        if behavior not in BEHAVIORS:
            raise ValueError('Choose what "%s" does' % label)
        key = c.get("key")
        if not (isinstance(key, str) and KEY_RE.match(key)) or key in taken_keys or key == "hours":
            key = _slug_key(label, taken_keys)
        taken_keys.add(key)
        hours = _num(c.get("hours", 0) or 0, 0, 24, '"%s" hours' % label)
        if behavior == "fixed_hours" and hours <= 0:
            raise ValueError('"%s" adds fixed hours, so enter how many (1 to 24)' % label)
        codes.append({"key": key, "label": label, "behavior": behavior,
                      "multiplier": _num(c.get("multiplier", 1), 0, 5, '"%s" rate' % label),
                      "hours": hours if behavior == "fixed_hours" else 0,
                      "counts": bool(c.get("counts")),
                      "needs_work": bool(c.get("needs_work")) and behavior == "fixed_hours"})
    return {"week_start": ws, "weekly": weekly, "daily_ot": daily_ot, "daily_dt": daily_dt,
            "consecutive": cons, "codes": codes}


def _g(x):
    return ("%g" % x)


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suffix)


def describe(rules):
    """Plain-English sentences for a validated rule set."""
    out = ["A work week starts on %s. Each week is calculated on its own." % DAY_NAMES[rules["week_start"]]]
    w = rules["weekly"]
    if w:
        out.append("Once a week's counted hours pass %s, further hours on plain days pay %sx."
                   % (_g(w["threshold"]), _g(w["multiplier"])))
    else:
        out.append("There is no weekly overtime threshold.")
    if rules["daily_ot"]:
        out.append("On plain days, hours beyond %s in a day pay %sx."
                   % (_g(rules["daily_ot"]["after"]), _g(rules["daily_ot"]["multiplier"])))
    if rules["daily_dt"]:
        out.append("On plain days, hours beyond %s in a day pay %sx."
                   % (_g(rules["daily_dt"]["after"]), _g(rules["daily_dt"]["multiplier"])))
    c = rules["consecutive"]
    if c:
        if c["restart"]:
            out.append("Every %s consecutive day worked pays %sx on all its hours%s. Days in a row are counted across "
                       "weeks and pay periods, not by day of the week. Any day paid at %sx or more restarts the count."
                       % (_ordinal(c["day"]), _g(c["multiplier"]), _counts(c["counts"], w), _g(c["multiplier"])))
        else:
            out.append("The %s consecutive day worked pays %sx on all its hours%s. Days in a row are counted across "
                       "weeks and pay periods, not by day of the week."
                       % (_ordinal(c["day"]), _g(c["multiplier"]), _counts(c["counts"], w)))
    for k in rules["codes"]:
        if k["behavior"] == "multiplier":
            out.append('Checking "%s" pays all hours that day at %sx%s.' % (k["label"], _g(k["multiplier"]), _counts(k["counts"], w)))
        elif k["behavior"] == "leave":
            out.append('Checking "%s" treats the hours entered as paid leave at %sx%s.' % (k["label"], _g(k["multiplier"]), _counts(k["counts"], w)))
        else:
            extra = (" only if hours were worked that day" if k["needs_work"] and k["counts"] and w else "")
            out.append('Checking "%s" adds %s hours at %sx%s%s; any hours worked that day pay normally.'
                       % (k["label"], _g(k["hours"]), _g(k["multiplier"]), _counts(k["counts"], w), extra))
    if len(rules["codes"]) > 1 or c:
        out.append("If more than one rate applies to a day, the highest rate is used.")
    return out


def _counts(counts, weekly):
    if not weekly:
        return ""
    return " (counts toward the weekly threshold)" if counts else " (does not count toward the weekly threshold)"


ARKEMA = {
    "week_start": 0,
    "weekly": {"threshold": 40, "multiplier": 1.5},
    "daily_ot": None, "daily_dt": None,
    "consecutive": {"day": 7, "multiplier": 2, "counts": False, "restart": True},
    "codes": [
        {"key": "ot", "label": "OT", "behavior": "multiplier", "multiplier": 1.5, "hours": 0, "counts": False},
        {"key": "dt", "label": "DT", "behavior": "multiplier", "multiplier": 2, "hours": 0, "counts": False},
        {"key": "vac", "label": "Vac", "behavior": "leave", "multiplier": 1, "hours": 0, "counts": True},
        {"key": "hol", "label": "Hol", "behavior": "fixed_hours", "multiplier": 1, "hours": 8, "counts": True,
         "needs_work": True},
    ],
}

STANDARD_US = {
    "week_start": 0,
    "weekly": {"threshold": 40, "multiplier": 1.5},
    "daily_ot": None, "daily_dt": None, "consecutive": None,
    "codes": [
        {"key": "vac", "label": "Vac", "behavior": "leave", "multiplier": 1, "hours": 0, "counts": False},
        {"key": "hol", "label": "Hol", "behavior": "leave", "multiplier": 1, "hours": 0, "counts": False},
    ],
}

BLANK = {"week_start": 0, "weekly": None, "daily_ot": None, "daily_dt": None, "consecutive": None, "codes": []}

BUILTINS = [
    ("Arkema", "Weekly OT after 40, OT/DT boxes, every 7th consecutive day double time, 8-hour holiday credit.", ARKEMA),
    ("Standard US 40-hour week", "Time and a half after 40 hours in a week. Paid leave does not count toward the 40.", STANDARD_US),
]
