# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手 · 业务 Agent 执行编排核心（Agent Harness）
============================================================================

本模块实现「业务 Agent 执行编排循环」，是上一轮架构方案中
【智能体编排层 Agent Orchestration】的落地。设计要点：

1. ★ Harness 模式（轻量 Agent Loop）：
   不依赖外部重量级框架（LangChain/AutoGen 等），自带一个「编排骨架(Harness)」，
   核心循环为：模型决策 → 执行工具 → 结果回填上下文 → 再次决策，最多 5 轮防死循环。
   （如需接入 LangChain/OpenAI Agents SDK，只需把 Planner 换成对应实现，工具定义不变。）

2. ★ 工具注册表（Skill/Function Calling 工具）：
   每个业务工具定义为 Tool(name, description, parameters, kind, handler)。
   kind 分两类：
     - "read"  读类/检索类（RAG 检索、档案校验、效期校验、批次合规校验）—— 可直接执行；
     - "draft" 写类/变更类（生成申领草稿、质量工单、追溯记录）—— 触发前【强制 HITL 阻断】，
                只生成「草稿预览」返回操作员复核，收到确认指令后才真正落库。

3. ★ 每轮工具调用完整写入 agent_execution_logs 表（需求②审计留痕）：
   记录 工具名称 / 入参 / 调用时间 / 耗时 / 结果摘要 / 操作人 / session_id。

4. ★ 复用模块（明确标注）：
   - 检索：stage1_prototype.hybrid_retrieve（BM25 + 向量混合检索，FAISS 优先/离线回退）
   - 嵌入/向量库：stage1_prototype 的 VectorStore / BM25 / EmbeddingProvider
   - LLM 网关：stage1_prototype.call_llm / get_llm_provider（可切换供应商）
   - 知识库装配：等价于 admin_app 的 _refresh_kb（从 admin.db 的 kb_* 表构建内存索引）
   - 提示词模板：admin_app 的 get_active_system_template（管理员在线维护、即时生效）

