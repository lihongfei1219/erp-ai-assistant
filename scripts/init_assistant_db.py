"""Explicitly initialize the separate local assistant database; never modify ERP_Local."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import pyodbc

from app.core.environment import environment
from app.integrations.feishu_jobs import SqlJobStore


def main():
    admin_odbc = environment().get('ERP_ASSISTANT_ADMIN_ODBC')
    if not admin_odbc:
        raise SystemExit('请在 .env 配置 ERP_ASSISTANT_ADMIN_ODBC 后再初始化助手库。')
    connection = pyodbc.connect(
        admin_odbc,
        timeout=5, autocommit=True)
    try:
        if connection.execute('SELECT DB_ID(?)', 'ERP_AI_Assistant').fetchone()[0] is None:
            connection.execute('CREATE DATABASE [ERP_AI_Assistant]')
    finally:
        connection.close()
    store = SqlJobStore('schema-setup', 'schema-setup')
    with store.connection() as connection:
        if connection.execute("SELECT SCHEMA_ID('erp_ai')").fetchone()[0] is None:
            connection.execute('CREATE SCHEMA erp_ai')
        if connection.execute("SELECT OBJECT_ID('erp_ai.feishu_sales_jobs')").fetchone()[0] is None:
            connection.execute('''
                CREATE TABLE erp_ai.feishu_sales_jobs (
                    job_id varchar(64) NOT NULL PRIMARY KEY,
                    app_id nvarchar(100) NOT NULL,
                    tenant_key nvarchar(100) NOT NULL,
                    chat_id nvarchar(200) NOT NULL,
                    user_open_id nvarchar(200) NOT NULL,
                    message_id nvarchar(200) NOT NULL,
                    event_id nvarchar(200) NOT NULL,
                    status varchar(20) NOT NULL CHECK (status IN
                        ('queued','analyzing','ready','sending','sent','unknown','rejected',
                         'revoked','failed')),
                    payload_json nvarchar(max) NOT NULL CHECK (ISJSON(payload_json)=1),
                    result_json nvarchar(max) NULL,
                    reply_text nvarchar(max) NULL,
                    error_code int NULL,
                    lease_token varchar(32) NULL,
                    lease_until datetime2 NULL,
                    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    updated_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    CONSTRAINT uq_feishu_message UNIQUE(app_id,tenant_key,message_id),
                    CONSTRAINT uq_feishu_event UNIQUE(app_id,tenant_key,event_id)
                )
            ''')
            connection.execute('CREATE INDEX ix_feishu_queue ON erp_ai.feishu_sales_jobs '
                               '(app_id,tenant_key,status,created_at)')
        connection.commit()
    print('ERP_AI_Assistant schema ready. Source ERP database was not modified.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Assistant initialization failed:', type(exc).__name__)
        raise SystemExit(1) from None
