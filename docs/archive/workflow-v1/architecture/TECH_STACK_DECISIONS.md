# 技术栈选型与论证

依据访问日期为 2026-11-14；`latest/main/master` 页面会变化。以下选的是技术路线，实施时提交精确版本与 package-lock，不把未安装的包标为兼容已验证。

| 层次 | 选择及理由 | 候选与代价 | 验证门槛 |
|---|---|---|---|
| Node | Node 24 LTS，兼顾已有生态与 Windows 10/11 x64 | Node 26 也是当前 LTS，但不为追新放大原生包组合风险 | 干净 Win10/11 安装、停止进程树、中文/空格路径 |
| Web | React + TypeScript + Vite，适合复杂交互且本地构建简单 | Next.js SSR/路由服务在本机工具无明显必要性 | lockfile 内 React 与 React Flow peer 一致 |
| 画布 | `@xyflow/react` 开源核心；只负责节点/边/交互适配 | 自研 SVG 画布会耗费大量交互预算 | 200 节点拖动与键盘、视口、分组验证 |
| 样式 | Tailwind CSS + 本地维护的 shadcn/ui 基础组件 | 全套后台模板难以贴合编辑器；也不能照搬不同风格组件 | token 统一、可访问性、离线资产 |
| 本地状态 | Zustand 独占可编辑图及命令历史 | 把图同时写进 Query cache 和 Store 容易覆盖未保存修改 | 保存版本竞争测试 |
| 服务端状态 | TanStack Query 保存列表/详情缓存、请求生命周期 | 不承担图的逐帧变更；焦点自动 refetch 不能重置编辑文档 | 禁止脏草稿被后台刷新覆盖 |
| 后端 | Fastify 插件式模块+显式依赖注入 | NestJS DI/模块/装饰器可约束大团队，但本项目 3–5 人、模块数有限；Fastify 需要自行坚持边界 | 架构依赖检查、route schema、统一错误处理 |
| SQLite ORM | Drizzle + better-sqlite3：TS schema、可检查 SQL、短事务便于 CAS | Prisma 也支持本地 SQLite，不需要云；其生成器与 schema 层有收益，但当前显式调度 SQL 更适合 Drizzle | migration 回归、实际 SQLite 版本、Windows 预编译包 |
| 实时消息 | SSE+HTTP 命令，业务为后端状态向浏览器推送 | WebSocket 双向/低延迟协作收益在 P0 不足；需自行心跳/重连/协议 | 断连补发、缺口重置、慢客户端 |
| 自动布局 | Dagre 两级布局：组内+超节点图；布局结果作为单个可撤销命令 | ELK 对复杂布局更丰富但包体和参数更多；P1 按实测迁移 | 跨分组边及超节点环检测，有失败时保留旧布局 |
| 测试 | Vitest、Testing Library、Fastify inject、临时 SQLite、Playwright | 不依赖云端 CI，不用 mock DB 代替事务验证 | 本地一个 check 命令可完整运行 |

## 原生数据库的取舍

better-sqlite3 的同步调用符合短事务模型，但同步就是会阻塞事件循环；限制查询/输出/日志大小，禁止在事务里执行网络、异步或长计算。Windows 的原生预编译文件不是默认保证：Phase 1 先验证 Node 24 x64 安装，不把 C++ 编译工具链强加为正常安装前提。若不可用，先选择经验证的兼容补丁版本；仍失败则评估 Node 内置 SQLite 或其他本地驱动，并回写 ADR、迁移和测试设计，不能悄悄改成云数据库。

官方 WAL 文档记录 3.51.3 修复及部分回移版本。安装检查读取 `sqlite_version()` 并匹配已修复版本清单，不能用字符串排序判断版本。首选 3.51.3 或更高已验证版本。[SQLite WAL 官方说明](https://www.sqlite.org/wal.html)

## 第三方许可证与版本管理

React Flow 核心 MIT 不代表其 Pro 示例免费。P0 自行实现命令历史，不复制付费撤销示例。FlowStudio 不复制 Dify/n8n 的受限产品源码，Flowise 的 enterprise 部分也不作为依赖。每个运行时依赖在 Phase 1 记录版本、许可证、用途及 NOTICE 要求；FlowStudio 自身许可证由仓库所有者在对外发布前决定。

## 已核实来源

- [Node 发布周期](https://nodejs.org/en/about/previous-releases)、[Node 24 平台](https://github.com/nodejs/node/blob/v24.x/BUILDING.md)：LTS 与系统平台；不支持推导所有 npm 原生包都可用。
- [Fastify 封装](https://fastify.dev/docs/latest/Reference/Encapsulation/)、[NestJS](https://docs.nestjs.com/)：模块组织能力；选择成本是本项目判断。
- [Drizzle SQLite](https://orm.drizzle.team/docs/get-started/sqlite-new)、[Prisma SQLite](https://www.prisma.io/docs/orm/v7/core-concepts/supported-databases/sqlite)、[驱动事务 API](https://github.com/WiseLibs/better-sqlite3/blob/master/docs/api.md)。
- [WHATWG SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html)：单向事件、id 和重连；持久补发由应用实现。
- [Worker Threads](https://nodejs.org/api/worker_threads.html)：CPU 密集用途；线程池是否值得使用须测量。
- [Vitest](https://vitest.dev/guide/)、[Playwright](https://playwright.dev/docs/intro)、[TanStack 默认行为](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults)。Zustand 文档访问失败，选用其 Store 模式属于设计建议，未据此声称核验最新 API。
- [React Flow 研究](../research/evidence/REACT_FLOW.md)：组件、自研边界与布局限制。
