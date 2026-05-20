from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from psycopg2 import sql
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import Json

from .db import (
    DbConnectionConfig,
    connect_to_database,
    qualified_name,
    quote_identifier,
    quoted_column_list,
    sql_literal,
)


ProgressCallback = Callable[[str], None]
logger = logging.getLogger(__name__)
LOG_SEP = "\u2014"


class BackupServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str
    oid: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.schema, self.name)


class BackupService:
    def backup_database(
        self,
        config: DbConnectionConfig,
        destination_parent: Path,
        progress: ProgressCallback | None = None,
    ) -> Path:
        logger.info(
            "[BackupService] backup_database %s start database=%s, destination_parent=%s",
            LOG_SEP,
            config.database.strip(),
            destination_parent,
        )
        self._ensure_directory(destination_parent)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = destination_parent / f"{self._safe_name(config.database)}_backup_{timestamp}"
        csv_dir = backup_root / "csv"
        sql_dir = backup_root / "sql"
        ddl_dir = backup_root / "table_creation_sql"
        for folder in (csv_dir, sql_dir, ddl_dir):
            folder.mkdir(parents=True, exist_ok=False)
            logger.debug(
                "[BackupService] backup_database %s folder_created path=%s",
                LOG_SEP,
                folder,
            )

        self._notify(progress, "Connecting to PostgreSQL...")
        try:
            with connect_to_database(config) as conn:
                tables = self._get_user_tables(conn)
                if not tables:
                    logger.warning(
                        "[BackupService] backup_database %s no_tables database=%s",
                        LOG_SEP,
                        config.database.strip(),
                    )
                    raise BackupServiceError("No user tables were found in this database.")

                ordered_tables = self._sort_tables_by_foreign_keys(conn, tables)
                logger.info(
                    "[BackupService] backup_database %s tables_discovered count=%d",
                    LOG_SEP,
                    len(ordered_tables),
                )
                self._notify(progress, f"Found {len(ordered_tables)} table(s).")
                ddl_sql = self._build_create_tables_sql(conn, ordered_tables)
                ddl_path = ddl_dir / "create_tables.sql"
                ddl_path.write_text(ddl_sql, encoding="utf-8")
                logger.info(
                    "[BackupService] backup_database %s ddl_written path=%s, bytes=%d",
                    LOG_SEP,
                    ddl_path,
                    len(ddl_sql.encode("utf-8")),
                )

                manifest_tables = []
                for index, table in enumerate(ordered_tables, start=1):
                    label = f"{table.schema}.{table.name}"
                    safe_base = self._safe_name(label)
                    csv_name = f"{safe_base}.csv"
                    sql_name = f"{safe_base}.sql"
                    columns = self._get_restorable_table_columns(conn, table)
                    logger.info(
                        "[BackupService] backup_database %s exporting_table index=%d, total=%d, table=%s, columns=%d",
                        LOG_SEP,
                        index,
                        len(ordered_tables),
                        label,
                        len(columns),
                    )

                    self._notify(progress, f"[{index}/{len(ordered_tables)}] Exporting {label} to CSV...")
                    self._export_table_csv(conn, table, columns, csv_dir / csv_name)
                    self._notify(progress, f"[{index}/{len(ordered_tables)}] Exporting {label} to SQL...")
                    self._export_table_inserts(conn, table, columns, sql_dir / sql_name)

                    manifest_tables.append(
                        {
                            "schema": table.schema,
                            "table": table.name,
                            "csv_file": f"csv/{csv_name}",
                            "sql_file": f"sql/{sql_name}",
                            "columns": columns,
                        }
                    )

                manifest = {
                    "database": config.database,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "format_version": 1,
                    "tables": manifest_tables,
                }
                manifest_path = backup_root / "manifest.json"
                manifest_path.write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8"
                )
                logger.info(
                    "[BackupService] backup_database %s manifest_written path=%s, tables=%d",
                    LOG_SEP,
                    manifest_path,
                    len(manifest_tables),
                )
        except Exception:
            logger.exception(
                "[BackupService] backup_database %s FAILED database=%s, backup_root=%s",
                LOG_SEP,
                config.database.strip(),
                backup_root,
            )
            raise

        self._notify(progress, f"Backup completed: {backup_root}")
        logger.info(
            "[BackupService] backup_database %s success backup_root=%s",
            LOG_SEP,
            backup_root,
        )
        return backup_root

    def restore_database(
        self,
        config: DbConnectionConfig,
        backup_root: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        logger.info(
            "[BackupService] restore_database %s start database=%s, backup_root=%s",
            LOG_SEP,
            config.database.strip(),
            backup_root,
        )
        if not backup_root.exists() or not backup_root.is_dir():
            logger.warning(
                "[BackupService] restore_database %s invalid_backup_folder path=%s",
                LOG_SEP,
                backup_root,
            )
            raise BackupServiceError("Please select a valid backup folder.")

        manifest_path = backup_root / "manifest.json"
        ddl_path = backup_root / "table_creation_sql" / "create_tables.sql"
        if not manifest_path.exists() or not ddl_path.exists():
            logger.warning(
                "[BackupService] restore_database %s missing_required_files manifest_exists=%s, ddl_exists=%s",
                LOG_SEP,
                manifest_path.exists(),
                ddl_path.exists(),
            )
            raise BackupServiceError(
                "The selected folder is missing manifest.json or table_creation_sql/create_tables.sql."
            )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tables = manifest.get("tables", [])
        if not isinstance(tables, list) or not tables:
            logger.warning(
                "[BackupService] restore_database %s empty_manifest manifest_path=%s",
                LOG_SEP,
                manifest_path,
            )
            raise BackupServiceError("The backup manifest does not contain any tables.")
        logger.info(
            "[BackupService] restore_database %s manifest_loaded tables=%d, source_database=%s",
            LOG_SEP,
            len(tables),
            manifest.get("database", ""),
        )

        self._notify(progress, "Connecting to PostgreSQL...")
        try:
            source_enum_labels = self._load_source_enum_labels(config, manifest.get("database"), tables)
            with connect_to_database(config) as conn:
                conn.autocommit = False
                logger.debug(
                    "[BackupService] restore_database %s transaction_started database=%s",
                    LOG_SEP,
                    config.database.strip(),
                )
                try:
                    with conn.cursor() as cur:
                        self._notify(progress, "Preparing restore SQL...")
                        restore_ddl = self._read_restore_ddl(
                            ddl_path,
                            backup_root,
                            tables,
                            source_enum_labels,
                        )
                        schema_ddl, foreign_key_ddl = self._split_foreign_key_constraint_blocks(
                            restore_ddl
                        )
                        logger.info(
                            "[BackupService] restore_database %s executing_schema_ddl bytes=%d, foreign_key_bytes=%d",
                            LOG_SEP,
                            len(schema_ddl.encode("utf-8")),
                            len(foreign_key_ddl.encode("utf-8")),
                        )
                        self._notify(progress, "Creating schemas and tables...")
                        cur.execute(schema_ddl)

                    self._notify(progress, "Preparing tables for restore...")
                    self._truncate_restore_tables(conn, tables)

                    for index, table_info in enumerate(tables, start=1):
                        schema = str(table_info["schema"])
                        table = str(table_info["table"])
                        columns = [str(column) for column in table_info["columns"]]
                        csv_path = backup_root / str(table_info["csv_file"])
                        if not csv_path.exists():
                            logger.error(
                                "[BackupService] restore_database %s missing_csv table=%s.%s, path=%s",
                                LOG_SEP,
                                schema,
                                table,
                                csv_path,
                            )
                            raise BackupServiceError(f"Missing CSV file: {csv_path}")

                        logger.info(
                            "[BackupService] restore_database %s restoring_table index=%d, total=%d, table=%s.%s, columns=%d",
                            LOG_SEP,
                            index,
                            len(tables),
                            schema,
                            table,
                            len(columns),
                        )
                        self._notify(progress, f"[{index}/{len(tables)}] Restoring {schema}.{table}...")
                        if columns:
                            self._restore_table_csv(conn, schema, table, columns, csv_path)
                        else:
                            logger.warning(
                                "[BackupService] restore_database %s skipped_table_no_columns table=%s.%s",
                                LOG_SEP,
                                schema,
                                table,
                            )
                            self._notify(
                                progress,
                                f"[{index}/{len(tables)}] Skipped {schema}.{table}; no restorable columns.",
                            )

                    if foreign_key_ddl.strip():
                        self._notify(progress, "Creating foreign keys...")
                        logger.info(
                            "[BackupService] restore_database %s executing_foreign_keys bytes=%d",
                            LOG_SEP,
                            len(foreign_key_ddl.encode("utf-8")),
                        )
                        with conn.cursor() as cur:
                            cur.execute(foreign_key_ddl)

                    self._notify(progress, "Resetting sequences...")
                    self._sync_sequence_values(conn, tables, progress)
                    conn.commit()
                    logger.info(
                        "[BackupService] restore_database %s transaction_committed database=%s",
                        LOG_SEP,
                        config.database.strip(),
                    )
                except Exception:
                    logger.exception(
                        "[BackupService] restore_database %s FAILED rollback_started backup_root=%s",
                        LOG_SEP,
                        backup_root,
                    )
                    conn.rollback()
                    logger.info(
                        "[BackupService] restore_database %s rollback_completed backup_root=%s",
                        LOG_SEP,
                        backup_root,
                    )
                    raise
        except Exception:
            logger.exception(
                "[BackupService] restore_database %s FAILED database=%s, backup_root=%s",
                LOG_SEP,
                config.database.strip(),
                backup_root,
            )
            raise

        self._notify(progress, "Restore completed.")
        logger.info(
            "[BackupService] restore_database %s success database=%s, backup_root=%s",
            LOG_SEP,
            config.database.strip(),
            backup_root,
        )

    def _get_user_tables(self, conn: PgConnection) -> list[TableRef]:
        logger.debug("[BackupService] _get_user_tables %s start", LOG_SEP)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname, c.relname, c.oid
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p')
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND n.nspname NOT LIKE 'pg_toast%'
                ORDER BY n.nspname, c.relname
                """
            )
            tables = [TableRef(schema=row[0], name=row[1], oid=row[2]) for row in cur.fetchall()]
        logger.debug(
            "[BackupService] _get_user_tables %s success count=%d",
            LOG_SEP,
            len(tables),
        )
        return tables

    def _get_restorable_table_columns(self, conn: PgConnection, table: TableRef) -> list[str]:
        logger.debug(
            "[BackupService] _get_restorable_table_columns %s table=%s.%s",
            LOG_SEP,
            table.schema,
            table.name,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.attname
                FROM pg_attribute a
                WHERE a.attrelid = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                  AND a.attgenerated = ''
                ORDER BY a.attnum
                """,
                (table.oid,),
            )
            columns = [row[0] for row in cur.fetchall()]
        logger.debug(
            "[BackupService] _get_restorable_table_columns %s success table=%s.%s, columns=%d",
            LOG_SEP,
            table.schema,
            table.name,
            len(columns),
        )
        return columns

    def _sort_tables_by_foreign_keys(
        self, conn: PgConnection, tables: Sequence[TableRef]
    ) -> list[TableRef]:
        logger.debug(
            "[BackupService] _sort_tables_by_foreign_keys %s tables=%d",
            LOG_SEP,
            len(tables),
        )
        table_by_key = {table.key: table for table in tables}
        dependencies: dict[tuple[str, str], set[tuple[str, str]]] = {
            table.key: set() for table in tables
        }

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT child_ns.nspname, child.relname, parent_ns.nspname, parent.relname
                FROM pg_constraint con
                JOIN pg_class child ON child.oid = con.conrelid
                JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
                JOIN pg_class parent ON parent.oid = con.confrelid
                JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
                WHERE con.contype = 'f'
                """
            )
            for child_schema, child_table, parent_schema, parent_table in cur.fetchall():
                child_key = (child_schema, child_table)
                parent_key = (parent_schema, parent_table)
                if child_key in dependencies and parent_key in table_by_key:
                    dependencies[child_key].add(parent_key)
        dependency_count = sum(len(parents) for parents in dependencies.values())
        logger.debug(
            "[BackupService] _sort_tables_by_foreign_keys %s dependencies=%d",
            LOG_SEP,
            dependency_count,
        )

        ordered: list[TableRef] = []
        remaining = dict(dependencies)
        while remaining:
            ready = [key for key, parents in remaining.items() if not parents.intersection(remaining)]
            if not ready:
                logger.warning(
                    "[BackupService] _sort_tables_by_foreign_keys %s cycle_detected remaining=%d",
                    LOG_SEP,
                    len(remaining),
                )
                ordered.extend(table_by_key[key] for key in sorted(remaining))
                break
            for key in sorted(ready):
                ordered.append(table_by_key[key])
                remaining.pop(key)
        logger.debug(
            "[BackupService] _sort_tables_by_foreign_keys %s success ordered=%d",
            LOG_SEP,
            len(ordered),
        )
        return ordered

    def _build_create_tables_sql(self, conn: PgConnection, tables: Sequence[TableRef]) -> str:
        logger.debug(
            "[BackupService] _build_create_tables_sql %s tables=%d",
            LOG_SEP,
            len(tables),
        )
        lines = [
            "-- Generated by PyQt PostgreSQL Backup Tool",
            "-- Creates schemas, tables, constraints, and non-constraint indexes.",
            "",
        ]

        for schema in sorted({table.schema for table in tables}):
            lines.append(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema)};")
        lines.append("")
        lines.extend(self._build_required_extension_sql(conn, tables))
        enum_lines = self._build_enum_types_sql(conn, tables)
        if enum_lines:
            lines.extend(enum_lines)
            lines.append("")

        primary_unique_check_constraints: list[str] = []
        foreign_key_constraints: list[str] = []
        index_lines: list[str] = []
        for table in tables:
            table_sql, constraints, indexes = self._build_single_table_sql(conn, table)
            lines.extend(table_sql)
            lines.append("")
            for constraint_type, constraint_block in constraints:
                if constraint_type == "f":
                    foreign_key_constraints.append(constraint_block)
                else:
                    primary_unique_check_constraints.append(constraint_block)
            index_lines.extend(indexes)

        constraint_blocks = primary_unique_check_constraints + foreign_key_constraints
        lines.extend(constraint_blocks)
        if constraint_blocks:
            lines.append("")
        lines.extend(index_lines)
        lines.append("")
        ddl_sql = self._inject_missing_sequence_creates("\n".join(lines))
        logger.debug(
            "[BackupService] _build_create_tables_sql %s success bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        return ddl_sql

    def _build_single_table_sql(
        self, conn: PgConnection, table: TableRef
    ) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        logger.debug(
            "[BackupService] _build_single_table_sql %s table=%s.%s",
            LOG_SEP,
            table.schema,
            table.name,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.attname,
                    pg_catalog.format_type(a.atttypid, a.atttypmod),
                    pg_get_expr(ad.adbin, ad.adrelid),
                    a.attnotnull,
                    a.attidentity,
                    a.attgenerated
                FROM pg_attribute a
                LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
                WHERE a.attrelid = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (table.oid,),
            )
            column_rows = cur.fetchall()

            cur.execute(
                """
                SELECT conname, pg_get_constraintdef(oid, true), contype
                FROM pg_constraint
                WHERE conrelid = %s
                ORDER BY CASE contype
                    WHEN 'p' THEN 1
                    WHEN 'u' THEN 2
                    WHEN 'c' THEN 3
                    WHEN 'f' THEN 4
                    ELSE 5
                END, conname
                """,
                (table.oid,),
            )
            constraints = cur.fetchall()
            constraint_names = {row[0] for row in constraints}

            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s
                ORDER BY indexname
                """,
                (table.schema, table.name),
            )
            indexes = [
                self._index_with_if_not_exists(row[1])
                for row in cur.fetchall()
                if row[0] not in constraint_names
            ]

        column_lines = []
        for name, data_type, default_expr, not_null, identity, generated in column_rows:
            parts = [quote_identifier(name), data_type]
            if identity == "a":
                parts.append("GENERATED ALWAYS AS IDENTITY")
            elif identity == "d":
                parts.append("GENERATED BY DEFAULT AS IDENTITY")
            elif generated == "s" and default_expr:
                parts.append(f"GENERATED ALWAYS AS ({default_expr}) STORED")
            elif default_expr:
                parts.append(f"DEFAULT {default_expr}")
            if not_null:
                parts.append("NOT NULL")
            column_lines.append("    " + " ".join(parts))

        create_lines = [
            f"CREATE TABLE IF NOT EXISTS {qualified_name(table.schema, table.name)} (",
            ",\n".join(column_lines),
            ");",
        ]
        constraint_blocks = [
            (constraint_type, self._constraint_block(table, name, definition))
            for name, definition, constraint_type in constraints
            if constraint_type != "n" and not str(definition).upper().startswith("NOT NULL ")
        ]
        logger.debug(
            "[BackupService] _build_single_table_sql %s success table=%s.%s, columns=%d, constraints=%d, indexes=%d",
            LOG_SEP,
            table.schema,
            table.name,
            len(column_lines),
            len(constraint_blocks),
            len(indexes),
        )
        return create_lines, constraint_blocks, indexes

    def _constraint_block(self, table: TableRef, name: str, definition: str) -> str:
        table_regclass = qualified_name(table.schema, table.name)
        return "\n".join(
            [
                "DO $$",
                "BEGIN",
                "    IF NOT EXISTS (",
                "        SELECT 1",
                "        FROM pg_constraint",
                f"        WHERE conname = {sql_literal(name)}",
                f"          AND conrelid = {sql_literal(table_regclass)}::regclass",
                "    ) THEN",
                f"        ALTER TABLE ONLY {table_regclass} ADD CONSTRAINT {quote_identifier(name)} {definition};",
                "    END IF;",
                "END $$;",
            ]
        )

    def _index_with_if_not_exists(self, indexdef: str) -> str:
        index_sql = re.sub(
            r"^CREATE\s+(UNIQUE\s+)?INDEX\s+",
            r"CREATE \1INDEX IF NOT EXISTS ",
            indexdef,
            flags=re.I,
        )
        return self._ensure_sql_statement_terminator(index_sql)

    def _build_required_extension_sql(
        self, conn: PgConnection, tables: Sequence[TableRef]
    ) -> list[str]:
        table_oids = [table.oid for table in tables]
        if not table_oids:
            return []

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_get_expr(ad.adbin, ad.adrelid)
                FROM pg_attrdef ad
                WHERE ad.adrelid = ANY(%s)
                """,
                (table_oids,),
            )
            default_sql = "\n".join(str(row[0]) for row in cur.fetchall() if row[0])

        extension_lines = []
        if "uuid_generate_v4" in default_sql:
            extension_lines.append('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        if "gen_random_uuid" in default_sql:
            extension_lines.append('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
        if extension_lines:
            logger.info(
                "[BackupService] _build_required_extension_sql %s extensions=%d",
                LOG_SEP,
                len(extension_lines),
            )
        return extension_lines

    def _build_enum_types_sql(self, conn: PgConnection, tables: Sequence[TableRef]) -> list[str]:
        table_oids = [table.oid for table in tables]
        if not table_oids:
            return []

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT enum_ns.nspname, enum_type.typname, enum_value.enumlabel
                FROM pg_attribute attr
                JOIN pg_type enum_type ON enum_type.oid = attr.atttypid
                JOIN pg_namespace enum_ns ON enum_ns.oid = enum_type.typnamespace
                JOIN pg_enum enum_value ON enum_value.enumtypid = enum_type.oid
                WHERE attr.attrelid = ANY(%s)
                  AND attr.attnum > 0
                  AND NOT attr.attisdropped
                  AND enum_type.typtype = 'e'
                GROUP BY enum_ns.nspname, enum_type.typname, enum_value.enumlabel, enum_value.enumsortorder
                ORDER BY enum_ns.nspname, enum_type.typname, enum_value.enumsortorder
                """,
                (table_oids,),
            )
            rows = cur.fetchall()

        enum_labels: dict[tuple[str, str], list[str]] = {}
        for schema, type_name, label in rows:
            enum_labels.setdefault((str(schema), str(type_name)), []).append(str(label))

        enum_blocks = [
            self._enum_type_block(schema, type_name, labels)
            for (schema, type_name), labels in sorted(enum_labels.items())
        ]
        if enum_blocks:
            logger.info(
                "[BackupService] _build_enum_types_sql %s enum_types=%d",
                LOG_SEP,
                len(enum_blocks),
            )
        return enum_blocks

    def _load_source_enum_labels(
        self,
        target_config: DbConnectionConfig,
        source_database: object,
        manifest_tables: Sequence[dict],
    ) -> dict[tuple[str, str], list[str]]:
        source_database_name = str(source_database or "").strip()
        if not source_database_name:
            logger.debug("[BackupService] _load_source_enum_labels %s no_source_database", LOG_SEP)
            return {}
        if source_database_name == target_config.database.strip():
            logger.debug(
                "[BackupService] _load_source_enum_labels %s source_is_target database=%s",
                LOG_SEP,
                source_database_name,
            )
            return {}

        source_config = DbConnectionConfig(
            host=target_config.host,
            port=target_config.port,
            user=target_config.user,
            password=target_config.password,
            database=source_database_name,
        )
        try:
            with connect_to_database(source_config) as source_conn:
                labels = self._get_enum_labels_for_manifest_tables(source_conn, manifest_tables)
        except Exception as exc:
            logger.warning(
                "[BackupService] _load_source_enum_labels %s unavailable source_database=%s, error=%s",
                LOG_SEP,
                source_database_name,
                exc,
            )
            return {}

        logger.info(
            "[BackupService] _load_source_enum_labels %s success source_database=%s, enum_types=%d",
            LOG_SEP,
            source_database_name,
            len(labels),
        )
        return labels

    def _get_enum_labels_for_manifest_tables(
        self,
        conn: PgConnection,
        manifest_tables: Sequence[dict],
    ) -> dict[tuple[str, str], list[str]]:
        table_pairs = sorted(
            {
                (str(table_info["schema"]), str(table_info["table"]))
                for table_info in manifest_tables
            }
        )
        if not table_pairs:
            return {}

        with conn.cursor() as cur:
            cur.execute(
                """
                WITH manifest_tables(schema_name, table_name) AS (
                    SELECT * FROM unnest(%s::text[], %s::text[])
                )
                SELECT enum_ns.nspname, enum_type.typname, enum_value.enumlabel
                FROM manifest_tables mt
                JOIN pg_namespace table_ns ON table_ns.nspname = mt.schema_name
                JOIN pg_class table_class
                  ON table_class.relnamespace = table_ns.oid
                 AND table_class.relname = mt.table_name
                JOIN pg_attribute attr ON attr.attrelid = table_class.oid
                JOIN pg_type enum_type ON enum_type.oid = attr.atttypid
                JOIN pg_namespace enum_ns ON enum_ns.oid = enum_type.typnamespace
                JOIN pg_enum enum_value ON enum_value.enumtypid = enum_type.oid
                WHERE attr.attnum > 0
                  AND NOT attr.attisdropped
                  AND enum_type.typtype = 'e'
                GROUP BY enum_ns.nspname, enum_type.typname, enum_value.enumlabel, enum_value.enumsortorder
                ORDER BY enum_ns.nspname, enum_type.typname, enum_value.enumsortorder
                """,
                ([schema for schema, _ in table_pairs], [table for _, table in table_pairs]),
            )
            rows = cur.fetchall()

        enum_labels: dict[tuple[str, str], list[str]] = {}
        for schema, type_name, label in rows:
            enum_labels.setdefault((str(schema), str(type_name)), []).append(str(label))
        logger.debug(
            "[BackupService] _get_enum_labels_for_manifest_tables %s enum_types=%d",
            LOG_SEP,
            len(enum_labels),
        )
        return enum_labels

    def _enum_type_block(self, schema: str, type_name: str, labels: Sequence[str]) -> str:
        enum_values = ", ".join(sql_literal(label) for label in labels)
        return "\n".join(
            [
                "DO $$",
                "BEGIN",
                "    IF NOT EXISTS (",
                "        SELECT 1",
                "        FROM pg_type t",
                "        JOIN pg_namespace n ON n.oid = t.typnamespace",
                f"        WHERE n.nspname = {sql_literal(schema)}",
                f"          AND t.typname = {sql_literal(type_name)}",
                "    ) THEN",
                f"        CREATE TYPE {qualified_name(schema, type_name)} AS ENUM ({enum_values});",
                "    END IF;",
                "END $$;",
            ]
        )

    def _read_restore_ddl(
        self,
        ddl_path: Path,
        backup_root: Path | None = None,
        manifest_tables: Sequence[dict] | None = None,
        source_enum_labels: dict[tuple[str, str], list[str]] | None = None,
    ) -> str:
        logger.debug(
            "[BackupService] _read_restore_ddl %s path=%s",
            LOG_SEP,
            ddl_path,
        )
        ddl_sql = ddl_path.read_text(encoding="utf-8")
        logger.debug(
            "[BackupService] _read_restore_ddl %s file_loaded bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        ddl_sql = self._remove_invalid_not_null_constraint_blocks(ddl_sql)
        ddl_sql = self._move_foreign_key_blocks_after_other_constraints(ddl_sql)
        ddl_sql = self._terminate_create_index_lines(ddl_sql)
        ddl_sql = self._inject_missing_sequence_creates(ddl_sql)
        ddl_sql = self._inject_required_extensions(ddl_sql)
        ddl_sql = self._inject_missing_enum_types(
            ddl_sql,
            backup_root,
            manifest_tables,
            source_enum_labels,
        )
        logger.debug(
            "[BackupService] _read_restore_ddl %s success bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        return ddl_sql

    def _inject_missing_sequence_creates(self, ddl_sql: str) -> str:
        logger.debug(
            "[BackupService] _inject_missing_sequence_creates %s start bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        sequence_names = self._extract_nextval_sequence_names(ddl_sql)
        if not sequence_names:
            logger.debug(
                "[BackupService] _inject_missing_sequence_creates %s no_sequences",
                LOG_SEP,
            )
            return ddl_sql

        existing_sequences = {
            self._normalize_regclass_name(match)
            for match in re.findall(
                r"CREATE\s+SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^;\n]+)",
                ddl_sql,
                flags=re.I,
            )
        }
        missing_sequences = [
            name for name in sequence_names if self._normalize_regclass_name(name) not in existing_sequences
        ]
        if not missing_sequences:
            logger.debug(
                "[BackupService] _inject_missing_sequence_creates %s all_sequences_present count=%d",
                LOG_SEP,
                len(sequence_names),
            )
            return ddl_sql

        logger.info(
            "[BackupService] _inject_missing_sequence_creates %s injecting_missing count=%d",
            LOG_SEP,
            len(missing_sequences),
        )
        sequence_lines = [
            f"CREATE SEQUENCE IF NOT EXISTS {self._qualified_sequence_name(name)};"
            for name in missing_sequences
        ]
        lines = ddl_sql.splitlines()
        insert_at = 0
        for index, line in enumerate(lines):
            if re.match(r"^\s*CREATE\s+SCHEMA\s+", line, flags=re.I):
                insert_at = index + 1

        return "\n".join(lines[:insert_at] + [""] + sequence_lines + [""] + lines[insert_at:]) + "\n"

    def _inject_required_extensions(self, ddl_sql: str) -> str:
        extension_lines = []
        if "uuid_generate_v4" in ddl_sql and not re.search(
            r'CREATE\s+EXTENSION\s+(?:IF\s+NOT\s+EXISTS\s+)?"uuid-ossp"',
            ddl_sql,
            flags=re.I,
        ):
            extension_lines.append('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        if "gen_random_uuid" in ddl_sql and not re.search(
            r'CREATE\s+EXTENSION\s+(?:IF\s+NOT\s+EXISTS\s+)?"pgcrypto"',
            ddl_sql,
            flags=re.I,
        ):
            extension_lines.append('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')

        if not extension_lines:
            logger.debug("[BackupService] _inject_required_extensions %s none_needed", LOG_SEP)
            return ddl_sql

        logger.info(
            "[BackupService] _inject_required_extensions %s injecting count=%d",
            LOG_SEP,
            len(extension_lines),
        )
        return self._insert_lines_after_schema_creates(ddl_sql, extension_lines)

    def _inject_missing_enum_types(
        self,
        ddl_sql: str,
        backup_root: Path | None,
        manifest_tables: Sequence[dict] | None,
        source_enum_labels: dict[tuple[str, str], list[str]] | None = None,
    ) -> str:
        enum_columns = self._extract_enum_columns_from_ddl(ddl_sql)
        if not enum_columns:
            logger.debug("[BackupService] _inject_missing_enum_types %s none_needed", LOG_SEP)
            return ddl_sql

        existing_enum_types = {
            self._normalize_regclass_name(match)
            for match in re.findall(
                r"CREATE\s+TYPE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s;]+)\s+AS\s+ENUM",
                ddl_sql,
                flags=re.I,
            )
        }
        enum_labels = self._infer_enum_labels_from_ddl(ddl_sql, enum_columns)
        if backup_root is not None and manifest_tables is not None:
            self._infer_enum_labels_from_csv(backup_root, manifest_tables, enum_columns, enum_labels)
        if source_enum_labels:
            self._merge_enum_labels(enum_labels, source_enum_labels)

        enum_blocks = []
        for enum_key in sorted({column_info[2] for column_info in enum_columns}):
            schema, type_name = enum_key
            if self._normalize_regclass_name(f"{schema}.{type_name}") in existing_enum_types:
                continue
            labels = enum_labels.get(enum_key, [])
            if not labels:
                logger.warning(
                    "[BackupService] _inject_missing_enum_types %s empty_labels_using_placeholder enum=%s.%s",
                    LOG_SEP,
                    schema,
                    type_name,
                )
                labels = ["__RESTORE_PLACEHOLDER__"]
            enum_blocks.append(self._enum_type_block(schema, type_name, labels))

        if not enum_blocks:
            logger.debug("[BackupService] _inject_missing_enum_types %s all_present", LOG_SEP)
            return ddl_sql

        logger.info(
            "[BackupService] _inject_missing_enum_types %s injecting count=%d",
            LOG_SEP,
            len(enum_blocks),
        )
        return self._insert_lines_after_schema_creates(ddl_sql, enum_blocks)

    def _merge_enum_labels(
        self,
        enum_labels: dict[tuple[str, str], list[str]],
        source_enum_labels: dict[tuple[str, str], list[str]],
    ) -> None:
        merged_count = 0
        for enum_key, labels in source_enum_labels.items():
            target_labels = enum_labels.setdefault(enum_key, [])
            before_count = len(target_labels)
            for label in labels:
                self._add_unique_enum_label(target_labels, label)
            if len(target_labels) != before_count:
                merged_count += 1
        logger.debug(
            "[BackupService] _merge_enum_labels %s merged_enum_types=%d",
            LOG_SEP,
            merged_count,
        )

    def _extract_enum_columns_from_ddl(self, ddl_sql: str) -> list[tuple[str, str, tuple[str, str], str]]:
        enum_columns = []
        current_schema = ""
        current_table = ""
        in_create_table = False
        create_table_pattern = re.compile(
            r'^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"([^"]+)"\."([^"]+)"\s*\(',
            flags=re.I,
        )
        column_pattern = re.compile(
            r'^\s*"((?:[^"]|"")+)\"\s+((?:"[^"]+"\.)?"[^"]+"|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\b',
            flags=re.I,
        )

        for line in ddl_sql.splitlines():
            create_match = create_table_pattern.match(line)
            if create_match:
                current_schema = create_match.group(1).replace('""', '"')
                current_table = create_match.group(2).replace('""', '"')
                in_create_table = True
                continue
            if in_create_table and line.strip() == ");":
                in_create_table = False
                current_schema = ""
                current_table = ""
                continue
            if not in_create_table:
                continue

            column_match = column_pattern.match(line)
            if not column_match:
                continue
            column_name = column_match.group(1).replace('""', '"')
            data_type = column_match.group(2)
            enum_key = self._enum_key_from_type_name(data_type, current_schema)
            if enum_key:
                enum_columns.append((current_schema, current_table, enum_key, column_name))

        logger.debug(
            "[BackupService] _extract_enum_columns_from_ddl %s enum_columns=%d",
            LOG_SEP,
            len(enum_columns),
        )
        return enum_columns

    def _enum_key_from_type_name(
        self, data_type: str, default_schema: str = "public"
    ) -> tuple[str, str] | None:
        parts = self._split_regclass_name(data_type)
        if not parts:
            return None
        type_name = parts[-1]
        if not type_name.lower().endswith("_enum"):
            return None
        schema = parts[-2] if len(parts) > 1 else default_schema or "public"
        return (schema, type_name)

    def _infer_enum_labels_from_ddl(
        self,
        ddl_sql: str,
        enum_columns: Sequence[tuple[str, str, tuple[str, str], str]],
    ) -> dict[tuple[str, str], list[str]]:
        enum_keys = {enum_key for _, _, enum_key, _ in enum_columns}
        enum_labels: dict[tuple[str, str], list[str]] = {enum_key: [] for enum_key in enum_keys}
        enum_keys_by_name = {enum_key[1]: enum_key for enum_key in enum_keys}

        for raw_label, raw_type in re.findall(
            r"DEFAULT\s+'((?:[^']|'')*)'::((?:\"[^\"]+\"\.)?\"[^\"]+\"|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
            ddl_sql,
            flags=re.I,
        ):
            enum_key = self._enum_key_from_type_name(raw_type)
            if enum_key is None and raw_type in enum_keys_by_name:
                enum_key = enum_keys_by_name[raw_type]
            if enum_key in enum_labels:
                self._add_unique_enum_label(enum_labels[enum_key], raw_label.replace("''", "'"))

        logger.debug(
            "[BackupService] _infer_enum_labels_from_ddl %s enum_types=%d",
            LOG_SEP,
            len(enum_labels),
        )
        return enum_labels

    def _infer_enum_labels_from_csv(
        self,
        backup_root: Path,
        manifest_tables: Sequence[dict],
        enum_columns: Sequence[tuple[str, str, tuple[str, str], str]],
        enum_labels: dict[tuple[str, str], list[str]],
    ) -> None:
        columns_by_table: dict[tuple[str, str], list[tuple[tuple[str, str], str]]] = {}
        for schema, table, enum_key, column in enum_columns:
            columns_by_table.setdefault((schema, table), []).append((enum_key, column))

        scanned_tables = 0
        for table_info in manifest_tables:
            table_key = (str(table_info["schema"]), str(table_info["table"]))
            enum_column_info = columns_by_table.get(table_key)
            if not enum_column_info:
                continue
            csv_path = backup_root / str(table_info["csv_file"])
            if not csv_path.exists():
                logger.warning(
                    "[BackupService] _infer_enum_labels_from_csv %s missing_csv table=%s.%s, path=%s",
                    LOG_SEP,
                    table_key[0],
                    table_key[1],
                    csv_path,
                )
                continue
            scanned_tables += 1
            with csv_path.open("r", encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    for enum_key, column in enum_column_info:
                        value = row.get(column)
                        if value:
                            self._add_unique_enum_label(enum_labels.setdefault(enum_key, []), value)

        logger.debug(
            "[BackupService] _infer_enum_labels_from_csv %s scanned_tables=%d, enum_types=%d",
            LOG_SEP,
            scanned_tables,
            len(enum_labels),
        )

    def _add_unique_enum_label(self, labels: list[str], label: str) -> None:
        if label not in labels:
            labels.append(label)

    def _insert_lines_after_schema_creates(self, ddl_sql: str, injected_lines: Sequence[str]) -> str:
        if not injected_lines:
            return ddl_sql

        lines = ddl_sql.splitlines()
        insert_at = 0
        for index, line in enumerate(lines):
            if re.match(r"^\s*CREATE\s+SCHEMA\s+", line, flags=re.I):
                insert_at = index + 1

        return "\n".join(lines[:insert_at] + [""] + list(injected_lines) + [""] + lines[insert_at:]) + "\n"

    def _extract_nextval_sequence_names(self, ddl_sql: str) -> list[str]:
        seen = set()
        names = []
        for raw_name in re.findall(r"nextval\('((?:[^']|'')+)'::regclass\)", ddl_sql, flags=re.I):
            name = raw_name.replace("''", "'")
            key = self._normalize_regclass_name(name)
            if key not in seen:
                seen.add(key)
                names.append(name)
        logger.debug(
            "[BackupService] _extract_nextval_sequence_names %s count=%d",
            LOG_SEP,
            len(names),
        )
        return names

    def _qualified_sequence_name(self, regclass_name: str) -> str:
        parts = self._split_regclass_name(regclass_name)
        if len(parts) == 1:
            return qualified_name("public", parts[0])
        return qualified_name(parts[-2], parts[-1])

    def _normalize_regclass_name(self, regclass_name: str) -> str:
        parts = self._split_regclass_name(regclass_name.strip())
        if len(parts) == 1:
            return "public." + parts[0].lower()
        return ".".join(part.lower() for part in parts[-2:])

    def _split_regclass_name(self, regclass_name: str) -> list[str]:
        cleaned = regclass_name.strip().strip(";")
        if "::" in cleaned:
            cleaned = cleaned.split("::", 1)[0]
        if cleaned.startswith("'") and cleaned.endswith("'"):
            cleaned = cleaned[1:-1]

        parts = []
        current = []
        in_quotes = False
        index = 0
        while index < len(cleaned):
            char = cleaned[index]
            if char == '"':
                if in_quotes and index + 1 < len(cleaned) and cleaned[index + 1] == '"':
                    current.append('"')
                    index += 1
                else:
                    in_quotes = not in_quotes
            elif char == "." and not in_quotes:
                parts.append("".join(current))
                current = []
            else:
                current.append(char)
            index += 1
        parts.append("".join(current))
        return [part for part in parts if part]

    def _remove_invalid_not_null_constraint_blocks(self, ddl_sql: str) -> str:
        logger.debug(
            "[BackupService] _remove_invalid_not_null_constraint_blocks %s start bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        cleaned_lines = []
        block_lines: list[str] = []
        in_do_block = False
        removed_count = 0

        for line in ddl_sql.splitlines():
            if not in_do_block and re.match(r"^\s*DO\s+\$\$", line, flags=re.I):
                in_do_block = True
                block_lines = [line]
                continue

            if in_do_block:
                block_lines.append(line)
                if re.match(r"^\s*END\s+\$\$;", line, flags=re.I):
                    block_text = "\n".join(block_lines)
                    block_upper = block_text.upper()
                    is_invalid_not_null_constraint = (
                        "ADD CONSTRAINT" in block_upper and " NOT NULL" in block_upper
                    )
                    if is_invalid_not_null_constraint:
                        removed_count += 1
                    else:
                        cleaned_lines.extend(block_lines)
                    in_do_block = False
                    block_lines = []
                continue

            cleaned_lines.append(line)

        if in_do_block:
            logger.warning(
                "[BackupService] _remove_invalid_not_null_constraint_blocks %s unterminated_do_block_preserved lines=%d",
                LOG_SEP,
                len(block_lines),
            )
            cleaned_lines.extend(block_lines)

        if removed_count:
            logger.warning(
                "[BackupService] _remove_invalid_not_null_constraint_blocks %s removed=%d",
                LOG_SEP,
                removed_count,
            )
        cleaned_sql = "\n".join(cleaned_lines)
        logger.debug(
            "[BackupService] _remove_invalid_not_null_constraint_blocks %s success bytes=%d",
            LOG_SEP,
            len(cleaned_sql.encode("utf-8")),
        )
        return cleaned_sql + "\n"

    def _move_foreign_key_blocks_after_other_constraints(self, ddl_sql: str) -> str:
        logger.debug(
            "[BackupService] _move_foreign_key_blocks_after_other_constraints %s start bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        output_lines = []
        block_lines: list[str] = []
        foreign_key_blocks: list[str] = []
        in_do_block = False
        moved_count = 0

        for line in ddl_sql.splitlines():
            if not in_do_block and re.match(r"^\s*DO\s+\$\$", line, flags=re.I):
                in_do_block = True
                block_lines = [line]
                continue

            if in_do_block:
                block_lines.append(line)
                if re.match(r"^\s*END\s+\$\$;", line, flags=re.I):
                    block_text = "\n".join(block_lines)
                    block_upper = block_text.upper()
                    is_foreign_key_constraint = (
                        "ADD CONSTRAINT" in block_upper and " FOREIGN KEY " in block_upper
                    )
                    if is_foreign_key_constraint:
                        foreign_key_blocks.append(block_text)
                        moved_count += 1
                    else:
                        output_lines.extend(block_lines)
                    in_do_block = False
                    block_lines = []
                continue

            output_lines.append(line)

        if in_do_block:
            logger.warning(
                "[BackupService] _move_foreign_key_blocks_after_other_constraints %s unterminated_do_block_preserved lines=%d",
                LOG_SEP,
                len(block_lines),
            )
            output_lines.extend(block_lines)

        if foreign_key_blocks:
            output_lines.append("")
            output_lines.extend(foreign_key_blocks)

        logger.debug(
            "[BackupService] _move_foreign_key_blocks_after_other_constraints %s success moved=%d",
            LOG_SEP,
            moved_count,
        )
        return "\n".join(output_lines) + "\n"

    def _split_foreign_key_constraint_blocks(self, ddl_sql: str) -> tuple[str, str]:
        logger.debug(
            "[BackupService] _split_foreign_key_constraint_blocks %s start bytes=%d",
            LOG_SEP,
            len(ddl_sql.encode("utf-8")),
        )
        schema_lines = []
        foreign_key_blocks: list[str] = []
        block_lines: list[str] = []
        in_do_block = False

        for line in ddl_sql.splitlines():
            if not in_do_block and re.match(r"^\s*DO\s+\$\$", line, flags=re.I):
                in_do_block = True
                block_lines = [line]
                continue

            if in_do_block:
                block_lines.append(line)
                if re.match(r"^\s*END\s+\$\$;", line, flags=re.I):
                    block_text = "\n".join(block_lines)
                    block_upper = block_text.upper()
                    is_foreign_key_constraint = (
                        "ADD CONSTRAINT" in block_upper and " FOREIGN KEY " in block_upper
                    )
                    if is_foreign_key_constraint:
                        foreign_key_blocks.append(block_text)
                    else:
                        schema_lines.extend(block_lines)
                    in_do_block = False
                    block_lines = []
                continue

            schema_lines.append(line)

        if in_do_block:
            logger.warning(
                "[BackupService] _split_foreign_key_constraint_blocks %s unterminated_do_block_preserved lines=%d",
                LOG_SEP,
                len(block_lines),
            )
            schema_lines.extend(block_lines)

        schema_ddl = "\n".join(schema_lines) + "\n"
        foreign_key_ddl = "\n\n".join(foreign_key_blocks) + ("\n" if foreign_key_blocks else "")
        logger.info(
            "[BackupService] _split_foreign_key_constraint_blocks %s success foreign_keys=%d",
            LOG_SEP,
            len(foreign_key_blocks),
        )
        return schema_ddl, foreign_key_ddl

    def _terminate_create_index_lines(self, ddl_sql: str) -> str:
        fixed_lines = []
        fixed_count = 0
        for line in ddl_sql.splitlines():
            stripped = line.rstrip()
            if re.match(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+", stripped, flags=re.I):
                before = stripped
                stripped = self._ensure_sql_statement_terminator(stripped)
                if stripped != before:
                    fixed_count += 1
            fixed_lines.append(stripped)
        if fixed_count:
            logger.warning(
                "[BackupService] _terminate_create_index_lines %s fixed=%d",
                LOG_SEP,
                fixed_count,
            )
        return "\n".join(fixed_lines) + "\n"

    def _ensure_sql_statement_terminator(self, sql_text: str) -> str:
        stripped = sql_text.rstrip()
        return stripped if stripped.endswith(";") else stripped + ";"

    def _sync_sequence_values(
        self,
        conn: PgConnection,
        manifest_tables: Sequence[dict],
        progress: ProgressCallback | None = None,
    ) -> None:
        logger.debug(
            "[BackupService] _sync_sequence_values %s tables=%d",
            LOG_SEP,
            len(manifest_tables),
        )
        reset_count = 0
        with conn.cursor() as cur:
            for table_info in manifest_tables:
                schema = str(table_info["schema"])
                table = str(table_info["table"])
                cur.execute(
                    """
                    SELECT column_name, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s
                      AND table_name = %s
                      AND column_default LIKE %s
                    """,
                    (schema, table, "nextval(%"),
                )
                for column, column_default in cur.fetchall():
                    sequence_name = self._sequence_name_from_default(str(column_default))
                    if not sequence_name:
                        logger.warning(
                            "[BackupService] _sync_sequence_values %s skipped_unparsed_default table=%s.%s, column=%s",
                            LOG_SEP,
                            schema,
                            table,
                            column,
                        )
                        continue

                    self._notify(
                        progress,
                        f"[BackupService] sync_sequence_values {LOG_SEP} table={schema}.{table}, column={column}, sequence={sequence_name}",
                    )
                    logger.debug(
                        "[BackupService] _sync_sequence_values %s resetting table=%s.%s, column=%s, sequence=%s",
                        LOG_SEP,
                        schema,
                        table,
                        column,
                        sequence_name,
                    )

                    max_query = sql.SQL("SELECT MAX({}) FROM {}").format(
                        sql.Identifier(column),
                        sql.Identifier(schema, table),
                    )
                    cur.execute(max_query)
                    max_value = cur.fetchone()[0]
                    if max_value is None:
                        cur.execute("SELECT setval(CAST(%s AS regclass), 1, false)", (sequence_name,))
                    else:
                        cur.execute(
                            "SELECT setval(CAST(%s AS regclass), %s, true)",
                            (sequence_name, int(max_value)),
                        )
                    reset_count += 1
        self._notify(progress, f"[BackupService] sync_sequence_values {LOG_SEP} success count={reset_count}")
        logger.info(
            "[BackupService] _sync_sequence_values %s success count=%d",
            LOG_SEP,
            reset_count,
        )

    def _truncate_restore_tables(self, conn: PgConnection, manifest_tables: Sequence[dict]) -> None:
        logger.debug(
            "[BackupService] _truncate_restore_tables %s tables=%d",
            LOG_SEP,
            len(manifest_tables),
        )
        table_names = []
        for table_info in manifest_tables:
            schema = str(table_info["schema"])
            table = str(table_info["table"])
            table_names.append(sql.Identifier(schema, table))

        if not table_names:
            logger.warning("[BackupService] _truncate_restore_tables %s no_tables", LOG_SEP)
            return

        truncate_sql = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(", ").join(table_names)
        )
        with conn.cursor() as cur:
            cur.execute(truncate_sql)
        logger.info(
            "[BackupService] _truncate_restore_tables %s success tables=%d",
            LOG_SEP,
            len(table_names),
        )

    def _sequence_name_from_default(self, column_default: str) -> str | None:
        match = re.search(r"nextval\('((?:[^']|'')+)'::regclass\)", column_default, flags=re.I)
        if not match:
            return None

        raw_name = match.group(1).replace("''", "'")
        return self._qualified_sequence_name(raw_name)

    def _export_table_csv(
        self, conn: PgConnection, table: TableRef, columns: Sequence[str], destination: Path
    ) -> None:
        logger.debug(
            "[BackupService] _export_table_csv %s table=%s.%s, columns=%d, destination=%s",
            LOG_SEP,
            table.schema,
            table.name,
            len(columns),
            destination,
        )
        if not columns:
            with destination.open("w", encoding="utf-8", newline="") as file:
                csv.writer(file).writerow([])
            logger.warning(
                "[BackupService] _export_table_csv %s empty_columns destination=%s",
                LOG_SEP,
                destination,
            )
            return

        copy_sql = sql.SQL("COPY {} ({}) TO STDOUT WITH CSV HEADER").format(
            sql.Identifier(table.schema, table.name),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        )
        with destination.open("w", encoding="utf-8", newline="") as file:
            with conn.cursor() as cur:
                cur.copy_expert(copy_sql.as_string(conn), file)
        logger.debug(
            "[BackupService] _export_table_csv %s success destination=%s",
            LOG_SEP,
            destination,
        )

    def _export_table_inserts(
        self, conn: PgConnection, table: TableRef, columns: Sequence[str], destination: Path
    ) -> None:
        logger.debug(
            "[BackupService] _export_table_inserts %s table=%s.%s, columns=%d, destination=%s",
            LOG_SEP,
            table.schema,
            table.name,
            len(columns),
            destination,
        )
        if not columns:
            destination.write_text("-- Table has no exportable columns.\n", encoding="utf-8")
            logger.warning(
                "[BackupService] _export_table_inserts %s empty_columns destination=%s",
                LOG_SEP,
                destination,
            )
            return

        select_query = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.Identifier(table.schema, table.name),
        )
        insert_prefix = (
            f"INSERT INTO {qualified_name(table.schema, table.name)} "
            f"({quoted_column_list(columns)}) VALUES\n"
        )
        placeholder_sql = "(" + ", ".join(["%s"] * len(columns)) + ")"
        json_column_indexes = self._get_json_column_indexes(conn, table, columns)
        row_count = 0
        batch_count = 0

        with destination.open("w", encoding="utf-8", newline="\n") as file:
            file.write(f"-- Data for {qualified_name(table.schema, table.name)}\n")
            with conn.cursor() as cur:
                cur.execute(select_query)
                while True:
                    rows = cur.fetchmany(500)
                    if not rows:
                        break
                    batch_count += 1
                    row_count += len(rows)
                    values = [
                        cur.mogrify(
                            placeholder_sql,
                            self._prepare_row_for_sql_insert(row, json_column_indexes),
                        ).decode("utf-8")
                        for row in rows
                    ]
                    file.write(insert_prefix)
                    file.write(",\n".join(values))
                    file.write(";\n\n")
        logger.debug(
            "[BackupService] _export_table_inserts %s success destination=%s, rows=%d, batches=%d",
            LOG_SEP,
            destination,
            row_count,
            batch_count,
        )

    def _get_json_column_indexes(
        self, conn: PgConnection, table: TableRef, columns: Sequence[str]
    ) -> set[int]:
        if not columns:
            logger.debug(
                "[BackupService] _get_json_column_indexes %s no_columns table=%s.%s",
                LOG_SEP,
                table.schema,
                table.name,
            )
            return set()

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.attname
                FROM pg_attribute a
                JOIN pg_type t ON t.oid = a.atttypid
                WHERE a.attrelid = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                  AND a.attgenerated = ''
                  AND t.typname IN ('json', 'jsonb')
                """,
                (table.oid,),
            )
            json_columns = {row[0] for row in cur.fetchall()}

        indexes = {index for index, column in enumerate(columns) if column in json_columns}
        logger.debug(
            "[BackupService] _get_json_column_indexes %s table=%s.%s, json_columns=%d",
            LOG_SEP,
            table.schema,
            table.name,
            len(indexes),
        )
        return indexes

    def _prepare_row_for_sql_insert(
        self, row: Sequence[object], json_column_indexes: set[int]
    ) -> tuple[object, ...]:
        if not json_column_indexes:
            return tuple(row)

        prepared = []
        for index, value in enumerate(row):
            if index in json_column_indexes and value is not None:
                prepared.append(Json(value))
            else:
                prepared.append(value)
        return tuple(prepared)

    def _restore_table_csv(
        self,
        conn: PgConnection,
        schema: str,
        table: str,
        columns: Sequence[str],
        csv_path: Path,
    ) -> None:
        logger.debug(
            "[BackupService] _restore_table_csv %s table=%s.%s, columns=%d, csv_path=%s",
            LOG_SEP,
            schema,
            table,
            len(columns),
            csv_path,
        )
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH CSV HEADER").format(
            sql.Identifier(schema, table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        )
        with csv_path.open("r", encoding="utf-8", newline="") as file:
            with conn.cursor() as cur:
                cur.copy_expert(copy_sql.as_string(conn), file)
        logger.debug(
            "[BackupService] _restore_table_csv %s success table=%s.%s",
            LOG_SEP,
            schema,
            table,
        )

    def _ensure_directory(self, path: Path) -> None:
        if not path.exists() or not path.is_dir():
            logger.warning(
                "[BackupService] _ensure_directory %s invalid path=%s",
                LOG_SEP,
                path,
            )
            raise BackupServiceError("Please select an existing save folder.")
        logger.debug("[BackupService] _ensure_directory %s success path=%s", LOG_SEP, path)

    def _safe_name(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
        return cleaned.strip("._") or "postgres"

    def _notify(self, progress: ProgressCallback | None, message: str) -> None:
        logger.debug("[BackupService] _notify %s message=%s", LOG_SEP, message)
        if progress:
            progress(message)
