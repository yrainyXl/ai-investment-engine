# AI Investment Engine 施工进度台账

> 状态更新日：2026-11-07（北京时间）
> 维护责任：ChatGPT
> 施工总纲：[CONSTRUCTION_PLAYBOOK.md](CONSTRUCTION_PLAYBOOK.md)
> 原则：**代码提交 ≠ 服务器部署 ≠ 数据源联调 ≠ 交易准入**；每项分别记录，不能以聊天总结代替验收证据。

## 一、当前总进度

| 里程碑 | 状态 | 已确认交付 | 剩余关键工作 |
|---|---|---|---|
| M0 施工治理与 DSH 调度通道 | done（基础任务闭环） | 已从当前 ChatGPT 调用 DSH MCP 提交并读取终态；施工总纲和任务台账已入库 | 后续按具体任务复用 Web Session / request_id |
| M1 v0.1 基础只读研究链 | done（按已收到的服务器验收报告） | DSH 报告 OTW MCP 只读、策略发现、健康检查、20 测试通过、无订单写入 | 不改变既有只读安全边界 |
| M2 v0.2 行情与基础回测 | done（研究基线） | DSH 已部署 SHA f5cc01de，601118 2031 条、RU0 2143 条、1 条异常隔离；SMA/持有回测与 RU0 研究快照已有真实结果，容器 healthy | 基本面四因子仍缺证据，进入 M3 |
| M3 基本面/行业证据 | planned | 已定义证据模型与四项假设门槛 | 真实库存、供给、现货、财报、预期数据源 |
| M4 多策略与实验 | planned | 简单 SMA/持有基线 | 策略注册表、样本外回测、模拟组合隔离 |
| M5 多 Agent 研究 | planned | 角色架构明确 | 实际模型适配、研究员/反方/风险 Agent |
| M6 纸面成交闭环 | blocked（安全门槛） | OTW 已有模拟账本与读接口 | 旧审计缺口、服务端冻结、明确用户解冻批准 |
| M7 复盘与绩效评价 | planned | 已有报告保存 API | 绩效归因、策略对比、每月版本评估 |

## 二、任务清单（只有满足验收证据才能标 done）

