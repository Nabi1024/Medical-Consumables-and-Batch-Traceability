# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手 · 数据导出器（Exporter）

把核心业务数据（耗材档案、批次追溯流水、申领单、质量工单）导出为
CSV / JSON，便于归档、跨系统对接与审计。零第三方依赖。

命令行用法：
    python exporter.py json ./out.json        # 导出全量 JSON
    python exporter.py csv  ./trace.csv       # 导出追溯流水宽表 CSV
    python exporter.py json ./out.json <db>   # 指定数据库路径
============================================================================
"""

import os
import sqlite3
import json
import csv
import sys
from datetime import datetime
from typing import Dict, Any, List

from data_models import (
    consumable_from_row, requisition_from_row, work_order_from_row, trace_record_from_row,
)

DEFAULT_DB = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.db"),
)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def fetch_all(db_path: str = DEFAULT_DB) -> Dict[str, List[Dict[str, Any]]]:
    """从数据库读取全部核心业务数据，返回按表名分组的 dict 列表。"""
    conn = _conn(db_path)
    try:
        data = {
            "consumables": [consumable_from_row(_row_to_dict(r)).to_dict()
                            for r in conn.execute("SELECT * FROM consumables")],
            "batch_trace_records": [trace_record_from_row(_row_to_dict(r)).to_dict()
                                    for r in conn.execute("SELECT * FROM batch_trace_records")],
            "requisitions": [requisition_from_row(_row_to_dict(r)).to_dict()
                             for r in conn.execute("SELECT * FROM requisitions")],
            "work_orders": [work_order_from_row(_row_to_dict(r)).to_dict()
                            for r in conn.execute("SELECT * FROM work_orders")],
        }
    finally:
        conn.close()
    return data


def export_json(db_path: str, out_path: str) -> int:
    """导出为 JSON，返回写入的记录总数。"""
    data = fetch_all(db_path)
    total = sum(len(v) for v in data.values())
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": db_path,
        "data": data,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return total


def export_csv(db_path: str, out_path: str) -> int:
    """导出追溯流水宽表 CSV（LEFT JOIN 耗材名），返回写入行数。"""
    conn = _conn(db_path)
    try:
        rows = conn.execute(
            "SELECT t.id, c.name AS consumable_name, c.code, t.batch_no, t.link_type, "
            "t.location, t.quantity, t.operator_id, t.event_time, t.source_system, t.created_at "
            "FROM batch_trace_records t "
            "LEFT JOIN consumables c ON t.consumable_id = c.id "
            "ORDER BY t.created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    cols = ["id", "consumable_name", "code", "batch_no", "link_type", "location",
            "quantity", "operator_id", "event_time", "source_system", "created_at"]
    # 用 utf-8-sig 保证 Excel 打开中文不乱码
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    return len(rows)


def main() -> None:
    fmt = sys.argv[1] if len(sys.argv) > 1 else "json"
    out = sys.argv[2] if len(sys.argv) > 2 else f"export.{fmt}"
    db = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_DB
    if fmt == "csv":
        n = export_csv(db, out)
    else:
        n = export_json(db, out)
    print(f"[导出] 格式={fmt} 文件={out} 记录数={n}")


if __name__ == "__main__":
    main()
