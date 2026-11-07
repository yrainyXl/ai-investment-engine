# AI Investment Engine 全局施工总纲（执行版）

> 文档版本：1.0 · 建档日：2026-11-07 · 责任主体：ChatGPT（研发与项目管理）、DSH/DeepSeek Harness（服务器执行）、OTW（投资数据与模拟账本）、用户（授权与目标裁决）。
>
> 本文是项目**实施和分工的权威施工文件**；动态任务状态统一见 [PROGRESS_LEDGER.md](PROGRESS_LEDGER.md)。既有 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)、[HARNESS_DEPLOY_TASK.md](HARNESS_DEPLOY_TASK.md) 作为历史细节和具体部署手册。施工任务发生变更时，先更新本文件或进度台账再执行。
>
> 当前变更范围：**仅整理文档与探查连接**；不得因为创建本文而部署、下单、启用定时任务或改变 OTW 权限。

## 1. 项目要建成什么

**AI Investment Engine = 多资产、多策略、多 Agent、证据驱动、可回测可审计的 AI 投资研究和模拟决策系统。**

目标不是“股价预测器”，也不是每天自动生成一篇股票分析。系统要能持续发现投资机会、提出可以证伪的投资假设、对照真实数据检验、量化评估风险收益、提出行动方案，在授权的模拟账本中记录决策和成交，并对结果做归因。

支持两条并存的研究路径：

- **A：预定义策略执行**。人先确定周期价值、基本面价值、趋势、ETF 轮动等策略与风险边界，AI 自主收集证据、作判断、对比回测；策略参数和版本可追溯。
- **B：自主策略发现**。AI 探索行业/股票/ETF，生成机会假设和候选策略；候选先进入研究实验、样本外验证和影子运行，**不自动获得交易资格**。批准后才加入可运行策略注册表。

首个深入样本为海南橡胶 601118 与天然橡胶行业链，但架构不绑定任何单一股票。可扩展 A 股、场内 ETF、开放式基金、行业及商品期货参考数据。真正的交易资产与行情参考指标必须区分。

## 2. 系统边界、职责与数据流

```text
用户：确定投资目标、风险约束、关键审批
       │
       ▼
ChatGPT（统一任务入口与项目经理）
  ├─ 需求、架构、代码、测试、PR、GitHub 进度台账
  ├─ 将服务器执行意图转换为受限任务并请求用户审批
  └─ 通过已验证的 DSH 工具提交 / 查询 / 接收执行结果
       │                                     ▲
       ▼                                     │
GitHub（代码+文档+版本唯一基线）           脱敏结果、job_id、日志摘要、运行 SHA
       │                                     │
       ▼                                     │
DSH / DeepSeek Harness（服务器执行器）──────┘
  └─ 固定目录拉取确定 SHA → 依文档构建 → 测试 → 部署 → 验证 / 回滚
       │
       ▼
AI Investment Engine（独立 Python 服务）
  ├─ Data Gateway：行情、库存、财报、产业链、来源和历史快照
  ├─ Research：事实/市场预期/推断 → 假设与反证 → 置信度
  ├─ Strategy Registry：策略/模型/参数/数据集/组合版本绑定
  ├─ Experiment & Backtest：基准、样本外、成本、敏感性、归因
  ├─ Agent Orchestrator：基本面/行业/量化/反方/决策/风险
  └─ Deterministic Risk Gate：仓位、T+1、流动性、止损、冻结、幂等
       ⇅ 受控 MCP/API
OTW（权威账户、组合、研究文件、runs/orders、模拟成交与绩效账本）
```

**OTW 负责账本和权威状态**：模拟资金、持仓、订单、run、策略文档和收益；不得在 Investment Engine 中再维护一份独立可变的“真实持仓”。

**Engine 负责研究与可复现决策**：行情缓存可以本地持久化，但每个结论必须带 symbol、as_of、来源、抓取时点、数据版本、策略版本、模型/提示词版本和风险结果。

**DSH 不负责业务判断**：它是受控服务器执行器，默认不得擅自修改投资策略或 OTW 数据；如部署中发现需要改代码，返回问题/PR 建议，由 ChatGPT 统一研发与提交。

**ChatGPT 负责协同**：任务拆解、GitHub 开发、发起部署、读取 DSH 返回、更新进度、处理失败原因。普通 ChatGPT + GitHub 插件即可进行大量仓库文档/代码工作；是否可从某一聊天直接控制 DSH，必须以当前会话实际暴露并调用成功的工具为准，不以其他客户端“已连接”的说法自动推断。

