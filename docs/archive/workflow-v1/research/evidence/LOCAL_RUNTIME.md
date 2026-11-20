# 本地技术栈研究证据

访问日期：2026-11-14。以下为官方文档已确认事实；不是本项目已完成的功能，也未开展性能实测。链接含动态版本页，实施时需锁定具体依赖版本。

| 主题 | 已确认事实 | 官方来源 | 对设计的启示（建议） |
|---|---|---|---|
| Windows | Node.js 24 分支平台表包含 Windows 10 x64；发布页将 24 列为 LTS | [平台表](https://github.com/nodejs/node/blob/v24.x/BUILDING.md)、[发布周期](https://nodejs.org/en/about/previous-releases) | 以 Node 24 LTS、Windows 10/11 x64 为兼容性基线，仍需干净机器验证原生驱动 |
| Fastify | 插件上下文具有依赖可见性封装 | [Encapsulation](https://fastify.dev/docs/latest/Reference/Encapsulation/) | 采用按业务模块注册的单体，不需要分布式服务 |
| NestJS | 提供 TypeScript、模块、Providers、DI 等组织能力 | [官方介绍](https://docs.nestjs.com/) | 比较团队约束收益与额外装饰器/框架学习成本，不能因其功能多就选用 |
| ORM | Drizzle 支持本地 SQLite 驱动；Prisma 亦支持 SQLite 与驱动适配器 | [Drizzle](https://orm.drizzle.team/docs/get-started/sqlite-new)、[Prisma SQLite](https://www.prisma.io/docs/orm/v7/core-concepts/supported-databases/sqlite) | 二者都不要求数据库服务；选型应看事务透明性和迁移维护，不能声称 Prisma 必须云端 |
| SQLite | WAL 支持读写并行但同一时刻仍只有一个写者，不适用于网络文件系统 | [WAL](https://www.sqlite.org/wal.html) | 本机磁盘、短事务、单写入所有者；不将并发任务等同于并发数据库写入 |
| SQLite 修复 | 官方 WAL 页记载 WAL-reset 修复于 3.51.3，另有回移版本 | [WAL-reset](https://www.sqlite.org/wal.html#walreset) | 实施时核实驱动内嵌 SQLite 为已修复版本，不只检查 npm 包名称 |
| 同步事务 | better-sqlite3 transaction 不支持 async 回调跨 await 保持事务 | [驱动 API](https://github.com/WiseLibs/better-sqlite3/blob/master/docs/api.md) | I/O 在事务外执行；结果与事件在同一短事务提交 |
| 备份 | SQLite Online Backup API 产生一致的数据库副本 | [Backup API](https://www.sqlite.org/backup.html) | 提供备份命令；禁止运行时只复制主 db 文件并宣称完整备份 |
| Worker | Worker Threads 适合 CPU 密集 JavaScript；普通异步 I/O 无需线程 | [Node Worker](https://nodejs.org/api/worker_threads.html) | 首版限制内置节点耗时与数据大小；以后按测量结果引入池，线程不是不可信代码沙箱 |
| SSE | EventSource 单向传输；规范定义事件 id、自动重连和 Last-Event-ID | [WHATWG SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html) | 命令使用 HTTP，状态使用 SSE；事件补发、保留窗口、去重仍需自行设计 |

研究限制：没有安装和运行上述完整框架，没有测量速度或在 Windows 10 真机上执行。网页展示的最新版本不自动成为 FlowStudio 锁定版本。此记录仅支撑后续技术论证。
