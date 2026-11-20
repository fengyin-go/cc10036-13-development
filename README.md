# 多维分析查询与指标平台

面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              20个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/delivery.py  交付链路接口（受众/推送/收据/文件）
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/delivery.py 交付编排：订阅→受众→导出→收据
│   ├── app/services/scope_filter.py 安全范围表达式解析器（无 eval）
│   ├── app/services/base.py     共享服务骨架
│   ├── app/store.py          内存数据仓库与示例数据
│   └── scripts/              端到端模拟推送与核对脚本
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编码、指标名称、业务域 |
| 多维立方 | `cube_builder` | OLAP立方 | 立方名称、事实表、维度列表 |
| 切片查询 | `slice_query` | 查询模板 | 模板名称、数据立方、查询维值 |
| 分群圈选 | `segment_rule` | 分群规则 | 分群名称、人群类型、筛选条件 |
| 看板布局 | `dashboard_layout` | 看板配置 | 看板名称、所属业务域、图表布局 |
| 即席导出 | `adhoc_export` | 导出任务 | 任务名称、查询SQL、导出格式 |
| 报表订阅 | `report_subscription` | 订阅配置 | 订阅名称、报表对象、推送周期 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则名称、监控指标、检测算法 |
| 下钻路径 | `drill_path` | 下钻配置 | 路径名称、起始维度、下钻层级 |
| 语义层 | `semantic_layer` | 语义定义 | 语义名称、物理表、字段映射 |
| 查询加速 | `query_accelerator` | 加速配置 | 配置名称、目标查询、加速方式 |
| 归因模型 | `attribution_model` | 归因配置 | 模型名称、归因对象、归因窗口 |
| 同期群分析 | `cohort_analysis` | 群组配置 | 群组名称、初始事件、回访事件 |
| 数据血缘 | `data_lineage` | 血缘节点 | 节点名称、节点类型、上游列表 |
| 查询调度 | `query_scheduler` | 调度计划 | 计划名称、查询语句、执行周期 |
| 结果缓存 | `result_cache` | 缓存条目 | 缓存键、查询描述、结果行数 |
| 数据范围 | `olap_scope` | 范围规则 | 规则名称、数据范围、授权对象 |
| 实验分析 | `ab_analysis` | 实验配置 | 实验名称、对照组、实验组 |
| 交付受众 | `delivery_audience` | 交付受众 | 接收人、受众身份、业务域、范围规则ID、覆盖区域 |
| 交付收据 | `delivery_receipt` | 交付收据 | 订阅期、业务域、接收人、文件名称、分域合计、权限范围 |

## 报表订阅交付链路

报表订阅通过「交付受众 → 数据范围 → 即席导出 → 交付收据」串联出按业务归属的
只读交付能力：

- `delivery_audience`：报表对象与受众身份的关联，明确每份数据的接收人、业务身份、
  业务域、覆盖区域、核对指标、订阅期和只读用途。
- `olap_scope`：受众绑定已生效的数据范围规则，行级过滤由
  `app/services/scope_filter.py` 的白名单表达式解析器执行（不使用 eval，
  非法表达式按未授权处理）。
- `adhoc_export`：按受众范围过滤与列投影生成只读业务报表，文件头写入业务归属、
  权限范围、分域合计与 MD5 校验和，并保留本期分域合计行。
- `delivery_receipt`：以「订阅期 + 业务域 + 接收人」为幂等键，同域同人本期
  并发生成只记一次交付。

接口入口（见 `backend/app/routers/delivery.py`）：

```bash
# 运行一次订阅模拟推送
curl -X POST http://127.0.0.1:8000/api/delivery/subscriptions/4/push
# 核对交付收据与期次汇总
curl 'http://127.0.0.1:8000/api/delivery/receipts?period=2026-11'
curl 'http://127.0.0.1:8000/api/delivery/receipts/summary?period=2026-11'
# 下载交付报表
curl -OJ 'http://127.0.0.1:8000/api/delivery/files/<文件名称>'
```

端到端核对脚本：

```bash
cd backend
.venv/bin/python -I scripts/test_scope_filter.py      # 范围表达式安全解析
DELIVERY_FILE_DIR=/tmp/delivery-run .venv/bin/python -I scripts/simulate_delivery.py
.venv/bin/python -I scripts/http_smoke_delivery.py    # 需先启动后端
```

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
