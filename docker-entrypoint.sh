#!/bin/sh
# Drop privileges to a configurable UID/GID so the bind-mounted /config is writable.
#
# Self-hosters typically bind-mount a host directory on /config. A bind mount keeps the host's
# ownership, so a fixed non-root user baked into the image usually can't write there (the daemon
# creates a missing source dir as root). We start as root, remap the `app` user to PUID/PGID
# (default 1000:1000, the common desktop/NAS user), fix ownership of /config, then exec the app
# as that user via gosu.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Already non-root (e.g. compose `user:` override): nothing to remap, just run and let the
# caller's UID own the permissions.
if [ "$(id -u)" -ne 0 ]; then
    exec "$@"
fi

# Remap the app user/group in place (-o allows reusing an id that already exists on the host).
groupmod -o -g "$PGID" app
usermod  -o -u "$PUID" app

# /config holds all writable state (SQLite + Plex client identity); make sure the app user owns
# it before dropping privileges.
chown -R app:app /config

echo "monitorr: starting as app (uid=$PUID gid=$PGID)"
exec gosu app "$@"
