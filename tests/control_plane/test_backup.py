import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which('pg_dump') or not shutil.which('pg_restore'), reason='PostgreSQL client tools required')
def test_backup_and_isolated_restore(owner, task_request, tmp_path):
    ledger, tenant, principal = owner
    ledger.create_task(tenant, principal, task_request)
    # Existing fixture DSN is converted to a URL without exposing it to command arguments.
    from urllib.parse import quote

    from psycopg.conninfo import conninfo_to_dict
    data = conninfo_to_dict(ledger.database_url)
    url = ('postgresql://' + quote(data['user'], safe='') + ':' + quote(data.get('password', ''), safe='')
           + '@' + data['host'] + ':' + data['port'] + '/' + data['dbname'])
    env = {**os.environ, 'DATABASE_URL': url}
    root = Path(__file__).resolve().parents[2]
    backup = tmp_path / 'ledger.dump'
    for script, argument in [('backup.sh', str(backup)), ('restore-check.sh', str(backup))]:
        result = subprocess.run([str(root / 'infrastructure/operations' / script), argument],
                                env=env, capture_output=True, timeout=60)
        assert result.returncode == 0, 'Backup/restore command failed (raw credentials omitted)'
    assert backup.stat().st_mode & 0o077 == 0
    result = subprocess.run([str(root / 'infrastructure/operations/backup.sh'), str(backup)],
                            env=env, capture_output=True, timeout=60)
    assert result.returncode != 0
