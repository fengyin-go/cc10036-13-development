"""交付链路 HTTP 冒烟测试：仅依赖标准库，针对运行中的 uvicorn 验证。

运行：
    cd backend
    DELIVERY_FILE_DIR=/tmp/delivery-http .venv/bin/uvicorn app.main:app --port 8000 &
    .venv/bin/python -I scripts/http_smoke_delivery.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
FAILURES: list[str] = []


def call(method: str, path: str) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            if "json" in content_type:
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'通过' if condition else '失败'}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    status, body = call("GET", "/api/health")
    check("健康检查", status == 200, str(body))

    status, body = call("GET", "/api/delivery/audiences?recipient=chenxiao@example.com")
    check("GET 交付受众", status == 200 and isinstance(body, dict) and body["total"] == 2,
          str(body)[:120])

    status, body = call("POST", "/api/delivery/subscriptions/4/push")
    check("POST 模拟推送", status == 200 and isinstance(body, dict) and body["ok"],
          str(body)[:160])
    if isinstance(body, dict):
        check("推送含交付清单与收据", body.get("receipt_count", 0) >= 1
              and len(body.get("deliveries", [])) == 3)

    # 幂等：再推一次
    status, body2 = call("POST", "/api/delivery/subscriptions/4/push")
    check("二次推送幂等复用", status == 200 and body2.get("deduplicated_count") == 2,
          str(body2.get("deduplicated_count")))

    status, body = call("GET", "/api/delivery/receipts?period=2026-11")
    check("GET 交付收据", status == 200 and body["total"] == 2, str(body)[:120])

    status, body = call("GET", "/api/delivery/receipts/summary?period=2026-11")
    check("GET 期次汇总", status == 200 and body["summary"]["receipt_count"] == 2,
          str(body)[:160])

    status, body = call("GET", "/api/delivery/audiences/1/deliver")
    check("GET 不允许触发交付动作（405）", status == 405, str(status))

    # 资源路由仍可访问新模块
    status, body = call("GET", "/api/delivery_audience?page=1&size=5")
    check("通用资源路由可查交付受众表", status == 200 and isinstance(body, dict)
          and body["total"] >= 3, str(body)[:120])

    # 文件下载（文件名含中文，需 URL 编码）
    if isinstance(body2, dict) and body2.get("deliveries"):
        ok = next((d for d in body2["deliveries"] if d.get("ok")), None)
        if ok:
            encoded = urllib.parse.quote(ok["file_name"])
            status, content = call("GET", f"/api/delivery/files/{encoded}")
            check("交付文件下载", status == 200 and isinstance(content, bytes), str(status))
            if isinstance(content, bytes):
                text = content.decode("utf-8-sig")
                check("下载文件含业务归属与只读标注",
                      f"# 业务域: {ok['domain']}" in text and "# 只读权限: 只读" in text)
                check("下载文件含分域合计",
                      f"{ok['domain']}合计" in text)
    status, _ = call("GET", "/api/delivery/files/..%2Fetc%2Fpasswd")
    check("路径穿越被拦截", status in (400, 404), str(status))

    print("\n==== HTTP 冒烟结果 ====")
    if FAILURES:
        print(f"{len(FAILURES)} 项未通过：{FAILURES}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
