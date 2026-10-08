import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = (
    os.getenv("DEEPSEEK_REVIEWER_MODEL")
    or os.getenv("DEEPSEEK_MODEL")
    or "deepseek-v4-pro"
)

DEFAULT_KG_DB = os.getenv("OPENTCM_KG_DB", os.path.join("data", "opentcm_kg.sqlite"))
DEFAULT_MODERN_DB = os.getenv("OPENTCM_MODERN_DB", os.path.join("data", "opentcm_modern.sqlite"))

LANGUAGE_NAMES = {
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
    "en": "English",
}

OPENING_SENTENCES = {
    "zh-Hans": "作为大语言模型驱动的中医智能助手，OpenTCM会竭力为您提供一份准确、可溯源的回答。",
    "zh-Hant": "作為大語言模型驅動的中醫智能助手，OpenTCM會竭力為您提供一份準確、可溯源的回答。",
    "en": "As an LLM-powered intelligent TCM assistant, OpenTCM will do its best to provide an accurate, source-traceable answer.",
}

TRAD_TO_SIMP = str.maketrans(
    {
        "請": "请",
        "檢": "检",
        "索": "索",
        "並": "并",
        "醫": "医",
        "藥": "药",
        "療": "疗",
        "經": "经",
        "證": "证",
        "據": "据",
        "獻": "献",
        "現": "现",
        "宮": "宫",
        "內": "内",
        "異": "异",
        "總": "总",
        "結": "结",
        "組": "组",
        "關": "关",
        "聯": "联",
        "於": "于",
        "婦": "妇",
        "類": "类",
        "與": "与",
        "斷": "断",
        "臨": "临",
        "床": "床",
        "癥": "症",
        "瘀": "瘀",
        "濕": "湿",
        "熱": "热",
        "帶": "带",
        "衝": "冲",
        "任": "任",
        "們": "们",
    }
)

NOISE_PHRASES = [
    "请检索并分析",
    "请只检索",
    "请只",
    "请检索分析",
    "检索并分析",
    "请检索",
    "检索",
    "分析",
    "总结",
    "归纳",
    "作用",
    "功效",
    "主治",
    "关于",
    "有关",
    "主要",
    "相关",
    "证据",
    "文献",
    "古代文献",
    "古籍文献",
    "妇科类古籍",
    "女科类古籍",
    "妇科类",
    "女科类",
    "古籍",
    "古代",
    "现代文献",
    "现代临床文献",
    "现代临床证据",
    "现代",
    "中医药治疗",
    "中医药",
    "中医",
    "治疗",
    "常用",
    "只",
    "主要中医证型",
    "中医证型",
    "证型",
    "中药组合",
    "用药组合",
    "药物组合",
]

STOP_TERMS = {
    "请",
    "请只",
    "只",
    "并",
    "和",
    "与",
    "及",
    "以及",
    "或者",
    "哪些",
    "什么",
    "如何",
    "可以",
    "需要",
    "是否",
    "证据",
    "文献",
    "研究",
    "临床",
    "现代",
    "古代",
    "古籍",
    "中医",
    "中药",
    "治疗",
    "分析",
    "总结",
    "主要",
    "常用",
    "相关",
    "有关",
    "关于",
    "妇科类",
    "女科类",
    "中",
}

ENTITY_ALIASES = {
    "痛经": ["痛经", "痛經", "经痛", "經痛", "原发性痛经", "继发性痛经"],
    "子宫内膜异位症": ["子宫内膜异位症", "子宮內膜異位症", "内异症", "內異症", "EMs", "endometriosis"],
    "子宫腺肌症": ["子宫腺肌症", "子宮腺肌症", "腺肌症", "adenomyosis"],
    "盆腔炎": ["盆腔炎", "盆腔炎性疾病", "pelvic inflammatory disease"],
    "月经不调": ["月经不调", "月經不調"],
    "不孕": ["不孕", "不孕症"],
    "带下": ["带下", "帶下"],
}

MODERN_ALIASES = {
    "痛经": ["原发性痛经", "继发性痛经", "经痛", "dysmenorrhea"],
    "子宫内膜异位症": ["内异症", "EMs", "endometriosis", "子宫内膜异位症相关疼痛"],
    "子宫腺肌症": ["腺肌症", "adenomyosis"],
    "盆腔炎": ["盆腔炎性疾病", "pelvic inflammatory disease"],
}

CLASSICAL_MAPPINGS = {
    "痛经": [
        "经行腹痛",
        "经来腹痛",
        "经水来腹痛",
        "月水来腹痛",
        "月经来腹痛",
        "经前腹痛",
        "经后腹痛",
        "行经腹痛",
        "经行少腹痛",
        "少腹痛",
    ],
    "子宫内膜异位症": [
        "痛经",
        "经行腹痛",
        "癥瘕",
        "症瘕",
        "少腹痛",
        "月经不调",
        "不孕",
        "血瘀",
        "瘀血",
    ],
    "子宫腺肌症": [
        "痛经",
        "经行腹痛",
        "癥瘕",
        "症瘕",
        "月经过多",
        "崩漏",
        "少腹痛",
        "血瘀",
    ],
    "盆腔炎": ["带下", "少腹痛", "腹痛", "湿热下注", "热毒", "癥瘕", "症瘕"],
}

QUERY_FACET_TERMS = {
    "证型": ["证型", "证候", "辨证"],
    "中药组合": ["中药组合", "用药组合", "药物组合", "方剂", "药材"],
    "方剂": ["方剂", "方药", "处方"],
}


def normalize_chinese_text(text: str) -> str:
    return str(text or "").translate(TRAD_TO_SIMP)


def unique_terms(terms: Sequence[str], limit: Optional[int] = None) -> List[str]:
    result: List[str] = []
    seen = set()
    for term in terms:
        term = normalize_chinese_text(str(term or "")).strip(" \t\r\n，,。；;：:?？!！、（）()[]【】「」'\"")
        term = re.sub(r"(相关|有关|的)$", "", term).strip()
        if term.startswith("中") and any(alias in term[1:] for aliases in ENTITY_ALIASES.values() for alias in aliases):
            term = term[1:]
        if not term or term in STOP_TERMS:
            continue
        if len(term) > 28 and not any(alias in term for aliases in ENTITY_ALIASES.values() for alias in aliases):
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(term)
        if limit and len(result) >= limit:
            break
    return result


def clean_query_keyword(term: str) -> str:
    value = normalize_chinese_text(term)
    for phrase in NOISE_PHRASES:
        value = value.replace(phrase, " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\r\n，,。；;：:?？!！、（）()[]【】「」'\"")


def split_query_terms(query: str) -> List[str]:
    working = normalize_chinese_text(query)
    for phrase in NOISE_PHRASES:
        working = working.replace(phrase, " ")
    pieces = re.split(r"[\s，,。；;：:?？!！、（）()【】「」]+|以及|或者|并且|并|和|与|及", working)
    return unique_terms(piece for piece in pieces if len(piece.strip()) >= 2)


def known_terms_in_query(query: str) -> List[str]:
    normalized = normalize_chinese_text(query).lower()
    terms: List[str] = []
    for canonical, aliases in ENTITY_ALIASES.items():
        for alias in [canonical] + aliases:
            if normalize_chinese_text(alias).lower() in normalized:
                terms.append(canonical)
                break
    return unique_terms(terms)


def detect_source_scope(query: str) -> str:
    normalized = normalize_chinese_text(query).lower()
    wants_modern = any(term in normalized for term in ["现代文献", "现代临床", "现代研究", "现代证据", "modern literature"])
    wants_classical = any(term in normalized for term in ["古代文献", "古籍", "古典文献", "古文献", "古籍证据", "classical"])
    if wants_modern and not wants_classical:
        return "modern"
    if wants_classical and not wants_modern:
        return "classical"
    return "both"


def detect_category_filter(query: str) -> Optional[str]:
    normalized = normalize_chinese_text(query)
    if any(term in normalized for term in ["妇科类古籍", "妇科古籍", "女科古籍", "妇科类文献", "女科类文献"]):
        return "gynecology"
    return None


