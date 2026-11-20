# 技术选型与决策依据
所有版本在实施时锁定兼容集；当前 frontend/package-lock.json 已有 React19/React Flow12/Vite7 系列依赖，不为文档强制升级。

| 领域 | 比较 | 结论与触发条件 |
|---|---|---|
| 前端 | 现有 React/TS/Vite vs 重建 Next.js | 保留现有，不需要SSR |
| 画布 | React Flow vs 自绘Canvas | 保留前者交互基础；业务图独立 |
| UI | 现有CSS vs Tailwind+shadcn/ui | P0统一CSS tokens复用现有；新增复杂可访问组件再评估局部引入，避免全量重写 |
| 布局 | 现有拓扑分层 vs Dagre/ELK | P0分层；跨组连线/大图测量不达标时做候选实验 |
| 后端 | FastAPI vs 大型服务框架 | FastAPI+Pydantic，契约清晰、本地单体 |
| LLM | 直接Provider适配 vs LangChain | P0直接适配，结构化Schema和预算自主控制；多供应商复杂度上升才考虑LangChain |
| 编排 | Python纯函数 vs LangGraph | P0纯函数；P2对持久等待/调度做框架实验 |
| 存储 | SQLite/SQLAlchemy vs PostgreSQL | 本地单用户选择SQLite、迁移用Alembic，不引入外部DB |
| 实时 | REST轮询 vs SSE vs WebSocket | P0轮询；P2单向运行事件用SSE；无实时多人协作，不选WebSocket |
| 工程 | 单体 vs 微服务/队列 | 单进程、持久PlanningRun+有界队列；中断显式失败，不引入Redis/Docker |

证据见 [研究](../research/REFERENCE_PROJECTS.md)。SQLite/Python 后端已由 T02/T07 在 Windows 上以 FastAPI + SQLAlchemy/Alembic 落地（见[本地部署](../development/LOCAL_DEPLOYMENT.md)），跨平台差异仍以实测为准；不能把推荐写成兼容认证。
