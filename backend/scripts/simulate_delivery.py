"""双身份交付对象模拟推送端到端核对脚本（服务级直调 + 多线程并发）。

运行：
    cd backend && .venv/bin/python -I scripts/simulate_delivery.py

核对点：
1. 同一接收人陈晓持有两个已授权业务身份，两域共享订阅期、区域与指标不同；
2. 模拟推送后实际导出数据量、文件内各域合计、交付收据一致；
3. 同一域同一人并发生成只记一次本期交付（幂等）；
4. 交付文件可核对范围权限与业务归属；
5. 范围规则冻结的受众被拒绝交付。
"""
from __future__ import annotations

import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.delivery import DeliveryOrchestrator
from app.services.report_subscription import ReportSubscriptionService
from app.services.adhoc_export import DELIVERY_FILE_DIR
from app.store import store

PERIOD = "2026-11"
SUB_ID = 4
PASS = "通过"
FAIL = "失败"


def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"[{PASS if condition else FAIL}] {name}" + (f" — {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    orchestrator = DeliveryOrchestrator()

    # ---- 0. 前置数据 ----
    audiences = store.rows("delivery_audience")
    chen = [a for a in audiences if a["接收人"] == "chenxiao@example.com"]
    check("接收人陈晓持有两个已授权业务身份", len(chen) == 2
          and all(a["status"] == "已授权" for a in chen))
    domains = {a["业务域"] for a in chen}
    check("两个身份分属不同业务域", domains == {"营收分析", "用户增长"}, str(sorted(domains)))
    check("两域共享同一订阅期", len({a["订阅期"] for a in chen}) == 1,
          str({a["订阅期"] for a in chen}))
    regions = {a["业务域"]: a["覆盖区域"] for a in chen}
    check("两域核对的销售指标覆盖区域不同",
          regions["营收分析"] == "上海,杭州,南京"
          and regions["用户增长"] == "北京,天津,石家庄", str(regions))

    # ---- 1. 首次模拟推送（报表订阅入口串联整条链路） ----
    push = ReportSubscriptionService().simulate_push(SUB_ID)
    check("模拟推送成功", push["ok"], push.get("message", ""))
    check("识别到 3 个受众（含 1 个应拒绝）", push["audience_count"] == 3,
          str(push["audience_count"]))
    check("成功交付 2 份双身份报表", push["delivery_count"] == 2,
          str(push["delivery_count"]))
    check("本期收据 2 条", push["receipt_count"] == 2, str(push["receipt_count"]))
    check("推送结果保留旧版兼容字段", push["recipient_count"] == 1
          and push["report_object"] == "区域销售经营报表")

    denied = [d for d in push["deliveries"] if not d["ok"]]
    check("冻结范围规则的受众被拒绝交付", len(denied) == 1
          and "未生效" in denied[0]["reason"], json.dumps(denied, ensure_ascii=False))

    # ---- 2. 实际导出数据量核对 ----
    sales = store.rows("sales_fact")
    expected = {
        "营收分析": len([r for r in sales if r["业务域"] == "营收分析"
                         and r["城市"] in ("上海", "杭州", "南京")]),
        "用户增长": len([r for r in sales if r["业务域"] == "用户增长"
                        and r["城市"] in ("北京", "天津", "石家庄")]),
    }
    got_rows = {d["domain"]: d["data_row_count"] for d in push["deliveries"] if d["ok"]}
    check("实际导出数据量与范围规则命中行数一致", got_rows == expected,
          f"got={got_rows} expected={expected}")

    # ---- 3. 文件内分域合计核对 ----
    expected_totals = {}
    for domain, cities in (("营收分析", ("上海", "杭州", "南京")),
                           ("用户增长", ("北京", "天津", "石家庄"))):
        scoped = [r for r in sales if r["业务域"] == domain and r["城市"] in cities]
        expected_totals[domain] = {
            "GMV": str(sum(r["GMV"] for r in scoped)),
            "订单数": str(sum(r["订单数"] for r in scoped)),
        }
    check("推送结果中的分域合计正确",
          push["domain_totals"] == expected_totals,
          json.dumps(push["domain_totals"], ensure_ascii=False))

    for delivery in (d for d in push["deliveries"] if d["ok"]):
        file_path = delivery["file_path"]
        check(f"交付文件已落盘：{os.path.basename(file_path)}", os.path.isfile(file_path))
        text = open(file_path, encoding="utf-8").read()
        check(f"文件含业务归属元数据（{delivery['domain']}）",
              f"# 业务域: {delivery['domain']}" in text)
        check("文件含只读权限标注", "# 只读权限: 只读" in text)
        check("文件含范围规则与行过滤条件", "# 范围规则: 陈晓-" in text
              and "# 行过滤条件: 城市 IN" in text)
        check("文件含校验和", "# 校验和MD5:" in text)
        check("文件含分域合计行", f"{delivery['domain']}合计" in text)
        for key, value in expected_totals[delivery["domain"]].items():
            check(f"文件合计行 {key}={value}", value in text)
        other_cities = {
            "营收分析": ("广州", "深圳", "北京", "天津", "石家庄", "成都", "重庆"),
            "用户增长": ("上海", "杭州", "南京", "广州", "深圳", "成都", "重庆"),
        }
        leaked = [c for c in other_cities[delivery["domain"]] if c in text]
        check(f"文件无越权区域数据（{delivery['domain']}）", not leaked, str(leaked))

    # ---- 4. 交付收据核对 ----
    receipts = orchestrator.receipt_svc.receipts_for_subscription("区域销售经营报表", PERIOD)
    check("本期收据 2 条", len(receipts) == 2, str(len(receipts)))
    for receipt in receipts:
        check(f"收据 {receipt['业务域']} 含去重键",
              str(receipt.get("去重键", "")).startswith(PERIOD))
        check(f"收据 {receipt['业务域']} 记录权限范围", "范围" in str(receipt.get("权限范围", "")))
        check(f"收据 {receipt['业务域']} 数据行数与导出一致",
              int(receipt["数据行数"]) == got_rows[receipt["业务域"]])

    # ---- 5. 并发幂等：同域同人并发生成只记一次 ----
    before = len(store.rows("delivery_receipt"))
    audience_one = store.rows("delivery_audience")[0]
    barrier = threading.Barrier(4)
    results: list[dict] = []

    def concurrent_deliver() -> None:
        barrier.wait()
        results.append(orchestrator.deliver_for_audience(audience_one))

    threads = [threading.Thread(target=concurrent_deliver) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    after = len(store.rows("delivery_receipt"))
    check("并发 4 次交付后收据总数不增加", after == before, f"before={before} after={after}")
    check("4 次并发全部幂等复用、无新建",
          len(results) == 4 and all(r.get("deduplicated") for r in results),
          str([r.get("deduplicated") for r in results]))
    check("收据累计并发拦截次数 >= 4",
          sum(int(r.get("并发拦截次数", 0)) for r in store.rows("delivery_receipt")) >= 4)

    # ---- 6. 再次整订阅推送：两域均命中幂等，不产生新文件/新收据 ----
    push2 = ReportSubscriptionService().simulate_push(SUB_ID)
    check("二次推送成功交付数仍为 2", push2["delivery_count"] == 2)
    check("二次推送全部命中幂等复用", push2["deduplicated_count"] == 2,
          str(push2["deduplicated_count"]))
    check("二次推送收据数仍为 2", push2["receipt_count"] == 2)

    # ---- 7. 期次汇总 ----
    summary = orchestrator.receipt_svc.period_summary(PERIOD)
    check("期次汇总按业务域各 1 条",
          summary["by_domain"] == {"营收分析": 1, "用户增长": 1},
          json.dumps(summary, ensure_ascii=False))
    check("期次汇总记录幂等拦截", summary["dedup_hits"] >= 4, str(summary["dedup_hits"]))

    print("\n================ 核对结果 ================")
    if failures:
        print(f"{len(failures)} 项未通过：{failures}")
        return 1
    files = sorted(os.listdir(DELIVERY_FILE_DIR))
    print(f"全部核对通过。交付文件目录 {DELIVERY_FILE_DIR}：{files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
