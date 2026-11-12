# M4 多策略注册与样本外实验骨架（Worker B）

> 状态：研发完成，**未部署**。生产保持 v0.5/v0.6 现状只读；本文与对应代码不产生任何 OTW 写入、订单、模型调用或调度。
>
> 代码：`investment_engine/strategy_experiments.py`
> 测试：`tests/test_strategy_experiments.py`
> 基线：GitHub `main` `e7762f2e494fabad3491d90c455d5ef8fbc1a774`（v0.6，PR #15）

## 1. 定位：协调层，不是第二个回测器

本模块只做「注册 + 切分 + 统一结果」，**不复制**已有实现：

| 能力 | 复用来源 |
|---|---|
| 价格模拟、成本、基准、防前视执行（t 收盘信号 → t+1 开盘成交） | `investment_engine.backtest.run_backtest` |
| Bar 质量校验、as_of 截断、来源/复权/频率字段 | `investment_engine.market_data`（`Bar`、`validate_bars`、`MarketDataStore`） |
| 策略元数据契约与校验（`strategy_id`、`version`、`asset_classes`、`parameter_schema`、`enabled`） | `investment_engine.contracts.StrategyDefinition` |

之所以新增 `StrategyExperimentRegistry` 而非直接复用 v0.6 的 `StrategyRegistry`：后者以 `strategy_id` 为主键，无法保存同一策略的多个版本，而 IS/OOS 对比需要版本历史。因此本注册表按 `(strategy_id, strategy_version)` 存储，并**调用** `StrategyDefinition.validate()` 做同源校验；`export_enabled()` 再投影回 v0.6 注册表，保持互通。此取舍是有意为之，不是遗漏。

## 2. 接口契约

### StrategySpec（不可变）

```python
StrategySpec(
    strategy_id="sma_trend_cn_stock",
    strategy_version="1.0.0",
    name="SMA trend baseline (A-share)",
    asset_class="stock",             # stock | etf | future
    parameters={"fast_period": 5, "slow_period": 20},
    parameter_schema={...},          # {"name": {"type","required","minimum","maximum"}}
    dataset_id="stock:601118:qfq:1d",
    risk_policy_id="risk_long_only_v1",
    portfolio_id="portfolio_cn_stock_research",
    implementation="sma_trend",      # sma_trend | buy_and_hold（唯一支持的 run_backtest 类型）
    enabled=False,                   # 默认 false，且本层禁止 True
)
```

- `enabled` 固定 `False`；构造 `enabled=True` 直接 `activation_not_supported`。启用属于独立、需审批的部署动作，不在本层。
- `parameters` 在构造时深拷贝冻结，事后修改调用方字典不影响已注册策略。
- 同一 `portfolio_id` 只能属于一个 `strategy_id`（`portfolio_isolation_violation`），策略之间不共享可变组合/账户状态。
- 未知参数、缺失必需参数、类型/区间不符、`fast>=slow` 一律 fail-closed。

### ExperimentSpec（不可变，成本与区间无默认值）

```python
ExperimentSpec(
    experiment_id="exp_sma_stock_is_oos_v1",
    strategy=spec,
    symbol="601118", asset_class="stock",
    adjustment="qfq", interval="1d",
    is_start=date(2025,1,1),  is_end=date(2025,8,31),
    oos_start=date(2025,9,1), oos_end=date(2025,12,31),
    as_of=<tz-aware datetime>,
    fee_bps=10.0, slippage_bps=5.0,     # 必填，无隐式默认
    benchmark="buy_and_hold",
    parameter_provenance="pre_registered",   # 只接受预注册
    walk_forward=WalkForwardConfig(train_bars=40, test_bars=24) | None,
)
```

### ExperimentReport（统一结果字段）

一次性给出：`is_metrics` / `oos_metrics` / `is_benchmark_metrics` / `oos_benchmark_metrics`，
度量集合固定为
`total_return, cagr, annualized_volatility, sharpe_ratio, max_drawdown, positive_day_ratio, exposure, turnover_units, entry_count, observations, fee_bps_per_side, slippage_bps_per_side`；
外加 `excess_return_is` / `excess_return_oos`（相对买入持有基准）、`costs`、`walk_forward`、`reproducibility_key`。

可追溯字段：`dataset_id`、`dataset_fingerprint`（价格序列确定性哈希）、`source`、`adjustment`、`price_frequency`、`exchange`、`currency`、`fetched_at_earliest/latest`、`bar_count`/`is_bar_count`/`oos_bar_count`、`parameters`、`strategy_version`、`as_of`、`risk_policy_id`、`portfolio_id`。

固定安全字段（`__post_init__` 兜底，无 setter）：

```json
{"action": "no_trade", "eligible_for_trading": false, "orders_enabled": false,
 "experimental_only": true, "optimization_performed": false, "tradability": "not_tradable"}
```

### 函数

| 函数 | 说明 |
|---|---|
| `run_experiment(spec, bars, *, now=None)` | 执行 IS/OOS；契约违规抛 `StrategyExperimentError(code=...)`，样本合法但不足则返回 `status="not_evaluable"` |
| `walk_forward_folds(rows, config, *, min_train_bars)` | 纯函数；`train_bars` 会被抬到策略最小需求，测试窗不得小于最小需求（spec 层已拦） |
| `StrategyExperimentRegistry` | `register / get / versions / list / portfolios / export_enabled` |
| `compare_experiments(reports, *, baseline_strategy_id=None)` | 并排对比，`selection_performed=False`，无排名/无胜者 |
| `dataset_fingerprint` / `expected_dataset_id` | 可复现性工具 |

