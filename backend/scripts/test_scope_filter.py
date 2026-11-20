"""安全范围表达式解析器单元测试：白名单语法、数值比较、恶意输入拒绝。

运行：cd backend && .venv/bin/python -I scripts/test_scope_filter.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.scope_filter import ScopeFilterError, evaluate_filter, filter_rows

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'通过' if condition else '失败'}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


ROWS = [
    {"城市": "上海", "GMV": 100, "订单数": 10},
    {"城市": "杭州", "GMV": 200, "订单数": 20},
    {"城市": "北京", "GMV": 300, "订单数": 30},
]


def main() -> int:
    # IN 列表
    hit = filter_rows("城市 IN ('上海','杭州')", ROWS)
    check("IN 过滤命中两行", [r["城市"] for r in hit] == ["上海", "杭州"])

    # NOT IN
    hit = filter_rows("城市 NOT IN ('上海','杭州')", ROWS)
    check("NOT IN 过滤", [r["城市"] for r in hit] == ["北京"])

    # 等值与 AND
    hit = filter_rows("城市 = '上海' AND GMV >= 100", ROWS)
    check("等值+AND+数值比较", len(hit) == 1 and hit[0]["城市"] == "上海")

    # OR 与比较运算
    hit = filter_rows("GMV > 150 OR 订单数 < 15", ROWS)
    check("OR 组合条件", {r["城市"] for r in hit} == {"上海", "杭州", "北京"})

    # 空表达式放行
    check("空表达式放行全部", len(filter_rows("", ROWS)) == 3)

    # 不存在的字段不命中（fail-closed）
    check("缺失字段比较不命中", evaluate_filter("不存在字段 = 'x'", ROWS[0]) is False)

    # 恶意输入：函数调用 / 属性访问 / 括号表达式
    for evil in (
        "__import__('os').system('id')",
        "城市.__class__",
        "(1+1)",
        "城市 = '上海'; DROP TABLE t",
        "城市 IN '上海'",
        "GMV = ",
    ):
        try:
            filter_rows(evil, ROWS)
            check(f"拒绝恶意/非法表达式：{evil}", False)
        except ScopeFilterError:
            check(f"拒绝恶意/非法表达式：{evil}", True)

    # 交付规则同款表达式可解析
    seed_expr = "城市 IN ('上海','杭州','南京')"
    check("种子范围表达式可解析",
          [r["城市"] for r in filter_rows(seed_expr, ROWS)] == ["上海", "杭州"])

    print("\n==== 解析器测试 ====")
    if FAILURES:
        print(f"{len(FAILURES)} 项未通过：{FAILURES}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
