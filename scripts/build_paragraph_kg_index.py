import argparse
import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Tuple


NUMBERED_PREFIX = re.compile(r"^\d+(?:\.\d+)*\s*")
SPECIAL_BOOK_TITLES = {
    "中医辞典": "中医词典",
    "中醫辭典": "中医词典",
    "中医词典": "中医词典",
    "中醫詞典": "中医词典",
}


def iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} 不是合法 JSONL: {exc}") from exc


def normalize_book_title(title: str) -> str:
    title = str(title or "").strip()
    title = NUMBERED_PREFIX.sub("", title).strip()
    if title in SPECIAL_BOOK_TITLES:
        return SPECIAL_BOOK_TITLES[title]
    if title.startswith("《") and title.endswith("》") and len(title) > 2:
        return title[1:-1].strip()
    return title


def derive_book_title(relative_path: str) -> str:
    parts = [part for part in PurePosixPath(str(relative_path).replace("\\", "/")).parts if part]
    if parts and "." in parts[-1]:
        parts = parts[:-1]

    for part in parts:
        cleaned = normalize_book_title(part)
        if cleaned in SPECIAL_BOOK_TITLES:
            return SPECIAL_BOOK_TITLES[cleaned]
        if part != NUMBERED_PREFIX.sub("", part):
            continue
        if cleaned:
            return cleaned

    for part in reversed(parts):
        cleaned = normalize_book_title(part)
        if cleaned and not cleaned.startswith("卷"):
            return cleaned
    return normalize_book_title(parts[-1]) if parts else "未知古籍"


def compact_json(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False, separators=(",", ":"))


def build_search_text(paragraph: Dict[str, Any], entities: List[Dict[str, Any]], triplets: List[Dict[str, Any]]) -> str:
    parts = [
        paragraph.get("book_title", ""),
        paragraph.get("relative_path", ""),
        paragraph.get("text", ""),
    ]
    for entity in entities:
        parts.append(str(entity.get("text", "")))
        parts.append(str(entity.get("type", "")))
    for triplet in triplets:
        parts.extend(
            [
                str(triplet.get("head_text", "")),
                str(triplet.get("head_type", "")),
                str(triplet.get("relation", "")),
                str(triplet.get("tail_text", "")),
                str(triplet.get("tail_type", "")),
                str(triplet.get("evidence", "")),
            ]
        )
    return " ".join(part for part in parts if part)


def create_schema(conn: sqlite3.Connection) -> str:
    conn.executescript(
        """
        DROP TABLE IF EXISTS documents_fts;
        DROP TABLE IF EXISTS triples;
        DROP TABLE IF EXISTS entity_mentions;
        DROP TABLE IF EXISTS documents;

        CREATE TABLE documents (
            paragraph_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            book_title TEXT NOT NULL,
            text TEXT NOT NULL,
            entities_json TEXT NOT NULL DEFAULT '[]',
            triplets_json TEXT NOT NULL DEFAULT '[]',
            symptom_groups_json TEXT NOT NULL DEFAULT '[]',
            entity_count INTEGER NOT NULL DEFAULT 0,
            triplet_count INTEGER NOT NULL DEFAULT 0,
            search_text TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE entity_mentions (
            paragraph_id TEXT NOT NULL,
            entity_text TEXT NOT NULL,
            entity_type TEXT,
            start_pos INTEGER,
            end_pos INTEGER
        );

        CREATE TABLE triples (
            paragraph_id TEXT NOT NULL,
            head_text TEXT NOT NULL,
            head_type TEXT,
            relation TEXT NOT NULL,
            tail_text TEXT NOT NULL,
            tail_type TEXT,
            evidence TEXT
        );
        """
    )
    tokenizer = "trigram"
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(paragraph_id UNINDEXED, book_title, relative_path, search_text, tokenize='trigram')"
        )
    except sqlite3.Error:
        tokenizer = "unicode61"
        conn.execute(
            "CREATE VIRTUAL TABLE documents_fts USING fts5(paragraph_id UNINDEXED, book_title, relative_path, search_text, tokenize='unicode61')"
        )
    return tokenizer


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_entity_text ON entity_mentions(entity_text);
        CREATE INDEX IF NOT EXISTS idx_entity_pid ON entity_mentions(paragraph_id);
        CREATE INDEX IF NOT EXISTS idx_triples_head ON triples(head_text);
        CREATE INDEX IF NOT EXISTS idx_triples_tail ON triples(tail_text);
        CREATE INDEX IF NOT EXISTS idx_triples_relation ON triples(relation);
        CREATE INDEX IF NOT EXISTS idx_triples_pid ON triples(paragraph_id);
        """
    )


def insert_paragraphs(conn: sqlite3.Connection, paragraph_index_path: str) -> int:
    rows = []
    count = 0
    for item in iter_jsonl(paragraph_index_path):
        relative_path = item.get("relative_path") or item.get("book_path") or ""
        book_title = derive_book_title(relative_path)
        rows.append(
            (
                item["paragraph_id"],
                relative_path,
                book_title,
                item.get("text", ""),
            )
        )
        count += 1
        if len(rows) >= 5000:
            conn.executemany(
                """
                INSERT OR REPLACE INTO documents(paragraph_id, relative_path, book_title, text)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            rows.clear()
            print(f"已载入段落元数据 {count:,} 条")
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO documents(paragraph_id, relative_path, book_title, text)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
    return count