| Task ID | Owner | 优先级 | 状态 | 依赖 | 完成证据 / 阻断原因 | 下一动作 |
|---|---|---|---|---|---|---|
| M0-DOC-001 | ChatGPT | P0 | done | 无 | [施工总纲](CONSTRUCTION_PLAYBOOK.md) + 本台账已提交 main | 后续任务变化时维护 |
| [M0-DSH-001](https://github.com/yrainyXl/ai-investment-engine/issues/2) | ChatGPT / DSH | P0 | done | DSH Web MCP | 已实际列出会话、创建项目会话、send 返回 accepted=true、read 返回最终回复 | 后续复用同一会话及 request_id 模式 |
| M0-DSH-002 | ChatGPT / DSH | P0 | done | M0-DSH-001 | DSH session `session-codex-ai-engine-m0-session-20261107-v1`、request_id `ai-engine-m0-readonly-dispatch-20261107-v1`；`read_dsh_session` 最终收到 M0-DSH-001/002 现场报告和 turn/end | 已验收真实任务派发与结果回读 |
| M1-OTW-001 | ChatGPT + DSH | P0 | done（按既有部署报告） | OTW paper-trading:r token | DSH 报告 v0.1 健康、策略发现、OTW context/document 时间匹配、无 runs/orders 写入 | 保持权限不变 |
| M2-CODE-001 | ChatGPT | P0 | done（代码） | M1-OTW-001 | [PR #1](https://github.com/yrainyXl/ai-investment-engine/pull/1) 已 merged，merge SHA `bbff880d2b051ee03a0df35678c9027895be1eec`，PR CI 成功 | 用确定 SHA 给 DSH 部署 |
| M2-DEPLOY-001 | DSH | P0 | done（已存量部署，非本次重部署） | M2-CODE-001 | DSH 在 2026-11-07 20:08 的现场只读报告：git HEAD=`bbff880d2b051ee03a0df35678c9027895be1eec`，分支 `deploy/v0.2`，镜像 `ai-investment-engine:0.2`，/healthz HTTP 200 version=0.2.0，127.0.0.1:8018，healthy | 无需再次部署；保持运行状态 |
| [M2-MARKET-001](https://github.com/yrainyXl/ai-investment-engine/issues/3) | DSH（执行）/ ChatGPT（评估） | P0 | done（行情接入） | M2-DEPLOY-001 | DSH 实际部署 SHA `f5cc01de4608b18af11e03ca596583743588b2e7`，健康 200；RU0 连续 2144 原始、2143 有效、1 条 2023-08-28 异常隔离，quality.valid=true，研究数据范围 2018-01-02→2026-11-06；601118 2031 条未变；缓存 sidecar 已实物验收 | M2-RESEARCH-001 刷新研究快照并核对绩效摘要 |
| M2-BACKTEST-001 | DSH（运行）/ ChatGPT（分析） | P0 | done（基线回测） | 601118 真实行情缓存 | DSH 返回 SMA：total_return=-67.4917%、最大回撤=-71.9848%、Sharpe=-0.4587；买入持有：total_return=-5.5948%、最大回撤=-44.5378%；区间 2018-01-02~2026-11-05，2031 条 qfq，均 eligible_for_trading=false | 进入样本外、成本口径和交易规则补强（M4） |
| [M3-PROVIDER-001](https://github.com/yrainyXl/ai-investment-engine/issues/5) | ChatGPT / DSH | P1 | partial（v0.3 已部署；库存 Provider 修复中） | M2 数据口径验收 | PR #6 squash merge SHA `15b636084a5da9f6320ccf5eb2017c5b9a02c551`、CI 69 passed；DSH 实际部署 SHA 一致，healthz 0.3.0/HTTP 200；CNINFO 公告 600 条 2022-11-25~2026-10-29，未解析 PDF；AKShare 1.20.35 库存 `futures_inventory_99` 不接受 `exchange` 参数，实际零条库存，未写 OTW | ChatGPT 修复库存接口并由 DSH 小范围复测；核对 CNINFO 分页上限 600 条的完整性 |
| M3-THESIS-001 | ChatGPT | P1 | planned | M3-PROVIDER-001 | 四因素当前仍缺独立证据链 | 记录事实/预期/推断、反证、有效期与失效条件 |
| M4-REGISTRY-001 | ChatGPT | P1 | planned | M3-THESIS-001 | 未建研究算法与 OTW 账户分离注册表 | 多策略/多组合版本映射，默认 disabled |
| M4-EXPERIMENT-001 | ChatGPT | P1 | planned | M2-BACKTEST-001、M4-REGISTRY-001 | 回测仍是基础技术指标基线 | 样本外、区间对照、成本敏感性、基准与结果复现 |
| M5-AGENTS-001 | ChatGPT | P2 | planned | M3/M4 的数据与实验验收 | 多 Agent 仍停留在架构 | 开发可替换 Agent 接口，结构化研究/反方/风险输出 |
| M6-PAPER-001 | ChatGPT / DSH / 用户 | 安全门槛 | blocked | 旧审计缺口、服务端冻结、明确解冻批准 | 尚不具备安全写入前提 | 暂时不开发/不启用 order write |
| M7-REVIEW-001 | ChatGPT | P2 | planned | M4/M5 和经授权的 M6 | 无多策略真实前向样本 | 长期绩效、超额收益、错因归因 |

## 三、DSH 当前连接探查记录

探查日期：2026-11-07（北京时间），由 ChatGPT 当前对话执行。

- 已发现且能用：GitHub 工具；OTW 工具在现有连接列表中。
- 在当前会话可用的工具目录中按 `dsh / harness / deepseek / dispatch` 检索：**没有发现 DSH 任务提交、任务查询、结果读取函数**。
- 检索当前用户自有的个人插件列表：仅看到 `otw-mcp`，没有看到 DSH 插件。此结论**不代表**用户其他会话、Work 或 Codex 环境没有连接 DSH。
- DSH 能力实测（20:08）：`list_dsh_sessions`、`create_dsh_session`、`send_dsh_message`、`read_dsh_session` 已实际调用；Web session `session-codex-ai-engine-m0-session-20261107-v1`，只读任务 request_id=`ai-engine-m0-readonly-dispatch-20261107-v1`，`accepted=true`、`running` 变为 `false`、有最终 assistant 报告和 turn/end。
- 初次只读诊断结果：v0.2 已部署，`bbff880d`，容器 healthy；601118 qfq 2031 条日线、RU0 独立缓存尚无，已有 SMA 与持有回测报告。
- 服务端是否已有自定义调度网关、MCP 接口或审批回调：`unknown`；不能从公网 Harness 网页可访问推断其支持外部任务提交。

**更新（2026-11-07 20:08 北京时间）：已经实际验证 ChatGPT → DSH Web Session 的建立与消息派发，`accepted=true`；并通过 `read_dsh_session` 确认 `running=true`。当前会话依然尚未取得 DSH 的最终诊断结果，不能标记部署或只读诊断完成。** [Issue #2](https://github.com/yrainyXl/ai-investment-engine/issues/2) 跟踪回读验收。

## 四、下一阶段派发队列

队列顺序固定；任务只有在前置条件满足后才升级状态。

| 顺序 | Task ID | 派发目标 | 安全范围 | 前置条件 |
|---|---|---|---|---|
| 1 | M0-DSH-001 | DSH 只读 capability/health 查询 | 不修改文件、不部署、不执行 Shell | 当前对话实际可调用 DSH 工具 |
| 2 | M0-DSH-002 | DSH 只读诊断与回读测试 | 仅 health/version/status | 成功获得 capability 与 task_id |
| 3 | M2-DEPLOY-001 | v0.2 固定 SHA 部署 | 允许 build/deploy/health，禁止 OTW 写入 | 任务通道与用户部署批准 |
| 4 | M2-MARKET-001 | 601118 + RU0 真实行情同步 | 仅引擎 `/data` 本地缓存 | v0.2 healthy |
| 5 | M2-BACKTEST-001 | 本地回测与研究报告 | 研究输出，不创建模拟订单 | 真实日线和数据质量通过 |

## 五、进度回填格式（每次 DSH 返回后更新）

```text
任务 ID：
更新日期/时间（Asia/Shanghai）：
Github PR / merge / executed SHA：
DSH job/task ID：
派发接口和派发确认：
DSH 实际状态：
真实结果和验收证据（链接/脱敏摘要）：
结果数据日期 / 来源：
验收结论：done / partial / blocked / failed
错误归类：代码 / 容器 / 网络 / 鉴权 / 外部数据 / 业务规则
对后继任务的影响：
下一负责人和动作：
```

回填规则：只有工具真正返回 `task_id` 才记录 `dispatched`；只有工具或有可信来源的 DSH 执行报告给出执行结果才记录 `done`。如果需要重试，记录重试号、原 task_id 和新的 job_id，不能覆盖历史失败记录。

## 六、变更日志

- **2026-11-07**：施工总纲和进度台账创建；确认 v0.2 PR #1 已合并，区分“代码完成”和“服务器未确认”；当前聊天 DSH 连接工具未暴露，M0-DSH-001 记 blocked 并登记 GitHub Issue #2；未派发 DSH 任务或部署任务，未修改 OTW 运行状态。


## 七、2026-11-07 DSH MCP 接入实测更新

- 连接：DSH MCP 插件在当前 ChatGPT 对话内已暴露 `list_dsh_sessions`、`create_dsh_session`、`send_dsh_message`、`read_dsh_session` 等工具。
- 账户：使用已连接的 DSH 账户；既有 Web Session 已成功列出。
- 新建项目会话：`session-codex-ai-engine-m0-session-20261107-v1`，工作目录 `/opt/apps/ai-investment-engine`。
- 消息幂等键：`ai-engine-m0-readonly-dispatch-20261107-v1`；工具实际返回 `accepted=true`，且 read API 返回 `running=true`。
- 范围：仅只读查看版本、git SHA、容器健康、行情与回测文件元数据；**未授权本轮部署**。
- 当前 `DSH final result`：尚未收到；部署进度保持原状态，等待结果再更新。


## 八、2026-11-07 20:08 DSH 首轮真实回传与任务调整

- **回传途径**：`mcp__dsh__read_dsh_session`，session=`session-codex-ai-engine-m0-session-20261107-v1`，实际观察到 `running=false`、`turn_ends=1` 和 DSH 最终报告。
- **服务器确认**：目录 `/opt/apps/ai-investment-engine`；部署分支 `deploy/v0.2`；实际 SHA `bbff880d2b051ee03a0df35678c9027895be1eec`，与 PR #1 merge 一致；容器 `ai-investment-engine:0.2`、healthy，`/healthz` 200，`127.0.0.1:8018` 仅回环。
- **市场数据**：601118 qfq 真实缓存 2031 条，截止 2026-11-05，AKShare Eastmoney；RU0 只有研究报告符号引用，无独立行情缓存。
- **报告**：已存在本地 SMA、buy-and-hold 和研究快照；本轮未读取详细绩效指标。
- **执行安全**：DSH 明确本轮只读，未修改配置、代码、服务、OTW 或权限；未打印 .env/token。
- **进度重排**：M2-DEPLOY-001 从 blocked 改 done（服务器此前已部署，避免重复），M2-MARKET-001 改 running，M2-BACKTEST-001 改 partial。下一行动：DSH 已实际接受 `ai-engine-m2-ru0-sync-20261107-v1`，仅允许向引擎 /data 同步公开 RU0 数据，不允许重部署或 OTW 写操作。


## 九、2026-11-07 RU0 异常排查、修复与再派发

- DSH 初次 RU0 同步 request_id=`ai-engine-m2-ru0-sync-20261107-v1`，真实执行失败，`MarketDataError: row 1372 inconsistent OHLC relationship`，未存盘，其他数据未改。
- DSH 按 `ai-engine-m2-ru0-inspect-20261107-v1` 只读检查 `futures_zh_daily_sina("RU0")`：2018 起筛选 2144 条，**仅 2023-08-28 一条错误**，`O=13270 H=13425 L=13225 C=13200`，原始 `settle=0`，并非同字段。其余 2143 条原始 OHLC 关系正常。
- ChatGPT 建立 [Issue #3](https://github.com/yrainyXl/ai-investment-engine/issues/3) 和 [PR #4](https://github.com/yrainyXl/ai-investment-engine/pull/4)；采用精确匹配例外隔离异常、原始记录独立持久化，未改通用严格质量门；GitHub CI 55 passed；Squash merge SHA=`f5cc01de4608b18af11e03ca596583743588b2e7`。
- DSH 已接受只升级 Investment Engine 并重新获取 RU0 的受控任务 request_id=`ai-engine-m2-ru0-deploy-f5cc01de-20261107-v1`，当前状态**仅 accepted，尚未收到最终执行结果**。
- 本轮不得视 RU0 数据完成；待真实 `running=false` / DSH 报告后再验收。详细执行标准 [RU0_QUARANTINE_PATCH.md](RU0_QUARANTINE_PATCH.md)。


## 十、2026-11-07 RU0 补丁部署及行情完整验收

- DSH Web session：`session-codex-ai-engine-m0-session-20261107-v1`；部署 request_id：`ai-engine-m2-ru0-deploy-f5cc01de-20261107-v1`，DSH 返回最终成功报告且 `running=false`。
- **运行提交**：`f5cc01de4608b18af11e03ca596583743588b2e7`，分支 `deploy/v0.2` ff-only，工作树干净；仅更新本项目 Engine 容器；`/healthz` 200，healthy，`127.0.0.1:8018`。
- **真实期货数据**：`future_RU0_continuous.csv`，来自 `akshare.sina.futures_zh_daily_sina`；原始 2144 行、有效 2143 行，2018-01-02 至 2026-11-06；2023-08-28 单条原始源异常已隔离到 `future_RU0_continuous.quality.json`，`excluded_rows=1`，未造价、未禁用通用严格质量校验。质量 `valid=true`（针对**入库有效子集**），37 条警告含 1 条隔离和 36 条交易日历差距提示；**不可当作完全无缺口序列**。
- **原有股票数据**：601118 前复权缓存仍 2031 条、截至 2026-11-05，大小和修改时间均保持，原有回测和研究文件仍在。DSH 未修改 Token、.env、OTW 数据或其它服务。
- **修复记录**：PR #4 已合并，合并提交 `f5cc01de`，Issue #3 已关闭；DSH 连接验证 Issue #2 也已关闭。
- **继续任务**：已通过 DSH MCP 派发 `ai-engine-m2-research-and-metrics-20261107-v1`，仅更新引擎本地研究快照并读取已存在的两份回测摘要；该任务当前仅 accepted，不可提前标记 done。


## 十一、2026-11-07 真实研究快照与回测结果回填

- DSH 会话：`session-codex-ai-engine-m0-session-20261107-v1`，请求 `ai-engine-m2-research-and-metrics-20261107-v1`，已收到实际完成报告；只更新 Engine `/data/reports/hainan-rubber-research.json`，未写 OTW。
- **RU0 研究快照**：`commodity_reference.status=available`，`bar_count=2143`，`last_bar=2026-11-06`，来源 AKShare Sina；`excluded_count=1`；`thesis.updated_at=2026-11-03T13:50:31.593693Z`。
- **基本面证据**：`hypothesis_assessment.overall=insufficient_evidence`，四项缺口是 `actual_supply_change`、`multiweek_inventory_trend`、`profit_pass_through`、`priced_market_expectations`。这确认下一步必须做行业、财报和预期的独立真实数据源，而不是继续堆叠技术指标。
- **SMA 20/60 基线（2018-01-02~2026-11-05，601118 前复权，2031 行）**：累计收益 `-67.4917%`，年化收益 `-13.0199%`，Sharpe `-0.4587`，最大回撤 `-71.9848%`，持仓占比 `25.57%`，换手单位 `132.0`。
- **买入并持有对照（同区间、同源）**：累计收益 `-5.5948%`，年化收益 `-0.7122%`，Sharpe `0.1807`，最大回撤 `-44.5378%`，仓位暴露 `100%`。
- 参数：单边手续费 `10 bps`、滑点 `5 bps` 为 DSH 据施工命令/CLI 默认值确认，**未在此次报告内逐字段从 JSON 直接读取**；研究使用供应商前复权数据、下一交易日开盘价模拟成交，忽略部分中国市场实际限制，不能认为是真实可交易策略或投资建议。两份回测报告仍保持 `eligible_for_trading=false`，原文件未被重写。
- **阶段判定**：M2 v0.2 基础行情/研究/回测可标 done（研究基线）；M3 基本面证据仍缺失，`M3-PROVIDER-001` 转入 ready_to_start。维持纸面订单冻结与 OTW MCP 只读权限。

- **下一研发工单**：[Issue #5：M3-PROVIDER-001](https://github.com/yrainyXl/ai-investment-engine/issues/5)，由 ChatGPT 开发；DSH 不自行修改业务代码或启动新的任务。


## 十二、2026-11-07 M3 Provider v1 开发中

- 关联 [Issue #5](https://github.com/yrainyXl/ai-investment-engine/issues/5)，开发分支 `feat/m3-evidence-providers-v1`，已建立 [PR #6](https://github.com/yrainyXl/ai-investment-engine/pull/6)，GitHub CI 69 passed；尚未合并 / 服务器部署。
- 新增 `Observation` 标准记录：factor、metric/value/unit、series definition、effective/published/retrieved 时间、来源 URL、revision、evidence type、人工审核和单位核验状态。只有时间、来源、单位/口径、人工审核全部通过才具备进入研究证据门的资格；inference 不可作为事实/预期。
- CNINFO Provider 当前只索引 601118 官方公告元数据（标题、公告时间、原文 URL），明确不解析 PDF、不把标题当财务事实。
- 库存 Provider 首先接入 AKShare/99qh 上期所天然橡胶交割仓库库存序列；它不是青岛/全国社会库存，且现接口未提供已验证单位和独立发布时间，故记录为 `source_native_unverified`、`published_at=null`，不能填充多周库存证据门。
- 新增本地存储 `/data/evidence`、认证只读 API 和两个 CLI 同步入口；未增加 OTW 写接口、交易或定时任务。
- 详见 [M3_PROVIDER_DESIGN.md](M3_PROVIDER_DESIGN.md) 与 [M3_DEPLOY_TASK.md](M3_DEPLOY_TASK.md)。下一步：审查并合并 PR #6，然后由 DSH 在现有 Engine 中只读联调，结果必须如实保留 unknown / unavailable。


## 十三、2026-11-07 M3 Provider v1 合并与 DSH 派发

- PR #6 已合并到 main，merge SHA `15b636084a5da9f6320ccf5eb2017c5b9a02c551`；GitHub CI 69 passed, 1 warning。
- 在现有 DSH Web Session `session-codex-ai-engine-m0-session-20261107-v1` 实际提交 v0.3 受控部署和两个数据源联调任务，request_id=`ai-engine-m3-provider-deploy-15b63608-20261107-v1`；DSH 工具返回 accepted=true（不代表已完成）。
- 只允许升级本项目 engine、在引擎 `/data/evidence` 同步公开 CNINFO 公告元数据与 AKShare/99qh 上期所橡胶交割仓库库存。禁止 OTW 读写、订单、调度以及其他服务修改。
- 下一步以 DSH 实际回执为准更新服务状态和真实源可用性；数据单位、发布时间未经核实的观察记录不可成为已确认投资事实。


## 十四、2026-11-07 M3 v0.3 服务器真实验收

- DSH Web Session `session-codex-ai-engine-m0-session-20261107-v1`，request_id=`ai-engine-m3-provider-deploy-15b63608-20261107-v1`，已实际读取终态回复、`running=false` 和 turn/end。
- 部署 SHA `15b636084a5da9f6320ccf5eb2017c5b9a02c551`，image `ai-investment-engine:0.3`，`/healthz` HTTP 200/version 0.3.0，绑定 `127.0.0.1:8018`，安全配置保留。
- CNINFO 公告索引已写入 600 条，首/末 2022-11-25→2026-10-29，来源与链接均 HTTPS，`metadata_only_pdf_not_parsed`。请求自 2020 年起，但目前 `max_pages=20` 可能导致历史不全；不视为完整公告库，更不能视为已抽取财报数字。
- AKShare/99qh 库存 Provider 调用失败，`TypeError: futures_inventory_99() got an unexpected keyword argument 'exchange'`，未生成库存文件、未产生任何库存记录；本项不标 done。ChatGPT 已通过 DSH 下发只读签名检查 `ai-engine-m3-inventory-signature-readonly-20261107-v1`，仅用于定位真实接口参数，不授权擅自修改代码。
- 原有 601118 2031 条、RU0 2143 条及研究/回测文件未改；DSH 未读/写 OTW、订单或调度。


## 十五、2026-11-07 M3 库存 Provider 现场诊断与补丁

- DSH request_id=`ai-engine-m3-inventory-signature-readonly-20261107-v1` 实际只读检查 AKShare 1.20.35，`futures_inventory_99(symbol='豆一')`，**没有 `exchange` 参数**；只读调用 `symbol='天然橡胶'` 成功，返回 1181 行，列 `日期`、`收盘价`、`库存`。
- 上游文档仅称“大宗商品库存数据”，未定义统计范围、计量单位、独立发布日期，故原代码描述“上期所交割仓库库存”缺乏证据。补丁改为 **99qh 天然橡胶库存（统计范围未核实）**；不进入 thesis 因子门，保留 `source_native_unverified`/`published_at=null`/`unit_verified=false`。
- ChatGPT 修复分支 `fix/m3-99qh-signature-and-scope`，待 CI 验证、合并并让 DSH 定点更新。不可宣称 M3 库存已有数据完成。


## 十六、2026-11-07 M3 库存接口修复合并与 DSH 任务派发

- PR #7 修复 AKShare `futures_inventory_99(symbol='天然橡胶')` 真实签名及证据范围错误，CI 已通过；squash merge SHA `73a20c6587c78790d2dd0551dee9182f469f8703`。
- 新序列为 `99qh_natural_rubber_inventory_scope_unverified`；由于原始 API 无可靠单位、独立发布日期、库存统计范围，入库记录不得算作已确认的上期所、青岛或全国社会库存，不能填补基本面因子门。
- ChatGPT 实际通过 DSH MCP 派发只更新 Engine 的小版本及一次库存同步，session=`session-codex-ai-engine-m0-session-20261107-v1`，request_id=`ai-engine-m3-inventory-hotfix-73a20c65-20261107-v1`，send 返回 `accepted=true`；**尚未读取终态，任务仍在 DSH 执行队列**。


## 十七、2026-11-07 M3 库存 Hotfix 部署受阻

- DSH 已完成 `ai-engine-m3-inventory-hotfix-73a20c65-20261107-v1` 任务并回传终态：预检通过，当前服务器仍是 PR #6 的 SHA `15b636084a5da9f6320ccf5eb2017c5b9a02c551`、`ai-investment-engine:0.3`、healthz 200/version 0.3.0、仅绑定 127.0.0.1:8018。
- 未部署 PR #7，原因是服务器 `git fetch origin` 对 GitHub HTTPS SSL timeout，curl 12 秒 0 bytes、git ls-remote 25 秒超时；目标 SHA `73a20c6587c78790d2dd0551dee9182f469f8703` 在服务器本地不可达。DSH 没有 merge、没有重建、没有执行 `sync-industry`，也没有修改工作树/容器/数据。
- 因此库存 Provider 代码修复已合并 main 但尚未进入服务器；当前仍无库存观察记录。下一步需先解决服务器到 GitHub 的受控代码同步问题，再继续部署。不得把 PR merge 或任务 accepted 说成服务器已更新。


## 十八、2026-11-07 M3 财报结构化提取开发中

- 新工单 [Issue #8：M3-FINANCIAL-001](https://github.com/yrainyXl/ai-investment-engine/issues/8)，开发分支 `feat/m3-cninfo-financial-facts-v1`。
- 新增 `FinancialFact` 结构：精确十进制字符串、币种/元单位声明、季度与年初至今期间、合并报表口径、公告发布时间/抓取时间、PDF 来源、页码、原始行标签、审计状态及人工复核状态。
- 新增 CNINFO 官方静态 PDF 域名白名单、文件大小/页数限制、pypdf 文本抽取和窄范围季度报表解析器；仅支持机器可读中文季度报告，不启用 OCR。
- 新增本地 `financial_facts.json`、`sync-financials --period 2026-Q3` CLI、认证只读 `GET /v1/fundamentals/facts`、研究快照数据覆盖计数。
- 机器提取结果始终 `reviewed_by_human=false`、`unit_verified=false`、`assessment=unassessed`、`eligible_for_assessment=false`。不会自动更新投资 thesis。
- 目标版本 v0.4.0。CI / PR / 部署尚待完成；部署依赖服务器 GitHub HTTPS fetch 故障先解决。此前 PR #7 库存接口修复仍未进入服务器。
- 详见 [M3_FINANCIAL_FACTS.md](M3_FINANCIAL_FACTS.md)。


## 十九、2026-11-08 通用模型抽象与 M3 财报质量修复

- 按“通用投资研究引擎、海南橡胶仅为首个验收样本”的目标，继续在 PR #9 分支实现，未合并 main、未部署服务器。
- 新增 `investment_engine/contracts.py`：`CompanyFinancials`、`IndustryMetricDefinition`、`IndustryMetricObservation`、`StrategyDefinition`、`StrategyRegistry`；并提供 CNINFO Provider 专属 `FinancialFact` 到通用财务契约的适配函数。策略注册仅为元数据，不代表启用或执行。
- CNINFO 解析器移除海南橡胶公司名称回退值；财报公告与 PDF 同步命令要求显式传入 symbol，避免默认绑定 601118。API 先鉴权，再校验 symbol，保持未授权请求返回 401。
- 修复 PDF 文本压缩时删除数字列分隔空格、导致相邻金额粘连的问题；新增对应回归测试。
- 新增通用契约、跨行业指标注册、策略注册及 CNINFO→通用财务契约映射测试。
- GitHub PR #9 当前 head SHA `73f3cf8b3cf8d43aeec6f02817de522602300f22`，GitHub Actions CI 已对该 SHA 返回 success。PR 仍 open，尚未合并。
- 通用性边界：当前只抽象了契约和注册元数据；CNINFO Provider 仍仅支持机器可读中文季度报告的窄版式，未实现多公司真实报告验收、半年/年度报告、基金净值/持仓 Provider，也未打通策略执行与风控闭环。
- 已向 DSH 派发只读服务器状态核查，request_id=`ai-engine-generic-m3-server-state-readonly-20261108-v1`，accepted=true，等待终态回执；未授权部署或任何服务器修改。
- 当前部署仍以此前 DSH 实际回报为准：服务器停留在 v0.3.0 / PR #6 SHA，PR #7 库存修复仍因 GitHub fetch 网络问题未部署。不得将 GitHub CI 成功等同于服务器已更新。
