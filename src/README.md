# src · 医用耗材批次追溯智能助手（MVP 源码）

> 本目录为项目的**最小可行产品（MVP）源码**，按作业要求组织为四大块：
> **工程架构 / Agent 框架 / 知识库 / 问答助手归档**。

---

## 1. 目录结构与模块映射

```
src/
├── 工程架构（Engineering Architecture）
│   ├── config.py            # 配置加载（离线/在线、密钥、TOP_K、MAX_AGENT_ITERS）
│   ├── data_models.py       # 领域数据模型 + 字段级校验（耗材/申领/工单/追溯/合规）
│   ├── report_generator.py  # 合规与追溯报告生成（Markdown）
│   ├── exporter.py          # 业务数据导出 JSON / CSV（UTF-8-SIG）
│   ├── cli.py               # 统一命令行入口（demo/eval/init/config/serve/index/report/export）
│   ├── demo_agent.py        # 演示数据播种（耗材档案/批次流水种子）
│   └── eval_agent.py        # 30 条离线评测器（工具路由/合规拒答/无内容陷阱）
│
├── Agent 框架（Agent Framework）
│   ├── agent_core.py        # ★ Agent Harness：意图路由 + 工具编排 + HITL + 审计
│   │                        #   含 RuleBasedPlanner / LLMPlanner 双规划器、7 项业务工具、
│   │                        #   合规优先路由、agent_execution_logs 审计留痕
│   └── stage1_prototype.py  # LLM / RAG 底座：BM25+向量混合检索、离线 TF-IDF、Mock LLM
│
├── 知识库（Knowledge Base）
│   └── docs/                # RAG 摄取源（启动时切片→嵌入→写入 kb_chunks）
│       ├── 01_医疗器械监管法规.md
│       ├── 02_院内耗材SOP.md
│       ├── 03_批次追溯规范.md
│       ├── 04_效期管理要求.md
│       ├── 05_不良事件上报规范.md
│       ├── 06_系统架构设计.md
│       ├── 07_API接口文档.md
│       └── 08_评测报告.md
│
├── 问答助手归档（QA Assistant & Archiving）
│   ├── chat_app.py          # 操作员问答 Web（FastAPI + 原生 JS），会话/消息持久化归档
│   └── admin_app.py         # 管理员后台（档案/申领/工单/追溯/审计，Jinja2 服务端渲染）
│
├── 测试（Tests，零依赖可跑）
│   ├── test_models_offline.py   # 领域模型校验（9 用例）
│   ├── test_report_offline.py   # 报告/导出集成（11 用例）
│   ├── test_chat_offline.py     # 问答离线链路（建库/检索/上下文/持久化）
│   └── test_admin_offline.py    # 后台逻辑（19 用例）
│
└── qa_evaluation_cases.json # 30 条评测用例（rag_query/agent_tool_call/risk_intent/multi_turn）
```

> 资源说明：`templates/`（服务端渲染模板）与 `static/`（前端单页）随 `chat_app.py` /
> `admin_app.py` 一并置于 `src/` 内，由模块按 `__file__` 自动定位，迁移目录不影响运行。

---

## 2. 运行方式（离线零依赖核心）

在**项目根目录**执行（脚本所在目录会自动加入路径，无需安装任何三方包）：

```bash
# 1) 初始化数据库 + 知识库索引
python src/cli.py init

# 2) 离线演示业务 Agent 三条轨迹（检索→档案→效期→批次合规→申领HITL→确认落单）
python src/cli.py demo

# 3) 运行 30 条评测，输出逐条与总分
python src/cli.py eval

# 4) 生成合规/追溯报告
python src/cli.py report

# 5) 导出业务数据为 JSON / CSV
python src/cli.py export json out.json
python src/cli.py export csv  out.csv
```

### Web 问答助手（问答助手归档，需装 Web 依赖）
```bash
pip install fastapi uvicorn jinja2 python-multipart
python src/cli.py serve        # 启动后访问 http://127.0.0.1:8000
```
> 离线沙箱若无法联网安装，核心能力（RAG + Agent + 评测 + 全部单测）仍可零依赖跑通。

---

## 3. 测试结论（本项目已验证可运行）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 离线评测 | `python src/cli.py eval` | **30/30 (100%)** |
| 模型单测 | `python src/test_models_offline.py` | 9 PASS / 0 FAIL |
| 报告/导出 | `python src/test_report_offline.py` | 11 PASS / 0 FAIL |
| 问答离线链路 | `python src/test_chat_offline.py` | 全部通过 |
| 后台逻辑 | `python src/test_admin_offline.py` | 19 PASS / 0 FAIL |
| 端到端 demo | `python src/cli.py demo` | 三轨迹 + 审计链正常 |
| 语法编译 | `python -m py_compile src/*.py` | 全部通过 |

---

## 4. 设计与需求对应关系
详见项目根目录《需求分析方案》《项目设计方案》《项目报价方案》。本 MVP 覆盖：
- REQ-1 统一库存口径 → `check_inventory_expiry` + 数据模型
- REQ-2 批次合规 → `check_batch_compliance`（越界拒答 + 无内容话术）
- REQ-3 主数据映射 → `data_models.validate`
- REQ-4 申领草稿与人工交接 → `draft_requisition` + HITL `pending`/`commit`
- REQ-5 效期/接口监控 → `check_inventory_expiry` + `report_generator`
- REQ-6 验收分母与关联 → `agent_execution_logs` 审计 + `exporter`
