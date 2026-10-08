import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pypdf import PdfReader


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

THERAPY_SUFFIXES = (
    "汤",
    "散",
    "丸",
    "方",
    "颗粒",
    "胶囊",
    "膏",
    "贴",
    "针",
    "灸",
    "针刺",
    "艾灸",
    "电针",
    "推拿",
    "按摩",
    "护理",
    "穴位贴敷",
    "耳穴",
    "拔罐",
    "刮痧",
    "熏蒸",
    "热敷",
    "埋线",
    "火龙罐",
    "药物灌肠",
)

OUTCOME_TERMS = [
    "疼痛",
    "痛经",
    "腹痛",
    "腰痛",
    "乳房胀痛",
    "乳房疼痛",
    "泌乳",
    "乳汁分泌",
    "月经",
    "出血",
    "妊娠率",
    "排卵率",
    "有效率",
    "总有效率",
    "生活质量",
    "焦虑",
    "抑郁",
    "复发率",
    "不良反应",
]

METRIC_TERMS = [
    "VAS",
    "VRS",
    "SAS",
    "SDS",
    "SF-36",
    "CA125",
    "CA-125",
    "β-EP",
    "PGE2",
    "PGF2α",
    "E2",
    "FSH",
    "LH",
    "TNF-α",
    "IL-6",
    "IL-8",
]

STOP_ENTITY_PREFIXES = (
    "目的",
    "方法",
    "结果",
    "结论",
    "观察",
    "比较",
    "采用",
    "选取",
    "随机",
    "治疗后",
    "对照组",
    "观察组",
)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_pdf_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = text.replace("\u3000", " ")
    text = text.replace("．", ".").replace("—", "-")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z])", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_for_regex(text: str) -> str:
    text = clean_pdf_text(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_pdf_text(path: Path, max_pages: int = 80) -> Tuple[str, Dict[str, Any], int, str]:
    try:
        reader = PdfReader(str(path))
        metadata = {str(k): str(v) for k, v in (reader.metadata or {}).items()}
        pages = min(len(reader.pages), max_pages)
        chunks = []
        for page in reader.pages[:pages]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                chunks.append("")
        text = clean_pdf_text("\n".join(chunks))
        quality = "text" if len(text) >= 400 else "low_text_or_scanned"
        return text, metadata, len(reader.pages), quality
    except Exception as exc:
        return "", {"extract_error": str(exc)}, 0, "extract_error"


def normalize_title_from_filename(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"^\d{4}\s+", "", stem)
    stem = re.sub(r"_[\u4e00-\u9fff]{2,4}$", "", stem)
    stem = stem.replace("...", "")
    return stem.strip(" _-")


def derive_article_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]
    return f"MOD{digest}"


def parse_folder_metadata(path: Path, root: Path) -> Dict[str, Any]:
    rel_parts = path.relative_to(root).parts
    condition = re.sub(r"\s*\d+\s*篇\s*$", "", rel_parts[0]) if rel_parts else ""
    category = rel_parts[1] if len(rel_parts) > 1 else ""
    language = "en" if "英文" in category else "zh-Hans"
    has_herbs = "含中药治疗" in category
    herbs_listed = "有列明中药" in category
    return {
        "source_condition": condition.strip(),
        "treatment_category": re.sub(r"\s+\d+\s*$", "", category).strip(),
        "language": language,
        "treatment_has_herbs": has_herbs,
        "herbs_listed": herbs_listed,
    }


def first_match(patterns: Sequence[str], text: str, flags: int = re.I | re.S) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            value = next((g for g in match.groups() if g), "")
            return re.sub(r"\s+", " ", value).strip(" ：:;；,.，。")
    return ""


def extract_between(text: str, starts: Sequence[str], ends: Sequence[str], limit: int) -> str:
    for start in starts:
        match = re.search(start, text, re.I | re.S)
        if not match:
            continue
        begin = match.end()
        end_pos = len(text)
        for end in ends:
            end_match = re.search(end, text[begin:], re.I | re.S)
            if end_match:
                end_pos = min(end_pos, begin + end_match.start())
        value = text[begin:end_pos].strip(" ：:;；,.，。")
        value = re.sub(r"\s+", " ", value)
        if value:
            return value[:limit].strip()
    return ""


