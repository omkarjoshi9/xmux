#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL through the secret environment}"
: "${1:?Usage: restore-check.sh backup.dump}"
python3 - "$1" <<'PY'
import os
import subprocess
import sys
from urllib.parse import urlsplit, unquote
from uuid import uuid4

url = urlsplit(os.environ['DATABASE_URL'])
if url.scheme not in {'postgres', 'postgresql'} or url.query:
    raise SystemExit('Use a PostgreSQL URL without query parameters; set TLS through libpq environment')
env = os.environ.copy()
for key, value in [('PGHOST', url.hostname), ('PGPORT', str(url.port or 5432)),
                   ('PGUSER', unquote(url.username or '')), ('PGPASSWORD', unquote(url.password or ''))]:
    env[key] = value
name = 'xmux_restore_' + uuid4().hex
env['PGDATABASE'] = name

def run(args):
    subprocess.run(args, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    run(['createdb', name])
except subprocess.CalledProcessError:
    raise SystemExit('Could not create isolated restore database') from None
try:
    run(['pg_restore', '--exit-on-error', '--no-owner', '--no-acl', '--dbname='+name, sys.argv[1]])
    run(['psql', '--no-psqlrc', '--set=ON_ERROR_STOP=1', '--command',
         'SELECT count(*) FROM schema_migrations; SELECT count(*) FROM tasks;'])
except subprocess.CalledProcessError:
    raise SystemExit('Restore validation failed; credentials and raw database errors omitted') from None
finally:
    run(['dropdb', '--if-exists', name])
PY
