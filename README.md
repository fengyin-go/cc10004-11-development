# 数据洞察分析工作台

面向多源数据接入、交互式查询分析、可视化看板搭建、分析报告生成与数据服务编排的数据洞察分析工作台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
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
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 交互查询 | `query` | 查询语句 | 语句编号、查询表达式、目标数据集 |
| 可视化图表 | `chart` | 图表配置 | 图表编号、图表名称、图表类型 |
| 数据源接入 | `source` | 接入配置 | 配置编号、连接类型、接入地址 |
| 聚合计算 | `aggregate` | 聚合方案 | 方案编号、聚合函数、目标字段 |
| 数据筛选 | `filter` | 筛选规则 | 规则编号、目标数据集、筛选字段 |
| 报告生成 | `reportgen` | 报告模板 | 模板编号、模板名称、包含图表 |
| 异常检测 | `anomaly` | 检测规则 | 规则编号、监控指标、基线均值 |
| 多维下钻 | `drill` | 下钻路径 | 路径编号、起点维度、下钻层数 |
| 对比分析 | `compare` | 对比方案 | 方案编号、基准数据集、对照数据集 |
| 分析流水线 | `pipeline` | 流水线节点 | 节点编号、节点类型、输入源 |
| 数据集管理 | `datastore` | 数据集定义 | 数据集编号、数据集名称、来源表名 |
| 趋势预测 | `forecast` | 预测模型 | 模型编号、模型类型、训练数据集 |
| 数据分片 | `segment` | 分片规则 | 规则编号、目标数据集、分片字段 |
| 调度管理 | `scheduler` | 调度配置 | 配置编号、关联任务、调度表达式 |
| 通知分发 | `notify` | 通知规则 | 规则编号、触发条件、通知渠道 |
| 查询缓存 | `cache` | 缓存策略 | 策略编号、目标查询、缓存键规则 |
| 数据标签 | `tag` | 标签定义 | 标签编号、标签名称、计算表达式 |
| 数据分享 | `share` | 分享链接 | 链接编号、目标数据集、访问密码 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 数据集列级借阅

数据集管理支持按字段开放借阅，能力由服务端按归属裁决，前端只负责渲染结果。

### 身份

演示环境用 `X-Actor-Id` 请求头映射到内置账号（见 `backend/app/identity.py`），
前端顶部可以切换；未提供或无法识别的身份一律按**公开浏览者**处理。
生产环境应替换为经过校验的登录票据，不能信任客户端自报的归属。

| 账号 | 名称 | 能力 |
| --- | --- | --- |
| `owner-alpha` / `owner-beta` | 数据集维护者 | 认领数据集、配置公开字段、审批申请、授予/撤销字段 |
| `borrower-gamma` | 使用方 | 按字段申请借阅，只能读取获准列 |
| `platform-admin` | 平台管理员 | 管理员能力与归属移交 |
| （未提供） | 公开浏览者 | 只读显式公开字段，没有任何写权限 |

### 归属

- 数据集有且只有一个所有者，存在 `owner_id` 上；请求头只回答“你是谁”。
- 样例 `DATA-0001` / `DATA-0002` 是无所有者数据：可只读浏览以兼容原有体验，
  但会拒绝任何状态流转，需维护者先调 `POST /api/datastore/{id}/access/claim`。
- 登记新数据集时登记人自动成为所有者，不会留下无主记录。

### 列级裁决与优先级

- 每次读取都重新计算有效字段，不缓存授权快照；收窄范围对新请求立即生效。
- 同字段多份有效授权时：`priority` 高者优先 → `revision` 新者优先 → 决定编号大者优先；
  证据留在 `access_decisions` 与审计事件里。
- 显式撤销（`POST /api/datastore/{id}/access/revoke`）推进版本并把对应字段标记为
  `已撤销`。撤销前创建的申请带旧的 `base_revision`，撤销后批准会被 409 拦下；
  旧客户端带旧 `expected_revision` 追加授权同样被 409 拦下。
- 已完成查询保留当时的准入证据（`admission` 含字段、决定、版本与服务端摘要），
  结果只按“完成时准入字段 ∩ 当前可读字段”下发，旧证据不能扩大读取。

### 接口速览

| 动作 | 接口 |
| --- | --- |
| 能力与可读列 | `GET /api/datastore/{id}/access/permission` |
| 认领 / 配置 | `POST /api/datastore/{id}/access/claim`、`/access/config` |
| 申请 / 裁决 / 撤回 | `POST /api/datastore/{id}/access/requests`、`/api/datastore/access/requests/{rid}/decision`、`.../withdraw` |
| 直接授权 / 撤销 | `POST /api/datastore/{id}/access/decisions`、`/access/revoke` |
| 决定与审计 | `GET /api/datastore/{id}/access/decisions`、`/access/events` |
| 查询执行与结果 | `POST /api/query/{id}/actions`、`GET /api/query/{id}/result` |

### 测试

```bash
cd backend
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests -q
```

`pytest`、`httpx` 只在测试时需要，未写入 `requirements.txt`，避免影响运行时依赖。

覆盖：角色能力区分、无主样例只读、服务端归属校验、申请-批准-到期-撤销时间线、
范围收窄对新查询生效、已完成查询证据保留但不扩大读取、冲突优先级、
显式撤销优先于过期客户端的追加请求。

领域：数据工程
子方向：数据分析与数据应用
