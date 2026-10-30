# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手 · 后端（操作员问答 + 管理员后台，统一应用）
============================================================================
本文件在 chat_app.py 基础上扩展，新增：
  ★ 模块 A 认证与 RBAC：JWT 签发/校验、密码哈希、get_current_user / require_admin
  ★ 模块 B 数据库与种子：扩展表结构（prompt_templates / operation_logs /
        kb_documents 补字段 / messages 补风险字段 / sessions 补风险字段），
        启动时种子 操作员+管理员 账号、默认 system 提示词模板
  ★ 模块 C 知识库管理：PDF(PyMuPDF)/Markdown 上传 → 自动切片 → 批量向量化入库
        → 列表/预览/删除/重建索引（复用 stage1 的 VectorStore/BM25/嵌入）
  ★ 模块 D 提示词模板：DB 持久化 CRUD、生效切换、RAG 实时读取"生效中"模板
  ★ 模块 E 对话审计与质控：按操作员/时间/关键词/风险检索、标记风险、导出 CSV
  ★ 模块 F 页面渲染：Jinja2 服务端渲染管理后台（SaaS 简约风）
  ★ 操作员 /chat：同步接入 JWT（决策②），会话归属到具体用户，便于审计溯源

离线可运行策略（与阶段1一致）：
  · JWT 用 HMAC+base64 自实现（HS256 等效），无需 python-jose；
  · 密码哈希优先 passlib[bcrypt]，缺失时退回 pbkdf2_sha256；
  · PDF 依赖 PyMuPDF(fitz)，缺失时仅 PDF 上传报错、Markdown 不受影响；
  · 向量/FAISS/嵌入 复用 stage1 的"装了即用、离线回退"机制。
============================================================================
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import stage1_prototype as s1   # ★ 复用阶段1 检索/嵌入/LLM 核心模块

from fastapi import FastAPI, HTTPException, Request, Depends, Header, Cookie, \
    UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse, \
    HTMLResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ===========================================================================
# 配置（可用环境变量覆盖）
# ===========================================================================
BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = os.getenv("DOCS_DIR", str(BASE_DIR / "docs"))              # 制度文档目录
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "admin.db"))            # SQLite 文件
STATIC_DIR = str(BASE_DIR / "static")                                 # 前端静态目录
TEMPLATE_DIR = str(BASE_DIR / "templates")                            # 服务端渲染模板目录
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "auto")                  # auto/tfidf/sklearn/api
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")          # 生产务必改掉
JWT_EXPIRE_H = int(os.getenv("JWT_EXPIRE_H", "8"))                    # 令牌有效期(小时)

# LLM 配置（与阶段1/ chat_app 一致）
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

# 知识库文档分类（上传时由管理员选择）
KB_CATEGORIES = ["regulation", "sop", "trace", "expiry", "adverse_event", "general"]
KB_CATEGORY_LABELS = {
    "regulation": "医疗器械监管法规",
    "sop": "院内耗材 SOP",
    "trace": "批次/效期追溯规范",
    "expiry": "效期管理要求",
    "adverse_event": "不良事件上报规范",
    "general": "其他",
}

# 风险类型（对话审计标记用）
RISK_TYPES = ["台账错报", "批次缺失", "流程不合规", "其他"]

# ---------------------------------------------------------------------------
# 统一错误码表（复用 chat_app 规范，并补鉴权类）
# ---------------------------------------------------------------------------
ERRORS = {
    0:     "成功",
    40001: "参数缺失或格式错误",
    40101: "未授权或令牌失效",
    40301: "权限不足（需要管理员角色）",
    40401: "资源不存在",
    41301: "请求体过大",
    42901: "请求过于频繁，请稍后再试",
    50301: "检索服务或大模型暂不可用",
    50002: "知识库尚未初始化，请稍候重试",
    50001: "服务内部错误",
}

KB = {"ready": False, "vs": None, "bm25": None, "chunks": [], "embedder": None}
TEMPLATES = Jinja2Templates(directory=TEMPLATE_DIR)


# ===========================================================================
# 模块 A：认证与 RBAC（JWT + 密码哈希 + 依赖）
# ===========================================================================
def _b64url(b: bytes) -> str:
    """base64url 编码（去填充），用于 JWT 三段式。"""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """base64url 解码（补填充）。"""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 3))


def create_access_token(sub: str, role: str, expires_h: int = JWT_EXPIRE_H) -> str:
    """
    签发 JWT（HS256 等效，纯 Python 实现，离线可用）。
    payload 含 sub(用户id)、role(角色)、exp(过期)、jti(防重放)。
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": sub, "role": role,
        "exp": int(time.time()) + expires_h * 3600,
        "jti": str(uuid.uuid4()),
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(JWT_SECRET.encode("utf-8"),
                   f"{h}.{p}".encode("utf-8"), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def decode_token(token: str) -> Dict:
    """
    校验并解析 JWT；任何失败（格式错/签名错/过期）一律抛 CredentialsError。
    生产环境建议改用 python-jose，此处自实现仅为离线可跑。
    """
    try:
        h, p, s = token.split(".")
    except ValueError:
        raise CredentialsError("令牌格式错误")
    expected = hmac.new(JWT_SECRET.encode("utf-8"),
                        f"{h}.{p}".encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(expected), s):
        raise CredentialsError("令牌签名校验失败")
    try:
        payload = json.loads(_b64url_decode(p))
    except Exception:
        raise CredentialsError("令牌载荷解析失败")
    if payload.get("exp", 0) < time.time():
        raise CredentialsError("令牌已过期")
    return payload


class CredentialsError(Exception):
    """鉴权失败统一异常（由依赖转换为 40101）。"""


# --- 密码哈希：优先 passlib，缺失退回 pbkdf2_sha256（离线可用）---
def hash_password(pw: str) -> str:
    """对用户密码做单向哈希；格式 `pbkdf2$<salt_hex>$<dk_hex>` 或 passlib 哈希串。"""
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return ctx.hash(pw)
    except Exception:
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 100_000)
        return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    if hashed.startswith("pbkdf2$"):
        _, salt_hex, dk_hex = hashed.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return ctx.verify(pw, hashed)
    except Exception:
        return False


# --- FastAPI 依赖：解析当前用户 ---
def get_current_user(request: Request,
                     authorization: Optional[str] = Header(default=None),
                     token_cookie: Optional[str] = Cookie(default=None)) -> Dict:
    """
    从 Authorization: Bearer <token> 或 Cookie(token=...) 取出并校验 JWT。
    失败统一抛 40101。返回 payload（含 sub/role）。
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif token_cookie:
        token = token_cookie
    if not token:
        raise HTTPException(status_code=401, detail=_err(40101, "缺少认证令牌"))
    try:
        return decode_token(token)
    except CredentialsError as e:
        raise HTTPException(status_code=401, detail=_err(40101, str(e)))


