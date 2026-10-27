"""Import a SQLite project archive as a provenance-linked knowledge graph."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_ARCHIVE_SUFFIXES = {".md", ".sql", ".txt"}
ENTITY_NAMESPACE = "archive-table"


def import_archive(conn: sqlite3.Connection, archive_root: str | Path) -> dict[str, int]:
    """Store archive text and its SQLite tables/foreign keys in the graph.

    The archive must contain a root-level ``schema.sql``. Its DDL is loaded into
    an in-memory SQLite database so table and foreign-key metadata comes from
    SQLite itself rather than SQL text matching.
    """
    root = Path(archive_root).expanduser().resolve()
    schema_path = root / "schema.sql"
    if not schema_path.is_file():
        raise ValueError(f"archive schema not found: {schema_path}")

    schema_text = schema_path.read_text(encoding="utf-8")
    schema_hash = hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
    archive_db = sqlite3.connect(":memory:")
    archive_db.row_factory = sqlite3.Row
    try:
        archive_db.execute("PRAGMA foreign_keys=ON")
        archive_db.executescript(schema_text)
        table_names = [
            row[0]
            for row in archive_db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = _read_tables(archive_db, table_names)
    finally:
        archive_db.close()

    documents = _read_archive_documents(root)
    document_ids: dict[str, int] = {}
    entity_ids: dict[str, int] = {}
    fact_count = 0
    business_entity_count = 0
    business_fact_count = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    with conn:
        for relative_path, content, content_hash in documents:
            row = conn.execute(
                "SELECT id FROM documents WHERE title=? AND content=? ORDER BY id LIMIT 1",
                (relative_path, content),
            ).fetchone()
            if row:
                document_id = row["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO documents(title, content) VALUES (?, ?)",
                    (relative_path, content),
                )
                document_id = cursor.lastrowid
            document_ids[relative_path] = document_id

        schema_document_id = document_ids[schema_path.relative_to(root).as_posix()]
        model_document_id = document_ids.get("docs/architecture/data-model.md", schema_document_id)

        for table in tables:
            name = f"PME::{table['name']}"
            row = conn.execute(
                "SELECT id FROM entities WHERE name=? AND type='DatabaseTable' ORDER BY id LIMIT 1",
                (name,),
            ).fetchone()
            if row:
                entity_id = row["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO entities(name, type) VALUES (?, 'DatabaseTable')",
                    (name,),
                )
                entity_id = cursor.lastrowid
            entity_ids[table["name"]] = entity_id
            _upsert_attribute(conn, entity_id, "columns", json.dumps(table["columns"], ensure_ascii=False))
            _upsert_attribute(conn, entity_id, "primary_key", json.dumps(table["primary_key"], ensure_ascii=False))
            _upsert_attribute(conn, entity_id, "schema_sha256", schema_hash)
            _add_evidence(conn, f"entity:{entity_id}", schema_document_id, "schema.sql", "SQLite table definition")
            if model_document_id != schema_document_id:
                _add_evidence(
                    conn,
                    f"entity:{entity_id}",
                    model_document_id,
                    "docs/architecture/data-model.md",
                    "Project data model",
                )

        for table in tables:
            subject_id = entity_ids[table["name"]]
            for foreign_key in table["foreign_keys"]:
                object_id = entity_ids.get(foreign_key["target_table"])
                if object_id is None:
                    continue
                source = (
                    f"archive:{root}:schema:{schema_hash}:fk:"
                    f"{table['name']}:{foreign_key['id']}:{foreign_key['sequence']}"
                )
                row = conn.execute("SELECT id FROM facts WHERE source=?", (source,)).fetchone()
                if row:
                    fact_id = row["id"]
                else:
                    cursor = conn.execute(
                        "INSERT INTO facts"
                        "(subject_id, predicate, object_id, valid_from, asserted_at, source) "
                        "VALUES (?, 'references', ?, ?, ?, ?)",
                        (subject_id, object_id, date.today().isoformat(), now, source),
                    )
                    fact_id = cursor.lastrowid
                    fact_count += 1
                _upsert_fact_attribute(conn, fact_id, "source_column", foreign_key["source_column"])
                _upsert_fact_attribute(conn, fact_id, "target_column", foreign_key["target_column"])
                _add_evidence(conn, f"fact:{fact_id}", schema_document_id, "schema.sql", source)
                if model_document_id != schema_document_id:
                    _add_evidence(
                        conn,
                        f"fact:{fact_id}",
                        model_document_id,
                        "docs/architecture/data-model.md",
                        "Project data model",
                    )

        requirements_document_id = document_ids.get("docs/requirements/需求文档.md")
        if requirements_document_id is not None:
            business_entity_count, business_fact_count = _import_business_graph(
                conn,
                root,
                requirements_document_id,
                entity_ids,
                now,
            )

    return {
        "documents": len(documents),
        "tables": len(tables),
        "foreign_keys": sum(len(table["foreign_keys"]) for table in tables),
        "entities_created_or_reused": len(entity_ids),
        "facts_created": fact_count,
        "business_entities_created": business_entity_count,
        "business_facts_created": business_fact_count,
    }


def _import_business_graph(
    conn: sqlite3.Connection,
    root: Path,
    requirements_document_id: int,
    table_entity_ids: dict[str, int],
    asserted_at: str,
) -> tuple[int, int]:
    entities = {
        "pme": {
            "name": "PME",
            "type": "BusinessUnit",
            "step": "Project overview",
            "locator": "docs/requirements/需求文档.md#项目概述",
            "attributes": {"scope": "负责 NEW SKU 与 POR BOM 自动化处理"},
        },
        "newsku": {
            "name": "NewSKU批次处理",
            "type": "BusinessProcess",
            "step": "Step 2-3",
            "locator": "docs/requirements/需求文档.md#step-3",
            "attributes": {"scope": "完整归档清单后筛选候选并进行 PLM 评估"},
        },
        "por_preview": {
            "name": "POR上传预览",
            "type": "BusinessActivity",
            "step": "Step 10",
            "locator": "docs/requirements/需求文档.md#step-10",
            "attributes": {
                "current_status": "只读预览",
                "submission_policy": "可选文件并取消；不保存、不确认、不提交",
            },
        },
        "barcode_maintenance": {
            "name": "条码多合一维护",
            "type": "BusinessActivity",
            "step": "Step 15-16",
            "locator": "docs/requirements/需求文档.md#step-15-16",
            "attributes": {
                "current_status": "生成待维护资料及上传表",
                "submission_policy": "不执行 ADD、Save 或提交确认",
                "trigger": "选中 BOM 描述包含 BULK，且解析出唯一 N IN 1 数量",
            },
        },
        "wimes": {
            "name": "WiMES",
            "type": "BusinessSystem",
            "step": "Step 9-10, 16",
            "locator": "docs/requirements/需求文档.md#step-9-10",
            "attributes": {"scope": "POR 上传预览与包装资料维护"},
        },
        "por_artifact": {
            "name": "POR BOM上传资料",
            "type": "DataArtifact",
            "step": "Step 6-10",
            "locator": "docs/requirements/需求文档.md#step-6-10",
            "attributes": {"current_status": "整理与验证；WiMES 端不提交"},
        },
        "nin1_rule": {
            "name": "BULK N IN 1包装判定",
            "type": "BusinessRule",
            "step": "Step 15",
            "locator": "docs/requirements/需求文档.md#step-15",
            "attributes": {
                "keyword": "BULK",
                "quantity_pattern": "正整数 N IN 1；兼容 10 IN 1 与 10IN1",
                "conflict_policy": "多种不同数量标记 CONFLICT 并转人工确认",
            },
        },
    }
    entity_ids: dict[str, int] = {}
    created_entities = 0
    for key, definition in entities.items():
        row = conn.execute(
            "SELECT id FROM entities WHERE name=? AND type=? ORDER BY id LIMIT 1",
            (definition["name"], definition["type"]),
        ).fetchone()
        if row:
            entity_id = row["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO entities(name, type) VALUES (?, ?)",
                (definition["name"], definition["type"]),
            )
            entity_id = cursor.lastrowid
            created_entities += 1
        entity_ids[key] = entity_id
        _upsert_attribute(conn, entity_id, "requirement_step", definition["step"])
        for attribute, value in definition["attributes"].items():
            _upsert_attribute(conn, entity_id, attribute, value)
        _add_evidence(
            conn,
            f"entity:{entity_id}",
            requirements_document_id,
            definition["locator"],
            f"需求文档 · {definition['step']}",
        )

    relations = [
        ("pme_owns_newsku", "pme", "newsku", "负责处理", "docs/requirements/需求文档.md#项目概述"),
        ("newsku_includes_por_preview", "newsku", "por_preview", "包含环节", "docs/requirements/需求文档.md#step-10"),
        ("newsku_includes_barcode_maintenance", "newsku", "barcode_maintenance", "包含环节", "docs/requirements/需求文档.md#step-15-16"),
        ("por_preview_uses_wimes", "por_preview", "wimes", "使用系统", "docs/requirements/需求文档.md#step-9-10"),
        ("barcode_maintenance_uses_wimes", "barcode_maintenance", "wimes", "使用系统", "docs/requirements/需求文档.md#step-16"),
        ("por_preview_handles_artifact", "por_preview", "por_artifact", "整理与验证", "docs/requirements/需求文档.md#step-6-10"),
        ("newsku_archives_records", "newsku", "table:new_sku_records", "完整归档至", "docs/requirements/需求文档.md#step-2-3"),
        ("newsku_tracks_runs", "newsku", "table:process_runs", "记录运行至", "docs/requirements/需求文档.md#项目概述"),
        ("newsku_tracks_candidates", "newsku", "table:batch_items", "跟踪候选至", "docs/requirements/需求文档.md#step-3"),
        ("por_preview_tracks_steps", "por_preview", "table:batch_steps", "追踪步骤至", "docs/requirements/需求文档.md#step-10"),
        ("barcode_parses_bom_items", "barcode_maintenance", "table:bom_items", "解析BOM明细", "docs/requirements/需求文档.md#step-14-15"),
        ("barcode_uses_packaging_result", "barcode_maintenance", "table:bom_packaging_results", "依据包装解析结果", "docs/requirements/需求文档.md#step-15"),
        ("barcode_creates_maintenance", "barcode_maintenance", "table:packaging_maintenance", "生成待维护记录", "docs/requirements/需求文档.md#step-16"),
        ("barcode_follows_nin1_rule", "barcode_maintenance", "nin1_rule", "遵循判定规则", "docs/requirements/需求文档.md#step-15"),
    ]
    created_facts = 0
    requirement_row = conn.execute(
        "SELECT content FROM documents WHERE id=?", (requirements_document_id,)
    ).fetchone()
    requirement_hash = hashlib.sha256(requirement_row["content"].encode("utf-8")).hexdigest()
    for key, subject_key, object_key, predicate, locator in relations:
        subject_id = entity_ids[subject_key]
        if object_key.startswith("table:"):
            object_id = table_entity_ids.get(object_key.removeprefix("table:"))
            if object_id is None:
                continue
        else:
            object_id = entity_ids[object_key]
        source = f"archive:{root}:requirements:{requirement_hash}:business:{key}"
        row = conn.execute("SELECT id FROM facts WHERE source=?", (source,)).fetchone()
        if row:
            fact_id = row["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO facts"
                "(subject_id, predicate, object_id, valid_from, asserted_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (subject_id, predicate, object_id, date.today().isoformat(), asserted_at, source),
            )
            fact_id = cursor.lastrowid
            created_facts += 1
        _add_evidence(
            conn,
            f"fact:{fact_id}",
            requirements_document_id,
            locator,
            f"需求文档 · {key}",
        )
    return created_entities, created_facts


def _read_tables(conn: sqlite3.Connection, table_names: list[str]) -> list[dict[str, Any]]:
    tables = []
    for table_name in table_names:
        quoted_name = '"' + table_name.replace('"', '""') + '"'
        columns = conn.execute(f"PRAGMA table_info({quoted_name})").fetchall()
        primary_key = [row["name"] for row in sorted(columns, key=lambda item: item["pk"]) if row["pk"]]
        foreign_keys = conn.execute(f"PRAGMA foreign_key_list({quoted_name})").fetchall()
        targets: dict[str, list[str]] = {}
        for foreign_key in foreign_keys:
            if foreign_key["to"] is None:
                target_columns = conn.execute(
                    f"PRAGMA table_info(\"{foreign_key['table'].replace(chr(34), chr(34) * 2)}\")"
                ).fetchall()
                target_column = next((column["name"] for column in target_columns if column["pk"] == foreign_key["seq"] + 1), "")
            else:
                target_column = foreign_key["to"]
            targets.setdefault(foreign_key["id"], []).append(target_column)
        tables.append({
            "name": table_name,
            "columns": [
                {"name": row["name"], "type": row["type"], "not_null": bool(row["notnull"])}
                for row in columns
            ],
            "primary_key": primary_key,
            "foreign_keys": [
                {
                    "id": row["id"],
                    "sequence": row["seq"],
                    "source_column": row["from"],
                    "target_table": row["table"],
                    "target_column": row["to"] or targets[row["id"]][row["seq"]],
                }
                for row in foreign_keys
            ],
        })
    return tables


def _read_archive_documents(root: Path) -> list[tuple[str, str, str]]:
    documents = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_ARCHIVE_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(root).as_posix()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        documents.append((relative_path, content, digest))
    return documents


def _upsert_attribute(conn: sqlite3.Connection, entity_id: int, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO entity_attributes(entity_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(entity_id, key) DO UPDATE SET value=excluded.value",
        (entity_id, key, value),
    )


def _upsert_fact_attribute(conn: sqlite3.Connection, fact_id: int, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO fact_attributes(fact_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(fact_id, key) DO UPDATE SET value=excluded.value",
        (fact_id, key, value),
    )


def _add_evidence(
    conn: sqlite3.Connection,
    semantic_object_id: str,
    document_id: int,
    locator: str,
    label: str,
) -> None:
    row = conn.execute(
        "SELECT id, title, content FROM documents WHERE id=?", (document_id,)
    ).fetchone()
    snapshot = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    reference_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{semantic_object_id}\0document\0{document_id}\0{locator}",
    ).hex
    conn.execute(
        "INSERT INTO semantic_evidence_refs"
        "(id, semantic_object_id, source_type, source_id, locator, label, captured_sha256) "
        "VALUES (?, ?, 'document', ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET captured_sha256=excluded.captured_sha256, label=excluded.label",
        (reference_id, semantic_object_id, str(document_id), locator, label, digest),
    )