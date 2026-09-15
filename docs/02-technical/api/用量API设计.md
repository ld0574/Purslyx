# Purslyx 用量 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 用量 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 已实现并通过 201 PostgreSQL 验收 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [用量模块设计](../modules/用量模块设计.md) |

## 1. Schema

### UsageBalance

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `feature` | enum | `analysis`、`rewrite`、`interview` |
| `unit` | enum | `times`、`sessions` |
| `granted_total` | integer | 累计发放 |
| `reserved_total` | integer | 运行中预留 |
| `settled_total` | integer | 已成功结算 |
| `available` | integer | 发放减预留减结算 |
| `updated_at` | datetime | 余额最后变更时间 |

求职账号返回三类，招聘账号只返回 `analysis`。不适用功能不返回零余额占位。

### UsageEntry

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `feature` | enum | 适用功能 |
| `entry_type` | enum | `grant`、`reserve`、`settle`、`release` |
| `count` | integer | 正整数；方向由类型表达 |
| `source_type` | enum | `trial`、`admin_grant`、`task` |
| `source_id` | UUID | 可见来源标识；不可见时为 `null` |
| `reason` | string | 用户可见的稳定原因文案 |
| `created_at` | datetime | 流水时间 |

## 2. 我的用量

### GET `/api/v1/usage`

需要已验证 Web 会话。查询参数 `entries_cursor`、`entries_limit`（默认 20、最大 100）和可选 `feature`。返回：

```json
{
  "data": {
    "registration_role": "seeker",
    "balances": [
      {
        "feature": "analysis",
        "unit": "times",
        "granted_total": 20,
        "reserved_total": 1,
        "settled_total": 4,
        "available": 15,
        "updated_at": "2026-09-14T08:30:00Z"
      }
    ],
    "entries": [],
    "page": {"next_cursor": null, "has_more": false}
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

响应不返回全站美元预算、模型价格、其他账号余额或管理员信息。页面发起计次操作前应重新读取或使用业务接口返回的预留结果；前端显示值不能作为扣次依据。

## 3. 管理端发放

### GET `/api/v1/admin/usage-grants`

需要 `admin.usage.grant`。查询参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `account_id` | UUID | 目标账号 |
| `operator_id` | UUID | 操作者账号 |
| `feature` | enum | 功能 |
| `created_from`、`created_to` | datetime | 半开时间范围 |
| `cursor`、`limit` | 分页 | 通用规则 |

列表项包含发放 ID、目标账号脱敏摘要、固定注册身份、功能、数量、原因、操作者、发放前后可用次数和时间。不返回业务资料。

### POST `/api/v1/admin/users/{id}/usage-grants`

需要 `admin.usage.grant`、CSRF 和 `Idempotency-Key`。

```json
{
  "feature": "analysis",
  "count": 10,
  "reason": "完成内测访谈，补充体验次数。"
}
```

`count` 为 1—10,000；`reason` 去首尾空白后 5—500 字符。目标求职账号允许三类功能，招聘账号只允许分析。成功返回 201：

```json
{
  "data": {
    "grant": {
      "id": "0b292156-afb8-45b6-bdab-25fb97c4a970",
      "feature": "analysis",
      "count": 10,
      "reason": "完成内测访谈，补充体验次数。",
      "before_available": 15,
      "after_available": 25,
      "created_at": "2026-09-14T08:30:00Z"
    },
    "balance": {}
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

同键同请求返回 200 原发放；同键不同数量、功能、目标或原因返回 409。接口只追加正数，不提供覆盖余额或负数更正。

## 4. 计次确认响应

分析、改写和面试接口成功受理时，公共异步响应中的 `usage_reservation` 必须包含功能、预留数和预留后的剩余数。使用次数不足返回 429，不创建模型调用；已有同幂等任务返回原预留。

任务最终结果：

- 完整成功：流水新增 `settle`，`reserved_total` 减少，`settled_total` 增加。
- 未交付失败、资料不足或来源删除：流水新增 `release`，可用次数恢复。
- 面试 3 个开场问题已交付：结算一场，后续回答与汇总不再返回新预留。
- 网络响应丢失：查询原任务和用量，不再次发起新计次请求。

## 5. 错误

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `USAGE_FEATURE_NOT_ALLOWED` | 403 | 固定身份不适用该功能 |
| `USAGE_INSUFFICIENT` | 429 | 剩余次数不足，输入仍保留 |
| `USAGE_ALREADY_RESERVED` | 202 | 返回原任务和预留 |
| `USAGE_IDEMPOTENCY_CONFLICT` | 409 | 同键请求摘要不同 |
| `USAGE_GRANT_INVALID` | 422 | 数量、原因或功能不合法 |
| `BUDGET_LIMIT_REACHED` | 429 | 全站当日预算不足 |
| `BUDGET_CONCURRENCY_LIMIT` | 429 | 模型并发槽已满 |
| `MODEL_PRICE_UNKNOWN` | 503 | 缺少可靠价格上界，禁止调用 |

全站预算错误只返回用户可执行动作，不公开预算金额、其他账号使用或模型内部配置。

## 6. 验证清单

覆盖求职／招聘功能集合、首次发放、并发预留最后一次、结算与释放幂等、面试开场后不重复计次、管理员重复发放、目标身份不适用、自提权、正数和原因限制、预算与功能次数错误分离。
