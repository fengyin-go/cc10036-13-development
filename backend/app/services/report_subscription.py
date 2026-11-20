"""报表订阅领域服务：订阅配置、推送管理、看板导出链消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ReportSubscriptionService(BaseModuleService):
    MODULE = "report_subscription"
    ENTITY = "订阅配置"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["订阅名称", "报表对象", "推送周期", "推送渠道", "接收人", "推送格式", "最近推送时间"]
    REQUIRED_FIELDS = ["订阅名称", "报表对象", "推送周期"]
    STATUS_ORDER = ["草稿", "已启用", "已暂停", "已停用"]
    ACTION_RULES = {"启用订阅": "已启用", "暂停订阅": "已暂停", "停用订阅": "已停用"}
    NEGATIVE_ACTIONS = ["停用订阅"]

    # ---- 业务方法 ----

    def get_active_subscriptions(self) -> list[dict[str, Any]]:
        """获取所有已启用订阅。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "已启用"
        ]

    def get_subscriptions_for_dashboard(self, dashboard_name: str) -> list[dict[str, Any]]:
        """获取某看板的所有订阅配置（看板布局→报表订阅消费）。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("报表对象") == dashboard_name
        ]

    def simulate_push(self, subscription_id: int) -> dict[str, Any]:
        """模拟推送订阅内容。

        串联交付链路：按订阅绑定的交付受众（业务归属+受众身份）生成只读业务报表，
        同域同人本期并发只记一次交付，并回写最近推送时间。
        """
        sub = store.find(self.MODULE, subscription_id)
        if sub is None:
            return {"ok": False, "message": f"订阅 {subscription_id} 不存在"}

        # 延迟导入，避免服务包初始化阶段的循环依赖。
        from app.services.delivery import DeliveryOrchestrator

        result = DeliveryOrchestrator().push_subscription(subscription_id)
        if not result.get("ok"):
            return result

        sub["最近推送时间"] = result.get("push_time")
        # 兼容旧调用方的扁平字段，同时保留完整交付清单。
        recipients = str(sub.get("接收人", "")).split(",")
        result.update({
            "subscription_name": sub.get("订阅名称"),
            "report_object": sub.get("报表对象"),
            "channel": sub.get("推送渠道", "邮件"),
            "recipient_count": len([r for r in recipients if r.strip()]),
            "recipients": [r.strip() for r in recipients if r.strip()][:3],
            "format": sub.get("推送格式"),
        })
        return result

    def bulk_toggle(self, subscription_ids: list[int], action: str) -> dict[str, Any]:
        """批量启停订阅。"""
        results: list[dict[str, Any]] = []
        for sid in subscription_ids:
            entry, msg = self.run_action(sid, action)
            results.append({"id": sid, "ok": entry is not None, "message": msg})
        return {
            "ok": True,
            "total": len(subscription_ids),
            "results": results,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["artifact"] = "报表订阅数据集"
        base["active_count"] = sum(1 for row in rows if row.get("status") == "已启用")
        return base