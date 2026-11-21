import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from xmux_control.db.ledger import Ledger
from xmux_control.db.migrate import migrate


@pytest.fixture(scope='session')
def database_url():
    source = os.environ.get('DATABASE_URL')
    if not source:
        pytest.fail('DATABASE_URL is required: start development dependencies with make deps')
    name = 'xmux_test_' + uuid4().hex
    params = conninfo_to_dict(source)
    with psycopg.connect(source, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    params['dbname'] = name
    url = make_conninfo(**params)
    try:
        migrate(url)
        yield url
    finally:
        with psycopg.connect(source, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture
def owner(database_url):
    tenant, principal = uuid4(), uuid4()
    ledger = Ledger(database_url)
    with ledger.connect() as conn:
        conn.execute('INSERT INTO principals(tenant_id,id) VALUES(%s,%s)', (tenant, principal))
    return ledger, tenant, principal


@pytest.fixture
def task_request():
    return {'schema_version': '1', 'idempotency_key': str(uuid4()), 'intent': 'research',
            'objective': 'Evaluate the supplied source', 'context_refs': []}
