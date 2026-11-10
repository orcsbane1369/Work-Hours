(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const CSRF = document.querySelector('meta[name="csrf"]').content;
  const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]; // index = JS getUTCDay()
  const WEEK_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]; // index = week_start
  const BEHAVIORS = [
    ["multiplier", "All hours that day at a rate"],
    ["leave", "Hours entered are paid leave"],
    ["fixed_hours", "Adds a fixed number of hours"],
  ];
  const usd = (n) => Number(n).toLocaleString("en-US", { style: "currency", currency: "USD" });
  const hrs = (n) => String(Math.round(Number(n) * 100) / 100);
  const rate = (m) => `${Math.round(Number(m) * 10000) / 10000}x`;

  function el(tag, props = {}, kids = []) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "text") e.textContent = v;
      else if (k === "class") e.className = v;
      else if (k in e) e[k] = v;
      else e.setAttribute(k, v);
    }
    for (const c of [].concat(kids)) e.append(c);
    return e;
  }

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    if (res.status === 401) { location.href = "/login"; throw new Error("Signed out"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  }
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // ---------- dates (UTC math, so daylight saving never shifts a day)
  const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const fmt = (d) => d.toISOString().slice(0, 10);
  const addDays = (s, n) => { const d = parse(s); d.setUTCDate(d.getUTCDate() + n); return fmt(d); };
  const shortDate = (s) => { const d = parse(s); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
  const longDate = (s) => { const d = parse(s); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}/${d.getUTCFullYear()}`; };
  const today = () => fmt(new Date());

  // ---------- state
  let stubs = [], selectedId = null, editingId = null, banks = [], deds = [], settings = {}, library = [];
  let rules = null;          // rules used by the open paystub
  let snapshotRules = null;  // rules saved with the paystub being edited
  let lastNet = null;

  // ---------- employer rule library (shared)
  async function loadLibrary() {
    library = await api("GET", "/api/rulesets");
    fillRuleSelect($("s_rules"), $("s_rules").value || (settings.ruleset_id ?? ""));
    fillRuleSelect($("rs_template"), "", [["", "Blank (no rules)"]], false);
  }
  function fillRuleSelect(sel, value, extra = [], placeholder = true) {
    sel.replaceChildren();
    if (placeholder) sel.append(el("option", { value: "", text: "Choose employer..." }));
    for (const [v, t] of extra) sel.append(el("option", { value: v, text: t }));
    for (const r of library) sel.append(el("option", { value: r.id, text: r.name }));
    sel.value = String(value ?? "");
    if (sel.value !== String(value ?? "")) sel.value = "";
  }

  // ---------- settings
  async function loadSettings() {
    settings = await api("GET", "/api/settings");
    $("s_rate").value = settings.hourly_rate;
    $("s_fed").value = settings.fed_pct;
    $("s_ss").value = settings.ss_pct;
    $("s_med").value = settings.med_pct;
    await loadLibrary();
    fillRuleSelect($("s_rules"), settings.ruleset_id ?? "");
    showSummary($("s_rules").value);
  }
  async function showSummary(id) {
    const box = $("rulesSummary"), list = $("rulesList");
    list.replaceChildren();
    if (!id) { box.hidden = true; return; }
    try {
      const rs = await api("GET", "/api/rulesets/" + id);
      rs.summary.forEach((t) => list.append(el("li", { text: t })));
      box.hidden = false;
    } catch { box.hidden = true; }
  }
  $("s_rules").addEventListener("change", () => showSummary($("s_rules").value));
  $("settingsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      settings = await api("PUT", "/api/settings", {
        ruleset_id: $("s_rules").value, hourly_rate: $("s_rate").value,
        fed_pct: $("s_fed").value, ss_pct: $("s_ss").value, med_pct: $("s_med").value,
      });
      $("settingsMsg").textContent = "Saved.";
    } catch (err) { $("settingsMsg").textContent = err.message; }
    setTimeout(() => ($("settingsMsg").textContent = ""), 3000);
  });

  // ---------- paystub list
  async function loadStubs() {
    const q = new URLSearchParams();
    if ($("f_start").value) q.set("start", $("f_start").value);
    if ($("f_end").value) q.set("end", $("f_end").value);
    stubs = await api("GET", "/api/paystubs?" + q);
    if (!stubs.some((s) => s.id === selectedId)) selectedId = null;
    renderStubs();
  }
  function renderStubs() {
    const tb = $("stubTable").tBodies[0];
    tb.replaceChildren();
    for (const s of stubs) {
      const tr = el("tr", { tabIndex: 0 }, [
        el("td", { text: longDate(s.pay_date) }),
        el("td", { text: `${longDate(s.period_start)} to ${longDate(s.period_end)}` }),
        el("td", { class: "emp", text: s.ruleset_name || "" }),
        el("td", { class: "n", text: usd(s.rate) }),
        el("td", { class: "n", text: hrs(s.total_hours) }),
        el("td", { class: "n", text: usd(s.gross) }),
        el("td", { class: "n", text: usd(s.deductions_total) }),
        el("td", { class: "n net", text: usd(s.net) }),
      ]);
      tr.setAttribute("aria-selected", s.id === selectedId);
      tr.addEventListener("click", () => select(s.id));
      tr.addEventListener("dblclick", () => openStub(s.id));
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter") openStub(s.id);
        if (e.key === " ") { e.preventDefault(); select(s.id); }
      });
      tb.append(tr);
    }
    $("emptyMsg").hidden = stubs.length > 0;
    $("stubCount").textContent = stubs.length ? `${stubs.length} paystub${stubs.length === 1 ? "" : "s"}` : "";
    $("btnEdit").disabled = $("btnDelete").disabled = selectedId === null;
  }
  function select(id) { selectedId = id; renderStubs(); }
  $("filterForm").addEventListener("submit", (e) => { e.preventDefault(); loadStubs(); });
  $("btnClear").addEventListener("click", () => { $("f_start").value = ""; $("f_end").value = ""; loadStubs(); });
  $("btnNew").addEventListener("click", () => openStub(null));
  $("btnEdit").addEventListener("click", () => selectedId !== null && openStub(selectedId));
  $("btnDelete").addEventListener("click", async () => {
    const s = stubs.find((x) => x.id === selectedId);
    if (!s || !confirm(`Delete the paystub paid ${longDate(s.pay_date)}? This cannot be undone.`)) return;
    await api("DELETE", "/api/paystubs/" + s.id);
    selectedId = null;
    loadStubs();
  });

  // ---------- paystub entry: hours grid (checkbox columns come from the employer's rules)
  const codes = () => (rules ? rules.codes : []);
  function codeHint(c) {
    if (c.behavior === "fixed_hours") return `${c.label}: adds ${c.hours} hours at ${rate(c.multiplier)}`;
    if (c.behavior === "leave") return `${c.label}: hours entered are paid leave at ${rate(c.multiplier)}`;
    return `${c.label}: all hours that day at ${rate(c.multiplier)}`;
  }
  function buildGrid(prev) {
    const cs = codes();
    for (const w of [0, 1]) {
      const g = $("week" + w);
      g.style.setProperty("--n", Math.max(cs.length, 1));
      g.replaceChildren(el("span"), el("span", { class: "hd", text: "Hours" }),
        ...cs.map((c) => el("span", { class: "hd", text: c.label, title: codeHint(c) })));
      if (!cs.length) g.append(el("span", { class: "hd", text: "No checkboxes" }));
      for (let i = 0; i < 7; i++) {
        const idx = w * 7 + i;
        g.append(
          el("span", { class: "day" }, [el("span", { id: `dn${idx}` }), el("small", { id: `dt${idx}` })]),
          el("input", { type: "number", step: "0.25", min: "0", max: "24", value: "0", id: `h${idx}`, "aria-label": `Day ${idx + 1} hours` }),
          ...cs.map((c) => el("label", { class: "ck", title: codeHint(c) }, [
            el("input", { type: "checkbox", id: `f${idx}_${c.key}`, "aria-label": `Day ${idx + 1} ${c.label}` }),
          ])),
        );
        if (!cs.length) g.append(el("span"));
        g.append(el("span", { class: "note", id: `note${idx}` }));
        if (prev && prev[idx]) {
          $("h" + idx).value = prev[idx].hours;
          for (const k of prev[idx].flags || []) { const cb = $(`f${idx}_${k}`); if (cb) cb.checked = true; }
        }
      }
    }
  }
  function refreshDates() {
    const start = $("p_start").value;
    const wsDow = rules ? (rules.week_start + 1) % 7 : 1;
    for (let i = 0; i < 14; i++) {
      $("dt" + i).textContent = start ? shortDate(addDays(start, i)) : "";
      $("dn" + i).textContent = DOW[start ? parse(addDays(start, i)).getUTCDay() : (wsDow + i) % 7];
    }
    const warn = $("weekWarn");
    if (start && rules && parse(start).getUTCDay() !== wsDow) {
      warn.textContent = `Period start is not a ${WEEK_NAMES[rules.week_start]}. These rules count weeks starting on ${WEEK_NAMES[rules.week_start]}.`;
      warn.hidden = false;
    } else warn.hidden = true;
  }
  function collectDays() {
    return Array.from({ length: 14 }, (_, i) => ({
      hours: $("h" + i)?.value || "0",
      flags: codes().filter((c) => $(`f${i}_${c.key}`)?.checked).map((c) => c.key),
    }));
  }
  function payload() {
    return {
      stub_id: editingId, ruleset_id: $("p_rules").value, streak_before: $("p_streak").value || "0",
      pay_date: $("p_paydate").value, period_start: $("p_start").value, period_end: $("p_end").value,
      rate: $("p_rate").value, fed_pct: $("p_fed").value, ss_pct: $("p_ss").value, med_pct: $("p_med").value,
      days: collectDays(),
      deduction_ids: Array.from(document.querySelectorAll("#dedChecks input:checked")).map((c) => Number(c.value)),
    };
  }
  async function chooseRules() {
    const v = $("p_rules").value;
    const prev = $("week0").children.length ? collectDays() : null;
    if (v === "snapshot") rules = snapshotRules;
    else if (v === "") rules = null;
    else rules = (await api("GET", "/api/rulesets/" + v)).rules;
    buildGrid(prev);
    refreshDates();
    calculate(false);
  }
  $("p_rules").addEventListener("change", () => chooseRules().catch((e) => ($("stubMsg").textContent = e.message)));

  // ---------- paystub entry: deductions + splits
  function renderDedChecks(selected, extra = []) {
    const box = $("dedChecks");
    box.replaceChildren();
    const all = [...deds, ...extra.filter((x) => !deds.some((d) => d.id === x.id))];
    $("dedNone").hidden = all.length > 0;
    for (const d of all) {
      const amt = d.mode === "percent" ? `${Number(d.amount)}%` : usd(d.amount);
      box.append(el("label", { class: "inline" }, [
        el("input", { type: "checkbox", value: d.id, checked: selected.includes(d.id) }),
        `${d.name} (${d.kind === "pre" ? "pre-tax" : "post-tax"}, ${amt})`,
      ]));
    }
  }
  function addSplit(bankId = "", bankName = "", amount = "") {
    const sel = el("select", { "aria-label": "Bank" }, [el("option", { value: "", text: "Choose bank..." })]);
    banks.forEach((b) => sel.append(el("option", { value: b.id, text: b.name })));
    if (bankId && !banks.some((b) => b.id === bankId)) sel.append(el("option", { value: bankId, text: `${bankName} (removed)` }));
    sel.value = bankId;
    const amt = el("input", { type: "number", step: "0.01", min: "0", value: amount, "aria-label": "Deposit amount" });
    const rm = el("button", { type: "button", text: "Remove" });
    const row = el("div", { class: "split-row" }, [sel, amt, rm]);
    rm.addEventListener("click", () => { row.remove(); updateRemaining(); });
    amt.addEventListener("input", updateRemaining);
    $("splits").append(row);
    updateRemaining();
  }
  const splitRows = () => Array.from(document.querySelectorAll("#splits .split-row"));
  function updateRemaining() {
    const out = $("remaining");
    if (lastNet === null) { out.textContent = ""; return; }
    const used = splitRows().reduce((a, r) => a + (parseFloat(r.querySelector("input").value) || 0), 0);
    const rem = Math.round((lastNet - used) * 100) / 100;
    out.textContent = splitRows().length ? `Remaining net pay: ${usd(rem)}` : "";
    out.className = splitRows().length && rem !== 0 ? "warn" : "muted";
  }
  $("btnAddSplit").addEventListener("click", () => addSplit());
  $("btnFillSplit").addEventListener("click", () => {
    const rows = splitRows();
    if (!rows.length || lastNet === null) return;
    const last = rows[rows.length - 1].querySelector("input");
    const others = rows.slice(0, -1).reduce((a, r) => a + (parseFloat(r.querySelector("input").value) || 0), 0);
    last.value = (Math.round((lastNet - others) * 100) / 100).toFixed(2);
    updateRemaining();
  });

  // ---------- paystub entry: calculation + results panel
  function dl(rows) {
    const d = el("dl");
    for (const [k, v, strong] of rows) d.append(el("dt", { text: k }), el("dd", { text: v, class: strong ? "strong" : "" }));
    return d;
  }
  function bucketRows(buckets) {
    if (!buckets.length) return [["No hours", "0"]];
    return buckets.map((b) => [Number(b.mult) === 1 ? "Straight (1x)" : rate(b.mult), hrs(b.hours)]);
  }
  function renderResults(r) {
    const box = $("results");
    box.replaceChildren();
    box.append(el("div", { class: "sub", text: `Rules: ${r.ruleset_name || "none"}` }));
    r.weeks.forEach((w, wi) => {
      const rows = bucketRows(w.buckets);
      if (r.threshold !== null) rows.push([`Counted toward ${hrs(r.threshold)}`, hrs(w.counted)]);
      box.append(el("div", { class: "sub", text: `Week ${wi + 1}` }), dl(rows));
      w.days.forEach((d, di) => { const n = $("note" + (wi * 7 + di)); if (n) n.textContent = d.note; });
    });
    box.append(el("div", { class: "sub", text: "Pay period" }), dl([
      ...bucketRows(r.buckets), ["Total hours", hrs(r.total_hours), true], ["Rate", usd(r.rate)], ["Gross pay", usd(r.gross), true],
    ]));
    const rows = [["Federal", usd(r.fed)], ["Social Security", usd(r.ss)], ["Medicare", usd(r.med)]];
    r.deduction_rows.forEach((d) => rows.push([`${d.name} (${d.kind === "pre" ? "pre" : "post"})`, usd(d.amount)]));
    rows.push(["Total deductions", usd(r.total_deductions), true]);
    box.append(el("div", { class: "sub", text: "Deductions" }), dl(rows));
    box.append(el("div", { class: "net-box" }, [el("span", { text: "Net pay" }), el("strong", { text: usd(r.net) })]));
    lastNet = r.net;
    updateRemaining();
  }
  async function calculate(showErrors = true) {
    if (!$("p_rules").value) {
      if (showErrors) $("stubMsg").textContent = "Choose an employer rule set first.";
      return null;
    }
    try {
      const r = await api("POST", "/api/calculate", payload());
      $("stubMsg").textContent = "";
      renderResults(r);
      return r;
    } catch (err) {
      if (showErrors) $("stubMsg").textContent = err.message;
      return null;
    }
  }
  const scheduleCalc = debounce(() => calculate(false), 350);
  $("btnCalc").addEventListener("click", () => calculate(true));
  $("stubForm").addEventListener("input", (e) => { if (!e.target.closest("#splits") && e.target.id !== "p_rules") scheduleCalc(); });
  $("stubForm").addEventListener("change", (e) => { if (!e.target.closest("#splits") && e.target.id !== "p_rules") scheduleCalc(); });
  // A run of consecutive days can start in the previous pay period, so suggest it from the paystub that ends
  // the day before this one starts. It stays editable.
  async function suggestStreak() {
    const start = $("p_start").value;
    if (!start) return;
    try {
      const s = await api("GET", "/api/streak?period_start=" + start);
      $("p_streak").value = s.streak;
      $("streakHint").textContent = s.source
        ? `Suggested from your paystub paid ${longDate(s.source)}. Change it if that's not right.`
        : "No paystub ends the day before this period. Enter the days you worked just before it, if any.";
    } catch { /* leave the value as typed */ }
  }
  $("p_start").addEventListener("change", async () => {
    if ($("p_start").value) {
      $("p_end").value = addDays($("p_start").value, 13);
      if (editingId === null) $("p_paydate").value = addDays($("p_start").value, 18); // pay date = period end + 5 days
    }
    refreshDates();
    await suggestStreak();
    calculate(false);
  });

  // ---------- paystub entry: open / save
  async function openStub(id) {
    editingId = id;
    lastNet = null;
    $("stubMsg").textContent = "";
    $("results").replaceChildren(el("p", { class: "muted", text: "Enter hours to see the calculation." }));
    [banks, deds] = await Promise.all([api("GET", "/api/banks"), api("GET", "/api/deductions")]);
    await loadLibrary();
    $("splits").replaceChildren();
    let selectedDeds = [], extra = [], prevDays = null, ruleChoice = String(settings.ruleset_id ?? "");
    snapshotRules = null;
    const rulesExtra = [];
    let s = null;
    if (id !== null) {
      s = await api("GET", "/api/paystubs/" + id);
      if (s.rules) {
        snapshotRules = s.rules;
        rulesExtra.push(["snapshot", `${s.ruleset_name || "Saved rules"} (as saved)`]);
        ruleChoice = "snapshot";
      }
      prevDays = s.days;
    }
    fillRuleSelect($("p_rules"), ruleChoice, rulesExtra);
    if (ruleChoice === "snapshot") rules = snapshotRules;
    else rules = ruleChoice ? (await api("GET", "/api/rulesets/" + ruleChoice)).rules : null;

    if (id === null) {
      $("stubTitle").textContent = "New paystub";
      const all = await api("GET", "/api/paystubs");
      const prevEnd = all.length ? all.map((x) => x.period_end).sort().pop() : null;
      let start;
      if (prevEnd) start = addDays(prevEnd, 1);
      else {
        const ws = rules ? (rules.week_start + 1) % 7 : 1; // JS weekday the weeks start on
        const d = parse(today());
        d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() - ws + 7) % 7) - 14);
        start = fmt(d);
      }
      $("p_start").value = start;
      $("p_end").value = addDays(start, 13);
      $("p_paydate").value = addDays(start, 18);
      $("p_rate").value = settings.hourly_rate;
      $("p_fed").value = settings.fed_pct; $("p_ss").value = settings.ss_pct; $("p_med").value = settings.med_pct;
      await suggestStreak();
    } else {
      $("stubTitle").textContent = "Edit paystub";
      $("p_start").value = s.period_start; $("p_end").value = s.period_end; $("p_paydate").value = s.pay_date;
      $("p_rate").value = s.rate; $("p_fed").value = s.fed_pct; $("p_ss").value = s.ss_pct; $("p_med").value = s.med_pct;
      $("p_streak").value = s.streak_before || 0;
      $("streakHint").textContent = "";
      selectedDeds = s.deduction_ids; extra = s.extra_deductions;
      s.splits.forEach((sp) => addSplit(sp.bank_id, sp.bank_name, sp.amount));
    }
    buildGrid(prevDays);
    renderDedChecks(selectedDeds, extra);
    refreshDates();
    $("stubDlg").showModal();
    if (id !== null) await calculate(false);
  }
  $("btnSave").addEventListener("click", async () => {
    const r = await calculate(true);
    if (!r) return;
    const body = payload();
    body.splits = splitRows().map((row) => ({ bank_id: Number(row.querySelector("select").value) || null, amount: row.querySelector("input").value || "0" }));
    try {
      if (editingId === null) await api("POST", "/api/paystubs", body);
      else await api("PUT", "/api/paystubs/" + editingId, body);
      $("stubDlg").close();
      loadStubs();
    } catch (err) { $("stubMsg").textContent = err.message; }
  });
  $("stubClose").addEventListener("click", () => $("stubDlg").close());

  // ---------- employer rule sets: library list + editor
  let rsEditId = null, rsReadOnly = false;

  function renderRsList() {
    const ul = $("rsList");
    ul.replaceChildren();
    for (const r of library) {
      const open = el("button", { type: "button", text: r.mine ? "Edit" : "View" });
      const dup = el("button", { type: "button", text: "Duplicate" });
      open.addEventListener("click", () => editRs(r.id));
      dup.addEventListener("click", () => duplicateRs(r.id));
      const acts = el("div", { class: "acts" }, [open, dup]);
      if (r.mine) {
        const del = el("button", { type: "button", class: "danger", text: "Delete" });
        del.addEventListener("click", async () => {
          if (!confirm(`Delete "${r.name}" from the shared library? Paystubs already saved keep their own copy of the rules.`)) return;
          try { await api("DELETE", "/api/rulesets/" + r.id); } catch (err) { alert(err.message); return; }
          await refreshAfterLibraryChange();
          $("rsForm").hidden = true;
        });
        acts.append(del);
      }
      ul.append(el("li", {}, [
        el("div", {}, [el("strong", { text: r.name }), el("span", { class: "meta", text: r.owner + (r.description ? `. ${r.description}` : "") })]),
        acts,
      ]));
    }
  }
  async function refreshAfterLibraryChange() {
    const keep = $("s_rules").value;
    settings = await api("GET", "/api/settings");
    await loadLibrary();
    fillRuleSelect($("s_rules"), keep && library.some((r) => String(r.id) === keep) ? keep : (settings.ruleset_id ?? ""));
    showSummary($("s_rules").value);
    renderRsList();
  }
  $("btnRules").addEventListener("click", async () => {
    await loadLibrary();
    renderRsList();
    $("rsForm").hidden = true;
    $("rulesDlg").showModal();
  });

  const setNum = (id, v, dflt) => { $(id).value = v ?? dflt; };
  function fillEditor({ name, description, rules: r, readonly, reason, id }) {
    rsEditId = id; rsReadOnly = readonly;
    $("rsTitle").textContent = readonly ? `View: ${name}` : (id ? `Edit: ${name}` : "New rule set");
    $("rs_name").value = name; $("rs_desc").value = description;
    const ws = $("rs_weekstart");
    ws.replaceChildren(...WEEK_NAMES.map((n, i) => el("option", { value: i, text: n })));
    ws.value = r.week_start;
    $("rs_w_on").checked = !!r.weekly; setNum("rs_w_thr", r.weekly?.threshold, 40); setNum("rs_w_mult", r.weekly?.multiplier, 1.5);
    $("rs_do_on").checked = !!r.daily_ot; setNum("rs_do_after", r.daily_ot?.after, 8); setNum("rs_do_mult", r.daily_ot?.multiplier, 1.5);
    $("rs_dd_on").checked = !!r.daily_dt; setNum("rs_dd_after", r.daily_dt?.after, 12); setNum("rs_dd_mult", r.daily_dt?.multiplier, 2);
    $("rs_c_on").checked = !!r.consecutive; setNum("rs_c_day", r.consecutive?.day, 7); setNum("rs_c_mult", r.consecutive?.multiplier, 2);
    $("rs_c_counts").checked = !!r.consecutive?.counts;
    $("rs_c_restart").checked = !!r.consecutive?.restart;
    $("rsCodes").replaceChildren();
    r.codes.forEach(addCodeRow);
    syncRuleLines();
    $("rsFields").disabled = readonly;
    $("rsReadonly").hidden = !readonly; $("rsReadonly").textContent = reason || "";
    $("rsSave").hidden = readonly; $("rsDuplicate").hidden = !readonly;
    $("rsMsg").textContent = "";
    $("rsForm").hidden = false;
    previewRs();
  }
  function syncRuleLines() {
    for (const [on, ids] of [["rs_w_on", ["rs_w_thr", "rs_w_mult"]], ["rs_do_on", ["rs_do_after", "rs_do_mult"]],
      ["rs_dd_on", ["rs_dd_after", "rs_dd_mult"]], ["rs_c_on", ["rs_c_day", "rs_c_mult", "rs_c_counts", "rs_c_restart"]]]) {
      ids.forEach((i) => { $(i).disabled = rsReadOnly || !$(on).checked; });
    }
    $("rsAddCode").disabled = rsReadOnly || $("rsCodes").children.length >= 8;
  }
  function addCodeRow(c = { label: "", behavior: "multiplier", multiplier: 1.5, hours: 0, counts: false }) {
    const label = el("input", { maxLength: 12, value: c.label, placeholder: "e.g. OT" });
    const beh = el("select", {}, BEHAVIORS.map(([v, t]) => el("option", { value: v, text: t })));
    beh.value = c.behavior;
    const mult = el("input", { type: "number", step: "0.05", min: "0", max: "5", value: c.multiplier });
    const hours = el("input", { type: "number", step: "0.25", min: "0", max: "24", value: c.hours || (c.behavior === "fixed_hours" ? 8 : 0) });
    const counts = el("input", { type: "checkbox", checked: !!c.counts });
    const needs = el("input", { type: "checkbox", checked: !!c.needs_work });
    const rm = el("button", { type: "button", class: "danger", text: "Remove" });
    const row = el("div", { class: "code-row" }, [
      el("label", {}, ["Label", label]), el("label", {}, ["What it does", beh]),
      el("label", {}, ["Pay rate (x)", mult]), el("label", {}, ["Hours added", hours]),
      el("div", { class: "stack" }, [
        el("label", { class: "inline" }, [counts, "Counts toward weekly threshold"]),
        el("label", { class: "inline" }, [needs, "...but only if hours were worked that day"]),
      ]), rm,
    ]);
    row.dataset.key = c.key || "";
    const sync = () => {
      hours.disabled = rsReadOnly || beh.value !== "fixed_hours";
      needs.disabled = rsReadOnly || beh.value !== "fixed_hours" || !counts.checked;  // only meaningful for fixed hours that count
    };
    beh.addEventListener("change", sync);
    counts.addEventListener("change", sync);
    rm.addEventListener("click", () => { row.remove(); syncRuleLines(); previewRs(); });
    $("rsCodes").append(row);
    sync();
    syncRuleLines();
  }
  function readEditor() {
    const on = (id) => $(id).checked;
    const v = (id) => $(id).value;
    return {
      name: v("rs_name"), description: v("rs_desc"),
      rules: {
        week_start: Number(v("rs_weekstart")),
        weekly: on("rs_w_on") ? { threshold: v("rs_w_thr"), multiplier: v("rs_w_mult") } : null,
        daily_ot: on("rs_do_on") ? { after: v("rs_do_after"), multiplier: v("rs_do_mult") } : null,
        daily_dt: on("rs_dd_on") ? { after: v("rs_dd_after"), multiplier: v("rs_dd_mult") } : null,
        consecutive: on("rs_c_on") ? { day: v("rs_c_day"), multiplier: v("rs_c_mult"), counts: on("rs_c_counts"), restart: on("rs_c_restart") } : null,
        codes: Array.from($("rsCodes").children).map((row) => {
          const [label, beh, mult, hours, counts, needs] = row.querySelectorAll("input, select");
          const c = { label: label.value, behavior: beh.value, multiplier: mult.value, hours: hours.value,
            counts: counts.checked, needs_work: needs.checked && !needs.disabled };
          if (row.dataset.key) c.key = row.dataset.key;
          return c;
        }),
      },
    };
  }
  const previewRs = debounce(async () => {
    const list = $("rsSummary");
    try {
      const p = await api("POST", "/api/rulesets/preview", { rules: readEditor().rules });
      list.replaceChildren(...p.summary.map((t) => el("li", { text: t })));
    } catch (err) { list.replaceChildren(el("li", { class: "error", text: err.message })); }
  }, 300);
  $("rsForm").addEventListener("input", () => { syncRuleLines(); previewRs(); });
  $("rsForm").addEventListener("change", () => { syncRuleLines(); previewRs(); });
  $("rsAddCode").addEventListener("click", () => { addCodeRow(); previewRs(); });

  async function editRs(id) {
    const r = await api("GET", "/api/rulesets/" + id);
    fillEditor({
      name: r.name, description: r.description, rules: r.rules, id: r.id, readonly: !r.mine,
      reason: r.builtin ? "This is a built-in rule set and can't be changed. Duplicate it to make your own version."
        : `Created by ${r.owner}. Only they can change it. Duplicate it to make your own version.`,
    });
  }
  async function duplicateRs(id) {
    const r = await api("GET", "/api/rulesets/" + id);
    fillEditor({ name: `${r.name} (copy)`, description: r.description, rules: r.rules, id: null, readonly: false });
  }
  $("rsDuplicate").addEventListener("click", () => rsEditId && duplicateRs(rsEditId));
  $("rsNew").addEventListener("click", async () => {
    const t = $("rs_template").value;
    const rules0 = t ? (await api("GET", "/api/rulesets/" + t)).rules
      : { week_start: 0, weekly: null, daily_ot: null, daily_dt: null, consecutive: null, codes: [] };
    fillEditor({ name: "", description: "", rules: rules0, id: null, readonly: false });
    $("rs_name").focus();
  });
  $("rsCancel").addEventListener("click", () => { $("rsForm").hidden = true; });
  $("rsForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (rsReadOnly) return;
    try {
      const body = readEditor();
      const res = rsEditId ? await api("PUT", "/api/rulesets/" + rsEditId, body) : await api("POST", "/api/rulesets", body);
      rsEditId = res.id;
      await refreshAfterLibraryChange();
      $("rsForm").hidden = true;
    } catch (err) { $("rsMsg").textContent = err.message; }
  });

  // ---------- yearly summary
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const table = (head, rows, numFrom = 1, foot) => {
    const t = el("table", {}, [el("thead", {}, [el("tr", {}, head.map((h, i) => el("th", { class: i >= numFrom ? "n" : "", text: h })))])]);
    t.append(el("tbody", {}, rows.map((r) => el("tr", { class: "static" }, r.map((c, i) =>
      (c instanceof Node) ? el("td", { class: "barcell" }, [c]) : el("td", { class: i >= numFrom ? "n" : "", text: c }))))));
    if (foot) t.append(el("tfoot", {}, [el("tr", {}, foot.map((c, i) => el("td", { class: i >= numFrom ? "n" : "", text: c })))]));
    return el("div", { class: "tablewrap" }, [t]);
  };
  const kpi = (label, value, cls = "") => el("div", { class: "kpi " + cls }, [el("span", { text: label }), el("strong", { text: value })]);

  async function loadSummary(year) {
    const s = await api("GET", "/api/summary" + (year ? "?year=" + year : ""));
    const sel = $("sum_year");
    sel.replaceChildren(...s.years.map((y) => el("option", { value: y, text: y })));
    sel.value = s.year;
    const body = $("sumBody");
    body.replaceChildren();
    const T = s.totals;
    if (!s.count) {
      body.append(el("p", { class: "empty", text: `No paystubs with a pay date in ${s.year}.` }));
      return;
    }
    const taxes = T.fed + T.ss + T.med;
    body.append(
      el("p", { class: "muted", text: `Paystubs are grouped by pay date, which is how wages are reported for the tax year. ${s.count} paystub${s.count === 1 ? "" : "s"} paid in ${s.year}.` }),
      el("div", { class: "kpis" }, [
        kpi("Gross pay", usd(T.gross)), kpi("Taxes withheld", usd(taxes)),
        kpi("Other deductions", usd(T.pre + T.post)), kpi("Net pay", usd(T.net), "net"),
        kpi("Hours paid", hrs(T.hours)), kpi("Average net per paystub", usd(T.net / s.count)),
      ]),
    );
    if (s.missing_breakdown) {
      body.append(el("p", { class: "warn", text: `${s.missing_breakdown} older paystub${s.missing_breakdown === 1 ? " was" : "s were"} saved before taxes and deductions were itemized. Gross and net include ${s.missing_breakdown === 1 ? "it" : "them"}, but taxes, deductions and hours-by-rate do not. Open and re-save ${s.missing_breakdown === 1 ? "it" : "them"} to include the details.` }));
    }

    const maxGross = Math.max(...s.by_month.map((m) => m.gross), 1);
    body.append(el("h3", { text: "By month paid" }), table(
      ["Month", "Paystubs", "Hours", "Gross", "Deductions", "Net", ""],
      s.by_month.filter((m) => m.count).map((m) => {
        const bar = el("span", { class: "bar" });
        bar.style.setProperty("--pct", Math.round((m.gross / maxGross) * 100));
        return [MONTHS[m.month - 1], String(m.count), hrs(m.hours), usd(m.gross), usd(m.deductions), usd(m.net), bar];
      }),
      1, ["Total", String(s.count), hrs(T.hours), usd(T.gross), usd(T.deductions), usd(T.net), ""],
    ));

    const ratesTotal = s.hours_by_rate.reduce((a, r) => a + r.hours, 0);
    const premium = s.hours_by_rate.filter((r) => r.mult > 1).reduce((a, r) => a + r.hours, 0);
    const taxRows = [["Federal withholding", usd(T.fed)], ["Social Security", usd(T.ss)], ["Medicare", usd(T.med)]];
    s.deductions.forEach((d) => taxRows.push([`${d.name} (${d.kind === "pre" ? "pre-tax" : "post-tax"})`, usd(d.total)]));
    body.append(el("div", { class: "two" }, [
      el("div", {}, [el("h3", { text: "Hours by pay rate" }), ratesTotal ? table(
        ["Rate", "Hours", "Share"],
        s.hours_by_rate.map((r) => [Number(r.mult) === 1 ? "Straight (1x)" : rate(r.mult), hrs(r.hours), `${Math.round((r.hours / ratesTotal) * 100)}%`]),
        1, ["Overtime / premium hours", hrs(premium), `${Math.round((premium / ratesTotal) * 100)}%`],
      ) : el("p", { class: "muted", text: "No itemized hours yet." })]),
      el("div", {}, [el("h3", { text: "Taxes and deductions" }), table(["Item", "Total"], taxRows, 1, ["All deductions (gross minus net)", usd(T.deductions)])]),
    ]));

    if (s.by_employer.length > 1) {
      body.append(el("h3", { text: "By employer" }), table(
        ["Employer rules", "Paystubs", "Hours", "Gross", "Net"],
        s.by_employer.map((e) => [e.name, String(e.count), hrs(e.hours), usd(e.gross), usd(e.net)]),
      ));
    }
  }
  $("btnSummary").addEventListener("click", async () => {
    try { await loadSummary(); $("sumDlg").showModal(); } catch (err) { alert(err.message); }
  });
  $("sumPrint").addEventListener("click", () =>
    window.open(`/summary/print?year=${encodeURIComponent($("sum_year").value)}&autoprint=1`, "_blank", "noopener"));
  $("sum_year").addEventListener("change", () => loadSummary($("sum_year").value).catch((e) => alert(e.message)));

  // ---------- banks
  async function renderBanks() {
    banks = await api("GET", "/api/banks");
    const ul = $("bankList");
    ul.replaceChildren();
    if (!banks.length) ul.append(el("li", { class: "muted", text: "No banks yet." }));
    for (const b of banks) {
      const rm = el("button", { type: "button", class: "danger", text: "Delete" });
      rm.addEventListener("click", async () => {
        if (!confirm(`Delete "${b.name}"? Past paystubs keep the bank name.`)) return;
        await api("DELETE", "/api/banks/" + b.id); renderBanks();
      });
      ul.append(el("li", {}, [el("span", { text: b.name }), rm]));
    }
  }
  $("btnBanks").addEventListener("click", async () => { $("bankMsg").textContent = ""; await renderBanks(); $("banksDlg").showModal(); });
  $("bankForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await api("POST", "/api/banks", { name: $("b_name").value }); $("b_name").value = ""; $("bankMsg").textContent = ""; renderBanks(); }
    catch (err) { $("bankMsg").textContent = err.message; }
  });

  // ---------- deductions
  async function renderDeds() {
    deds = await api("GET", "/api/deductions");
    const ul = $("dedList");
    ul.replaceChildren();
    if (!deds.length) ul.append(el("li", { class: "muted", text: "No deductions yet." }));
    for (const d of deds) {
      const amt = d.mode === "percent" ? `${Number(d.amount)}% of gross` : usd(d.amount);
      const rm = el("button", { type: "button", class: "danger", text: "Delete" });
      rm.addEventListener("click", async () => {
        if (!confirm(`Delete "${d.name}"? Past paystubs are not changed.`)) return;
        await api("DELETE", "/api/deductions/" + d.id); renderDeds();
      });
      const fica = d.reduces_fica ? ", reduces FICA wages" : "";
      ul.append(el("li", {}, [el("span", { text: `${d.name}: ${d.kind === "pre" ? "pre-tax" : "post-tax"}, ${amt}${fica}` }), rm]));
    }
  }
  $("btnDeds").addEventListener("click", async () => { $("dedMsg").textContent = ""; await renderDeds(); $("dedsDlg").showModal(); });
  $("dedForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("POST", "/api/deductions", {
        name: $("d_name").value, kind: $("d_kind").value, mode: $("d_mode").value,
        amount: $("d_amount").value, reduces_fica: $("d_fica").checked,
      });
      $("dedForm").reset(); $("dedMsg").textContent = ""; renderDeds();
    } catch (err) { $("dedMsg").textContent = err.message; }
  });

  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  // The button bar is pinned to the top; tell the CSS how tall it is so the column headings stick right below it.
  const bar = document.querySelector(".toolbar");
  const setBar = () => document.documentElement.style.setProperty("--bar-h", Math.round(bar.getBoundingClientRect().height) + "px");
  setBar(); window.addEventListener("resize", setBar);
  if (window.ResizeObserver) new ResizeObserver(setBar).observe(bar);

  // ---------- init
  $("f_start").value = new Date().getFullYear() + "-01-01";
  $("f_end").value = "";
  loadSettings().then(loadStubs);
})();
