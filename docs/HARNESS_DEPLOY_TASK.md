# DeepSeek Harness 执行任务：部署 AI Investment Engine v0.1（只读）

> 面向在用户服务器上运行的 DeepSeek Harness。请完整阅读本文件后再执行。所有部署命令都应以实际环境核查结果为准，不假定当前运行路径、容器名、端口或凭证。
>
> 源码仓库：https://github.com/yrainyXl/ai-investment-engine
>
> **绝对边界：不执行真实证券交易、不提交 OTW 模拟订单、不修改 OTW 现有投资组合/策略文档/历史运行记录，不停止现有 ChatGPT 定时任务。** 本阶段仅安装并验证一个隔离的只读研究服务。

## 0. 操作权限及审批

允许：只读查看服务器运行状态、检查 Docker/Compose、检查端口占用、拉取本仓库代码、运行测试、在独立目录构建并启动本项目。

必须在执行前请求用户批准：安装系统软件、调整防火墙或反向代理、修改其他应用/共享 Docker 网络配置、重启宿主机或其他服务、删除任何数据、给任何 Agent 开放公网访问、赋予额外 OTW 写权限。

禁止：使用 `docker compose down -v`，清空磁盘/数据库，导出或打印 Token、SSH 私钥、OTW 环境变量内容，向 ChatGPT/GitHub Issue/PR/日志发送密钥，为方便调试关闭 TLS 校验、在命令行参数或公开 URL 中放 Token。

## 1. 只读环境调查（先完成，不改服务器）

1. 检查 `docker --version`、`docker compose version`、Python 版本、可用磁盘容量和端口 8018 是否已使用。
2. 确认 OTW 和 DeepSeek Harness 正在运行的方式、各自容器与网络名称，但不要读取或打印它们的机密环境变量。
3. 确认 `ai-investment-engine` 的部署目录位置。建议独立目录，例如 `/srv/ai-investment-engine`，不要覆盖现有服务。
4. 如果 8018 已占用，选择新的 **127.0.0.1** 回环端口并在报告中说明。不得未经审批更改其他服务端口。
5. 若仓库设为 Private，请让用户安装只读 Deploy Key 或使用既有 GitHub 授权，**不得让用户把 PAT 粘贴到聊天、Issue 或项目文件**。

阶段报告：只返回 Docker/Compose 可用性、建议目录、端口是否可用、OTW 访问方案（同机回环还是 HTTPS），**绝不输出凭证**。

## 2. 拉取代码与测试

```bash
# 目录仅为建议；按第 1 步实际情况选择
sudo mkdir -p /srv/ai-investment-engine
sudo chown "$(id -un):$(id -gn)" /srv/ai-investment-engine
cd /srv/ai-investment-engine
# 空目录才 clone；已存在时先检查 git status，保留未提交修改
git clone https://github.com/yrainyXl/ai-investment-engine.git .
git status --short
git rev-parse HEAD
```

> 若非空或无权限，先调整操作方案并请求必要批准，**不要盲目执行清理或 `git reset --hard`**。

使用 Docker 验证无需污染全局 Python 环境（此时尚未准备 `.env`，先独立 build/run）：

```bash
docker build -t ai-investment-engine:0.1 .
# 仅使用仓库中明确标记的 synthetic fixtures；无网络、无 Token
# 可以直接运行 CLI，无需安装 pytest：
docker run --rm --network none ai-investment-engine:0.1 \
  python -m investment_engine.cli --source fixture --out /tmp/preview.synthetic.json
```

如果 `docker compose build` 因拉取基础镜像/依赖失败，应记录网络或代理错误；不得绕过证书验证。测试阶段可使用服务器现有的 Python 虚拟环境运行 `python -m pip install -e '.[api,test]'` 后 `python -m pytest -q`，但必须隔离在该项目环境。

## 3. 创建密钥配置（仅在服务器上）

