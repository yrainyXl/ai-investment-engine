# Agent 操作原则（OpenTraderWorld / DeepSeek Harness / Coding Agents）

1. 本仓库 v0.1 只有 read-only preview，不允许下真实股票订单、OTW 纸面订单，也不允许主动对 OTW POST/PUT/PATCH/DELETE。
2. OTW 是账本和真实数据来源；本服务只读取现有 OTW `/context` 和 `/documents/{asset_id}`，禁止借历史聊天内容伪造接口结果。
3. 操作服务器前阅读 `docs/HARNESS_DEPLOY_TASK.md`，遵循审批边界；不得停机、清理其他容器、改变防火墙和代理。
4. 禁止提交 `.env`、token、私钥、生产账户导出和敏感日志。严禁把 OTW Token 与 ENGINE_API_TOKEN 混用。
5. 首轮完成健康检查、合成数据 smoke test、OTW 真实只读联调即可。不要擅自增加自动调度和模拟成交权限。
6. 任何投资信号只是研究提案；无可验证数据时输出 `insufficient_evidence` 与 `no_trade`。
7. 执行结果必须报告 Git SHA、测试/联调实际状态、可审计的非敏感摘要及未完成事项。