def require_admin(payload: Dict = Depends(get_current_user)) -> Dict:
    """接口级 RBAC：仅 admin 角色可过；否则 40301。"""
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail=_err(40301))
    return payload


def require_page_admin(request: Request) -> Dict:
    """页面级守卫：未登录/非 admin 直接 302 跳转到 /login。"""
    token = request.cookies.get("token")
    if not token:
        raise RedirectResponse("/login", status_code=302)
    try:
        payload = decode_token(token)
    except CredentialsError:
        raise RedirectResponse("/login", status_code=302)
    if payload.get("role") != "admin":
        raise RedirectResponse("/login", status_code=302)
    return payload


# ===========================================================================
# 模块 B：数据库与种子
# ===========================================================================
def init_db() -> None:
    """建表 + 迁移扩展字段（兼容已存在的 chat.db 结构）。"""
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
            status TEXT DEFAULT 'active', created_at TEXT, updated_at TEXT,
            risk_flag INTEGER DEFAULT 0, risk_type TEXT, risk_note TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, citations TEXT, created_at TEXT,
            risk_flag INTEGER DEFAULT 0, risk_type TEXT, risk_note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
        CREATE TABLE IF NOT EXISTS kb_documents (
            id TEXT PRIMARY KEY, title TEXT, category TEXT, source TEXT,
            file_type TEXT, original_filename TEXT, chunk_count INTEGER,
            status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id TEXT PRIMARY KEY, document_id TEXT, chunk_index INTEGER,
            content TEXT, embedding TEXT, meta TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(document_id);
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id TEXT PRIMARY KEY, name TEXT, type TEXT, content TEXT,
            is_active INTEGER DEFAULT 0, version INTEGER DEFAULT 1,
            created_by TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS operation_logs (
            id TEXT PRIMARY KEY, user_id TEXT, username TEXT, role TEXT,
            action TEXT, object_type TEXT, object_id TEXT, detail TEXT,
            ip TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS consumables (id TEXT PRIMARY KEY, code TEXT, udi TEXT, name TEXT);
        CREATE TABLE IF NOT EXISTS batch_trace_records (id TEXT PRIMARY KEY, consumable_id TEXT, batch_no TEXT);
        CREATE TABLE IF NOT EXISTS requisitions (id TEXT PRIMARY KEY, session_id TEXT);
        CREATE TABLE IF NOT EXISTS work_orders (id TEXT PRIMARY KEY, session_id TEXT);
    """)
    # ---- 迁移：为兼容旧库补扩展字段（不存在才加）----
    _add_col(cur, "kb_documents", "file_type", "TEXT")
    _add_col(cur, "kb_documents", "original_filename", "TEXT")
    _add_col(cur, "sessions", "risk_flag", "INTEGER DEFAULT 0")
    _add_col(cur, "sessions", "risk_type", "TEXT")
    _add_col(cur, "sessions", "risk_note", "TEXT")
    _add_col(cur, "messages", "risk_flag", "INTEGER DEFAULT 0")
    _add_col(cur, "messages", "risk_type", "TEXT")
    _add_col(cur, "messages", "risk_note", "TEXT")
    conn.commit()
    conn.close()


def _add_col(cur, table: str, col: str, typ: str) -> None:
    """SQLite 安全加列（避免重复加列报错）。"""
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    except sqlite3.OperationalError:
        pass  # 列已存在


def db_conn() -> sqlite3.Connection:
    """短生命周期连接（sqlite3 非线程安全，异步请求内务必即用即关）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed_data() -> None:
    """种子：默认账号（operator/admin）+ 默认 system 提示词模板。"""
    conn = db_conn()
    cur = conn.cursor()
    # 1) 默认账号（仅在无账号时插入）
    if cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
        now = _now()
        users = [
            ("operator", "operator123", "操作员", "operator"),
            ("admin", "Admin@123", "系统管理员", "admin"),
        ]
        for uname, pw, full, role in users:
            cur.execute(
                "INSERT INTO users(id,username,email,password_hash,role,full_name,dept,is_active,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), uname, f"{uname}@hospital.local",
                 hash_password(pw), role, full, "设备物资部", 1, now, now))
    # 2) 默认 system 提示词模板（仅在无生效模板时插入，确保 RAG 开箱即用）
    exists = cur.execute(
        "SELECT COUNT(*) AS c FROM prompt_templates WHERE type='system'").fetchone()["c"]
    if exists == 0:
        cur.execute(
            "INSERT INTO prompt_templates(id,name,type,content,is_active,version,created_by,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), "默认系统提示词", "system", DEFAULT_SYSTEM_TEMPLATE,
             1, 1, "system", _now(), _now()))
    conn.commit()
    conn.close()


