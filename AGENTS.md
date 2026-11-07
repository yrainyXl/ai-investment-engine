# Agent 操作约定（ChatGPT / DSH / OTW）

正式施工计划：[docs/CONSTRUCTION_PLAYBOOK.md](docs/CONSTRUCTION_PLAYBOOK.md)

实时任务状态：[docs/PROGRESS_LEDGER.md](docs/PROGRESS_LEDGER.md)

1. **唯一代码基线是 GitHub**：ChatGPT 主责需求、业务代码、测试、PR 和进度；DSH 主责服务器拉取固定 SHA、构建、部署、验证和回滚。服务器上遇到业务代码问题，报告给 ChatGPT，避免长期维护未回传 GitHub 的私有分叉。
2. **当前版本 v0.2 仍只读 OTW**：允许从 OTW MCP `otw_read` 读取策略、context 和文档；允许将行情与研究/回测报告写入 Engine 自己的 `/data`。禁止对 OTW 调用 `otw_write`、创建 runs/orders、修改持仓、修改策略文档或下真实证券订单。
3. **模拟交易必须保持冻结**：旧实验审计缺口尚未完成恢复验收。即使用户口头希望“自动交易”，也必须独立处理服务端冻结开关、风控、幂等、审批等准入事项后才能进入实施。
4. **派发必须可核实**：只有 ChatGPT 真正调用 DSH 提交接口获得 task_id，才可记为 dispatched；GitHub Issue、执行任务书和聊天提示词都不是实际派发成功。
5. **结果回传必须可核实**：DSH 返回 task_id、目标与实际 SHA、结果状态、health、数据时间/来源、脱敏错误信息；ChatGPT 才依据验收证据更新进度台账。未知结果不可写 done。
6. **权限与审批**：服务器执行使用最小权限；未经明确批准，不调整网络、密钥、数据库写权限、定时调度或运行服务。不得暴露无限制 Shell/root 网关；不要将批准文本当成服务端鉴权凭据。
7. **资料安全**：禁止提交 `.env`、token、私钥、账户明细与生产日志。OTW Token 与 ENGINE_API_TOKEN 不能混用；市场数据持久化于被 Git 忽略的 `data/`。
8. **投研证据与成本**：必须区分事实、市场预期与推断；数据来源/发布时间/生效时间/抓取时间可追溯；错误或不可用数据要明确报错，不能伪造。控制 Harness Token 消耗，优先分析必要 diff、避免反复全仓扫描。
9. **操作服务器前**阅读 [docs/HARNESS_DEPLOY_TASK.md](docs/HARNESS_DEPLOY_TASK.md)，以部署现场的确认版本和用户审批为准。不得停机、清理其他容器、改变防火墙或代理。
10. **不新增重复调度**：既有 ChatGPT 盘前/盘中/收盘任务暂不迁移。每轮只读任务和部署都不得触发模拟成交；将来迁移必须先并行核对再切换。
