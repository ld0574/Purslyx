# Purslyx 日志 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 日志 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-15 |
| 状态 | 已实现并通过权限、导出、并发与完整性验收 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [日志模块设计](../modules/日志模块设计.md) |

## 1. 公共筛选与日志摘要

三类列表共同支持：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `created_from` | datetime | 近 24 小时 | 包含 |
| `created_to` | datetime | 当前时间 | 不包含，最长 31 天 |
| `account_id` | UUID | 无 | 已知账号过滤；仍受权限限制 |
| `result` | enum | 无 | `succeeded`、`denied`、`failed` |
| `request_id` | string | 无 | 精确关联 ID |
| `cursor`、`limit` | 分页 | 20 | 最大 100 |

`LogSummary` 只包含日志 ID、类别、时间、脱敏账号摘要、动作／事件／任务类型、结果、稳定原因码、request_id 和允许的关联 ID。不返回请求体、响应体、堆栈、简历、JD、回答、反馈、密码、Cookie、令牌或密钥。

### GET `/api/v1/admin/logs/{type}/{id}`

`type` 只允许 `operations`、`security`、`tasks`，并分别检查对应的分类权限。详情请求每次重新鉴权；响应结构按下列三类定义，不存在、越权或分类与 ID 不匹配统一返回 404。

## 2. 管理操作日志

### GET `/api/v1/admin/logs/operations`

需要 `admin.logs.operations.read`。额外筛选 `action`、`target_type`、`target_id`、`operator_id`。返回角色、权限、账号状态、次数、反馈和日志导出等管理操作摘要。

### 管理操作详情

使用通用详情路径并令 `type=operations`。返回：

```json
{
  "id": "6a82070f-f5bd-4c4a-918f-52edfc08216a",
  "category": "operations",
  "operator": {"id": "...", "email_masked": "a***@example.test"},
  "action": "usage.grant",
  "target": {"type": "usage_grant", "id": "..."},
  "result": "succeeded",
  "reason": "完成内测访谈，补充体验次数。",
  "before": {"available": 15},
  "after": {"available": 25},
  "request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P",
  "created_at": "2026-09-14T08:30:00Z"
}
```

`before`、`after` 只接受 `admin-audit-values-v1` 白名单字段。

## 3. 登录安全日志

### GET `/api/v1/admin/logs/security`

需要 `admin.logs.security.read`。额外筛选 `event_type`、`reason_code` 和可选 `session_id`。事件类型：`login`、`rate_limited`、`account_locked`、`password_reset`、`account_recovery_requested`、`account_recovered`、`web_session_issued`、`web_session_revoked`、`browser_session_issued`、`browser_session_revoked`、`email_verified`。

### 登录安全详情

使用通用详情路径并令 `type=security`。返回事件类型、结果、已知账号摘要、会话公开标识、稳定原因码、降精度网络前缀、客户端类别、request_id 和时间。不返回邮箱是否存在的内部判断、完整 IP、User-Agent 原文、会话摘要或认证令牌。

## 4. 任务运行日志

### GET `/api/v1/admin/logs/tasks`

需要 `admin.logs.tasks.read`。额外筛选 `task_type`、`task_status`、`retry_count_min`、业务资源类型与 ID。数据从任务模块受限投影读取。

### 任务运行详情

使用通用详情路径并令 `type=tasks`。返回任务类型、状态、所属账号脱敏摘要、创建／开始／完成时间、排队与执行耗时、尝试数量、每次尝试结果、模型调用关联 ID、已知／未知成本状态和业务结果引用。模型调用只返回供应商、模型、用量、耗时、价格版本和稳定错误，不返回提示词或正文。

## 5. 日志导出

### POST `/api/v1/admin/log-exports`

需要 `admin.logs.export`、目标类别查看权限、CSRF 和 `Idempotency-Key`。

```json
{
  "log_category": "task_runs",
  "export_format": "csv",
  "filter": {
    "created_from": "2026-09-13T00:00:00Z",
    "created_to": "2026-09-14T00:00:00Z",
    "result": "failed"
  }
}
```

`log_category` 为 `admin_operations`、`security`、`task_runs`；格式为 `csv` 或 `jsonl`。范围最长 31 天，预计超过 100,000 行时返回 422 并要求缩小范围。成功返回 202 `task_type=log_export` 和导出摘要。

### GET `/api/v1/admin/log-exports/{id}`

需要申请人本人或仍有相应类别及导出权限。返回 `queued`、`exporting`、`downloadable`、`failed`、`expired` 状态，原筛选摘要、格式、行数、任务、到期时间和失败码。

### GET `/api/v1/admin/log-exports/{id}/file`

状态必须为 `downloadable` 且未过期。返回私有文件流；CSV 为 UTF-8 with BOM 并对 `= + - @` 开头单元格做公式注入转义，JSONL 每行包含 Schema 版本。首次下载时间和每次下载操作写审计。过期返回 410。

## 6. 权限与错误

| 类别 | 查看权限 | 导出附加权限 |
| --- | --- | --- |
| 管理操作 | `admin.logs.operations.read` | `admin.logs.export` |
| 登录安全 | `admin.logs.security.read` | `admin.logs.export` |
| 任务运行 | `admin.logs.tasks.read` | `admin.logs.export` |

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `LOG_PERMISSION_DENIED` | 403 | 缺少类别查看或导出权限 |
| `LOG_TYPE_INVALID` | 422 | 日志类别不在白名单 |
| `LOG_RANGE_INVALID` | 422 | 时间倒置、超过 31 天或预计行数超限 |
| `LOG_EXPORT_NOT_READY` | 409 | 导出尚未可下载 |
| `LOG_EXPORT_EXPIRED` | 410 | 文件已过期并禁止访问 |
| `LOG_EXPORT_FAILED` | 503 | 导出失败，可恢复原任务 |
| `STORAGE_CAPACITY_LIMIT` | 503 | 本机存储达到保护阈值，暂停新日志导出 |

完整性巡检没有普通管理 API；通过定时任务和受控维护命令运行。发现异常只告警，不由日志接口修复。

## 7. 验证清单

覆盖三类权限互不继承、详情再次鉴权、筛选分页、账号不存在不泄露、白名单前后值、完整 IP 与凭据脱敏、任务正文不出现、导出幂等、范围和行数限制、CSV 公式注入、过期下载、下载审计和资料删除后只保留无正文运行事实。
