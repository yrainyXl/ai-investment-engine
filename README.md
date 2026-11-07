# AI Investment Engine v0.4

OTW 外置投资研究引擎。当前定位是**只读研究与历史回测**：读取 OTW 真实账户上下文，通过 AKShare 获取公开日线数据，建立可追溯的数据缓存，评估投资假设并运行透明的基准回测。不会写入 OTW runs/orders/documents，也不会连接券商或下真实订单。

## 项目施工与多 Agent 分工

- [施工总纲（目标架构、ChatGPT / DSH / OTW 职责、审批与派发协议）](docs/CONSTRUCTION_PLAYBOOK.md)
- [任务进度台账（基于真实 DSH 返回更新）](docs/PROGRESS_LEDGER.md)
- [DSH 连接只读能力探查 Issue #2](https://github.com/yrainyXl/ai-investment-engine/issues/2)

部署和开发状态分开记录；GitHub 合并不等于服务器上线，Issue 创建不等于 DSH 已收到任务。

## 本阶段已实现

- OTW REST / MCP 只读传输；当前服务器使用 OTW_TRANSPORT=mcp，仅调用 otw_read。
- 动态发现 OTW 模拟策略，不需要固定 OTW_STRATEGY_ID。
- AKShare 日线数据适配：A 股、场内 ETF、国内商品期货连续序列。
- 统一 OHLCV 数据结构，保留来源、抓取时间、复权口径、交易所、单位。
- 本地 CSV 缓存，按 symbol / asset_type / adjustment 分开存储；再次同步时会从已有序列最早日期刷新，避免前复权因子或期货连续序列变动后混用新旧历史数据。
- 研究快照与回测报告持久化到 /data/reports，可通过 API 列出；不写入 OTW。
- 数据质量检查：时间顺序、重复日期、未来数据、OHLC 关系、价格/成交量异常；交易日间隔只作为日历提示。
- 研究快照：股票与期货价格观察；把确认事实、市场预期、个人推断分开登记；缺少来源时明确输出 unknown。
- 历史回测：买入持有基准、SMA 趋势策略、手续费/滑点、年化收益、波动率、Sharpe、最大回撤、正收益日比例、仓位暴露和换手。
- 回测信号使用收盘后数据，下一交易日开盘执行；不使用同一根 K 线未来收盘价。
- Docker 数据持久化挂载，PyPI 镜像通过 build arg 配置。

## 安装

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[api,market-data,fundamentals,test]'
python -m pytest -q
~~~

AKShare 是可选依赖，导入采用 lazy import。未安装时，数据同步会明确报错，不会返回模拟行情。

## 获取海南橡胶和天然橡胶数据

A 股日线（前复权）：

~~~bash
python -m investment_engine.market_cli sync \
  --asset-type stock --symbol 601118 \
  --start-date 20180101 --end-date 20261105 --adjustment qfq
~~~

天然橡胶主力连续参考序列：

~~~bash
python -m investment_engine.market_cli sync \
  --asset-type future --symbol RU0 \
  --start-date 20180101 --end-date 20261105 --adjustment continuous
~~~

查看本地缓存：

~~~bash
python -m investment_engine.market_cli list
~~~

当前 AKShare 适配使用 stock_zh_a_hist 获取 A 股日线、fund_etf_hist_em 获取 ETF 日线、futures_zh_daily_sina 获取期货连续/合约日线。AKShare 上游接口可能随数据网站调整而变化，失败时会返回 unavailable/error，不会编造数据。

**复权口径：**
- A 股/ETF 默认 qfq（前复权），适合连续收益与技术指标研究。
- none 为不复权，适合核对原始交易价格；分红送转会形成价格跳空。
- hfq 为后复权，历史值可能出现异常或负值；本项目价格校验会拒绝非正价格，不建议作为默认研究口径。
- 期货连续序列标记为 continuous。主力连续合约有换月拼接影响，不等于可交易的单一合约，也不能直接代表海南橡胶的实际采购/销售结算价。

成交量单位保留在数据元信息中：A 股为接口返回的“手”，ETF 暂标记 provider_units（不擅自假定份额或手），期货为“手/合约”；成交额若上游未提供则留空，不会推算。每次同步会报告 requested_start_date 和 effective_refresh_start_date。

## 回测

SMA 20/60 趋势策略：

~~~bash
python -m investment_engine.market_cli backtest \
  --symbol 601118 --asset-type stock --adjustment qfq \
  --strategy sma_trend --fast-period 20 --slow-period 60 \
  --fee-bps 10 --slippage-bps 5 --out output/601118-sma.json
~~~

买入并持有基准：

~~~bash
python -m investment_engine.market_cli backtest \
  --symbol 601118 --asset-type stock --adjustment qfq \
  --strategy buy_and_hold --out output/601118-benchmark.json
~~~

回测约定：
- 用 t 日收盘数据计算信号，t+1 日开盘执行；收益按开盘到下一开盘计算。
- 研究和回测按北京时间 15:05 作为日线收盘安全边界；此前不会把当天尚未完成的日 K 线纳入判断。
- 手续费和滑点以单边 basis points 配置，买入/卖出均计成本。
- 仅多头、无杠杆；没有模拟涨跌停排队、停牌成交、冲击成本、印花税差异、分红现金流、退市样本和幸存者偏差处理。
- 使用前复权数据时，回测收益采用供应商复权价格序列，不代表历史真实成交价。
- 结果只用于研究策略行为，不具备交易资格，不构成收益预测。

## 投资假设与证据

研究快照：

~~~bash
python -m investment_engine.market_cli research \
  --futures-symbol RU0 --out output/hainan-rubber-research.json
~~~

命令通过 OTW MCP 只读获取策略上下文和全局策略文档，并读取本地已经缓存的 601118、RU0 数据。也可以显式传 --symbol、--strategy-id、--evidence evidence.json。

证据文件按三类保存：

- facts：确认事实，例如已披露产量、库存数据、公司财报。必须有人审阅并提供来源 URL、发布日期、生效日期、实际获取日期。
- expectations：市场预期，例如一致预期、分析师预测或当时公开的市场预期记录。
- inferences：个人推断，例如“胶价上涨可能改善公司利润”。推断不会被当成事实或市场预期。

单条证据示例：

~~~json
{
  "facts": [
    {
      "factor": "multiweek_inventory_trend",
      "assessment": "supporting",
      "title": "青岛天然橡胶库存连续下降",
      "detail": "填写经核实的库存变化、统计范围和单位",
      "source_url": "https://example.com/source",
      "published_at": "2026-11-04T08:00:00+08:00",
      "effective_at": "2026-11-03T00:00:00+08:00",
      "retrieved_at": "2026-11-04T10:00:00+08:00",
      "reviewed_by_human": true,
      "confidence": 0.8
    }
  ],
  "expectations": [],
  "inferences": []
}
~~~

assessment 只能由人审阅后标为 supporting 或 contradicting。证据发布时间、生效时间、获取时间任何一个晚于 as_of，都不能用于该时点的历史判断。示例 URL 是占位符，不代表真实证据。

对海南橡胶，OHLCV 和 RU0 只能提供价格与趋势观察，不能证明实际供给变化、库存变化、公司利润传导或市场已计价预期。上述基本面证据缺失时，研究结果会保持 unknown / insufficient_evidence。

## M3 财报结构化提取

同步 CNINFO 公告元数据后，提取指定季度报告：

~~~bash
python -m investment_engine.market_cli sync-financials --symbol 601118 --period 2026-Q3
~~~

提取结果保存在 `/data/evidence/financial_facts.json`。每条记录保留精确十进制值、报告期间、合并报表口径、发布日期、抓取时间、来源 PDF 和页码。所有结果均标记为 `machine_extracted_pending_human_review`，不会自动进入投资逻辑。详见 [M3 财报提取设计](docs/M3_FINANCIAL_FACTS.md)。

## HTTP API

需要先配置 ENGINE_API_TOKEN。所有接口均受 Bearer Token 保护（/healthz 除外）。

- GET /v1/strategies：动态读取 OTW 策略列表。
- POST /v1/analysis/preview：现有只读分析预览。
- GET /v1/industry/observations：读取本地行业观察记录及 point-in-time 资格状态。
- GET /v1/fundamentals/disclosures：读取 CNINFO 公告元数据。
- GET /v1/fundamentals/facts：读取季度报告结构化事实（机器提取、待人工复核）。
- GET /v1/market-data：列出本地数据序列。
- POST /v1/market-data/sync：获取并缓存指定 symbol 的日线数据。
- GET /v1/market-data/{symbol}：读取本地缓存。
- POST /v1/backtests/run：运行 SMA/买入持有回测。
- POST /v1/research/preview：读取 OTW 假设文档并生成研究快照。
- GET /v1/reports：列出已保存的研究快照和回测报告。
- GET /v1/reports/{report_id}：读取指定报告的完整内容。

示例：

~~~bash
curl -X POST http://127.0.0.1:8018/v1/market-data/sync \
  -H "Authorization: Bearer $ENGINE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"601118","asset_type":"stock","start_date":"20180101","adjustment":"qfq"}'
~~~

## Docker 部署

复制 .env.example 为服务器私有的 .env，配置 OTW MCP 和引擎访问凭证。服务器使用 Docker Compose 时，./data 会持久化市场数据和研究/回测报告。容器以 nobody 身份运行，部署前需让宿主机目录可写：

~~~bash
mkdir -p data
sudo chown 65534:65534 data
chmod 750 data
~~~

国内服务器可在 .env 设置：

~~~dotenv
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
~~~

默认值是官方 https://pypi.org/simple。镜像地址只影响 Docker build，不需要写入代码。

~~~bash
docker compose config --quiet
docker compose up -d --build engine
docker compose ps
curl --fail http://127.0.0.1:8018/healthz
~~~

## 明确边界

- 只读 OTW：MCP 客户端只调用 otw_read。没有 otw_write、订单提交或策略文档更新。
- 不创建 OTW runs/orders，不调整原有冻结状态，不创建定时任务。
- AKShare 数据为公开数据源抓取结果，需保留 source/fetched_at/adjustment；接口中断或数据异常必须显式失败。
- 当前仅支持 A 股、ETF 日线和国内期货日线；开放式公募基金单位净值、财报分业务拆分、库存多源交叉核对仍是后续工作。
- 研究证据需要人工确认；当前没有 LLM Agent 自动抓取和自动判断事实。
- 所有回测和研究输出均 eligible_for_trading=false。不能把回测结果当作真实成交能力或投资建议。
