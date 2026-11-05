# DeepSeek Harness 执行任务：升级 AI Investment Engine v0.2

目标分支：feat/phase2-market-data-backtesting
部署目录：/opt/apps/ai-investment-engine
当前线上版本：v0.1.1，127.0.0.1:8018

## 任务范围

升级为 v0.2，增加 AKShare 日线行情同步、本地数据缓存、投资假设证据分类、SMA/买入持有历史回测和研究 API。保留 OTW MCP 只读连接。

**硬性边界：不创建 OTW runs/orders/documents，不调用 otw_write/otw_compute，不下真实证券订单，不改变旧模拟成交冻结，不新增定时任务，不扩大 MCP token 权限，不停止原有 ChatGPT 任务。**

## 1. 更新代码

1. 检查工作区干净和当前部署 SHA，保留当前本地 main，不执行 reset --hard。
2. git fetch origin。
3. 确认远端分支 feat/phase2-market-data-backtesting 存在。
4. 为当前部署版本创建本地备份分支，例如 backup/engine-v0.1.1。
5. 切换到远端目标分支的本地跟踪分支。不要删除备份分支。
6. 不要把 .env、data/、本地报告或任何密钥提交 Git。

## 2. 保留并补充环境配置

原有 OTW_BASE_URL、OTW_API_TOKEN、ENGINE_API_TOKEN 保持不变；确认 OTW_TRANSPORT=mcp。

在服务器 .env 增加或确认以下非秘密配置：

~~~dotenv
ENGINE_DATA_DIR=/data
MARKET_DATA_PROVIDER=akshare
MARKET_DATA_DEFAULT_START_DATE=20180101
MARKET_DATA_DEFAULT_ADJUSTMENT=qfq
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
~~~

PIP_INDEX_URL 只作为 Docker build 参数。默认值仍为官方 PyPI；不要把清华源硬编码到 Dockerfile。

## 3. 创建持久化数据目录

容器以 nobody（UID 65534）运行，宿主机数据目录需要可写：

~~~bash
cd /opt/apps/ai-investment-engine
mkdir -p data
sudo chown 65534:65534 data
chmod 750 data
~~~

确认 data/ 已被 .gitignore 忽略。

## 4. 构建与启动

~~~bash
docker compose config --quiet
docker compose up -d --build engine
docker compose ps
curl --fail http://127.0.0.1:8018/healthz
~~~

镜像应为 ai-investment-engine:0.2。端口仍然只绑定 127.0.0.1:8018。不得将服务暴露公网。

如果 AKShare 安装或上游接口请求失败，记录错误并停止数据同步，不要生成假数据。

## 5. 首次行情同步

仅获取公开日线并写入引擎自己的 ./data 目录，不写入 OTW：

~~~bash
docker compose exec -T engine python -m investment_engine.market_cli sync \
  --asset-type stock --symbol 601118 \
  --start-date 20180101 --end-date 20261105 --adjustment qfq

docker compose exec -T engine python -m investment_engine.market_cli sync \
  --asset-type future --symbol RU0 \
  --start-date 20180101 --end-date 20261105 --adjustment continuous

docker compose exec -T engine python -m investment_engine.market_cli list
~~~

如果 RU0 上游接口不可用，保留股票行情结果并报告期货数据失败；不要改用未经核实的其他序列冒充 RU0。

## 6. 回测与研究快照

~~~bash
docker compose exec -T engine python -m investment_engine.market_cli backtest \
  --symbol 601118 --asset-type stock --adjustment qfq \
  --strategy sma_trend --fast-period 20 --slow-period 60 \
  --fee-bps 10 --slippage-bps 5 --out /data/reports/601118-sma.json

docker compose exec -T engine python -m investment_engine.market_cli backtest \
  --symbol 601118 --asset-type stock --adjustment qfq \
  --strategy buy_and_hold --out /data/reports/601118-buy-hold.json

docker compose exec -T engine python -m investment_engine.market_cli research \
  --futures-symbol RU0 --out /data/reports/hainan-rubber-research.json
~~~

回测输出只写本地文件，不代表交易建议。研究快照应显示 OHLCV 观察、RU0 参考序列（若同步成功）、四项基本面假设的证据缺口。

## 7. API 能力

所有接口（/healthz 除外）使用 ENGINE_API_TOKEN：

- GET /v1/market-data
- POST /v1/market-data/sync
- GET /v1/market-data/{symbol}
- POST /v1/backtests/run
- POST /v1/research/preview
- 原有 GET /v1/strategies、POST /v1/analysis/preview 保持。

## 8. 完成报告

只需简要报告：
- 部署分支和 Git SHA；
- 容器健康状态；
- AKShare 版本；
- 601118 和 RU0 的数据源、条数、起止日期、复权口径及质量警告；
- 两份回测结果文件是否成功生成并保存在 /data/reports；
- 研究快照是否生成；
- 确认 OTW runs/orders/documents 未变化、没有新增任务、MCP token 权限未变化。

不要打印任何 token 或 .env 内容。

## 回滚

如 v0.2 启动失败，停止本项目容器即可。保留 data/ 和旧备份分支；切回备份分支重建旧镜像。不要使用 docker compose down -v，不触碰 OTW/DeepSeek Harness 或其他服务。
