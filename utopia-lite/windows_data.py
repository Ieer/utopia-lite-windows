"""Snapshot local SQLite data and relocate bundled mounts on Windows."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

import sqlite_vec

from app import local_data


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw")
    connection = sqlite3.connect(uri, uri=True)
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)
    return connection


def backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = connect(source, readonly=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA journal_mode=DELETE")
    finally:
        destination_connection.close()
        source_connection.close()
    connection = connect(destination, readonly=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError(f"Database integrity check failed: {destination}")
    finally:
        connection.close()


def snapshot(source: Path, destination: Path, markdown_root: Path) -> None:
    target = destination / "utopia-lite.db"
    backup(source, target)
    connection = connect(target)
    try:
        mounts = connection.execute("SELECT name, path FROM mounted_dbs ORDER BY name").fetchall()
        for index, (name, path) in enumerate(mounts):
            mount_source = Path(path).expanduser()
            if not mount_source.is_absolute():
                mount_source = source.parent / mount_source
            relative = Path("sources") / str(index + 1) / mount_source.name
            mount_target = destination / relative
            backup(mount_source, mount_target)
            connection.execute("UPDATE mounted_dbs SET path=? WHERE name=?", (relative.as_posix(), name))
            connection.execute(
                "UPDATE mounted_source_metadata SET source_sha256=? WHERE mount_name=?",
                (local_data.file_sha256(mount_target), name),
            )
        markdown_files = connection.execute("SELECT relative_path FROM local_markdown_files").fetchall()
        for (relative_path,) in markdown_files:
            original = (markdown_root / relative_path).resolve(strict=True)
            if not original.is_relative_to(markdown_root.resolve()):
                raise ValueError("Markdown path escapes the configured source directory")
            copied = destination / "markdown" / relative_path
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, copied)
        connection.commit()
        print(f"Database snapshot includes {len(mounts)} mounted source(s) and {len(markdown_files)} Markdown file(s).")
    finally:
        connection.close()


def prepare(database: Path) -> int:
    connection = connect(database)
    try:
        mounts = connection.execute("SELECT name, path FROM mounted_dbs").fetchall()
        for name, path in mounts:
            relative = Path(path)
            if not relative.is_absolute() and relative.parts and relative.parts[0] == "sources":
                resolved = (database.parent / relative).resolve(strict=True)
                if not resolved.is_relative_to((database.parent / "sources").resolve()):
                    raise ValueError("Mounted path escapes the bundled sources directory")
                connection.execute("UPDATE mounted_dbs SET path=? WHERE name=?", (str(resolved), name))
        row = connection.execute("SELECT value FROM _meta WHERE key='embed_dim'").fetchone()
        connection.commit()
        return int(row[0]) if row else 64
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = commands.add_parser("snapshot")
    snapshot_parser.add_argument("source", type=Path)
    snapshot_parser.add_argument("destination", type=Path)
    snapshot_parser.add_argument("markdown_root", type=Path)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "snapshot":
        snapshot(arguments.source, arguments.destination, arguments.markdown_root)
    else:
        print(prepare(arguments.database))


if __name__ == "__main__":
    main()