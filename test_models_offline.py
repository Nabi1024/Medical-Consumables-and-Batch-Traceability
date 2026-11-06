# -*- coding: utf-8 -*-
"""离线单元测试：领域数据模型 data_models 的字段校验与工厂函数。运行：python test_models_offline.py"""
import sys

from data_models import (
    Consumable, Requisition, QualityWorkOrder, BatchTraceRecord,
    consumable_from_row, requisition_from_row, work_order_from_row,
    trace_record_from_row, ModelValidationError,
)

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


def run():
    # 合法耗材应通过校验
    c = Consumable("id1", "CS-1", "UDI1", "注射器", "5ml", "支", "低值", "某厂", "常温", 24, 100)
    try:
        c.validate()
        check("合法耗材校验通过", True)
    except Exception:
        check("合法耗材校验通过", False)

    # 空名称/编码应被拦截
    try:
        Consumable("id2", "", "", "", "", "", "", "", "", 1, 0).validate()
        check("空名称校验被拦截", False)
    except ModelValidationError:
        check("空名称校验被拦截", True)

    # 负库存应被拦截
    try:
        Consumable("id3", "C", "N", "x", "", "", "", "", "", 1, -5).validate()
        check("负库存校验被拦截", False)
    except ModelValidationError:
        check("负库存校验被拦截", True)

    # 申领缺批次号应被拦截（批次可追溯是硬性要求）
    try:
        Requisition("r1", "RQ1", "s", "op", "cid", "", 1, "", "dep").validate()
        check("申领缺批次号被拦截", False)
    except ModelValidationError:
        check("申领缺批次号被拦截", True)

    # 工单缺关联（耗材与批次号皆空）应被拦截
    try:
        QualityWorkOrder("w1", "WO1", "s", "quality", "t", "", "", "", "", "mid").validate()
        check("工单缺关联被拦截", False)
    except ModelValidationError:
        check("工单缺关联被拦截", True)

    # 工厂函数对缺失字段的兜底
    check("consumable_from_row 兜底字段",
          consumable_from_row({"id": "x"}).name == "" and consumable_from_row({"id": "x"}).stock_qty == 0)
    check("requisition_from_row 兜底 quantity",
          requisition_from_row({"id": "r", "quantity": None}).quantity == 0)
    check("work_order_from_row 兜底 priority",
          work_order_from_row({}).priority == "mid")
    check("trace_record_from_row 兜底 batch_no",
          trace_record_from_row({"id": "t"}).batch_no == "")


if __name__ == "__main__":
    run()
    print(f"\n结果：{_PASS} PASS / {_FAIL} FAIL")
    sys.exit(1 if _FAIL else 0)
