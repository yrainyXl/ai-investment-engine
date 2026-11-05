# GitHub 与服务端操作（可交由 DeepSeek Harness 执行）

## 1. 源码仓库（已创建）

目标仓库：`https://github.com/yrainyXl/ai-investment-engine`。建议将当前 Public 仓库改为 Private。

由 Harness 在服务器上从 `main` 拉取代码；使用 deploy key 或已有凭证，不要把 token 塞进 URL。不得擅自删除正在运行的 OTW 或 DeepSeek Harness 服务。

如果采用 HTTPS remote，请使用 GitHub 提供的标准认证，不要将个人访问令牌写进 shell 命令、README 或代码。

## 2. 优先在本地验证

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[api,test]'
make test
make demo
```

此步骤只使用**合成数据**，不会访问 OTW。

## 3. 使用真实 OTW 做只读验证

```bash
export OTW_BASE_URL='https://YOUR_OTW_HOST'
# 使用秘密管理器或私有环境变量注入 token，不要提交 .env
export OTW_API_TOKEN='YOUR_READ_ONLY_TOKEN'
python -m investment_engine.cli --source otw --list-strategies
python -m investment_engine.cli --source otw --out output/otw-preview.json
# 多个 active 策略时，选择指定 UUID 或仅进行批量只读预览：
python -m investment_engine.cli --source otw --strategy-id '<从 OTW 返回的 UUID>' --out output/one.json
python -m investment_engine.cli --source otw --all-active --out output/all-active.json
```

确认输出中的 `strategy_id`、`account_summary`、`thesis.updated_at` 与 OTW 前台一致。此过程不会创建 runs 和 orders。

**警告**：如果当前 OTW token 不是 HTTP Bearer 或服务器不允许该权限，不要反复尝试猜测鉴权；查看后端实际配置，并由部署端修复。不要把 token 发进 ChatGPT。

## 4. 后续需要服务进程时（暂不安排定时任务）

本机方式：

```bash
uvicorn investment_engine.api:app --host 127.0.0.1 --port 8018
```

推荐 Docker Compose（先准备不入库的 `.env`）：

```bash
cp .env.example .env
chmod 600 .env
# 通过服务器密钥管理方式填写 OTW_BASE_URL、OTW_API_TOKEN、ENGINE_API_TOKEN
# 注意：ENGINE_API_TOKEN 必须与 OTW_API_TOKEN 不同。
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8018/healthz
```

真实只读验证（不要在命令、输出和日志中暴露 Token）：

```bash
docker compose run --rm --no-deps engine python -m investment_engine.cli \
  --source otw --out /tmp/otw-preview.json
```

完整逐项说明及失败处理见 `docs/HARNESS_DEPLOY_TASK.md`。

`-p 127.0.0.1:...` 很重要，确保预览 API 不被直接暴露到公网。若未来需要从 OTW 容器访问，请改成受控内部网络 + 服务鉴权，而不是无鉴权公网端点。

## 5. 不应在这一轮做的事情

- 不要关停已有 ChatGPT 每日四次分析任务。
- 不要将旧实验自动成交冻结改成开启。
- 不要为测试创建模拟成交或重置现有纸面持仓。
- 不要安装仓库中的第三方 Skill 后直接赋予写入 OTW /orders 的权限。
- 不要把投资服务接真实券商交易 API。

下一轮以真实只读数据的端到端验收为基础，再考虑补历史 K 线、研究插件与回测。

## 多策略发现与审批边界

无需把策略 ID 放入环境变量，也无需因为增加模拟账户而重启容器。使用 `GET /v1/strategies` 动态列举经 OTW 授权可见的策略；`POST /v1/analysis/preview` 通过请求体 `strategy_id` 指定分析目标。若未指定且仅存在一个 active 策略，自动选择；若有多个 active，则要求显式选择，以免误分析错误账户。CLI 的 `--all-active` 可逐个运行全部 active 策略的**只读预览**。研究规则、证据和回测数据将来需与各策略单独关联，不能跨策略复用。自动发现不表示自动执行交易。
