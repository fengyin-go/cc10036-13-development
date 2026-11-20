"""交付编排服务：串联报表订阅与即席导出，按交付受众数据生成只读业务报表。

链路：
    报表订阅 → 交付受众（业务归属/身份/范围）→ 数据范围规则过滤 →
    即席导出构建带分域合计的只读报表 → 交付收据幂等记账（同域同人本期一次）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.adhoc_export import AdhocExportService
from app.services.delivery_audience import DeliveryAudienceService
from app.services.delivery_receipt import DeliveryReceiptService
from app.store import store

class DeliveryOrchestrator:
    def __init__(self) -> None:
        self.audience_svc = DeliveryAudienceService()
        self.receipt_svc = DeliveryReceiptService()
        self.export_svc = AdhocExportService()

    # ---- 单受众交付 ----

    def deliver_for_audience(
        self,
        audience: dict[str, Any],
        *,
        period: str | None = None,
        push_time: str | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """为单个交付受众生成报表并记账；同域同人本期并发只记一次。"""
        period = str(period or audience.get("订阅期", ""))
        push_time = push_time or _now()

        validation = self.audience_svc.validate_audience(audience)
        if not validation["ok"]:
            return {
                "ok": False,
                "audience_id": audience.get("id"),
                "recipient": audience.get("接收人"),
                "identity": audience.get("受众身份"),
                "domain": audience.get("业务域"),
                "reason": validation["reason"],
            }

        recipient = str(audience.get("接收人", ""))
        domain = str(audience.get("业务域", ""))
        existing = self.receipt_svc.find_current_period(period, domain, recipient)
        if existing is not None:
            # 同域同人本期已交付：复用既有导出文件与收据，不重复生成数据。
            existing["并发拦截次数"] = int(existing.get("并发拦截次数", 0)) + 1
            return {
                "ok": True,
                "deduplicated": True,
                "audience_id": audience.get("id"),
                "receipt_id": existing.get("id"),
                "recipient": recipient,
                "identity": audience.get("受众身份"),
                "domain": domain,
                "period": period,
                "file_name": existing.get("文件名称"),
                "data_row_count": existing.get("数据行数"),
                "domain_totals": _decode_totals(existing.get("分域合计")),
                "message": "命中本期交付幂等键，复用既有交付，不重复记账",
            }

        scope_rule = self.audience_svc.resolve_scope_rule(audience)
        source_rows = [
            dict(row) for row in store.rows("sales_fact")
            if str(row.get("业务域", "")) == domain
        ]

        task = self.export_svc.register_delivery_task(audience, period)
        report = self.export_svc.build_delivery_report(
            int(task["id"]), audience, scope_rule, source_rows,
        )
        if not report.get("ok"):
            return report

        receipt, created = self.receipt_svc.record_once({
            "订阅期": period,
            "业务域": domain,
            "接收人": recipient,
            "受众身份": audience.get("受众身份", ""),
            "报表对象": audience.get("报表对象", ""),
            "导出任务ID": report["task_id"],
            "文件名称": report["file_name"],
            "数据行数": report["data_row_count"],
            "分域合计": report["domain_totals"],
            "权限范围": f"{scope_rule.get('规则名称')}|{scope_rule.get('行过滤条件', '')}",
            "推送时间": push_time,
            "推送渠道": channel or "邮件",
        })
        return {
            "ok": True,
            "deduplicated": not created,
            "audience_id": audience.get("id"),
            "receipt_id": receipt.get("id"),
            "export_task_id": report["task_id"],
            "recipient": recipient,
            "identity": audience.get("受众身份"),
            "domain": domain,
            "period": period,
            "file_name": report["file_name"],
            "file_path": report["file_path"],
            "checksum_md5": report["checksum_md5"],
            "data_row_count": report["data_row_count"],
            "domain_totals": report["domain_totals"],
            "scope_rule": report["scope_rule"],
            "readonly": report["readonly"],
            "push_time": push_time,
        }

    # ---- 订阅级模拟推送 ----

    def push_subscription(self, subscription_id: int) -> dict[str, Any]:
        """运行一次订阅模拟推送：为该订阅下全部已授权交付受众生成交付数据。"""
        sub = store.find("report_subscription", subscription_id)
        if sub is None:
            return {"ok": False, "message": f"订阅 {subscription_id} 不存在"}

        report_object = str(sub.get("报表对象", ""))
        period = _period_of(sub)
        push_time = str(sub.get("最近推送时间", "")) or _now()
        channel = str(sub.get("推送渠道", "邮件"))

        audiences = self.audience_svc.identities_for_subscription(report_object, period)
        deliveries = [
            self.deliver_for_audience(
                audience, period=period, push_time=push_time, channel=channel,
            )
            for audience in audiences
        ]

        succeeded = [item for item in deliveries if item.get("ok")]
        receipts = self.receipt_svc.receipts_for_subscription(report_object, period)
        domain_totals = {
            str(item.get("domain")): item.get("domain_totals")
            for item in succeeded if item.get("domain_totals")
        }
        return {
            "ok": True,
            "subscription_id": subscription_id,
            "subscription_name": sub.get("订阅名称"),
            "report_object": report_object,
            "period": period,
            "push_time": push_time,
            "channel": channel,
            "audience_count": len(audiences),
            "delivery_count": len(succeeded),
            "deduplicated_count": sum(1 for item in succeeded if item.get("deduplicated")),
            "receipt_count": len(receipts),
            "domain_totals": domain_totals,
            "deliveries": deliveries,
            "receipts": receipts,
            "message": (
                f"模拟推送完成：{len(succeeded)} 份交付（含 "
                f"{sum(1 for item in succeeded if item.get('deduplicated'))} 次幂等复用），"
                f"本期收据 {len(receipts)} 条"
            ),
        }


def _period_of(subscription: dict[str, Any]) -> str:
    """从订阅配置推导订阅期；新增订阅可显式携带「订阅期」字段，缺省用当前月份。"""
    explicit = subscription.get("订阅期")
    if explicit:
        return str(explicit)
    now = datetime.now()
    return f"{now.year}-{now.month:02d}"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _decode_totals(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        import json
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return {}
