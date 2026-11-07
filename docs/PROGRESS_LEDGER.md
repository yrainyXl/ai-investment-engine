# AI Investment Engine 施工进度台账

> 状态更新日：2026-11-07（北京时间）
> 维护责任：ChatGPT
> 施工总纲：[CONSTRUCTION_PLAYBOOK.md](CONSTRUCTION_PLAYBOOK.md)
> 原则：**代码提交 ≠ 服务器部署 ≠ 数据源联调 ≠ 交易准入**；每项分别记录，不能以聊天总结代替验收证据。

## 一、当前总进度

| 里程碑 | 状态 | 已确认交付 | 剩余关键工作 |
|---|---|---|---|
| M0 施工治理与 DSH 调度通道 | partial | 施工总纲、任务台账、DSH 调度/回传目标契约入库 | 当前聊天 DSH 实际工具发现、只读探针、任务回读 |
| M1 v0.1 基础只读研究链 | done（按已收到的服务器验收报告） | DSH 报告 OTW MCP 只读、策略发现、健康检查、20 测试通过、无订单写入 | 不改变既有只读安全边界 |
| M2 v0.2 行情与基础回测 | partial | GitHub PR #1 已合并；主线有 AKShare provider、缓存、回测、研究快照；PR CI 通过 | 服务器 v0.2 的实际部署 SHA、真实 601118/RU0 同步与报告未收到 |
| M3 基本面/行业证据 | planned | 已定义证据模型与四项假设门槛 | 真实库存、供给、现货、财报、预期数据源 |
| M4 多策略与实验 | planned | 简单 SMA/持有基线 | 策略注册表、样本外回测、模拟组合隔离 |
| M5 多 Agent 研究 | planned | 角色架构明确 | 实际模型适配、研究员/反方/风险 Agent |
| M6 纸面成交闭环 | blocked（安全门槛） | OTW 已有模拟账本与读接口 | 旧审计缺口、服务端冻结、明确用户解冻批准 |
| M7 复盘与绩效评价 | planned | 已有报告保存 API | 绩效归因、策略对比、每月版本评估 |

## 二、任务清单（只有满足验收证据才能标 done）

| Task ID | Owner | 优先级 | 状态 | 依赖 | 完成证据 / 阻断原因 | 下一动作 |
|---|---|---|---|---|---|---|
| M0-DOC-001 | ChatGPT | P0 | done | 无 | [施工总纲](CONSTRUCTION_PLAYBOOK.md) + 本台账已提交 main | 后续任务变化时维护 |
| M0-DSH-001 | ChatGPT / DSH | P0 | blocked | 当前聊天可用 DSH 工具 | 当前工具目录未暴露 DSH 派发/查询工具；尚无只读调用成功记录 | 取得当前会话可调用的 DSH 工具名称/插件入口后仅做只读 capability/status 探测 |
| M0-DSH-002 | ChatGPT / DSH | P0 | planned | M0-DSH-001 | 无真实 DSH task_id、状态流转或回传记录 | 创建一次只读诊断任务，按 task_id 读取终态，记录脱敏结果 |
| M1-OTW-001 | ChatGPT + DSH | P0 | done（按既有部署报告） | OTW paper-trading:r token | DSH 报告 v0.1 健康、策略发现、OTW context/document 时间匹配、无 runs/orders 写入 | 保持权限不变 |
| M2-CODE-001 | ChatGPT | P0 | done（代码） | M1-OTW-001 | [PR #1](https://github.com/yrainyXl/ai-investment-engine/pull/1) 已 merged，merge SHA `bbff880d2b051ee03a0df35678c9027895be1eec`，PR CI 成功 | 用确定 SHA 给 DSH 部署 |
| M2-DEPLOY-001 | DSH | P0 | blocked | M0-DSH-001/002，用户对目标版本的批准 | **尚未通过当前通道派发**；未取得服务器 v0.2 运行 SHA | 审批后按 `docs/HARNESS_DEPLOY_TASK.md` 部署，并回传 job_id |
| M2-MARKET-001 | DSH（执行）/ ChatGPT（评估） | P0 | planned | M2-DEPLOY-001 | 无可核实的 601118 与 RU0 真实行数据报告 | 同步行情，报告来源、数据日期、条数、异常/缺失 |
| M2-BACKTEST-001 | DSH（运行）/ ChatGPT（分析） | P0 | planned | M2-MARKET-001 | 无服务器端 SMA 与 buy-hold 真实历史回测文件 | 产出两组回测、注明参数、复权、手续费和样本范围 |
| M3-PROVIDER-001 | ChatGPT | P1 | planned | M2 数据口径验收 | 无行业/财报独立 source provider | 实现公告、财报、天然橡胶现货与库存的可追溯读取 |
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
- DSH capability 实际调用：`not_attempted`（本会话没有可用工具）。
- DSH `task_id`：`unknown`；`status`：`not_dispatched`；`result`：`not_received`。
- 服务端是否已有自定义调度网关、MCP 接口或审批回调：`unknown`；不能从公网 Harness 网页可访问推断其支持外部任务提交。

**结论：目前已完成文档及本会话工具发现，尚未建立可证明的当前聊天 → DSH → 当前聊天任务闭环。** 不允许把 GitHub Issue 的创建或聊天提示词当成实际派发。

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

- **2026-11-07**：施工总纲和进度台账创建；确认 v0.2 PR #1 已合并，区分“代码完成”和“服务器未确认”；当前聊天 DSH 连接工具未暴露，M0-DSH-001 记 blocked；未派发部署任务，未修改 OTW 运行状态。