def split_keywords(raw: str) -> List[str]:
    raw = re.sub(r"\s+", " ", raw or "")
    items = re.split(r"[；;，,、\|/]+|\s{2,}", raw)
    keywords = []
    for item in items:
        item = item.strip(" ：:;；,.，。[]【】")
        if 2 <= len(item) <= 40 and item not in keywords:
            keywords.append(item)
    return keywords[:16]


def extract_keywords(text: str) -> List[str]:
    raw = first_match(
        [
            r"(?:关键词|关键字|關鍵詞|關鍵字)\s*[:：】]?\s*(.{2,220}?)(?:中图分类号|文献标识码|DOI|Abstract|引言|1\s|$)",
            r"(?:Key words|Keywords)\s*[:：]?\s*(.{2,220}?)(?:DOI|Introduction|1\s|$)",
        ],
        text,
    )
    return split_keywords(raw)


def extract_abstract(text: str) -> str:
    abstract = extract_between(
        text,
        [r"(?:摘要|摘 要|【摘要】|Abstract)\s*[:：】]?"],
        [
            r"(?:关键词|关键字|關鍵詞|Key words|Keywords)\s*[:：]",
            r"(?:中图分类号|文献标识码|DOI)\s*[:：]",
            r"\n\s*1\s*[\.、]",
        ],
        1800,
    )
    if abstract:
        return abstract
    return _sentence_window(text, 0, 900)


def extract_conclusion(text: str, abstract: str) -> str:
    conclusion = first_match(
        [
            r"(?:结论|結論|Conclusion)\s*[:：]?\s*(.{20,700}?)(?:关键词|关键字|Key words|Keywords|中图分类号|DOI|参考文献|References|$)",
        ],
        abstract,
    )
    if conclusion:
        return conclusion
    conclusion = first_match(
        [
            r"(?:结论|結論|Conclusion)\s*[:：]?\s*(.{20,900}?)(?:参考文献|References|$)",
            r"(?:综上|综上所述|總之)\s*[，,]?\s*(.{20,700}?)(?:参考文献|References|$)",
        ],
        text[-5000:],
    )
    return conclusion[:900]


def extract_year(path: Path, text: str, metadata: Dict[str, Any]) -> str:
    candidates = [path.stem, metadata.get("/CreationDate", ""), text[:1200]]
    for value in candidates:
        match = re.search(r"(19|20)\d{2}", value or "")
        if match:
            return match.group(0)
    return ""


def extract_authors(path: Path, text: str, title: str) -> List[str]:
    stem = path.stem
    if "_" in stem:
        suffix = stem.rsplit("_", 1)[-1]
        if re.fullmatch(r"[\u4e00-\u9fff]{2,6}", suffix):
            return [suffix]
    match = re.match(r"^(19|20)\d{2}\s+([\u4e00-\u9fff]{2,6})\s+", stem)
    if match:
        return [match.group(2)]

    lines = [re.sub(r"\s+", "", line).strip() for line in text[:1800].splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines[:30]):
        if title and (title.replace(" ", "")[:12] in line or line[:12] in title.replace(" ", "")):
            for next_line in lines[index + 1 : index + 5]:
                if 2 <= len(next_line) <= 28 and re.search(r"[\u4e00-\u9fff]{2,4}(?:，|,|、)?", next_line):
                    if not any(token in next_line for token in ["摘要", "目的", "方法", "医院", "大学", "基金"]):
                        return [name for name in re.split(r"[，,、\s]+", next_line) if 2 <= len(name) <= 5][:8]
    return []


def extract_journal(text: str, title: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text[:1600].splitlines()]
    title_key = title.replace(" ", "")[:10]
    candidates = []
    for line in lines[:20]:
        compact = line.replace(" ", "")
        if not compact or (title_key and title_key in compact):
            continue
        if any(token in compact for token in ["期", "卷", "Journal", "Medicine", "医学", "中医药", "医药", "护理", "研究"]):
            if len(compact) <= 80:
                candidates.append(line)
    return candidates[0] if candidates else ""


