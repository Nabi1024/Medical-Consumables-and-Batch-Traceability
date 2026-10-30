# -*- coding: utf-8 -*-
"""离线测试桩：用最小桩件替换 fastapi/pydantic，验证 chat_app 的非 Web 逻辑链路。"""
import sys, types, os, json, tempfile

# ---- 1) 注入桩件，使 chat_app 可在无第三方包环境导入 ----
def _make_module(name, attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m

# 用真正的类定义桩件，避免 lambda 返回非 None 的坑
class _FastAPI:
    def __init__(self, *a, **k): pass
    def on_event(self, *a, **k): return lambda f: f
    def exception_handler(self, *a, **k): return lambda f: f
    def post(self, *a, **k): return lambda f: f
    def get(self, *a, **k): return lambda f: f
    def mount(self, *a, **k): pass

class _HTTPException(Exception):
    def __init__(self, status_code=500, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

class _Request: pass

fastapi = _make_module("fastapi", {
    "FastAPI": _FastAPI,
    "HTTPException": _HTTPException,
    "Request": _Request,
})
fastapi_responses = _make_module("fastapi.responses", {
    "StreamingResponse": type("StreamingResponse", (), {"__init__": lambda self, *a, **k: None}),
    "JSONResponse": type("JSONResponse", (), {"__init__": lambda self, *a, **k: None}),
    "FileResponse": type("FileResponse", (), {"__init__": lambda self, *a, **k: None}),
})
fastapi_staticfiles = _make_module("fastapi.staticfiles", {
    "StaticFiles": type("StaticFiles", (), {"__init__": lambda self, *a, **k: None}),
})
fastapi_exceptions = _make_module("fastapi.exceptions", {"RequestValidationError": _HTTPException})
pydantic = _make_module("pydantic", {
    "BaseModel": type("BaseModel", (), {}),
    "Field": staticmethod(lambda *a, **k: None),
})
sys.modules["fastapi"] = fastapi
sys.modules["fastapi.responses"] = fastapi_responses
sys.modules["fastapi.staticfiles"] = fastapi_staticfiles
sys.modules["fastapi.exceptions"] = fastapi_exceptions
sys.modules["pydantic"] = pydantic

# ---- 2) 用临时库 + 样例文档，避免污染真实数据 ----
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "chat.db")
os.environ["DOCS_DIR"] = os.path.join(tmp, "docs")
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBED_PROVIDER"] = "auto"

import chat_app as app

# ---- 3) 启动逻辑：建表 + 建知识库 ----
app.init_db()
app.s1._ensure_sample_docs(os.environ["DOCS_DIR"])   # 注入 5 类样例制度文档
app.ingest_kb_if_needed()
assert app.KB["ready"], "知识库未就绪"
print(f"[测试] KB 就绪，文档块={len(app.KB['chunks'])}, FAISS={app.s1._FAISS_AVAILABLE}")

# ---- 4) 驱动一次 chat_stream，解析事件 ----
sid = app.get_or_create_session(None)
print(f"[测试] 新会话 session_id={sid[:8]}…")

events = {"status": [], "token": [], "sources": [], "done": [], "error": []}
full = ""
for chunk in app.chat_stream(sid, "高值耗材怎么管理？"):
    # chunk 形如 "event: xxx\ndata: {...}\n\n"
    ev = None; data = ""
    for line in chunk.strip().split("\n"):
        if line.startswith("event:"): ev = line[6:].strip()
        elif line.startswith("data:"): data += line[5:].strip()
    if ev and data:
        obj = json.loads(data)
        events[ev].append(obj)
        if ev == "token": full += obj["delta"]

print("[测试] 事件统计:", {k: len(v) for k, v in events.items()})
assert events["status"], "缺少 status 事件"
assert events["token"], "缺少 token 事件（流式）"
assert events["sources"], "缺少 sources 事件"
assert events["done"], "缺少 done 事件"
print("[测试] 引用来源:", [(s["doc"], round(s["score"],3)) for s in events["sources"][0]["sources"]])
print("[测试] 助手回答(前80字):", full[:80].replace("\n"," "))

# ---- 5) 验证历史上下文：同一会话第二次提问应把第一轮写入历史 ----
hist = app.load_history(sid, 6)
print(f"[测试] 历史消息数={len(hist)}（应为2：用户+助手）")
assert len(hist) == 2, "历史未落库"

# ---- 6) 验证会话持久化：新会话复用已存在 id ----
sid2 = app.get_or_create_session(sid)
assert sid2 == sid, "复用会话失败"
# 用真实 HTTPException 判断：不存在的 session_id 应抛 404
try:
    # get_or_create_session 内部 raise HTTPException(404)
    app.get_or_create_session("non-exist-id")
    raise AssertionError("应抛出 404")
except Exception as e:
    if "404" in str(getattr(e, 'status_code', '')) or "会话不存在" in str(getattr(e, 'detail', '')):
        print("[测试] 不存在的 session_id 正确返回 40401 语义")
    else:
        raise

print("\n✅ 离线链路全部通过：建库 / 混合检索 / SSE 事件流 / 历史上下文 / 会话持久化 / 404 语义")
