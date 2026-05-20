from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import psycopg2
from psycopg2.extensions import connection as PgConnection


logger = logging.getLogger(__name__)
LOG_SEP = "\u2014"


@dataclass(frozen=True)
class DbConnectionConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = ""
    database: str = ""

    def validate(self) -> None:
        logger.debug(
            "[DbConnectionConfig] validate %s host=%s, port=%s, user=%s, database=%s, password_set=%s",
            LOG_SEP,
            self.host.strip(),
            self.port,
            self.user.strip(),
            self.database.strip(),
            bool(self.password),
        )
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
            logger.warning(
                "[DbConnectionConfig] validate %s FAILED missing=%s",
                LOG_SEP,
                ",".join(missing),
            )
            raise ValueError("Please enter " + ", ".join(missing) + ".")
        logger.debug(
            "[DbConnectionConfig] validate %s success database=%s",
            LOG_SEP,
            self.database.strip(),
        )


def connect_to_database(config: DbConnectionConfig) -> PgConnection:
    logger.info(
        "[Database] connect_to_database %s host=%s, port=%s, user=%s, database=%s, password_set=%s",
        LOG_SEP,
        config.host.strip(),
        config.port,
        config.user.strip(),
        config.database.strip(),
        bool(config.password),
    )
    config.validate()
    try:
        conn = psycopg2.connect(
            host=config.host.strip(),
            port=config.port,
            user=config.user.strip(),
            password=config.password,
            dbname=config.database.strip(),
            connect_timeout=10,
            application_name="pyqt_postgres_backup_tool",
        )
    except Exception:
        logger.exception(
            "[Database] connect_to_database %s FAILED host=%s, port=%s, user=%s, database=%s",
            LOG_SEP,
            config.host.strip(),
            config.port,
            config.user.strip(),
            config.database.strip(),
        )
        raise
    logger.info(
        "[Database] connect_to_database %s success database=%s",
        LOG_SEP,
        config.database.strip(),
    )
    return conn


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qualified_name(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quoted_column_list(columns: Iterable[str]) -> str:
    return ", ".join(quote_identifier(column) for column in columns)
