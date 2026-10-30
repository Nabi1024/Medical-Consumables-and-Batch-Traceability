# -*- coding: utf-8 -*-
"""
============================================================================
业务 Agent 执行轨迹演示（离线可跑）
============================================================================
运行：python demo_agent.py
演示内容（对应需求④三条典型对话）：
  a. “一次性无菌注射器的入库验收要求是什么？” —— 仅 RAG 检索，不变更
  b. “帮我提交一批留置针耗材申领”           —— 多步工具 + 生成草稿 + 确认落单（HITL）
  c. “这批耗材批次号缺失，可以办理入库吗？” —— 批次合规校验 + 法规依据

同时把每轮工具调用写入 agent_execution_logs（需求②审计留痕），演示结束打印日志表。
============================================================================
"""
import time

import agent_core as ac


def seed_consumables():
    """插入样例耗材档案（若无），供档案/库存效期校验演示。"""
    conn = ac.db_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM consumables").fetchone()["c"] > 0:
        conn.close()
        return
    samples = [
        ("CS-001", "UDI1230001", "一次性无菌注射器", "5ml", "支", "低值耗材",
         "某医疗", "常温", 24, 5000, "active"),
        ("CS-002", "UDI1230002", "留置针", "22G", "支", "高值耗材",
         "某医疗", "常温", 36, 800, "active"),
    ]
    conn.executemany(
        "INSERT INTO consumables(id,code,udi,name,spec,unit,category,manufacturer,"
        "storage_condition,expiry_months,stock_qty,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(str(__import__("uuid").uuid4()), *s) for s in samples])
    conn.commit()
    conn.close()
    print("[种子] 已插入 2 条耗材档案（一次性无菌注射器 / 留置针）")


def print_trace(title: str, result: dict):
    print("\n" + "=" * 70)
    print(f"🎯 {title}")
    print("=" * 70)
    print(f"用户问题 → session_id={result['session_id'][:8]}")
    for step in result["trace"]:
        print(f"  ├─ [第{step['iter']}轮] 工具={step['tool']} 类型={step['kind']} "
              f"耗时={step['latency_ms']}ms 状态={step['status']}")
        print(f"  │     入参={step['input_args']}")
        print(f"  │     结果={step['result_summary']}")
    if result["need_confirm"]:
        print("  └─ ⛔ 触发 HITL 阻断：已生成草稿，需操作员确认后才落库")
        print(f"        待确认草稿={result['pending']['draft']}")
    else:
        print(f"  └─ ✅ 最终回答：{result['answer']}")


def main():
    print(">> 初始化 Agent 运行环境（建表 + 种子耗材 + 知识库装配）")
    ac.init_agent_db()
    # 演示前清空运行态表，保证每次轨迹展示干净（不影响知识库）
    conn = ac.db_conn()
    conn.execute("DELETE FROM agent_execution_logs")
    conn.execute("DELETE FROM requisitions")
    conn.execute("DELETE FROM work_orders")
    conn.execute("DELETE FROM batch_trace_records")
    conn.execute("DELETE FROM sessions")
    conn.commit()
    conn.close()
    seed_consumables()
    ac.ensure_kb_ready()
    harness = ac.make_harness()   # 默认 RuleBasedPlanner（离线演示）

    operator_id = "operator-demo"

    # ---------------- 轨迹 a：仅 RAG 检索，不变更类工具 ----------------
    ra = harness.run("一次性无菌注射器的入库验收要求是什么？", None, operator_id)
    print_trace("轨迹 a · 一次性无菌注射器入库验收要求（仅检索）", ra)

    # ---------------- 轨迹 b：申领多步 + 草稿 + 确认落单（HITL） ----------------
    slots_b = {
        "keyword": "留置针", "batch_no": "LZ2026-08", "quantity": 50,
        "expiry_date": "2027-12-31", "purpose": "内科病房常规使用", "department": "内科",
    }
    rb = harness.run("帮我提交一批留置针耗材申领", None, operator_id, slots=slots_b)
    print_trace("轨迹 b · 提交留置针申领（检索→档案→库存效期→生成草稿）", rb)
    if rb["need_confirm"]:
        print("\n  >>> 操作员复核草稿后发送确认指令：『确认提交』")
        commit_res = harness.confirm(rb["pending"], rb["session_id"], operator_id)
        print(f"  >>> ✅ 确认后落库结果：{commit_res}")

    # ---------------- 轨迹 c：批次合规校验 + 法规依据 ----------------
    slots_c = {"batch_no": "", "keyword": "医用耗材"}
    rc = harness.run("这批耗材批次号缺失，可以办理入库吗？", None, operator_id, slots=slots_c)
    print_trace("轨迹 c · 批次号缺失能否入库（批次合规校验）", rc)

    # ---------------- 展示 Agent 执行日志表（审计留痕） ----------------
    print("\n" + "=" * 70)
    print("📋 agent_execution_logs（每轮工具调用完整审计留痕）")
    print("=" * 70)
    conn = ac.db_conn()
    rows = conn.execute(
        "SELECT tool_name, status, latency_ms, result_summary, operator_id, session_id "
        "FROM agent_execution_logs ORDER BY created_at").fetchall()
    conn.close()
    for r in rows:
        print(f"  · {r['tool_name']:<22} | {r['status']:<14} | {r['latency_ms']:>4}ms | "
              f"{r['result_summary']}")


if __name__ == "__main__":
    main()
