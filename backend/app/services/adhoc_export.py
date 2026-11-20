"""即席导出领域服务：异步任务调度、文件生成、格式转换、交付报告构建。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from typing import Any

from app.services.base import BaseModuleService
from app.services.scope_filter import ScopeFilterError, filter_rows
from app.store import store
from app.analytics_engine import ExportScheduler

# 交付报告落盘目录（模拟对象存储/文件网关），由配置默认指向后端工作目录下。
DELIVERY_FILE_DIR = os.environ.get(
    "DELIVERY_FILE_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "delivery_files"),
)


class AdhocExportService(BaseModuleService):
    MODULE = "adhoc_export"
    ENTITY = "导出任务"
    OPERATION_TERM = "查询"
    LIST_FIELDS = ["任务名称", "查询SQL", "导出格式", "行数限制", "是否压缩", "分区过滤", "创建时间"]
    REQUIRED_FIELDS = ["任务名称", "查询SQL", "导出格式"]
    STATUS_ORDER = ["排队中", "执行中", "已完成", "导出失败", "已过期"]
    ACTION_RULES = {"开始导出": "执行中", "完成导出": "已完成", "标记失败": "导出失败", "清理过期": "已过期"}
    NEGATIVE_ACTIONS = ["标记失败"]

    def __init__(self) -> None:
        super().__init__()
        self._scheduler = ExportScheduler(max_concurrent=3)

    # ---- 业务方法 ----

    def submit_export(
        self, task_id: int, query_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """提交导出任务并异步执行。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"导出任务 {task_id} 不存在"}

        export_format = str(task.get("导出格式", "csv")).lower()
        row_limit = _parse_int(task.get("行数限制"), 1000)
        compressed = str(task.get("是否压缩", "否")).lower() in ("是", "true", "yes", "1")

        self._scheduler.enqueue(
            task_id=str(task_id),
            priority=1,
            sql=str(task.get("查询SQL", "")),
            format=export_format,
        )

        self.run_action(task_id, "开始导出")

        # 同步执行导出（简化版）
        try:
            file_content = _generate_export_file(
                query_data[:row_limit], export_format, compressed,
            )
            self.run_action(task_id, "完成导出")
            return {
                "ok": True,
                "task_id": task_id,
                "task_name": str(task.get("任务名称", "")),
                "format": export_format,
                "row_count": min(len(query_data), row_limit),
                "compressed": compressed,
                "file_size_bytes": len(file_content) if isinstance(file_content, bytes) else len(file_content.encode()),
                "content_preview": (
                    file_content[:500] if isinstance(file_content, str)
                    else file_content[:500].decode("utf-8", errors="replace")
                ),
            }
        except Exception as exc:
            self.run_action(task_id, "标记失败")
            return {"ok": False, "message": f"导出失败: {exc}"}

    def batch_export(
        self, task_ids: list[int], query_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """批量提交导出任务。"""
        results: list[dict[str, Any]] = []
        for tid in task_ids:
            result = self.submit_export(tid, query_data)
            results.append(result)
        success = sum(1 for r in results if r.get("ok"))
        return {
            "ok": True,
            "total_tasks": len(task_ids),
            "success_count": success,
            "fail_count": len(task_ids) - success,
            "results": results,
        }

    def get_queue_status(self) -> dict[str, Any]:
        """获取导出队列状态。"""
        return {
            "ok": True,
            "queue_size": self._scheduler.queue_size(),
            "running_count": self._scheduler.running_count(),
        }

    # ---- 交付报告（报表订阅→即席导出 串联） ----

    def register_delivery_task(
        self, audience: dict[str, Any], period: str, export_format: str = "csv",
    ) -> dict[str, Any]:
        """为一次受众交付登记一条即席导出任务，任务名称可追溯到业务域与受众身份。"""
        task, missing = self.create_entry({
            "任务名称": (
                f"交付报告-{audience.get('业务域')}-{audience.get('受众身份')}-{period}"
            ),
            "查询SQL": (
                f"SELECT * FROM sales_fact WHERE 业务域='{audience.get('业务域')}' "
                f"AND 订阅期='{period}' /* scope_rule={audience.get('范围规则ID')} "
                f"readonly={audience.get('只读权限', '只读')} */"
            ),
            "导出格式": export_format,
            "行数限制": "100000",
            "是否压缩": "否",
            "分区过滤": f"订阅期={period}",
            "创建时间": period,
        })
        if missing:  # pragma: no cover - 交付任务字段由系统填充，理论上不会缺
            raise ValueError(f"交付导出任务缺少字段：{'、'.join(missing)}")
        return task

    def build_delivery_report(
        self,
        task_id: int,
        audience: dict[str, Any],
        scope_rule: dict[str, Any],
        source_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """按交付受众的范围权限生成可供对应身份只读的业务报表。

        流程：行级范围过滤 → 可见字段列投影 → 追加本业务域合计行 →
        写入带权限/归属元数据与校验和的 CSV 文件，并把导出任务流转到「已完成」。
        """
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"导出任务 {task_id} 不存在"}

        expression = str(scope_rule.get("行过滤条件", "")).strip()
        try:
            scoped_rows = filter_rows(expression, source_rows) if expression else list(source_rows)
        except ScopeFilterError as exc:
            self.run_action(task_id, "标记失败")
            return {"ok": False, "message": f"范围规则无法解析，按未授权处理：{exc}"}

        visible_fields = [
            f.strip() for f in str(scope_rule.get("可见字段", "")).split(",") if f.strip()
        ]
        if visible_fields and "全部" not in visible_fields:
            projected = [
                {field: row.get(field, "") for field in visible_fields if field in row}
                for row in scoped_rows
            ]
        else:
            projected = [dict(row) for row in scoped_rows]

        totals = _domain_totals(projected)
        if projected:
            total_row: dict[str, Any] = {key: "" for key in projected[0].keys()}
            label_key = first_present(total_row, ("城市", "地区", "日期", "产品类目"))
            if label_key:
                total_row[label_key] = f"{audience.get('业务域')}合计"
            for key, value in totals.items():
                total_row[key] = value
            projected.append(total_row)

        file_name = (
            f"delivery_{audience.get('业务域')}_{audience.get('受众身份')}"
            f"_{audience.get('订阅期')}_{task_id}.csv"
        )
        file_result = _write_delivery_csv(
            file_name=file_name,
            audience=audience,
            scope_rule=scope_rule,
            rows=projected,
            totals=totals,
        )
        self.run_action(task_id, "完成导出")
        return {
            "ok": True,
            "task_id": task_id,
            "file_name": file_name,
            "file_path": file_result["file_path"],
            "data_row_count": len(scoped_rows),
            "total_row_count": len(projected),
            "domain_totals": totals,
            "visible_fields": visible_fields or "全部",
            "checksum_md5": file_result["checksum_md5"],
            "file_size_bytes": file_result["file_size_bytes"],
            "scope_rule": scope_rule.get("规则名称"),
            "readonly": audience.get("只读权限", "只读"),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "导出任务数据集"
        base["queue_size"] = self._scheduler.queue_size()
        return base


def _parse_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _generate_export_file(
    data: list[dict[str, Any]], format: str, compressed: bool,
) -> str | bytes:
    """生成导出文件内容。"""
    if not data:
        return "" if format == "csv" else "[]"

    if format == "csv":
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(list(data[0].keys()))
        for row in data:
            writer.writerow(list(row.values()))
        return output.getvalue()

    elif format in ("json", "jsonl"):
        if format == "jsonl":
            return "\n".join(json.dumps(row) for row in data)
        return json.dumps(data, ensure_ascii=False, indent=2)

    elif format == "parquet":
        # 简化parquet: JSON序列化
        return json.dumps(data, ensure_ascii=False).encode()

    return json.dumps(data)


# ---------------------------------------------------------------------------
# 交付报告辅助
# ---------------------------------------------------------------------------

def first_present(row: dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    for key in candidates:
        if key in row:
            return key
    return next(iter(row), None)


def _domain_totals(rows: list[dict[str, Any]]) -> dict[str, str]:
    """对数值列做分域合计；非数值列不参与合计。"""
    numeric_sums: dict[str, float] = {}
    numeric_ints: dict[str, bool] = {}
    for row in rows:
        for key, value in row.items():
            try:
                num = float(value)
            except (ValueError, TypeError):
                continue
            numeric_sums[key] = numeric_sums.get(key, 0.0) + num
            numeric_ints[key] = numeric_ints.get(key, True) and float(num).is_integer()
    totals: dict[str, str] = {}
    for key, total in numeric_sums.items():
        totals[key] = str(int(total)) if numeric_ints.get(key) else str(round(total, 2))
    return totals


def _write_delivery_csv(
    *,
    file_name: str,
    audience: dict[str, Any],
    scope_rule: dict[str, Any],
    rows: list[dict[str, Any]],
    totals: dict[str, str],
) -> dict[str, Any]:
    """生成带交付元数据头与校验和的只读业务报表 CSV 并落盘。"""
    os.makedirs(DELIVERY_FILE_DIR, exist_ok=True)
    file_path = os.path.join(DELIVERY_FILE_DIR, file_name)

    meta_lines = [
        ("报表对象", audience.get("报表对象", "")),
        ("业务域", audience.get("业务域", "")),
        ("接收人", audience.get("接收人", "")),
        ("受众身份", audience.get("受众身份", "")),
        ("数据用途", audience.get("数据用途", "")),
        ("只读权限", audience.get("只读权限", "只读")),
        ("范围规则", scope_rule.get("规则名称", "")),
        ("行过滤条件", scope_rule.get("行过滤条件", "")),
        ("覆盖区域", audience.get("覆盖区域", "")),
        ("核对指标", audience.get("核对指标", "")),
        ("订阅期", audience.get("订阅期", "")),
        ("分域合计", json.dumps(totals, ensure_ascii=False)),
    ]

    payload = io.StringIO()
    writer = csv.writer(payload, lineterminator="\n")
    columns = list(rows[0].keys()) if rows else []
    if columns:
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(col, "") for col in columns])
    payload_text = payload.getvalue()
    checksum = hashlib.md5(payload_text.encode("utf-8")).hexdigest()

    header = io.StringIO()
    header.write("﻿")
    for key, value in meta_lines:
        header.write(f"# {key}: {value}\n")
    header.write(f"# 校验和MD5: {checksum}\n")
    content = header.getvalue() + payload_text

    with open(file_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    return {
        "file_path": file_path,
        "checksum_md5": checksum,
        "file_size_bytes": len(content.encode("utf-8")),
    }