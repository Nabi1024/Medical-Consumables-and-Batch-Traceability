# 医用耗材批次追溯智能助手（Medical-Consumables-and-Batch-Traceability）

面向医疗机构的「医用耗材 + 批次追溯」智能问答与业务 Agent 系统：以 RAG 检索院内制度/法规为基础，
通过业务 Agent（Harness 编排 + 工具调用 + HITL 人工确认）完成耗材档案校验、库存效期校验、
批次合规校验、申领/工单/追溯草稿生成，并对违规意图（绕过审批、篡改追溯、隐瞒效期）做合规拒答。

本仓库同时包含「离线可演示」与「真实大模型可接入」两套能力：离线模式下零第三方依赖即可完整跑通
演示、评测与报告导出；配置 `LLM_API_KEY` 后即可切换真实大模型，路由、HITL、合规拒答等核心逻辑不变。

## 核心能力总览

- **制度/法规问答（RAG）**：基于院内文档切片 + BM25 与向量混合检索（FAISS 优先、离线可降级为纯
  Python 暴力余弦），检索结果标注来源文件名（`【来源: xxx.md】`），对知识库确无内容的问题如实回答，
  绝不编造。
- **业务 Agent 编排（Harness）**：自带轻量 Agent 循环（模型决策 → 执行工具 → 回填上下文 → 再决策，
  最多 5 轮防死循环），注册 7 个业务工具，支持 RuleBasedPlanner（离线确定性）与 LLMPlanner（真实
  大模型）两种决策器，工具定义不变即可互换。
- **合规优先意图路由**：调用任何工具前先识别违规意图（绕过审批、篡改追溯、隐瞒效期、跳过校验等），
  一律拒绝执行，并给出合规话术与法规依据。
- **HITL 人工确认（write 类工具）**：申领草稿、质量工单、追溯记录等写类工具仅生成「草稿预览」，
  须操作员二次确认后才真正落库（`COMMITTERS` 映射），杜绝未授权变更。
- **批次缺失阻断**：申领前若批次号缺失，强制阻断草稿生成，提示补齐后再提交，落实「一物一码、批次
  可追溯」。
- **审计留痕**：`agent_execution_logs` 完整记录每轮工具调用的入参 / 耗时 / 结果摘要 / 状态 / 操作人，
  支撑质控与事后复盘。
- **报告与导出**：内置合规/追溯报告生成器（Markdown）与数据导出器（JSON / CSV），便于归档、对接与审计。
- **数据不出院**：默认私有化部署、SQLite 本地存储；离线环境下零三方依赖即可完整运行与演示。

## 目录结构

```
stage1_prototype.py   阶段1 RAG 原型：文档切片 / TF-IDF 向量 / FAISS(可降级) / BM25 / 混合检索 / LLM 网关
chat_app.py           操作员问答后端（FastAPI + SQLite + SSE 流式 + 最近6轮上下文）
admin_app.py          管理员后台（JWT + RBAC + 知识库管理 + 提示词模板 + 对话审计质控）
agent_core.py         业务 Agent 编排核心（Harness 循环 / 7 个工具 / HITL 阻断 / 执行日志）
demo_agent.py         离线演示三条典型轨迹（仅检索 / 申领落单HITL / 批次合规校验）
eval_agent.py         离线评测器（基于 qa_evaluation_cases.json，30 条用例自动打分）
data_models.py        领域数据模型与字段级校验（Consumable / Requisition / WorkOrder / TraceRecord 等）
report_generator.py   追溯/合规报告生成器（结构化 dict + Markdown 渲染）
exporter.py           业务数据导出器（JSON / CSV）
cli.py                统一命令行入口（demo / eval / init / index / report / export / serve / config）
config.py             配置管理（环境变量覆盖、离线/在线判断、安全默认值）
qa_evaluation_cases.json  30 条评测用例（rag_query / agent_tool_call / risk_intent / multi_turn_context）
docs/                 制度文档与工程文档（监管法规 / 院内SOP / 批次追溯 / 效期管理 / 不良事件 / 架构 / API / 评测报告）
templates/ static/    Web 前端（Jinja2 服务端渲染 + 原生 JS 单页）
test_*.py             离线单元测试与集成测试（零依赖可跑）
```