本文件零第三方依赖（仅 Python 标准库 + 复用 stage1_prototype），离线也可跑通演示。
============================================================================
"""

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# ★ 复用模块：阶段1 的检索 / 向量 / LLM 网关（智能体编排层核心依赖）
# ---------------------------------------------------------------------------
import stage1_prototype as s1

# 与 admin_app 共享同一 SQLite（知识库、耗材档案、申领单等都在这里）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "admin.db"))
DOCS_DIR = os.getenv("DOCS_DIR", os.path.join(BASE_DIR, "docs"))

# LLM / 嵌入 配置（与 admin_app 保持一致，可被环境变量覆盖）
LLM_CFG = {
    "llm_provider": os.getenv("LLM_PROVIDER", "mock"),
    "llm_base_url": os.getenv("LLM_BASE_URL", ""),
    "llm_api_key": os.getenv("LLM_API_KEY", ""),
    "llm_model": os.getenv("LLM_MODEL", ""),
    "embed_provider": os.getenv("EMBED_PROVIDER", "auto"),
}
EMBED_PROVIDER = LLM_CFG["embed_provider"]
TOP_K = int(os.getenv("TOP_K", "3"))
MAX_AGENT_ITERS = int(os.getenv("MAX_AGENT_ITERS", "5"))   # 需求①：整体最大迭代 5 轮，避免死循环

# 知识库内存索引（进程级，等价于 admin_app.KB）
KB = {"ready": False, "vs": None, "bm25": None, "chunks": [], "embedder": None}


# ===========================================================================
# 模块 A：数据库封装 + Agent 专用表
# ===========================================================================
def db_conn() -> sqlite3.Connection:
    """短生命周期连接（sqlite3 非线程安全，即用即关）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def init_agent_db() -> None:
    """
    ★ 创建 Agent 运行所需的全部表（幂等，CREATE IF NOT EXISTS）。
    同时补齐 consumables / requisitions / work_orders / batch_trace_records 的扩展字段，
    使本模块既可独立运行，也能与 admin_app 共享同一 admin.db。
    """
    conn = db_conn()
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
        -- ★ 耗材档案（比 admin_app 极简版更丰富，满足「档案校验 / 库存效期校验」）
        CREATE TABLE IF NOT EXISTS consumables (
            id TEXT PRIMARY KEY, code TEXT, udi TEXT, name TEXT,
            spec TEXT, unit TEXT, category TEXT, manufacturer TEXT,
            storage_condition TEXT, expiry_months INTEGER,
            stock_qty INTEGER DEFAULT 0, status TEXT DEFAULT 'active'
        );
        -- ★ 批次追溯流水（追加写事件日志，全链路溯源）
        CREATE TABLE IF NOT EXISTS batch_trace_records (
            id TEXT PRIMARY KEY, consumable_id TEXT, batch_no TEXT,
            link_type TEXT, location TEXT, quantity INTEGER,
            operator_id TEXT, event_time TEXT, source_system TEXT,
            trace_payload TEXT, created_at TEXT
        );
        -- ★ 耗材申领单（草稿经确认后落单）
        CREATE TABLE IF NOT EXISTS requisitions (
            id TEXT PRIMARY KEY, requisition_no TEXT, session_id TEXT,
            applicant_id TEXT, consumable_id TEXT, batch_no TEXT,
            quantity INTEGER, purpose TEXT, department TEXT,
            status TEXT DEFAULT 'submitted', approver_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        -- ★ 质量异常工单
        CREATE TABLE IF NOT EXISTS work_orders (
            id TEXT PRIMARY KEY, order_no TEXT, session_id TEXT,
            type TEXT, title TEXT, description TEXT, consumable_id TEXT,
            batch_no TEXT, reporter_id TEXT, priority TEXT,
            status TEXT DEFAULT 'open', created_at TEXT, closed_at TEXT
        );
        -- ★★★ 需求②：Agent 执行日志表（每轮工具调用完整留痕，支撑质控/复盘）
        CREATE TABLE IF NOT EXISTS agent_execution_logs (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            operator_id TEXT,
            tool_name TEXT,
            input_args TEXT,        -- JSON：工具入参
            started_at TEXT,        -- 调用开始时间
            latency_ms INTEGER,     -- 耗时（毫秒）
            result_summary TEXT,    -- 结果摘要
            status TEXT,            -- ok / await_confirm / committed / error
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agentlog_session ON agent_execution_logs(session_id);
        CREATE INDEX IF NOT EXISTS idx_agentlog_tool ON agent_execution_logs(tool_name);
    """)
    conn.commit()
    conn.close()


def write_agent_log(session_id: str, operator_id: str, tool_name: str,
                    input_args: dict, started_at: str, latency_ms: int,
                    result_summary: str, status: str) -> str:
    """★ 写入一条 Agent 执行日志（需求②）。返回日志 id。"""
    log_id = str(uuid.uuid4())
    conn = db_conn()
    conn.execute(
        "INSERT INTO agent_execution_logs"
        "(id,session_id,operator_id,tool_name,input_args,started_at,latency_ms,result_summary,status,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (log_id, session_id, operator_id, tool_name, json.dumps(input_args, ensure_ascii=False),
         started_at, latency_ms, result_summary, status, _now()))
    conn.commit()
    conn.close()
    return log_id


# ===========================================================================
# 模块 B：知识库装配（等价于 admin_app._refresh_kb，复用 stage1）
# ===========================================================================
def ensure_kb_ready() -> None:
    """
    ★ 复用 stage1 的摄取/切片/嵌入链路，把制度文档灌入 admin.db 的 kb_* 表，
    并构建内存向量/BM25 索引供 Agent 检索。
    与 admin_app 共享知识库：管理员在后台上传的文档，Agent 可直接检索。
    """
    conn = db_conn()
    cnt = conn.execute("SELECT COUNT(*) AS c FROM kb_chunks").fetchone()["c"]
    conn.close()
    if cnt == 0:
        _ingest_docs_from_dir()
    _rebuild_memory_index()


def _ingest_docs_from_dir() -> None:
    """从 DOCS_DIR 摄取 MD/TXT → 切片 → 嵌入 → 写入 kb_documents / kb_chunks。"""
    docs = s1.load_markdown_docs(DOCS_DIR)
    if not docs:
        return
    embedder = s1.get_embedding_provider(EMBED_PROVIDER, LLM_CFG)
    # TF-IDF 类必须在「全量语料」上拟合，否则向量空间错位导致检索失效
    all_chunks = []
    for d in docs:
        for ch in s1.chunk_text(d["text"]):
            all_chunks.append(ch)
    if isinstance(embedder, (s1.OfflineTfidfEmbeddingProvider, s1.SklearnTfidfEmbeddingProvider)):
        embedder.fit(all_chunks)
    conn = db_conn()
    cur = conn.cursor()
    for d in docs:
        doc_id = str(uuid.uuid4())
        chunks = s1.chunk_text(d["text"])
        embs = embedder.encode(chunks)
        src = d["source"]
        cur.execute(
            "INSERT INTO kb_documents(id,title,category,source,file_type,original_filename,chunk_count,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, src, "general", src, "md", src,
             len(chunks), "indexed", _now()))
        for i, (ch, e) in enumerate(zip(chunks, embs)):
            cur.execute(
                "INSERT INTO kb_chunks(id,document_id,chunk_index,content,embedding,meta) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), doc_id, i, ch,
                 json.dumps(e, ensure_ascii=False), json.dumps({"idx": i}, ensure_ascii=False)))
    conn.commit()
    conn.close()


def _rebuild_memory_index() -> None:
    """从 admin.db 的 kb_* 表重建内存向量/BM25 索引（与 admin_app._refresh_kb 等价）。"""
    conn = db_conn()
    rows = conn.execute(
        "SELECT kc.content AS content, kc.embedding AS embedding, kd.source AS source "
        "FROM kb_chunks kc JOIN kb_documents kd ON kc.document_id=kd.id").fetchall()
    conn.close()
    chunks = [{"source": r["source"], "text": r["content"]} for r in rows]
    embeddings = [json.loads(r["embedding"]) for r in rows]
    embedder = s1.get_embedding_provider(EMBED_PROVIDER, LLM_CFG)
    if isinstance(embedder, (s1.OfflineTfidfEmbeddingProvider, s1.SklearnTfidfEmbeddingProvider)):
        embedder.fit([c["text"] for c in chunks])
    vs = s1.VectorStore()
    vs.build(embeddings, chunks)
    bm25 = s1.BM25([s1._tokenize(c["text"]) for c in chunks])
    KB.update({"ready": True, "vs": vs, "bm25": bm25, "chunks": chunks, "embedder": embedder})


# ===========================================================================
# 模块 C：工具定义（Function Calling / Skill）
# ===========================================================================
@dataclass
class Tool:
    """★ 一个可被 Agent 调用的业务工具（Skill）。
    kind: "read" 可直接执行；"draft" 触发前强制 HITL 阻断（只生成草稿预览）。"""
    name: str
    description: str
    parameters: dict          # JSON Schema，供 LLM 决策与参数校验
    kind: str                 # "read" | "draft"
    handler: Callable         # (args: dict, ctx: dict) -> dict


@dataclass
class Decision:
    """Planner 每轮给出的决策：要么是最终回答，要么是一次工具调用。"""
    kind: str                 # "final" | "tool"
    text: Optional[str] = None
    tool: Optional[str] = None
    args: Optional[dict] = None


# ---------------------------------------------------------------------------
# 工具 1：RAG 检索法规 / 院内 SOP（read 类）
# ---------------------------------------------------------------------------
def _tool_rag_query(args: dict, ctx: dict) -> dict:
    """★ 复用 stage1.hybrid_retrieve 做 BM25+向量混合检索，返回 top-k 制度片段。"""
    if not KB["ready"]:
        return {"ok": False, "summary": "知识库未就绪", "data": []}
    contexts = s1.hybrid_retrieve(args["query"], KB["vs"], KB["bm25"], KB["embedder"], top_k=TOP_K)
    snippets = []
    for c in contexts:
        snippets.append({"source": c["source"], "text": c["text"], "score": c.get("score", 0)})
    return {"ok": True, "summary": f"检索到 {len(snippets)} 条制度片段",
            "data": {"query": args["query"], "snippets": snippets}}


# ---------------------------------------------------------------------------
# 工具 2：耗材档案校验（read 类）—— 查准入规范 / 基础档案
# ---------------------------------------------------------------------------
def _tool_check_consumable_archive(args: dict, ctx: dict) -> dict:
    """查 consumables 表，返回耗材准入/基础档案。"""
    kw = args.get("keyword", "")
    conn = db_conn()
    if kw:
        row = conn.execute(
            "SELECT * FROM consumables WHERE name LIKE ? OR code LIKE ? OR udi LIKE ? LIMIT 1",
            (f"%{kw}%", f"%{kw}%", f"%{kw}%")).fetchone()
    else:
        row = conn.execute("SELECT * FROM consumables LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {"ok": False, "summary": f"未找到耗材档案：{kw}", "data": None}
    d = dict(row)
    return {"ok": True, "summary": f"档案命中：{d['name']}（编码 {d['code']}，状态 {d['status']}）", "data": d}


# ---------------------------------------------------------------------------
# 工具 3：库存 & 效期校验（read 类）
# ---------------------------------------------------------------------------
def _tool_check_inventory_expiry(args: dict, ctx: dict) -> dict:
    """基于耗材档案 + 申领数量 + 效期，给出库存/效期校验结论。"""
    consumable_id = args.get("consumable_id") or _resolve_consumable_id(args.get("keyword", ""))
    batch_no = args.get("batch_no", "")
    quantity = int(args.get("quantity", 0) or 0)
    expiry_date = args.get("expiry_date", "")
    conn = db_conn()
    row = conn.execute("SELECT * FROM consumables WHERE id=? OR name LIKE ? OR code LIKE ?",
                      (consumable_id, f"%{args.get('keyword','')}%", f"%{args.get('keyword','')}%")).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "summary": "未找到对应耗材，无法校验", "data": None}
    d = dict(row)
    # 简单规则校验（演示用）：效期不得早于今天；库存不得低于申领量
    today = time.strftime("%Y-%m-%d")
    problems = []
    if expiry_date and expiry_date < today:
        problems.append(f"效期 {expiry_date} 已过期，禁止入库")
    if quantity and d.get("stock_qty", 0) < quantity:
        problems.append(f"当前库存 {d.get('stock_qty')} 低于申领量 {quantity}")
    if problems:
        # 工具执行成功，仅校验结论为「不合规」（status 记为 ok，结论放 data）
        return {"ok": True, "summary": "；".join(problems), "data": {"problems": problems, "archive": d,
                                                                     "pass": False}}
    return {"ok": True, "summary": f"库存/效期校验通过（库存 {d.get('stock_qty')}，效期 {expiry_date or '未提供'}）",
            "data": {"archive": d, "expiry_date": expiry_date, "quantity": quantity, "pass": True}}


def _resolve_consumable_id(keyword: str) -> str:
    if not keyword:
        return ""
    conn = db_conn()
    row = conn.execute("SELECT id FROM consumables WHERE name LIKE ? OR code LIKE ? LIMIT 1",
                      (f"%{keyword}%", f"%{keyword}%")).fetchone()
    conn.close()
    return row["id"] if row else ""


# ---------------------------------------------------------------------------
# 工具 4：批次合规校验（read 类）—— 轨迹 c 用
# ---------------------------------------------------------------------------
def _tool_check_batch_compliance(args: dict, ctx: dict) -> dict:
    """
    批次合规校验：依据「批次可追溯」要求，判断批次号等关键信息是否齐全。
    返回校验结论 + 法规/SOP 依据（RAG 检索支撑）。
    """
    batch_no = (args.get("batch_no") or "").strip()
    keyword = args.get("keyword", "")
    # RAG 检索「批次 / 追溯」相关制度作为依据
    basis = []
    if KB["ready"]:
        basis_ctx = s1.hybrid_retrieve("批次号 追溯 入库 合规要求", KB["vs"], KB["bm25"],
                                       KB["embedder"], top_k=2)
        basis = [f"【{c['source']}】{c['text'][:80]}…" for c in basis_ctx]
    if not batch_no:
        # 工具执行成功，仅校验结论为「不合规」（status 记为 ok，结论放 data）
        return {
            "ok": True,
            "summary": "批次号缺失，不符合「一物一码、批次可追溯」入库要求，禁止办理入库",
            "data": {"compliant": False, "reason": "批次号缺失",
                     "basis": basis or ["《医疗器械使用质量监督管理办法》：医用耗材须可追溯至批次"]}
        }
    return {"ok": True, "summary": f"批次号 {batch_no} 齐全，批次合规校验通过",
            "data": {"compliant": True, "batch_no": batch_no, "basis": basis}}


# ---------------------------------------------------------------------------
# 工具 5/6/7：写类草稿（draft 类）—— 触发前强制 HITL 阻断，仅生成预览
# ---------------------------------------------------------------------------
def _tool_draft_requisition(args: dict, ctx: dict) -> dict:
    """★ 生成耗材申领草稿（不落库）。落库动作只在操作员确认后由 commit 执行。"""
    consumable_id = args.get("consumable_id") or _resolve_consumable_id(args.get("keyword", ""))
    draft = {
        "type": "requisition",
        "consumable_id": consumable_id,
        "keyword": args.get("keyword", ""),
        "batch_no": args.get("batch_no", ""),
        "quantity": args.get("quantity", 0),
        "expiry_date": args.get("expiry_date", ""),
        "purpose": args.get("purpose", ""),
        "department": args.get("department", ctx.get("dept", "默认科室")),
    }
    return {"ok": True, "summary": "已生成申领草稿，等待操作员复核确认", "data": draft}


def _tool_create_work_order(args: dict, ctx: dict) -> dict:
    """★ 生成质量异常工单草稿（不落库）。"""
    draft = {
        "type": "work_order",
        "wo_type": args.get("wo_type", "quality"),
        "title": args.get("title", "耗材质量异常"),
        "description": args.get("description", ""),
        "consumable_id": args.get("consumable_id") or _resolve_consumable_id(args.get("keyword", "")),
        "batch_no": args.get("batch_no", ""),
        "priority": args.get("priority", "mid"),
    }
    return {"ok": True, "summary": "已生成质量异常工单草稿，等待操作员复核确认", "data": draft}


def _tool_add_trace_record(args: dict, ctx: dict) -> dict:
    """★ 生成追溯记录草稿（不落库）。"""
    draft = {
        "type": "trace_record",
        "consumable_id": args.get("consumable_id") or _resolve_consumable_id(args.get("keyword", "")),
        "batch_no": args.get("batch_no", ""),
        "link_type": args.get("link_type", "inbound"),
        "location": args.get("location", ""),
        "quantity": args.get("quantity", 0),
        "operator_id": ctx.get("operator_id", ""),
    }
    return {"ok": True, "summary": "已生成追溯记录草稿，等待操作员复核确认", "data": draft}


# ---------------------------------------------------------------------------
# 落库提交（仅确认后调用）—— COMMITTERS 与 draft 工具一一对应
# ---------------------------------------------------------------------------
def _commit_requisition(draft: dict, operator_id: str, session_id: str) -> dict:
    rid = str(uuid.uuid4())
    no = "RQ" + time.strftime("%Y%m%d%H%M%S")
    conn = db_conn()
    conn.execute(
        "INSERT INTO requisitions(id,requisition_no,session_id,applicant_id,consumable_id,batch_no,"
        "quantity,purpose,department,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, no, session_id, operator_id, draft.get("consumable_id"), draft.get("batch_no"),
         draft.get("quantity"), draft.get("purpose"), draft.get("department"),
         "submitted", _now(), _now()))
    conn.commit()
    conn.close()
    return {"ok": True, "summary": f"申领单已落库：{no}", "data": {"id": rid, "no": no}}


def _commit_work_order(draft: dict, operator_id: str, session_id: str) -> dict:
    wid = str(uuid.uuid4())
    no = "WO" + time.strftime("%Y%m%d%H%M%S")
    conn = db_conn()
    conn.execute(
        "INSERT INTO work_orders(id,order_no,session_id,type,title,description,consumable_id,"
        "batch_no,reporter_id,priority,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (wid, no, session_id, draft.get("wo_type"), draft.get("title"), draft.get("description"),
         draft.get("consumable_id"), draft.get("batch_no"), operator_id,
         draft.get("priority"), "open", _now()))
    conn.commit()
    conn.close()
    return {"ok": True, "summary": f"质量工单已落库：{no}", "data": {"id": wid, "no": no}}


def _commit_trace_record(draft: dict, operator_id: str, session_id: str) -> dict:
    tid = str(uuid.uuid4())
    conn = db_conn()
    conn.execute(
        "INSERT INTO batch_trace_records(id,consumable_id,batch_no,link_type,location,quantity,"
        "operator_id,event_time,source_system,trace_payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tid, draft.get("consumable_id"), draft.get("batch_no"), draft.get("link_type"),
         draft.get("location"), draft.get("quantity"), operator_id, _now(), "agent",
         json.dumps(draft, ensure_ascii=False), _now()))
    conn.commit()
    conn.close()
    return {"ok": True, "summary": f"追溯记录已落库：{tid[:8]}", "data": {"id": tid}}


# ★ draft 工具名 → 落库函数 映射（确认后执行）
COMMITTERS = {
    "draft_requisition": _commit_requisition,
    "create_quality_work_order": _commit_work_order,
    "add_trace_record": _commit_trace_record,
}


def build_tools() -> List[Tool]:
    """★ 注册全部业务工具（Function Calling 定义）。"""
    return [
        Tool("rag_query", "检索医疗器械监管法规、院内耗材 SOP、批次/效期追溯规范。",
             {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
             "read", _tool_rag_query),
        Tool("check_consumable_archive", "查询耗材基础档案与准入规范。",
             {"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]},
             "read", _tool_check_consumable_archive),
        Tool("check_inventory_expiry", "校验库存是否充足、效期是否合规。",
             {"type": "object", "properties": {
                 "keyword": {"type": "string"}, "consumable_id": {"type": "string"},
                 "batch_no": {"type": "string"}, "quantity": {"type": "integer"},
                 "expiry_date": {"type": "string"}},
              "required": []},
             "read", _tool_check_inventory_expiry),
        Tool("check_batch_compliance", "批次合规校验：判断批次号等关键信息是否齐全，返回结论与法规/SOP依据。",
             {"type": "object", "properties": {
                 "batch_no": {"type": "string"}, "keyword": {"type": "string"}},
              "required": []},
             "read", _tool_check_batch_compliance),
        Tool("draft_requisition", "【写类·需确认】生成耗材申领草稿（不落库），供操作员复核。",
             {"type": "object", "properties": {
                 "keyword": {"type": "string"}, "consumable_id": {"type": "string"},
                 "batch_no": {"type": "string"}, "quantity": {"type": "integer"},
                 "expiry_date": {"type": "string"}, "purpose": {"type": "string"},
                 "department": {"type": "string"}},
              "required": ["keyword"]},
             "draft", _tool_draft_requisition),
        Tool("create_quality_work_order", "【写类·需确认】生成质量异常工单草稿（不落库）。",
             {"type": "object", "properties": {
                 "keyword": {"type": "string"}, "title": {"type": "string"},
                 "description": {"type": "string"}, "batch_no": {"type": "string"},
                 "wo_type": {"type": "string"}, "priority": {"type": "string"}},
              "required": []},
             "draft", _tool_create_work_order),
        Tool("add_trace_record", "【写类·需确认】生成批次追溯记录草稿（不落库）。",
             {"type": "object", "properties": {
                 "keyword": {"type": "string"}, "batch_no": {"type": "string"},
                 "link_type": {"type": "string"}, "location": {"type": "string"},
                 "quantity": {"type": "integer"}},
              "required": []},
             "draft", _tool_add_trace_record),
    ]


# ===========================================================================
# 模块 D：Planner（决策器）—— 两种实现
# ===========================================================================
class RuleBasedPlanner:
    """
    ★ 离线/默认决策器：基于关键词 + 流程状态，确定性地给出下一步工具调用或最终回答。
    保证在无真实大模型时，也能演示完整 Agent 轨迹（需求④三条示例）。
    接入真实 LLM 时，替换为 LLMPlanner 即可，工具定义不变。
    """

    def plan(self, messages: List[Dict], tools: List[Tool], state: dict) -> Decision:
        # 取用户最新问题
        user_q = [m["content"] for m in messages if m["role"] == "user"][-1]
        # 已执行的工具序列（用于多步流程的状态推进）
        done = state.get("done_tools", [])
        slots = state.get("slots", {})

        q = user_q

        # ---- 轨迹 c：批次号缺失 / 能否入库 -> 仅批次合规校验（读类，一步即结论）----
        if re.search(r"批次号缺失|可以.*入库|能否.*入库|能不能.*入库", q):
            if "check_batch_compliance" not in done:
                return Decision("tool", tool="check_batch_compliance",
                                args={"batch_no": slots.get("batch_no", ""), "keyword": slots.get("keyword", "")})
            # 已校验，直接综合结论（避免被「办理入库」误带进申领多步流程）
            return Decision("final", text=None)

        # ---- 轨迹 b：提交 / 申领 -> 多步：检索准入 -> 档案校验 -> 库存效期 -> 草稿(待确认) ----
        if re.search(r"提交.*申领|申领|办理.*入库", q):
            if "rag_query" not in done:
                return Decision("tool", tool="rag_query",
                                args={"query": f"{slots.get('keyword','')} 耗材准入规范 申领要求"})
            if "check_consumable_archive" not in done:
                return Decision("tool", tool="check_consumable_archive",
                                args={"keyword": slots.get("keyword", "")})
            if "check_inventory_expiry" not in done:
                return Decision("tool", tool="check_inventory_expiry",
                                args={"keyword": slots.get("keyword", ""),
                                      "batch_no": slots.get("batch_no", ""),
                                      "quantity": slots.get("quantity", 0),
                                      "expiry_date": slots.get("expiry_date", "")})
            if "draft_requisition" not in done:
                return Decision("tool", tool="draft_requisition",
                                args={"keyword": slots.get("keyword", ""),
                                      "batch_no": slots.get("batch_no", ""),
                                      "quantity": slots.get("quantity", 0),
                                      "expiry_date": slots.get("expiry_date", ""),
                                      "purpose": slots.get("purpose", ""),
                                      "department": slots.get("department", "")})
            # 草稿已生成，等待确认（由 harness 返回 pending，不在此出 final）

        # ---- 轨迹 a：入库验收要求是什么 -> 仅 RAG 检索（读类，不变更）----
        if re.search(r"入库验收|要求是什么|什么要求|规范", q) and "rag_query" not in done:
            return Decision("tool", tool="rag_query", args={"query": q})

        # ---- 默认：无法决策时，做一轮 RAG 检索再总结 ----
        if "rag_query" not in done:
            return Decision("tool", tool="rag_query", args={"query": q})

        # ---- 兜底：给出综合最终回答 ----
        return Decision("final", text=None)


class LLMPlanner:
    """
    ★ 真实大模型决策器：把工具定义交给 LLM（OpenAI 兼容端点），要求其以
    结构化 JSON 输出决策（action=tool|final）。适配任意支持 chat 的端点，
    不依赖 provider 原生 function calling，更通用稳健。
    （离线环境默认不启用；配置 LLM_API_KEY 后可将 planner 切为此类。）
    """

    def __init__(self, provider):
        self.provider = provider

    def plan(self, messages: List[Dict], tools: List[Tool], state: dict) -> Decision:
        tools_json = [{"name": t.name, "description": t.description,
                      "parameters": t.parameters, "kind": t.kind} for t in tools]
        sys_prompt = (
            "你是医用耗材智能助手的「任务规划器」。根据用户问题，从工具列表中选择下一步动作。\n"
            "只输出一行 JSON：\n"
            '{"action":"tool","tool":"工具名","args":{...}} 或 {"action":"final"}\n'
            "写类(draft)工具只能用于生成草稿，不得直接落库。\n"
            f"可用工具：{json.dumps(tools_json, ensure_ascii=False)}"
        )
        resp = self.provider.chat([{"role": "system", "content": sys_prompt}] + messages[-6:])
        try:
            obj = json.loads(_extract_json(resp))
            if obj.get("action") == "tool":
                return Decision("tool", tool=obj["tool"], args=obj.get("args", {}))
        except Exception:
            pass
        return Decision("final", text=None)


def _extract_json(text: str) -> str:
    """从模型可能的回复中抽取 JSON 片段。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else "{}"


# ===========================================================================
# 模块 E：Agent Harness（编排循环核心）
# ===========================================================================
class AgentHarness:
    """
    ★ Harness 模式核心：标准 Agent 循环 =
       模型决策 → 执行工具 → 结果回填上下文 → 模型继续判断，最多 MAX_AGENT_ITERS 轮。
    关键约束：
       - draft 类工具触发前【强制 HITL 阻断】：只返回草稿预览，不落库；
       - 收到操作员确认指令后，才调用 COMMITTERS 真正持久化落单（confirm_action）。
       - 每轮工具调用写入 agent_execution_logs（需求②）。
    """

    def __init__(self, tools: List[Tool], planner, cfg: dict = None):
        self.tools = {t.name: t for t in tools}
        self.planner = planner
        self.cfg = cfg or LLM_CFG

    # ---- 主入口：处理一轮用户请求 ----
    def run(self, user_query: str, session_id: str, operator_id: str,
            slots: dict = None, history: List[Dict] = None) -> dict:
        """
        返回结构：
          {
            "answer": str,            # 最终回答（若无需确认）
            "need_confirm": bool,     # 是否有待确认草稿
            "pending": {...} | None,  # 待确认草稿（含 tool_name + draft）
            "trace": [...]            # 本轮完整执行轨迹（工具/入参/耗时/结果）
          }
        """
        # 1) 会话与消息上下文装配
        session_id = self._ensure_session(session_id, operator_id)
        messages = list(history or [])
        messages.append({"role": "user", "content": user_query})

        state = {"done_tools": [], "slots": slots or {}}
        trace: List[Dict] = []

        # 2) 标准 Agent 循环（最多 5 轮防死循环）
        for it in range(1, MAX_AGENT_ITERS + 1):
            decision = self.planner.plan(messages, list(self.tools.values()), state)

            if decision.kind == "final":
                answer = self._synthesize(messages)
                return self._wrap(answer, False, None, trace, session_id)

            # ---- 执行工具调用 ----
            tool = self.tools.get(decision.tool)
            if not tool:
                return self._wrap(f"未知工具：{decision.tool}", False, None, trace, session_id)

            started = _now()
            t0 = time.time()
            # 写类工具：强制 HITL 阻断 —— 仅生成草稿预览，不落库
            if tool.kind == "draft":
                res = tool.handler(decision.args, {"operator_id": operator_id, "dept": "默认科室"})
                latency = int((time.time() - t0) * 1000)
                summary = res.get("summary", "")
                # 记录日志：await_confirm（尚未落库）
                write_agent_log(session_id, operator_id, tool.name, decision.args, started,
                                latency, summary, "await_confirm")
                trace.append({"iter": it, "tool": tool.name, "kind": "draft",
                              "input_args": decision.args, "latency_ms": latency,
                              "result_summary": summary, "status": "await_confirm"})
                pending = {"tool_name": tool.name, "draft": res.get("data", {}),
                           "args": decision.args}
                return self._wrap(None, True, pending, trace, session_id)

            # 读类工具：直接执行，结果回填上下文
            try:
                res = tool.handler(decision.args, {"operator_id": operator_id})
                latency = int((time.time() - t0) * 1000)
                summary = res.get("summary", "")
                status = "ok" if res.get("ok") else "error"
                write_agent_log(session_id, operator_id, tool.name, decision.args, started,
                                latency, summary, status)
                trace.append({"iter": it, "tool": tool.name, "kind": "read",
                              "input_args": decision.args, "latency_ms": latency,
                              "result_summary": summary, "status": status})
                # 把工具结果作为 role=tool 消息回填，供模型后续决策/综合
                messages.append({"role": "tool", "name": tool.name,
                                 "content": json.dumps(res.get("data", {}), ensure_ascii=False)})
                state["done_tools"].append(tool.name)
            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                write_agent_log(session_id, operator_id, tool.name, decision.args, started,
                                latency, f"异常：{e}", "error")
                trace.append({"iter": it, "tool": tool.name, "kind": tool.kind,
                              "input_args": decision.args, "latency_ms": latency,
                              "result_summary": f"异常：{e}", "status": "error"})
                return self._wrap(f"工具 {tool.name} 执行异常：{e}", False, None, trace, session_id)

        # 3) 达到最大迭代仍未 final：强制综合总结，避免死循环
        answer = self._synthesize(messages)
        return self._wrap(answer, False, None, trace, session_id)

    # ---- 确认动作：收到操作员确认后，真正落库 ----
    def confirm(self, pending: dict, session_id: str, operator_id: str) -> dict:
        """
        ★ HITL 第二步：仅当操作员明确确认后，才调用对应 COMMITTERS 落库。
        返回落库结果，并写日志 status=committed。
        """
        tool_name = pending["tool_name"]
        committer = COMMITTERS.get(tool_name)
        if not committer:
            return {"ok": False, "summary": f"无可落库函数：{tool_name}"}
        started = _now()
        t0 = time.time()
        res = committer(pending.get("draft", {}), operator_id, session_id)
        latency = int((time.time() - t0) * 1000)
        write_agent_log(session_id, operator_id, f"{tool_name}#commit", pending.get("args", {}),
                        started, latency, res.get("summary", ""), "committed")
        return res

    # ---- 内部：综合最终回答（复用 LLM 网关）----
    def _synthesize(self, messages: List[Dict]) -> str:
        """用 LLM 把检索/工具结果综合成面向操作员的回答（标注引用）。"""
        # 提取工具结果作为上下文
        ctx_parts = []
        for m in messages:
            if m.get("role") == "tool":
                ctx_parts.append(f"【工具 {m.get('name')} 返回】{m['content']}")
        if not ctx_parts:
            return "暂无相关制度或数据可参考。"
        # 复用阶段1 的提示词组装思路 + LLM 网关
        provider = s1.get_llm_provider(self.cfg["llm_provider"], self.cfg)
        prompt = (
            "你是医用耗材批次追溯智能助手。请基于下方工具返回的制度/数据，"
            "用中文简洁回答操作员，并标注来源文件名。若信息不足，明确说明。\n\n"
            + "\n".join(ctx_parts)
        )
        try:
            return provider.chat([{"role": "system", "content": prompt},
                                  {"role": "user", "content": "请根据上述上下文回答。"}])
        except Exception:
            return "\n".join(ctx_parts)  # Mock/离线退化为直接回显

    def _ensure_session(self, session_id: str, operator_id: str) -> str:
        if session_id:
            return session_id
        sid = str(uuid.uuid4())
        conn = db_conn()
        conn.execute("INSERT INTO sessions(id,user_id,title,status,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,?)", (sid, operator_id, "Agent会话", "active", _now(), _now()))
        conn.commit()
        conn.close()
        return sid

    def _wrap(self, answer, need_confirm, pending, trace, session_id) -> dict:
        return {"answer": answer, "need_confirm": need_confirm,
                "pending": pending, "trace": trace, "session_id": session_id}


# ===========================================================================
# 便捷入口
# ===========================================================================
def make_harness(use_llm_planner: bool = False) -> AgentHarness:
    """构造 Harness。默认 RuleBasedPlanner（离线可演示）；配了 API Key 可切 LLMPlanner。"""
    tools = build_tools()
    if use_llm_planner:
        provider = s1.get_llm_provider(LLM_CFG["llm_provider"], LLM_CFG)
        planner = LLMPlanner(provider)
    else:
        planner = RuleBasedPlanner()
    return AgentHarness(tools, planner)


# 供测试桩导入：把可能缺失的三方符号占位，避免离线 import 报错
try:
    import fastapi  # noqa: F401  （仅占位，运行期若缺失由 admin_app 负责）
except Exception:
    pass