**用户负责审批**：部署/变更运行环境、Token 权限、模拟成交解冻、调度切换等应明确批准。ChatGPT 的描述不构成服务器端合法审批凭据。

## 3. 不可突破的业务与安全约束

1. **真实证券交易不在本项目范围**；默认只读 OTW + 本地研究。
2. 目前海南橡胶纸面交易仍受旧实验审计缺口约束（既有研究记录标记 `paper_execution_paused=true`）；**不能仅靠文档标记当作服务端硬冻结**。解决缺口、建立服务端冻结闸门并经用户明确验收前，不开启模拟订单写入。
3. 数据缺失时输出 `unavailable`、`unknown`、`insufficient_evidence`；不得用合成数据冒充真实行情，不用聊天记忆冒充 OTW 实时读取。
4. 每条证据区分**事实、市场当时预期、推断**，记录 `source_url`、`published_at`、`effective_at`、`retrieved_at`、`as_of`；在决策时点未知的信息不得参与历史判断。
5. 多个策略/模拟组合必须相互独立，动态发现 OTW 策略不代表自动启用；使用注册表而非全局 `OTW_STRATEGY_ID`。
6. 策略 Agent 的建议只产生 proposal；最终执行权属于**确定性风控层**，不可由 LLM 指令绕过。
7. 仅在固定版本、独立权限、经审批的任务范围内允许 DSH 操作；不公开无限制 Shell/root，不把 OTW/DSH/ENGINE Token 写入 GitHub 或聊天。
8. 不重复创建调度器：当前既有北京时间 09:00/11:00/14:00/17:00 相关任务，未来迁移要先并行只读对账、然后单一切换。
9. 服务器资源有限：原则上复用远程大模型 API、轻量 Python 服务与外置缓存；不把部署本地大模型当成前置条件。
10. 所有回测仅用于研究。单一历史回测结果和统计指标不能直接推出未来收益。

## 4. 角色 RACI（谁开发，谁操作，谁审批）

| 工作 | ChatGPT | DSH | 用户 | OTW |
|---|---|---|---|---|
| 投资系统架构、数据模型、任务拆解 | **主责** | 咨询可行性 | 审定目标 | 提供接口 |
| 编写业务代码、测试、文档、PR | **主责** | 仅响应服务器差异 | 关键变更批准 | — |
| CI、GitHub 合并/发布版本 | 主责准备/按授权操作 | 拉取确定 SHA | 关键发布审批 | — |
| Docker 构建、服务器配置、部署/回滚 | 准备执行任务、接收结果 | **主责** | 高风险操作批准 | — |
| 公共市场数据抓取/诊断 | 开发和分析 | 运行、反馈环境问题 | — | 可作为已有数据源 |
| OTW 模拟账本与策略文档 | 读取、分析、受控写入设计 | 不直接改业务数据 | 成交解冻审批 | **权威系统** |
| 投资假设、策略实验、多 Agent 协调 | **主责** | 运行调度（获授权后） | 策略启用/风险边界 | 保存审计 |
| 安全密钥 | 不接收/不提交明文 | 服务端保管、轮换 | 授权 | 权限验证 |
| 任务进度、验收、变更记录 | **主责维护** | 回报任务结果 | 重大变更裁决 | 数据对账 |

工作约定：ChatGPT 优先直接在当前聊天通过 GitHub 连接进行文档和小中型代码修改，不把所有开发转成 Codex。只有明确需要完整计算机操作/大型仓库执行环境，或当前客户端不支持所需动作时再使用 Work/Codex。**不承诺任何具体订阅额度的计费差异**；以用户实际产品用量为准。

## 5. 施工分期与必须满足的验收门槛

