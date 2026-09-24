"""Read deployment configuration without exporting secrets or interpolating values."""

import os
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values
from dotenv.parser import parse_stream

ROOT = Path(__file__).resolve().parents[3]


def project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def read_env_file(path: Path) -> dict[str, str]:
    try:
        if not path.is_file():
            return {}
        source = path.read_text(encoding="utf-8-sig")
        if any(binding.error for binding in parse_stream(StringIO(source))):
            raise ValueError()
        return {
            key: value
            for key, value in dotenv_values(
                stream=StringIO(source),
                interpolate=False,
            ).items()
            if value is not None
        }
    except (OSError, ValueError):
        raise ValueError("无法读取 .env，请检查格式及权限（未输出配置值）。") from None


def environment(path: Path | None = None) -> dict[str, str]:
    source = path or project_path(os.environ.get("ERP_ENV_FILE", ".env"))
    return {**read_env_file(source), **os.environ}


def configured_path(key: str, default: str | Path) -> Path:
    return project_path(environment().get(key) or default)
