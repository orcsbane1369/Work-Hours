import copy
from decimal import Decimal as D
import calc, rules as R

ARK = R.validate_rules(R.ARKEMA)
STD = R.validate_rules(R.STANDARD_US)

def week(*hours, flags=None):
    ds = [{"hours": h, "flags": []} for h in hours] + [{"hours": 0, "flags": []} for _ in range(7 - len(hours))]
    for i, f in (flags or {}).items():
        ds[i]["flags"] = f.split()
    return ds

def buckets(w):
    return {str(b["mult"]): b["hours"] for b in w["buckets"]}

def test_plain_week_over_40():
    assert buckets(calc.calc_week(week(10, 10, 10, 10, 10), ARK)) == {"1": 40, "1.5": 10}

def test_ot_box_all_hours_and_does_not_count_toward_40():
    # Mon-Thu 10h plain (40), Fri 8h marked OT -> OT hours at 1.5, nothing else changes
    w = calc.calc_week(week(10, 10, 10, 10, 8, flags={4: "ot"}), ARK)
    assert buckets(w) == {"1": 40, "1.5": 8} and w["counted"] == 40

def test_ot_marked_hours_do_not_push_plain_hours_over_40():
    # 24 OT hours first, then 36 plain hours: plain stay straight because OT hours don't count
    w = calc.calc_week(week(12, 12, 12, 12, 12, flags={0: "ot", 1: "ot"}), ARK)
    assert buckets(w) == {"1": 36, "1.5": 24}

def test_dt_box_all_hours():
    w = calc.calc_week(week(8, 8, flags={1: "dt"}), ARK)
    assert buckets(w) == {"1": 8, "2": 8} and w["counted"] == 8

def test_holiday_adds_eight_and_counts_toward_40():
    w = calc.calc_week(week(8, 8, 8, 8, 8, flags={0: "hol"}), ARK)  # 8 hol + 40 worked
    assert buckets(w) == {"1": 40, "1.5": 8}

def test_holiday_with_no_hours():
    w = calc.calc_week(week(flags={2: "hol"}), ARK)
    assert buckets(w) == {"1": 8} and w["counted"] == 0   # paid, but not counted toward the 40

def test_vacation_counts_toward_40_and_pays_straight():
    w = calc.calc_week(week(8, 8, 8, 8, 8, 8, flags={0: "vac"}), ARK)
    assert buckets(w) == {"1": 40, "1.5": 8}

def test_seventh_consecutive_day_is_double_time_and_does_not_count():
    w = calc.calc_week(week(6, 6, 6, 6, 6, 6, 6), ARK)
    assert buckets(w) == {"1": 36, "2": 6} and w["counted"] == 36

def test_gap_or_vacation_breaks_streak():
    assert "2" not in buckets(calc.calc_week(week(6, 6, 6, 0, 6, 6, 6), ARK))
    assert "2" not in buckets(calc.calc_week(week(6, 6, 6, 6, 6, 6, 6, flags={3: "vac"}), ARK))

def test_highest_rate_wins_when_several_apply():
    # 7th day with OT box checked: 7th-day 2x beats OT 1.5x
    w = calc.calc_week(week(6, 6, 6, 6, 6, 6, 6, flags={6: "ot"}), ARK)
    assert buckets(w)["2"] == 6

def test_user_screenshot_week():
    f1 = {2: "ot", 3: "ot"}
    f2 = {1: "dt", 2: "ot", 3: "ot"}
    days = week(0, 0, 12, 12, 12, 12, 12, flags=f1) + week(*([12] * 7), flags=f2)
    r = calc.calc_paystub(days, "42.08", 20, "6.2", "1.45", [], ARK)
    # Worked Wed-Sun, then Mon-Sun: week 2 Tuesday is the 7th day in a row (DT). Week 2 Sunday is the 13th, so it is
    # a plain day, not double time. Plain hours in week 2: Mon, Fri, Sat, Sun = 48 -> 40 straight + 8 over.
    assert {str(b["mult"]): b["hours"] for b in r["buckets"]} == {"1": 76, "1.5": 56, "2": 12}
    assert r["gross"] == D("7742.72"), r["gross"]
    assert r["end_streak"] == 5      # Tuesday (7th) restarted the count; Wed-Sun since then = 5

def stub(hours14, flags=None, streak_before=0, rules=ARK):
    ds = [{"hours": h, "flags": []} for h in hours14]
    for i, f in (flags or {}).items():
        ds[i]["flags"] = f.split()
    return calc.calc_paystub(ds, "1", 0, 0, 0, [], rules, streak_before)