| 里程碑 | 主要交付 | 负责人 | 阶段准入/验收 |
|---|---|---|---|
| M0 治理与任务通道 | 施工总纲、进度台账、DSH 派发/回读协议、审批规则 | ChatGPT；DSH 能力自检 | 可真实提交一次只读任务、按 task_id 查结果、状态持久化 |
| M1 基础研究服务（v0.1） | OTW MCP 只读、策略动态发现、账户/文档、no_trade 预览 | ChatGPT 研发、DSH 部署 | 真实读成功、权限只读、零 OTW 写入 |
| M2 市场数据与基准（v0.2） | AKShare 日线、CSV 缓存、来源质量、SMA/持有回测、研究快照 | ChatGPT 研发、DSH 部署 | 代码 CI、真实 601118/RU0 数据、数据时间、回测报告；失败不能伪造 |
| M3 行业与基本面证据 | 天然橡胶现货/库存/产量/天气、公司公告财报、预期与估值 | ChatGPT 研发、DSH 只读数据联调 | 每项数据可追溯，单位与发布日期清楚；至少支持/反对证据并存 |
| M4 策略实验系统 | 策略注册表、策略版本、参数、历史/样本外回测、基准与敏感性 | ChatGPT 研发、DSH 运行实验 | 不同策略/组合隔离；实验可复现；区分 IS/OOS 与交易成本 |
| M5 AI 多 Agent 决策 | 研究 Agent、反方 Agent、量化、决策、风险 Agent；按需调用 | ChatGPT 研发、DSH 运行模型 | 结构化输出、证据引用、冲突/未知显式、成本记录、风险不可绕过 |
| M6 纸面交易闭环（条件阶段） | OTW runs/order 受控写入、幂等、T+1、风控、对账 | ChatGPT 研发、DSH 部署、用户批准 | 审计缺口解决 + 服务端冻结 + 用户明确解冻；否则保持只读 |
| M7 长期评估优化 | 多 AI/多策略独立组合、基准、归因、错因分析、策略版本复核 | ChatGPT 分析、DSH 维护 | 每次决策可复盘，按月统计相对基准，不因一次胜负自动调整规则 |

### M2 之后的优先实施顺序

**先补基础事实与实验验证，再接复杂 Agent。** 推荐下一开发单元按依赖执行：

1. M0-DSH：在现有 ChatGPT 会话完成 DSH 工具能力探查；可用时先试 `get_capabilities`/`list_tasks` 或同等只读功能，再提交受限诊断任务。
2. M2-DEPLOY：部署已合并 v0.2 的确定 SHA，在服务器同步 601118 与 RU0，记录来源、起止日期、质量与故障；不改变 OTW。
3. M3-DATA：分独立 provider 接入公司公告/财报、天然橡胶现货和库存（真实数据源、可追溯发布时点），天气/产量作为可选加强；不先做大规模爬虫。
4. M3-THESIS：把“供给变化 → 胶价 → 利润传导 → 预期差 → 风险补偿”编码为可证伪的研究卡，每项绑定证据、反证和失效条件。
5. M4-REGISTRY：研究算法策略与 OTW 模拟组合分离建模；引入 `strategy_version`、`model_version`、`dataset_id`、`risk_policy_id`、`portfolio_id`，默认 `disabled`。
6. M4-BACKTEST：完善手续费（含最低费、印花税）、停牌/涨跌停/T+1、复权、交易成本、样本外比较，并建立指数基准；以现有简单 SMA 作为可解释的基线。
7. M5-AGENTS：先研究/反方/风险三角色，再扩展 TradingAgents 思路；比较与纯规则决策的增量价值和 Token 成本。
8. M6-PAPER：只在审计与冻结问题解决后另立施工单，经明确批准才实现 OTW 模拟写入；不要与前述只读工作混合发布。

## 6. ChatGPT ↔ DSH 任务协议（目标契约，不代表已确认存在同名 API）

用户称已建立 ChatGPT → DSH 的连接；**当前会话是否已安装并暴露这些工具需要实际探测**。下列只是**要求实现的语义**，不是宣称 DSH 已有这些接口。

推荐能力集（可映射到实际不同的工具名）：

- `capabilities`：**只读**发现可调用任务类型、允许仓库/目录、身份与权限、支持的审批方式、执行器版本。
- `submit_task`：提交任务并返回唯一 `task_id`、`status`，最初应为 `queued/pending_approval`；不能把“提交成功”当作“执行成功”。
- `get_task(task_id)`：读取真实状态 `queued/approved/running/succeeded/failed/cancelled`、开始/结束时间和部署 SHA。
- `get_result(task_id)`：读取最终结果、验收项、脱敏日志摘要、错误分类及产物链接；无结果返回 `pending`，不能编造。
- `list_tasks`：按任务 ID 和时间查询历史任务，辅助掉线重试和去重。
- `cancel_task`：仅对可取消的任务；对已部署服务应另建回滚任务而非强制终止。

建议任务请求结构：