# 默认 system 提示词模板（来自需求方给定文本，并保留 {context} 占位供 RAG 注入）
DEFAULT_SYSTEM_TEMPLATE = """你是院内医用耗材批次追溯智能助手，回答必须严格基于检索到的法规、管理制度、作业 SOP 原文；检索不到对应内容时，直接回复"现有制度文档中无该项内容"；每条回答末尾标注引用文档名称与片段，不得虚构追溯标准、效期管理规则与业务流程。

【检索到的制度文档片段】
{context}"""


def write_op_log(user_id, username, role, action, object_type,
                 object_id, detail, ip) -> None:
    """
    管理员操作审计留痕：所有写操作（上传/删除/重建/模板编辑/风险标记）成功后调用。
    仅追加、不可篡改，满足医械追溯审计要求。
    """
    conn = db_conn()
    conn.execute(
        "INSERT INTO operation_logs(id,user_id,username,role,action,object_type,object_id,detail,ip,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, username, role, action, object_type,
         str(object_id), json.dumps(detail, ensure_ascii=False), ip, _now()))
    conn.commit()
    conn.close()


# ===========================================================================
# 模块 C：知识库管理（上传/切片/向量化/列表/删除/重建）
# ===========================================================================
def extract_pdf_text(path: str) -> str:
    """用 PyMuPDF(fitz) 抽取 PDF 全文；缺失库则明确报错（决策③）。"""
    try:
        import fitz
    except Exception:
        raise RuntimeError("未安装 PyMuPDF(fitz)，无法解析 PDF，请先 pip install pymupdf")
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def extract_markdown_text(path: str) -> str:
    """读取 Markdown 原文（复用阶段1 思路，单文件版）。"""
    return Path(path).read_text(encoding="utf-8")


