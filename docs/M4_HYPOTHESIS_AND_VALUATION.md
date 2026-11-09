# M4：投资假设验证、情景分析与估值基础

## 目标

M4 建立可复现的确定性计算层，把投资 thesis 拆成可验证变量、公式、阈值、数据质量条件和时间边界。它不负责生成买卖指令，也不替代后续风险引擎和 Agent 决策层。

核心约束：
- 同一份输入、公式版本和 as_of 必须得到同一结果。
- 只使用显式声明的有限运算，不执行 eval、Python 表达式或用户代码。
- 事实值必须从本地 evidence store 按 fact_id 解析；API 不接受调用方自报的 review/unit/scope 标记。
- 人工审核是独立操作；审核事实不等于该事实支持某个投资假设。
- 场景覆盖值必须标记为 scenario_assumption，不能覆盖或污染原始事实。
- 所有计算仅写入本地审计报告，不写 OTW、不下单、不创建调度。

## 1. 财务事实审核

机器提取的 financial_facts.json 记录默认：
- reviewed_by_human=false
- unit_verified=false
- scope_verified=false
- assessment=unassessed

通过 POST /v1/fundamentals/facts/{fact_id}/review 才能记录人工审核。请求要求 reviewer、review_note、unit_verified、scope_verified 和 confirm_source_checked=true。审核会写入 reviewer、reviewed_at 和审计报告，但不改变提取数值、原始来源或 thesis assessment。

审核时间也受 point-in-time 约束：若 reviewed_at > as_of，历史评估不能使用该事实。只要单位或报表范围未确认，事实仍不可用于量化假设验证。

eligible_for_assessment 表示事实已经过人工/单位/范围审核且在 as_of 可见；它不代表已经判定支持或反驳某个 thesis。assessment 保持 unassessed，直到独立评估步骤产生结果。

## 2. 假设定义与输入

每条 HypothesisDefinition 至少包含：
- hypothesis_id、entity_id、strategy_id、claim
- operation、variables（变量 ID 列表）
- target、target_unit、direction、tolerance
- 可选 max_age_days

每个输入变量由本地财务事实解析，包含 metric、exact decimal value、unit、period_type、period_start/end、fact_id、published_at、effective_at、retrieved_at、reviewed_at 和审核标记。

当前 API 首先支持本地 FinancialFactStore 中的公司财务事实，且要求 fact.symbol 与 hypothesis.entity_id 一致。ETF NAV、基金持仓、行业观察和宏观指标后续通过 provider-neutral resolver 接入；不能把当前第一版描述为所有资产数据都已接通。

## 3. 支持的确定性运算

| operation | 公式 | 约束 |
|---|---|---|
| sum | Σ xᵢ | 同单位、同期间 |
| difference | x₁ − x₂ | 两个输入，同单位、同期间 |
| product | x₁ × x₂ | 两个输入、输出单位相乘 |
| ratio | x₁ / x₂ | 两个输入，同单位、同期间，分母非零 |
| margin | x₁ / x₂ | 同 ratio；例如毛利 / 收入 |
| growth | (current / prior) − 1 | 同单位、同 period_type，比较期间时长相同 |
| weighted_sum | Σ(wᵢ × xᵢ) | 同单位、每个输入一个权重 |

当前不做自动单位换算；单位字符串不完全一致时拒绝计算。YTD 与单季度数据不可混算。增长运算的变量顺序固定为 current、prior。

阈值判定：
- above / below 在 tolerance 带内返回 inconclusive。
- equal 在 tolerance 范围内返回 supported，否则 contradicted。
- 数据缺失、质量不合格、期间不匹配、单位不匹配或分母为零，返回 not_evaluable。

## 4. 情景分析

POST /v1/hypotheses/scenarios 对同一组已审核事实运行 baseline 和显式场景覆盖。

场景覆盖必须引用 hypothesis 已声明的变量；每个覆盖值是精确十进制假设，并在结果中标记 input_kind=scenario_assumption、source_id=scenario:<scenario_id>:<variable_id>。场景是假设，不是事实、市场预期或预测。原始事实文件不会被改写。

## 5. 估值计算库

investment_engine/valuation.py 提供纯函数：
- Multiple valuation：已审核的每股指标 × 明确倍数。当前要求每股指标为正值，不对负 EPS 套用常规正 PE。
- DCF：逐年 FCF 折现 + Gordon Growth Terminal Value − Net Debt，再除以 Shares Outstanding。
- DCF sensitivity：WACC × terminal growth 网格；所有 WACC 必须大于 terminal growth。

估值输入的源事实、模型假设、人工审核时间、单位和口径都要可追溯。该版本的 valuation 纯函数尚未接入 HTTP API 和本地 facts resolver；不能把函数存在视为估值数据已经自动打通。后续接入前要先把 EPS/BVPS、净债务、股数和预测现金流的来源契约做好。

## 6. API

所有接口均需 ENGINE_API_TOKEN：
- GET /v1/fundamentals/facts：读取财务事实及审核状态。
- POST /v1/fundamentals/facts/{fact_id}/review：明确的人工审核写入，仅改本地 evidence 文件和审计报告。
- POST /v1/hypotheses/evaluate：使用 fact_id 从本地 facts store 解析输入，执行确定性假设评估。
- POST /v1/hypotheses/scenarios：baseline + what-if 场景。
- GET /v1/reports / GET /v1/reports/{report_id}：读取本地审计报告。

POST 计算接口只产生本地审计报告，不写 OTW。审核 endpoint 是唯一新增的事实审核写入口；禁止自动任务或分析 Agent 自行调用它，必须有明确人工核验和批准。

## 7. 明确不在 M4 范围内

- LLM 研究 Agent、反方 Agent、投票/仲裁机制。
- 组合仓位、风险预算、最大回撤限制、止损与流动性风控。
- 自动买卖建议、模拟订单提交、真实交易或券商接入。
- 行业/基金/宏观 Provider 的完整接入。
- 自动将评估结果写回 OTW 的策略文档。

这些工作依赖 M4 的可复现计算与质量门禁稳定后，再分阶段建设 M5 风险层和 M6 Agent/决策层。
