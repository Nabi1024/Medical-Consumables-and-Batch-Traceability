# 贡献指南（CONTRIBUTING）

感谢你关注「医用耗材批次追溯智能助手」项目。本项目遵循「离线可演示、合规优先、可审计」的设计理念，
欢迎以 Issue / Pull Request 形式参与贡献。

## 开发环境

- 推荐 Python 3.8+。
- 离线演示、评测、报告、导出**无需任何第三方包**，开箱即跑。
- 仅需 Web 服务时：`pip install -r requirements.txt`（fastapi / uvicorn / jinja2 / python-multipart）。

## 本地验证（提交前必跑）

```bash
python test_models_offline.py     # 领域模型校验单测
python test_report_offline.py     # 报告/导出集成测试
python test_chat_offline.py       # 操作员端 Web 逻辑（桩件隔离）
python test_admin_offline.py      # 管理员端 Web 逻辑（桩件隔离）
python cli.py eval                # 30 条评测用例，目标 30/30
```

CI（`.github/workflows/ci.yml`）会在 push / PR 时自动复跑上述用例。

## 提交规范

- 提交信息建议遵循 `type: 简述` 格式，type 取值：`feat` / `fix` / `docs` / `test` / `refactor` / `chore`。
- 一次提交聚焦一个逻辑改动，便于评审与回溯。
- 不要在提交中携带运行时产物：`*.db`、`__pycache__/`、`.workbuddy/`、`uploads/` 已被 `.gitignore` 忽略。

## 代码风格

- 全部源码与文档使用简体中文注释，保持与现有模块一致的风格。
- 新增业务逻辑优先复用 `agent_core` 的工具与 `data_models` 的领域模型，避免重复造轮子。
- 新增写类（变更类）能力必须接入 HITL 人工确认（`draft` 工具 + `COMMITTERS`），并写入执行日志。

## 新增业务工具的流程

1. 在 `agent_core.build_tools()` 注册 `Tool`（含 JSON Schema 参数与 `kind`）。
2. 实现 `handler`；若为写类，提供对应的落库函数并登记到 `COMMITTERS`。
3. 在 `RuleBasedPlanner.FLOWS` 中编排调用顺序（合规优先）。
4. 如涉及新实体，先在 `data_models.py` 定义并在 `agent_core.init_agent_db()` 同步建表。

## 行为准则

- 围绕「合规、可追溯、可审计」展开，任何可能弱化管控或隐瞒留痕的改动都不被接受。
- 讨论请保持专业与友善。