def kb_upload_and_index(filename: str, raw_bytes: bytes, category: str) -> Dict:
    """
    上传入口：识别类型 → 抽文本 → 500字切片 → 写 kb_documents/kb_chunks → 刷新向量库。
    返回文档元信息。category 由管理员在页面选择。
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "pdf":
        tmp = str(BASE_DIR / f".tmp_{uuid.uuid4()}.pdf")
        Path(tmp).write_bytes(raw_bytes)
        try:
            text = extract_pdf_text(tmp)
        finally:
            Path(tmp).unlink(missing_ok=True)
        file_type = "pdf"
    elif ext in ("md", "markdown", "txt"):
        text = raw_bytes.decode("utf-8", errors="ignore")
        file_type = "markdown"
    else:
        raise ValueError("仅支持 PDF / Markdown(.md/.txt) 文件")

    # 切片（复用阶段1 500字、重叠50）
    pieces = s1.chunk_text(text, size=500, overlap=50)
    if not pieces:
        raise ValueError("文件解析后无有效文本内容")

    # 文档 + 切片入库
    doc_id = str(uuid.uuid4())
    now = _now()
    conn = db_conn()
    conn.execute(
        "INSERT INTO kb_documents(id,title,category,source,file_type,original_filename,chunk_count,status,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (doc_id, filename, category, filename, file_type, filename, len(pieces), "indexed", now))
    for i, piece in enumerate(pieces):
        conn.execute(
            "INSERT INTO kb_chunks(id,document_id,chunk_index,content,embedding,meta)"
            " VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), doc_id, i, piece, json.dumps([], ensure_ascii=False), "{}"))
    conn.commit()
    conn.close()

    _refresh_kb()  # 重新拟合嵌入并构建内存索引（确保 TF-IDF 词表一致）
    return {"id": doc_id, "title": filename, "category": category,
            "chunk_count": len(pieces), "status": "indexed"}


def kb_list(page: int = 1, size: int = 20, category: Optional[str] = None) -> Dict:
    """文档列表（分页 + 分类筛选），含切片数与状态。"""
    conn = db_conn()
    where = "WHERE 1=1"
    args: List = []
    if category:
        where += " AND category=?"
        args.append(category)
    total = conn.execute(f"SELECT COUNT(*) AS c FROM kb_documents {where}", args).fetchone()["c"]
    rows = conn.execute(
        f"SELECT id,title,category,file_type,chunk_count,status,created_at FROM kb_documents {where}"
        f" ORDER BY created_at DESC LIMIT ? OFFSET ?",
        args + [size, (page - 1) * size]).fetchall()
    conn.close()
    return {"total": total, "page": page, "size": size,
            "items": [dict(r) for r in rows]}


def kb_chunks_preview(doc_id: str, limit: int = 5) -> List[Dict]:
    """切片预览：取前 N 段原文（不返回向量，避免体积过大）。"""
    conn = db_conn()
    rows = conn.execute(
        "SELECT chunk_index,content FROM kb_chunks WHERE document_id=? ORDER BY chunk_index LIMIT ?",
        (doc_id, limit)).fetchall()
    conn.close()
    return [{"chunk_index": r["chunk_index"], "content": r["content"]} for r in rows]


def kb_delete(doc_id: str) -> None:
    """删除文档及其全部切片/向量，并刷新内存索引。"""
    conn = db_conn()
    conn.execute("DELETE FROM kb_chunks WHERE document_id=?", (doc_id,))
    conn.execute("DELETE FROM kb_documents WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()
    _refresh_kb()


def kb_reindex(doc_id: str) -> Dict:
    """重建索引：删除该文档旧切片 → 重新抽文本切片（此处简化为用已存切片重编码）→ 刷新。"""
    # 设计说明：真正"重新分段"应从原文件重抽文本；原型阶段按"重新向量化"实现：
    # 删除旧切片后用文档 source 重新读取/抽取并重切。为稳健，这里复用已存 content 重编码。
    conn = db_conn()
    conn.execute("DELETE FROM kb_chunks WHERE document_id=?", (doc_id,))
    row = conn.execute("SELECT source,title,category FROM kb_documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        raise ValueError("文档不存在")
    # 重新抽取原文件文本并重切（PDF 需原文件，演示环境用 source 名定位 ./docs 下文件）
    src = Path(DOCS_DIR) / row["source"]
    if src.exists():
        if src.suffix.lower() == ".pdf":
            text = extract_pdf_text(str(src))
        else:
            text = extract_markdown_text(str(src))
        pieces = s1.chunk_text(text, size=500, overlap=50)
        conn = db_conn()
        for i, piece in enumerate(pieces):
            conn.execute(
                "INSERT INTO kb_chunks(id,document_id,chunk_index,content,embedding,meta)"
                " VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), doc_id, i, piece, json.dumps([], ensure_ascii=False), "{}"))
        conn.execute("UPDATE kb_documents SET chunk_count=?,status='indexed' WHERE id=?",
                     (len(pieces), doc_id))
        conn.commit()
        conn.close()
    _refresh_kb()
    return {"id": doc_id, "status": "indexed"}


def _refresh_kb() -> None:
    """
    统一刷新：读全部切片 → 拟合并编码嵌入（写回 SQLite）→ 重建内存 FAISS/BM25 索引。
    TF-IDF 类必须在「全量语料」上拟合，否则新文档向量空间与旧不一致，检索失效。
    """
    conn = db_conn()
    rows = conn.execute("SELECT id,content FROM kb_chunks").fetchall()
    conn.close()
    texts = [r["content"] for r in rows]
    embedder = s1.get_embedding_provider(EMBED_PROVIDER, LLM_CFG)
    if isinstance(embedder, (s1.OfflineTfidfEmbeddingProvider, s1.SklearnTfidfEmbeddingProvider)):
        embedder.fit(texts)  # 全量拟合
    embs = embedder.encode(texts) if texts else []
    conn = db_conn()
    for r, e in zip(rows, embs):
        conn.execute("UPDATE kb_chunks SET embedding=? WHERE id=?",
                     (json.dumps(e, ensure_ascii=False), r["id"]))
    conn.commit()
    conn.close()

    # 重建内存索引（含真实文件名 source）
    conn = db_conn()
    rows = conn.execute(
        "SELECT kc.content AS content, kc.embedding AS embedding, kd.source AS source "
        "FROM kb_chunks kc JOIN kb_documents kd ON kc.document_id=kd.id").fetchall()
    conn.close()
    chunks = [{"source": r["source"], "text": r["content"]} for r in rows]
    embeddings = [json.loads(r["embedding"]) for r in rows]
    emb2 = s1.get_embedding_provider(EMBED_PROVIDER, LLM_CFG)
    if isinstance(emb2, (s1.OfflineTfidfEmbeddingProvider, s1.SklearnTfidfEmbeddingProvider)):
        emb2.fit([c["text"] for c in chunks])
    vs = s1.VectorStore()
    vs.build(embeddings, chunks)
    bm25 = s1.BM25([s1._tokenize(c["text"]) for c in chunks])
    KB.update({"ready": True, "vs": vs, "bm25": bm25, "chunks": chunks, "embedder": emb2})
    print(f"[知识库] 刷新完成，文档块={len(chunks)}，FAISS={s1._FAISS_AVAILABLE}")


def ingest_kb_if_needed() -> None:
    """首次启动若库空，则从 DOCS_DIR 摄取样例/真实制度文档。"""
    conn = db_conn()
    cnt = conn.execute("SELECT COUNT(*) AS c FROM kb_chunks").fetchone()["c"]
    conn.close()
    if cnt == 0:
        for f in Path(DOCS_DIR).glob("*"):
            if f.suffix.lower() in (".md", ".txt", ".markdown"):
                try:
                    kb_upload_and_index(f.name, f.read_bytes(), "general")
                except Exception as e:
                    print(f"[知识库] 摄取 {f.name} 失败：{e}")
    else:
        _refresh_kb()


# ===========================================================================
# 模块 D：提示词模板（DB 持久化 + 生效切换 + RAG 实时读取）
# ===========================================================================
def prompt_list() -> List[Dict]:
    conn = db_conn()
    rows = conn.execute(
        "SELECT id,name,type,content,is_active,version,updated_at FROM prompt_templates ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def prompt_get(pid: str) -> Dict:
    conn = db_conn()
    r = conn.execute(
        "SELECT id,name,type,content,is_active,version,updated_at FROM prompt_templates WHERE id=?",
        (pid,)).fetchone()
    conn.close()
    if not r:
        raise ValueError("模板不存在")
    return dict(r)


def prompt_create(name: str, ptype: str, content: str, created_by: str) -> str:
    pid = str(uuid.uuid4())
    conn = db_conn()
    conn.execute(
        "INSERT INTO prompt_templates(id,name,type,content,is_active,version,created_by,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, name, ptype, content, 0, 1, created_by, _now(), _now()))
    conn.commit()
    conn.close()
    return pid


def prompt_update(pid: str, name: str, content: str) -> None:
    conn = db_conn()
    conn.execute(
        "UPDATE prompt_templates SET name=?,content=?,updated_at=? WHERE id=?",
        (name, content, _now(), pid))
    conn.commit()
    conn.close()


def prompt_activate(pid: str) -> None:
    """
    设为"生效中"：同类型模板仅一个生效——先把同类型全部置 0，再置本模板为 1。
    RAG 链读取 is_active=1 的 system 模板，故修改即时生效、无需重启。
    """
    conn = db_conn()
    ptype = conn.execute("SELECT type FROM prompt_templates WHERE id=?", (pid,)).fetchone()
    if not ptype:
        conn.close()
        raise ValueError("模板不存在")
    conn.execute("UPDATE prompt_templates SET is_active=0 WHERE type=?", (ptype["type"],))
    conn.execute("UPDATE prompt_templates SET is_active=1,updated_at=? WHERE id=?", (_now(), pid))
    conn.commit()
    conn.close()


def get_active_system_template() -> str:
    """RAG 链读取的当前生效 system 模板；无则返回阶段1 默认。"""
    conn = db_conn()
    r = conn.execute(
        "SELECT content FROM prompt_templates WHERE type='system' AND is_active=1 "
        "ORDER BY updated_at DESC LIMIT 1").fetchone()
    conn.close()
    return r["content"] if r else s1.SYSTEM_TEMPLATE


# ===========================================================================
# 模块 E：对话审计与质控（检索 / 标记 / 导出 CSV）
# ===========================================================================
def audit_search(operator: Optional[str] = None, start: Optional[str] = None,
                 end: Optional[str] = None, keyword: Optional[str] = None,
                 risk: Optional[str] = None, page: int = 1, size: int = 20) -> Dict:
    """
    按 操作员 / 时间范围 / 关键词 / 风险标记 检索会话列表（分页）。
    会话级风险标记取自 sessions.risk_flag。
    """
    where = "WHERE 1=1"
    args: List = []
    if operator:
        where += " AND u.username LIKE ?"
        args.append(f"%{operator}%")
    if start:
        where += " AND s.created_at >= ?"
        args.append(start)
    if end:
        where += " AND s.created_at <= ?"
        args.append(end)
    if keyword:
        # 关键词匹配：会话内任意消息含关键词
        where += (" AND s.id IN (SELECT DISTINCT session_id FROM messages "
                  "WHERE content LIKE ?)")
        args.append(f"%{keyword}%")
    if risk == "1":
        where += " AND s.risk_flag=1"
    elif risk == "0":
        where += " AND s.risk_flag=0"

    conn = db_conn()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM sessions s LEFT JOIN users u ON s.user_id=u.id {where}",
        args).fetchone()["c"]
    rows = conn.execute(
        f"""SELECT s.id, s.title, s.created_at, s.risk_flag, s.risk_type,
                   u.username AS operator,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count,
                   (SELECT content FROM messages m WHERE m.session_id=s.id AND m.role='user'
                    ORDER BY created_at ASC LIMIT 1) AS first_question
            FROM sessions s LEFT JOIN users u ON s.user_id=u.id {where}
            ORDER BY s.created_at DESC LIMIT ? OFFSET ?""",
        args + [size, (page - 1) * size]).fetchall()
    conn.close()
    return {"total": total, "page": page, "size": size,
            "items": [dict(r) for r in rows]}


def audit_detail(session_id: str) -> Dict:
    """会话详情：全部消息（含引用与风险字段）+ 会话级风险标记。"""
    conn = db_conn()
    sess = conn.execute(
        "SELECT s.id,s.title,s.created_at,s.risk_flag,s.risk_type,s.risk_note,"
        "u.username AS operator FROM sessions s LEFT JOIN users u ON s.user_id=u.id "
        "WHERE s.id=?", (session_id,)).fetchone()
    if not sess:
        conn.close()
        raise ValueError("会话不存在")
    msgs = conn.execute(
        "SELECT role,content,citations,created_at,risk_flag,risk_type FROM messages "
        "WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
    conn.close()
    return {"session": dict(sess),
            "messages": [{"role": r["role"], "content": r["content"],
                          "citations": json.loads(r["citations"] or "[]"),
                          "created_at": r["created_at"],
                          "risk_flag": r["risk_flag"], "risk_type": r["risk_type"]}
                         for r in msgs]}


def audit_flag(session_id: str, risk_type: str, note: str) -> None:
    """标记会话为风险对话（会话级 + 其全部消息级同步），用于质控追踪。"""
    conn = db_conn()
    conn.execute("UPDATE sessions SET risk_flag=1,risk_type=?,risk_note=? WHERE id=?",
                 (risk_type, note, session_id))
    conn.execute("UPDATE messages SET risk_flag=1,risk_type=? WHERE session_id=?",
                 (risk_type, session_id))
    conn.commit()
    conn.close()


def audit_unflag(session_id: str) -> None:
    """取消风险标记。"""
    conn = db_conn()
    conn.execute("UPDATE sessions SET risk_flag=0,risk_type=NULL,risk_note=NULL WHERE id=?",
                 (session_id,))
    conn.execute("UPDATE messages SET risk_flag=0,risk_type=NULL WHERE session_id=?",
                 (session_id,))
    conn.commit()
    conn.close()


def audit_export_csv(operator=None, start=None, end=None, keyword=None,
                     risk=None) -> str:
    """
    导出筛选结果 CSV（决策①选 CSV，零依赖，Excel/WPS 可直接打开）。
    返回 CSV 文本字符串；调用方用 StreamingResponse 下发为附件。
    """
    # 复用 audit_search 但忽略分页，拉全量（size 设大）
    data = audit_search(operator, start, end, keyword, risk, page=1, size=100000)["items"]
    conn = db_conn()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["会话ID", "操作员", "创建时间", "首条问题", "消息数",
                    "风险标记", "风险类型", "会话标题"])
    for it in data:
        writer.writerow([it["id"], it["operator"], it["created_at"],
                         (it.get("first_question") or "")[:200],
                         it["msg_count"], "是" if it["risk_flag"] else "否",
                         it.get("risk_type") or "", it["title"]])
    conn.close()
    return out.getvalue()


def op_log_list(operator=None, action=None, page=1, size=50) -> Dict:
    """操作日志列表（审计留痕查询）。"""
    where = "WHERE 1=1"
    args: List = []
    if operator:
        where += " AND username LIKE ?"
        args.append(f"%{operator}%")
    if action:
        where += " AND action=?"
        args.append(action)
    conn = db_conn()
    total = conn.execute(f"SELECT COUNT(*) AS c FROM operation_logs {where}", args).fetchone()["c"]
    rows = conn.execute(
        f"SELECT id,username,role,action,object_type,object_id,detail,ip,created_at "
        f"FROM operation_logs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        args + [size, (page - 1) * size]).fetchall()
    conn.close()
    return {"total": total, "page": page, "size": size,
            "items": [dict(r) for r in rows]}


# ===========================================================================
# 统一工具
# ===========================================================================
def _err(code: int, detail: Optional[str] = None) -> Dict:
    return {"code": code, "message": ERRORS.get(code, "未知错误"),
            "detail": detail, "trace_id": str(uuid.uuid4())}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# 模块 F：FastAPI 应用与路由
# ===========================================================================
app = FastAPI(title="医用耗材批次追溯智能助手", version="0.2.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    seed_data()
    ingest_kb_if_needed()


# --- 异常处理器（统一错误码）---
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code,
                        content=_err(50001 if exc.status_code >= 500 else 40001, str(detail)))


@app.exception_handler(Exception)
async def global_exc_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=_err(50001, str(exc)))


# ===========================================================================
# F.1 认证接口
# ===========================================================================
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, description="用户名")
    password: str = Field(..., min_length=1, description="密码")


class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, description="省略则自动建新会话")
    question: str = Field(..., min_length=1, max_length=QUESTION_MAX, description="操作员提问")


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    """登录：校验密码 → 签发 JWT。操作员与管理员共用，靠 role 区分权限。"""
    conn = db_conn()
    row = conn.execute("SELECT id,password_hash,role,is_active FROM users WHERE username=?",
                       (req.username,)).fetchone()
    conn.close()
    if not row or not verify_password(req.password, row["password_hash"]):
        return JSONResponse(status_code=401, content=_err(40101, "用户名或密码错误"))
    if not row["is_active"]:
        return JSONResponse(status_code=403, content=_err(40301, "账号已停用"))
    token = create_access_token(row["id"], row["role"])
    return {"code": 0, "message": "ok",
            "data": {"access_token": token, "role": row["role"],
                     "token_type": "Bearer"}}


@app.get("/api/auth/me")
async def auth_me(payload: Dict = Depends(get_current_user)):
    return {"code": 0, "message": "ok", "data": payload}


# ===========================================================================
# F.2 操作员问答 /chat（决策②：同步接入 JWT）
# ===========================================================================
_RATE: Dict[str, List[float]] = {}
RATE_LIMIT = 20
RATE_WINDOW = 60.0


def _rate_ok(key: str) -> bool:
    now = time.time()
    hits = [t for t in _RATE.get(key, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_LIMIT:
        _RATE[key] = hits
        return False
    hits.append(now)
    _RATE[key] = hits
    return True


def _sse(event: str, data: Dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_messages_with_history(query: str, contexts: List[Dict], history: List[Dict]) -> List[Dict]:
    """拼装提示词：当前生效 system 模板（含 {context}）+ 历史 + 当前问题。"""
    context_blob = "\n\n".join(
        f"【来源: {c['source']}】\n{c['text']}" for c in contexts
    ) or "（未检索到相关制度片段）"
    tpl = get_active_system_template()
    try:
        sys_content = tpl.format(context=context_blob)
    except (KeyError, ValueError):
        sys_content = tpl + "\n\n【检索到的制度文档片段】\n" + context_blob
    msgs: List[Dict] = [{"role": "system", "content": sys_content}]
    for h in history:
        msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": query})
    return msgs


def get_or_create_session_for_user(session_id: Optional[str], user_id: str) -> str:
    """会话归属到当前登录用户（审计溯源需要）。"""
    conn = db_conn()
    if not session_id:
        new_id = str(uuid.uuid4())
        conn.execute("INSERT INTO sessions(id,user_id,title,status,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?)", (new_id, user_id, "新会话", "active", _now(), _now()))
        conn.commit()
        conn.close()
        return new_id
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=_err(40401))
    return session_id


def save_message(session_id, role, content, citations=None) -> str:
    mid = str(uuid.uuid4())
    conn = db_conn()
    conn.execute("INSERT INTO messages(id,session_id,role,content,citations,created_at)"
                 " VALUES(?,?,?,?,?,?)",
                 (mid, session_id, role, content,
                  json.dumps(citations or [], ensure_ascii=False), _now()))
    conn.commit()
    conn.close()
    return mid


def load_history(session_id, rounds=HISTORY_ROUNDS) -> List[Dict]:
    conn = db_conn()
    rows = conn.execute(
        "SELECT role,content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
        "ORDER BY created_at DESC LIMIT ?", (session_id, rounds * 2)).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def chat_stream(session_id: str, question: str):
    """端到端 SSE：存用户消息 → 读历史 → 混合检索 → 流式生成 → 落库。"""
    assistant_text = ""
    sources_payload = []
    try:
        save_message(session_id, "user", question)
        history = load_history(session_id, HISTORY_ROUNDS)
        yield _sse("status", {"code": 0, "message": "正在检索耗材法规、院内 SOP 文档…"})
        contexts = s1.hybrid_retrieve(question, KB["vs"], KB["bm25"], KB["embedder"], top_k=TOP_K)
        for c in contexts:
            sources_payload.append({"doc": c["source"], "snippet": c["text"][:120],
                                    "score": c.get("score", 0)})
        yield _sse("status", {"code": 0, "message": "正在生成回答…"})
        messages = build_messages_with_history(question, contexts, history)
        provider = s1.get_llm_provider(LLM_CFG["llm_provider"], LLM_CFG)
        for delta in provider.stream_chat(messages):
            assistant_text += delta
            yield _sse("token", {"delta": delta})
        yield _sse("sources", {"sources": sources_payload})
        save_message(session_id, "assistant", assistant_text,
                     [{"doc": s["doc"], "snippet": s["snippet"], "score": s["score"]}
                      for s in sources_payload])
        yield _sse("done", {"session_id": session_id, "finish_reason": "stop"})
    except Exception as e:
        yield _sse("error", _err(50301 if ("LLM" in str(e) or "embed" in str(e).lower()) else 50001, str(e)))


@app.post("/chat")
async def chat(req: ChatRequest, request: Request,
               payload: Dict = Depends(get_current_user)):
    # 限流
    client = request.client.host if request.client else "local"
    if not _rate_ok(client):
        return JSONResponse(status_code=429, content=_err(42901))
    if not KB["ready"]:
        return JSONResponse(status_code=503, content=_err(50002))
    try:
        session_id = get_or_create_session_for_user(req.session_id, payload["sub"])
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code,
                            content=e.detail if isinstance(e.detail, dict) else _err(40401))
    if not req.question or not req.question.strip():
        return JSONResponse(status_code=400, content=_err(40001, "问题内容不能为空"))
    return StreamingResponse(chat_stream(session_id, req.question),
                            media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ===========================================================================
# F.3 知识库管理接口（admin）
# ===========================================================================
@app.get("/api/kb/documents")
async def api_kb_list(page: int = 1, size: int = 20, category: Optional[str] = None,
                      _: Dict = Depends(require_admin)):
    return {"code": 0, "message": "ok", "data": kb_list(page, size, category)}


@app.post("/api/kb/documents/upload")
async def api_kb_upload(file: UploadFile = File(...),
                        category: str = Form("general"),
                        payload: Dict = Depends(require_admin),
                        request: Request = None):
    raw = await file.read()
    try:
        info = kb_upload_and_index(file.filename, raw, category)
    except ValueError as e:
        return JSONResponse(status_code=400, content=_err(40001, str(e)))
    except RuntimeError as e:
        return JSONResponse(status_code=503, content=_err(50301, str(e)))
    write_op_log(payload["sub"], _uname(payload), "admin", "kb_upload",
                 "kb_document", info["id"], {"title": info["title"],
                 "chunk_count": info["chunk_count"]}, _ip(request))
    return {"code": 0, "message": "ok", "data": info}


@app.get("/api/kb/documents/{doc_id}/chunks")
async def api_kb_chunks(doc_id: str, limit: int = 5,
                        _: Dict = Depends(require_admin)):
    return {"code": 0, "message": "ok", "data": kb_chunks_preview(doc_id, limit)}


@app.delete("/api/kb/documents/{doc_id}")
async def api_kb_delete(doc_id: str, payload: Dict = Depends(require_admin),
                        request: Request = None):
    kb_delete(doc_id)
    write_op_log(payload["sub"], _uname(payload), "admin", "kb_delete",
                 "kb_document", doc_id, {}, _ip(request))
    return {"code": 0, "message": "ok"}


@app.post("/api/kb/documents/{doc_id}/reindex")
async def api_kb_reindex(doc_id: str, payload: Dict = Depends(require_admin),
                         request: Request = None):
    try:
        info = kb_reindex(doc_id)
    except ValueError as e:
        return JSONResponse(status_code=404, content=_err(40401, str(e)))
    write_op_log(payload["sub"], _uname(payload), "admin", "kb_reindex",
                 "kb_document", doc_id, {}, _ip(request))
    return {"code": 0, "message": "ok", "data": info}


# ===========================================================================
# F.4 提示词模板接口（admin）
# ===========================================================================
class PromptCreateReq(BaseModel):
    name: str
    type: str = "system"
    content: str


class PromptUpdateReq(BaseModel):
    name: str
    content: str


@app.get("/api/prompts")
async def api_prompt_list(_: Dict = Depends(require_admin)):
    return {"code": 0, "message": "ok", "data": prompt_list()}


@app.get("/api/prompts/active")
async def api_prompt_active():
    """RAG 问答读取的当前生效 system 模板（无需鉴权，供 /chat 内部/前端预览）。"""
    return {"code": 0, "message": "ok", "data": {"content": get_active_system_template()}}


@app.post("/api/prompts")
async def api_prompt_create(req: PromptCreateReq, payload: Dict = Depends(require_admin),
                            request: Request = None):
    pid = prompt_create(req.name, req.type, req.content, payload["sub"])
    write_op_log(payload["sub"], _uname(payload), "admin", "prompt_create",
                 "prompt_template", pid, {"name": req.name}, _ip(request))
    return {"code": 0, "message": "ok", "data": {"id": pid}}


@app.put("/api/prompts/{pid}")
async def api_prompt_update(pid: str, req: PromptUpdateReq,
                            payload: Dict = Depends(require_admin),
                            request: Request = None):
    try:
        prompt_update(pid, req.name, req.content)
    except ValueError as e:
        return JSONResponse(status_code=404, content=_err(40401, str(e)))
    write_op_log(payload["sub"], _uname(payload), "admin", "prompt_update",
                 "prompt_template", pid, {"name": req.name}, _ip(request))
    return {"code": 0, "message": "ok"}


@app.post("/api/prompts/{pid}/activate")
async def api_prompt_activate(pid: str, payload: Dict = Depends(require_admin),
                             request: Request = None):
    try:
        prompt_activate(pid)
    except ValueError as e:
        return JSONResponse(status_code=404, content=_err(40401, str(e)))
    write_op_log(payload["sub"], _uname(payload), "admin", "prompt_activate",
                 "prompt_template", pid, {}, _ip(request))
    return {"code": 0, "message": "ok"}


# ===========================================================================
# F.5 对话审计接口（admin）
# ===========================================================================
@app.get("/api/audit/conversations")
async def api_audit_search(operator: Optional[str] = None, start: Optional[str] = None,
                           end: Optional[str] = None, keyword: Optional[str] = None,
                           risk: Optional[str] = None, page: int = 1, size: int = 20,
                           _: Dict = Depends(require_admin)):
    return {"code": 0, "message": "ok",
            "data": audit_search(operator, start, end, keyword, risk, page, size)}


@app.get("/api/audit/conversations/{session_id}")
async def api_audit_detail(session_id: str, _: Dict = Depends(require_admin)):
    try:
        return {"code": 0, "message": "ok", "data": audit_detail(session_id)}
    except ValueError as e:
        return JSONResponse(status_code=404, content=_err(40401, str(e)))


class FlagReq(BaseModel):
    risk_type: str
    note: str = ""


@app.post("/api/audit/conversations/{session_id}/flag")
async def api_audit_flag(session_id: str, req: FlagReq,
                         payload: Dict = Depends(require_admin),
                         request: Request = None):
    audit_flag(session_id, req.risk_type, req.note)
    write_op_log(payload["sub"], _uname(payload), "admin", "audit_flag",
                 "conversation", session_id, {"risk_type": req.risk_type}, _ip(request))
    return {"code": 0, "message": "ok"}


@app.delete("/api/audit/conversations/{session_id}/flag")
async def api_audit_unflag(session_id: str, payload: Dict = Depends(require_admin),
                           request: Request = None):
    audit_unflag(session_id)
    write_op_log(payload["sub"], _uname(payload), "admin", "audit_unflag",
                 "conversation", session_id, {}, _ip(request))
    return {"code": 0, "message": "ok"}


@app.get("/api/audit/conversations/export")
async def api_audit_export(operator: Optional[str] = None, start: Optional[str] = None,
                           end: Optional[str] = None, keyword: Optional[str] = None,
                           risk: Optional[str] = None,
                           _: Dict = Depends(require_admin)):
    csv_text = audit_export_csv(operator, start, end, keyword, risk)
    headers = {"Content-Disposition": "attachment; filename=audit_export.csv"}
    return Response(content="\ufeff" + csv_text, media_type="text/csv", headers=headers)


# ===========================================================================
# F.6 操作日志接口（admin）
# ===========================================================================
@app.get("/api/logs")
async def api_logs(operator: Optional[str] = None, action: Optional[str] = None,
                   page: int = 1, size: int = 50, _: Dict = Depends(require_admin)):
    return {"code": 0, "message": "ok", "data": op_log_list(operator, action, page, size)}


# ===========================================================================
# F.7 管理后台页面（Jinja2 服务端渲染，SaaS 简约风）
# ===========================================================================
@app.get("/login")
async def page_login(request: Request):
    return TEMPLATES.TemplateResponse("login.html", {"request": request})


@app.get("/admin")
async def page_admin(request: Request):
    p = require_page_admin(request)
    # 概览统计
    conn = db_conn()
    stats = {
        "docs": conn.execute("SELECT COUNT(*) AS c FROM kb_documents").fetchone()["c"],
        "sessions": conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"],
        "risk": conn.execute("SELECT COUNT(*) AS c FROM sessions WHERE risk_flag=1").fetchone()["c"],
        "logs": conn.execute("SELECT COUNT(*) AS c FROM operation_logs").fetchone()["c"],
    }
    conn.close()
    return TEMPLATES.TemplateResponse("overview.html",
                                     {"request": request, "stats": stats, "username": p.get("sub")})


@app.get("/admin/kb")
async def page_kb(request: Request):
    require_page_admin(request)
    return TEMPLATES.TemplateResponse("kb.html",
                                     {"request": request,
                                      "categories": KB_CATEGORY_LABELS})


@app.get("/admin/prompts")
async def page_prompts(request: Request):
    require_page_admin(request)
    return TEMPLATES.TemplateResponse("prompts.html", {"request": request})


@app.get("/admin/audit")
async def page_audit(request: Request):
    require_page_admin(request)
    return TEMPLATES.TemplateResponse("audit.html",
                                     {"request": request, "risk_types": RISK_TYPES})


@app.get("/admin/audit/{session_id}")
async def page_audit_detail(request: Request, session_id: str):
    require_page_admin(request)
    return TEMPLATES.TemplateResponse("audit_detail.html",
                                     {"request": request, "session_id": session_id,
                                      "risk_types": RISK_TYPES})


@app.get("/admin/logs")
async def page_logs(request: Request):
    require_page_admin(request)
    return TEMPLATES.TemplateResponse("logs.html", {"request": request})


# ===========================================================================
# 辅助：取用户名 / 取客户端 IP（用于审计）
# ===========================================================================
def _uname(payload: Dict) -> str:
    conn = db_conn()
    r = conn.execute("SELECT username FROM users WHERE id=?", (payload["sub"],)).fetchone()
    conn.close()
    return r["username"] if r else payload.get("sub", "")


def _ip(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


# ===========================================================================
# 操作员前端单页（仍托管静态文件，同源零跨域）
# ===========================================================================
@app.get("/")
async def index():
    html_path = Path(STATIC_DIR) / "index.html"
    if not html_path.exists():
        return JSONResponse(status_code=404, content=_err(40401, "前端页面缺失"))
    return FileResponse(str(html_path))


if not os.path.isdir(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
