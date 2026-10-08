import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_cors import CORS

from GraphRAG import TCMGraphRAGApp, normalize_language


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY")
CORS(app)

OPENTCM_PASSWORD = os.getenv("OPENTCM_PASSWORD")
for name, value in (("FLASK_SECRET_KEY", app.secret_key), ("OPENTCM_PASSWORD", OPENTCM_PASSWORD)):
    if not value or value.startswith("replace-with-"):
        raise RuntimeError(f"Set your own {name} in the local .env before starting OpenTCM.")
GUIDE_DIR = os.path.join(app.root_path, "ultilization_guide")
CONVERSATION_DB = os.getenv(
    "OPENTCM_CONVERSATION_DB",
    os.path.join(PROJECT_ROOT, "data", "opentcm_conversations.sqlite"),
)

LANG_TEXT: Dict[str, Dict[str, str]] = {
    "zh-Hans": {
        "thinking": "深度思考中...",
        "standard_title": "标准回答",
        "deep_title": "深度思考",
        "fast_pending": "即将生成快速回答",
        "step_intent": "理解问题意图与检索词",
        "step_retrieval": "进行知识图谱链式检索",
        "step_general": "补充通用中医知识框架",
        "step_plan": "整理回答思路",
        "final_title": "中医智能助手回答",
        "fast_mode_note": "标准回答模式：跳过额外通用知识扩展，优先使用高相关检索证据和模型已有知识生成精简回答。",
        "no_core": "服务核心组件尚未初始化，请检查知识库索引和 API 配置。",
        "empty_query": "查询内容不能为空。",
        "error": "处理请求时发生内部错误",
        "sources_found": "已检索出 {count} 个最相关出处。",
        "source_line": "{idx}. 《{book}》：{path}",
        "modern_source_line": "{idx}. 现代文献：{title}（{year}，{journal}）",
        "path_prefix": "链式线索",
        "plan": "将结合古籍三元组关系、段落原文、现代文献摘要/结论/评估数据和模型通用中医知识回答；检索证据会保留出处编号，通用知识只作为解释框架。",
    },
    "zh-Hant": {
        "thinking": "深度思考中...",
        "standard_title": "標準回答",
        "deep_title": "深度思考",
        "fast_pending": "即將生成快速回答",
        "step_intent": "理解問題意圖與檢索詞",
        "step_retrieval": "進行知識圖譜鏈式檢索",
        "step_general": "補充通用中醫知識框架",
        "step_plan": "整理回答思路",
        "final_title": "中醫智能助手回答",
        "fast_mode_note": "標準回答模式：跳過額外通用知識擴展，優先使用高相關檢索證據和模型既有知識生成精簡回答。",
        "no_core": "服務核心組件尚未初始化，請檢查知識庫索引和 API 配置。",
        "empty_query": "查詢內容不能為空。",
        "error": "處理請求時發生內部錯誤",
        "sources_found": "已檢索出 {count} 個最相關出處。",
        "source_line": "{idx}. 《{book}》：{path}",
        "modern_source_line": "{idx}. 現代文獻：{title}（{year}，{journal}）",
        "path_prefix": "鏈式線索",
        "plan": "將結合古籍三元組關係、段落原文、現代文獻摘要/結論/評估資料和模型通用中醫知識回答；檢索證據會保留出處編號，通用知識只作為解釋框架。",
    },
    "en": {
        "thinking": "Deep reasoning...",
        "standard_title": "Standard Answer",
        "deep_title": "Deep Reasoning",
        "fast_pending": "Preparing a standard answer",
        "step_intent": "Understanding intent and retrieval terms",
        "step_retrieval": "Running chained knowledge-graph retrieval",
        "step_general": "Adding a general TCM knowledge frame",
        "step_plan": "Structuring the answer",
        "final_title": "TCM AI Assistant Answer",
        "fast_mode_note": "Standard mode: skipping the extra general-context expansion and using the highest-relevance evidence plus the model's existing knowledge for a concise answer.",
        "no_core": "The core service is not initialized. Please check the KG index and API configuration.",
        "empty_query": "Query cannot be empty.",
        "error": "An internal error occurred while processing the request",
        "sources_found": "Retrieved the {count} most relevant sources.",
        "source_line": "{idx}. {book}: {path}",
        "modern_source_line": "{idx}. Modern literature: {title} ({year}, {journal})",
        "path_prefix": "Chained clue",
        "plan": "The answer will combine classical graph triples, original passages, modern literature abstracts/conclusions/evaluation data, and the model's general TCM knowledge. Retrieved evidence will keep source IDs; general knowledge is used only as an explanatory frame.",
    },
}

