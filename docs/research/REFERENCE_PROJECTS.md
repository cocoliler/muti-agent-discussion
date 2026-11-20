# 参考项目研究与设计取舍
调研日期 2026-11-15。实际读取下列官方页面与部分源码；未运行这些项目，未锁定仓库 commit。结论限于列明文件，应用建议是 FlowStudio 设计判断，不是性能实测。
源码以链接供团队复核，不复制其实现或产品资源。

| 项目 / 研究模块与证据 | 已确认机制 | 本项目应用 / 不采用项 | 成本与风险 |
|---|---|---|---|
| Dify [workflow/index.tsx](https://github.com/langgenius/dify/blob/main/web/app/components/workflow/index.tsx) | nodeTypes/edgeTypes 注册、自定义注释、选择模式、draft 同步 hook、只读模式与变量类型匹配入口 | 保留主画布+按需详情；独立业务/布局；不搬运多人协作、数据集和工具市场 | 中；只读入口文件，未验证所有 hook 的内部事务 |
| n8n [execution order](https://docs.n8n.io/flow-logic/execution-order/) 与 [executions](https://docs.n8n.io/workflows/executions/) | 运行记录独立；官方说明部分执行规则与画布位置相关，多分支不等于并发 | 借鉴连线和历史表达；FlowStudio 明确依赖决定拓扑、布局无业务效力 | 低至中；不能照搬自动化节点语义到 SE 任务 |
| Flowise [Interface.ts](https://github.com/FlowiseAI/Flowise/blob/main/packages/components/src/Interface.ts)、[Agentflow V2](https://docs.flowiseai.com/using-flowise/agentflowv2) | INodeProperties/INodeParams/INodeData 分离元数据、配置和实例；输入输出及动态选项接口；流程状态与节点交接 | 建立 Task renderer registry、受控详情配置和显式输入引用；不引入任意 code 节点或实例序列化 | 中；部分接口 any 不应复制到严格领域 DTO |
| LangGraph [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | checkpoint/thread_id 与人工恢复；恢复可能重入节点 | P2 候选持久等待；副作用去重与业务库对账；不替代当前纯 DAG 算法 | 高于纯函数；恢复复杂度需 Windows 实验 |
| React Flow [Custom Nodes](https://reactflow.dev/learn/customization/custom-nodes)、[Save and Restore](https://reactflow.dev/examples/interaction/save-and-restore) | 自定义组件与 handles、nodeTypes 注册；nodes/edges/viewport 可恢复 | 复用现有 @xyflow/react；坐标/注释独立，业务保存经过后端校验 | 低；撤销、布局和业务校验不能假定库自动实现 |
| MetaGPT [product_manager.py](https://github.com/FoundationAgents/MetaGPT/blob/main/metagpt/roles/product_manager.py) | ProductManager 角色组织 PrepareDocuments/WritePRD 等活动，固定 SOP 可按序处理 | 借鉴角色/活动/中间产物；本项目输入已有 Spec，PM 主要输出任务而非重新写 PRD | 中；职责名字相同不代表输入输出相同 |
| OpenHands [README 架构与仓库边界](https://github.com/OpenHands/OpenHands/blob/main/README.md) | Agent Canvas 与 Agent Server/SDK、工具/会话/事件分工 | 参考 UI 与执行边界、产物和受控工作区；不嵌入整套执行部署 | 高；本轮只验证 README 边界，未审计执行沙箱源码 |
| DeepSeek [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) | JSON模式、提示示例、token上限、空白/截断处理 | Provider适配+严格Schema+有界修复，不把合法JSON当合法计划 | 中；质量、延时、usage与模型能力仍需真实测量 |

## 未验证范围
Flowise 最初尝试 packages/components/nodes.ts 返回404，后通过目录定位到 src/Interface.ts；不得引用不存在文件。
React Flow 自动布局概览两次超时；不据此宣称某布局器已验证。P0 保留本地已有拓扑分层，Dagre/ELK 作为后续候选实验。
未检查 n8n 编辑器源码、Flowise 实际 React Flow UI 注册实现、Dify 各节点执行和变量内部实现。当前 UI 取舍以入口源码、官方说明和用户提供截图为依据。
未锁定参考 commit；实施若复用接口或依赖必须固定版本再复核。

## 许可与采用边界
已查 [Dify LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE) 与 [n8n LICENSE](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)，均含使用限制，不能概括为无条件宽松许可。
其他项目本轮未逐包核验 LICENSE/SPDX，不作可直接复制结论。这里只参考机制、独立编写设计；如后续复制源码/图标/Pro 示例，必须逐项核验对应版本许可与署名要求。
