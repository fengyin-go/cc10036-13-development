"""交付收据领域服务：记录每一次订阅期交付，提供本期幂等去重。

去重键 = 订阅期 + 业务域 + 接收人。同一域的同一人在同一订阅期并发生成时，
只有首个请求落账，其余请求直接取回已存在的本期收据，保证「只记一次本期交付」。
"""
from __future__ import annotations

import threading
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DeliveryReceiptService(BaseModuleService):
    MODULE = "delivery_receipt"
    ENTITY = "交付收据"
    OPERATION_TERM = "记账"
    LIST_FIELDS = [
        "订阅期", "业务域", "接收人", "受众身份", "报表对象",
        "导出任务ID", "文件名称", "数据行数", "分域合计", "权限范围", "推送时间", "推送渠道",
    ]
    REQUIRED_FIELDS = ["订阅期", "业务域", "接收人"]
    STATUS_ORDER = ["已交付", "未授权", "已撤销"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    def __init__(self) -> None:
        super().__init__()
        # 进程内交付锁：串行化「查重→记账」临界区，模拟并发推送下的幂等语义。
        self._ledger_lock = threading.Lock()

    # ---- 业务方法 ----

    @staticmethod
    def dedup_key(period: str, domain: str, recipient: str) -> str:
        return f"{period}::{domain}::{recipient}"

    def find_current_period(
        self, period: str, domain: str, recipient: str,
    ) -> dict[str, Any] | None:
        """查询本期是否已存在交付收据。"""
        target = self.dedup_key(period, domain, recipient)
        for row in store.rows(self.MODULE):
            key = self.dedup_key(
                str(row.get("订阅期", "")), str(row.get("业务域", "")), str(row.get("接收人", "")),
            )
            if key == target:
                return row
        return None

    def record_once(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """幂等记账。返回 (收据, 是否本次新建)；并发重复请求返回旧收据与 False。"""
        period = str(payload.get("订阅期", ""))
        domain = str(payload.get("业务域", ""))
        recipient = str(payload.get("接收人", ""))
        with self._ledger_lock:
            existing = self.find_current_period(period, domain, recipient)
            if existing is not None:
                existing["并发拦截次数"] = int(existing.get("并发拦截次数", 0)) + 1
                return existing, False

            rows = store.rows(self.MODULE)
            receipt = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            receipt.update({field: payload.get(field, "") for field in self.LIST_FIELDS})
            receipt["去重键"] = self.dedup_key(period, domain, recipient)
            receipt["并发拦截次数"] = 0
            receipt["status"] = "已交付"
            receipt["pending"] = False
            receipt["abnormal"] = False
            rows.append(receipt)
            return receipt, True

    def receipts_for_subscription(self, report_object: str, period: str) -> list[dict[str, Any]]:
        """某订阅本期的全部收据。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("报表对象") == report_object
            and str(row.get("订阅期", "")) == str(period)
        ]

    def period_summary(self, period: str) -> dict[str, Any]:
        """订阅期交付汇总，用于推送后核对。"""
        rows = [row for row in store.rows(self.MODULE) if str(row.get("订阅期", "")) == period]
        by_domain: dict[str, int] = {}
        for row in rows:
            domain = str(row.get("业务域", "未分域"))
            by_domain[domain] = by_domain.get(domain, 0) + 1
        return {
            "period": period,
            "receipt_count": len(rows),
            "by_domain": by_domain,
            "dedup_hits": sum(int(row.get("并发拦截次数", 0)) for row in rows),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "交付收据数据集"
        base["delivered_count"] = len(store.rows(self.MODULE))
        return base