GUIDE_FILES = {
    "legal": "opentcm_legal_multilang.html",
    "user_zh-Hans": "简体中文_OpenTCM操作指南.html",
    "user_zh-Hant": "繁體中文_OpenTCM 操作指南.html",
    "user_en": "English__OpenTCM User Guide.html",
}

SAFETY_TEXT = {
    "zh-Hans": "\n\n**临床安全提醒：** 本回答内容基于大型语言模型构建的中医古籍知识图谱数据库、现代中医临床文献数据库及大模型的通用参数知识记忆库，仅供中医药文献研究与专家参考，具体诊疗须由合格医师结合病史、四诊、检查和用药禁忌判断。",
    "zh-Hant": "\n\n**臨床安全提醒：** 本回答內容基於大型語言模型構建的中醫古籍知識圖譜資料庫、現代中醫臨床文獻資料庫及大模型的通用參數知識記憶庫，僅供中醫藥文獻研究與專家參考，具體診療須由合格醫師結合病史、四診、檢查和用藥禁忌判斷。",
    "en": "\n\n**Clinical safety note:** This answer is based on a TCM classical-text knowledge-graph database, a modern TCM clinical-literature database constructed with large language models, and the model's general parametric knowledge memory. It is for TCM literature research and expert reference only; clinical decisions must be made by qualified clinicians with full history, examination, diagnostic findings, and medication contraindications.",
}

RED_FLAG_TERMS = [
    "剧烈疼痛",
    "劇烈疼痛",
    "大量出血",
    "陰道出血",
    "阴道出血",
    "妊娠",
    "怀孕",
    "懷孕",
    "孕期",
    "宫外孕",
    "宮外孕",
    "异位妊娠",
    "異位妊娠",
    "高热",
    "高熱",
    "发热不退",
    "發熱不退",
    "晕厥",
    "暈厥",
    "昏厥",
    "胸痛",
    "呼吸困难",
    "呼吸困難",
    "意识障碍",
    "意識障礙",
    "偏瘫",
    "偏癱",
    "失语",
    "失語",
    "抽搐",
    "黑便",
    "呕血",
    "嘔血",
    "急腹症",
    "severe pain",
    "heavy bleeding",
    "vaginal bleeding",
    "pregnan",
    "ectopic",
    "high fever",
    "syncope",
    "faint",
    "chest pain",
    "shortness of breath",
    "confusion",
    "stroke",
    "seizure",
    "emergency",
]

