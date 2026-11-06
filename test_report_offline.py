# -*- coding: utf-8 -*-
"""离线集成测试：report_generator + exporter 对真实 schema 的读取/导出。运行：python test_report_offline.py"""
import os
import sys
import json
import tempfile

# ★ 在导入任何业务模块前锁定临时库，避免污染真实的 admin.db
_TMP = tempfile.mkdtemp(prefix="report_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "report_test.db")

import agent_core  # noqa: E402  (import 顺序受临时库路径约束)
import report_generator  # noqa: E402
import exporter  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  [PASS] {name}")
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}")


def seed():
    """建表并灌入样例数据（含一条缺批次号的追溯流水，用于验证核查逻辑）。"""
    agent_core.init_agent_db()
    conn = agent_core.db_conn()
    conn.execute(
        "INSERT INTO consumables(id,code,udi,name,spec,unit,category,manufacturer,"
        "storage_condition,expiry_months,stock_qty,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c1", "CS-1", "U1", "一次性无菌注射器", "5ml", "支", "低值", "某厂", "常温", 24, 10, "active"))
    conn.execute(
        "INSERT INTO consumables(id,code,udi,name,spec,unit,category,manufacturer,"
        "storage_condition,expiry_months,stock_qty,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("c2", "CS-2", "U2", "留置针", "22G", "支", "高值", "某厂", "常温", 36, 0, "active"))
    conn.execute(
        "INSERT INTO batch_trace_records(id,consumable_id,batch_no,link_type,location,quantity,"
        "operator_id,event_time,source_system,trace_payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("t1", "c1", "B2026", "inbound", "库房", 10, "op", "2026-01-01", "agent", "{}", "2026-01-01 00:00:00"))
    conn.execute(
        "INSERT INTO batch_trace_records(id,consumable_id,batch_no,link_type,location,quantity,"
        "operator_id,event_time,source_system,trace_payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("t2", "c2", "", "inbound", "库房", 5, "op", "2026-01-02", "agent", "{}", "2026-01-02 00:00:00"))
    conn.execute(
        "INSERT INTO requisitions(id,requisition_no,session_id,applicant_id,consumable_id,batch_no,"
        "quantity,purpose,department,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("rq1", "RQ1", "s", "op", "c1", "B2026", 10, "用途", "内科", "submitted",
         "2026-01-03 00:00:00", "2026-01-03 00:00:00"))
    conn.execute(
        "INSERT INTO work_orders(id,order_no,session_id,type,title,description,consumable_id,"
        "batch_no,reporter_id,priority,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("w1", "WO1", "s", "quality", "质量异常", "desc", "c1", "B2026", "op", "mid", "open",
         "2026-01-04 00:00:00"))
    conn.commit()
    conn.close()


def run():
    seed()
    db = os.environ["DB_PATH"]

    # —— 报告生成 ——
    rep = report_generator.generate(db)
    s = rep["summary"]
    check("报告统计-耗材=2", s["consumables"] == 2)
    check("报告统计-追溯=2", s["batch_trace_records"] == 2)
    check("报告统计-申领=1", s["requisitions"] == 1)
    check("报告统计-工单=1", s["work_orders"] == 1)
    check("库存预警包含断供项", any("断供" in w for w in rep["stock_warnings"]))
    check("可追溯性核查发现缺批号", not rep["compliance_checks"][1]["passed"])
    md = report_generator.to_markdown(rep)
    check("Markdown 渲染含标题", "# 医用耗材" in md)

    # —— 导出 JSON ——
    jp = os.path.join(_TMP, "out.json")
    n = exporter.export_json(db, jp)
    check("JSON 导出记录总数=6", n == 6)
    with open(jp, encoding="utf-8") as f:
        obj = json.load(f)
    check("JSON 结构含 data.consumables", "data" in obj and "consumables" in obj["data"])

    # —— 导出 CSV ——
    cp = os.path.join(_TMP, "out.csv")
    m = exporter.export_csv(db, cp)
    check("CSV 导出追溯行=2", m == 2)
    with open(cp, encoding="utf-8-sig") as f:
        header = f.readline().strip()
    check("CSV 含表头 consumable_name", "consumable_name" in header)


if __name__ == "__main__":
    run()
    print(f"\n结果：{_PASS} PASS / {_FAIL} FAIL")
    sys.exit(1 if _FAIL else 0)
