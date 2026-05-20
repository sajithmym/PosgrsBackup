from __future__ import annotations

import csv
import json
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
        self._ensure_directory(destination_parent)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = destination_parent / f"{self._safe_name(config.database)}_backup_{timestamp}"
        csv_dir = backup_root / "csv"
        sql_dir = backup_root / "sql"
        ddl_dir = backup_root / "table_creation_sql"
        for folder in (csv_dir, sql_dir, ddl_dir):
            folder.mkdir(parents=True, exist_ok=False)

        self._notify(progress, "Connecting to PostgreSQL...")
        with connect_to_database(config) as conn:
            tables = self._get_user_tables(conn)
            if not tables:
                raise BackupServiceError("No user tables were found in this database.")

            ordered_tables = self._sort_tables_by_foreign_keys(conn, tables)
            self._notify(progress, f"Found {len(ordered_tables)} table(s).")
            ddl_sql = self._build_create_tables_sql(conn, ordered_tables)
            (ddl_dir / "create_tables.sql").write_text(ddl_sql, encoding="utf-8")

            manifest_tables = []
            for index, table in enumerate(ordered_tables, start=1):
                label = f"{table.schema}.{table.name}"
                safe_base = self._safe_name(label)
                csv_name = f"{safe_base}.csv"
                sql_name = f"{safe_base}.sql"
                columns = self._get_restorable_table_columns(conn, table)

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
            (backup_root / "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )

        self._notify(progress, f"Backup completed: {backup_root}")
        return backup_root

    def restore_database(
        self,
        config: DbConnectionConfig,
        backup_root: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        if not backup_root.exists() or not backup_root.is_dir():
            raise BackupServiceError("Please select a valid backup folder.")

        manifest_path = backup_root / "manifest.json"
        ddl_path = backup_root / "table_creation_sql" / "create_tables.sql"
        if not manifest_path.exists() or not ddl_path.exists():
            raise BackupServiceError(
                "The selected folder is missing manifest.json or table_creation_sql/create_tables.sql."
            )

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tables = manifest.get("tables", [])
        if not isinstance(tables, list) or not tables:
            raise BackupServiceError("The backup manifest does not contain any tables.")

        self._notify(progress, "Connecting to PostgreSQL...")
        with connect_to_database(config) as conn:
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    self._notify(progress, "Creating schemas and tables...")
                    cur.execute(self._read_restore_ddl(ddl_path))

                self._notify(progress, "Preparing tables for restore...")
                self._truncate_restore_tables(conn, tables)

                for index, table_info in enumerate(tables, start=1):
                    schema = str(table_info["schema"])
                    table = str(table_info["table"])
                    columns = [str(column) for column in table_info["columns"]]
                    csv_path = backup_root / str(table_info["csv_file"])
                    if not csv_path.exists():
                        raise BackupServiceError(f"Missing CSV file: {csv_path}")

                    self._notify(progress, f"[{index}/{len(tables)}] Restoring {schema}.{table}...")
                    if columns:
                        self._restore_table_csv(conn, schema, table, columns, csv_path)
                    else:
                        self._notify(
                            progress,
                            f"[{index}/{len(tables)}] Skipped {schema}.{table}; no restorable columns.",
                        )

                self._notify(progress, "Resetting sequences...")
                self._sync_sequence_values(conn, tables)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        self._notify(progress, "Restore completed.")

    def _get_user_tables(self, conn: PgConnection) -> list[TableRef]:
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
            return [TableRef(schema=row[0], name=row[1], oid=row[2]) for row in cur.fetchall()]

    def _get_restorable_table_columns(self, conn: PgConnection, table: TableRef) -> list[str]:
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
            return [row[0] for row in cur.fetchall()]

    def _sort_tables_by_foreign_keys(
        self, conn: PgConnection, tables: Sequence[TableRef]
    ) -> list[TableRef]:
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

        ordered: list[TableRef] = []
        remaining = dict(dependencies)
        while remaining:
            ready = [key for key, parents in remaining.items() if not parents.intersection(remaining)]
            if not ready:
                ordered.extend(table_by_key[key] for key in sorted(remaining))
                break
            for key in sorted(ready):
                ordered.append(table_by_key[key])
                remaining.pop(key)
        return ordered

    def _build_create_tables_sql(self, conn: PgConnection, tables: Sequence[TableRef]) -> str:
        lines = [
            "-- Generated by PyQt PostgreSQL Backup Tool",
            "-- Creates schemas, tables, constraints, and non-constraint indexes.",
            "",
        ]

        for schema in sorted({table.schema for table in tables}):
            lines.append(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema)};")
        lines.append("")

        constraint_blocks: list[str] = []
        index_lines: list[str] = []
        for table in tables:
            table_sql, constraints, indexes = self._build_single_table_sql(conn, table)
            lines.extend(table_sql)
            lines.append("")
            constraint_blocks.extend(constraints)
            index_lines.extend(indexes)

        lines.extend(constraint_blocks)
        if constraint_blocks:
            lines.append("")
        lines.extend(index_lines)
        lines.append("")
        return self._inject_missing_sequence_creates("\n".join(lines))

    def _build_single_table_sql(
        self, conn: PgConnection, table: TableRef
    ) -> tuple[list[str], list[str], list[str]]:
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
            self._constraint_block(table, name, definition)
            for name, definition, constraint_type in constraints
            if constraint_type != "n" and not str(definition).upper().startswith("NOT NULL ")
        ]
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

    def _read_restore_ddl(self, ddl_path: Path) -> str:
        ddl_sql = ddl_path.read_text(encoding="utf-8")
        ddl_sql = self._remove_invalid_not_null_constraint_blocks(ddl_sql)
        ddl_sql = self._terminate_create_index_lines(ddl_sql)
        ddl_sql = self._inject_missing_sequence_creates(ddl_sql)
        return ddl_sql

    def _inject_missing_sequence_creates(self, ddl_sql: str) -> str:
        sequence_names = self._extract_nextval_sequence_names(ddl_sql)
        if not sequence_names:
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
            return ddl_sql

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

    def _extract_nextval_sequence_names(self, ddl_sql: str) -> list[str]:
        seen = set()
        names = []
        for raw_name in re.findall(r"nextval\('((?:[^']|'')+)'::regclass\)", ddl_sql, flags=re.I):
            name = raw_name.replace("''", "'")
            key = self._normalize_regclass_name(name)
            if key not in seen:
                seen.add(key)
                names.append(name)
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
        return re.sub(
            r"DO\s+\$\$\s*BEGIN\s*IF\s+NOT\s+EXISTS\s*\(.*?\)\s*THEN\s*"
            r"ALTER\s+TABLE\s+ONLY\s+.*?\s+ADD\s+CONSTRAINT\s+.*?\s+NOT\s+NULL\s+.*?;"
            r"\s*END\s+IF;\s*END\s+\$\$;",
            "",
            ddl_sql,
            flags=re.IGNORECASE | re.DOTALL,
        )

    def _terminate_create_index_lines(self, ddl_sql: str) -> str:
        fixed_lines = []
        for line in ddl_sql.splitlines():
            stripped = line.rstrip()
            if re.match(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+", stripped, flags=re.I):
                stripped = self._ensure_sql_statement_terminator(stripped)
            fixed_lines.append(stripped)
        return "\n".join(fixed_lines) + "\n"

    def _ensure_sql_statement_terminator(self, sql_text: str) -> str:
        stripped = sql_text.rstrip()
        return stripped if stripped.endswith(";") else stripped + ";"

    def _sync_sequence_values(self, conn: PgConnection, manifest_tables: Sequence[dict]) -> None:
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
                      AND column_default LIKE 'nextval(%'
                    """,
                    (schema, table),
                )
                for column, column_default in cur.fetchall():
                    sequence_name = self._sequence_name_from_default(str(column_default))
                    if not sequence_name:
                        continue

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

    def _truncate_restore_tables(self, conn: PgConnection, manifest_tables: Sequence[dict]) -> None:
        table_names = []
        for table_info in manifest_tables:
            schema = str(table_info["schema"])
            table = str(table_info["table"])
            table_names.append(sql.Identifier(schema, table))

        if not table_names:
            return

        truncate_sql = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(", ").join(table_names)
        )
        with conn.cursor() as cur:
            cur.execute(truncate_sql)

    def _sequence_name_from_default(self, column_default: str) -> str | None:
        match = re.search(r"nextval\('((?:[^']|'')+)'::regclass\)", column_default, flags=re.I)
        if not match:
            return None

        raw_name = match.group(1).replace("''", "'")
        return self._qualified_sequence_name(raw_name)

    def _export_table_csv(
        self, conn: PgConnection, table: TableRef, columns: Sequence[str], destination: Path
    ) -> None:
        if not columns:
            with destination.open("w", encoding="utf-8", newline="") as file:
                csv.writer(file).writerow([])
            return

        copy_sql = sql.SQL("COPY {} ({}) TO STDOUT WITH CSV HEADER").format(
            sql.Identifier(table.schema, table.name),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        )
        with destination.open("w", encoding="utf-8", newline="") as file:
            with conn.cursor() as cur:
                cur.copy_expert(copy_sql.as_string(conn), file)

    def _export_table_inserts(
        self, conn: PgConnection, table: TableRef, columns: Sequence[str], destination: Path
    ) -> None:
        if not columns:
            destination.write_text("-- Table has no exportable columns.\n", encoding="utf-8")
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

        with destination.open("w", encoding="utf-8", newline="\n") as file:
            file.write(f"-- Data for {qualified_name(table.schema, table.name)}\n")
            with conn.cursor() as cur:
                cur.execute(select_query)
                while True:
                    rows = cur.fetchmany(500)
                    if not rows:
                        break
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

    def _get_json_column_indexes(
        self, conn: PgConnection, table: TableRef, columns: Sequence[str]
    ) -> set[int]:
        if not columns:
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

        return {index for index, column in enumerate(columns) if column in json_columns}

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
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH CSV HEADER").format(
            sql.Identifier(schema, table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        )
        with csv_path.open("r", encoding="utf-8", newline="") as file:
            with conn.cursor() as cur:
                cur.copy_expert(copy_sql.as_string(conn), file)

    def _ensure_directory(self, path: Path) -> None:
        if not path.exists() or not path.is_dir():
            raise BackupServiceError("Please select an existing save folder.")

    def _safe_name(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
        return cleaned.strip("._") or "postgres"

    def _notify(self, progress: ProgressCallback | None, message: str) -> None:
        if progress:
            progress(message)
