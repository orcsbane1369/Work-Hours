# Running Work Hours Tracker on Unraid (Docker) behind a Cloudflare Tunnel

    browser --https--> Cloudflare --> tunnel (cloudflared) --> workhours container (port 5005) --> /data

One small extra container runs the app. Your existing **binhex-nginx and its web page stay exactly as they are**. Nothing
about them changes, and you do not create a second nginx. Cloudflare provides the https certificate. The data is a
single SQLite file in an Unraid share, so Unraid's backups cover it.

I could not run Docker where I built this, so the Dockerfile and compose file are untested on a real Docker host. They
are plain and standard, but expect to adjust a path or a port. Read "Troubleshooting" first if something does not connect.

## Why it cannot live in the nginx web folder
`/mnt/user/web_server_files/html/` is what nginx serves to the public. nginx hands out files as they are; it cannot run
a Python program. Worse, anything placed in that folder, including this app's database (your pay history) and its
session key, could be downloaded by anyone who guessed the file name. Keep the app and its data in their own folders,
as below.

## 1. Put the files on Unraid
Unzip the project somewhere like `/mnt/user/appdata/workhours-src`. The zip contains a folder called `workhours`, so the
files end up in `/mnt/user/appdata/workhours-src/workhours/`. **Every command below is run from inside that inner folder**
(the one that contains `Dockerfile`); check with `ls`. Then create the data folder the app will use:

    mkdir -p /mnt/user/appdata/workhours/backups
    chown -R 99:100 /mnt/user/appdata/workhours

