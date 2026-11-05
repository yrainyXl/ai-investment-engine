# ChatGPT 统一入口 → 用户审批 → DeepSeek Harness 执行：接入设计

## 结论

**可实现，但本仓库 v0.1 尚未具备远程 Harness 执行接口，也不代表当前 ChatGPT 已与服务器上的 Harness 连接。**

DeepSeek Harness 开源项目（`deepseek-ai/deepseek-harness`）目前公开了：

- `@deepseek-ai/dsh-acp`：Agent Client Protocol（ACP），用 **JSON-RPC stdio** 面向可信本地程序驱动 Agent 会话。
- `@deepseek-ai/dsh-mcp-client`：Harness 作为 **MCP 客户端**连接其他服务。此方向本身并不能让 ChatGPT 调用 Harness。

参考：
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/acp/acp/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md

因此：**ACP stdio 不是直接公开到公网的 HTTP API**。想让 ChatGPT 从网络触发 Harness，必须先在用户服务器实现一个带认证、限权、审计与审批检查的网关，或者使用一个共享的 GitHub 任务队列。

## 推荐分两段实施

### 阶段 1：GitHub 作为审核和任务交接（无需远程执行权限）

1. ChatGPT 生成代码、PR 和 `docs/HARNESS_DEPLOY_TASK.md` 这类执行说明。
2. 用户审阅 PR（或专门的待执行 Issue）后明确批准。
3. 用户将文档或 GitHub Issue 交给 Harness，Harness 仅在自己的服务器执行允许范围内的操作。
4. Harness 报告部署 SHA、健康状态、测试输出、变更范围。ChatGPT 再读取 GitHub 记录复核。

优点：权限隔离最简单；GitHub 留下有据可查的批准记录。

如果要半自动化，可由 Harness 在服务器使用只读 GitHub API 轮询带 `deploy-approved` 标签的 Issues，回报完成状态；但不能仅因 Issue 作者写了“已批准”就执行。必须校验实际授权者、标签变更和明确允许的任务类型。

### 阶段 2：部署最小权限 Harness Dispatch Gateway（未来单独项目）

结构：

```text
ChatGPT（提交操作请求）
   ↓ 独立 HTTPS MCP / Plugin 客户端认证
Dispatch Gateway（校验身份、审批凭证、任务白名单、重放防护）
   ↓ 入库 queued（不可执行任意 Shell）
User Approval（用户明确审批具体动作、目标、有效期）
   ↓ approved 后才派发
Server Executor（只接受已批准的任务类型）
   ↓ 通过本地 ACP stdio 管理 DeepSeek Harness 会话
Harness 在指定工作目录执行（受 OS/容器权限约束）
   ↓
任务状态、脱敏日志、PR/提交 SHA、验收报告回传 ChatGPT
```

对外建议仅暴露这些工具：

- `list_jobs`：只读任务列表和状态。
- `get_job`：只读单任务详情。
- `submit_proposal`：创建**待审批**任务，不能执行。
- `approve_job`：只有用户亲自通过可信审批面板或已授权身份确认，不允许把模型文本“同意”当作服务器端唯一授权。
- `run_approved_job`：执行前必须由网关验证独立审批凭据。
- `cancel_job`：取消排队/执行中的任务，并记录审计。

执行类型先限制为 `deploy_preview`、`run_tests`、`check_health`、`collect_redacted_logs`；**不支持自由 Shell 命令、不支持 root、不支持上传任意脚本并执行**。

关键实现条件：

- 网络：独立 HTTPS 网关或可信隧道；不公开 ACP stdio 端点。
- 认证：ChatGPT 使用专用、可撤销的短期/轮换凭证；Harness 的服务端凭证不返给模型。
- 授权：`proposal_hash`、`approved_by`、`approved_at`、过期时间、部署目标、具体 commit SHA；每次执行二次校验并保证幂等。
- 执行隔离：非 root UID、固定目录和仓库允许列表、独立容器；不挂载 Docker socket 或主机密钥给研究 Agent。
- 审计：所有状态转换持久化，脱敏处理，保留用户可复查的执行摘要和失败记录。
- 人工风险门槛：修改 OTW 写权限、启用模拟订单、修改生产交易规则等必须进入单独更严格的人工审批流程。

## 你需要提供的最少部署信息（仅服务端维护，不写入聊天）

1. DeepSeek Harness 的安装来源、版本/Commit、运行 profile，以及当前通过 SSH/容器/CLI 哪种方式调用。
2. 是否已经运行 ACP profile 或其他受控的远程任务服务；实际监听地址和网络边界。
3. 可否给 `ai-investment-engine` 独立部署账户/容器与只读 OTW API 凭证。
4. 希望使用 GitHub Issue 审批模式（简单）还是私有 MCP Gateway（更实时）。

上述信息足以让 Harness 自己评估后续集成，不需要向 ChatGPT 发送任何访问服务器的密码或 token。

## 重要限制

- ChatGPT 当前已连接 GitHub，可在授权范围内创建/更新仓库内容与 PR；**现阶段没有该服务器 Harness 的已配置执行工具**。
- ChatGPT 的自动化或 GitHub 连接**不会自动获得**服务器的执行权限。
- 一旦网关开发完成，还需单独在 ChatGPT 中配置相应插件/工具，并验证实际权限及审批；在此之前不能声称“已直连”。
- 任何未经用户审核的 AI 代码不可自动部署到正在运行的金融/交易服务。
