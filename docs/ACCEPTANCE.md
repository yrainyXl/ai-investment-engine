# 验收清单（v0.1）

## 自动化测试

```bash
python -m pytest -q
python -m investment_engine.cli --source fixture --out output/latest-preview.json
```

必须满足：

- [ ] fixture 输出 `decision=no_trade`，`execution.order_submitted=false`。
- [ ] 四项证据缺失时返回 `insufficient_evidence`；禁止随意补齐缺失事实。
- [ ] 在 `as_of` 之后才披露的证据不允许作为当时已知事实。
- [ ] 历史数据缺失不伪造策略收益；日期异常时拒绝处理。
- [ ] 即使人工输入四项 positive 且均线看多，也只能形成被风控否决的 `buy_candidate`。
- [ ] 任意客户端 HTTP 请求失败时 fail-closed，不从 fixture 偷偷回退。
- [ ] 项目代码中不存在 POST /orders 调用。

## 接入验收（用户服务器）

- [ ] 通过受控环境变量提供仅有必要权限的 API token。
- [ ] `--source otw` 返回实际策略 ID、账户现金/持仓、策略文档时间，和 OTW 前台一致。
- [ ] 本地 JSON 不含 token，不包含任何真实券商凭证。
- [ ] 运行前后的 OTW `/runs` 和 `/orders` 数量不变。
- [ ] 服务器只通过内网/本机访问 preview API；未暴露公网无鉴权端点。

## 升级为写入阶段之前的强制门槛

- [ ] 明确处理旧实验审计缺口，记录用户的恢复授权验收。
- [ ] OTW 服务端实现/验证强制拒绝模拟订单的总开关。
- [ ] 后端已验证 A 股交易日、T+1、整手、停牌/涨跌停、费率、现金/仓位和报价时效。
- [ ] 真正的独立历史数据源和回测策略已建立，覆盖未来数据泄漏检查。
- [ ] `/runs` 写入幂等且可以读回；一个 `run_id` 只允许符合决策的纸面订单。
- [ ] 调度器唯一化，不与现有 09/11/14/17 定时任务产生重复执行。

以上未满足时不得声称“自动交易闭环已上线”。
