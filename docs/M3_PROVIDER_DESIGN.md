# M3 Provider v1：证据底座与首批数据源

本阶段只增加公开来源读取和本地研究数据存储；不改变 OTW 权限，不增加订单或调度。

## 1. 统一观察记录

`investment_engine.evidence.Observation` 将行业指标存为可追溯记录：

- subject / factor / metric / value / unit
- series_id / series_definition
- source_name / source_url
- effective_at：指标所描述的实际期间/日期
- published_at：来源正式发布日期；未知时必须留空
- retrieved_at：本系统抓取时间
- revision_id：供应商修订版本（若可用）
- evidence_type：confirmed_fact / market_expectation / inference
- assessment：supporting / contradicting / unassessed
- reviewed_by_human / unit_verified

**Provider 抓到数据 ≠ 已有可用于投资判断的证据。** 只有发布时间、有效时间、抓取时间均不晚于 `as_of`，且来源、口径/单位经过核实、人工复核并明确支持/反对时，才会标记 `eligible_for_assessment=true`。推断永远不能代替事实或市场预期。

文件保存在 `ENGINE_DATA_DIR/evidence/`，不是 Git 版本数据：
- `industry_observations.json`
- `source_documents.json`

## 2. Provider v1

### CNINFO 公司公告索引

实现：`CNInfoDisclosureProvider`

- 官方站点： https://www.cninfo.com.cn/
- 查询对象默认 `601118`，先动态解析 orgId，再按公告日期查询。
- 保存公告标题、公告编号、发布日期、公告/PDF 链接及检索时间。
- **当前只索引元数据，不下载/解析 PDF，不自动抽取营收、成本或自产/外采比例。** `content_status=metadata_only_pdf_not_parsed`，不可把公告标题作为财务事实。

命令：
```bash
python -m investment_engine.market_cli sync-disclosures --symbol 601118 --start-date 2020-01-01 --end-date 2026-11-07 --max-pages 5
```

### 天然橡胶库存（统计范围尚未核实）

实现：`AkShareRubberInventoryProvider`

- AKShare：`futures_inventory_99(symbol="天然橡胶")`
- 来源站点： https://www.99qh.com/data/stockIn
- 该接口实际返回 `日期`、`收盘价`、`库存`，**未声明库存统计范围**，不能将其称为上期所交割仓库库存，也不能作为青岛/全国社会库存。
- 当前 AKShare 返回字段未提供可验证的单位和独立发布日期，所以 unit 标记 `source_native_unverified`、`published_at=null`、`unit_verified=false`。它可作为原始观察留存，但不会自动填充 `multiweek_inventory_trend` 证据门。
- 后续要补上期所官方仓单日报的单位/原始发布日期核验，并单独接入隆众/卓创等青岛与全国社会库存；不得将不同库存口径相加或拼成一条连续序列。

命令：
```bash
python -m investment_engine.market_cli sync-industry
```

## 3. API

需要现有 `ENGINE_API_TOKEN`：
- `GET /v1/industry/observations?series_id=...&as_of=RFC3339&limit=500`
- `GET /v1/fundamentals/disclosures?symbol=601118&as_of=RFC3339&limit=200`

两个接口都只读。同步由本地 CLI 执行，不存在 HTTP 写入 evidence 的路由。

研究快照附带 `provider_data_summary`，只汇总本地 Provider 数据覆盖情况，不会自动将未经审核的数据注入假设状态。

## 4. 已知缺口 / 下一小步

1. CNINFO 公告 PDF 正文下载与解析（先对 2026 三季报做字段级人工对照测试）。
2. 将报表期末日与正式披露日分开，抽取合并报表和母公司报表时明确 scope；严禁混用。
3. 对“利润传导”至少需要分业务收入/成本/毛利、原料采购成本、销量、自产/外采结构；只有 RU0 价格不足以证明。
4. 接入上期所官方天然橡胶仓单日报，并另行获取青岛/全国社会库存的稳定周序列；每个系列保留自己的单位和统计范围。
5. 现货价格要按胶种、等级、地区、含税/不含税、交货方式区分，不能将 RU0 连续期货价代替海南橡胶实际采购/销售价格。
6. 预期数据需有可回溯的分析师预测/一致预期发布时间快照；当前股价涨跌不等于市场预期本身。

## 5. 安全与验收

- 无 OTW 写 API、订单、定时任务。
- 数据只写入本地 `/data/evidence`。
- Provider 失败明确报错，不生成空值/假记录。
- 原始观察默认未人工审核，未核实单位或发布日期的数据不可用于历史假设评估。
