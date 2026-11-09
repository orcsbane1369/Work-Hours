# Work Hours Tracker (web)

Web version of the desktop paystub calculator. Flask + SQLite, multi-user, each user's data is private.

## Run
    python -m venv .venv
    .venv\Scripts\activate          (Windows)   |   source .venv/bin/activate  (Mac/Linux)
    pip install -r requirements.txt
    python app.py
Open http://127.0.0.1:5000 and create an account.

Data lives in `instance/workhours.db` (one file, back it up by copying it). The session signing key is
generated into `instance/secret_key`. Keep both files private.

## Settings (environment variables)
- `WH_ALLOW_REGISTRATION=0`  turn off sign-ups once everyone has an account
- `WH_REGISTER_CODE=...`     keep sign-ups open but require this invitation code
- `WH_DB=path`               use a different database file
- `WH_HTTPS=1`               Secure cookies and HSTS (set this when served over https)
- `WH_BEHIND_PROXY=1`        trust one reverse proxy (nginx) for the visitor's IP and https
- `WH_SECRET_KEY=...`        session signing key (otherwise generated into `instance/secret_key`)
- `WH_CLIENT_IP_HEADER=CF-Connecting-IP`  read the real visitor address from this header (Cloudflare Tunnel)
- `WH_INSTANCE=/data`        folder for the database and session key (a Docker volume)
- `WH_HOST=127.0.0.1`, `PORT=5000`

Docker settings live in a `.env` file (copy `.env.example`); it is never committed to GitHub, so updates keep your settings.
To run it in Docker (for example on Unraid) behind nginx and a Cloudflare Tunnel, see **DEPLOY.md**. There is a
`Dockerfile` and `docker-compose.yml`; `python serve.py` (Waitress) is what runs inside the container.

## Employer rule sets
Pay rules are data, not code. Open **Employer rules** on the main screen to see the shared library, view a
rule set in plain English, duplicate one, or build a new one from a form:
- Work week start day
- Automatic overtime: weekly threshold, daily overtime, daily double time, Nth consecutive day
- Day checkboxes: add as many as the employer needs (max 8). Each one either pays all that day's hours at a
  rate, treats the hours entered as paid leave, or adds a fixed number of hours (like an 8-hour holiday).
  Each says whether its hours count toward the weekly threshold.

Library rules: everyone can use any rule set. Only its creator can edit or delete it (built-ins are read-only);
anyone can Duplicate one to make their own. Each saved paystub keeps a copy of the rules it was calculated with,
so editing or deleting a rule set never changes old paystubs. Choose your employer in Pay settings; you can also
switch rules inside a paystub.

Consecutive-day rules (like "every 7th day in a row is double time") count real consecutive worked days. They are
not tied to Sunday or to the week: a run carries from week 1 into week 2, and in from the previous pay period.
The paystub screen has "Days worked in a row before this period", filled in automatically from the paystub that
ends the day before (you can change it, e.g. if there is no earlier paystub in the app). A day with hours typed
counts as worked unless a paid-leave box (Vac) is checked, which resets the count. With the rule's "restart"
option on (Arkema), any day paid at the rule's rate or higher restarts the count, so the 7th day pays double, the
count starts over, and the 14th pays double next. A day you mark DT restarts it too. Editing an old paystub does
not update later ones, so re-save a later paystub if you correct an earlier one.

A day-checkbox that adds fixed hours (like Hol, +8) can be set to count toward the weekly threshold only if hours
were also worked that day. For Arkema, a holiday nobody worked is paid 8 straight hours that do not count toward
the 40 and do not count as a day worked.

If several rates apply to one day, the highest wins. Hours on a day marked with a code count toward the weekly
threshold only if that code says so. Plain-day hours always count. See `calc.py` for the exact order.

Built-ins: **Arkema** (weekly OT after 40, OT and DT boxes that do not count toward the 40, 7th consecutive day
double time, Vac counts, Hol adds 8 straight hours that count) and **Standard US 40-hour week**.

Upgrading from the earlier version: just replace the files and start the app. Your database upgrades itself,
existing users are switched to the Arkema rules, and old paystubs open with those rules.

## Yearly summary
The **Yearly summary** button totals a tax year, grouped by pay date: gross, taxes withheld (federal, Social
Security, Medicare), other deductions by name, net, hours by pay rate (with the overtime share), a month-by-month
table, and a breakdown by employer. It reads the breakdown saved with each paystub, so it reports exactly what
you saved. Paystubs saved before this version have no itemized taxes; they still count in gross and net, and the
summary tells you how many to open and re-save.

**Print / Save as PDF** (in the summary window) opens a paper-sized version of the summary, including a paystub
detail list, and brings up the print dialog. Choose "Save as PDF" as the destination to get a file. It works the
same on a phone or computer.

Built-in rule sets are refreshed whenever the app starts. Paystubs already saved keep the rules they were calculated
with. To recalculate an old paystub under the current rules, open it and pick the rule set in its dropdown.

## Tests
    python test_calc.py     # overtime rules
    python test_app.py      # login, CSRF, per-user isolation, save/edit, splits

## Security notes
Passwords are hashed (werkzeug), sessions are HttpOnly + SameSite, every write needs a CSRF token, login locks
for 5 minutes after 5 failures, and every query is scoped to the signed-in user. The server binds to 127.0.0.1
only. Before exposing it beyond your own PC, put it behind HTTPS (reverse proxy) and use a production server
(e.g. waitress) instead of Flask's built-in one.

## Version control (GitHub)
Repository: https://github.com/orcsbane1369/Work-Hours

`.gitignore` keeps secrets and data out of the repository (`.env`, the database, the session key). `.env.example` is the
template that is committed. To publish a change from the folder that holds `app.py`:

    git add .
    git status          # check no .env, .db or secret_key is listed
    git commit -m "Describe the change"
    git push

Run `python test_calc.py` and `python test_app.py` before you push.

## Updating on Unraid
After a change has been pushed to GitHub, open the Unraid terminal and run:

    cd /mnt/user/appdata/workhours-git
    sh deploy/update-unraid.sh

The script does four things: backs up the database (into the `backups` folder of the data directory), pulls the
latest code from GitHub, rebuilds and restarts the container (`docker compose up -d --build`), and removes old images.
Your `.env` and your database are never touched. It needs no git install (git runs from a throwaway container).

First-time setup on a new server:

    cd /mnt/user/appdata
    docker run --rm -v /mnt/user/appdata:/git alpine/git clone https://github.com/orcsbane1369/Work-Hours.git workhours-git
    cd workhours-git
    cp .env.example .env && chmod 600 .env && nano .env      # or copy your existing .env here
    docker compose up -d --build

See DEPLOY.md for the Cloudflare Tunnel setup.
