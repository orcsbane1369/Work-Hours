"""Pay calculator driven by an employer rule set (see rules.py). Pure functions: no web or database code.

How one week is calculated, day by day in order:
  1. Every checked `fixed_hours` code adds its hours at its rate (e.g. Holiday +8 at 1x).
  2. If a `leave` code is checked, the hours typed are paid leave at that code's rate, and the day is not worked.
  3. Otherwise, if hours were typed, the day is "worked" (it extends the consecutive-day streak). The candidates are
     the Nth-consecutive-day rule and every checked `multiplier` code. If any apply, ALL the day's hours pay at the
     highest candidate rate. A streak is simply consecutive worked days: it is not tied to a day of the week, and it
     carries across week boundaries and in from the previous pay period. If none apply, it is a plain day:
       - hours beyond the daily double-time / daily overtime limits (if the rule set has them) pay those rates
       - the rest pay 1x until the week's counted hours reach the weekly threshold, then at the weekly overtime rate
  Hours count toward the weekly threshold only where the code / rule says `counts`. Plain-day hours always count.
  A `fixed_hours` code with `needs_work` counts toward the threshold only on days hours were also worked.
  With the consecutive rule's `restart` on, any day paid at that rate or higher (by the rule or a checkbox) restarts
  the day count, so the rule fires on days N, 2N, 3N...
"""
from decimal import Decimal as D, ROUND_HALF_UP

CENT = D("0.01")


def money(x):
    return D(x).quantize(CENT, ROUND_HALF_UP)


def _d(v):
    return D(str(v))


def _num(v):
    try:
        return D(str(v if v not in (None, "") else "0"))
    except Exception:
        raise ValueError("Invalid number: %r" % (v,))


def _key(m):
    return format(_d(m).normalize(), "f")


def _label_rate(m):
    return "%sx" % _key(m)


def end_streak(days, rules, start=0):
    """The consecutive-day count at the end of `days` (a whole number of weeks), given `start` days before them.
    Runs the same day-by-day logic as the pay calculation, so restarts after double-time days are honored.
    Used to carry the count into the next pay period."""
    if len(days) % 7:
        raise ValueError("Days must be whole weeks")
    s = int(start)
    for i in range(0, len(days), 7):
        s = calc_week(days[i:i + 7], rules, s)["end_streak"]
    return s


def calc_week(days, rules, streak0=0):
    """days: list of 7 dicts {hours, flags:[code keys]}. Returns hour buckets by pay rate and per-day notes.
    streak0 = consecutive worked days immediately before the first of these days (they carry across weeks)."""
    if len(days) != 7:
        raise ValueError("A week needs exactly 7 days")
    codes = {c["key"]: c for c in rules["codes"]}
    weekly = rules.get("weekly")
    threshold = _d(weekly["threshold"]) if weekly else None
    weekly_mult = _d(weekly["multiplier"]) if weekly else None
    d_ot, d_dt, cons = rules.get("daily_ot"), rules.get("daily_dt"), rules.get("consecutive")

    buckets = {}  # rate (as text) -> hours

    def add(mult, hours):
        if hours:
            k = _key(mult)
            buckets[k] = buckets.get(k, D(0)) + hours

    counted = D(0)
    streak = int(streak0)
    detail = []
    for day in days:
        h = _num(day.get("hours"))
        if h < 0 or h > 24:
            raise ValueError("Hours per day must be between 0 and 24")
        raw_flags = set(day.get("flags") or [])
        flags = [k for k in codes if k in raw_flags]  # in rule-set order; unknown codes are ignored
        notes = []

        for k in flags:
            c = codes[k]
            if c["behavior"] == "fixed_hours":
                fh = _d(c["hours"])
                add(c["multiplier"], fh)
                if c["counts"] and (h > 0 or not c.get("needs_work")):
                    counted += fh
                notes.append("%s +%s hours" % (c["label"], _key(fh)))

        leaves = [codes[k] for k in flags if codes[k]["behavior"] == "leave"]
        mults = [codes[k] for k in flags if codes[k]["behavior"] == "multiplier"]
        if leaves:
            c = leaves[0]
            add(c["multiplier"], h)
            if c["counts"]:
                counted += h
            streak = 0
            if h:
                notes.append(c["label"])
        elif h > 0:
            streak += 1
            cands = []
            if cons and streak == cons["day"]:
                cands.append((_d(cons["multiplier"]), bool(cons["counts"]), "Day %d" % streak))
            for c in mults:
                cands.append((_d(c["multiplier"]), bool(c["counts"]), c["label"]))
            if cands:
                mult, counts, name = max(cands, key=lambda t: t[0])
                add(mult, h)
                if counts:
                    counted += h
                notes.append("%s %s" % (name, _label_rate(mult)))
                if cons and cons.get("restart") and mult >= _d(cons["multiplier"]):
                    streak = 0  # a day paid at the consecutive-day rate (or more) restarts the count
            else:
                base, dt_part, ot_part = h, D(0), D(0)
                if d_dt:
                    a = _d(d_dt["after"])
                    dt_part, base = max(h - a, D(0)), min(h, a)
                if d_ot:
                    a = _d(d_ot["after"])
                    ot_part, base = max(base - a, D(0)), min(base, a)
                if dt_part:
                    add(d_dt["multiplier"], dt_part)
                    notes.append("Daily %s" % _label_rate(d_dt["multiplier"]))
                if ot_part:
                    add(d_ot["multiplier"], ot_part)
                    notes.append("Daily %s" % _label_rate(d_ot["multiplier"]))
                if threshold is not None:
                    straight = min(base, max(threshold - counted, D(0)))
                    over = base - straight
                    add(1, straight)
                    add(weekly_mult, over)
                    if over:
                        notes.append("Over %s: %s" % (_key(threshold), _label_rate(weekly_mult)))
                else:
                    add(1, base)
                counted += h
        else:
            streak = 0
        detail.append({"note": ", ".join(notes)})

    return {"buckets": _as_list(buckets), "counted": counted, "days": detail, "end_streak": streak}