```json
{
  "task_id": "M2-DEPLOY-001",
  "kind": "deploy_preview",
  "repo": "yrainyXl/ai-investment-engine",
  "ref": "EXACT_COMMIT_SHA_FROM_GITHUB",
  "target": "/opt/apps/ai-investment-engine",
  "requested_by": "chatgpt",
  "approved_by": null,
  "approval_id": null,
  "allowed_actions": ["fetch", "build", "health_check", "read_only_data_sync", "backtest"],
  "forbidden_actions": ["otw_write", "order_submit", "change_auth", "enable_scheduler"],
  "instructions": "docs/HARNESS_DEPLOY_TASK.md",
  "idempotency_key": "M2-DEPLOY-001:EXACT_COMMIT_SHA_FROM_GITHUB",
  "requested_at": "RFC3339"
}
```

任务状态必须独立于聊天历史持久化，且所有变更使用同一个 `task_id`。部署任务在用户审批前只能被创建为 proposal；DSH 网关应独立验证用户身份、审批时效、操作白名单、目标 SHA，不能信任任务正文中的任意“批准”字样。

DSH 返回结构至少应有：

```json
{
  "task_id": "M2-DEPLOY-001",
  "status": "succeeded | failed | running | pending_approval",
  "executed_sha": "EXACT_SHA_OR_NULL",
  "started_at": "RFC3339_OR_NULL",
  "finished_at": "RFC3339_OR_NULL",
  "checks": [{"name": "healthz", "status": "passed | failed | skipped"}],
  "artifacts": [{"kind": "report", "path_or_url": "NON_SECRET_ARTIFACT"}],
  "error": null,
  "redacted_summary": "NO_TOKENS"
}
```

**回传机制：** 普通 ChatGPT 对话不具备常驻公网回调监听器；优先由当前聊天真实可用的 MCP/Plugin 工具主动 `get_task` / `get_result` 查询。若需要断线后通知，应让 DSH 在可信持久队列/GitHub Issue 中回写状态（必须验证来源）或通过独立自动化任务轮询，**不能假设 ChatGPT 能接收任何 HTTP push**。

### 连接验收步骤（按最小权限顺序）

1. 在当前聊天发现 DSH 实际工具名称和权限；缺失则记 `not_exposed_in_current_session`，不得代替实际调用。
2. 调用工具只读读取 `capabilities` 或等价状态接口；记录时间、工具名、脱敏结果。
3. 创建一个**只读**诊断任务（例如返回 Harness 版本/运行健康状态），记录实际 `task_id`；不部署、不写 OTW。
4. 实际调用 `get_task`/`get_result` 获取完成结果；验证状态从 pending/running 到 terminal，并记录证据。
5. 再开放需要用户审批的部署任务；以固定 SHA、白名单动作和最小权限执行。

## 6A. 真实 DSH MCP 工具映射（2026-11-07 已验证）

本节优先于第 6 节的抽象接口名。DSH 插件 `dev-6aef12fa6a9c8191b86486034dd241ef` 已在当前 ChatGPT 对话实际暴露。可用工具如下：

| 实际工具 | 用途 | 使用限制 |
|---|---|---|
| `mcp__dsh__list_dsh_sessions` | 枚举用户真实 DSH Web 项目会话 | 已成功调用 |
| `mcp__dsh__create_dsh_session` | 在服务器真实项目目录创建 Web 会话 | 已在 `/opt/apps/ai-investment-engine` 调用 |
| `mcp__dsh__send_dsh_message` | 将具体执行任务发给真实 Web Harness 会话 | 已实际调用，返回 `accepted=true`；不代表执行完成 |
| `mcp__dsh__read_dsh_session` | 读取会话历史、`running`、最终消息和 `turn_ends` | 已实际调用，先读到 `running=true` |
| `mcp__dsh__cancel_dsh_session` | 停止正在执行的当前 turn | 不撤销已经产生的变更 |
| `mcp__dsh__submit_task` / `get_task_status` / `continue_task` | 孤立的旧任务执行器 | **不要用于此真实项目**，与用户 Web 项目会话分离 |
| `mcp__dsh__deploy_task` / `get_deployment_status` | 独立 Node HTTP /preview 演示部署槽位 | **不适用于本项目现有 Docker Compose + FastAPI 服务** |

**实际关联主键**：DSH Web 会话 `session_id` + 一次发送的 `request_id`（重试必须复用）；收到终态时关联会话中的完整 assistant 回复、`turn_ends` 和执行 SHA。它不是孤立 `submit_task` 返回的 `task_id`；台账中的 `DSH job/task ID` 可以记录为 `session_id/request_id`。

目前已实际创建 Web 项目会话：
- `session_id=session-codex-ai-engine-m0-session-20261107-v1`
- `request_id=ai-engine-m0-readonly-dispatch-20261107-v1`
- 工作目录 `/opt/apps/ai-investment-engine`
- 初始派发 `accepted=true`，并独立回读 `running=true`
- **第一条任务是只读现场探针；完成状态以 DSH 最终结果为准**。

