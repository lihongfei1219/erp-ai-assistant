import pytest

from app.integrations.feishu_jobs import JobStoreError, SqlJobStore


def test_source_database_connection_is_rejected_before_any_write(monkeypatch):
    statements = []

    class Connection:
        closed = False

        def execute(self, statement):
            statements.append(statement)
            return self

        def fetchone(self):
            return ['ERP_Local']

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr('app.integrations.feishu_jobs.pyodbc.connect', lambda *a, **k: connection)
    store = SqlJobStore('cli_synthetic', 'tenant-synthetic')
    with pytest.raises(JobStoreError, match='独立'):
        store.check()
    assert statements == ['SELECT DB_NAME()']
    assert connection.closed
