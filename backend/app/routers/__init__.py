"""业务路由汇总：交付链路为显式路径需先注册，资源路由的动态路径兜底。"""
from __future__ import annotations

from app.routers import delivery, resource

ROUTERS = [delivery, resource]
