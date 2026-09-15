# Purslyx 任务 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 任务 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 已实现并通过 201 PostgreSQL Worker 验收 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [任务模块设计](../modules/任务模块设计.md) |

## 1. Task Schema

```json
{
  "id": "af52b918-a90f-49fd-b3fa-c80923fb3d97",
  "task_type": "analysis",
  "status": "running",
  "current_step": "evidence_validation",
  "progress": {
    "completed_steps": 3,
    "total_steps": 6,
    "display_percent": 50
  },
  "input_versions": [
    {
      "resource_type": "document_version",
      "resource_id": "1f523350-2faf-4f23-bf42-b4462f39fd59",
      "version_no": 2
    }
  ],
  "result": null,
  "failure": null,
  "required_actions": [],
  "retryable": false,
  "poll_after_ms": 2000,
  "created_at": "2026-09-14T08:30:00Z",
  "started_at": "2026-09-14T08:30:02Z",
  "completed_at": null
}
```

| 字段 | 规则 |
| --- | --- |
| `task_type` | `document_parse`、`analysis`、`rewrite`、`interview_opening`、`interview_turn`、`interview_summary`、`pdf_export`、`content_cleanup`、`log_export`、`integrity_scan`、`daily_rollup` |
| `status` | `queued`、`running`、`retry_wait`、`needs_input`、`succeeded`、`failed`、`cancelled` |
| `progress` | 只表示已持久化步骤；无法可靠计算时为 `null`，不使用虚假进度 |
| `result` | 成功时包含 `resource_type`、`resource_id` 和 API `path`；不含业务正文 |
| `failure` | 包含稳定 `code`、可展示 `message`、`retryable` 和 `action` |
| `required_actions` | `needs_input` 时列出补充资料、选择期望等受控动作和产品路径 |
| `poll_after_ms` | 建议轮询间隔；等待时间较长时服务端可逐步增大 |

响应不包含内部执行代次、租约持有者、Celery ID、检查点键、供应商原始错误或内部表 ID。

## 2. 查询任务

### GET `/api/v1/tasks/{id}`

需要 Web 会话。返回本账号 `Task`。任务属于其他账号或正文访问已撤销时返回 404；无正文运行审计不会通过普通任务接口公开。

可选请求头 `If-None-Match` 使用任务 revision ETag；无变化可返回 304。成功响应设置 `ETag` 和私有禁止缓存头，前端仍应按 `poll_after_ms` 控制频率。

不同状态的约定：

- `succeeded`：`result` 必须存在，前端打开业务资源接口。
- `failed`：`failure` 必须存在；只有 `retryable=true` 才显示恢复按钮。
- `needs_input`：不继续调用模型，不占 Worker；`required_actions` 指向补充入口。补充产生新版本时由业务接口创建新任务。
- `retry_wait`：返回 `next_retry_at`，前端只继续轮询。
- `cancelled`：说明来源删除、用户取消或执行被替代，不允许重试已删除来源。

## 3. 重试任务

### POST `/api/v1/tasks/{id}/retry`

需要 CSRF 和 `Idempotency-Key`。请求：

```json
{
  "reason": "user_retry"
}
```

只恢复原任务、原输入引用和原业务目标，不允许提交替换版本。成功返回 202 更新后的 Task；已有运行中执行时也返回 202 原状态。若恢复需要重新预留功能次数，服务端按同一业务任务规则处理，但最终最多成功结算一次。

允许重试：可恢复 `failed`、到期但未自动补投的 `retry_wait`、输入引用完全未变的 `needs_input`。不允许重试：`succeeded`、`cancelled`、来源删除、能力／格式不支持、输入已经替换。新输入必须回到分析、改写、面试等业务接口新建任务。

## 4. 轮询与页面恢复

- 前台页面初始按任务返回的 `poll_after_ms`，架构建议约 2 秒；连续无变化后逐步放慢到 10 秒。
- 页面不可见时暂停高频轮询，最多每 30 秒检查；恢复可见后立即查询一次。
- 429／503 遵循 `Retry-After`；网络错误采用带抖动退避。任何轮询都不使用新的幂等键，也不创建新任务。
- 工作台读取各业务列表提供的进行中任务摘要，不提供“全部任务”菜单。点击继续后回原业务页面，再查询任务详情。

## 5. 错误

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `TASK_NOT_RETRYABLE` | 409 | 当前状态或错误不允许重试 |
| `TASK_INPUT_CHANGED` | 409 | 输入摘要不再匹配，需新建业务任务 |
| `TASK_ALREADY_RUNNING` | 202 | 返回原执行，不新建任务 |
| `TASK_QUEUE_LIMIT_REACHED` | 429 | 账号或全站排队达到上限 |
| `TASK_SOURCE_UNAVAILABLE` | 404 | 来源不可见或已删除 |
| `MODEL_TEMPORARILY_UNAVAILABLE` | 503 | 供应商暂不可用，可按策略恢复 |
| `MODEL_OUTPUT_INVALID` | 503 | 模型结构或引用校验失败，按 failure.retryable 判断 |

内部 `TASK_LEASE_LOST` 不直接作为 HTTP 错误；当前 Worker 停止写入，任务进入可恢复状态。

## 6. 验证清单

覆盖同键创建返回原任务、轮询不创建任务、ETag、Redis 重启、重复 Celery 消息、租约到期、旧代次迟到、供应商结果未知、检查点恢复、来源删除、`needs_input` 新旧版本、免费任务和计次任务的响应差异。
