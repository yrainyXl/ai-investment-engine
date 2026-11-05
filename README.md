# AI Investment Engine v0.1

OTW 外置研究与投资决策引擎：**read-only preview**，不改变 OTW 现有账本，不进行实际证券交易，也不提交模拟订单。

> 基线：2026-11-05 经 OTW MCP 只读核对。已确认 `paper-trading` 的 `context`、`runs`、`orders`、`documents` 端点；已确认海南橡胶模拟策略存在。当前 `histdata/datasets` 和 `backtest/strategies` 均为空。该策略历史分析中有 **模拟成交冻结** 标记，恢复条件尚未完成。本项目默认全部禁止下单，保持现有任务原样运行。

## 交付内容

- `investment_engine/otw_client.py`：OTW HTTP **只读**接口（现有路径，不虚构新的服务器 API）。
- `investment_engine/analysis.py`：四项周期投资假设证据状态、60 日价格均线诊断、风控否决。
- `investment_engine/pipeline.py`：串联 OTW 上下文、全局策略文档、投资假设、量化诊断、交易意向、风控、决策与 OTW `/runs` payload 预览。
- `investment_engine/cli.py`：本地命令行，输出完整 JSON 决策快照。
- `investment_engine/api.py`：可选 FastAPI 本机 `/healthz`、`/v1/analysis/preview`，**无写接口**。
- `docs/IMPLEMENTATION_PLAN.md`：分期实施计划和责任分工。
- `docs/OTW_INTEGRATION.md`：经实际查询确认的接口、限制和安全边界。
- `docs/ACCEPTANCE.md`：验收标准与暂不开放交易的条件。
- `fixtures/`：明确标注的合成示例数据，绝不视为真实行情。

## 一键验证（无需 OTW Token）

在项目目录运行：

```bash
python -m investment_engine.cli --source fixture --out output/latest-preview.json
python -m pytest -q
```

不希望安装依赖也能直接运行 CLI。只有测试需要 `pytest`。本地模拟输出不会自动上传 OTW。

## 只读连接现有 OTW

先在本地环境变量中配置（不要提交到 Git）：

```bash
export OTW_BASE_URL='https://<你的OTW域名>'
export OTW_API_TOKEN='<服务器端可用的只读Token>'
python -m investment_engine.cli --source otw --list-strategies
# 当前只有一个 active 策略时可自动选择：
python -m investment_engine.cli --source otw --out output/live-preview.json
# 多个 active 策略时，请显式选择：
python -m investment_engine.cli --source otw --strategy-id '<从列表获取的UUID>' --out output/one-preview.json
# 或者以只读方式批量预览全部 active 策略：
python -m investment_engine.cli --source otw --all-active --out output/all-active.json
```

注意：已在 ChatGPT MCP 连接里能调用 OTW，**并不能证明本地这个 HTTP Token 已配置或有相同权限**；本地 HTTP 鉴权需由你在服务器上核实。不应把 ChatGPT 内部凭证复制进仓库。失败只会报错，不会回退到记忆或模拟数据伪装成功。

策略 ID 不再属于服务端固定配置：引擎通过 OTW `GET /api/paper-trading/strategies` 动态发现。HTTP 端点 `GET /v1/strategies` 需要 `ENGINE_API_TOKEN` 鉴权，`POST /v1/analysis/preview` 可以通过请求体传入 `strategy_id`。未提供 ID 时只允许唯一的 active 策略自动选择；多个 active 策略要求明确 ID，避免误用账户。批量预览使用 CLI 的 `--all-active`，不自动下单，也不允许同一份证据/日线文件套用到不同策略。

可选：`--evidence fixtures/evidence.template.json` 提供待审阅的基本面证据；`--bars path/to/daily.csv` 提供本地日线 (`date,close`) 以计算均线。没有历史数据时明确输出 `unavailable`，**不会制造回测收益**。

## 本机 API

```bash
python -m pip install -e '.[api,test]'
uvicorn investment_engine.api:app --host 127.0.0.1 --port 8018
curl -s http://127.0.0.1:8018/healthz
curl -s -X POST http://127.0.0.1:8018/v1/analysis/preview \
  -H 'Content-Type: application/json' -d '{"source":"fixture"}'
```

不要直接暴露到公网；若以后经 OTW 反向代理使用，必须先配置鉴权、调用限速、超时、审计和 IP/网络边界。

## 可执行边界

当前版本 **没有 TradingAgents / LLM 自动研究、AKShare 自动数据抓取、真实因子回测或交易执行**。它提供的是能够在 OTW 实际输入上验证的安全骨架：读真实上下文、标记缺失证据、输出结构化决策、留下可审计快照，以及统一 API。所谓 `buy_candidate` / `sell_candidate` **只是候选意向**，所有候选订单都被否决，最终 `decision=no_trade`；不能解释为已经验证的盈利信号。OTW `/runs` 的内容也仅导出为本地预览，不自动 POST。

更完整的工程分期见 [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)。

## 当前覆盖范围

v0.1 已支持读取和遍历多个 OTW 模拟策略，但单次研究仍只支持单标的。对于一个策略内存在多个资产的情况会拒绝输出，防止拿第一只资产冒充完整组合分析。跨资产、多持仓的全局文档匹配、定时任务迁移、性能比较及真正的 AI/策略回测属于后续阶段。`account_summary.reference_price` 只代表 OTW 参考报价，**不等于已核验的交易所收盘价或可执行成交价**。

服务器部署由 DeepSeek Harness 执行时，优先阅读 [docs/HARNESS_DEPLOY_TASK.md](docs/HARNESS_DEPLOY_TASK.md)；Docker Compose 的最小部署方式见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

**注意：** `compose.yaml` 只绑定服务器的 `127.0.0.1:8018`，不能直接公网访问。`/v1/analysis/preview` 在 OTW 模式必须配置独立的 `ENGINE_API_TOKEN` 并携带 `Authorization: Bearer ...`；没有该 token 只能进行 fixture 演示。仓库不能包含任何实际 token、`.env`、服务器密钥或真实账户导出。

关于将 ChatGPT 当作入口，经过审批再将服务器操作委派给 Harness 的可行架构，见 [docs/HARNESS_INTEGRATION.md](docs/HARNESS_INTEGRATION.md)。`examples/preview.synthetic.json` 是合成样例输出，不是实时投资结论。
