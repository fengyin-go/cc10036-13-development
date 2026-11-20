"""交付受众领域服务：报表对象与受众身份的关联，产出按业务归属的交付受众数据。

每条交付受众记录明确「一份数据的用途」——哪位接收人、以哪个业务身份、
在哪个业务域、依据哪条数据范围规则、核对哪些销售指标与覆盖区域、
在哪个订阅期以何种只读用途交付。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.scope_filter import ScopeFilterError, compile_filter
from app.store import store


class DeliveryAudienceService(BaseModuleService):
    MODULE = "delivery_audience"
    ENTITY = "交付受众"
    OPERATION_TERM = "登记"
    LIST_FIELDS = [
        "接收人", "受众身份", "业务域", "报表对象", "数据用途",
        "范围规则ID", "覆盖区域", "核对指标", "订阅期", "只读权限",
    ]
    REQUIRED_FIELDS = ["接收人", "受众身份", "业务域", "报表对象", "范围规则ID", "订阅期"]
    STATUS_ORDER = ["待授权", "已授权", "已撤销"]
    ACTION_RULES = {"确认授权": "已授权", "撤销授权": "已撤销"}
    NEGATIVE_ACTIONS = ["撤销授权"]

    # ---- 业务方法 ----

    def list_audiences(
        self, *, recipient: str | None = None, domain: str | None = None,
        report_object: str | None = None,
    ) -> list[dict[str, Any]]:
        """按接收人 / 业务域 / 报表对象检索交付受众（默认只返回已授权）。"""
        rows = [row for row in store.rows(self.MODULE) if row.get("status") == "已授权"]
        if recipient:
            rows = [row for row in rows if row.get("接收人") == recipient]
        if domain:
            rows = [row for row in rows if row.get("业务域") == domain]
        if report_object:
            rows = [row for row in rows if row.get("报表对象") == report_object]
        return rows

    def identities_for_subscription(self, report_object: str, period: str) -> list[dict[str, Any]]:
        """某订阅（报表对象+订阅期）下全部已授权交付受众，按业务域排序保证交付确定性。"""
        rows = self.list_audiences(report_object=report_object)
        matched = [row for row in rows if str(row.get("订阅期", "")) == str(period)]
        return sorted(matched, key=lambda row: (str(row.get("业务域", "")), int(row.get("id", 0))))

    def resolve_scope_rule(self, audience: dict[str, Any]) -> dict[str, Any] | None:
        """解析受众绑定的数据范围规则，非「已生效」规则按无授权处理。"""
        try:
            rule_id = int(audience.get("范围规则ID", 0))
        except (ValueError, TypeError):
            return None
        rule = store.find("olap_scope", rule_id)
        if rule is None or rule.get("status") != "已生效":
            return None
        return rule

    def validate_audience(self, audience: dict[str, Any]) -> dict[str, Any]:
        """校验受众身份是否仍具备交付资格：授权状态、范围规则、过滤表达式可解析。"""
        authorized = audience.get("status") == "已授权"
        rule = self.resolve_scope_rule(audience) if authorized else None
        expression_valid = False
        expression_error = ""
        if rule is not None:
            try:
                compile_filter(str(rule.get("行过滤条件", "")))
                expression_valid = True
            except ScopeFilterError as exc:
                expression_error = str(exc)
        eligible = authorized and rule is not None and expression_valid
        return {
            "ok": eligible,
            "audience_id": audience.get("id"),
            "recipient": audience.get("接收人"),
            "identity": audience.get("受众身份"),
            "domain": audience.get("业务域"),
            "scope_rule": rule.get("规则名称") if rule else None,
            "reason": (
                "" if eligible
                else "受众未授权" if not authorized
                else "数据范围规则未生效" if rule is None
                else f"范围表达式无效：{expression_error}"
            ),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["artifact"] = "交付受众数据集"
        base["authorized_count"] = sum(1 for row in rows if row.get("status") == "已授权")
        base["domain_count"] = len({row.get("业务域") for row in rows if row.get("业务域")})
        return base
