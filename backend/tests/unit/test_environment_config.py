"""Deployment configuration is portable, literal and independent of .local secrets."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.core.environment import configured_path, environment, read_env_file
from app.core.settings import ApiSettings, source_odbc
from app.integrations.feishu_app import AppConfigError, load_config
from app.notifications.feishu import FeishuSettings
from app.semantic.context import signing_key


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("ERP_ENV_FILE", str(path))
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_TENANT_KEY",
                "FEISHU_ALLOWED_CHAT_IDS", "FEISHU_ALLOWED_USER_OPEN_IDS",
                "FEISHU_USER_ACCESS_MODE", "ERP_CONTEXT_SIGNING_KEY", "ERP_REPORT_PATH",
                "ERP_SOURCE_ODBC", "FEISHU_WEBHOOK_URL", "FEISHU_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    return path


def test_env_literals_preserve_windows_odbc_password_and_do_not_export(env_file, monkeypatch):
    env_file.write_text(
        "ERP_SOURCE_ODBC='SERVER=lpc:.\\ERPLOCAL;PWD=p#${NOT_A_VAR}=value;'\n"
        "FEISHU_WEBHOOK_SECRET='literal # ${NOT_A_VAR}'\n", encoding="utf-8",
    )
    assert source_odbc() == r"SERVER=lpc:.\ERPLOCAL;PWD=p#${NOT_A_VAR}=value;"
    assert FeishuSettings.from_env().secret == "literal # ${NOT_A_VAR}"
    monkeypatch.setenv("ERP_SOURCE_ODBC", "override")
    assert source_odbc() == "override"


def test_env_only_feishu_authorization_and_explicit_empty_override(env_file, monkeypatch):
    env_file.write_text(
        "FEISHU_APP_ID=cli_test\nFEISHU_APP_SECRET=private-test\n"
        "FEISHU_TENANT_KEY=test\nFEISHU_ALLOWED_CHAT_IDS=oc_a, oc_b\n"
        "FEISHU_USER_ACCESS_MODE=all_group_members\n", encoding="utf-8",
    )
    config = load_config()
    config.require_authorized_groups()
    assert config.allowed_chat_ids == ("oc_a", "oc_b")
    assert config.allowed_user_open_ids == ()
    monkeypatch.setenv("FEISHU_ALLOWED_CHAT_IDS", "")
    with pytest.raises(AppConfigError):
        load_config().require_authorized_groups()


def test_default_does_not_read_legacy_json_or_webhook(env_file, monkeypatch, tmp_path):
    legacy = tmp_path / "feishu-app.json"
    legacy.write_text('{"app_id":"cli_old","app_secret":"old-secret"}', encoding="utf-8")
    monkeypatch.setattr("app.integrations.feishu_app.DEFAULT_CONFIG", legacy)
    monkeypatch.setattr("app.notifications.feishu.LOCAL_DIR", tmp_path)
    (tmp_path / "feishu-webhook.txt").write_text("old-private-url", encoding="utf-8")
    with pytest.raises(AppConfigError):
        load_config()
    assert load_config(legacy).app_id == "cli_old"
    assert FeishuSettings.from_env().webhook == ""


def test_relative_paths_are_project_based_not_cwd(env_file, monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.environment.ROOT", tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    env_file.write_text("ERP_REPORT_PATH=data/report.json\nERP_SERVICES_LOCK=state/run.lock\n")
    assert ApiSettings.from_env().report_path == tmp_path / "data/report.json"
    assert configured_path("ERP_SERVICES_LOCK", "unused") == tmp_path / "state/run.lock"


def test_invalid_env_does_not_disclose_contents(env_file):
    env_file.write_text("FEISHU_APP_SECRET='private-test\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        environment()
    assert "private-test" not in str(exc.value)


def test_signature_env_matches_legacy_key(env_file, tmp_path):
    raw = bytes(range(32))
    legacy = tmp_path / "legacy.key"
    legacy.write_bytes(raw)
    expected = signing_key("owner", path=legacy)
    env_file.write_text(f"ERP_CONTEXT_SIGNING_KEY='{raw.hex()}'\n", encoding="utf-8")
    assert signing_key("owner") == expected


def test_migration_roundtrip_removes_only_legacy_configuration(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts/migrate_env.py"
    spec = importlib.util.spec_from_file_location("migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("ERP_CONTEXT_SIGNING_KEY", raising=False)
    local = tmp_path / ".local"
    local.mkdir()
    config = {"app_id": "cli_test", "app_secret": "s'\\#${VALUE}", "tenant_key": "tenant",
              "allowed_chat_ids": ["oc_one"], "user_access_mode": "all_group_members"}
    (local / "feishu-app.json").write_text(json.dumps(config), encoding="utf-8")
    (local / "semantic-context.key").write_bytes(bytes(range(32)))
    (local / "runtime-state.json").write_text("preserve", encoding="utf-8")
    (tmp_path / ".env").write_text("ERP_AI_ENABLED=false\n", encoding="utf-8")
    before = (tmp_path / ".env").read_bytes()
    module.migrate(tmp_path)
    assert (tmp_path / ".env").read_bytes() == before
    module.migrate(tmp_path, apply=True)
    values = read_env_file(tmp_path / ".env")
    assert values["FEISHU_APP_SECRET"] == config["app_secret"]
    assert values["ERP_CONTEXT_SIGNING_KEY"] == bytes(range(32)).hex()
    assert not (local / "feishu-app.json").exists()
    assert not (local / "semantic-context.key").exists()
    assert (local / "runtime-state.json").read_text() == "preserve"
    module.migrate(tmp_path, apply=True)
    assert read_env_file(tmp_path / ".env") == values


def test_invalid_migration_preserves_originals(tmp_path):
    path = Path(__file__).resolve().parents[3] / "scripts/migrate_env.py"
    spec = importlib.util.spec_from_file_location("invalid_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / ".local").mkdir()
    original = tmp_path / ".local/feishu-app.json"
    original.write_text('{"bad":"private-test"}', encoding="utf-8")
    with pytest.raises(ValueError):
        module.migrate(tmp_path, apply=True)
    assert original.exists() and not (tmp_path / ".env").exists()


def test_launcher_uses_env_then_explicit_cli_without_legacy_config(env_file, monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[3] / "start.py"
    spec = importlib.util.spec_from_file_location("env_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("app.core.environment.ROOT", tmp_path)
    (tmp_path / "report.json").write_text("{}")
    env_file.write_text("ERP_HOST=0.0.0.0\nERP_PORT=8123\nERP_REPORT_PATH=report.json\n")
    args = module.arguments([])
    assert args.host == "0.0.0.0" and args.port == 8123
    assert args.report_path == tmp_path / "report.json" and args.config is None
    assert "--config" not in module.child_command(args, "feishu")
    override = module.arguments(["--host", "127.0.0.1", "--port", "8124"])
    assert override.host == "127.0.0.1" and override.port == 8124


def test_assistant_connection_uses_env_without_connecting_database(env_file, monkeypatch):
    from types import SimpleNamespace

    from app.integrations.feishu_jobs import SqlJobStore

    expected = "DRIVER={Example};SERVER=tcp:db.example,1433;DATABASE=ERP_AI_Assistant;PWD=x#y"
    env_file.write_text(f"ERP_ASSISTANT_ODBC='{expected}'\n")
    monkeypatch.delenv("ERP_ASSISTANT_ODBC", raising=False)
    called = []
    connection = SimpleNamespace(
        execute=lambda _: SimpleNamespace(fetchone=lambda: ("ERP_AI_Assistant",)),
        close=lambda: None,
    )

    def connect(odbc, **kwargs):
        called.append(odbc)
        return connection

    monkeypatch.setattr("app.integrations.feishu_jobs.pyodbc.connect", connect)
    with SqlJobStore("app", "tenant").connection():
        pass
    assert called == [expected]
