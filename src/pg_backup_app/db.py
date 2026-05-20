from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import psycopg2
from psycopg2.extensions import connection as PgConnection


@dataclass(frozen=True)
class DbConnectionConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = ""
    database: str = ""

    def validate(self) -> None:
        missing = []
        if not self.host.strip():
            missing.append("host")
        if not self.user.strip():
            missing.append("user")
        if not self.database.strip():
            missing.append("database name")
        if self.port <= 0:
            missing.append("valid port")
        if missing:
            raise ValueError("Please enter " + ", ".join(missing) + ".")


def connect_to_database(config: DbConnectionConfig) -> PgConnection:
    config.validate()
    return psycopg2.connect(
        host=config.host.strip(),
        port=config.port,
        user=config.user.strip(),
        password=config.password,
        dbname=config.database.strip(),
        connect_timeout=10,
        application_name="pyqt_postgres_backup_tool",
    )


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qualified_name(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quoted_column_list(columns: Iterable[str]) -> str:
    return ", ".join(quote_identifier(column) for column in columns)