def _sentence_window(text: str, index: int, radius: int = 180) -> str:
    start = max(0, index - radius)
    end = min(len(text), index + radius)
    value = text[start:end]
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ，,。；;：:")


def extract_evaluation_data(text: str) -> List[Dict[str, str]]:
    patterns = [
        r"P\s*[<=>]\s*0?\.\d+",
        r"\d+(?:\.\d+)?\s*%",
        r"\d+\s*例",
        r"总有效率.{0,80}",
        r"有效率.{0,80}",
        r"随机.{0,80}",
        r"对照组.{0,120}",
        r"观察组.{0,120}",
        r"差异.{0,80}统计学意义",
    ]
    items: List[Dict[str, str]] = []
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            snippet = _sentence_window(text, match.start(), 180)
            key = snippet[:120]
            if key in seen:
                continue
            seen.add(key)
            items.append({"type": "evaluation", "value": match.group(0), "evidence": snippet})
            if len(items) >= 14:
                return items
    return items


def add_entity(entities: List[Dict[str, str]], seen: set, text: str, entity_type: str, evidence: str = "") -> None:
    text = re.sub(r"\s+", "", text or "").strip(" ：:;；,.，。()（）[]【】")
    if not (2 <= len(text) <= 32):
        return
    if text.startswith(STOP_ENTITY_PREFIXES):
        return
    key = (text, entity_type)
    if key in seen:
        return
    seen.add(key)
    entities.append({"text": text, "type": entity_type, "evidence": evidence[:240]})


def phrase_candidates(text: str) -> Iterable[str]:
    for chunk in re.split(r"[，,。；;：:、\n\(\)（）\[\]【】]", text):
        chunk = re.sub(r"\s+", "", chunk)
        if 2 <= len(chunk) <= 42:
            yield chunk


def extract_entities(article: Dict[str, Any]) -> List[Dict[str, str]]:
    evidence_text = "。".join(
        str(article.get(field) or "")
        for field in ["title", "abstract", "conclusion"]
    )
    evidence_text += "。" + "；".join(article.get("keywords") or [])
    entities: List[Dict[str, str]] = []
    seen = set()

    add_entity(entities, seen, article.get("source_condition", ""), "现代文献病种", "来源目录")
    for keyword in article.get("keywords", []):
        add_entity(entities, seen, keyword, "关键词", "文献关键词")

    for term in OUTCOME_TERMS:
        if term in evidence_text:
            add_entity(entities, seen, term, "结局指标/症状", term)

    for term in METRIC_TERMS:
        if re.search(re.escape(term), evidence_text, re.I):
            add_entity(entities, seen, term, "评估指标", term)

    for match in re.finditer(r"[\u4e00-\u9fff]{1,10}(?:证|型)", evidence_text):
        add_entity(entities, seen, match.group(0), "证候/分型", _sentence_window(evidence_text, match.start()))

    for match in re.finditer(r"[\u4e00-\u9fff]{1,8}穴", evidence_text):
        add_entity(entities, seen, match.group(0), "穴位", _sentence_window(evidence_text, match.start()))

    disease_pattern = r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:病|症|炎|痛|瘤|不孕|缺乳|出血|腺肌症|腺肌病|内膜异位症)"
    for match in re.finditer(disease_pattern, evidence_text):
        add_entity(entities, seen, match.group(0), "疾病/病症", _sentence_window(evidence_text, match.start()))

    for phrase in phrase_candidates(evidence_text):
        if any(phrase.endswith(suffix) or suffix in phrase for suffix in THERAPY_SUFFIXES):
            if any(skip in phrase for skip in ["目的", "方法", "结果", "结论", "比较", "统计学"]):
                continue
            add_entity(entities, seen, phrase, "治疗方法/方药", phrase)

    return entities[:80]