- 根据 `.env.example` 在项目目录创建未纳入 Git 管理的 `.env`，权限设为 600。
- `ENGINE_MODE=preview`。
- `OTW_BASE_URL` 指向服务端能访问的 OTW HTTPS API 地址（如果同机走回环，可用 `http://127.0.0.1:<受控端口>`）。
- `OTW_API_TOKEN` 由 OTW 管理端生成或安全注入，应限制到本策略所需的**只读**范围；如果当前服务端没有相应的 token/权限，停止真实数据联调并报告，不应复用不明来源的凭证。
- `OTW_STRATEGY_ID` 使用 OTW 现有“ai chatgpt海南个股投资”模拟策略 ID，不创建新策略。
- `ENGINE_API_TOKEN` 为单独生成的高强度随机密钥，仅控制对本引擎预览 API 的访问；它与 `OTW_API_TOKEN` **必须不同**。
- `.env` 文件、内部网络地址、服务器主机信息和用户数据不提交 GitHub。

**与用户确认来源后再配置真正密钥。不要自动抓取其他进程的环境变量、证书、浏览器 Session 或机密存储。**

## 4. 启动部署

```bash
cd /srv/ai-investment-engine
# 确保 .env 已在服务器配置、compose.yaml 本机端口无冲突
docker compose config --quiet
docker compose up -d --build engine
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8018/healthz
```

预期 `/healthz` 返回 `status=ok`、`mode=read_only_preview`、`orders_enabled=false`。`compose.yaml` 仅监听 127.0.0.1，不允许随意换成 0.0.0.0 或公网域名。

验收 HTTP 权限：

```bash
# 未带授权访问 fixture 在 ENGINE_API_TOKEN 配置后应返回 HTTP 401
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Content-Type: application/json' \
  -d '{"source":"fixture"}' \
  http://127.0.0.1:8018/v1/analysis/preview
```

不要在可以留存的 Shell 历史中直接敲入真实 token。可在可信会话中通过安全方式读取密钥后测试授权请求，但不要在执行记录中打印值。

## 5. OTW 真实数据只读联调

在启动后运行下面的容器内部 CLI（`--source otw` 不会向 OTW 写入任何数据）：

```bash
docker compose exec -T engine python -m investment_engine.cli \
  --source otw --out /tmp/otw-preview.json
```

核对：`source=otw`、目标策略 ID、现金和持仓数量、全局策略文档更新时间，与 OTW 前台一致；量化数据缺失则输出 unavailable，不能伪造历史数据。若服务器返回 401/403、DNS/TLS 失败或错误数据，按失败报告处理，不擅自改动 OTW 鉴权或数据。

建议运行前后只读查询 OTW 的 runs 和 orders 总数，确认没有因为 v0.1 产生记录。现有文档、资产和定时任务均保持不变。

## 6. 交付验收与回滚

交付报告需要包含：

- 当前部署 Git commit SHA（运行 `git rev-parse HEAD`）；
- Docker 服务状态、健康检查状态、本机绑定端口；
- `python -m pytest -q` 的真实结果（如果运行），或解释仅完成容器 CLI smoke test；
- fixture 验收结果和 OTW 真实只读验证结果（成功/失败及原因）；
- 未提交订单、未更改 OTW、未停止旧任务的检查结论；
- 遗留问题和需用户批准的下一步动作；
- 不要复制任何 `.env` 内容、Authorization Header 或包含可复用密钥的日志。

失败时：仅回滚本项目容器（`docker compose stop engine`），保留部署目录和已存在的数据；不得触碰 OTW/DeepSeek Harness 容器，不得使用带 `-v` 的清理命令。

## 明确不在本次部署范围

1. 不接入 AKShare 网络爬取、Qlib、AKQuant 或 TradingAgents；这些属于未来版本。
2. 不部署反向代理、公网入口、MCP remote executor、ChatGPT → Harness 自动任务派发。
3. 不创建定时任务或启用任何自动订单。
4. 不请求服务器 root 全局控制权；只使用部署所需最小权限。