RED_FLAG_TEXT = {
    "zh-Hans": "\n\n**严重风险提醒：** 当前问题可能涉及需要优先排除的临床风险。若出现突发剧烈疼痛、持续高热、大量出血、妊娠期腹痛或阴道出血、晕厥、胸痛、呼吸困难、意识障碍、偏瘫失语、抽搐、黑便或呕血等情况，应立即线下就医或急诊处理；OpenTCM 的回答不能替代急诊分诊和临床处置。",
    "zh-Hant": "\n\n**嚴重風險提醒：** 當前問題可能涉及需要優先排除的臨床風險。若出現突發劇烈疼痛、持續高熱、大量出血、妊娠期腹痛或陰道出血、暈厥、胸痛、呼吸困難、意識障礙、偏癱失語、抽搐、黑便或嘔血等情況，應立即線下就醫或急診處理；OpenTCM 的回答不能替代急診分診和臨床處置。",
    "en": "\n\n**Serious-risk reminder:** This question may involve clinical risks that should be ruled out first. Sudden severe pain, persistent high fever, heavy bleeding, abdominal pain or vaginal bleeding during pregnancy, syncope, chest pain, shortness of breath, altered consciousness, stroke-like symptoms, seizure, melena, or hematemesis requires urgent in-person or emergency care; OpenTCM cannot replace emergency triage or clinical management.",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def shorten_text(text: Any, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def detect_clinical_red_flags(query: str, lang: str) -> str:
    haystack = str(query or "").lower()
    if any(term.lower() in haystack for term in RED_FLAG_TERMS):
        return RED_FLAG_TEXT[normalize_language(lang)]
    return ""


class ConversationStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    lang TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id)")

    def create(self, lang: str, title: str = "") -> Dict[str, str]:
        conversation_id = uuid.uuid4().hex
        now = utc_now()
        title = title or {
            "zh-Hans": "新的对话",
            "zh-Hant": "新的對話",
            "en": "New chat",
        }.get(lang, "新的对话")
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations(id, title, lang, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, title, lang, now, now),
            )
        return {"id": conversation_id, "title": title, "lang": lang, "created_at": now, "updated_at": now}

    def exists(self, conversation_id: str) -> bool:
        if not conversation_id:
            return False
        with self.lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return bool(row)

    def ensure(self, conversation_id: Optional[str], lang: str, first_query: str = "") -> Dict[str, str]:
        if conversation_id and self.exists(conversation_id):
            self.touch(conversation_id, lang=lang)
            item = self.get(conversation_id)
            if item:
                return item
        return self.create(lang, shorten_text(first_query, 36) if first_query else "")

    def touch(self, conversation_id: str, lang: Optional[str] = None, title: Optional[str] = None) -> None:
        now = utc_now()
        assignments = ["updated_at = ?"]
        values: List[Any] = [now]
        if lang:
            assignments.append("lang = ?")
            values.append(lang)
        if title:
            assignments.append("title = ?")
            values.append(title)
        values.append(conversation_id)
        with self.lock, self._connect() as conn:
            conn.execute(f"UPDATE conversations SET {', '.join(assignments)} WHERE id = ?", values)

    def maybe_title_from_first_message(self, conversation_id: str, query: str) -> None:
        with self.lock, self._connect() as conn:
            row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND role = 'user'",
                (conversation_id,),
            ).fetchone()["n"]
        if row and count <= 1 and row["title"] in {"新的对话", "新的對話", "New chat"}:
            self.touch(conversation_id, title=shorten_text(query, 36))

    def get(self, conversation_id: str) -> Optional[Dict[str, str]]:
        with self.lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, lang, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 80) -> List[Dict[str, Any]]:
        with self.lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.title, c.lang, c.created_at, c.updated_at,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
                FROM conversations c
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        sources: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = utc_now()
        with self.lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages(conversation_id, role, content, sources_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    role,
                    content,
                    json.dumps(sources or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    def get_messages(self, conversation_id: str, limit: int = 120) -> List[Dict[str, Any]]:
        with self.lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, sources_json, metadata_json, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        messages = []
        for row in rows:
            item = dict(row)
            try:
                item["sources"] = json.loads(item.pop("sources_json") or "[]")
            except Exception:
                item["sources"] = []
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            messages.append(item)
        return messages

    def recent_context(self, conversation_id: str, rounds: int = 6) -> str:
        messages = self.get_messages(conversation_id, limit=rounds * 2 + 2)
        if not messages:
            return ""
        recent = messages[-(rounds * 2):]
        lines = []
        for item in recent:
            label = "用户" if item["role"] == "user" else "OpenTCM"
            lines.append(f"{label}: {shorten_text(item.get('content', ''), 260)}")
        return "\n".join(lines)

    def delete(self, conversation_id: str) -> bool:
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cursor.rowcount > 0


conversation_store = ConversationStore(CONVERSATION_DB)


def generate_followup_suggestions(query: str, extracted: Dict[str, Any], language: str) -> List[str]:
    keywords = [item for item in extracted.get("keywords", []) if item]
    subject = keywords[0] if keywords else shorten_text(query, 18)
    lang = normalize_language(language)
    if lang == "en":
        return [
            f"What classical terms or aliases may correspond to {subject}?",
            f"What are the main TCM patterns and treatment principles for {subject}?",
            f"What modern clinical evidence is available for {subject}?",
            f"What safety concerns or red flags should clinicians consider?",
        ]
    if lang == "zh-Hant":
        return [
            f"{subject}在古籍中可能對應哪些病名或異名？",
            f"{subject}常見證候、病機和治法如何整理？",
            f"{subject}有哪些現代臨床研究證據？",
            "需要注意哪些嚴重症狀或用藥禁忌？",
        ]
    return [
        f"{subject}在古籍中可能对应哪些病名或异名？",
        f"{subject}常见证候、病机和治法如何整理？",
        f"{subject}有哪些现代临床研究证据？",
        "需要注意哪些严重症状或用药禁忌？",
    ]


tcm_app_instance = None


def initialize_rag() -> None:
    global tcm_app_instance
    db_path = os.getenv("OPENTCM_KG_DB", os.path.join("data", "opentcm_kg.sqlite"))
    modern_db_path = os.getenv("OPENTCM_MODERN_DB", os.path.join("data", "opentcm_modern.sqlite"))
    try:
        logger.info("正在初始化 OpenTCM GraphRAG，SQLite 知识库: %s；现代文献库: %s", db_path, modern_db_path)
        tcm_app_instance = TCMGraphRAGApp(db_path=db_path, modern_db_path=modern_db_path)
        logger.info("OpenTCM GraphRAG 初始化成功。")
    except Exception as exc:
        logger.error("OpenTCM GraphRAG 初始化失败: %s", exc, exc_info=True)
        tcm_app_instance = None


initialize_rag()


def is_authenticated() -> bool:
    return bool(session.get("opentcm_authenticated"))


@app.before_request
def require_login():
    endpoint = request.endpoint or ""
    if endpoint in {"login", "static", "serve_image"}:
        return None
    if endpoint.startswith("static"):
        return None
    if not is_authenticated():
        return redirect(url_for("login", lang=normalize_language(request.args.get("lang"))))
    return None


def sse(data: Dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_paths_for_thinking(retrieval: Dict, lang: str) -> str:
    text = LANG_TEXT[lang]
    lines = []
    for index, path in enumerate(retrieval.get("paths", [])[:3], start=1):
        parts = []
        for triple in path.get("triples", []):
            parts.append(
                f"{triple.get('head_text')} --{triple.get('relation')}--> {triple.get('tail_text')}"
            )
        if parts:
            citations = " ".join(f"[{cid}]" for cid in path.get("citation_ids", []))
            lines.append(f"{text['path_prefix']} {index}: {'；'.join(parts)} {citations}".strip())
    return "\n".join(lines)


def normalize_answer_mode(value: str) -> str:
    return "deep" if str(value or "").strip().lower() in {"deep", "tcm_deep", "tcm-depth"} else "fast"


def generate_step_by_step_streaming_response(
    query: str,
    language: str,
    answer_mode: str = "fast",
    conversation_id: Optional[str] = None,
):
    lang = normalize_language(language)
    mode = normalize_answer_mode(answer_mode)
    is_fast = mode == "fast"
    text = LANG_TEXT[lang]
    conversation = conversation_store.ensure(conversation_id, lang, first_query=query)
    conversation_id = conversation["id"]
    context_summary = conversation_store.recent_context(conversation_id, rounds=6)
    final_answer_parts: List[str] = []
    extracted: Dict[str, Any] = {}
    retrieval: Dict[str, Any] = {"sources": []}
    red_flag_text = detect_clinical_red_flags(query, lang)

    try:
        yield sse({"type": "conversation", "conversation_id": conversation_id, "title": conversation["title"]})
        conversation_store.add_message(
            conversation_id,
            "user",
            query,
            metadata={"answer_mode": mode, "lang": lang},
        )
        conversation_store.maybe_title_from_first_message(conversation_id, query)

        if not tcm_app_instance:
            yield sse({"type": "error", "content": text["no_core"]})
            return

        rag = tcm_app_instance.rag
        contextual_query = rag.expand_followup_query(query, context_summary)
        if is_fast:
            yield sse({"type": "standard_status", "content": text["fast_pending"]})
            extracted = rag.extract_keywords_and_intent(
                contextual_query,
                language=lang,
                fast=True,
                conversation_context=context_summary,
            )
            retrieval = rag.retrieve_relevant_knowledge(contextual_query, extracted, limit=5)
            yield sse({"type": "sources", "sources": retrieval.get("sources", [])})

            yield sse({"type": "final_answer_start", "title": text["final_title"]})
            for chunk in rag.generate_answer_stream(
                query,
                retrieval,
                extracted,
                language=lang,
                general_context="",
                answer_mode=mode,
                conversation_context=context_summary,
            ):
                final_answer_parts.append(chunk)
                yield sse({"type": "final_answer_content", "content": chunk})
            if red_flag_text:
                final_answer_parts.append(red_flag_text)
                yield sse({"type": "final_answer_content", "content": red_flag_text})
            final_answer_parts.append(SAFETY_TEXT[lang])
            yield sse({"type": "final_answer_content", "content": SAFETY_TEXT[lang]})
            followups = generate_followup_suggestions(query, extracted, lang)
            yield sse({"type": "followups", "questions": followups})
            conversation_store.add_message(
                conversation_id,
                "assistant",
                "".join(final_answer_parts),
                sources=retrieval.get("sources", []),
                metadata={"extracted": extracted, "followups": followups, "answer_mode": mode, "lang": lang},
            )
            yield sse({"type": "final_end"})
            return

        yield sse({"type": "thinking_start", "title": text["deep_title"]})

        yield sse({"type": "thinking_step", "step": 1, "title": text["step_intent"]})
        extracted = rag.extract_keywords_and_intent(
            contextual_query,
            language=lang,
            fast=is_fast,
            conversation_context=context_summary,
        )
        scope_labels = {
            "zh-Hans": {
                "classical": "古籍知识图谱",
                "modern": "现代文献数据库",
                "both": "古籍知识图谱 + 现代文献数据库",
            },
            "zh-Hant": {
                "classical": "古籍知識圖譜",
                "modern": "現代文獻資料庫",
                "both": "古籍知識圖譜 + 現代文獻資料庫",
            },
            "en": {
                "classical": "classical knowledge graph",
                "modern": "modern literature database",
                "both": "classical KG + modern literature",
            },
        }
        mapped_terms = extracted.get("semantic_mappings", [])
        intent_lines = [
            f"Intent: {extracted.get('intent', '')}",
            f"Scope: {scope_labels.get(lang, scope_labels['zh-Hans']).get(extracted.get('source_scope', 'both'), extracted.get('source_scope', 'both'))}",
            f"Keywords: {', '.join(extracted.get('keywords', []))}",
        ]
        if mapped_terms:
            intent_lines.append(f"Semantic mapping: {', '.join(mapped_terms[:12])}")
        if extracted.get("category_filter"):
            intent_lines.append(f"Category filter: {extracted.get('category_filter')}")
        if context_summary:
            intent_lines.append(f"Context: 已结合最近对话摘要进行追问解析")
        intent_lines.append(f"Chain: {extracted.get('chain_focus', '') or 'auto'}")
        intent_content = (
            "\n".join(intent_lines)
        )
        yield sse({"type": "thinking_content", "step": 1, "content": intent_content})

        yield sse({"type": "thinking_step", "step": 2, "title": text["step_retrieval"]})
        retrieval = rag.retrieve_relevant_knowledge(contextual_query, extracted, limit=5 if is_fast else 10)
        yield sse({"type": "sources", "sources": retrieval.get("sources", [])})

        source_lines = [text["sources_found"].format(count=len(retrieval.get("sources", [])))]
        for source in retrieval.get("sources", [])[:20]:
            if source.get("source_type") == "modern":
                source_lines.append(
                    text["modern_source_line"].format(
                        idx=f"[{source.get('id', '')}]",
                        title=source.get("title") or source.get("book_title", ""),
                        year=source.get("publication_year") or "年份不详",
                        journal=source.get("journal") or "期刊不详",
                    )
                )
            else:
                source_lines.append(
                    text["source_line"].format(
                        idx=f"[{source.get('id', '')}]",
                        book=source.get("book_title", ""),
                        path=source.get("relative_path", ""),
                    )
                )
        path_summary = format_paths_for_thinking(retrieval, lang)
        if path_summary:
            source_lines.append("\n" + path_summary)
        yield sse({"type": "thinking_content", "step": 2, "content": "\n".join(source_lines)})

        general_chunks = []
        yield sse({"type": "thinking_step", "step": 3, "title": text["step_general"]})
        if is_fast:
            yield sse({"type": "thinking_content", "step": 3, "content": text["fast_mode_note"]})
        else:
            for chunk in rag.generate_general_context_stream(contextual_query, language=lang):
                general_chunks.append(chunk)
                yield sse({"type": "thinking_content", "step": 3, "content": chunk})
        general_context = "".join(general_chunks)

        yield sse({"type": "thinking_step", "step": 4, "title": text["step_plan"]})
        yield sse({"type": "thinking_content", "step": 4, "content": text["plan"]})
        yield sse({"type": "thinking_end"})

        yield sse({"type": "final_answer_start", "title": text["final_title"]})
        for chunk in rag.generate_answer_stream(
            query,
            retrieval,
            extracted,
            language=lang,
            general_context=general_context,
            answer_mode=mode,
            conversation_context=context_summary,
        ):
            final_answer_parts.append(chunk)
            yield sse({"type": "final_answer_content", "content": chunk})
        if red_flag_text:
            final_answer_parts.append(red_flag_text)
            yield sse({"type": "final_answer_content", "content": red_flag_text})
        final_answer_parts.append(SAFETY_TEXT[lang])
        yield sse({"type": "final_answer_content", "content": SAFETY_TEXT[lang]})
        followups = generate_followup_suggestions(query, extracted, lang)
        yield sse({"type": "followups", "questions": followups})
        conversation_store.add_message(
            conversation_id,
            "assistant",
            "".join(final_answer_parts),
            sources=retrieval.get("sources", []),
            metadata={"extracted": extracted, "followups": followups, "answer_mode": mode, "lang": lang},
        )
        yield sse({"type": "final_end"})

    except Exception as exc:
        logger.error("流式响应生成错误: %s", exc, exc_info=True)
        yield sse({"type": "error", "content": f"{text['error']}: {exc}"})


@app.route("/login", methods=["GET", "POST"])
def login():
    lang = normalize_language(request.values.get("lang"))
    error = False
    if request.method == "POST":
        password = request.form.get("password", "")
        lang = normalize_language(request.form.get("lang"))
        if password == OPENTCM_PASSWORD:
            session["opentcm_authenticated"] = True
            session["opentcm_language"] = lang
            return redirect(url_for("welcome", lang=lang))
        error = True
    return render_template("login.html", lang=lang, error=error)


@app.route("/logout")
def logout():
    lang = normalize_language(request.args.get("lang") or session.get("opentcm_language"))
    session.clear()
    return redirect(url_for("login", lang=lang))


@app.route("/")
def welcome():
    lang = normalize_language(request.args.get("lang") or session.get("opentcm_language"))
    session["opentcm_language"] = lang
    return render_template("welcome.html", lang=lang)


@app.route("/chat_page")
def chat_ui_page():
    lang = normalize_language(request.args.get("lang") or session.get("opentcm_language"))
    session["opentcm_language"] = lang
    return render_template("chat.html", lang=lang)


@app.route("/api/conversations", methods=["GET", "POST"])
def conversations_api():
    lang = normalize_language(request.values.get("lang") or session.get("opentcm_language"))
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        conversation = conversation_store.create(lang, shorten_text(payload.get("title") or "", 36))
        return jsonify({"conversation": conversation})
    return jsonify({"conversations": conversation_store.list()})


@app.route("/api/conversations/<conversation_id>", methods=["GET", "DELETE"])
def conversation_detail_api(conversation_id: str):
    if request.method == "DELETE":
        return jsonify({"deleted": conversation_store.delete(conversation_id)})
    conversation = conversation_store.get(conversation_id)
    if not conversation:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"conversation": conversation, "messages": conversation_store.get_messages(conversation_id)})


@app.route("/api/chat", methods=["GET"])
def handle_chat_api():
    if not tcm_app_instance:
        lang = normalize_language(request.args.get("lang"))
        return Response(
            (sse({"type": "error", "content": LANG_TEXT[lang]["no_core"]}) for _ in range(1)),
            mimetype="text/event-stream",
        )

    user_query = request.args.get("query", "").strip()
    lang = normalize_language(request.args.get("lang") or session.get("opentcm_language"))
    answer_mode = normalize_answer_mode(request.args.get("mode", "fast"))
    conversation_id = request.args.get("conversation_id", "").strip() or None
    if not user_query:
        return Response(
            (sse({"type": "error", "content": LANG_TEXT[lang]["empty_query"]}) for _ in range(1)),
            mimetype="text/event-stream",
        )

    logger.info("收到用户查询: %s | mode=%s | conversation=%s", user_query, answer_mode, conversation_id)
    return Response(
        generate_step_by_step_streaming_response(
            user_query,
            lang,
            answer_mode=answer_mode,
            conversation_id=conversation_id,
        ),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.route("/guide/<guide_type>")
def guide(guide_type: str):
    lang = normalize_language(request.args.get("lang") or session.get("opentcm_language"))
    if guide_type == "legal":
        filename = GUIDE_FILES["legal"]
    elif guide_type == "user":
        filename = GUIDE_FILES.get(f"user_{lang}", GUIDE_FILES["user_zh-Hans"])
    else:
        return redirect(url_for("welcome", lang=lang))
    return send_from_directory(GUIDE_DIR, filename)


@app.route("/static/images/<filename>")
def serve_image(filename):
    return send_from_directory(os.path.join(app.static_folder, "images"), filename)


if __name__ == "__main__":
    logger.info("启动 Flask 开发服务器...")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")), debug=False, threaded=True)
