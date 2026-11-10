# Sprint E2E-001 — 通用投资研究闭环（并行工作契约）

目标：一次研发周期内交付“真实来源数据读取 → 证据门禁 → 假设验证 → 风险评估 → 可解释的研究结论 → 本地审计报告”，在 GitHub 集成、CI、一次部署后按阶段验收。海南橡胶为第一组验证样本；第二组至少选择**另一行业或 ETF**。不能把代码模块完成当成业务闭环完成。

基线：main 1123a47570f37a38f3fed27fd842a465216ca55c（11/10），线上 v0.5.0，M5 风险纯函数已经在 main 但未部署。

## 并行职责、文件所有权

Worker A（数据上下文）：仅修改 `investment_engine/research_context.py`、`tests/test_research_context.py`、本工作包专属注释/文档。负责从 `FinancialFactStore`、`EvidenceStore` 和 `MarketDataStore` 的**已落地只读记录**构建统一资产研究上下文，as_of 防前视、披露与审核状态分离、真实来源/统计范围/原始数据质量透明。缺数据就输出 missing；不自行审批、不从历史对话冒充实时数据。不得调用 OTW 写接口、不能绕过现有审核门禁。

Worker B（结论与风险编排）：仅修改 `investment_engine/decision_report.py`、`tests/test_decision_report.py`。纯函数接收已生成的上下文/假设评估/估值状态/风险报告，生成 `DecisionReport`，必须区分 `insufficient_evidence`、`risk_blocked`、`review_ready`，结构化列举缺失证据、反方证据与风险触发。所有输出始终 `eligible_for_trading=false`，`orders_enabled=false`、`action=no_trade`（研究结论，不是自动买卖信号）。无外网、无真实账户操作。不要以正面行情替代基本面盈利验证。

当前 ChatGPT（集成/验收）：独占 `investment_engine/e2e_pipeline.py`、`tests/test_e2e_pipeline.py`、`investment_engine/api.py`、`README.md`、`docs/PROGRESS_LEDGER.md` 和 CI/部署文档。审阅 A/B 成果、统一接口、添加只读运行入口与本地 ReportStore 审计；统一 PR/CI；只有完整通过才安排**一次** DSH 生产部署。

## 固定数据接口（worker 可增字段，不要删这些字段）

A 导出 `build_research_context(*, root: str|Path, entity_id: str, as_of: datetime, asset_class: str="stock") -> dict`。
返回对象至少：
```json
{
  "schema_version": "1",
  "entity_id": "601118",
  "asset_class": "stock",
  "as_of": "<ISO8601 timezone-aware>",
  "data_status": "available|partial|unavailable",
  "financial_facts": [
    {"fact_id":"...", "metric":"revenue","value":"...","currency":"CNY","period_type":"year_to_date",
     "period_start":"...","period_end":"...", "source_url":"https://...", "published_at":"...",
     "retrieved_at":"...", "reviewed_at":null,"eligible_for_assessment":false,"eligibility_reason":"..."}
  ],
  "industry_observations": [],
  "missing_evidence": ["..."],
  "provenance_warnings": ["..."]
}
```
不能将 `eligible_for_assessment=false` 的事实提升为已确认。若证券无任何源数据，返回 unavailable 和合理缺项；不能凭空回填。

B 导出 `synthesize_decision_report(*, context: dict, hypothesis_results: list[dict], risk_report: dict|None, valuation_result: dict|None=None) -> dict`。
返回至少：
```json
{"schema_version":"1","entity_id":"601118","as_of":"...","decision_status":"insufficient_evidence|risk_blocked|review_ready",
 "action":"no_trade","orders_enabled":false,"eligible_for_trading":false,
 "supporting_factors":[],"contradicting_factors":[],"missing_evidence":[],
 "risk_flags":[],"rationale":[]}
```
规则：缺假设结果、任一核心结果 `not_evaluable`、上下文缺审核事实 => insufficient_evidence；有明确风险限额超标 => risk_blocked；只有通过完整质控才可 review_ready，但**review_ready 仍不自动下单**。异常/矛盾的数据永远 fail-closed。

## 技术与交付

- 模块边界以上述接口固定。所有测试用 Python stdlib / 项目现有依赖；合成数据明确标注 fixture，不说是实时行情。
- Worker 只能在独立的 Git worktree 和独立 feature branch 修改代码，禁止触及正在运行的 /opt/apps/ai-investment-engine/deploy/v0.2 主工作区，不执行 deploy/docker 操作、不修改 secrets，不开调度。
- A branch `feat/sprint-context-20261110`；B branch `feat/sprint-decision-20261110`；集成 branch `feat/sprint-integration-20261110`。每个 Worker 返回 commit SHA、测试结果、差异与阻塞。不要自行 merge main 或部署。
- 只有集成 PR 在 CI 全部成功且双资产验收测试通过之后，才一次性部署；部署过程按“服务健康→数据来源→假设门禁→风险→决策报告”五步验收。任何关键步骤失败就中止升级或依受控策略回滚。
- 与当前一期不同：网络问题限时排查；没有效果就不持续折腾，继续代码研发。代码合并与生产部署的完成状态必须分别记录。
