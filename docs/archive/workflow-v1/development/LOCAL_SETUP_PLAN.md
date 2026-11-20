# Windows 本地运行计划

**当前尚无可启动应用。本文中的命令是后续 Phase 1–8 必须交付的脚本契约，本轮不执行、不声称已经验证。** 目标是 Windows 10/11 x64、本机 SSD、普通用户权限、Node.js + npm，无数据库服务或中间件安装。

## 支持环境与首轮安装门槛

推荐 Node.js 24 LTS 的经验证补丁版，锁定在未来 `engines`、版本文件和安装文档中；不写“任意最新版”。2026-11-14 官方发布表仍将24列为 LTS，此处选择24是兼容稳定性建议，不代表它是当日最新 LTS。[Node.js 发布表](https://nodejs.org/en/about/previous-releases)

数据库随应用驱动提供，无需用户安装 SQLite 命令行。better-sqlite3 提供主要平台的预编译包，但原生安装仍可能因 Node ABI/架构/网络缓存不匹配而回退编译；官方故障说明也列出了 Windows 构建工具路径。因此“只装 Node 即可”必须经过实际验证，不能只依据 README 推断。[驱动 README](https://raw.githubusercontent.com/WiseLibs/better-sqlite3/master/README.md)；[安装故障说明](https://github.com/WiseLibs/better-sqlite3/blob/master/docs/troubleshooting.md)

Phase 1 建立以下环境矩阵并保存真实结果：Windows 10 x64、Windows 11 x64；锁定 Node/npm；全新 npm cache；无 Python/Visual Studio C++ 工具链；英文和中文/空格工作目录；普通用户权限。记录安装日志、驱动版本与 `SELECT sqlite_version()`，不得将源码编译成功冒充预编译安装成功。若当前版本不满足，先调整锁定依赖或重新评审驱动，不把额外系统工具作为默认用户前提。

WAL 修复版本基线采用内嵌 SQLite ≥3.51.3；官方也列有部分旧分支回移，但 P0 不增加多分支允许列表。启动检查的是驱动实际引擎版本，不能读取系统 `sqlite3 --version` 代替。[SQLite WAL 修复说明](https://sqlite.org/wal.html#walreset)

## 预期首次启动

以下命令在对应脚本实现后，从仓库根目录在 PowerShell 运行；仓库 URL 由团队创建后填写。

```powershell
git clone <FlowStudio仓库URL>
cd FlowStudio
node --version
npm --version
npm ci
npm run db:init
npm run dev
```

`db:init` 创建数据目录、申请与服务一致的独占维护锁、执行迁移；对已有库只应用未执行迁移，不重置数据。`dev` 通过跨平台 Node launcher 启动 Vite 与 Fastify，处理 Windows 子进程退出；Vite `127.0.0.1:5173` 代理 `/api` 到后端 `127.0.0.1:3000`。命令退出时不留下孤儿后端。

展示/日常使用流程：

```powershell
npm run build
npm start
```

`start` 只运行一个 Node 后端进程，提供构建后的静态资源和 API/SSE，浏览器访问 `http://127.0.0.1:3000`。如果缺少构建产物、迁移未完成或数据版本过新，输出可操作错误并退出；不会自动拉取在线资源。

首次安装依赖及 Playwright 浏览器需要网络或预先准备的本机缓存；**基础功能离线**指依赖安装完成后的项目管理、编辑、校验、运行、审批与历史。应用不使用 CDN/在线字体。离线验收关闭外网但保留 loopback，不阻止localhost。

## 数据目录与配置

默认根目录下 `.flowstudio/`，包含数据库、日志、备份和实例锁；整目录加入 Git ignore。可用绝对路径环境变量切换：

```powershell
$env:FLOWSTUDIO_DATA_DIR = 'D:\FlowStudioData'
npm start
```

变量只对当前会话生效；应用启动时显示解析后的数据路径。只允许本机目录，不推荐 OneDrive/同步盘，不支持网络共享数据库。目录切换视为选择另一个工作空间，不自动复制原数据。WAL 仅允许一个写者，且不适用于网络文件系统，这是设计限制而非性能调优选项。[SQLite WAL](https://sqlite.org/wal.html)

首版配置只保留端口、数据目录、日志级别；并发、保留策略、UI偏好通过受校验设置管理，执行时将相关策略冻结到 run。不能经环境变量意外开放远程接口。端口变更不能绕过同一数据目录的单实例锁。

## 脚本应满足的行为

|未来命令|职责|完成/失败的判断|
|---|---|---|
|`npm run db:init`|建目录和应用迁移|可重复、失败回滚、打印版本|
|`npm run db:check`|版本、外键、quick_check、数据路径检查|有问题非零退出；不修复/删除用户数据|
|`npm run dev`|开发前后端生命周期|一个命令启动，Ctrl+C 清理子进程|
|`npm run build`|类型检查并构建前后端|缺失类型/浏览器引入服务端模块即失败|
|`npm start`|单进程本地完整应用|READY后才接运行；无外部服务依赖|
|`npm run check`|lint、类型、单元、集成|非零退出能追踪到失败项目|
|`npm run test:e2e`|真实后端与临时数据库的浏览器测试|不触碰用户工作空间|
|`npm run backup -- --out <绝对路径>`|服务停止后的SQLite一致性备份|维护锁、目标不存在、完整性检查|
|`npm run restore -- --from <备份> --data-dir <新目录>`|恢复到新数据目录|目标为空、版本兼容、完整性检查|

所有脚本由 Node 实现跨平台路径与进程控制；不依赖 Bash 的 `rm`、`cp`、环境赋值语法。若 PowerShell 因执行策略拦截 npm.ps1，文档可指导调用系统同目录 `npm.cmd`，不要求用户降低全局执行策略。

## 停机、备份与恢复

关闭浏览器不停止运行；关闭后端或 Ctrl+C 会停止调度，最多5秒中止收敛，未完成运行记 INTERRUPTED。强制结束进程后，下次启动执行相同的未终态中断扫描，包括排队与审批等待；没有自动续跑或任意暂停恢复。

P0 的备份/恢复走停止服务后的维护命令，确保拿到相同数据目录锁。备份使用 SQLite 驱动备份 API 或在连接完全关闭后建立一致快照；不得在线只复制 `.db` 并丢弃 WAL。备份记录 schema/app版本、生成时间和校验摘要。恢复永远写新目录，校验后用户通过数据目录设置选择；原目录保留，应用不自动覆盖。首次恢复需在临时目录演练，验证项目、不可变版本、结果和历史可读。

## 故障诊断边界

|现象|应显示的诊断与处理|
|---|---|
|端口被占|地址与占用提示，选择另一端口或关闭已知实例；不自动结束他人进程|
|数据目录锁存在|说明同一工作空间已有实例；仅确证进程已死才允许陈旧锁清理|
|原生模块加载失败|显示锁定Node/架构/驱动信息，执行安装矩阵排查；不自动安装系统工具|
|数据库只读/磁盘满|健康为不可写，停止新调度；恢复存储后重新启动，不能显示未落盘的成功|
|schema高于应用支持|拒绝写入，使用匹配应用或恢复兼容备份|
|电脑睡眠后超时|按持久deadline检查，承认实际处理晚于截止，不承诺睡眠期间执行|

维护脚本必须把路径解析为绝对路径并检查目标边界，删除只针对指定终态历史/日志。P0 无自启动服务、Docker 文件、数据库管理服务或系统任务计划配置。