def entities_by_type(entities: Sequence[Dict[str, str]], entity_type: str) -> List[Dict[str, str]]:
    return [entity for entity in entities if entity.get("type") == entity_type]


def add_relation(
    relations: List[Dict[str, str]],
    seen: set,
    head: Dict[str, str],
    relation: str,
    tail: Dict[str, str],
    evidence: str,
) -> None:
    key = (head["text"], relation, tail["text"])
    if key in seen or head["text"] == tail["text"]:
        return
    seen.add(key)
    relations.append(
        {
            "head_text": head["text"],
            "head_type": head.get("type", ""),
            "relation": relation,
            "tail_text": tail["text"],
            "tail_type": tail.get("type", ""),
            "evidence": evidence[:320],
        }
    )


def build_relations(article: Dict[str, Any], entities: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    relations: List[Dict[str, str]] = []
    seen = set()
    evidence = article.get("conclusion") or article.get("abstract") or article.get("title") or ""

    conditions = entities_by_type(entities, "现代文献病种") + entities_by_type(entities, "疾病/病症")
    therapies = entities_by_type(entities, "治疗方法/方药")
    outcomes = entities_by_type(entities, "结局指标/症状")
    syndromes = entities_by_type(entities, "证候/分型")
    metrics = entities_by_type(entities, "评估指标")
    acupoints = entities_by_type(entities, "穴位")

    for therapy in therapies[:14]:
        for condition in conditions[:6]:
            add_relation(relations, seen, therapy, "现代文献-治疗/干预病症", condition, evidence)
        for outcome in outcomes[:8]:
            add_relation(relations, seen, therapy, "现代文献-改善/观察结局", outcome, evidence)

    for syndrome in syndromes[:10]:
        for condition in conditions[:4]:
            add_relation(relations, seen, condition, "现代文献-涉及证候/分型", syndrome, evidence)

    for acupoint in acupoints[:10]:
        for therapy in therapies[:6]:
            add_relation(relations, seen, therapy, "现代文献-使用穴位", acupoint, evidence)

    for metric in metrics[:10]:
        for outcome in outcomes[:6]:
            add_relation(relations, seen, metric, "现代文献-评估结局", outcome, evidence)

    return relations[:80]


def build_search_text(article: Dict[str, Any], entities: Sequence[Dict[str, str]], relations: Sequence[Dict[str, str]]) -> str:
    parts = [
        article.get("title", ""),
        article.get("source_condition", ""),
        article.get("treatment_category", ""),
        article.get("journal", ""),
        article.get("publication_year", ""),
        article.get("abstract", ""),
        article.get("conclusion", ""),
        " ".join(article.get("keywords") or []),
    ]
    for item in article.get("evaluation_data") or []:
        parts.append(item.get("value", ""))
        parts.append(item.get("evidence", ""))
    for entity in entities:
        parts.append(entity.get("text", ""))
        parts.append(entity.get("type", ""))
    for relation in relations:
        parts.extend(
            [
                relation.get("head_text", ""),
                relation.get("relation", ""),
                relation.get("tail_text", ""),
                relation.get("evidence", ""),
            ]
        )
    return compact_for_regex(" ".join(parts))


def create_schema(conn: sqlite3.Connection) -> str:
    conn.executescript(
        """
        DROP TABLE IF EXISTS modern_articles;
        DROP TABLE IF EXISTS modern_entities;
        DROP TABLE IF EXISTS modern_relations;
        DROP TABLE IF EXISTS modern_fts;

        CREATE TABLE modern_articles (
            article_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            title TEXT,
            authors_json TEXT NOT NULL DEFAULT '[]',
            journal TEXT,
            publication_year TEXT,
            publication_date TEXT,
            language TEXT,
            source_condition TEXT,
            treatment_category TEXT,
            treatment_has_herbs INTEGER NOT NULL DEFAULT 0,
            herbs_listed INTEGER NOT NULL DEFAULT 0,
            abstract TEXT,
            conclusion TEXT,
            keywords_json TEXT NOT NULL DEFAULT '[]',
            evaluation_data_json TEXT NOT NULL DEFAULT '[]',
            full_text TEXT,
            text_quality TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            search_text TEXT
        );

        CREATE TABLE modern_entities (
            article_id TEXT NOT NULL,
            entity_text TEXT NOT NULL,
            entity_type TEXT,
            evidence TEXT
        );

        CREATE TABLE modern_relations (
            article_id TEXT NOT NULL,
            head_text TEXT NOT NULL,
            head_type TEXT,
            relation TEXT NOT NULL,
            tail_text TEXT NOT NULL,
            tail_type TEXT,
            evidence TEXT
        );
        """
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE modern_fts USING fts5(article_id UNINDEXED, title, abstract, conclusion, keywords, evaluation, search_text, tokenize='trigram')"
        )
        tokenizer = "trigram"
    except sqlite3.OperationalError:
        conn.execute(
            "CREATE VIRTUAL TABLE modern_fts USING fts5(article_id UNINDEXED, title, abstract, conclusion, keywords, evaluation, search_text, tokenize='unicode61')"
        )
        tokenizer = "unicode61"
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_modern_entity_text ON modern_entities(entity_text);
        CREATE INDEX IF NOT EXISTS idx_modern_entity_article ON modern_entities(article_id);
        CREATE INDEX IF NOT EXISTS idx_modern_relation_head ON modern_relations(head_text);
        CREATE INDEX IF NOT EXISTS idx_modern_relation_tail ON modern_relations(tail_text);
        CREATE INDEX IF NOT EXISTS idx_modern_relation_article ON modern_relations(article_id);
        """
    )
    return tokenizer


def parse_article(path: Path, root: Path) -> Tuple[Dict[str, Any], List[Dict[str, str]], List[Dict[str, str]]]:
    relative_path = str(path.relative_to(root)).replace("\\", "/")
    text, metadata, page_count, text_quality = extract_pdf_text(path)
    normalized = compact_for_regex(text)
    folder_meta = parse_folder_metadata(path, root)
    title = metadata.get("/Title", "").strip() or normalize_title_from_filename(path)
    if not title or len(title) < 3:
        title = normalize_title_from_filename(path)
    title = clean_pdf_text(title)
    abstract = extract_abstract(normalized)
    conclusion = extract_conclusion(normalized, abstract)
    keywords = extract_keywords(normalized)
    evaluation_data = extract_evaluation_data(normalized)
    year = extract_year(path, normalized, metadata)
    article = {
        "article_id": derive_article_id(relative_path),
        "relative_path": relative_path,
        "file_name": path.name,
        "title": title,
        "authors": extract_authors(path, text, title),
        "journal": extract_journal(text, title),
        "publication_year": year,
        "publication_date": year,
        "abstract": abstract,
        "conclusion": conclusion,
        "keywords": keywords,
        "evaluation_data": evaluation_data,
        "full_text": normalized,
        "text_quality": text_quality,
        "page_count": page_count,
        "metadata": metadata,
        **folder_meta,
    }
    entities = extract_entities(article)
    relations = build_relations(article, entities)
    article["search_text"] = build_search_text(article, entities, relations)
    return article, entities, relations


def insert_article(
    conn: sqlite3.Connection,
    article: Dict[str, Any],
    entities: Sequence[Dict[str, str]],
    relations: Sequence[Dict[str, str]],
) -> None:
    conn.execute(
        """
        INSERT INTO modern_articles(
            article_id, relative_path, file_name, title, authors_json, journal,
            publication_year, publication_date, language, source_condition,
            treatment_category, treatment_has_herbs, herbs_listed, abstract,
            conclusion, keywords_json, evaluation_data_json, full_text,
            text_quality, page_count, metadata_json, search_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article["article_id"],
            article["relative_path"],
            article["file_name"],
            article.get("title", ""),
            compact_json(article.get("authors") or []),
            article.get("journal", ""),
            article.get("publication_year", ""),
            article.get("publication_date", ""),
            article.get("language", ""),
            article.get("source_condition", ""),
            article.get("treatment_category", ""),
            int(bool(article.get("treatment_has_herbs"))),
            int(bool(article.get("herbs_listed"))),
            article.get("abstract", ""),
            article.get("conclusion", ""),
            compact_json(article.get("keywords") or []),
            compact_json(article.get("evaluation_data") or []),
            article.get("full_text", ""),
            article.get("text_quality", ""),
            int(article.get("page_count") or 0),
            compact_json(article.get("metadata") or {}),
            article.get("search_text", ""),
        ),
    )
    conn.execute(
        """
        INSERT INTO modern_fts(article_id, title, abstract, conclusion, keywords, evaluation, search_text)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article["article_id"],
            article.get("title", ""),
            article.get("abstract", ""),
            article.get("conclusion", ""),
            " ".join(article.get("keywords") or []),
            compact_json(article.get("evaluation_data") or []),
            article.get("search_text", ""),
        ),
    )
    conn.executemany(
        """
        INSERT INTO modern_entities(article_id, entity_text, entity_type, evidence)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                article["article_id"],
                entity.get("text", ""),
                entity.get("type", ""),
                entity.get("evidence", ""),
            )
            for entity in entities
            if entity.get("text")
        ],
    )
    conn.executemany(
        """
        INSERT INTO modern_relations(article_id, head_text, head_type, relation, tail_text, tail_type, evidence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                article["article_id"],
                relation.get("head_text", ""),
                relation.get("head_type", ""),
                relation.get("relation", ""),
                relation.get("tail_text", ""),
                relation.get("tail_type", ""),
                relation.get("evidence", ""),
            )
            for relation in relations
            if relation.get("head_text") and relation.get("relation") and relation.get("tail_text")
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OpenTCM modern literature SQLite/JSONL index from PDFs.")
    parser.add_argument("--source_dir", default=os.path.join("data", "modern_literature"))
    parser.add_argument("--sqlite_out", default=os.path.join("data", "opentcm_modern.sqlite"))
    parser.add_argument("--jsonl_out", default=os.path.join("data", "modern_literature_index.jsonl"))
    parser.add_argument("--summary_out", default=os.path.join("data", "modern_literature_schema_summary.json"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    root = Path(args.source_dir)
    pdfs = sorted(root.rglob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        raise SystemExit(f"未在 {root} 找到 PDF 文件。")

    Path(args.sqlite_out).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.sqlite_out)
    tokenizer = create_schema(conn)

    stats = Counter()
    entity_types = Counter()
    relation_types = Counter()
    quality_counts = Counter()

    with open(args.jsonl_out, "w", encoding="utf-8") as handle:
        for index, pdf_path in enumerate(pdfs, start=1):
            article, entities, relations = parse_article(pdf_path, root)
            insert_article(conn, article, entities, relations)
            handle.write(
                compact_json(
                    {
                        **{k: v for k, v in article.items() if k != "full_text"},
                        "text_excerpt": article.get("full_text", "")[:1600],
                        "entities": entities,
                        "relations": relations,
                    }
                )
                + "\n"
            )
            stats["articles"] += 1
            stats["entities"] += len(entities)
            stats["relations"] += len(relations)
            quality_counts[article.get("text_quality", "")] += 1
            entity_types.update(entity.get("type", "") for entity in entities)
            relation_types.update(relation.get("relation", "") for relation in relations)
            if index % 25 == 0:
                conn.commit()
                print(f"已处理 {index}/{len(pdfs)} 篇；实体 {stats['entities']}；关系 {stats['relations']}")

    conn.commit()
    summary = {
        "source_dir": str(root),
        "sqlite_out": args.sqlite_out,
        "jsonl_out": args.jsonl_out,
        "article_count": stats["articles"],
        "entity_count": stats["entities"],
        "relation_count": stats["relations"],
        "fts_tokenizer": tokenizer,
        "text_quality_counts": dict(quality_counts),
        "entity_type_counts": dict(entity_types.most_common()),
        "relation_type_counts": dict(relation_types.most_common()),
    }
    with open(args.summary_out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
