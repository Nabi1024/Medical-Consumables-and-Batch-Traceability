# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手 · 领域数据模型（Domain Models）

本模块以 Python 标准库 dataclass 定义核心业务实体，提供：
  - 字段级基础校验（validate）：如效期月份必须为正、库存不能为负、批次号必填；
  - 统一的 to_dict()：便于序列化入库 / 导出 / 接口返回；
  - 与 agent_core 的 SQLite 表结构（consumables / batch_trace_records /
    requisitions / work_orders）字段保持一致，可作为各层之间的「数据契约」。

设计原则：
  - 零第三方依赖，离线即可 import；
  - 不负责持久化（读写交给 agent_core / exporter），只承载「数据 + 规则」；
  - 所有校验错误统一以 ModelValidationError（继承自 ValueError）抛出，便于上层捕获。
============================================================================
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


class ModelValidationError(ValueError):
    """领域模型校验失败的统一异常。"""
    pass


@dataclass
class Consumable:
    """耗材档案（对应 consumables 表）。"""
    id: str
    code: str
    udi: str
    name: str
    spec: str
    unit: str
    category: str
    manufacturer: str
    storage_condition: str
    expiry_months: int
    stock_qty: int
    status: str = "active"

    def validate(self) -> None:
        """字段级合规校验，失败时抛出 ModelValidationError。"""
        if not self.name or not self.code:
            raise ModelValidationError("耗材名称(name)与编码(code)不能为空")
        if self.expiry_months is not None and self.expiry_months <= 0:
            raise ModelValidationError("效期月份(expiry_months)必须为正整数")
        if self.stock_qty is not None and self.stock_qty < 0:
            raise ModelValidationError("库存数量(stock_qty)不能为负")
        if self.status not in ("active", "inactive", "recalled"):
            raise ModelValidationError("状态(status)取值不合法：active/inactive/recalled")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BatchTraceRecord:
    """批次追溯流水（对应 batch_trace_records 表）。"""
    id: str
    consumable_id: str
    batch_no: str
    link_type: str
    location: str
    quantity: int
    operator_id: str
    event_time: str
    source_system: str
    trace_payload: str
    created_at: str

    def validate(self) -> None:
        if not self.consumable_id:
            raise ModelValidationError("追溯记录必须关联耗材(consumable_id)")
        # ★ 批次可追溯是硬性要求：任何追溯流水都必须带批次号
        if not self.batch_no:
            raise ModelValidationError("批次号(batch_no)不能为空——批次可追溯是硬性要求")
        if self.quantity is not None and self.quantity < 0:
            raise ModelValidationError("数量(quantity)不能为负")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Requisition:
    """耗材申领单（对应 requisitions 表）。"""
    id: str
    requisition_no: str
    session_id: str
    applicant_id: str
    consumable_id: str
    batch_no: str
    quantity: int
    purpose: str
    department: str
    status: str = "submitted"
    approver_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> None:
        if not self.consumable_id:
            raise ModelValidationError("申领单必须关联耗材(consumable_id)")
        if self.quantity is not None and self.quantity <= 0:
            raise ModelValidationError("申领数量(quantity)必须为正整数")
        if not self.batch_no:
            raise ModelValidationError("申领单批次号(batch_no)不能为空——批次可追溯")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualityWorkOrder:
    """质量异常工单（对应 work_orders 表）。"""
    id: str
    order_no: str
    session_id: str
    type: str
    title: str
    description: str
    consumable_id: str
    batch_no: str
    reporter_id: str
    priority: str
    status: str = "open"
    created_at: str = ""
    closed_at: str = ""

    VALID_PRIORITIES = ("low", "mid", "high", "urgent")

    def validate(self) -> None:
        if not self.title:
            raise ModelValidationError("工单标题(title)不能为空")
        if self.priority not in self.VALID_PRIORITIES:
            raise ModelValidationError(f"优先级(priority)必须属于 {self.VALID_PRIORITIES}")
        if not self.consumable_id and not self.batch_no:
            raise ModelValidationError("工单至少应关联耗材或批次号之一")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComplianceCheck:
    """单次合规校验结果（由 Agent 工具或 report_generator 产出，不持久化）。"""
    check_type: str                      # 如 batch_compliance / inventory_expiry
    target: str                          # 被校验对象（耗材名 / 批次号）
    passed: bool
    reasons: List[str] = field(default_factory=list)
    basis: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def level(self) -> str:
        return "合规" if self.passed else "不合规"


# ---------------------------------------------------------------------------
# 工厂函数：从 SQLite 行(dict)构造领域对象，并做基本字段兜底，避免 None 引发类型错误
# ---------------------------------------------------------------------------
def consumable_from_row(row: Dict[str, Any]) -> Consumable:
    return Consumable(
        id=row.get("id", ""),
        code=row.get("code", ""),
        udi=row.get("udi", ""),
        name=row.get("name", ""),
        spec=row.get("spec", ""),
        unit=row.get("unit", ""),
        category=row.get("category", ""),
        manufacturer=row.get("manufacturer", ""),
        storage_condition=row.get("storage_condition", ""),
        expiry_months=row.get("expiry_months") or 0,
        stock_qty=row.get("stock_qty") or 0,
        status=row.get("status", "active"),
    )


def requisition_from_row(row: Dict[str, Any]) -> Requisition:
    return Requisition(
        id=row.get("id", ""),
        requisition_no=row.get("requisition_no", ""),
        session_id=row.get("session_id", ""),
        applicant_id=row.get("applicant_id", ""),
        consumable_id=row.get("consumable_id", ""),
        batch_no=row.get("batch_no", ""),
        quantity=row.get("quantity") or 0,
        purpose=row.get("purpose", ""),
        department=row.get("department", ""),
        status=row.get("status", "submitted"),
        approver_id=row.get("approver_id", ""),
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


def work_order_from_row(row: Dict[str, Any]) -> QualityWorkOrder:
    return QualityWorkOrder(
        id=row.get("id", ""),
        order_no=row.get("order_no", ""),
        session_id=row.get("session_id", ""),
        type=row.get("type", "quality"),
        title=row.get("title", ""),
        description=row.get("description", ""),
        consumable_id=row.get("consumable_id", ""),
        batch_no=row.get("batch_no", ""),
        reporter_id=row.get("reporter_id", ""),
        priority=row.get("priority", "mid"),
        status=row.get("status", "open"),
        created_at=row.get("created_at", ""),
        closed_at=row.get("closed_at", ""),
    )


def trace_record_from_row(row: Dict[str, Any]) -> BatchTraceRecord:
    return BatchTraceRecord(
        id=row.get("id", ""),
        consumable_id=row.get("consumable_id", ""),
        batch_no=row.get("batch_no", ""),
        link_type=row.get("link_type", ""),
        location=row.get("location", ""),
        quantity=row.get("quantity") or 0,
        operator_id=row.get("operator_id", ""),
        event_time=row.get("event_time", ""),
        source_system=row.get("source_system", ""),
        trace_payload=row.get("trace_payload", ""),
        created_at=row.get("created_at", ""),
    )