## 快速运行（离线 / 零三方依赖）

```bash
python demo_agent.py            # 离线演示 Agent 三条轨迹（默认 Mock LLM）
python eval_agent.py            # 运行 30 条评测用例并打出分数
python cli.py init              # 初始化数据库与知识库索引
python cli.py report            # 生成追溯/合规报告（Markdown）
python cli.py export json out.json   # 导出业务数据为 JSON
python cli.py export csv  out.csv    # 导出追溯流水宽表 CSV
python stage1_prototype.py --selftest   # RAG 自检
```

## 命令行总览（cli.py）

| 命令 | 作用 |
| --- | --- |
| `demo` | 离线演示业务 Agent 三条轨迹（检索→档案→库存→合规→申领 HITL） |
| `eval` | 运行 30 条评测用例并打印逐条结果与总分 |
| `init` | 初始化 SQLite 数据库与知识库内存索引 |
| `index` | 重建知识库内存索引（文档变更后刷新检索） |
| `report` | 生成追溯/合规报告（Markdown，可重定向到文件） |
| `export [json\|csv] [path]` | 导出业务数据为 JSON 或 CSV |
| `serve` | 启动 Web 服务（操作员问答 + 管理员后台，需安装 Web 依赖） |
| `config` | 打印当前生效配置（离线/在线、密钥状态等） |

接入真实大模型（回答质量由真实 LLM 决定，路由/HITL/拒答逻辑不变）：

```bash
export LLM_PROVIDER=openai        # 或 deepseek
export LLM_BASE_URL=https://...
export LLM_API_KEY=sk-xxx
export LLM_MODEL=xxx
python -c "import agent_core; agent_core.make_harness(use_llm_planner=True)"   # 切换 LLMPlanner
```

Web 服务（需联网安装）：`pip install fastapi uvicorn jinja2 python-multipart`，然后 `python admin_app.py`。

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

## 工程规范与测试

- **离线测试**：`test_models_offline.py`（领域模型校验）、`test_report_offline.py`（报告/导出集成）、
  `test_chat_offline.py` 与 `test_admin_offline.py`（用桩件替换 fastapi/pydantic，验证 Web 逻辑）均为
  零依赖可跑，直接 `python test_xxx.py` 即可。
- **持续集成**：`.github/workflows/ci.yml` 在 push/PR 时自动安装依赖并运行全部离线测试 + 评测。
- **构建与依赖**：`requirements.txt` 列出 Web 运行所需三方包；`pyproject.toml` 提供项目元信息与打包配置。
- **本地任务编排**：`Makefile` 提供 `install/demo/eval/init/config/test/serve/clean` 等便捷指令。
- **许可证**：采用 MIT 许可证（见 `LICENSE`）。

## 二次开发指南

- **新增一个业务工具**：在 `agent_core.build_tools()` 中新增 `Tool(...)`，实现其 `handler`，若为 write
  类（`kind="draft"`）须在 `COMMITTERS` 中提供对应的落库函数；并在 `RuleBasedPlanner.FLOWS` 中编排好
  调用顺序。
- **扩展领域模型**：在 `data_models.py` 中以 `@dataclass` 定义实体并编写 `validate()` 校验规则；数据库
  表结构变更请在 `agent_core.init_agent_db()` 的建表语句中同步。
- **接入新数据源**：`report_generator` 与 `exporter` 已与持久化解耦，仅需保证 SQLite 中存在对应表即可
  生成报告或导出，无需改动核心逻辑。

## 部署与依赖

- 离线演示/评测/报告：**无需任何第三方包**，标准 Python 3.8+ 即可。
- Web 服务：`pip install -r requirements.txt`（fastapi / uvicorn / jinja2 / python-multipart），
  然后 `python cli.py serve` 或 `python admin_app.py`。
- 嵌入与向量：默认纯 Python 离线 TF-IDF + 暴力余弦；可选 `pip install scikit-learn` 提升检索质量，
  FAISS 亦可按需接入。

## 许可证

本项目以 MIT 许可证开源，详见 `LICENSE`。
