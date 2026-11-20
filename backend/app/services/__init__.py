"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.metric_def import MetricDefService
from app.services.cube_builder import CubeBuilderService
from app.services.slice_query import SliceQueryService
from app.services.segment_rule import SegmentRuleService
from app.services.dashboard_layout import DashboardLayoutService
from app.services.adhoc_export import AdhocExportService
from app.services.report_subscription import ReportSubscriptionService
from app.services.anomaly_detect import AnomalyDetectService
from app.services.drill_path import DrillPathService
from app.services.semantic_layer import SemanticLayerService
from app.services.query_accelerator import QueryAcceleratorService
from app.services.attribution_model import AttributionModelService
from app.services.cohort_analysis import CohortAnalysisService
from app.services.data_lineage import DataLineageService
from app.services.query_scheduler import QuerySchedulerService
from app.services.result_cache import ResultCacheService
from app.services.olap_scope import OlapScopeService
from app.services.ab_analysis import AbAnalysisService
from app.services.delivery_audience import DeliveryAudienceService
from app.services.delivery_receipt import DeliveryReceiptService
from app.services.delivery import DeliveryOrchestrator

SERVICES: dict[str, BaseModuleService] = {
    "metric_def": MetricDefService(),
    "cube_builder": CubeBuilderService(),
    "slice_query": SliceQueryService(),
    "segment_rule": SegmentRuleService(),
    "dashboard_layout": DashboardLayoutService(),
    "adhoc_export": AdhocExportService(),
    "report_subscription": ReportSubscriptionService(),
    "anomaly_detect": AnomalyDetectService(),
    "drill_path": DrillPathService(),
    "semantic_layer": SemanticLayerService(),
    "query_accelerator": QueryAcceleratorService(),
    "attribution_model": AttributionModelService(),
    "cohort_analysis": CohortAnalysisService(),
    "data_lineage": DataLineageService(),
    "query_scheduler": QuerySchedulerService(),
    "result_cache": ResultCacheService(),
    "olap_scope": OlapScopeService(),
    "ab_analysis": AbAnalysisService(),
    "delivery_audience": DeliveryAudienceService(),
    "delivery_receipt": DeliveryReceiptService(),
}

# 交付编排器单例：报表订阅→交付受众→即席导出→交付收据 的链路入口。
delivery_orchestrator = DeliveryOrchestrator()


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
