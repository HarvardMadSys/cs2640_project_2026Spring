"""
Download the BIRD benchmark and convert its SQLite databases to DuckDB.

BIRD (Big Bench for Large-scale Database Grounded Text-to-SQL) dev set is
distributed as a zip from the official Aliyun mirror. Each database in the
dev split is a SQLite file; this script copies all tables into DuckDB so the
SQL agent can run queries without the SQLite overhead.

Source: https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip
Output: data/bird/{database_name}.duckdb, one file per BIRD database.
Logs:   data/logs/bird.log

Usage:
    python benchmarks/sql/download_bird.py
    DATA_DIR=/path/to/data python benchmarks/sql/download_bird.py
"""

import gzip
import logging
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
BIRD_DIR = DATA_DIR / "bird"
LOG_FILE = DATA_DIR / "logs" / "bird.log"


def setup_logging():
    BIRD_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )


def sqlite_to_duckdb(sqlite_path: Path, duckdb_path: Path) -> dict:
    """
    Copy all tables from a SQLite file into a new DuckDB file.
    Returns a dict of {table_name: row_count}.
    """
    import duckdb

    conn_sqlite = sqlite3.connect(sqlite_path)
    conn_duck = duckdb.connect(str(duckdb_path))

    tables = conn_sqlite.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()

    row_counts = {}
    for (table_name,) in tables:
        rows = conn_sqlite.execute(f"SELECT * FROM [{table_name}]").fetchall()
        if not rows:
            row_counts[table_name] = 0
            continue

        col_info = conn_sqlite.execute(f"PRAGMA table_info([{table_name}])").fetchall()
        col_names = [c[1] for c in col_info]
        col_types = [c[2].upper() for c in col_info]

        type_map = {
            "INTEGER": "BIGINT", "INT": "BIGINT", "REAL": "DOUBLE",
            "TEXT": "VARCHAR", "BLOB": "BLOB", "NUMERIC": "DOUBLE",
            "": "VARCHAR",
        }
        duck_types = []
        for ct in col_types:
            matched = next((v for k, v in type_map.items() if ct.startswith(k)), "VARCHAR")
            duck_types.append(matched)

        col_defs = ", ".join(f'"{n}" {t}' for n, t in zip(col_names, duck_types))
        conn_duck.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs})')

        placeholders = ", ".join(["?"] * len(col_names))
        conn_duck.executemany(
            f'INSERT INTO "{table_name}" VALUES ({placeholders})', rows
        )
        row_counts[table_name] = len(rows)

    conn_sqlite.close()
    conn_duck.close()
    return row_counts


def download_and_convert():
    try:
        import duckdb
    except ImportError:
        sys.exit("Error: duckdb not installed. Run: pip install duckdb")

    import requests

    BIRD_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"

    log = logging.getLogger(__name__)
    log.info(f"Downloading BIRD dev set from {BIRD_URL} ...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        zip_path = tmpdir / "dev.zip"
        try:
            with requests.get(BIRD_URL, stream=True, timeout=600) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                done = 0
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                log.info(f"  downloaded {done / 1e6:.1f} MB (total {total / 1e6:.1f} MB)")
        except Exception as e:
            log.error(f"Download failed: {e}")
            sys.exit(1)

        log.info("Extracting zip ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmpdir)
        # Some BIRD zips contain a nested .zip for the databases.
        for nested in tmpdir.rglob("*.zip"):
            log.info(f"  extracting nested zip {nested.name}")
            with zipfile.ZipFile(nested) as zf:
                zf.extractall(nested.parent)

        local_dir = tmpdir
        sqlite_files = list(local_dir.rglob("*.sqlite"))
        if not sqlite_files:
            log.error("No .sqlite files found in downloaded BIRD data.")
            sys.exit(1)

        summary = {}
        for sqlite_path in sorted(sqlite_files):
            db_name = sqlite_path.stem
            duckdb_path = BIRD_DIR / f"{db_name}.duckdb"

            if duckdb_path.exists():
                log.info(f"  {db_name}: already exists, skipping")
                summary[db_name] = "skipped"
                continue

            log.info(f"  {db_name}: converting {sqlite_path.name} -> {duckdb_path.name}")
            try:
                row_counts = sqlite_to_duckdb(sqlite_path, duckdb_path)
                total = sum(row_counts.values())
                log.info(f"    tables: {list(row_counts.keys())}  total rows: {total:,}")
                summary[db_name] = row_counts
            except Exception as e:
                log.error(f"    FAILED: {e}")
                summary[db_name] = f"error: {e}"

    log.info("\n--- Summary ---")
    for db, info in summary.items():
        if isinstance(info, dict):
            total = sum(info.values())
            log.info(f"  {db}: {total:,} rows across {len(info)} tables")
        else:
            log.info(f"  {db}: {info}")

    return summary


if __name__ == "__main__":
    setup_logging()
    download_and_convert()
