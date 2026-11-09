# DSH 部署任务：M4 / v0.5.0（PR #13）

## 任务边界

只部署 ChatGPT 已提交、GitHub Actions 测试通过并合并的正式 SHA。**执行前由 ChatGPT 提供确切 merge SHA**；不得用此文档中的 PR head SHA 代替。

目标仓库：yrainyXl/ai-investment-engine
部署目录：/opt/apps/ai-investment-engine
部署分支：deploy/v0.2
目标镜像：ai-investment-engine:0.5
目标 API version：0.5.0

本次只涉及：确定性投资假设验证器、审计报告、what-if 场景、人工复核入口（默认禁用）及估值纯函数。没有 OTW 写 API、券商交易、模拟订单或新定时任务。

## 前置检查

1. 检查当前分支 deploy/v0.2、工作树 clean、现有 healthz 200、当前 SHA 和数据卷状态。
2. git fetch origin；核查目标 SHA 是 origin/main 的祖先，且部署分支能 fast-forward。网络失败、工作树不干净或目标不一致就立即停止。
3. 只执行 git merge --ff-only <ChatGPT 交付的精确 SHA>；不得 reset、rebase、force。
4. 保持 .env 完全不变。特别是 **不得自行生成或配置 ENGINE_FACT_REVIEW_TOKEN**，该凭证只能由人工审核管理员单独配置；当前缺失意味着审核端点安全禁用。
5. 只重建 engine 服务，保留数据卷、127.0.0.1:8018、只读 rootfs、cap_drop ALL 和 no-new-privileges；不触碰其他服务。
6. 运行 healthz，必须 200、version=0.5.0、orders_enabled=false。

## 只读验收

1. 核查历史 OHLCV 缓存、回测报告、CNINFO 公告元数据、99qh 行业观察及 financial_facts.json 仍在；只报条数和数据日期，不输出大量原始记录。
2. 原始 2026-Q3 机器财务事实仍全部 reviewed_by_human=false、unit_verified=false、scope_verified=false、assessment=unassessed；不要模拟人工审批。
3. 无需再次同步 CNINFO、行业或市场数据，不重试已成功的外部 Provider。
4. 使用本地存在的 2026-Q3 同期 revenue 与 cost_of_revenue fact_id 调用 /v1/hypotheses/evaluate 做一次测试：因事实未人工审核，结果必须是 status=not_evaluable，computed_value=null。允许产生本地一份审计报告，不允许改事实。
5. 用错误 / 缺失 X-Fact-Review-Token 发起测试请求，验证审核端点安全拒绝（默认 ENGINE_FACT_REVIEW_TOKEN 未配置，应该返回 503）；禁止提供正式审核凭证或真实人工审核。
6. 如任何一步失败，报告第一处失败及脱敏错误；不要自行修改代码或配置，不要重试外部同步。

## 回传

报告实际 Git SHA、镜像、healthz 状态、已有数据统计、假设评估 status、审核端点拒绝状态和安全配置是否保持不变。不要声称本次完成真正的证券投资决策；只是 M4 数据质量门禁验收。
