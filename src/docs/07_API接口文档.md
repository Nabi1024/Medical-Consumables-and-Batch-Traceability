# API 接口文档

> 医用耗材批次追溯智能助手 —— Web 服务接口说明

操作员问答服务（`chat_app.py`）与管理员后台（`admin_app.py`）均为 FastAPI 应用，
统一采用 JSON 入参、统一错误码。以下列出核心接口。

## 统一错误码

| 错误码   | 含义                     |
| -------- | ------------------------ |
| 0        | 成功                     |
| 40001    | 参数校验失败             |
| 40101    | 未鉴权 / Token 缺失      |
| 40301    | 权限不足（RBAC 拒绝）    |
| 40401    | 资源不存在               |
| 41301    | 上传文件过大             |
| 42901    | 请求过于频繁             |
| 50301    | 大模型 / 检索服务不可用  |
| 50001/50002 | 服务器内部错误         |

响应统一结构：`{"code": 0, "message": "...", "data": {...}}`。

---

## 操作员问答服务（chat_app）

### POST /chat（SSE 流式）

操作员发起一轮问答，服务端以 `text/event-stream` 流式返回检索状态、正文与引用来源。

请求体：
```json
{ "session_id": "可选, 不传则新建", "message": "一次性无菌注射器的入库验收要求" }
```

SSE 事件示例（每行 `data: {...}`）：
```json
{"type": "status", "text": "正在检索耗材法规、院内 SOP 文档…"}
{"type": "token",  "text": "根据"}
{"type": "token",  "text": "《一次性使用无菌医疗器械…》"}
{"type": "sources", "sources": [{"source": "01_医疗器械监管法规.md", "score": 0.91}]}
{"type": "done",   "session_id": "sess_xxx"}
```

要点：
- 上下文：自动拼接最近 6 轮历史（sessions / messages 表），保证多轮指代连贯。
- 引用：返回片段均标注来源文件名，便于操作员核对制度出处。
- 安全：后台服务与操作员服务建议网络隔离；本服务不暴露写类工具。

### GET /health

健康检查，返回 `{"status": "ok"}`。

---

## 管理员后台（admin_app）

### POST /api/auth/login

登录获取 JWT。

请求体：`{"username": "admin", "password": "Admin@123"}`
响应：`{"code":0, "data": {"token": "<jwt>", "role": "admin"}}`

受保护接口需在 Header 携带：`Authorization: Bearer <jwt>`。

### POST /api/kb/upload

上传知识库文档（PDF / Markdown），服务端切片、向量化并写入 `kb_documents` /
`kb_chunks`。超大体量返回 41301。

### GET /api/prompts  /  POST /api/prompts

提示词模板的查询与持久化管理（存于 `prompt_templates` 表），支持系统默认模板
与角色自定义模板。

### GET /api/audit/export?format=csv

对话审计与质控数据导出为 CSV（来自 `operation_logs` / `agent_execution_logs`），
满足等保三级留痕与复盘要求。

### RBAC 角色

- `admin`：可管理知识库、提示词模板、查看并导出审计。
- `operator`：仅可使用问答，无后台管理权限（40301 拦截越权）。
