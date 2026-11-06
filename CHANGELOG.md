# 更新日志（CHANGELOG）

本项目采用「提交即记录」的轻量版本管理，以下按里程碑汇总关键变更。

## v1.0.0 · 核心能力定型

- 阶段1 RAG 原型 `stage1_prototype.py`：文档切片 / TF-IDF 向量 / FAISS(可降级) / BM25 / 混合检索 / LLM 网关。
- 业务 Agent 编排核心 `agent_core.py`：Harness 循环、7 个业务工具、HITL 强制阻断、执行日志审计。
- 合规优先意图路由：违规意图一律拒答；批次缺失强制阻断申领草稿。
- Web 两端：`chat_app.py`（操作员问答）、`admin_app.py`（管理员后台 JWT+RBAC+知识库+提示词+质控）。
- 离线评测器 `eval_agent.py`：30 条用例，离线评分 30/30 (100%)。
- 文档与工程规范：8 份 docs、LICENSE(MIT)、Makefile、requirements、.gitignore、2 份测试。

## v1.1.0 · 数据契约与可观测性增强

- 新增 `data_models.py`：以 dataclass 定义核心领域实体（Consumable / Requisition / QualityWorkOrder /
  BatchTraceRecord / ComplianceCheck），提供字段级校验与「行→对象」工厂函数，作为各层数据契约。
- 新增 `report_generator.py`：扫描业务库生成结构化追溯/合规报告（数据概览、库存预警、批次可追溯性
  核查、近期活动），并渲染为 Markdown。
- 新增 `exporter.py`：将核心业务数据导出为 JSON / CSV（CSV 含耗材名宽表，utf-8-sig 兼容 Excel）。
- `cli.py` 新增 `index` / `report` / `export` 子命令，统一命令行入口。
- 新增 `test_models_offline.py` 与 `test_report_offline.py`，扩充离线测试覆盖。

## v1.2.0 · 工程规范与 CI

- 新增 `.github/workflows/ci.yml`：push / PR 时自动跑全部离线测试 + 评测。
- 新增 `pyproject.toml`：项目元信息与打包配置，暴露 `mcb-agent` 入口。
- 新增 `CONTRIBUTING.md` 与本文档，完善协作与变更记录规范。
- README 扩写至完整版，新增能力总览、命令行总览、工程规范、二次开发指南等章节。
