#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${DATABASE_URL:?Set DATABASE_URL through the secret environment}"
: "${1:?Usage: backup.sh destination.dump}"
if [[ -e "$1" ]]; then
  echo 'Refusing to overwrite an existing backup' >&2
  exit 1
fi
# libpq consumes the connection through the environment, not process arguments.
connection_exports=$(python3 - <<'INNER'
import os, shlex
from urllib.parse import urlsplit, unquote
url = urlsplit(os.environ['DATABASE_URL'])
if url.scheme not in {'postgres', 'postgresql'} or url.query:
    raise SystemExit('Use PostgreSQL URL without query parameters; TLS uses libpq environment')
for key, value in [('PGHOST', url.hostname), ('PGPORT', str(url.port or 5432)),
                   ('PGUSER', unquote(url.username or '')), ('PGPASSWORD', unquote(url.password or '')),
                   ('PGDATABASE', unquote(url.path.lstrip('/')))]:
    print('export ' + key + '=' + shlex.quote(value or ''))
INNER
)
eval "$connection_exports"
unset connection_exports
temporary=$(mktemp "${1}.partial.XXXXXX")
trap 'rm -f "$temporary"' EXIT
pg_dump --format=custom --no-owner --no-acl --file="$temporary"
pg_restore --list "$temporary" >/dev/null
mv "$temporary" "$1"
