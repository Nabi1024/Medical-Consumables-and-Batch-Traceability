# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手 · 追溯/合规报告生成器（Report Generator）

读取 agent_core 共享的 SQLite（admin.db）中五类业务表，产出：
  - 结构化报告 dict（供接口 / 前端消费）；
  - Markdown 文本报告（供人工查阅、归档、导出）。

报告内容：
  1) 概览：耗材数、在库、批次追溯流水数、申领单数、工单数、Agent 执行日志数；
  2) 库存与效期风险提示（stock_qty=0 视为断供预警）；
  3) 批次可追溯性核查（是否存在缺批次号的追溯/申领记录）；
  4) 近期业务活动（最近 N 条申领 / 工单 / 追溯）。

离线零依赖：仅标准库；数据库路径可通过参数或 DB_PATH 环境变量覆盖。
============================================================================
"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, Any, List

from data_models import (
    consumable_from_row, requisition_from_row, work_order_from_row,
    trace_record_from_row, ComplianceCheck,
)

DEFAULT_DB = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.db"),
)


def _conn(db_path: str) -> sqlite3.Connection:
    """短生命周期只读连接。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def generate(db_path: str = DEFAULT_DB) -> Dict[str, Any]:
    """扫描业务库，生成结构化追溯/合规报告。"""
    conn = _conn(db_path)
    try:
        consumables = [consumable_from_row(_row_to_dict(r))
                       for r in conn.execute("SELECT * FROM consumables")]
        traces = [trace_record_from_row(_row_to_dict(r))
                  for r in conn.execute("SELECT * FROM batch_trace_records ORDER BY created_at DESC")]
        requisitions = [requisition_from_row(_row_to_dict(r))
                        for r in conn.execute("SELECT * FROM requisitions ORDER BY created_at DESC")]
        work_orders = [work_order_from_row(_row_to_dict(r))
                       for r in conn.execute("SELECT * FROM work_orders ORDER BY created_at DESC")]
        logs = conn.execute("SELECT COUNT(*) AS c FROM agent_execution_logs").fetchone()["c"]
    finally:
        conn.close()

    # —— 库存 / 效期预警 ——
    stock_warnings = []
    for c in consumables:
        if (c.stock_qty or 0) <= 0:
            stock_warnings.append(f"{c.name}({c.code}) 库存为 0，存在断供风险")

    # —— 批次可追溯性核查 ——
    traceability_issues = []
    for t in traces:
        if not t.batch_no:
            traceability_issues.append(f"追溯流水 {t.id[:8]} 缺失批次号")
    for rq in requisitions:
        if not rq.batch_no:
            traceability_issues.append(f"申领单 {rq.requisition_no} 缺失批次号")

    checks: List[ComplianceCheck] = [
        ComplianceCheck(
            check_type="stock_safety", target="库存安全",
            passed=len(stock_warnings) == 0,
            reasons=stock_warnings or ["所有耗材库存充足，无断供预警"],
        ),
        ComplianceCheck(
            check_type="batch_traceability", target="批次可追溯",
            passed=len(traceability_issues) == 0,
            reasons=traceability_issues or ["全部追溯/申领记录均含批次号，满足一物一码"],
        ),
    ]

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": db_path,
        "summary": {
            "consumables": len(consumables),
            "batch_trace_records": len(traces),
            "requisitions": len(requisitions),
            "work_orders": len(work_orders),
            "agent_execution_logs": logs,
        },
        "compliance_checks": [c.to_dict() for c in checks],
        "stock_warnings": stock_warnings,
        "traceability_issues": traceability_issues,
        "recent_requisitions": [r.to_dict() for r in requisitions[:10]],
        "recent_work_orders": [w.to_dict() for w in work_orders[:10]],
    }
    return report


def to_markdown(report: Dict[str, Any]) -> str:
    """把结构化报告渲染为 Markdown 文本。"""
    s = report["summary"]
    lines: List[str] = []
    lines.append("# 医用耗材批次追溯 · 合规与追溯报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 数据源：{report['db_path']}")
    lines.append("")
    lines.append("## 一、数据概览")
    lines.append("")
    lines.append(f"- 耗材档案：{s['consumables']} 条")
    lines.append(f"- 批次追溯流水：{s['batch_trace_records']} 条")
    lines.append(f"- 耗材申领单：{s['requisitions']} 条")
    lines.append(f"- 质量异常工单：{s['work_orders']} 条")
    lines.append(f"- Agent 执行审计日志：{s['agent_execution_logs']} 条")
    lines.append("")
    lines.append("## 二、合规核查")
    lines.append("")
    for c in report["compliance_checks"]:
        icon = "✅" if c["passed"] else "⚠️"
        level = "合规" if c["passed"] else "不合规"
        lines.append(f"### {icon} {c['check_type']}（{level}）")
        for r in c["reasons"]:
            lines.append(f"- {r}")
        lines.append("")
    lines.append("## 三、近期业务活动")
    lines.append("")
    if report["recent_requisitions"]:
        lines.append("### 最近申领单")
        for r in report["recent_requisitions"][:5]:
            lines.append(f"- {r['requisition_no']} | {r['department']} | "
                         f"数量 {r['quantity']} | 状态 {r['status']}")
    else:
        lines.append("- 暂无申领记录")
    lines.append("")
    if report["recent_work_orders"]:
        lines.append("### 最近质量工单")
        for w in report["recent_work_orders"][:5]:
            lines.append(f"- {w['order_no']} | {w['title']} | "
                         f"优先级 {w['priority']} | 状态 {w['status']}")
    else:
        lines.append("- 暂无工单记录")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    print(to_markdown(generate(db)))


if __name__ == "__main__":
    main()
