"""
Safe SQLite connection helper for mounted filesystems.

SQLite's journaling/locking can fail on certain mounted or networked
filesystems (FUSE, VirtioFS, NFS, etc.), causing "disk I/O error".

This module provides a context manager that:
  1. Copies the .db file to a local temp directory for safe access.
  2. On exit, copies it back if any writes were made (write=True).

Usage:
    from db_helper import open_db

    # Read-only (default) — no copy-back
    with open_db() as conn:
        c = conn.cursor()
        c.execute("SELECT ...")

    # Read-write — copies modified DB back to the original location
    with open_db(write=True) as conn:
        c = conn.cursor()
        c.execute("INSERT ...")
        conn.commit()
"""

import sqlite3
import shutil
import tempfile
import os
from contextlib import contextmanager

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_SCRIPT_DIR, "german_learning.db")


def _needs_temp_copy(db_path):
    """Check if we need a temp copy by trying a real table query.

    A simple 'SELECT 1' can succeed even on broken mounted filesystems,
    so we test with an actual table read from sqlite_master.
    """
    try:
        conn = sqlite3.connect(db_path)
        # SELECT 1 is not enough — it doesn't touch the file's data pages.
        # Query sqlite_master to force SQLite to actually read the DB file.
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        conn.close()
        return False
    except sqlite3.OperationalError:
        return True


@contextmanager
def open_db(db_path=None, write=False):
    """
    Open a SQLite connection safely, working around mounted-filesystem issues.

    Args:
        db_path: Path to the .db file. Defaults to german_learning.db next to this script.
        write:   If True, copy the modified DB back after closing.

    Yields:
        sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    if db_path is None:
        db_path = DB_PATH

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    use_temp = _needs_temp_copy(db_path)

    if use_temp:
        tmp_dir = tempfile.mkdtemp(prefix="german_db_")
        tmp_db = os.path.join(tmp_dir, "german_learning.db")
        shutil.copy2(db_path, tmp_db)
        active_path = tmp_db
    else:
        tmp_dir = None
        active_path = db_path

    conn = sqlite3.connect(active_path)
    conn.row_factory = sqlite3.Row

    try:
        yield conn
    finally:
        conn.close()
        if use_temp:
            if write:
                shutil.copy2(tmp_db, db_path)
            # Clean up temp files
            try:
                os.remove(tmp_db)
                os.rmdir(tmp_dir)
            except OSError:
                pass
