# Purslyx 统计 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 统计 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 待评审；字段级契约，接口尚未实现 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [统计模块设计](../modules/统计模块设计.md) |

## 1. 个人统计

### GET `/api/v1/stats/me`

需要 Web 会话。可选 `date`，默认为 `Asia/Shanghai` 的今天，只允许查询本人已有数据。响应按固定注册身份返回不同指标：

```json
{
  "data": {
    "date": "2026-09-14",
    "timezone": "Asia/Shanghai",
    "registration_role": "seeker",
    "metrics": [
      {
        "key": "go_to_apply_clicks",
        "label": "去投递点击数",
        "value": 3,
        "definition": "Purslyx 成功记录并发起原岗位跳转的次数。"
      }
    ],
    "calculated_at": "2026-09-14T08:30:00Z"
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

求职身份返回 `go_to_apply_clicks`；招聘身份返回 `candidate_analyses_completed`。两者都从服务端业务事实实时读取。字段和界面不得改名为已投递数、平台回复数或录用数。

## 2. 产品反馈

### POST `/api/v1/feedback`

需要 Web 会话、CSRF 和 `Idempotency-Key`。

```json
{
  "feedback_type": "suggestion",
  "context": {
    "type": "analysis",
    "resource_id": "c4676e87-7d1b-4c5d-a0ef-fda62723298f"
  },
  "rating": 4,
  "payment_intent": "depends_on_price",
  "content": "希望报告中的条件冲突可以更快定位。"
}
```

| 字段 | 规则 |
| --- | --- |
| `feedback_type` | `issue`、`suggestion`、`payment_intent`、`other` |
| `context.type` | `general`、`analysis`、`rewrite`、`interview`、`export` |
| `context.resource_id` | `general` 时必须为空，其他类型必填且属于当前账号 |
| `rating` | 可空整数 1—5 |
| `payment_intent` | `willing`、`depends_on_price`、`unwilling`、`not_answered` |
| `content` | 可空；有值时 1—5000 字符；问题和建议类型必填 |

成功返回 201 `Feedback`，状态 `new`。产品付费意愿只取该明确字段，不能由 rating、点击或使用行为推断。

## 3. 管理站点概况

### GET `/api/v1/admin/metrics`

需要 `admin.stats.read`。查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `date_from` | date | 近 7 天 | 包含 |
| `date_to` | date | 今天 | 包含，最长 93 天 |
| `registration_role` | enum | `all` | `all`、`seeker`、`recruiter` |
| `feature` | enum | `all` | `all`、`analysis`、`rewrite`、`interview`、`import` |
| `granularity` | enum | `day` | 首版只允许 `day` |

返回：

```json
{
  "data": {
    "timezone": "Asia/Shanghai",
    "date_from": "2026-09-08",
    "date_to": "2026-09-14",
    "registration_role": "all",
    "feature": "all",
    "rule_version": "daily-metrics-v1",
    "source_watermark_at": "2026-09-14T08:25:00Z",
    "totals": {
      "registered_accounts": 100,
      "active_accounts": 47,
      "tasks_succeeded": 120,
      "tasks_failed": 9,
      "task_retries": 12,
      "usage_settled": 86,
      "go_to_apply_clicks": 35,
      "rewrite_adopted_segments": 42,
      "resume_exports": 18,
      "interviews_completed": 7,
      "interviews_ended_early": 2,
      "feedback_submitted": 11,
      "payment_intent_positive": 3
    },
    "series": []
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

`series` 每项包含日期、同一组计数、平均耗时、P95 和样本数。数据尚未聚合到请求结束时间时仍返回可用数据，并明确水位；完全无可用聚合返回 503 `STATS_NOT_READY`。

### GET `/api/v1/admin/costs`

需要 `admin.stats.read`，日期、身份和功能筛选同概况。返回已知成本、未知成本条数、模型调用数、成功结果数和每个成功结果的已知成本：

```json
{
  "known_cost": "12.48390000",
  "currency": "USD",
  "unknown_cost_count": 2,
  "model_call_count": 146,
  "successful_result_count": 86,
  "known_cost_per_success": "0.14516163",
  "pricing_watermark_at": "2026-09-14T08:25:00Z"
}
```

成功结果为 0 时 `known_cost_per_success=null`。未知成本不加入已知均值，也不显示为零。

## 4. 管理反馈

### GET `/api/v1/admin/feedback`

需要 `admin.stats.read`。查询 `feedback_type`、`status`、`registration_role`、`payment_intent`、时间和分页。列表返回反馈 ID、账号脱敏摘要、类型、评分、付费意愿、内容预览、上下文类型和时间。

### GET `/api/v1/admin/feedback/{id}`

需要 `admin.stats.read`。返回完整反馈正文、可见上下文摘要、状态、查看人和时间；不返回上下文业务正文。每次详情读取再次鉴权。

### PUT `/api/v1/admin/feedback/{id}`

需要 `admin.stats.read`、CSRF 和幂等键。

```json
{
  "status": "reviewed",
  "base_revision": 1,
  "reason": "已纳入首版体验问题清单。"
}
```

状态允许 `new → reviewed → closed`，也允许已查看反馈直接关闭；不能重新打开已关闭反馈。成功返回 200，并同事务写管理审计。

## 5. 错误与边界

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `STATS_RANGE_INVALID` | 422 | 日期倒置、超过 93 天或筛选组合不合法 |
| `STATS_NOT_READY` | 503 | 没有完整可用聚合 |
| `STATS_PERMISSION_DENIED` | 403 | 缺少统计权限 |
| `FEEDBACK_CONTEXT_NOT_FOUND` | 404 | 上下文资源不存在、不属于当前账号或已删除 |
| `FEEDBACK_CONTEXT_INVALID` | 422 | 上下文类型、资源组合或字段不合法 |
| `FEEDBACK_CONTENT_INVALID` | 422 | 正文、评分或付费意愿不合法 |
| `FEEDBACK_STATUS_CONFLICT` | 409 | revision 或状态流转冲突 |

首版不提供通用 `/events`，也不接受客户端提交成功、采用、导出、跳转或付费事实。所有管理响应禁止公共缓存。

## 6. 验证清单

覆盖上海日界线、点击去重、招聘报告只计可用状态、个人即时数据、管理水位、迟到数据补算、P95 原样本重算、未知成本、零成功分母、反馈上下文越权、付费意愿不推断和反馈状态审计。
