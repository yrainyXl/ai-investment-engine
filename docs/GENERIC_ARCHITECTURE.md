# 通用数据模型与行业插件边界

## 设计原则

AI Investment Engine 面向多资产、多证券和多行业。海南橡胶（601118）只是首个端到端验收样本，不是产品边界。

分层约束：

1. **通用财务事实层**：以 entity、metric、value、currency/scale、period、statement scope、source/provenance 为核心。不得把股票代码、公司名称或行业特定字段写入公共模型。
2. **数据源 Provider 层**：CNINFO、交易所、基金公告、行情供应商各自实现适配器。Provider 可以有源站特定解析规则，但必须输出统一契约。
3. **行业指标层**：用可注册的 `IndustryMetricDefinition` 描述指标 ID、行业、单位、统计范围和聚合方式；用 `IndustryMetricObservation` 保存观察值。天然橡胶库存与白酒渠道库存属于不同指标，不得混用。
4. **策略注册层**：`StrategyDefinition` 描述资产类别、输入依赖、参数 schema、决策周期、风险模型和实现入口。注册元数据不等于策略已启用，更不自动执行。
5. **决策层**：后续将通用事实与行业指标输入研究 Agent、反方 Agent、量化验证与风控门。任何单一 Provider 输出都不能直接变成交易指令。

## 本次 v0.4 边界

- `contracts.py` 提供 provider-neutral 的 `CompanyFinancials`、`IndustryMetricDefinition`、`IndustryMetricObservation`、`StrategyDefinition` 和 `StrategyRegistry`。
- `financials.py` 当前是一个**CNINFO 中国 A 股季度报告 PDF Provider/解析器**，并非通用 PDF 财报解析器。它输出通用财务事实字段，但目前只支持机器可读中文季度报告的有限版式与合并报表口径。
- CNINFO PDF 域名白名单、公告元数据字段、中文行标签属于 Provider 实现细节，不进入通用契约。
- 不支持的报告版式必须明确失败，不允许用错误行/列静默填值。
- 基金净值、基金持仓、ETF 申赎和其他资产类别仍需各自 Provider；不得将上市公司利润表模型套用到基金。

## 新增资产/行业的扩展方式

1. 新增或复用标准 entity ID 与 asset type。
2. 选择数据源并实现 Provider，将源字段映射到通用契约。
3. 若有行业特有指标，注册独立的 IndustryMetricDefinition。
4. 为 Provider 增加 fixture 测试：单位、期间、来源时间、统计范围、缺失值、修订数据。
5. 用第二家不同公司的真实报告验收通用财务模型，再增加半年度/年度报告及其他市场数据源。
6. 只有当策略依赖数据通过质量、时点与人工审核门后，才允许策略评估使用。

## 明确不做

- 不为每个策略设置环境变量或硬编码策略 ID。
- 不把行业专属指标塞进 CompanyFinancials。
- 不将机器提取标记为人工确认事实。
- 不启用 OTW 写入、模拟订单或真实交易执行。
