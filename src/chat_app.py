# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手 · 操作员问答入口（后端）
============================================================================
技术栈：FastAPI（异步）+ SQLite（标准库 sqlite3）+ SSE 流式。
复用阶段1（stage1_prototype.py）核心模块：
  · load_markdown_docs / chunk_text        —— 知识库摄取与 500 字切片
  · EmbeddingProvider / get_embedding_provider —— 嵌入（离线 TF-IDF / FAISS 优先）
  · VectorStore / BM25 / hybrid_retrieve   —— BM25+向量混合检索
  · LLMProvider / get_llm_provider / stream_chat —— LLM 网关（支持 SSE 流式）
  · SYSTEM_TEMPLATE                        —— 提示词模板（强制仅依制度作答+标注来源）

功能：
  1) POST /chat：接收 {session_id?, question}，返回 SSE 流式响应（状态/逐字/引用/完成）
  2) 会话与消息持久化到 SQLite（sessions / messages 表；知识库存 kb_documents/kb_chunks）
  3) 每次请求读取该会话最近 6 轮对话，作为 LLM 上下文传入 RAG 链路
  4) 统一错误码 + 中文错误信息（非 SSE 错误走 JSON，SSE 通道内错误走 error 事件）
  5) FastAPI 直接托管前端单页（static/index.html），同源零跨域
