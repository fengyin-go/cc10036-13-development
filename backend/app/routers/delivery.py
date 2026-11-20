"""交付链路接口：交付受众查询、模拟推送、交付收据核对与交付文件下载。"""
from __future__ import annotations

import os
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas import ActionPayload
from app.services.adhoc_export import DELIVERY_FILE_DIR
from app.services.delivery_audience import DeliveryAudienceService
from app.services.delivery_receipt import DeliveryReceiptService
from app.services.report_subscription import ReportSubscriptionService
from app.services import delivery_orchestrator
from app.store import store

router = APIRouter(prefix="/api/delivery", tags=["交付链路"])


@router.get("/audiences")
def list_audiences(
    recipient: str | None = None,
    domain: str | None = None,
    report_object: str | None = None,
) -> dict[str, Any]:
    """查询交付受众数据（默认仅已授权身份）。"""
    svc = DeliveryAudienceService()
    items = svc.list_audiences(recipient=recipient, domain=domain, report_object=report_object)
    return {"ok": True, "total": len(items), "items": items}


@router.get("/receipts")
def list_receipts(period: str | None = None, report_object: str | None = None) -> dict[str, Any]:
    """查询交付收据，可按订阅期/报表对象核对。"""
    svc = DeliveryReceiptService()
    rows = list(store.rows(svc.MODULE))
    if period:
        rows = [row for row in rows if str(row.get("订阅期", "")) == period]
    if report_object:
        rows = [row for row in rows if row.get("报表对象") == report_object]
    return {"ok": True, "total": len(rows), "items": rows}


@router.get("/receipts/summary")
def receipt_summary(period: str = "2026-11") -> dict[str, Any]:
    """订阅期交付汇总：收据数、分域计数、幂等拦截次数。"""
    return {"ok": True, "summary": DeliveryReceiptService().period_summary(period)}


@router.post("/subscriptions/{subscription_id}/push")
def push_subscription(subscription_id: int) -> dict[str, Any]:
    """运行一次订阅模拟推送，串联交付受众→即席导出→交付收据。"""
    return ReportSubscriptionService().simulate_push(subscription_id)


@router.post("/audiences/{audience_id}/deliver")
def deliver_single(audience_id: int, payload: ActionPayload | None = None) -> dict[str, Any]:
    """对单个交付受众执行一次交付（用于并发幂等验证）。"""
    audience = store.find(DeliveryAudienceService().MODULE, audience_id)
    if audience is None:
        raise HTTPException(status_code=404, detail=f"交付受众 {audience_id} 不存在")
    return delivery_orchestrator.deliver_for_audience(audience)


@router.get("/files/{file_name}")
def download_file(file_name: str) -> FileResponse:
    """下载交付报表文件，供核对范围权限、业务归属与分域合计。"""
    if "/" in file_name or ".." in file_name:
        raise HTTPException(status_code=400, detail="非法文件名")
    file_path = os.path.join(DELIVERY_FILE_DIR, file_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"交付文件 {file_name} 不存在")
    # 中文文件名按 RFC 5987 编码，避免 latin-1 头部编码失败。
    encoded_name = urllib.parse.quote(file_name)
    return FileResponse(
        file_path,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=delivery.csv; filename*=UTF-8''{encoded_name}"
            )
        },
    )
