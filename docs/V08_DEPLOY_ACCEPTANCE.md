# v0.8 部署验收单

## 发布标识

| 项目 | 值 |
| --- | --- |
| 待验 SHA | `aa9bd618f0e249124aa38f27bdb589abd4c0dfb9` |
| 镜像 | `ai-investment-engine:0.8.0` |
| API、OpenAPI、Python 包版本 | `0.8.0` |
| 上次确认生产代码 | `85657beeacb52fedde825f3dab60dc2518f6752c` |

此单供后续获授权的一次部署和验收使用。固定 SHA 指向发布代码提交，GitHub 集成 PR 的后续验收单提交仅更新文档。执行前将目标提交与主分支核对。
生产实例保持 localhost、研究只读、无交易调度。任何门禁失败均记录证据并停止推进。

## 部署前检查

1. 在当前部署目录确认工作树干净，记录现有提交、Compose project 名、引擎镜像 ID 和
   当前 `/healthz`。确认主机数据挂载路径、属主和权限正确。留存旧 Compose 配置和
   可启动的旧镜像，按现场流程对本地持久化数据作一致性备份。
2. 记录股票、ETF 与期货的 cache key、日期范围、行数和校验和。记录公告、行业观察、
   财务事实、手工审核标记和既有报告的数量与校验和。凭据、账户信息和原始生产日志留在主机。
3. 确认 `ENGINE_API_TOKEN` 已配置，`ENGINE_LLM_ENABLED=false`。OTW 账户研究默认关闭。
   若单次验收已获授权，提供独立 GET-only `ENGINE_OTW_RESEARCH_TOKEN`、可信 HTTPS
   `ENGINE_OTW_RESEARCH_BASE_URL`、`ENGINE_OTW_RESEARCH_STRATEGIES` 资产到策略 UUID 映射。
   原有 OTW 配置由既有接口使用。财报审核凭据与引擎和 OTW 凭据分别管理。

## 固定构建与切换

在既有生产工作树执行，保持项目名、原数据目录、运行凭据及现有网络隔离。构建镜像后仅切换
`engine` 服务。优先使用现场已有的有界网络及部署命令封装。

```bash
set -eu
v08_sha=aa9bd618f0e249124aa38f27bdb589abd4c0dfb9
timeout 60 git fetch origin refs/heads/main:refs/remotes/origin/main
git cat-file -e "$v08_sha^{commit}"
git merge-base --is-ancestor "$v08_sha" origin/main
test -z "$(git status --porcelain)"
git merge-base --is-ancestor HEAD "$v08_sha"
git merge --ff-only "$v08_sha"
test "$(git rev-parse HEAD)" = "$v08_sha"
export ENGINE_GIT_SHA="$v08_sha"
docker compose config --quiet
docker compose build engine
test "$(docker image inspect ai-investment-engine:0.8.0 \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" = "$v08_sha"
docker compose up -d --no-deps --no-build engine
docker compose ps engine
curl --fail --max-time 5 http://127.0.0.1:8018/healthz
```

## 验收门禁

- `/healthz` HTTP 200，`version=0.8.0`，`git_sha` 等于固定 SHA，
  `orders_enabled=false`。OpenAPI 同版本，存在 `POST /v1/research/analyze` 及报告查询。
  无 bearer 的研究请求返回 401。
- 在运行任何数据同步前，对部署前记录的历史数据和报告逐项对账。既有文件校验和、记录数、
  覆盖日期和审核状态应一致。确认后才可以按现场授权补充目标资产的不复权日线，单独记录来源、
  更新日期和新增行数。复权缓存可作为带标识的参考。
- 按 README 命令提交 `601118.SH` 股票及 `510300` ETF 请求并按报告 ID 回读。
  验证来源、最新可用时点、数据口径、假设结果、Agent 状态、估值与风险缺口。
  两条链路均须 `action=no_trade`、`orders_enabled=false`、`eligible_for_trading=false`。
  对 ETF 提交独立 IS/OOS 窗口，核对 benchmark、成本、两段指标与数据指纹。样本不足时
  保留 `not_evaluable` 及理由。历史 as_of 早于抓取或审核时间时，相应证据不可用。
- 模型默认关闭。报告须显示 gateway `availability=disabled`、模型请求为零。需要启用时，
  由人工独立提供并核对受控 API Key、可信 HTTPS 端点、模型版本、价格和单次预算，
  并批准付费验证。现有研究 bearer 对这些开关和人工审核均无配置权限。
- OTW 已配置时，从服务日志或只读网关审计核对本次研究仅访问 strategies 与所绑定的
  context 两个 GET。检查快照、报价有效期、币种和集中度。缺失报价、历史账户快照、
  行业映射或权益序列时保留 unknown 原因。OTW 未配置时预期账户风险 unknown。
- 验收回执记录实际 SHA、镜像 ID、health、OpenAPI 版本、数据对账结果、两份报告 ID、
  模型网关状态和 OTW GET 核验结果。故障说明仅提供脱敏摘要。

## 回滚

如切换后的服务不健康或安全/数据校验失败，使用部署前记录的旧镜像 ID 和旧 Compose 配置。
将旧镜像 ID 标记为专用本地回滚 tag，并通过 Compose override 为现有项目的 `engine`
指定该 tag。使用旧配置及当前生产数据路径执行 `docker compose ... up -d --no-deps
--no-build engine`。核对旧镜像 ID、原 health、挂载、历史数据与订单冻结。保留失败报告和
新旧镜像以供复盘，数据备份仅在发现实际损坏且得到独立恢复授权时使用。
