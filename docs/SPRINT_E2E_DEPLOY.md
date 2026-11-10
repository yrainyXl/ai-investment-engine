# E2E Sprint v0.6 — 单次部署与分阶段验收

仅在以下条件全部满足后交给 DSH：Worker A/B 完整代码进入同一个集成 PR；GitHub CI 全绿；两个不同行业/资产类型的研究 cycle 测试通过；合并后提供精确 SHA。不要因为某一模块通过单测就提前部署。

生产目录 `/opt/apps/ai-investment-engine`，保持 `deploy/v0.2` 分支、`127.0.0.1:8018`、`./data:/data`、当前全部 secrets 和 Docker 安全策略。目标为 `ai-investment-engine:0.6` / API 0.6.0。

## Gate 0：部署前
- 读生产当前 Git SHA、分支、工作树 clean、当前健康状态和已缓存数据条数。
- 对 GitHub 单次 bounded fetch，验证**精确 merge SHA 属于 main 且可 fast-forward**。如 github.com TLS 阻断、timeout，停止；不修改系统代理/DNS/hosts/firewall，不重复无限次。
- 不重启/触碰 OTW、数据库、代理、其它容器。
- 只进行一次 engine build/up；保留数据与 secrets，不重新同步已经成功的 CNINFO/99qh/行情。

## Gate 1：健康与兼容
- /healthz 200 version=0.6.0，orders_enabled=false；查看端口、安全选项与持久数据卷。
- 读取现有 13 条 Q3 财务事实、行情缓存、报告数量；禁止修改原始事实/人工审核凭证。

## Gate 2：来源和研究上下文
- 调用 POST /v1/research/cycle（研究 bearer；无订单权限），entity_id=601118、stock、as_of 真实当前时点。报告保留 source ID/发布日期/审核状态。
- Q3 的 13 条事实均未经复核；不得把它们标为 eligible。即使有行情数据，也不能凭价格认定利润传导。
- 用一只不同行业或 ETF 构造第二次 cycle；如果服务器没有该资产的真实源数据，应 `data_status=unavailable` / `insufficient_evidence`，不允许假数据伪装真实数据。

## Gate 3：假设与风险
- 引用现有 revenue、cost_of_revenue fact_id 运行差额假设，预期 `not_evaluable`，原因包括审核缺失。
- 风险模块如没有可信的只读真实持仓，只允许做**明确的 hypothetical fixture** 压力情景测试，不得贴上“真实账户”标签；该版本 `eligible_for_trading=false`。
- 估值与市场预期缺来源时应该输出 missing / not_evaluable，不得自动编数。

## Gate 4：决策报告与本地审计
- 输出必须 `action=no_trade`、`orders_enabled=false`、`eligible_for_trading=false`，并解释缺失证据和风险，持久化本地 `/data/reports/research_cycle/` 审计报告。
- 原人工 review endpoint 仍须独立 secret；缺失时保持禁用。禁止 DSH 代替人工审批。
- 回传每一 Gate 的 pass/fail 与实际返回片段、精确 deployed SHA、数据数量，且明确当前限制。

任何 Gate 出现关键失败，停止扩展动作并报告；不要私自修改线上代码、密钥、调度或 OTW。不做模拟成交和真实交易。
