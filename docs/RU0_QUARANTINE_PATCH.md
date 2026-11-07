# RU0 连续期货行情异常修复与现场执行说明

关联： [Issue #3](https://github.com/yrainyXl/ai-investment-engine/issues/3) · [进度台账](PROGRESS_LEDGER.md)

## 真实故障

2026-11-07，DSH 从服务端实际调用 AKShare `futures_zh_daily_sina("RU0")`，历史数据返回非空，但由于 2023-08-28 的 OHLC 不一致，v0.2 严格质量门阻止整段历史行情写入：

- 日期：`2023-08-28`
- `open=13270`、`high=13425`、`low=13225`、`close=13200`、`volume=269600`
- `settle=0.0`（原始独立字段，不是用于计算的 close）
- `close < low`，共 2144 条过滤后记录仅一条异常
- 原始接口的 `close` 与 `settle` 是**不同字段**，不能将上述异常解释成结算价越界。
- DSH 已实际确认此前失败没有写入 `future_RU0_continuous.csv`，且没有触碰既有 601118 数据和回测报告。

AKShare 官方接口说明： https://github.com/akfamily/akshare/blob/main/docs/data/futures/futures.md

## 修复策略

在原有严密 `validate_bars` 不变的前提下，**只对一条已经核验的来源异常例外**：必须同时匹配 `symbol=RU0`、`asset_type=future`、`adjustment=continuous`、日期 `2023-08-28` 和完整 `O/H/L/C=(13270,13425,13225,13200)`，且唯一错误为 inconsistent OHLC。

- 不擅自修改 high、low、close；不把 settle 代替 close。
- 不将该条作为合法 OHLCV 写入 `.csv`；排除该点，其他条严格验收后方可入库。
- 精确保留该条原始数据（含 volume、settle、来源、抓取时间和 issue 链接）到 `future_RU0_continuous.quality.json`。
- 同步结果必须返回 `source_record_status=partial_quarantined`、`quarantined_count=1`、`source_rows_in_window` 和 `quarantine_file`，且质量 warning 明示隔离。
- 本地序列列表、读取行情 API、研究快照和回测报告携带质量隔离元数据，不能静默把“不完整”当成完整历史。
- 其他日期或其它值的异常仍失败关闭；即便同一天上游更改数据后仍异常，也不宽容处理。
- 隔离后的样本少了一天；特别注意 `2023-08-25 → 2023-08-29` 的缺口会影响部分滚动指标及回测，研究报告不应把它当成无缝可交易合约。

## DSH 现场升级指令（仅在 GitHub PR 通过 CI 并合并之后）

本任务只允许升级独立投资引擎，不得修改 OTW / DeepSeek Harness / 其他服务 / Token / 定时任务 / 实盘或模拟订单。

1. 保留当前健康容器及 `/data`；确认当前运行 SHA、分支、工作树状态和 `127.0.0.1:8018` 本地监听。
2. 用正常 `git fetch origin` 获取已合并的 main，在原有部署分支上以安全的快进方式更新至**固定 SHA**（由 ChatGPT 任务明确指定），不要使用 `reset --hard`、`push --force`。若本地提交分叉或工作树不干净，停止并报告。
3. 按现有 Compose 重建本项目 `engine`，保持现有 `.env`、`OTW_TRANSPORT=mcp`、`ENGINE_API_TOKEN`、只读根文件系统及端口，不触碰其他容器。
4. 健康检查成功后，执行**一次** `docker compose exec -T engine python -m investment_engine.market_cli sync --asset-type future --symbol RU0 --start-date 20180101 --adjustment continuous`。
5. 回传实际 SHA、容器健康、请求返回的 `source_record_status`、`source_rows_in_window`、`stored_count`、`quarantined_count`、`quality.valid`、warnings、首末日期，以及 `quarantine_file` 的相对路径。不得打印完整环境变量、Token 或其它服务日志。
6. 回传 `GET /v1/market-data` 或 CLI list 中 RU0 的 `excluded_rows`，并核实原有 601118 缓存及报告存在。仅本地数据写入，**不运行 OTW 写入/下单**。
7. 如果出现不同日期/更多异常，停止重试并报告，不修改校验规则或填补虚构数据。

该 Patch 不开放模拟交易；所有研究和回测仍标记不可用于直接交易。