(99:100 is Unraid's `nobody:users`, which the container runs as.)

## 2. Build the image
From an Unraid terminal:

    cd /mnt/user/appdata/workhours-src/workhours
    ls                      # you should see Dockerfile, app.py, docker-compose.yml ...
    docker build -t workhours:latest .

## 3. Start the app container
**With compose** (Compose Manager plugin, or the terminal). First create your settings file once. It is a hidden file
named `.env` that lives next to `docker-compose.yml`, and an update never overwrites it:

    cd /mnt/user/appdata/workhours-src/workhours
    cp .env.example .env && chmod 600 .env
    nano .env                    # set WH_ALLOW_REGISTRATION=1 (and WH_REGISTER_CODE) until your account exists
    docker compose up -d --build

You should not need to edit `docker-compose.yml` itself. Everything you might change is in `.env` (the sign-up switch, the
invitation code, the port, the data folder). `ls -a` shows hidden files if you want to check it is there.

**With Unraid's Add Container form** instead:

| Field | Value |
|---|---|
| Name | workhours |
| Repository | workhours:latest |
| Network Type | Bridge |
| Port | Host `5005`, Container `5000`, TCP |
| Path | Container `/data`, Host `/mnt/user/appdata/workhours`, Read/Write |
| Variable | `WH_BEHIND_PROXY` = `1` |
| Variable | `WH_HTTPS` = `1` |
| Variable | `WH_CLIENT_IP_HEADER` = `CF-Connecting-IP` |
| Variable | `WH_ALLOW_REGISTRATION` = `1` (until your account exists) |
| Extra Parameters | `--user 99:100 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true` |

Check it: `docker logs workhours` should show Waitress serving, `docker ps` should show it healthy, and
`http://<your-unraid-ip>:5005/login` should show the login page from a computer on your home network.

## 4. Add it to your tunnel
In the Cloudflare dashboard (Zero Trust > Networks > Tunnels), add a public hostname `hours.yourdomain.com` whose service
is `http://<your-unraid-ip>:5005` (use `localhost:5005` if your tunnel container uses host networking). Your other
hostnames are untouched. (`deploy/cloudflared-config.example.yml` shows the config-file version.)

Do **not** forward port 5005 on your router. The tunnel is the only way in from the internet.

## 5. First run
Open `https://hours.yourdomain.com` and create your account. Then edit `.env`, set `WH_ALLOW_REGISTRATION=0`, and run
`docker compose up -d` so strangers cannot sign up. To let a few specific people sign up, keep it `1` and add `WH_REGISTER_CODE` (a
long phrase you give them) instead.

## Optional: send it through binhex-nginx anyway
You do not need to. If you want to, point a server block at `http://<unraid-ip>:5005` (`deploy/nginx-workhours.conf` has
the pieces), and point the tunnel hostname at nginx instead. binhex-nginx is on the default bridge network, so it cannot
look up the container by name; it must use the server's IP and the published port. I have not verified where binhex-nginx
reads its site configs, so check its `/config` folder first.

## Security: how the login is protected, and what you chose to skip
You chose not to put Cloudflare Access in front, so the app's own login is the front door and anyone on the internet
can reach it. What protects it now:

- https only (Cloudflare), https-only cookies, HSTS, strict content-security headers, CSRF protection on every change
- passwords stored hashed; a minimum of 10 characters
- five wrong passwords lock that visitor out for five minutes; fifteen wrong guesses against one account, from anywhere,
  lock that account for five minutes (an attacker can use this to annoy you, but only briefly)
- sign-ups closed or behind an invitation code; every record is private to its account

What it does not have: a second step at login. If someone learns or guesses your password, they are in. So:

1. **Use a long, unique password** (a passphrase from a password manager). This matters more than anything else here.
2. **You can add Cloudflare Access later with no change to the app.** If the double login is the annoyance, Access
   sessions can last up to a month per device, and you can list only your own email.
3. **My suggestion if you want more protection** is a built-in authenticator-app code at login (a second step on the same
   login screen, so you sign in once, not twice). It is not built yet.
4. A Cloudflare rate-limiting rule on `/login` is a good extra layer if your plan allows one.

## Backups
The database is `/mnt/user/appdata/workhours/workhours.db`. Unraid's appdata backup plugin will copy it, but a plain file
copy of a database in use can be inconsistent, so take a proper snapshot first. This works while the app is running
(Unraid's User Scripts plugin can run it nightly):

    docker exec workhours python -c "import sqlite3,datetime; s=sqlite3.connect('/data/workhours.db'); d=sqlite3.connect('/data/backups/workhours-%s.db' % datetime.date.today()); s.backup(d)"

Old snapshots pile up in `/mnt/user/appdata/workhours/backups`; delete the ones you do not need. Keep a copy off the
server too, and try restoring one occasionally. To restore, stop the container, copy a snapshot over `workhours.db`,
start it again.

## Updating (from GitHub)
Your settings are in `.env` and your data is in `/mnt/user/appdata/workhours`; neither is in GitHub, so an update cannot
overwrite them. After you push a change to GitHub, one command on Unraid does everything:

    cd /mnt/user/appdata/workhours-git
    sh deploy/update-unraid.sh

It takes a database backup (into the `backups` folder), pulls the latest code, rebuilds the image, restarts the container,
and prints the version now running. The code is baked into the image, so the rebuild is required; the script handles it.
You do not need git installed on Unraid: the script runs git from a small throwaway container.

## One-time: switch Unraid from the zip folder to GitHub
1. **Clone the repo.** If it is private (recommended), create a read-only token first: GitHub > Settings > Developer
   settings > Personal access tokens > Fine-grained tokens > only the `Work-Hours` repository, permission *Contents:
   Read-only*. Never paste a token into a chat or a file you share. Then, on Unraid:

       cd /mnt/user/appdata
       docker run --rm -v "$PWD":/git alpine/git clone https://orcsbane1369:YOUR_TOKEN@github.com/orcsbane1369/Work-Hours.git workhours-git

   (For a public repo, leave out `orcsbane1369:YOUR_TOKEN@`.) The token is stored in `workhours-git/.git/config`, which is
   why it should be read-only and limited to this one repository.
2. **Bring your settings along:** `cp workhours-src/workhours/.env workhours-git/.env && chmod 600 workhours-git/.env`
3. **Start it from the new folder:** `cd workhours-git && docker compose up -d --build`. The project name is fixed
   (`name: workhours`), so this simply replaces the old container. If it says the container name is already in use,
   run `docker rm -f workhours` once and repeat (your data is not affected).
4. Once it works, the old `workhours-src` folder can be deleted. Run compose from only one folder.

## If you ever want to go back to zip files
Unzip over the source folder (`.env` and your data are never in the zip) and run `docker compose up -d --build`.

## Troubleshooting
- **The tunnel hostname shows an error (502/1033):** the app container is not running, or the tunnel's service address is
  wrong. Open `http://<unraid-ip>:5005/login` from your home network. If that works, fix the address in the tunnel.
- **You log in and get bounced back to the login page:** cookies are https-only (`WH_HTTPS=1`) but you opened the site over
  plain http, for example `http://<unraid-ip>:5005`. Use the `https://hours.yourdomain.com` address to sign in.
  (To test on your home network over http, temporarily remove `WH_HTTPS`.)
- **Everyone gets locked out together:** `WH_CLIENT_IP_HEADER` is missing, so the app sees only the tunnel's address.
- **"Permission denied" writing to /data:** run `chown -R 99:100 /mnt/user/appdata/workhours` again.
- **Port already in use:** choose a different host port (the left number) and update the tunnel to match.
