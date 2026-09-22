import os
from dataclasses import dataclass
from pathlib import Path

LOCAL_SOURCE_ODBC = (
    "DRIVER={ODBC Driver 18 for SQL Server};SERVER=lpc:.\\ERPLOCAL;"
    "DATABASE=ERP_Local;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes"
)


@dataclass(frozen=True)
class ApiSettings:
    report_path: Path | None = None

    @classmethod
    def from_env(cls):
        value = os.environ.get("ERP_REPORT_PATH")
        return cls(
            report_path=Path(value) if value else None,
        )


def source_odbc() -> str:
    return os.environ.get("ERP_SOURCE_ODBC", LOCAL_SOURCE_ODBC)
