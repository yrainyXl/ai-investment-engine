# V07 集成验收报告（Agent Runtime + Strategy Experiments）

- 任务：`AIENGINE-V07-INTEGRATION-20261112`
- 集成分支：`feat/v07-agents-experiments-integration`
- 基线：`origin/main = e7762f2e494fabad3491d90c455d5ef8fbc1a774`（v0.6）
- 生产 `deploy/v0.2` 工作树未改动，仍为 `e7762f2`。
- 仅 `.worktrees/` 内新增独立集成 worktree，未合并 `main`、未部署、未重启服务。

## 1. 集成来源（固定 SHA，可复核）

| 来源 | 分支 | SHA | 文件 |
| --- | --- | --- | --- |
| Worker A（M5） | `feat/m5-agent-runtime-20261112` | `11024d181fce78be57865b759d580d187fc2d166` | `investment_engine/agent_runtime.py`、`tests/test_agent_runtime.py`、`docs/NEXT_AGENT_RUNTIME.md` |
| Worker B（M4） | `feat/m4-strategy-experiments` | `9351a7e934d9d7566c231926b7eef05768e97e38` | `investment_engine/strategy_experiments.py`、`tests/test_strategy_experiments.py`、`docs/NEXT_STRATEGY_EXPERIMENTS.md` |

两个 commit 的父提交均为 `e7762f2`，按固定 SHA 以 `git cherry-pick -x` 依次应用，
无冲突；6 个文件与各自源 worktree 逐字节一致（SHA-256 比对），集成后
`git status` clean。

## 2. 接口一致性核对

- Worker A 模块只依赖标准库，无项目内导入、无网络/OTW/订单/时钟决策依赖；
  模型客户端由调用方注入（`complete(request)` 或可调用对象）。
- Worker B 复用 v0.6 既有接口，签名已逐一核对：
  - `investment_engine.backtest.run_backtest(bars, *, strategy, fast_period, slow_period, fee_bps, slippage_bps, as_of)`；
  - `investment_engine.contracts.StrategyDefinition` / `StrategyRegistry`；
  - `investment_engine.market_data.ADJUSTMENTS`、`ASSET_TYPES`、`Bar`、`validate_bars`。
- 未发现两模块对同一全局状态、文件或环境变量的竞争性写入；两者均为纯函数式/不可变结果输出。
- 未发现 hardcoded 密钥；Worker A 自带密钥样式脱敏正则，用于错误信息。
- 两个模块均不暴露 `place_order` / `submit_order` / `otw_write` / 网络导入等交易面。

## 3. 测试结果（同一 venv，未改系统环境）

复用 Worker B worktree 的 `.venv`（Python 3.12.3，pytest 9.2.0），该 venv 未安装本项目包，
`python -m pytest` 从集成 worktree 根目录导入，已验证 `investment_engine.__file__`
指向集成 worktree。

| 范围 | 数量 | 结果 |
| --- | --- | --- |
| Worker A 单测 `tests/test_agent_runtime.py` | 27 | 27 passed |
| Worker B 单测 `tests/test_strategy_experiments.py` | 31 | 31 passed |
| 新增集成 `tests/test_v07_integration.py` | 6 | 6 passed |
| 组合全量 `pytest` | 265 | 265 passed, 1 warning |

警告为第三方 `starlette.testclient` 的 `httpx2` 弃用提示，与本次改动无关。

新增集成测试仅覆盖集成接缝，不重复两个 worker 的单测：

1. 两个模块从同一 v0.6 树导入；
2. 两个模块均不暴露订单/OTW 写接口；
3. 同一 510300 ETF 合成数据分别跑通实验与 Agent 运行，均输出固定
   `action="no_trade"`、`eligible_for_trading=False`、`orders_enabled=False`；
4. 遍历 Agent 输出，禁止出现交易/仓位类字段（`buy`/`sell`/`order`/`quantity`/
   `position_size` 等），实验报告同样不含此类字段；
5. 无模型客户端时 Agent fail-closed，仍保持 no_trade，模型调用数为 0；
6. 实验 `as_of` 超前（look-ahead）时抛错，不静默截断。

## 4. 数据来源与边界

- 全部测试数据为**合成 fixture**，明确标注 `test.v07.integration.fixture.v1`；
  覆盖 510300 ETF（Agent 侧为 NAV observation，实验侧为日线 OHLCV）与既有
  600519 / 601118 单测覆盖。未读取生产 `data/`、未调用 OTW MCP、未访问外网。
- 模型侧全部为本地脚本化 **Stub**（`ModelResponse`），无付费模型调用、无 API Key。
- Worker A 结果固定字段：`action="no_trade"`、`eligible_for_trading=False`、
  `orders_enabled=False`、`human_review_required=True`、`auto_approval=False`。
- Worker B 结果固定字段：`action="no_trade"`、`eligible_for_trading=False`、
  `orders_enabled=False`、`experimental_only=True`、`optimization_performed=False`、
  `tradability="not_tradable"`；策略 `enabled` 恒为 `False`，无激活路径。
- 未将 Agent 接入生产 API、OTW 或调度；未新增任何订单/成交路径。

## 5. 已知缺口（不阻塞本轮集成）

- 交易规则数据缺口由 Worker B 显式列为 `BACKTEST_GAPS`（分红、停牌、涨跌停、
  T+1、ST/退市、市场冲击、幸存者偏差），报告因此恒为 `not_tradable`。
- Agent 输出的研究文本仅为解释，不构成事实、预测或交易决定；引用必须来自
  通过审核/时效/质量闸门的证据，否则该角色 `unavailable`。
- 本轮未做性能优化、权限调整、生产部署或 GitHub CI；CI 真实状态见交付说明。
- Worker B 的 `parameter_schema` 仅做类型/最小值校验，不是完整 JSON Schema 实现。

## 6. 交付与回滚

- 离线交付：`git bundle` + `git format-patch`（见同目录交付路径），可在无网络
  环境复核固定 SHA。
- 回滚：删除集成分支/worktree 即可；生产 `deploy/v0.2` 全程未变。
