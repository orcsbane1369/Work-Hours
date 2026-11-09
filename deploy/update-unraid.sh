#!/bin/sh
# One-command update on Unraid: back up the database, pull the latest code from GitHub, rebuild, restart.
#   cd /mnt/user/appdata/workhours-git && sh deploy/update-unraid.sh
# Needs no git install: git runs from a tiny throwaway container (alpine/git).
set -e
cd "$(dirname "$0")/.."

echo "== 1/4 Backing up the database (skipped if the app is not running)"
docker exec workhours python -c "import sqlite3,datetime; s=sqlite3.connect('/data/workhours.db'); d=sqlite3.connect('/data/backups/workhours-%s.db' % datetime.datetime.now().strftime('%Y-%m-%d-%H%M')); s.backup(d)" \
  && echo "   backup saved in the backups folder" || echo "   (no backup taken)"

echo "== 2/4 Pulling the latest code from GitHub"
docker run --rm -v "$PWD":/git alpine/git -c safe.directory=/git pull --ff-only

echo "== 3/4 Rebuilding and restarting"
docker compose up -d --build

echo "== 4/4 Cleaning up old images"
docker image prune -f >/dev/null

echo "Done. Running version:"
docker run --rm -v "$PWD":/git alpine/git -c safe.directory=/git log -1 --format='   %h  %s  (%cr)'