============================================================================
"""

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import stage1_prototype as s1   # ★ 直接复用阶段1 检索/嵌入/LLM 核心模块

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 配置（可用环境变量覆盖；离线默认走 mock LLM + 离线 TF-IDF 嵌入）
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = os.getenv("DOCS_DIR", str(BASE_DIR / "docs"))          # 制度文档目录
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "chat.db"))         # SQLite 文件
STATIC_DIR = str(BASE_DIR / "static")                             # 前端静态目录
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "auto")              # auto/tfidf/sklearn/hash/api

# LLM 配置（与阶段1 一致；mock 无需 key；配置 LLM_API_KEY 后自动启用真实大模型）
LLM_CFG = {
    "llm_provider": os.getenv("LLM_PROVIDER", "mock"),
    "llm_base_url": os.getenv("LLM_BASE_URL", ""),
    "llm_api_key": os.getenv("LLM_API_KEY", ""),
    "llm_model": os.getenv("LLM_MODEL", ""),
    "embed_provider": EMBED_PROVIDER,
    "embed_base_url": os.getenv("EMBED_BASE_URL", ""),
    "embed_api_key": os.getenv("EMBED_API_KEY", ""),
    "embed_model": os.getenv("EMBED_MODEL", ""),
}

TOP_K = int(os.getenv("TOP_K", "3"))          # 混合检索召回条数
HISTORY_ROUNDS = 6                            # 最近 6 轮对话作为上下文
QUESTION_MAX = 2000                           # 问题最大字数

# ---------------------------------------------------------------------------
# 统一错误码表（约束：所有异常场景返回统一错误码 + 中文信息）
# ---------------------------------------------------------------------------
ERRORS = {
    0:     "成功",
    40001: "参数缺失或格式错误",
    40101: "未授权或令牌失效",
    40301: "无权访问该会话",
    40401: "会话不存在",
    41301: "请求体过大",
    42901: "请求过于频繁，请稍后再试",
    50301: "检索服务或大模型暂不可用",
    50002: "知识库尚未初始化，请稍候重试",
    50001: "服务内部错误",
}

KB = {"ready": False, "vs": None, "bm25": None, "chunks": [], "embedder": None}


# ===========================================================================
# SQLite 数据库初始化（沿用阶段1 架构表结构：用户/会话/消息/知识库…）
# ===========================================================================
def init_db() -> None:
    """创建表结构。本期问答只用到 users/sessions/messages/kb_*，其余表预留。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE, email TEXT,
            password_hash TEXT, role TEXT, full_name TEXT, dept TEXT,
            is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
            status TEXT DEFAULT 'active', created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, citations TEXT, created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
        CREATE TABLE IF NOT EXISTS kb_documents (
            id TEXT PRIMARY KEY, title TEXT, category TEXT, source TEXT,
            chunk_count INTEGER, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id TEXT PRIMARY KEY, document_id TEXT, chunk_index INTEGER,
            content TEXT, embedding TEXT, meta TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(document_id);
        CREATE TABLE IF NOT EXISTS consumables (id TEXT PRIMARY KEY, code TEXT, udi TEXT, name TEXT);
        CREATE TABLE IF NOT EXISTS batch_trace_records (id TEXT PRIMARY KEY, consumable_id TEXT, batch_no TEXT);
        CREATE TABLE IF NOT EXISTS requisitions (id TEXT PRIMARY KEY, session_id TEXT);
        CREATE TABLE IF NOT EXISTS work_orders (id TEXT PRIMARY KEY, session_id TEXT);
        CREATE TABLE IF NOT EXISTS audit_logs (id TEXT PRIMARY KEY, action TEXT, detail TEXT, created_at TEXT);
    """)
    conn.commit()
    conn.close()


def db_conn() -> sqlite3.Connection:
    """每次请求新建连接（sqlite3 连接非线程安全，FastAPI 异步下务必短生命周期）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ===========================================================================
# 知识库摄取与索引构建（阶段1 模块复用 + 持久化到 SQLite）
# ===========================================================================
def ingest_kb_if_needed() -> None:
    """若 kb_chunks 为空则从 DOCS_DIR 摄取并写入 SQLite；随后在内存构建 FAISS+BM25。"""
    conn = db_conn()
    cnt = conn.execute("SELECT COUNT(*) AS c FROM kb_chunks").fetchone()["c"]
    conn.close()
    if cnt == 0:
        _ingest_and_store()
    _build_in_memory_index()


def _ingest_and_store() -> None:
    """读取 MD → 500字切片 → 嵌入 → 写 SQLite（kb_documents + kb_chunks）。"""
    docs = s1.load_markdown_docs(DOCS_DIR)
    chunks: List[Dict] = []
    for d in docs:
        for piece in s1.chunk_text(d["text"], size=500, overlap=50):
            chunks.append({"source": d["source"], "text": piece})

    embedder = s1.get_embedding_provider(EMBED_PROVIDER, LLM_CFG)
    # TF-IDF 类需先拟合语料（与阶段1 build_index 一致）
    if isinstance(embedder, (s1.OfflineTfidfEmbeddingProvider, s1.SklearnTfidfEmbeddingProvider)):
        embedder.fit([c["text"] for c in chunks])
    embeddings = embedder.encode([c["text"] for c in chunks])

    conn = db_conn()
    cur = conn.cursor()
    for d in docs:
        cur.execute("INSERT OR IGNORE INTO kb_documents(id,title,category,source,chunk_count,status,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), d["source"], "general", d["source"], 0, "indexed", _now()))
    # 按 source 归属 chunk 到对应 document，写入切片与向量
    doc_map = {r["source"]: r["id"] for r in cur.execute("SELECT id,source FROM kb_documents").fetchall()}
    for c, emb in zip(chunks, embeddings):
        did = doc_map.get(c["source"])
        cur.execute("INSERT INTO kb_chunks(id,document_id,chunk_index,content,embedding,meta)"
                    " VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), did, 0, c["text"], json.dumps(emb, ensure_ascii=False), "{}"))
    conn.commit()
    conn.close()
    print(f"[知识库] 已摄取并写入 SQLite：{len(chunks)} 个文档块")


def _build_in_memory_index() -> None:
    """从 SQLite 读回切片与向量，构建内存 FAISS/回退索引 + BM25（与阶段1 一致）。"""
    conn = db_conn()
    # 关联 kb_documents 取回真实文件名作为引用来源展示
    rows = conn.execute(
        "SELECT kc.content AS content, kc.embedding AS embedding, kd.source AS source "
        "FROM kb_chunks kc JOIN kb_documents kd ON kc.document_id = kd.id"
    ).fetchall()
    conn.close()
    chunks = [{"source": r["source"], "text": r["content"]} for r in rows]
    embeddings = [json.loads(r["embedding"]) for r in rows]

    embedder = s1.get_embedding_provider(EMBED_PROVIDER, LLM_CFG)
    # 关键：查询时用到的 embedder 必须用「同一语料」拟合，否则 TF-IDF 词表/维度与入库向量不一致
    if isinstance(embedder, (s1.OfflineTfidfEmbeddingProvider, s1.SklearnTfidfEmbeddingProvider)):
        embedder.fit([c["text"] for c in chunks])
    vs = s1.VectorStore()
    vs.build(embeddings, chunks)
    bm25 = s1.BM25([s1._tokenize(c["text"]) for c in chunks])
    KB.update({"ready": True, "vs": vs, "bm25": bm25, "chunks": chunks, "embedder": embedder})
    print(f"[知识库] 内存索引构建完成，文档块={len(chunks)}，FAISS={s1._FAISS_AVAILABLE}")


# ===========================================================================
# 会话 / 消息 持久化辅助
# ===========================================================================
def get_or_create_session(session_id: Optional[str]) -> str:
    """session_id 省略则自动建新会话；提供但不存在则报 40401；存在则复用。"""
    conn = db_conn()
    if not session_id:
        new_id = str(uuid.uuid4())
        conn.execute("INSERT INTO sessions(id,user_id,title,status,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?)", (new_id, "operator", "新会话", "active", _now(), _now()))
        conn.commit()
        conn.close()
        return new_id
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=_err(40401))
    return session_id


def save_message(session_id: str, role: str, content: str, citations: Optional[List[Dict]] = None) -> str:
    mid = str(uuid.uuid4())
    conn = db_conn()
    conn.execute("INSERT INTO messages(id,session_id,role,content,citations,created_at)"
                 " VALUES(?,?,?,?,?,?)",
                 (mid, session_id, role, content, json.dumps(citations or [], ensure_ascii=False), _now()))
    conn.commit()
    conn.close()
    return mid


def load_history(session_id: str, rounds: int = HISTORY_ROUNDS) -> List[Dict]:
    """读取该会话最近 N 轮(user+assistant)对话，按时间升序返回。"""
    conn = db_conn()
    rows = conn.execute(
        "SELECT role,content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
        "ORDER BY created_at DESC LIMIT ?", (session_id, rounds * 2)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ===========================================================================
# 提示词组装：阶段1 SYSTEM_TEMPLATE + 追加「最近 6 轮历史」
# ===========================================================================
def build_messages_with_history(query: str, contexts: List[Dict], history: List[Dict]) -> List[Dict]:
    context_blob = "\n\n".join(
        f"【来源: {c['source']}】\n{c['text']}" for c in contexts
    ) or "（未检索到相关制度片段）"
    sys_content = s1.SYSTEM_TEMPLATE.format(context=context_blob)
    msgs: List[Dict] = [{"role": "system", "content": sys_content}]
    for h in history:                                  # ★ 复用阶段1 上下文思路，追加历史
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": query})
    return msgs


# ===========================================================================
# 统一错误响应
# ===========================================================================
def _err(code: int, detail: Optional[str] = None) -> Dict:
    return {"code": code, "message": ERRORS.get(code, "未知错误"), "detail": detail, "trace_id": str(uuid.uuid4())}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# 限流（简单固定窗口，单机演示用；超限返回 42901）
# ===========================================================================
_RATE: Dict[str, List[float]] = {}
RATE_LIMIT = 20          # 每窗口允许请求数
RATE_WINDOW = 60.0       # 窗口秒数


def _rate_ok(key: str) -> bool:
    now = time.time()
    hits = _RATE.get(key, [])
    hits = [t for t in hits if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT:
        _RATE[key] = hits
        return False
    hits.append(now)
    _RATE[key] = hits
    return True


# ===========================================================================
# FastAPI 应用
# ===========================================================================
app = FastAPI(title="医用耗材批次追溯智能助手 · 操作员问答入口", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    ingest_kb_if_needed()


# 自定义异常处理器：把 HTTPException / 全局异常统一成 {code,message,detail,trace_id}
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    # FastAPI 默认 404/422 等也走这里；我们仅对主动抛出的 _err 形态转换
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code,
                        content=_err(50001 if exc.status_code >= 500 else 40001, str(detail)))


@app.exception_handler(Exception)
async def global_exc_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=_err(50001, str(exc)))


# Pydantic 参数校验失败（422）统一归为 40001 参数错误
@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=_err(40001, "请求参数校验失败"))


# ---------------------------------------------------------------------------
# 请求体模型
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, description="省略则自动建新会话")
    question: str = Field(..., min_length=1, max_length=QUESTION_MAX, description="操作员提问")


# ---------------------------------------------------------------------------
# SSE 辅助：把事件名+数据格式化为 SSE 文本块
# ---------------------------------------------------------------------------
def _sse(event: str, data: Dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 核心接口：POST /chat（SSE 流式）
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    # 1) 限流
    client = request.client.host if request.client else "local"
    if not _rate_ok(client):
        return JSONResponse(status_code=429, content=_err(42901))

    # 2) 知识库就绪检查（启动建库可能仍在进行）
    if not KB["ready"]:
        return JSONResponse(status_code=503, content=_err(50002))

    # 3) 会话处理（省略→自动建；提供但不存在→40401）
    try:
        session_id = get_or_create_session(req.session_id)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code,
                            content=e.detail if isinstance(e.detail, dict) else _err(40401))

    # 4) 参数二次兜底校验
    if not req.question or not req.question.strip():
        return JSONResponse(status_code=400, content=_err(40001, "问题内容不能为空"))

    # 5) 组装 SSE 流（生成逻辑抽离为 chat_stream，便于测试与复用）
    return StreamingResponse(chat_stream(session_id, req.question), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# SSE 生成器（可被路由调用，也可被测试直接驱动）
# ---------------------------------------------------------------------------
def chat_stream(session_id: str, question: str):
    """端到端：存用户消息 → 读历史 → 混合检索 → 流式生成 → 落库 → 发完成事件。"""
    assistant_text = ""
    sources_payload = []
    try:
        # 写入用户消息
        save_message(session_id, "user", question)

        # 读取最近 6 轮历史
        history = load_history(session_id, HISTORY_ROUNDS)

        # 状态提示①：检索中
        yield _sse("status", {"code": 0, "message": "正在检索耗材法规、院内 SOP 文档…"})

        # 混合检索（复用阶段1）
        contexts = s1.hybrid_retrieve(question, KB["vs"], KB["bm25"], KB["embedder"], top_k=TOP_K)
        for c in contexts:
            sources_payload.append({
                "doc": c["source"], "snippet": c["text"][:120], "score": c.get("score", 0)
            })

        # 状态提示②：生成中
        yield _sse("status", {"code": 0, "message": "正在生成回答…"})

        # 组装提示词（系统+历史+当前问题）
        messages = build_messages_with_history(question, contexts, history)

        # 取得 LLM provider（复用阶段1 工厂；mock 或真实供应商）
        provider = s1.get_llm_provider(LLM_CFG["llm_provider"], LLM_CFG)

        # 流式生成（逐 token → typing 效果）
        for delta in provider.stream_chat(messages):
            assistant_text += delta
            yield _sse("token", {"delta": delta})

        # 引用来源事件
        yield _sse("sources", {"sources": sources_payload})

        # 落库助手消息（含引用）
        save_message(session_id, "assistant", assistant_text,
                     [{"doc": s["doc"], "snippet": s["snippet"], "score": s["score"]}
                      for s in sources_payload])

        # 完成事件
        yield _sse("done", {"session_id": session_id, "finish_reason": "stop"})
    except Exception as e:  # 通道内异常：以 error 事件下发，保持 SSE 不断开
        yield _sse("error", _err(50301 if "LLM" in str(e) or "embed" in str(e).lower() else 50001, str(e)))


# ---------------------------------------------------------------------------
# 辅助接口
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"code": 0, "message": "ok", "kb_ready": KB["ready"],
            "faiss": s1._FAISS_AVAILABLE, "knowledge_chunks": len(KB["chunks"])}


@app.post("/sessions")
async def create_session():
    sid = get_or_create_session(None)
    return {"code": 0, "message": "ok", "session_id": sid}


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    conn = db_conn()
    rows = conn.execute(
        "SELECT role,content,citations,created_at FROM messages WHERE session_id=? ORDER BY created_at",
        (session_id,)).fetchall()
    conn.close()
    return {"code": 0, "message": "ok",
            "messages": [{"role": r["role"], "content": r["content"],
                          "citations": json.loads(r["citations"] or "[]"),
                          "created_at": r["created_at"]} for r in rows]}


# ---------------------------------------------------------------------------
# 前端单页托管（FastAPI 直接 serve，同源零跨域）
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    html_path = Path(STATIC_DIR) / "index.html"
    if not html_path.exists():
        return JSONResponse(status_code=404, content=_err(40401, "前端页面缺失"))
    return FileResponse(str(html_path))


if not os.path.isdir(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# 本地直接运行：uvicorn chat_app:app --reload
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