def bk(r):
    return {str(b["mult"]): b["hours"] for b in r["buckets"]}

def test_seventh_day_is_not_tied_to_sunday():
    # Fri, Sat, Sun of week 1 + Mon..Thu of week 2 = 7 in a row; the 7th is a THURSDAY (index 10).
    r = stub([0, 0, 0, 0, 6, 6, 6, 6, 6, 6, 6, 0, 0, 0])
    assert bk(r) == {"1": 36, "2": 6} and r["weeks"][1]["days"][3]["note"] == "Day 7 2x"
    assert r["weeks"][0]["days"][6]["note"] == ""          # Sunday of week 1 is only day 3

def test_working_a_sunday_is_not_double_time_by_itself():
    r = stub([0, 6, 6, 6, 6, 6, 6, 0, 0, 0, 0, 0, 0, 0])   # Tue-Sun = only 6 days in a row
    assert "2" not in bk(r)
    r = stub([6, 0, 6, 6, 6, 6, 6, 0, 0, 0, 0, 0, 0, 0])   # Sunday after a gap
    assert "2" not in bk(r)

def test_streak_carries_into_the_period_from_the_previous_one():
    r = stub([6] + [0] * 13, streak_before=6)               # 6 days before, this is the 7th
    assert bk(r) == {"2": 6}
    r = stub([6] + [0] * 13, streak_before=5)               # only the 6th
    assert bk(r) == {"1": 6}

def test_vacation_day_breaks_a_streak_across_the_week_boundary():
    r = stub([0, 0, 0, 0, 6, 6, 6, 6, 6, 6, 6, 0, 0, 0], flags={7: "vac"})
    assert "2" not in bk(r)

def test_end_streak_helper():
    days = [{"hours": 0 if i < 10 else 8, "flags": []} for i in range(14)]
    assert calc.end_streak(days, ARK, 0) == 4
    assert calc.end_streak([{"hours": 8, "flags": []}] * 14, ARK, 3) == 3     # DT on day 4 and day 11, then 3 more
    assert calc.end_streak(days[:13] + [{"hours": 8, "flags": ["vac"]}], ARK, 0) == 0

def test_every_seventh_day_is_double_time():
    r = stub([6] * 14)                                       # 14 days in a row
    assert bk(r) == {"1": 72, "2": 12}
    assert r["weeks"][0]["days"][6]["note"] == "Day 7 2x" and r["weeks"][1]["days"][6]["note"] == "Day 7 2x"
    assert r["end_streak"] == 0                              # day 14 restarted the count

def test_a_day_marked_dt_restarts_the_count():
    # 3 days, day 4 marked DT (restarts), then 7 more days: the 7th one after the DT day pays double
    r = stub([6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 0, 0, 0], flags={3: "dt"})
    notes = [d["note"] for d in r["weeks"][0]["days"] + r["weeks"][1]["days"]]
    assert notes[3].startswith("DT") and notes[10] == "Day 7 2x" and notes[9] == ""
    assert bk(r)["2"] == 12

def test_an_ot_marked_day_does_not_restart_the_count():
    r = stub([6] * 7 + [0] * 7, flags={2: "ot"})
    assert r["weeks"][0]["days"][6]["note"] == "Day 7 2x"

def test_restart_is_optional_per_rule_set():
    rules = copy.deepcopy(R.ARKEMA); rules["consecutive"]["restart"] = False
    r = stub([6] * 14, rules=R.validate_rules(rules))
    assert bk(r)["2"] == 6                                   # only the exact 7th day, not the 14th

def test_holiday_hours_count_toward_40_only_when_hours_were_worked():
    # No hours that day: 8 holiday hours paid but not counted, so 5 x 8 plain hours stay straight
    w = calc.calc_week(week(0, 8, 8, 8, 8, 8, flags={0: "hol"}), ARK)
    assert buckets(w) == {"1": 48} and w["counted"] == 40
    # Worked on the holiday: the 8 hours count, so the 5th plain day is overtime
    w = calc.calc_week(week(8, 8, 8, 8, 8, flags={0: "hol"}), ARK)
    assert buckets(w) == {"1": 40, "1.5": 8} and w["counted"] == 48

