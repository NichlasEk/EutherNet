#!/bin/sh
set -eu

pid="$(
  /usr/bin/ss -ltnp 'sport = :15000' 2>/dev/null \
    | /usr/bin/sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
    | /usr/bin/head -n 1
)"

if [ -n "$pid" ]; then
  /usr/bin/kill "$pid" || true
fi

/usr/bin/sleep 20
/usr/bin/ss -ltnp 'sport = :15000' || true
/usr/bin/timeout 8 /usr/bin/curl -fsS http://127.0.0.1:15000/api/stats >/dev/null
