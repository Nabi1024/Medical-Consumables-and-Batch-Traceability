# 五类场景量化测试（tests_scene）

本目录包含医用耗材批次追溯 MVP 的五类场景量化测试全部资产：
测试方案、真实执行脚本、可视化图表生成脚本、五份场景测试报告（Markdown 版，
docx 版见项目交付物）。

## 场景覆盖与结果（2026-11-06 实测）

| 场景 | 用例数 | 通过 | 通过率 | 关键红线 |
|---|---|---|---|---|
| 正常 normal | 12 | 12 | 100% | 评测集准确率 ≥ 28/30 |
| 边界 boundary | 15 | 14 | 93.3% | 语义正确率 ≥ 90% |
| 对抗 adversarial | 13 | 13 | 100% | 越界/注入拦截率 100% |
| 权限 permission | 18 | 18 | 100% | RBAC 拦截率 100% |
| 故障 fault | 9 | 9 | 100% | 隔离实例健康恢复 |
| **合计** | **67** | **66** | **98.5%** | — |

唯一未过项 B-10（知识库外问题拒答缺口）已记录为 P2 改进项。

## 运行方式

```bash
# 前置：问答助手服务运行中（默认 http://127.0.0.1:8001，可用 --base 指定）
# 管理后台运行中（默认 http://127.0.0.1:8002）
python run_scene_tests.py normal        # 正常场景
python run_scene_tests.py boundary      # 边界场景
python run_scene_tests.py adversarial   # 对抗场景
python run_scene_tests.py permission    # 权限场景
python run_scene_tests.py fault         # 故障场景

# 汇总图表生成（输出到 images/）
python gen_images.py
```

结果 JSON 落盘到 `results/`（自动创建），可直接溯源每条用例的
预期、实际输出、耗时。

## 文件说明

- `测试方案.md` — 量化测试方案（场景定义、用例清单、红线指标）
- `run_scene_tests.py` — 真实测试执行器（HTTP 层无代理直连 + SSE 解析 +
  agent_core 进程内测试 + 故障注入）
- `gen_images.py` — Pillow 可视化图表生成（终端截图风/流程图/条形统计图）
- `*_场景测试.md` — 五份场景测试过程报告
- `images/summary_all.png` — 五场景通过率汇总图
