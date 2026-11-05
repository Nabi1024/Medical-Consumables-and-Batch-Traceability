# 医用耗材批次追溯智能助手（Medical-Consumables-and-Batch-Traceability）

面向医疗机构的「医用耗材 + 批次追溯」智能问答与业务 Agent 系统：以 RAG 检索院内制度/法规为基础，
通过业务 Agent（Harness 编排 + 工具调用 + HITL 人工确认）完成耗材档案校验、库存效期校验、
批次合规校验、申领/工单/追溯草稿生成，并对违规意图（绕过审批、篡改追溯、隐瞒效期）做合规拒答。

## 目录结构

```
stage1_prototype.py   阶段1 RAG 原型：文档切片 / TF-IDF 向量 / FAISS(可降级) / BM25 / 混合检索 / LLM 网关
chat_app.py           操作员问答后端（FastAPI + SQLite + SSE 流式 + 最近6轮上下文）
admin_app.py          管理员后台（JWT + RBAC + 知识库管理 + 提示词模板 + 对话审计质控）
agent_core.py         业务 Agent 编排核心（Harness 循环 / 7 个工具 / HITL 阻断 / 执行日志）
demo_agent.py         离线演示三条典型轨迹（仅检索 / 申领落单HITL / 批次合规校验）
eval_agent.py         离线评测器（基于 qa_evaluation_cases.json，30 条用例自动打分）
qa_evaluation_cases.json  30 条评测用例（rag_query / agent_tool_call / risk_intent / multi_turn_context）
docs/                 5 份制度文档（监管法规 / 院内SOP / 批次追溯 / 效期管理 / 不良事件上报）
templates/ static/    Web 前端（Jinja2 服务端渲染 + 原生 JS 单页）
```

## 快速运行（离线 / 零三方依赖）

```bash
python demo_agent.py            # 离线演示 Agent 三条轨迹（默认 Mock LLM）
python eval_agent.py            # 运行 30 条评测用例并打出分数
python stage1_prototype.py --selftest   # RAG 自检
```

接入真实大模型（回答质量由真实 LLM 决定，路由/HITL/拒答逻辑不变）：

```bash
export LLM_PROVIDER=openai        # 或 deepseek
export LLM_BASE_URL=https://...
export LLM_API_KEY=sk-xxx
export LLM_MODEL=xxx
python -c "import agent_core, demo_agent; ..."   # 此时 Planner 可切 LLMPlanner
```

Web 服务（需联网安装）：`pip install fastapi uvicorn jinja2`，然后 `python admin_app.py`。

## 评测与评分

`qa_evaluation_cases.json` 含 30 条用例，覆盖四类：

- `rag_query`（10）：制度/法规问答；含 3 条「知识库无相关内容」陷阱，必须如实回「现有制度文档中无该项内容」，不编造。
- `agent_tool_call`（10）：档案/库存/批次/申领/工单/追溯等工具的正确依赖顺序与 HITL 阻断。
- `risk_intent`（5）：绕过审批、篡改追溯记录、隐瞒效期等违规意图，**一律拒绝执行任何工具**。
- `multi_turn_context`（5）：代词指代、跨轮实体替换、草稿二次确认。

`eval_agent.py` 对可确定性量化的部分（工具路由正确性、合规拒答、无内容陷阱）自动打分。
当前离线（RuleBasedPlanner + Mock）评分：**30/30 (100%)**（rag_query 10/10、agent_tool_call 10/10、
risk_intent 5/5、multi_turn_context 5/5）。真实 LLM 接入后，回答的「引用标注/自然度」会进一步提升。

## 合规要点

- **合规优先路由**：`RuleBasedPlanner` 在调用任何工具前先识别违规意图，坚决拒答，绝不执行不可逆/造假动作。
- **HITL 强制阻断**：申领草稿 / 质量工单 / 追溯记录等写类工具仅生成预览，须操作员二次确认后才落库（COMMITTERS）。
- **批次缺失阻断**：申领前若批次号缺失，强制阻断草稿生成，提示补齐后再提交。
- **审计留痕**：`agent_execution_logs` 完整记录每轮工具调用的入参/耗时/结果摘要/状态/操作人。
- **数据不出院**：默认私有化部署、SQLite 本地存储；离线环境下零三方依赖即可完整运行与演示。
```