def _as_list(buckets):
    return [{"mult": D(k), "hours": v} for k, v in sorted(buckets.items(), key=lambda kv: D(kv[0]))]


def calc_paystub(days14, rate, fed_pct, ss_pct, med_pct, deductions, rules, streak_before=0):
    """days14: 14 day dicts (week 1 then week 2).
    streak_before: consecutive days worked immediately before the period started (a run can begin in the previous
    pay period and continue into this one; it also carries from week 1 into week 2).
    deductions: list of dicts {name, kind: 'pre'|'post', mode: 'flat'|'percent', amount, reduces_fica}."""
    rate = _num(rate)
    if rate < 0:
        raise ValueError("Rate cannot be negative")
    if len(days14) != 14:
        raise ValueError("Expected 14 days")
    w1 = calc_week(days14[:7], rules, streak_before)
    weeks = [w1, calc_week(days14[7:], rules, w1["end_streak"])]

    merged = {}
    for w in weeks:
        for b in w["buckets"]:
            k = _key(b["mult"])
            merged[k] = merged.get(k, D(0)) + b["hours"]
    total_hours = sum(merged.values(), D(0))
    gross = money(rate * sum((D(k) * v for k, v in merged.items()), D(0)))

    ded_rows, pre_all, pre_fica, post = [], D(0), D(0), D(0)
    for d in deductions:
        amt = _num(d["amount"])
        val = money(gross * amt / 100) if d["mode"] == "percent" else money(amt)
        ded_rows.append({"name": d["name"], "kind": d["kind"], "amount": val})
        if d["kind"] == "pre":
            pre_all += val
            if d.get("reduces_fica"):
                pre_fica += val
        else:
            post += val

    fed_base = max(gross - pre_all, D(0))
    fica_base = max(gross - pre_fica, D(0))
    fed = money(fed_base * _num(fed_pct) / 100)
    ss = money(fica_base * _num(ss_pct) / 100)
    med = money(fica_base * _num(med_pct) / 100)
    net = money(gross - pre_all - fed - ss - med - post)
    return {
        "rate": money(rate),
        "weeks": weeks,
        "buckets": _as_list(merged),
        "streak_before": int(streak_before), "end_streak": weeks[1]["end_streak"],
        "total_hours": total_hours,
        "gross": gross,
        "deduction_rows": ded_rows,
        "pre_total": money(pre_all), "post_total": money(post),
        "fed": fed, "ss": ss, "med": med,
        "total_deductions": money(gross - net),
        "net": net,
    }