今后 ChatGPT 可在当前普通对话中通过上述 MCP 进行**真实项目任务派发与回读**，无需强制转到 Codex；工具可用范围仍取决于当前会话是否安装和授权对应插件。服务器端 ACL、审批和具体操作限制始终由 DSH 和用户约束。

## 7. 单任务闭环 SOP（失败也要留痕）

1. **规划**：ChatGPT 在 [PROGRESS_LEDGER.md](PROGRESS_LEDGER.md) 创建 `TASK-ID`，写明范围、依赖、负责人、验收和风险。
2. **开发**：ChatGPT 在 GitHub 分支实施代码、CI、PR；通过合并获得唯一的部署 SHA。不要让 DSH 独立编写第二套业务实现。
3. **审批**：用户对具体目标 SHA、风险操作、环境明确授权；低风险只读诊断依网关既定策略执行。
4. **派发**：ChatGPT 实际调用已发现的 DSH 工具，拿到真实 `task_id` 后状态改为 `dispatched`。调用失败或工具缺失则状态保持 `blocked`，不可声称已通知。
5. **回传**：依据 `task_id` 查询结果；检查 executed_sha、health、真实数据时间/来源、权限范围、日志是否脱敏。
6. **调整**：若是代码缺陷 → ChatGPT 修复并提交新 PR；若是网络/容器/密钥 → DSH 在批准范围内处理；若是数据源不可用 → 明确缺失，选择替代 provider 前先验证口径。
7. **验收**：只有实际证据满足验收项才标记 `done`，否则为 `failed/blocked/partial`；附 GitHub SHA、DSH job_id、结果来源和时间。
8. **复盘**：ChatGPT 更新进度与后继任务。不得用口头“已完成”代替工程证据。失败重试必须复用同一个幂等键或建立明确的重试记录。

## 8. 任务状态、决策记录与维护规则

进度台账： [PROGRESS_LEDGER.md](PROGRESS_LEDGER.md)

状态定义：`planned`（计划）、`in_progress`（开发/执行中）、`ready_for_dispatch`（代码/文档就绪）、`pending_approval`（待审批）、`dispatched`（工具已接受）、`running`（DSH 执行中）、`blocked`（依赖或工具缺失）、`partial`（部分完成）、`done`（有验收证据）、`failed`（已确定失败）、`cancelled`（取消）。

每条任务最少包含：ID、所属里程碑、负责人、目标、输入依赖、交付路径、代码 SHA、审批状态、DSH 实际任务 ID、状态、验收证据、更新时间和下一动作。未知字段必须明确 `unknown`，不要编造。

重要变更记录（ADR）至少包括：时间、决定、理由、备选方案、影响、批准者、回滚方式；尤其是数据供应商切换、回测口径变化、模拟下单开放、多个调度器的迁移。

## 9. 当前确证基线与即时下一任务

截至 2026-11-07 本文建立：

- GitHub 仓库 `yrainyXl/ai-investment-engine` 中 PR #1 已合并，合并 SHA `bbff880d2b051ee03a0df35678c9027895be1eec`，v0.2 已在代码主线；PR CI 记录显示通过，**不等于已在服务器部署 v0.2**。
- DSH 先前报告 v0.1.1 运行健康，绑定 `127.0.0.1:8018`，OTW MCP 只读联调成功；当前服务器是否已升级 v0.2 尚无新返回。
- 当前聊天可通过 GitHub 连接维护仓库。但**此前缺失，现已在 2026-11-07 当次插件唤醒后发现并实际调用 DSH Web Session 工具**；个人插件目录只查到 OTW MCP。用户所述 DSH 已打通可能在其他会话、工作模式或尚未暴露给本会话，已在本次 MCP 派发确认 `accepted=true`，但诊断完成/部署仍须独立验证。
- 下一优先任务：`M0-DSH-002` 通过 `read_dsh_session` 验收只读任务结果；若服务器仍是 v0.1，待符合审批条件后派发 `M2-DEPLOY-001` v0.2 部署和真实数据联调。

**版本判定原则：** GitHub merge、服务器运行 SHA、DSH 反馈 SHA、OTW 实时数据时间是四个不同事实，必须分别报告。

---
维护责任：ChatGPT。此文档只记录规划与协议，不包含凭证；服务器执行由 DSH 负责，未建立有效调用接口前不执行自动派发。
