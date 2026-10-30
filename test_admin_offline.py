# -*- coding: utf-8 -*-
"""
离线集成测试：用最小桩件替换 fastapi/pydantic 等，直接驱动 admin_app 的核心逻辑函数
（无需 uvicorn / fastapi / pymupdf）。验证：认证/RBAC、知识库上传检索、提示词模板、
对话审计标记与 CSV 导出、操作日志。运行：python test_admin_offline.py
"""
import importlib
import os
import sys
import tempfile

# ---- 1) 在导入 admin_app 前，准备干净的临时库与空文档目录 ----
_TMP = tempfile.mkdtemp(prefix="admin_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "admin_test.db")
os.environ["DOCS_DIR"] = os.path.join(_TMP, "docs")   # 空目录，避免误摄取样例
os.makedirs(os.environ["DOCS_DIR"], exist_ok=True)


# ---- 2) 构建 fastapi / pydantic 桩件（仅满足导入与装饰器，不做网络） ----
def _make_module(name, attrs):
    mod = type(sys)("stub_" + name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class _FastAPI:
    def __init__(self, *a, **k): pass
    def on_event(self, *a, **k): return lambda f: f
    def exception_handler(self, *a, **k): return lambda f: f
    def get(self, *a, **k): return lambda f: f
    def post(self, *a, **k): return lambda f: f
    def put(self, *a, **k): return lambda f: f
    def delete(self, *a, **k): return lambda f: f
    def mount(self, *a, **k): return None


class _HTTPException(Exception):
    def __init__(self, status_code=500, detail=None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class _Depends:
    def __init__(self, dep): self.dependency = dep


def _sentinel(*a, **k): return None


fastapi = _make_module("fastapi", {
    "FastAPI": _FastAPI, "HTTPException": _HTTPException, "Request": object,
    "Depends": _Depends, "Header": staticmethod(_sentinel), "Cookie": staticmethod(_sentinel),
    "UploadFile": object, "File": staticmethod(_sentinel), "Form": staticmethod(_sentinel),
    "Query": staticmethod(_sentinel),
})
fastapi_responses = _make_module("fastapi.responses", {
    "JSONResponse": object, "StreamingResponse": object, "RedirectResponse": object,
    "HTMLResponse": object, "FileResponse": object, "Response": object,
})
fastapi_templating = _make_module("fastapi.templating", {
    "Jinja2Templates": type("J", (), {"__init__": lambda self, *a, **k: None,
                                      "TemplateResponse": lambda self, *a, **k: None}),
})
fastapi_staticfiles = _make_module("fastapi.staticfiles", {
    "StaticFiles": type("S", (), {"__init__": lambda self, *a, **k: None}),
})
pydantic = _make_module("pydantic", {
    "BaseModel": type("B", (), {}), "Field": staticmethod(_sentinel),
})


# ---- 3) 导入被测模块 ----
import admin_app as app

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  [PASS] {name}")
    else:
        FAIL += 1; print(f"  [FAIL] {name}")

print("== 管理员后台离线集成测试 ==")

# ---- A) 数据库与种子 ----
app.init_db()
app.seed_data()
conn = app.db_conn()
u = conn.execute("SELECT id,role FROM users WHERE username='admin'").fetchone()
op = conn.execute("SELECT id FROM users WHERE username='operator'").fetchone()
tpl = conn.execute("SELECT COUNT(*) AS c FROM prompt_templates WHERE type='system' AND is_active=1").fetchone()
conn.close()
check("种子管理员账号存在", u is not None and u["role"] == "admin")
check("种子操作员账号存在", op is not None)
check("默认 system 提示词已生效", tpl["c"] == 1)

# ---- B) 认证与 RBAC ----
tok = app.create_access_token(u["id"], "admin")
dec = app.decode_token(tok)
check("JWT 签发/解析(sub,role)", dec["sub"] == u["id"] and dec["role"] == "admin")
bad = app.create_access_token("x", "operator")
try:
    app.decode_token(bad + "tamper"); ok = False
except app.CredentialsError:
    ok = True
check("JWT 签名篡改被拒", ok)
hp = app.hash_password("Admin@123")
check("密码哈希/校验一致", app.verify_password("Admin@123", hp) and not app.verify_password("wrong", hp))

# ---- C) 知识库：上传 MD → 列表 → 重建 → 删除 ----
info = app.kb_upload_and_index("测试制度.md", "# 高值耗材\n一物一码管理。\n".encode("utf-8"), "sop")
check("MD 上传入库(切片>0)", info["chunk_count"] > 0 and info["status"] == "indexed")
lst = app.kb_list()
check("文档列表包含上传项", any(d["title"] == "测试制度.md" for d in lst["items"]))
chk = app.kb_chunks_preview(info["id"], 5)
check("切片预览返回内容", len(chk) > 0 and "高值耗材" in chk[0]["content"])
app.kb_reindex(info["id"])
check("重建索引后仍可查", app.kb_list()["total"] >= 1)
app.kb_delete(info["id"])
check("删除后列表减少", not any(d["title"] == "测试制度.md" for d in app.kb_list()["items"]))

# ---- D) 提示词模板：创建/生效切换/RAG 读取 ----
pid = app.prompt_create("测试模板", "system", "你是测试助手 {context}", u["id"])
app.prompt_activate(pid)
active = app.get_active_system_template()
check("激活后 RAG 读取到新模板", active == "你是测试助手 {context}")
# 还原：把默认模板重新激活（通过列表找默认并激活）
for t in app.prompt_list():
    if t["name"] == "默认系统提示词":
        app.prompt_activate(t["id"]); break
check("可切回默认模板", "检索到的制度文档片段" in app.get_active_system_template())

# ---- E) 对话审计：模拟会话 → 检索 → 标记 → 导出 CSV ----
conn = app.db_conn()
sid = "test-session-1"
conn.execute("INSERT INTO sessions(id,user_id,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
             (sid, op["id"], "测试会话", "active", app._now(), app._now()))
conn.execute("INSERT INTO messages(id,session_id,role,content,citations,created_at) VALUES(?,?,?,?,?,?)",
             (str(app.uuid.uuid4()), sid, "user", "耗材怎么追溯？", "[]", app._now()))
conn.execute("INSERT INTO messages(id,session_id,role,content,citations,created_at) VALUES(?,?,?,?,?,?)",
             (str(app.uuid.uuid4()), sid, "assistant", "按批号追溯。", "[]", app._now()))
conn.commit(); conn.close()

res = app.audit_search(operator="operator", risk=None)
check("审计可按操作员检索到会话", res["total"] >= 1 and any(x["id"] == sid for x in res["items"]))
det = app.audit_detail(sid)
check("会话详情含 2 条消息", len(det["messages"]) == 2)
app.audit_flag(sid, "批次缺失", "测试备注")
conn = app.db_conn()
rf = conn.execute("SELECT risk_flag,risk_type FROM sessions WHERE id=?", (sid,)).fetchone()
conn.close()
check("风险标记写入会话", rf["risk_flag"] == 1 and rf["risk_type"] == "批次缺失")
csv_text = app.audit_export_csv(operator="operator")
check("CSV 导出含表头与数据", "会话ID" in csv_text and sid in csv_text)
app.audit_unflag(sid)
conn = app.db_conn()
rf2 = conn.execute("SELECT risk_flag FROM sessions WHERE id=?", (sid,)).fetchone()
conn.close()
check("取消风险标记", rf2["risk_flag"] == 0)

# ---- F) 操作日志：直接验证 write_op_log 写入与查询（路由层在写操作成功后调用它） ----
app.write_op_log(u["id"], "admin", "admin", "kb_upload", "kb_document", "doc-test-001",
                 {"title": "测试制度.md"}, "127.0.0.1")
logs = app.op_log_list(action="kb_upload")
check("操作日志写入并可按动作检索", logs["total"] >= 1 and logs["items"][0]["action"] == "kb_upload")

print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