def build_semantic_terms(core_terms: Sequence[str], source_scope: str) -> Tuple[List[str], List[str]]:
    aliases: List[str] = []
    mapped: List[str] = []
    for term in core_terms:
        canonical_hits = known_terms_in_query(term) or [normalize_chinese_text(term)]
        for canonical in canonical_hits:
            aliases.extend(MODERN_ALIASES.get(canonical, []))
            if source_scope != "modern":
                mapped.extend(CLASSICAL_MAPPINGS.get(canonical, []))
    return unique_terms(aliases, limit=12), unique_terms(mapped, limit=18)


def infer_query_facets(query: str) -> List[str]:
    normalized = normalize_chinese_text(query)
    facets: List[str] = []
    for label, aliases in QUERY_FACET_TERMS.items():
        if any(alias in normalized for alias in aliases):
            facets.append(label)
    return unique_terms(facets, limit=4)


def infer_intent(query: str, source_scope: str) -> str:
    normalized = normalize_chinese_text(query)
    if any(term in normalized for term in ["文献证据", "文献", "证据", "研究"]) and any(term in normalized for term in ["证型", "证候", "中药组合", "用药组合", "方剂", "药材"]):
        prefix = "现代文献证据" if source_scope == "modern" else ("古代文献证据" if source_scope == "classical" else "文献证据")
        return f"{prefix}与证型-方药规律分析"
    if any(word in normalized for word in ["诊断", "辨证", "什么病", "证候", "证型"]):
        return "诊断与辨证"
    if any(word in normalized for word in ["治疗", "治", "怎么办", "方剂", "用药", "调理", "中药组合"]):
        return "诊疗与治疗方案"
    if any(word in normalized for word in ["功效", "作用", "主治", "可以治疗"]):
        return "查询方药功效"
    return "查询实体信息"


def default_chain_focus(intent: str, source_scope: str) -> str:
    if source_scope == "modern":
        return "疾病->证型->治法->方剂/中药组合->疗效证据"
    if "证型" in intent or "治疗" in intent or "诊疗" in intent:
        return "现代病名/症状->古籍异名->证候->治法->方剂->药材"
    return "实体->相关症状/证候->治法/方药->出处原文"


def herb_function_query(query: str, terms: Sequence[str], intent: str) -> bool:
    normalized = normalize_chinese_text(query)
    return (
        any(token in normalized for token in ["作用", "功效", "主治", "药性", "性味"])
        or any(token in intent for token in ["方药功效", "功效"])
    ) and bool(terms)


def path_leaf(relative_path: str) -> str:
    return normalize_chinese_text(str(relative_path or "").replace("\\", "/").split("/")[-1])


def normalize_language(language: Optional[str]) -> str:
    if language in LANGUAGE_NAMES:
        return language
    aliases = {
        "zh": "zh-Hans",
        "zh-cn": "zh-Hans",
        "zh-hans": "zh-Hans",
        "cn": "zh-Hans",
        "simplified": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh-hk": "zh-Hant",
        "zh-hant": "zh-Hant",
        "traditional": "zh-Hant",
        "en-us": "en",
        "en": "en",
    }
    return aliases.get(str(language or "").strip().lower(), "zh-Hans")


def _chat_completion_url(base_url: str) -> str:
    base_url = (base_url or "https://api.deepseek.com").rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _shorten(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _safe_json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _is_reasonable_literature_text(text: str) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    sample = text[:1200]
    total = len(sample)
    if total < 12:
        return True
    valid = 0
    controls = 0
    for ch in sample:
        code = ord(ch)
        category = unicodedata.category(ch)
        if ch.isspace():
            valid += 1
        elif category.startswith("C"):
            controls += 1
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            valid += 1
        elif 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF:
            valid += 1
        elif 32 <= code <= 126:
            valid += 1
        elif ch in "，。；：？！、（）《》“”‘’·—-±≤≥％%":
            valid += 1
    return controls == 0 and valid / total >= 0.62


def clean_literature_text(text: str, limit: int = 1800) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text or not _is_reasonable_literature_text(text):
        return ""
    return _shorten(text, limit)


class DeepSeekClient:
    def __init__(self) -> None:
        self.api_key = DEEPSEEK_API_KEY
        self.model = DEEPSEEK_MODEL
        self.url = _chat_completion_url(DEEPSEEK_BASE_URL)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and "your-" not in self.api_key.lower())

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 700,
    ) -> str:
        if not self.configured:
            return "API Key未配置或无效。"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            response = requests.post(
                self.url,
                headers=self._headers(),
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            return f"API请求失败: {exc}"

    def stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 3600,
    ) -> Iterator[str]:
        if not self.configured:
            yield "API Key未配置或无效。"
            return

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        try:
            with requests.post(
                self.url,
                headers=self._headers(),
                json=payload,
                timeout=240,
                stream=True,
            ) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield text
        except requests.exceptions.RequestException as exc:
            yield f"调用DeepSeek API时发生错误: {exc}"


