# 工程组织与契约管理

## 已跟踪目录

```text
frontend/
  src/                       # 项目工作区、规格、任务、画布和浏览器存储
  src/api/generated/         # 契约生成类型
  tests/                     # 领域测试与 Chromium 端到端测试
tech/
  flowstudio_contracts/      # Pydantic 模型，字段与约束的事实源
  artifacts/                 # JSON Schema、OpenAPI、样例、哈希清单
  scripts/export_contracts.py
  docs/                      # 系统架构与技术选型
backend/
  app/                       # FastAPI 入口、api/application/domain/persistence/providers
  migrations/                # Alembic 迁移（首个 revision 建全部表）
  tests/                     # T05 编排器 unittest + 本目录 api/ 下的 pytest 集成测试
  tools/export_openapi.py    # 与 tech 操作表比对，检测路由/响应漂移
  fixtures/                  # TEST 模式 fixture（文件名即 fixture_id）
  scripts/                   # dev.ps1 / start.ps1
  requirements.txt, alembic.ini, .env.example
  flowstudio_orchestration/  # 确定性编排器（T05，纯函数，只依赖契约）
docs/                        # 产品、交互、实施、验收及设计参考
```

集成基线和当前交付状态见[实现审计](../PROJECT_AUDIT.md)。`backend/` 已包含 T02/T07 的 FastAPI 入口、应用用例与仓储；模型调用端口留给 T04，REAL 未配置时显式失败。旧 CLI 原型位于独立分支。

## 契约变更与协作

1. 在 `tech/flowstudio_contracts` 修改模型及不变量。
2. 从仓库根目录运行 `python tech/scripts/export_contracts.py`，一并提交 Schema、OpenAPI、示例、TypeScript 和清单。
3. 运行 `python tech/scripts/export_contracts.py --check` 检查漂移，并使用 `tech/artifacts/examples` 验证前后端消费者。前端在 `frontend/src/api/generated/contracts.ts` 获取生成类型，导入校验读取生成的 Schema。
4. 前端草稿、来源标签、布局视图可定义自己的状态，通过边界适配传入正式 DTO。浏览器预检与服务端编排共享验收用例，服务端报告负责正式保存。

`docs/contracts/` 保留为既有设计附件。实际消费路径见[契约模块说明](../../tech/README.md)。