def test_holiday_without_hours_does_not_extend_a_streak():
    # Six days worked, a paid holiday nobody worked on day 7, then work on day 8: the holiday breaks the run,
    # so neither day 7 nor day 8 pays double time.
    r = stub([6] * 6 + [0, 6] + [0] * 6, flags={6: "hol"})
    assert "2" not in bk(r)
    assert r["weeks"][0]["days"][6]["note"] == "Hol +8 hours"
    assert r["weeks"][1]["days"][0]["note"] == ""

def test_rules_validation_new_fields():
    c = R.validate_rules(R.ARKEMA)
    assert c["consecutive"]["restart"] is True
    assert [k["needs_work"] for k in c["codes"]] == [False, False, False, True]
    bad = R.validate_rules(dict(R.BLANK, codes=[{"label": "X", "behavior": "multiplier", "needs_work": True}]))
    assert bad["codes"][0]["needs_work"] is False            # only meaningful for fixed-hours codes

def test_describe_mentions_restart_and_needs_work():
    text = " ".join(R.describe(R.validate_rules(R.ARKEMA)))
    assert "Every 7th consecutive day" in text and "restarts the count" in text
    assert "only if hours were worked that day" in text

def test_paystub_deductions_and_net():
    days = week(8, 8, 8, 8, 8) + week(10, 10, 10, 10, 10)
    ded = [{"name": "Health", "kind": "pre", "mode": "flat", "amount": "50", "reduces_fica": True},
           {"name": "Union", "kind": "post", "mode": "percent", "amount": "1"}]
    r = calc.calc_paystub(days, "42.08", 10, "6.2", "1.45", ded, ARK)
    assert r["gross"] == D("3997.60")
    assert r["net"] == r["gross"] - r["total_deductions"]
    assert r["post_total"] == D("39.98")

def test_standard_rules_leave_does_not_count():
    # vacation paid straight but not counted: 8 vac + 40 plain (all straight, since plain only reaches 40)
    w = calc.calc_week(week(8, 8, 8, 8, 8, 8, flags={0: "vac"}), STD)
    assert buckets(w) == {"1": 48}

def test_daily_overtime_and_double_time():
    rules = copy.deepcopy(R.BLANK)
    rules["daily_ot"] = {"after": 8, "multiplier": 1.5}
    rules["daily_dt"] = {"after": 12, "multiplier": 2}
    w = calc.calc_week(week(14), R.validate_rules(rules))
    assert buckets(w) == {"1": 8, "1.5": 4, "2": 2}

def test_no_weekly_threshold():
    w = calc.calc_week(week(12, 12, 12, 12, 12), R.validate_rules(R.BLANK))
    assert buckets(w) == {"1": 60}

def test_custom_code_with_fixed_hours_and_rate():
    rules = copy.deepcopy(R.BLANK)
    rules["codes"] = [{"label": "Callin", "behavior": "fixed_hours", "multiplier": 1.25, "hours": 4, "counts": False},
                      {"label": "Prem", "behavior": "multiplier", "multiplier": 1.1, "counts": True}]
    r = R.validate_rules(rules)
    k = {c["label"]: c["key"] for c in r["codes"]}
    w = calc.calc_week(week(8, 8, flags={0: k["Callin"], 1: k["Prem"]}), r)
    assert buckets(w) == {"1.25": 4, "1": 8, "1.1": 8}

def test_validation_rejects_bad_rules():
    for bad in (
        dict(R.BLANK, week_start=9),
        dict(R.BLANK, weekly={"threshold": 400, "multiplier": 1.5}),
        dict(R.BLANK, codes=[{"label": "", "behavior": "leave"}]),
        dict(R.BLANK, codes=[{"label": "A", "behavior": "nope"}]),
        dict(R.BLANK, codes=[{"label": "A", "behavior": "leave"}, {"label": "a", "behavior": "leave"}]),
        dict(R.BLANK, codes=[{"label": "A", "behavior": "fixed_hours", "hours": 0}]),
        dict(R.BLANK, daily_ot={"after": 10, "multiplier": 1.5}, daily_dt={"after": 8, "multiplier": 2}),
        "junk", None,
    ):
        try:
            R.validate_rules(bad)
        except ValueError:
            continue
        raise AssertionError("accepted bad rules: %r" % (bad,))

def test_describe_arkema_reads_naturally():
    text = " ".join(R.describe(ARK))
    assert '"OT" pays all hours that day at 1.5x (does not count' in text
    assert "7th consecutive day" in text and 'adds 8 hours' in text

if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v(); n += 1
    print(n, "tests passed")