def write_document(
    conn: sqlite3.Connection,
    jsonl_handle,
    paragraph_id: str,
    entities: List[Dict[str, Any]],
    triplets: List[Dict[str, Any]],
    symptom_groups: List[Dict[str, Any]],
) -> Tuple[int, int, str, str]:
    row = conn.execute(
        "SELECT paragraph_id, relative_path, book_title, text FROM documents WHERE paragraph_id = ?",
        (paragraph_id,),
    ).fetchone()
    if not row:
        return 0, 0, "", ""

    paragraph = {
        "paragraph_id": row[0],
        "relative_path": row[1],
        "book_title": row[2],
        "text": row[3],
    }
    search_text = build_search_text(paragraph, entities, triplets)
    entity_json = compact_json(entities)
    triplet_json = compact_json(triplets)
    symptom_group_json = compact_json(symptom_groups)

    conn.execute(
        """
        UPDATE documents
        SET entities_json = ?, triplets_json = ?, symptom_groups_json = ?,
            entity_count = ?, triplet_count = ?, search_text = ?
        WHERE paragraph_id = ?
        """,
        (
            entity_json,
            triplet_json,
            symptom_group_json,
            len(entities),
            len(triplets),
            search_text,
            paragraph_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO documents_fts(paragraph_id, book_title, relative_path, search_text)
        VALUES (?, ?, ?, ?)
        """,
        (paragraph_id, paragraph["book_title"], paragraph["relative_path"], search_text),
    )

    if entities:
        conn.executemany(
            """
            INSERT INTO entity_mentions(paragraph_id, entity_text, entity_type, start_pos, end_pos)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    paragraph_id,
                    str(entity.get("text", "")).strip(),
                    entity.get("type"),
                    entity.get("start"),
                    entity.get("end"),
                )
                for entity in entities
                if str(entity.get("text", "")).strip()
            ],
        )

    if triplets:
        conn.executemany(
            """
            INSERT INTO triples(paragraph_id, head_text, head_type, relation, tail_text, tail_type, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    paragraph_id,
                    str(triplet.get("head_text", "")).strip(),
                    triplet.get("head_type"),
                    str(triplet.get("relation", "")).strip(),
                    str(triplet.get("tail_text", "")).strip(),
                    triplet.get("tail_type"),
                    triplet.get("evidence"),
                )
                for triplet in triplets
                if str(triplet.get("head_text", "")).strip()
                and str(triplet.get("relation", "")).strip()
                and str(triplet.get("tail_text", "")).strip()
            ],
        )

    output = {
        **paragraph,
        "entities": entities,
        "triplets": triplets,
        "symptom_groups": symptom_groups,
    }
    jsonl_handle.write(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(entities), len(triplets), paragraph["book_title"], paragraph["relative_path"]


def add_missing_documents(conn: sqlite3.Connection, jsonl_handle) -> int:
    rows = conn.execute(
        """
        SELECT paragraph_id, relative_path, book_title, text
        FROM documents
        WHERE search_text = ''
        """
    ).fetchall()
    for row in rows:
        paragraph = {
            "paragraph_id": row[0],
            "relative_path": row[1],
            "book_title": row[2],
            "text": row[3],
            "entities": [],
            "triplets": [],
            "symptom_groups": [],
        }
        search_text = build_search_text(paragraph, [], [])
        conn.execute(
            "UPDATE documents SET search_text = ? WHERE paragraph_id = ?",
            (search_text, row[0]),
        )
        conn.execute(
            "INSERT INTO documents_fts(paragraph_id, book_title, relative_path, search_text) VALUES (?, ?, ?, ?)",
            (row[0], row[2], row[1], search_text),
        )
        jsonl_handle.write(json.dumps(paragraph, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(rows)


def build(args: argparse.Namespace) -> None:
    os.makedirs(os.path.dirname(args.jsonl_out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(args.sqlite_out) or ".", exist_ok=True)
    if args.rebuild:
        for path in [args.jsonl_out, args.sqlite_out, args.summary_out]:
            if path and os.path.exists(path):
                os.remove(path)

    conn = sqlite3.connect(args.sqlite_out)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    tokenizer = create_schema(conn)
    print(f"SQLite FTS tokenizer: {tokenizer}")

    with conn:
        paragraph_count = insert_paragraphs(conn, args.paragraph_index)

    entity_type_counts: Counter = Counter()
    relation_counts: Counter = Counter()
    book_counts: Counter = Counter()
    processed = 0
    total_entities = 0
    total_triplets = 0

    with open(args.jsonl_out, "w", encoding="utf-8", newline="\n") as jsonl_handle:
        conn.execute("BEGIN")
        for output in iter_jsonl(args.deepseek_output):
            paragraph_id = output.get("paragraph_id")
            if not paragraph_id:
                continue
            entities = output.get("entities") or []
            triplets = output.get("triplets") or []
            symptom_groups = output.get("symptom_groups") or []
            entity_count, triplet_count, book_title, _ = write_document(
                conn,
                jsonl_handle,
                paragraph_id,
                entities,
                triplets,
                symptom_groups,
            )
            if book_title:
                book_counts[book_title] += 1
            total_entities += entity_count
            total_triplets += triplet_count
            entity_type_counts.update(entity.get("type") for entity in entities if entity.get("type"))
            relation_counts.update(triplet.get("relation") for triplet in triplets if triplet.get("relation"))
            processed += 1
            if processed % 5000 == 0:
                conn.commit()
                conn.execute("BEGIN")
                print(
                    f"已合并 {processed:,} 段，实体 {total_entities:,}，三元组 {total_triplets:,}"
                )

        missing_count = add_missing_documents(conn, jsonl_handle)
        conn.commit()

    create_indexes(conn)
    conn.commit()

    summary = {
        "paragraph_count": paragraph_count,
        "processed_deepseek_outputs": processed,
        "missing_deepseek_outputs_filled_empty": missing_count,
        "entity_count": total_entities,
        "triplet_count": total_triplets,
        "book_count": len(book_counts),
        "fts_tokenizer": tokenizer,
        "top_books": book_counts.most_common(20),
        "entity_type_counts": dict(entity_type_counts.most_common()),
        "relation_counts": dict(relation_counts.most_common()),
        "jsonl_out": args.jsonl_out,
        "sqlite_out": args.sqlite_out,
    }
    with open(args.summary_out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    conn.close()

    print("构建完成")
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:4000])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build OpenTCM paragraph KG JSONL and SQLite FTS index.")
    parser.add_argument("--paragraph_index", default=os.path.join("data", "deepseek_success_160k_index.jsonl"))
    parser.add_argument("--deepseek_output", default=os.path.join("data", "deepseek_outputs_normalized.jsonl"))
    parser.add_argument("--jsonl_out", default=os.path.join("data", "paragraph_kg_index.jsonl"))
    parser.add_argument("--sqlite_out", default=os.path.join("data", "opentcm_kg.sqlite"))
    parser.add_argument("--summary_out", default=os.path.join("data", "paragraph_kg_schema_summary.json"))
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
