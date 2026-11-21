# v0.8 研究数据与估值口径

研究入口使用 Engine 已有的本地缓存，股票代码 `601118.SH` 对应缓存标识 `601118`。可用现有 CLI 将公开行情写入独立数据目录。以下命令使用 AKShare 公开行情数据源及北京时间交易日。运行时将结束日期改为已结束的最近交易日。

```bash
export ENGINE_DATA_DIR="$PWD/research-demo-data"
investment-engine-market sync --symbol 601118 --asset-type stock --start-date 20250101 --end-date 20261120 --adjustment none
investment-engine-market sync --symbol 510300 --asset-type etf --start-date 20250101 --end-date 20261120 --adjustment none
```

日线需已经收盘，且抓取时间须晚于该日收盘校验时点并不晚于研究时点。本地历史行情由最后一次抓取的记录组成，过去抓取版本未保留时会形成历史证据缺口。研究会检查 OHLCV、标的、来源、复权、币种、成交量单位和覆盖区间。收盘价超过七个自然日会标记过期，仍可用于覆盖时段内的历史实验。前复权系列仅提供信息性摘要。原始成交量单位未知时保留提供方口径。

财务事实、公告及行业观察来自同一 `ENGINE_DATA_DIR/evidence`。行业观察和 ETF NAV 可通过受控离线流程写入 `EvidenceStore.save_observations`。发布、生效、取回及人工核对必须在研究时点前。使用已核对的观察时，在 `metadata.reviewed_at` 记录带时区的核对时间，保留来源、单位、标的关联和 `reviewed_by_human`、`unit_verified` 标记。市场预期须有来源并有独立证据类型。

## 受审核估值数据

P/E 和 P/B 分别使用已有未复权收盘价与最新、同口径且已审核的每股指标。P/E 接受 `earnings_per_share`、`ttm` 和连续十二个月期间。P/B 接受 `book_value_per_share`、`point_in_time`，期间起止为同一天。两者均要求 `unit=CNY/share`、`value_scale=yuan_per_share`、`currency=CNY`、`statement_scope=consolidated`。ETF 折溢价使用收盘同日、已核对的基金单位净值观察，指标为 `nav_per_share` 或 `etf_nav`，单位为 `CNY/share` 或 `CNY/unit`。

现有季度 PDF 解析器仍只提取其能够核对的字段。导入独立发布的每股指标或 DCF 资产负债事实时，受控导入脚本使用 `FinancialFactStore.save([FinancialFact(...)])`，传入唯一事实和原始披露标识、HTTPS 来源、页码、期间、单位、值、披露与抓取时间。所有新导入事实初始待审。经人工核对来源后，使用独立审核凭据访问既有 `POST /v1/fundamentals/facts/{fact_id}/review` 写入审核记录。重复导入保留相同来源与数值的既有审核状态。

DCF 还需同一披露时点的 `net_debt`（`CNY` / `yuan`）和 `shares_outstanding`（`shares` / `shares`），两者均为 `point_in_time` 的合并口径。管理员可在受控运行时数据目录中提供 `evidence/dcf_assumptions_<symbol>.json` 版本数组。假设以 `assumptions_as_of` 选择当时可用的最新唯一版本，由管理员在来源及人工审核明确后写入。请求与模型无法提供此文件。字段如下。

| 字段 | 含义 |
| --- | --- |
| `entity_id`、`currency` | 标的裸代码、`CNY` |
| `assumptions_id`、`source_name`、`source_url` | 假设版本的来源标识、名称和 HTTPS 记录 |
| `assumptions_as_of`、`reviewed_at`、`reviewed_by_human` | 版本时点、人工复核时点、复核标记 |
| `forecast_fcf`、`discount_rate`、`terminal_growth` | 十进制字符串形式的年度 CNY 自由现金流数组、折现率、永续增长率 |
| `net_debt_source_id`、`shares_outstanding_source_id` | 一致披露期间的已审核事实 ID |

DCF 的预测现金流、折现率和永续增长率属于**审核后的情景假设**。估值缺少足够来源时会返回 `not_evaluable` 并保留方法级缺口。

## OTW 账户配置

仅启用服务器可信配置映射的账户，并验证策略与组合的双向关联。使用 `ENGINE_OTW_RESEARCH_BASE_URL`、`ENGINE_OTW_RESEARCH_TOKEN` 和 `ENGINE_OTW_RESEARCH_STRATEGIES`（JSON，如 `{"stock:601118":"<OTW 策略 UUID>"}`）。新研究入口只使用 OTW REST GET，生产需由管理员确认部署环境可用的只读 REST 认证。现有 MCP 认证接口独立保留。缺少可用 REST 身份时会报告账户风险未知。

最新研究在账户请求完成后确定截至时点。显式历史请求不会用当前计算的账户快照回填。账户风险检查快照、报价、完整性、币种与权益勾稽，报告只持久化研究用权重及来源时间等摘要。行业暴露和回撤需另外提供点时历史证据。