class SQLiteKnowledgeBase:
    def __init__(self, db_path: str = DEFAULT_KG_DB) -> None:
        self.db_path = db_path
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"未找到知识库 SQLite 文件: {db_path}。请先运行 scripts/build_paragraph_kg_index.py。"
            )
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only = ON")

    def close(self) -> None:
        self.conn.close()

    def _document_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "paragraph_id": row["paragraph_id"],
            "book_title": row["book_title"],
            "relative_path": row["relative_path"],
            "text": row["text"],
            "entities": _safe_json_loads(row["entities_json"], []),
            "triplets": _safe_json_loads(row["triplets_json"], []),
            "entity_count": row["entity_count"],
            "triplet_count": row["triplet_count"],
        }

    def _get_document(self, paragraph_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT paragraph_id, relative_path, book_title, text, entities_json,
                   triplets_json, entity_count, triplet_count
            FROM documents
            WHERE paragraph_id = ?
            """,
            (paragraph_id,),
        ).fetchone()
        return self._document_from_row(row) if row else None

    def _add_candidate(
        self,
        candidates: Dict[str, Dict[str, Any]],
        paragraph_id: str,
        score: float,
        reason: str,
    ) -> None:
        if not paragraph_id:
            return
        item = candidates.setdefault(paragraph_id, {"score": 0.0, "reasons": set()})
        item["score"] += score
        item["reasons"].add(reason)

    def _matches_category_filter(self, doc: Dict[str, Any], category_filter: Optional[str]) -> bool:
        if not category_filter:
            return True
        haystack = normalize_chinese_text(
            " ".join(
                [
                    doc.get("book_title", ""),
                    doc.get("relative_path", ""),
                    doc.get("text", "")[:220],
                ]
            )
        )
        if category_filter == "gynecology":
            return any(
                term in haystack
                for term in [
                    "妇科",
                    "女科",
                    "胎产",
                    "产后",
                    "妊娠",
                    "月经",
                    "经水",
                    "带下",
                    "崩漏",
                    "经闭",
                    "痛经",
                    "经行",
                ]
            )
        return True

    def _document_relevance_boost(self, doc: Dict[str, Any], terms: Sequence[str]) -> float:
        path_text = normalize_chinese_text(" ".join([doc.get("book_title", ""), doc.get("relative_path", "")]))
        leaf = path_leaf(doc.get("relative_path", ""))
        body_head = normalize_chinese_text(doc.get("text", "")[:420])
        boost = 0.0
        for term in unique_terms(terms, limit=12):
            if len(term) < 2:
                continue
            exact_leaf = leaf in {f"{term}.txt", term}
            formula_leaf = term in leaf and any(mark in leaf for mark in ["汤", "散", "丸", "饮", "方", "膏", "丹", "剂"])
            if exact_leaf:
                boost += 220.0
                if any(mark in path_text for mark in ["本草", "药性", "药物", "中药", "药类", "方药类"]):
                    boost += 160.0
            elif formula_leaf:
                boost -= 55.0
            if term in path_text:
                boost += 120.0
            elif term in body_head:
                boost += 8.0
        if herb_function_query(path_text, terms, "") or any(mark in path_text for mark in ["本草类", "本草", "药性", "药物", "中医辞典/中药"]):
            if any(path_leaf(doc.get("relative_path", "")) in {f"{term}.txt", term} for term in unique_terms(terms, limit=8)):
                boost += 120.0
        if any(term in terms for term in ["痛经", "经行腹痛", "经来腹痛", "月水来腹痛"]):
            if any(mark in path_text for mark in ["痛经", "经候门", "妇科", "女科", "妇产"]):
                boost += 60.0
            else:
                boost -= 18.0
        if any(term in terms for term in ["子宫内膜异位症", "癥瘕", "症瘕"]):
            if any(mark in path_text for mark in ["癥瘕", "症瘕", "妇科", "女科", "妇产"]):
                boost += 18.0
        return boost

    def _fts_phrases(self, query: str, keywords: Sequence[str]) -> List[str]:
        phrases: List[str] = []
        for value in list(keywords or []) + [query]:
            value = re.sub(r"\s+", " ", str(value or "")).strip()
            value = value.replace('"', " ")
            if len(value) >= 2 and value not in phrases:
                phrases.append(value)
        return phrases[:8]

    def _safe_fts_match(self, phrase: str, limit: int = 24) -> List[sqlite3.Row]:
        if len(phrase) < 3:
            return []
        quoted = f'"{phrase}"'
        try:
            rows = list(
                self.conn.execute(
                    """
                    SELECT d.paragraph_id, bm25(documents_fts) AS rank
                    FROM documents_fts
                    JOIN documents d ON d.paragraph_id = documents_fts.paragraph_id
                    WHERE documents_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (quoted, limit),
                )
            )
            if rows:
                return rows
        except sqlite3.Error:
            pass
        return list(
            self.conn.execute(
                """
                SELECT paragraph_id, 0 AS rank
                FROM documents
                WHERE search_text LIKE ?
                LIMIT ?
                """,
                (f"%{phrase}%", limit),
            )
        )

    def _entity_matches(self, term: str, limit: int = 24) -> List[sqlite3.Row]:
        if len(term) < 2:
            return []
        rows = list(
            self.conn.execute(
                """
                SELECT DISTINCT paragraph_id, entity_text, entity_type
                FROM entity_mentions
                WHERE entity_text = ?
                LIMIT ?
                """,
                (term, limit),
            )
        )
        if len(rows) >= limit:
            return rows
        rows.extend(
            list(
                self.conn.execute(
                    """
                    SELECT DISTINCT paragraph_id, entity_text, entity_type
                    FROM entity_mentions
                    WHERE entity_text LIKE ?
                    LIMIT ?
                    """,
                    (f"%{term}%", limit - len(rows)),
                )
            )
        )
        return rows

    def _triple_matches(self, terms: Sequence[str], limit: int = 80) -> List[Dict[str, Any]]:
        triples: List[Dict[str, Any]] = []
        seen = set()
        for term in terms:
            if len(term) < 2:
                continue
            exact_rows = self.conn.execute(
                """
                SELECT paragraph_id, head_text, head_type, relation, tail_text, tail_type, evidence
                FROM triples
                WHERE head_text = ? OR tail_text = ?
                LIMIT ?
                """,
                (term, term, limit),
            ).fetchall()
            like_rows = []
            if len(exact_rows) < limit // 2:
                like_rows = self.conn.execute(
                    """
                    SELECT paragraph_id, head_text, head_type, relation, tail_text, tail_type, evidence
                    FROM triples
                    WHERE head_text LIKE ? OR tail_text LIKE ? OR relation LIKE ? OR evidence LIKE ?
                    LIMIT ?
                    """,
                    (f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", limit // 2),
                ).fetchall()
            for row in list(exact_rows) + list(like_rows):
                key = (
                    row["paragraph_id"],
                    row["head_text"],
                    row["relation"],
                    row["tail_text"],
                )
                if key in seen:
                    continue
                seen.add(key)
                triples.append(dict(row))
                if len(triples) >= limit:
                    return triples
        return triples

    def _relation_score(self, relation: str, intent: str, query: str) -> float:
        relation = relation or ""
        query = query or ""
        score = 5.0
        diagnostic_terms = ["诊断", "辨证", "症状", "证候", "什么病", "病机"]
        treatment_terms = ["治疗", "治法", "方剂", "用药", "怎么办", "调理"]
        herb_terms = ["中药", "药材", "方", "功效", "作用", "主治"]
        if any(t in relation for t in ["症状-指向证候", "体征-指向证候", "症状组-诊断为"]):
            score += 16
        if any(t in relation for t in ["疾病-具有症状", "疾病-具有体征", "疾病-分型为"]):
            score += 14
        if any(t in relation for t in ["治疗疾病", "治疗症状", "对应治法", "使用药材", "治疗证候"]):
            score += 18
        if any(t in query for t in diagnostic_terms) or "诊断" in intent:
            if any(t in relation for t in ["诊断", "指向证候", "具有症状", "具有体征", "分型"]):
                score += 12
        if any(t in query for t in treatment_terms) or "治疗" in intent:
            if any(t in relation for t in ["治疗", "治法", "使用药材", "对应治法"]):
                score += 14
        if any(t in query for t in herb_terms):
            if any(t in relation for t in ["治疗疾病", "治疗症状", "使用药材", "主治"]):
                score += 10
        return score

    def _chain_paths(
        self,
        seed_terms: Sequence[str],
        intent: str,
        query: str,
        max_paths: int = 8,
    ) -> List[Dict[str, Any]]:
        first_hop = self._triple_matches(seed_terms, limit=120)
        paths: List[Dict[str, Any]] = []
        seen = set()

        for first in sorted(
            first_hop,
            key=lambda t: self._relation_score(t["relation"], intent, query),
            reverse=True,
        )[:36]:
            next_terms = [first["head_text"], first["tail_text"]]
            second_hop = self._triple_matches(next_terms, limit=60)
            best_second = sorted(
                [
                    t
                    for t in second_hop
                    if not (
                        t["head_text"] == first["head_text"]
                        and t["tail_text"] == first["tail_text"]
                        and t["relation"] == first["relation"]
                    )
                ],
                key=lambda t: self._relation_score(t["relation"], intent, query),
                reverse=True,
            )[:3]

            if not best_second:
                path_key = (
                    first["head_text"],
                    first["relation"],
                    first["tail_text"],
                    first["paragraph_id"],
                )
                if path_key not in seen:
                    seen.add(path_key)
                    paths.append({"triples": [first], "score": self._relation_score(first["relation"], intent, query)})
                continue

            for second in best_second:
                path_key = (
                    first["head_text"],
                    first["relation"],
                    first["tail_text"],
                    second["relation"],
                    second["tail_text"],
                    second["paragraph_id"],
                )
                if path_key in seen:
                    continue
                seen.add(path_key)
                score = self._relation_score(first["relation"], intent, query) + self._relation_score(second["relation"], intent, query)
                paths.append({"triples": [first, second], "score": score})
                if len(paths) >= max_paths:
                    return sorted(paths, key=lambda p: p["score"], reverse=True)

        return sorted(paths, key=lambda p: p["score"], reverse=True)[:max_paths]

    def retrieve(
        self,
        query: str,
        keywords: Sequence[str],
        intent: str,
        limit: int = 10,
        category_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        candidates: Dict[str, Dict[str, Any]] = {}
        terms = [str(t).strip() for t in keywords if str(t).strip()]
        if query and query not in terms:
            terms.append(query)

        if herb_function_query(query, terms, intent):
            for term in unique_terms(terms, limit=8):
                if len(term) < 2:
                    continue
                for row in self.conn.execute(
                    """
                    SELECT paragraph_id, relative_path
                    FROM documents
                    WHERE relative_path LIKE ?
                    LIMIT 120
                    """,
                    (f"%{term}.txt",),
                ):
                    leaf = path_leaf(row["relative_path"])
                    if leaf == f"{term}.txt" or leaf.endswith(f"）{term}.txt") or leaf.endswith(f"){term}.txt"):
                        self._add_candidate(candidates, row["paragraph_id"], 520.0, f"单味药条目:{term}")

        for phrase in self._fts_phrases(query, terms):
            rows = self._safe_fts_match(phrase, limit=28)
            for index, row in enumerate(rows):
                self._add_candidate(
                    candidates,
                    row["paragraph_id"],
                    max(3.0, 36.0 - index),
                    f"全文匹配:{phrase}",
                )

        for term in terms[:12]:
            for index, row in enumerate(self._entity_matches(term, limit=20)):
                self._add_candidate(
                    candidates,
                    row["paragraph_id"],
                    max(4.0, 32.0 - index),
                    f"实体匹配:{row['entity_text']}",
                )

        matched_triples = self._triple_matches(terms[:12], limit=120)
        for triple in matched_triples:
            score = self._relation_score(triple["relation"], intent, query)
            self._add_candidate(
                candidates,
                triple["paragraph_id"],
                score,
                f"三元组:{triple['head_text']}->{triple['tail_text']}",
            )

        paths = self._chain_paths(terms[:12], intent, query)
        for path in paths:
            for triple in path["triples"]:
                self._add_candidate(
                    candidates,
                    triple["paragraph_id"],
                    18.0 + path["score"] / 4,
                    "链式路径",
                )

        if not candidates and query:
            for row in self.conn.execute(
                """
                SELECT paragraph_id
                FROM documents
                WHERE search_text LIKE ?
                LIMIT 20
                """,
                (f"%{query[:40]}%",),
            ):
                self._add_candidate(candidates, row["paragraph_id"], 5.0, "原文兜底")

        ranked = sorted(candidates.items(), key=lambda item: item[1]["score"], reverse=True)
        scored_docs = []
        for paragraph_id, meta in ranked[:260]:
            doc = self._get_document(paragraph_id)
            if not doc:
                continue
            if not self._matches_category_filter(doc, category_filter):
                continue
            adjusted_score = min(meta["score"], 220.0) + self._document_relevance_boost(doc, terms)
            scored_docs.append((adjusted_score, paragraph_id, doc))
        scored_docs.sort(key=lambda item: item[0], reverse=True)

        documents = []
        for _, paragraph_id, doc in scored_docs:
            doc["citation_id"] = f"S{len(documents) + 1}"
            doc["reasons"] = sorted(candidates[paragraph_id]["reasons"])[:4]
            documents.append(doc)
            if len(documents) >= limit:
                break

        citation_by_pid = {doc["paragraph_id"]: doc["citation_id"] for doc in documents}
        visible_paths = []
        for path in paths:
            path_triples = []
            citation_ids = []
            for triple in path["triples"]:
                if triple["paragraph_id"] in citation_by_pid:
                    citation_ids.append(citation_by_pid[triple["paragraph_id"]])
                    path_triples.append(triple)
            if path_triples:
                visible_paths.append(
                    {
                        "triples": path_triples,
                        "citation_ids": sorted(set(citation_ids), key=citation_ids.index),
                    }
                )
            if len(visible_paths) >= 5:
                break

        return {
            "documents": documents,
            "paths": visible_paths,
            "sources": [self._source_payload(doc) for doc in documents],
        }

    def _source_payload(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": doc["citation_id"],
            "paragraph_id": doc["paragraph_id"],
            "book_title": doc["book_title"],
            "relative_path": doc["relative_path"],
            "text": doc["text"],
            "entities": [
                {"text": ent.get("text"), "type": ent.get("type")}
                for ent in doc.get("entities", [])[:16]
            ],
            "triplets": [
                {
                    "head_text": tri.get("head_text"),
                    "relation": tri.get("relation"),
                    "tail_text": tri.get("tail_text"),
                    "evidence": tri.get("evidence"),
                }
                for tri in doc.get("triplets", [])[:16]
            ],
        }


class SQLiteModernLiteratureBase:
    def __init__(self, db_path: str = DEFAULT_MODERN_DB) -> None:
        self.db_path = db_path
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"未找到现代文献 SQLite 文件: {db_path}。请先运行 scripts/build_modern_literature_index.py。"
            )
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only = ON")

    def close(self) -> None:
        self.conn.close()

    def _article_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        abstract = clean_literature_text(row["abstract"], 1800)
        conclusion = clean_literature_text(row["conclusion"], 1400)
        return {
            "article_id": row["article_id"],
            "relative_path": row["relative_path"],
            "file_name": row["file_name"],
            "title": row["title"],
            "authors": _safe_json_loads(row["authors_json"], []),
            "journal": row["journal"],
            "publication_year": row["publication_year"],
            "publication_date": row["publication_date"],
            "language": row["language"],
            "source_condition": row["source_condition"],
            "treatment_category": row["treatment_category"],
            "treatment_has_herbs": bool(row["treatment_has_herbs"]),
            "herbs_listed": bool(row["herbs_listed"]),
            "abstract": abstract,
            "abstract_omitted": bool(row["abstract"] and not abstract),
            "conclusion": conclusion,
            "conclusion_omitted": bool(row["conclusion"] and not conclusion),
            "keywords": _safe_json_loads(row["keywords_json"], []),
            "evaluation_data": _safe_json_loads(row["evaluation_data_json"], []),
            "full_text": row["full_text"],
            "text_quality": row["text_quality"],
            "page_count": row["page_count"],
            "metadata": _safe_json_loads(row["metadata_json"], {}),
        }

    def _get_article(self, article_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT article_id, relative_path, file_name, title, authors_json, journal,
                   publication_year, publication_date, language, source_condition,
                   treatment_category, treatment_has_herbs, herbs_listed, abstract,
                   conclusion, keywords_json, evaluation_data_json, full_text,
                   text_quality, page_count, metadata_json
            FROM modern_articles
            WHERE article_id = ?
            """,
            (article_id,),
        ).fetchone()
        return self._article_from_row(row) if row else None

    def _entities(self, article_id: str, limit: int = 24) -> List[Dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT entity_text AS text, entity_type AS type, evidence
                FROM modern_entities
                WHERE article_id = ?
                LIMIT ?
                """,
                (article_id, limit),
            )
        ]

    def _relations(self, article_id: str, limit: int = 24) -> List[Dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT head_text, head_type, relation, tail_text, tail_type, evidence
                FROM modern_relations
                WHERE article_id = ?
                LIMIT ?
                """,
                (article_id, limit),
            )
        ]

    def _add_candidate(
        self,
        candidates: Dict[str, Dict[str, Any]],
        article_id: str,
        score: float,
        reason: str,
    ) -> None:
        if not article_id:
            return
        item = candidates.setdefault(article_id, {"score": 0.0, "reasons": set()})
        item["score"] += score
        item["reasons"].add(reason)

    def _fts_phrases(self, query: str, keywords: Sequence[str]) -> List[str]:
        phrases: List[str] = []
        for value in list(keywords or []) + [query]:
            value = re.sub(r"\s+", " ", str(value or "")).strip()
            value = value.replace('"', " ")
            if len(value) >= 2 and value not in phrases:
                phrases.append(value)
        return phrases[:8]

    def _safe_fts_match(self, phrase: str, limit: int = 30) -> List[sqlite3.Row]:
        if len(phrase) < 2:
            return []
        quoted = f'"{phrase}"'
        try:
            rows = list(
                self.conn.execute(
                    """
                    SELECT a.article_id, bm25(modern_fts) AS rank
                    FROM modern_fts
                    JOIN modern_articles a ON a.article_id = modern_fts.article_id
                    WHERE modern_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (quoted, limit),
                )
            )
            if rows:
                return rows
        except sqlite3.Error:
            pass
        return list(
            self.conn.execute(
                """
                SELECT article_id, 0 AS rank
                FROM modern_articles
                WHERE search_text LIKE ? OR title LIKE ? OR source_condition LIKE ?
                LIMIT ?
                """,
                (f"%{phrase}%", f"%{phrase}%", f"%{phrase}%", limit),
            )
        )

    def _entity_matches(self, term: str, limit: int = 30) -> List[sqlite3.Row]:
        if len(term) < 2:
            return []
        rows = list(
            self.conn.execute(
                """
                SELECT DISTINCT article_id, entity_text, entity_type
                FROM modern_entities
                WHERE entity_text = ?
                LIMIT ?
                """,
                (term, limit),
            )
        )
        if len(rows) >= limit:
            return rows
        rows.extend(
            list(
                self.conn.execute(
                    """
                    SELECT DISTINCT article_id, entity_text, entity_type
                    FROM modern_entities
                    WHERE entity_text LIKE ?
                    LIMIT ?
                    """,
                    (f"%{term}%", limit - len(rows)),
                )
            )
        )
        return rows

    def _relation_matches(self, terms: Sequence[str], limit: int = 100) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        seen = set()
        for term in terms:
            if len(term) < 2:
                continue
            matched = self.conn.execute(
                """
                SELECT article_id, head_text, head_type, relation, tail_text, tail_type, evidence
                FROM modern_relations
                WHERE head_text LIKE ? OR tail_text LIKE ? OR relation LIKE ? OR evidence LIKE ?
                LIMIT ?
                """,
                (f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%", limit // 2),
            ).fetchall()
            for row in matched:
                key = (row["article_id"], row["head_text"], row["relation"], row["tail_text"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(dict(row))
                if len(rows) >= limit:
                    return rows
        return rows

    def _relation_score(self, relation: str, intent: str, query: str) -> float:
        relation = relation or ""
        score = 6.0
        if "治疗/干预病症" in relation:
            score += 18
        if "改善/观察结局" in relation:
            score += 14
        if "评估结局" in relation:
            score += 12
        if "使用穴位" in relation:
            score += 10
        if "涉及证候" in relation:
            score += 9
        if any(word in query for word in ["疗效", "临床", "随机", "研究", "证据", "现代", "文献"]):
            score += 10
        if any(word in query for word in ["治疗", "调理", "方", "针", "灸", "护理"]):
            if any(token in relation for token in ["治疗", "干预", "改善"]):
                score += 8
        return score

    def _article_relevance_boost(self, article: Dict[str, Any], terms: Sequence[str], query: str) -> float:
        title = normalize_chinese_text(article.get("title", ""))
        raw_title = str(article.get("title", "")).lower()
        raw_abstract = str(article.get("abstract", "")).lower()
        raw_keywords = " ".join(str(item) for item in article.get("keywords", [])).lower()
        category = normalize_chinese_text(article.get("treatment_category", ""))
        path = normalize_chinese_text(article.get("relative_path", ""))
        haystack = " ".join([title, category, path, normalize_chinese_text(article.get("source_condition", ""))])
        boost = 0.0
        for term in unique_terms(terms, limit=10):
            raw_term = str(term).lower()
            if term in title:
                boost += 32.0
            elif term in haystack:
                boost += 12.0
            if raw_term and len(raw_term) >= 3:
                if raw_term in raw_title:
                    boost += 20.0
                if raw_term in raw_keywords:
                    boost += 16.0
                if raw_term in raw_abstract:
                    boost += 8.0
        wants_herb_pattern = any(term in normalize_chinese_text(query) for term in ["中药组合", "用药组合", "药物组合", "方剂", "方药"])
        wants_pattern = any(term in normalize_chinese_text(query) for term in ["证型", "证候", "辨证"])
        wants_modern_evidence = any(term in normalize_chinese_text(query) for term in ["现代", "临床", "研究", "证据", "英文", "english"])
        if wants_modern_evidence and str(article.get("language", "")).lower() == "en":
            boost += 18.0
        if wants_herb_pattern:
            if "含中药治疗" in category or "含中药治疗" in path:
                boost += 45.0
            if "有列明中药" in category or "有列明中药" in path:
                boost += 35.0
            if any(token in title for token in ["方", "汤", "丸", "散", "中药", "内异止痛", "化瘀", "活血"]):
                boost += 26.0
            if any(token in title for token in ["护理", "情志护理", "管理模式"]) and not any(token in title for token in ["方", "汤", "丸", "中药"]):
                boost -= 45.0
        if wants_pattern and any(token in title for token in ["型", "证", "辨证"]):
            boost += 22.0
        return boost

    def retrieve(self, query: str, keywords: Sequence[str], intent: str, limit: int = 8) -> Dict[str, Any]:
        candidates: Dict[str, Dict[str, Any]] = {}
        generic_terms = {
            "中医", "中药", "现代", "临床", "证据", "文献", "研究", "治疗", "疗效",
            "观察", "分析", "方法", "哪些", "什么", "如何", "是否", "有什么",
        }
        terms = [
            str(t).strip()
            for t in keywords
            if str(t).strip() and str(t).strip() not in generic_terms
        ]
        if not terms and query:
            terms.append(query)

        for term in terms[:8]:
            if len(term) < 2:
                continue
            for index, row in enumerate(
                self.conn.execute(
                    """
                    SELECT article_id, source_condition, title
                    FROM modern_articles
                    WHERE source_condition = ?
                    LIMIT 80
                    """,
                    (term,),
                )
            ):
                self._add_candidate(
                    candidates,
                    row["article_id"],
                    max(80.0, 180.0 - index),
                    f"现代文献病种精确匹配:{term}",
                )
            for index, row in enumerate(
                self.conn.execute(
                    """
                    SELECT article_id, source_condition, title
                    FROM modern_articles
                    WHERE title LIKE ? OR keywords_json LIKE ?
                    LIMIT 30
                    """,
                    (f"%{term}%", f"%{term}%"),
                )
            ):
                reason = f"现代文献病种/题名匹配:{term}"
                self._add_candidate(
                    candidates,
                    row["article_id"],
                    max(24.0, 95.0 - index),
                    reason,
                )

        for phrase in self._fts_phrases(query, terms):
            for index, row in enumerate(self._safe_fts_match(phrase, limit=30)):
                self._add_candidate(
                    candidates,
                    row["article_id"],
                    max(4.0, 36.0 - index),
                    f"现代文献全文匹配:{phrase}",
                )

        for term in terms[:12]:
            for index, row in enumerate(self._entity_matches(term, limit=24)):
                self._add_candidate(
                    candidates,
                    row["article_id"],
                    max(5.0, 32.0 - index),
                    f"现代文献实体匹配:{row['entity_text']}",
                )

        matched_relations = self._relation_matches(terms[:12], limit=120)
        for relation in matched_relations:
            self._add_candidate(
                candidates,
                relation["article_id"],
                self._relation_score(relation["relation"], intent, query),
                f"现代文献关系:{relation['head_text']}->{relation['tail_text']}",
            )

        ranked = sorted(candidates.items(), key=lambda item: item[1]["score"], reverse=True)
        scored_articles = []
        for article_id, meta in ranked[:220]:
            article = self._get_article(article_id)
            if not article:
                continue
            adjusted_score = min(meta["score"], 220.0) + self._article_relevance_boost(article, terms, query)
            scored_articles.append((adjusted_score, article_id, article, meta))
        scored_articles.sort(key=lambda item: item[0], reverse=True)

        articles = []
        for _, article_id, article, meta in scored_articles[:limit]:
            article["citation_id"] = f"M{len(articles) + 1}"
            article["reasons"] = sorted(meta["reasons"])[:5]
            article["entities"] = self._entities(article_id, limit=24)
            article["triplets"] = self._relations(article_id, limit=24)
            articles.append(article)

        return {
            "documents": articles,
            "sources": [self._source_payload(article) for article in articles],
        }

    def _source_payload(self, article: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": article["citation_id"],
            "source_type": "modern",
            "article_id": article["article_id"],
            "title": article.get("title", ""),
            "book_title": article.get("title", ""),
            "relative_path": article.get("relative_path", ""),
            "authors": article.get("authors", []),
            "journal": article.get("journal", ""),
            "publication_year": article.get("publication_year", ""),
            "publication_date": article.get("publication_date", ""),
            "source_condition": article.get("source_condition", ""),
            "treatment_category": article.get("treatment_category", ""),
            "keywords": article.get("keywords", []),
            "abstract": clean_literature_text(article.get("abstract", ""), 1800),
            "abstract_omitted": article.get("abstract_omitted", False),
            "conclusion": clean_literature_text(article.get("conclusion", ""), 1400),
            "conclusion_omitted": article.get("conclusion_omitted", False),
            "evaluation_data": article.get("evaluation_data", [])[:10],
            "text": clean_literature_text(article.get("abstract", ""), 1800)
            or clean_literature_text(article.get("conclusion", ""), 1400)
            or clean_literature_text(article.get("full_text", ""), 1200),
            "entities": [
                {"text": ent.get("text"), "type": ent.get("type")}
                for ent in article.get("entities", [])[:18]
            ],
            "triplets": [
                {
                    "head_text": tri.get("head_text"),
                    "relation": tri.get("relation"),
                    "tail_text": tri.get("tail_text"),
                    "evidence": tri.get("evidence"),
                }
                for tri in article.get("triplets", [])[:18]
            ],
        }


class GraphRAG:
    def __init__(self, knowledge_base: SQLiteKnowledgeBase, modern_base: Optional[SQLiteModernLiteratureBase] = None):
        self.kb = knowledge_base
        self.modern_kb = modern_base
        self.llm = DeepSeekClient()
        self.llm_cache: Dict[str, str] = {}
        self.retrieval_lock = threading.RLock()

    def _fallback_extract(self, query: str) -> Dict[str, Any]:
        return self._refine_extraction(query, {})

    def expand_followup_query(self, query: str, conversation_context: str = "") -> str:
        context = re.sub(r"\s+", " ", str(conversation_context or "")).strip()
        if not context:
            return query
        normalized = normalize_chinese_text(query)
        followup_terms = [
            "上述",
            "上文",
            "前面",
            "刚才",
            "继续",
            "这个",
            "这些",
            "该方",
            "此方",
            "它",
            "其",
            "them",
            "it",
            "above",
            "previous",
            "continue",
        ]
        is_followup = len(normalized) <= 40 or any(term in normalized.lower() for term in followup_terms)
        if not is_followup:
            return query
        return f"{query}\n\n【最近对话摘要】{context[:1200]}"

    def _refine_extraction(self, query: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        normalized_query = normalize_chinese_text(query)
        source_scope = str(raw.get("source_scope") or "").strip().lower()
        if source_scope not in {"classical", "modern", "both"}:
            source_scope = detect_source_scope(normalized_query)
        category_filter = raw.get("category_filter") or detect_category_filter(normalized_query)
        if isinstance(category_filter, str):
            category_filter = category_filter.strip().lower()
            if category_filter in {"", "none", "null", "false"}:
                category_filter = None

        raw_keywords = raw.get("keywords") or []
        if not isinstance(raw_keywords, list):
            raw_keywords = []
        cleaned_keywords = unique_terms(clean_query_keyword(item) for item in raw_keywords)
        cleaned_keywords = [
            term
            for term in cleaned_keywords
            if term not in STOP_TERMS and not any(noise in term for noise in ["请检索", "检索并分析", "总结主要"])
        ]

        core_terms = unique_terms(
            known_terms_in_query(normalized_query) + cleaned_keywords + split_query_terms(normalized_query),
            limit=10,
        )
        facets = infer_query_facets(normalized_query)
        aliases, mapped_terms = build_semantic_terms(core_terms, source_scope)

        display_keywords = unique_terms(core_terms + facets, limit=8)
        retrieval_terms = unique_terms(core_terms + aliases + mapped_terms + facets, limit=28)

        intent = str(raw.get("intent") or "").strip()
        if not intent or len(intent) > 30 or intent == "查询实体信息":
            intent = infer_intent(normalized_query, source_scope)
        else:
            intent = normalize_chinese_text(intent)
            if any(term in normalized_query for term in ["文献证据", "文献", "证据"]):
                intent = infer_intent(normalized_query, source_scope)

        if source_scope == "both" and herb_function_query(normalized_query, core_terms, intent):
            source_scope = "classical"

        chain_focus = normalize_chinese_text(str(raw.get("chain_focus") or "")).strip()
        if not chain_focus or chain_focus.lower() == "auto":
            chain_focus = default_chain_focus(intent, source_scope)

        if not display_keywords and normalized_query.strip():
            display_keywords = [normalized_query.strip()]
            retrieval_terms = unique_terms([normalized_query.strip()], limit=8)

        return {
            "keywords": display_keywords,
            "retrieval_terms": retrieval_terms,
            "semantic_mappings": mapped_terms,
            "intent": intent,
            "chain_focus": chain_focus,
            "source_scope": source_scope,
            "category_filter": category_filter,
            "normalized_query": normalized_query,
        }

    def extract_keywords_and_intent(
        self,
        query: str,
        language: str = "zh-Hans",
        fast: bool = False,
        conversation_context: str = "",
    ) -> Dict[str, Any]:
        if fast:
            return self._fallback_extract(query)
        cache_key = f"extract::{query}::{conversation_context[:200]}"
        if cache_key in self.llm_cache:
            try:
                return json.loads(self.llm_cache[cache_key])
            except Exception:
                pass

        prompt = f"""
请分析下面这个中医 GraphRAG 检索问题，输出严格 JSON。不要把“请检索、分析、总结、文献证据、中医药治疗”等任务指令当作关键词。
JSON 字段：
- keywords: 2到8个核心检索实体，只保留疾病、现代病名、古籍异名、症状、证候、方剂、中药、治法、穴位等名词实体。
- intent: 用一句短语概括意图，例如“古代文献证据与证型-方药规律分析”“现代文献证据与证型-方药规律分析”“诊断与辨证”“查询方药功效”。
- source_scope: classical、modern、both 三选一。用户明确说古籍/古代文献则 classical；明确说现代文献/现代临床证据则 modern；未限定则 both。
- chain_focus: 建议链式检索方向，例如“现代病名/症状->古籍异名->证候->治法->方剂->药材”。
- category_filter: 如果用户明确限定“妇科类古籍/女科古籍”，填 gynecology，否则填 null。

最近对话摘要（如果用户问题包含“上述、这个、继续、它”等指代，请据此补全语义；不要把摘要中的无关内容当作新问题）：
{conversation_context or "无"}

用户问题：{query}
"""
        response = self.llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=500,
        )
        try:
            match = re.search(r"\{.*\}", response, flags=re.S)
            data = json.loads(match.group(0) if match else response)
            keywords = data.get("keywords") or []
            if not isinstance(keywords, list):
                keywords = []
            result = {
                "keywords": [str(item).strip() for item in keywords if str(item).strip()][:8],
                "intent": str(data.get("intent") or "查询实体信息").strip(),
                "chain_focus": str(data.get("chain_focus") or "").strip(),
                "source_scope": str(data.get("source_scope") or "").strip(),
                "category_filter": data.get("category_filter"),
            }
            result = self._refine_extraction(query, result)
            self.llm_cache[cache_key] = json.dumps(result, ensure_ascii=False)
            return result
        except Exception:
            return self._fallback_extract(query)

    def retrieve_relevant_knowledge(
        self,
        query: str,
        extracted_info: Dict[str, Any],
        limit: int = 10,
    ) -> Dict[str, Any]:
        keywords = extracted_info.get("retrieval_terms") or extracted_info.get("keywords", [])
        intent = extracted_info.get("intent", "查询实体信息")
        source_scope = extracted_info.get("source_scope") or "both"
        category_filter = extracted_info.get("category_filter")
        search_query = extracted_info.get("normalized_query") or normalize_chinese_text(query)
        classical_limit = limit if source_scope == "classical" else max(1, limit // 2)
        modern_limit = limit if source_scope == "modern" else max(1, limit - classical_limit)
        with self.retrieval_lock:
            classical = {"documents": [], "sources": [], "paths": []}
            modern = {"documents": [], "sources": []}
            if source_scope in {"classical", "both"}:
                classical = self.kb.retrieve(
                    search_query,
                    keywords,
                    intent,
                    limit=classical_limit,
                    category_filter=category_filter,
                )
            if source_scope in {"modern", "both"} and self.modern_kb:
                modern = self.modern_kb.retrieve(search_query, keywords, intent, limit=modern_limit)
        sources = (classical.get("sources", []) + modern.get("sources", []))[:limit]
        return {
            **classical,
            "modern_documents": modern.get("documents", []),
            "modern_sources": modern.get("sources", []),
            "sources": sources,
        }

    def _format_path(self, path: Dict[str, Any]) -> str:
        parts = []
        for triple in path.get("triples", []):
            parts.append(
                f"{triple.get('head_text')} --{triple.get('relation')}--> {triple.get('tail_text')}"
            )
        citations = " ".join(f"[{cid}]" for cid in path.get("citation_ids", []))
        return "；".join(parts) + (f" {citations}" if citations else "")

    def _format_knowledge_for_llm(self, retrieval: Dict[str, Any]) -> str:
        documents = retrieval.get("documents", [])
        modern_documents = retrieval.get("modern_documents", [])
        if not documents and not modern_documents:
            return "未从知识库中检索到与问题直接相关的古籍段落或现代文献。"

        lines: List[str] = []
        paths = retrieval.get("paths", [])
        if paths:
            lines.append("【链式检索路径】")
            for idx, path in enumerate(paths[:5], start=1):
                lines.append(f"{idx}. {self._format_path(path)}")
            lines.append("")

        if documents:
            lines.append("【最相关古籍出处（最多10条）】")
            for doc in documents:
                cid = doc["citation_id"]
                lines.append(f"{cid}. 《{doc['book_title']}》")
                lines.append(f"路径: {doc['relative_path']}")
                lines.append(f"段落ID: {doc['paragraph_id']}")
                if doc.get("reasons"):
                    lines.append(f"命中线索: {'; '.join(doc['reasons'])}")
                lines.append(f"原文: {_shorten(doc['text'], 900)}")

                entities = doc.get("entities", [])
                if entities:
                    entity_bits = [
                        f"{ent.get('text')}({ent.get('type')})"
                        for ent in entities[:18]
                        if ent.get("text")
                    ]
                    if entity_bits:
                        lines.append("实体: " + "、".join(entity_bits))

                triplets = doc.get("triplets", [])
                if triplets:
                    lines.append("三元组:")
                    for tri in triplets[:16]:
                        evidence = tri.get("evidence") or ""
                        evidence_part = f"；证据: {_shorten(evidence, 120)}" if evidence else ""
                        lines.append(
                            f"- {tri.get('head_text')}({tri.get('head_type')}) "
                            f"--{tri.get('relation')}--> "
                            f"{tri.get('tail_text')}({tri.get('tail_type')}){evidence_part} [{cid}]"
                        )
                lines.append("")

        if modern_documents:
            lines.append("【最相关现代文献出处（最多10条）】")
            for article in modern_documents:
                cid = article["citation_id"]
                lines.append(f"{cid}. {article.get('title', '')}")
                background = []
                if article.get("authors"):
                    background.append("作者: " + "、".join(article.get("authors", [])[:8]))
                if article.get("journal"):
                    background.append("期刊: " + article.get("journal", ""))
                if article.get("publication_year"):
                    background.append("发表时间: " + article.get("publication_year", ""))
                if article.get("source_condition"):
                    background.append("病种目录: " + article.get("source_condition", ""))
                if article.get("treatment_category"):
                    background.append("治疗类别: " + article.get("treatment_category", ""))
                if background:
                    lines.append("；".join(background))
                lines.append(f"文件路径: {article.get('relative_path', '')}")
                if article.get("reasons"):
                    lines.append(f"命中线索: {'; '.join(article['reasons'])}")
                if article.get("keywords"):
                    lines.append("关键词: " + "、".join(article.get("keywords", [])[:12]))
                if article.get("abstract"):
                    lines.append(f"摘要: {_shorten(article.get('abstract', ''), 900)}")
                if article.get("conclusion"):
                    lines.append(f"结论: {_shorten(article.get('conclusion', ''), 700)}")
                evaluation = article.get("evaluation_data") or []
                if evaluation:
                    lines.append("评估数据摘录:")
                    for item in evaluation[:8]:
                        lines.append(f"- {_shorten(item.get('evidence') or item.get('value') or '', 180)} [{cid}]")
                entities = article.get("entities", [])
                if entities:
                    entity_bits = [
                        f"{ent.get('text')}({ent.get('type')})"
                        for ent in entities[:18]
                        if ent.get("text")
                    ]
                    if entity_bits:
                        lines.append("现代文献实体: " + "、".join(entity_bits))
                triplets = article.get("triplets", [])
                if triplets:
                    lines.append("现代文献关系:")
                    for tri in triplets[:14]:
                        evidence = tri.get("evidence") or ""
                        evidence_part = f"；证据: {_shorten(evidence, 120)}" if evidence else ""
                        lines.append(
                            f"- {tri.get('head_text')}({tri.get('head_type')}) "
                            f"--{tri.get('relation')}--> "
                            f"{tri.get('tail_text')}({tri.get('tail_type')}){evidence_part} [{cid}]"
                        )
                lines.append("")
        return "\n".join(lines).strip()

    def generate_answer_stream(
        self,
        query: str,
        retrieval: Dict[str, Any],
        extracted_info: Dict[str, Any],
        language: str = "zh-Hans",
        general_context: str = "",
        answer_mode: str = "fast",
        conversation_context: str = "",
    ) -> Iterator[str]:
        language = normalize_language(language)
        answer_mode = "deep" if answer_mode == "deep" else "fast"
        context = self._format_knowledge_for_llm(retrieval)
        opening = OPENING_SENTENCES[language]
        yield opening + "\n\n"

        language_name = LANGUAGE_NAMES[language]
        system_prompt = f"""
你是 OpenTCM，一个严谨、审慎、可溯源的中医古籍与现代文献 GraphRAG 智能助手。
请使用{language_name}回答。你面向医学专家，可以使用专业术语，但要结构清晰。
不要提及“融合两份答案”“第一份回答”“第二份回答”等内部流程。
不要输出隐藏思维链；只给出面向专家的分析过程、证据和结论。
可以使用你自身通用参数记忆中的中医基础知识作结构、解释和临床常识补充；但凡来自古籍知识图谱或现代文献检索上下文的具体信息，必须保留出处编号。
如果有上文摘要，必须先解析当前问题中的指代，再回答当前追问；不要把无关历史内容混入结论。
请使用 Markdown 三级标题组织主要小节，例如“### 核心结论”；尽量使用短段落和要点，避免整段堆叠。
如果用户问题涉及现代病名、检验指标、影像检查、药理机制或西医治疗，请单列“现代检查/药理与中西医对照”，说明现代医学信息如何辅助而不是替代中医辨证。
对不典型症状或信息不足的临床问题，应先提出可验证的证候/病机假设，再列出需要补充确认的病史、体征、检查或用药信息。
"""
        if answer_mode == "fast":
            user_prompt = f"""
开头句已经由系统显示，请不要重复开头句，直接继续正式回答。

【最近对话摘要】
{conversation_context or "无"}

【用户问题】
{query}

【意图识别】
意图: {extracted_info.get('intent', '未知')}
关键词: {', '.join(extracted_info.get('keywords', []))}
链式检索方向: {extracted_info.get('chain_focus', '') or '根据问题在疾病、症状、证候、治法、方剂、药材之间扩展'}

【GraphRAG 精简检索上下文】
{context}

【回答要求】
1. 用{language_name}给出简洁、重点清晰、专业、可溯源回答，优先回答用户核心问题。
2. 默认使用 Markdown 三级标题输出以下小节：核心结论、古籍知识图谱证据、现代文献证据、英文研究证据、中医辨证与方药分析、现代检查/药理与中西医对照、可继续追问的问题。
3. “核心结论”控制在 3-5 条；如果证据不足，明确说明直接证据有限。
4. “英文研究证据”只在检索到英文现代文献或英文标题/摘要/关键词时列出；没有则简短写明未检出高相关英文文献。
5. 区分直接检索证据、推断性关联、需要临床确认的信息。
6. 每条来自检索上下文的具体知识点必须带 [S1] 或 [M1] 等出处编号；不要编造不存在的出处。
7. 如涉及现代检查、药理、指南式治疗或西医病名，请用通用医学知识做审慎解释，并标明哪些属于检索证据、哪些属于通用知识框架。
8. 对不典型症状，先列可能证候/病机，再列需要补充确认的信息。
9. 可用通用中医知识补足结构，但不要伪装成检索出处。
10. 不要自行添加临床安全提醒；系统会在最终回答后固定追加安全提醒。
"""
            max_tokens = 2600
        else:
            user_prompt = f"""
开头句已经由系统显示，请不要重复开头句，直接继续正式回答。

【最近对话摘要】
{conversation_context or "无"}

【用户问题】
{query}

【意图识别】
意图: {extracted_info.get('intent', '未知')}
关键词: {', '.join(extracted_info.get('keywords', []))}
链式检索方向: {extracted_info.get('chain_focus', '') or '根据问题在疾病、症状、证候、治法、方剂、药材之间扩展'}

【GraphRAG 检索上下文】
{context}

【大语言模型通用知识补充框架】
{general_context.strip() or "请基于自身通用参数记忆，补充必要的中医理论背景、辨证框架、常见治则、临床注意事项；不要编造古籍出处。"}

【回答要求】
1. 回答要比只列检索结果更完整，但归纳要简洁：先给出 3-5 条核心结论，再分层展开。
2. 以 GraphRAG 检索到的三元组和原文为核心证据，并结合原文补足三元组遗漏的信息。
3. 同时整合现代临床文献的摘要、结论、关键词、评估数据和关系网络；现代文献证据使用 [M1] 这类编号，古籍证据使用 [S1] 这类编号。
4. 如果问题是症状的诊断和治疗，请按“症状/体征 -> 可能证候或疾病 -> 治法 -> 方剂/药材/穴位”的链条组织。
5. 如果问题是中药或方剂的功效，请反向检索并说明其可能关联的疾病、症状、证候和治法。
6. 单列“英文研究证据”：只纳入检索到的英文现代文献、英文标题、英文摘要或英文关键词；没有则说明未检出高相关英文文献。
7. 单列“现代检查/药理与中西医对照”：结合检验、影像、病理、药理或西医病名解释其与中医辨证、病机和治法的关系；如果上下文没有直接数据，应说明这是通用知识框架。
8. 对不典型症状，应先给出可能相关证候/病机的审慎假设，再说明需要临床确认哪些信息。
9. 对研究进展类问题，概括现代文献中的干预措施、对照方式、结局指标、疗效方向和证据局限。
10. 可以使用大语言模型自身的通用中医知识补充解释、临床框架和常识，但不要把通用知识伪装成古籍或现代文献出处。
11. 每一条来自检索上下文的具体知识点，句末必须带对应出处标记，例如 [S1] 或 [M1]。不要编造不存在的出处编号。
12. 若证据不足，请明确指出“古籍或现代文献检索证据有限”，再给出审慎解释。
13. 不要自行添加临床安全提醒；系统会在最终回答后固定追加安全提醒。
"""
            max_tokens = 5200
        yield from self.llm.stream(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.15,
            max_tokens=max_tokens,
        )

    # Backward-compatible wrappers for older callers.
    def generate_graphrag_response_stream(self, query: str, context_for_llm: str, intent: str) -> Iterator[str]:
        retrieval = {"documents": [], "paths": [], "sources": []}
        prompt = f"请基于以下中医知识库上下文回答问题，并保留来源标注。\n\n{context_for_llm}\n\n问题：{query}"
        yield from self.llm.stream([{"role": "user", "content": prompt}], temperature=0.1, max_tokens=1800)

    def get_general_deepseek_response_stream(self, query: str, temperature: float = 0.5, max_tokens: int = 2048) -> Iterator[str]:
        prompt = f"请以严谨中医专业语言回答：{query}"
        yield from self.llm.stream([{"role": "user", "content": prompt}], temperature=temperature, max_tokens=max_tokens)

    def generate_general_context_stream(self, query: str, language: str = "zh-Hans") -> Iterator[str]:
        language = normalize_language(language)
        language_name = LANGUAGE_NAMES[language]
        prompt = f"""
请使用{language_name}，为下面的中医问题生成一份“通用知识补充框架”。
要求：
1. 面向医学专家，内容要专业、系统，但只作为最终回答的参考材料。
2. 从中医基础理论、常见辨证路径、治则治法、方药思路、禁忌与安全点展开。
3. 不要声称来自某本古籍，不要编造出处编号，不要写最终临床结论。

问题：{query}
"""
        yield from self.llm.stream(
            [{"role": "user", "content": prompt}],
            temperature=0.35,
            max_tokens=1500,
        )

    def synthesize_responses(self, query: str, graphrag_response: str, general_response: str, temperature: float = 0.3, max_tokens: int = 3000) -> Iterator[str]:
        prompt = f"""
请将下面材料整理为一个单一、可溯源、专业的中医回答。不要提及材料来源的内部分类。
问题：{query}
材料一：{graphrag_response}
材料二：{general_response}
"""
        yield from self.llm.stream([{"role": "user", "content": prompt}], temperature=temperature, max_tokens=max_tokens)


class TCMGraphRAGApp:
    def __init__(
        self,
        db_path: str = DEFAULT_KG_DB,
        csv_path: Optional[str] = None,
        modern_db_path: Optional[str] = DEFAULT_MODERN_DB,
    ) -> None:
        if csv_path and not db_path:
            db_path = csv_path
        if not DEEPSEEK_API_KEY:
            print("警告: 未设置 DEEPSEEK_API_KEY，DeepSeek API 调用将失败。")
        print(f"正在加载 OpenTCM SQLite 知识库: {db_path}")
        self.kb = SQLiteKnowledgeBase(db_path)
        self.modern_kb = None
        if modern_db_path and os.path.exists(modern_db_path):
            print(f"正在加载 OpenTCM 现代文献库: {modern_db_path}")
            self.modern_kb = SQLiteModernLiteratureBase(modern_db_path)
        else:
            print(f"提示: 未找到现代文献库 {modern_db_path}，将仅使用古籍知识图谱。")
        self.rag = GraphRAG(self.kb, self.modern_kb)
        print("OpenTCM GraphRAG 检索与问答系统初始化完成。")

    def run_interactive_cli(self) -> None:
        print("=" * 80)
        print("OpenTCM GraphRAG CLI (输入 exit 退出)")
        print("=" * 80)
        while True:
            query = input("\n请输入问题: ").strip()
            if query.lower() in {"exit", "quit", "退出"}:
                break
            start = time.time()
            extracted = self.rag.extract_keywords_and_intent(query)
            retrieval = self.rag.retrieve_relevant_knowledge(query, extracted)
            print(f"意图: {extracted.get('intent')}")
            print(f"关键词: {extracted.get('keywords')}")
            print(f"检索出处数: {len(retrieval.get('documents', []))}")
            print("-" * 80)
            for chunk in self.rag.generate_answer_stream(query, retrieval, extracted):
                print(chunk, end="", flush=True)
            print(f"\n\n用时 {time.time() - start:.2f}s")


def main() -> None:
    app = TCMGraphRAGApp(DEFAULT_KG_DB, modern_db_path=DEFAULT_MODERN_DB)
    app.run_interactive_cli()


if __name__ == "__main__":
    main()
