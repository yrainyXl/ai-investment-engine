# OTW 当前接口映射（已于 2026-11-05 通过 MCP 只读核对）

以下均为当前已有 OTW 接口，不是建议新建。使用占位符 `{id}`、`{asset_id}`，不要将个人 token 或账户详情写进仓库。

| 资源 | 现有接口 | 当前使用建议 |
|---|---|---|
| 纸面策略列表 | GET `/api/paper-trading/strategies` | 寻找策略并核对状态 |
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
