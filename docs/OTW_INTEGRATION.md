# OTW 当前接口映射（已于 2026-11-05 通过 MCP 只读核对）

以下均为当前已有 OTW 接口，不是建议新建。使用占位符 `{id}`、`{asset_id}`，不要将个人 token 或账户详情写进仓库。

| 资源 | 现有接口 | 当前使用建议 |
|---|---|---|
| 纸面策略列表 | GET `/api/paper-trading/strategies` | 动态发现策略/关联投资组合；无需配置全局 OTW_STRATEGY_ID |
| 当前账户 | GET `/api/paper-trading/strategies/{id}/context` | 引擎直接 GET；现金/权益/持仓权威读取 |
| 历史分析 | GET `/api/paper-trading/strategies/{id}/runs` | 读取已执行判断，包括 no_trade |
| 保存分析 | POST `/api/paper-trading/strategies/{id}/runs` | **v0.1 不调用**；未来与幂等键联合使用 |
| 模拟订单 | GET/POST `/api/paper-trading/strategies/{id}/orders` | **v0.1 只读，不实现 POST** |
| 全局策略文档 | GET `/api/paper-trading/strategies/{id}/documents/{asset_id}` | 读取 TipTap JSON，拿 `updated_at` 做上下文版本 |
| 文档更新 | PUT `/api/paper-trading/strategies/{id}/documents/{asset_id}` | v0.1 不调用；以后以 `expected_updated_at` 乐观锁更新 |
| 历史数据列表 | GET `/api/histdata/datasets` | 当前返回空数组；先由服务端下载 |
| 历史 K 线 | GET `/api/histdata/datasets/{id}/bars` | 待 datasets 就绪后适配，需保留并行的时间戳/价格列 |
| 预设回测策略 | GET `/api/backtest/strategies` | 当前返回空数组；不等于回测服务不存在 |
| 数据连接器 | GET `/api/connectors?module=histdata` | 已存在 China Market，无需新增密钥，但需核对历史日线来源和质量 |

### 已确认的请求语义

`POST /runs` 的已知字段包括：`idempotency_key`、`decision`、`confidence`（必填），`scheduled_at`（RFC3339，可选）、`evidence`、`market_facts`、`market_expectations`、`inference`、`risk_assessment`。v0.1 仅导出其 payload 预览。

`POST /orders` 必须提供 `idempotency_key`、`run_id`（已写入分析记录的 UUID）、`asset_id`、`side`、`quantity`。OTW 服务端负责报价、模拟成交价、手续费和自身的资金/仓位风控，客户端不应提交价格。

`PUT /documents/{asset_id}` 使用 `expected_updated_at`，且 `markdown` 或 TipTap `content` 二选一；冲突返回 409。全局策略不应在每日无变化时重复写入。

### 已知阻断项

- 现有“暂停模拟成交”明确出现在历史研究记录和全局策略文档，**尚未验证 OTW 服务端本身有强制冻结开关**。以后放开下单必须补后端开关。
- 没有任何历史 K 线 dataset，不能据此声称已有可用回测样本。
- 研究历史曾有价格多源不一致和交易所原始数据未核实情况，不能把参考报价当作正式成交价/结算价。
- ChatGPT MCP 有效不代表任意服务器上的 `OTW_API_TOKEN` 可用。生产鉴权、网段、TLS 以及访问审计必须由用户侧验证。
- 2026-11-05 的接口只读核查与项目生成均未创建 run、订单、策略或文档。

### 引擎 v0.1.1 的动态发现策略

- 每次读取 OTW `GET /api/paper-trading/strategies` 获取可见列表，使用 OTW 返回的 `id`、`portfolio_id` 和 `status` 标识模拟账户。
- `GET /v1/strategies` 在引擎侧提供经过 `ENGINE_API_TOKEN` 鉴权的只读策略列表。
- `POST /v1/analysis/preview` 用请求体 `strategy_id` 选择目标；若省略，只允许恰好一个 active 策略时自动推断，多于一个 active 时返回 409。
- CLI `--list-strategies` 列出全部可见模拟策略；`--all-active` 只读遍历 active 策略（不发送 OTW POST、PUT 或任何模拟订单）。
- 在后续版本中引入独立的**策略运行注册表**，区分 OTW 的模拟投资账户 ID 与研究算法策略 ID。新发现的模拟账户应默认处于未启用自动交易状态，只有完成研究算法、历史数据、风险规则绑定与审批后才允许自动执行。
- v0.1 的每次分析仍仅支持单标的，遇到多资产组合明确报错，避免把首只资产的分析误认为整个账户的结论。


## v0.2 MCP 传输与研究集成

当前服务器已验证的鉴权方式是 OTW MCP：REST 的 paper-trading 端点只接受 session cookie，而服务器只读 MCP Token 通过 Bearer 访问 /api/mcp。引擎使用 OTW_TRANSPORT=mcp 时，仅发送 JSON-RPC tools/call → otw_read，读取以下路径：

- /api/paper-trading/strategies
- /api/paper-trading/strategies/{id}/context
- /api/paper-trading/strategies/{id}/documents/{asset_id}

引擎不调用 otw_write 或 otw_compute。MCP Token 仍只需 paper-trading read 权限。

市场数据由独立 AKShare provider 获取并写入引擎自身的数据目录，不写入 OTW histdata。A 股 601118 使用 stock_zh_a_hist；ETF 使用 fund_etf_hist_em；期货参考序列使用 futures_zh_daily_sina。数据来源、抓取时间和复权口径随每根 Bar 保存。

研究 API POST /v1/research/preview 读取 OTW 策略和对应标的全局策略文档，再关联本地已缓存的日线数据。回测 API POST /v1/backtests/run 只使用引擎本地历史数据。以上 POST 都是引擎自身的研究 API，不是 OTW 写入接口。

**数据边界：** RU0 主力连续序列不是可交割单一合约，也不是海南橡胶实际采购/销售结算价；A 股 OHLCV 不能证明天然橡胶实体供需、库存变化或公司利润传导。上述假设仍需要独立、带时间戳和来源的基本面证据。
