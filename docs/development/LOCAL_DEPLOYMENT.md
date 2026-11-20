# Windows 本地开发与运行

需要 Python 3.11+、Node.js 22.12+ 和 npm。以下命令从仓库根目录开始。

## 启动开发环境

在第一个 PowerShell 窗口启动后端：

```powershell
Set-Location backend
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

首次启动会创建 `backend/.venv` 并安装依赖。保持此窗口运行。后端默认监听 `127.0.0.1:8000`，可访问 [API 文档](http://127.0.0.1:8000/docs)，或用 `GET /api/v1/projects` 检查服务。

在另一个 PowerShell 窗口从仓库根目录启动前端：

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

打开 [FlowStudio](http://127.0.0.1:5173/)。Vite 开发代理将 `/api` 转发到 `127.0.0.1:8000`。项目操作见[前端使用指南](../../frontend/README.md)。

## 配置真实模型

如还没有 `backend/.env`，把 `backend/.env.example` 复制为 `backend/.env`。填写以下三项：

```dotenv
DEEPSEEK_API_KEY=填写实际密钥
FLOWSTUDIO_DEFAULT_MODEL=deepseek-flash
FLOWSTUDIO_MODEL_ALLOWLIST=deepseek-flash
```

启动脚本自动读取 `.env`。同名终端环境变量优先，修改后需要重启后端。`.env` 已被 Git 忽略，密钥只供后端使用。REAL 模式通过 DeepSeek 生成任务并执行校验；若未设置密钥，返回 `MODEL_AUTH`。TEST 模式读取显式选择的本地样例，`diamond` 需要规格包含需求 `R1` 和 `R2`。

## 同源运行和存储

在 `frontend` 运行 `npm.cmd run build` 后，回到 `backend` 运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

需先通过开发脚本初始化虚拟环境。启动脚本检测到 `frontend/dist` 时，由 FastAPI 同源提供页面和 API，此时打开 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)。

后端数据默认保存在 `backend/.flowstudio/flowstudio.db`，可用 `FLOWSTUDIO_DATA_DIR` 或 `FLOWSTUDIO_DATABASE_URL` 配置。升级前备份数据库。浏览器草稿和快照属于页面所在源，开发端口 `5173` 和同源端口 `8000` 使用各自的浏览器存储。

## 验证

- 前端：`npm.cmd run build`、`npm.cmd test`、`npm.cmd run test:browser`。浏览器测试读取生产构建，可设置 `CHROME_BIN` 和 `FLOWSTUDIO_TEST_BACKEND_URL`。
- 后端：使用后端虚拟环境的 Python，在仓库根目录运行 `python -m pytest backend/tests backend/tests_pm`。契约检查见[工程组织](PROJECT_STRUCTURE.md)。
- HTTP 冒烟：在 `backend` 目录以虚拟环境的 Python 运行 `python scripts/e2e_smoke.py --base-url http://127.0.0.1:8000`，使用 TEST 样例校验规格、规划、保存、布局和导出。

首次在干净环境验证可运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1 -Bootstrap
```

依赖已安装时，一键执行全部 TEST 基线：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1
```

脚本会执行后端测试、T05、契约与 OpenAPI 检查、前端构建、领域测试、浏览器回归，并自动启动同源后端完成浏览器联调、P0 HTTP 验收和 HTTP 端到端测试。P0 验收还会实际停止并重启后端，比较规格、计划、布局和导出 hash。结果写入 `evidence/runs/<timestamp>/summary.md`、原始 `logs/` 和浏览器截图。可使用 `-SkipBrowser` 或 `-SkipHttp` 跳过当前机器不可用的层；REAL 验收始终独立执行，不由该脚本伪造。

质量属性测量使用 `-Quality` 启用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1 -Quality
```

该模式会增加 100 任务/1000 边编排基准、100 任务持久化基准、事务失败注入、100 任务/200 边画布性能、键盘焦点和主题对比度测量。

REAL 验收在配置服务端密钥和明确用户规格后，从 `backend` 目录单独运行：

```powershell
python scripts\real_acceptance.py --base-url http://127.0.0.1:8000 --spec <设计规格.json> --evidence <仓库外证据.json>
```

该命令会执行 baseline、单项需求变化和 `standard → coarse` 粒度变化，记录模型、attempt、usage、耗时、候选和 must 需求映射；A05 人工签字不会由脚本伪造。

生成只包含 Git 跟踪源码的交付包，以及最近一次验收的脱敏日志/截图/测量证据：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_delivery.ps1
```

产物位于 `evidence/delivery/`，包含源码 ZIP、证据 ZIP 和 SHA-256 清单。
