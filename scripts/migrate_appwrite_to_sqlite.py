#!/usr/bin/env python3
import argparse
import logging
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

load_dotenv(os.path.join(ROOT, ".env"))

from appwrite.exception import AppwriteException
from appwrite.query import Query

from appwrite_client import COLLECTIONS, DATABASE_ID, tablesdb
from services.database import database_path, init_db, insert_row_ignore
from services.row_utils import row_id as _row_id


logger = logging.getLogger("migrate_appwrite_to_sqlite")
DEFAULT_LIMIT = 100
TABLES = tuple(dict.fromkeys(COLLECTIONS.values()))


def _row_to_dict(row):
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_dict"):
        value = row.to_dict()
    elif hasattr(row, "model_dump"):
        value = row.model_dump(by_alias=True, mode="json")
    else:
        return row

    data = value.pop("data", None)
    if isinstance(data, dict):
        value.update(data)
    return value


def _row_list_to_rows(response):
    if isinstance(response, dict):
        return [_row_to_dict(row) for row in response.get("rows", [])]
    if hasattr(response, "to_dict"):
        value = response.to_dict()
    elif hasattr(response, "model_dump"):
        value = response.model_dump(by_alias=True, mode="json")
    else:
        value = {}
    rows = getattr(response, "rows", value.get("rows", []))
    return [_row_to_dict(row) for row in rows]


def _require_appwrite():
    if tablesdb is None or not DATABASE_ID:
        raise RuntimeError("Appwrite TablesDB is not configured. Check APPWRITE_* values in .env.")


def list_appwrite_rows(table_id, limit=DEFAULT_LIMIT):
    _require_appwrite()
    offset = 0
    while True:
        response = tablesdb.list_rows(
            database_id=DATABASE_ID,
            table_id=table_id,
            queries=[Query.limit(limit), Query.offset(offset)],
        )
        rows = _row_list_to_rows(response)
        for row in rows:
            yield row
        if len(rows) < limit:
            break
        offset += limit


def migrate_table(table_id, *, db_path=None, dry_run=False):
    stats = {"source": 0, "inserted": 0, "ignored": 0, "skipped": 0, "failed": 0}
    for row in list_appwrite_rows(table_id):
        stats["source"] += 1
        row_id = _row_id(row)
        if not row_id:
            stats["skipped"] += 1
            logger.warning("%s: skipped row without id", table_id)
            continue
        if dry_run:
            stats["inserted"] += 1
            continue
        try:
            inserted = insert_row_ignore(table_id, row_id=row_id, data=row, path=db_path)
        except AppwriteException:
            stats["failed"] += 1
            logger.exception("%s: failed to insert row %s", table_id, row_id)
            continue
        if inserted:
            stats["inserted"] += 1
        else:
            stats["ignored"] += 1
    return stats


def parse_args():
    parser = argparse.ArgumentParser(description="Migrate Appwrite TablesDB data into nest.sqlite3.")
    parser.add_argument("--database-path", default=None, help="Override the target SQLite database path.")
    parser.add_argument("--table", action="append", choices=TABLES, help="Migrate only this Appwrite table. May be repeated.")
    parser.add_argument("--dry-run", action="store_true", help="Read Appwrite rows without writing SQLite.")
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    db_path = database_path(args.database_path)
    tables = tuple(dict.fromkeys(args.table or TABLES))

    if not args.dry_run:
        init_db(path=db_path)

    logger.info("Target SQLite database: %s", db_path)
    failures = []
    for table_id in tables:
        logger.info("Migrating %s", table_id)
        try:
            stats = migrate_table(table_id, db_path=db_path, dry_run=args.dry_run)
        except Exception:
            failures.append(table_id)
            logger.exception("%s: migration failed", table_id)
            continue
        logger.info(
            "%s: source=%s inserted=%s ignored=%s skipped=%s failed=%s",
            table_id,
            stats["source"],
            stats["inserted"],
            stats["ignored"],
            stats["skipped"],
            stats["failed"],
        )
        if stats["failed"]:
            failures.append(table_id)

    if failures:
        logger.error("Migration completed with failures: %s", ", ".join(failures))
        return 1
    logger.info("Migration completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