## 3. 前视与区间规则（fail-closed）

| 情形 | 行为 |
|---|---|
| Bar 日期晚于 `as_of` | `lookahead_future_bars`，**拒绝而非静默截断** |
| IS 与 OOS 重叠或相接 | `is_oos_overlap` |
| `as_of` 无时区 / 在未来 | `invalid_as_of` / `future_as_of` |
| Bar 的 symbol/asset_type/adjustment/interval 与 spec 不符 | `dataset_*_mismatch` |
| `strategy.dataset_id` 与数据实际身份不符 | `dataset_id_mismatch` |
| 缺 source / fetched_at / currency | `missing_provenance` |
| 多来源混用 | `mixed_provenance` |
| 样本不足（IS 或 OOS < `slow_period+2`） | `not_evaluable` + `insufficient_is_bars` / `insufficient_oos_bars` |
| walk-forward 窗口放不下 | `not_evaluable` + `insufficient_walk_forward_folds` |
| walk-forward 几何短于策略最小需求 | `walk_forward_window_too_short` |

`parameter_provenance` 只接受 `pre_registered`；本层**不做**参数搜索、网格、收益最大化、自动优选或策略晋级。

### 复权口径的诚实声明

ETF/股票行情通常只提供「当前复权」序列，历史 bar 可能已含后来的除权除息修订。因此当 `adjustment != "none"` 时：
- `backtest_gaps` 追加 `point_in_time_adjustment_unverified`；
- `provenance_warnings` 明确写明这不是 point-in-time 复权序列。

不伪装成历史可成交价。

## 4. 回测缺口与「不可交易」

以下缺口无数据支撑，固定列举（不是假设消失）：

```
dividend_cashflows_not_modeled
suspension_days_not_modeled
price_limit_up_down_and_queue_not_modeled
t_plus_one_settlement_not_modeled
st_delisting_status_not_modeled
market_impact_and_capacity_not_modeled
survivorship_and_index_constituent_history_not_modeled
```

因此 `tradability="not_tradable"`、`eligible_for_trading=false`、`orders_enabled=false`、`action="no_trade"`。任何「回测收益高」都不构成交易资格。

## 5. 测试覆盖（31 例）

`tests/test_strategy_experiments.py`，`.venv/bin/python -m pytest tests/test_strategy_experiments.py -q` → **31 passed**。

两类**不同行业/资产类型**的 fixture：

- A 股 601118（stock, qfq, SSE）；
- 宽基 ETF 510300（etf, qfq, SSE）。

覆盖场景：

1. 注册表默认 `enabled=false`、与 `contracts.StrategyRegistry` 互通；
2. `enabled=True` 被拒绝；
3. 参数冻结（外部字典改动不影响已注册策略）；
4. 未知/缺失/越界参数 fail-closed；
5. 组合隔离（跨策略共用 `portfolio_id` 被拒）、同策略新版本允许；
6. 两类资产各自完成 IS/OOS + 基准；
7. **未来数据**拒绝且不被截断；
8. **区间交叉**拒绝（含反向区间）；
9. **样本不足** → `not_evaluable`；
10. `dataset_id` / asset_class / adjustment / provenance 不符；
11. 无隐式成本默认值（缺 `fee_bps` 直接 `TypeError`）；
12. 参数搜索声明被拒；
13. **重复实验字节级可复现**（`to_json()` 相同）；
14. **费用变化**降低收益、写入成本字段、改变 `reproducibility_key`；
15. walk-forward 折数/窗口不重叠/聚合口径、几何过短被拒；
16. **策略隔离**：跑完一个实验不改另一个策略的注册；
17. 对比中性（`selection_performed=false`，无 rank/winner）；
18. 报告安全字段不可翻转（`dataclasses.replace` 亦抛错）；
19. 模块无 OTW/下单/HTTP/socket 入口；
20. 回测缺口与非交易标记；
21. `MarketDataStore` CSV 落盘 → 读回 → 实验的端到端往返，fingerprint 稳定。

## 6. 如何运行

```bash
# 在独立 worktree 内
python3 -m venv .venv
.venv/bin/pip install "pytest>=8,<10" "fastapi>=0.110,<1" "httpx>=0.27,<1"
.venv/bin/python -m pytest tests/test_strategy_experiments.py -q   # 31 passed
.venv/bin/python -m pytest -q                                      # 232 passed
```

## 7. 明确的非目标

- 不写 OTW、不建 order/run、不改持仓/策略文档、不发请求。
- 不接投资 API、不调用真实模型、不生成模拟订单、不开调度、不部署。
- 不做参数寻优、不自动批准、不把 `review_ready`/高收益当作交易许可。
- 不修改 v0.6 已有接口（`api.py`、`e2e_pipeline.py` 未触碰）。

## 8. 后续可接的点（尚未实现）

1. 把 `ExperimentReport` 接入只读报告存档（`ReportStore`）以便跨周期比较——需集成方确认字段。
2. 引入真正的交易规则数据（停牌、涨跌停、T+1、分红）后，逐项把 `backtest_gaps` 转为已建模，并重新定义 `tradability`。
3. AI 策略实验：新增 `implementation` 需要先有可复现的决策记录回放，而不是在本层塞入 prompt 调用。
