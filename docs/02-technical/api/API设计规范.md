# Purslyx API 设计规范

| 项 | 内容 |
| --- | --- |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 待评审；用于生成 FastAPI Schema、前端类型和 OpenAPI，不代表接口已实现 |
| API 前缀 | `/api/v1` |
| 技术基线 | FastAPI、Pydantic 2、SQLAlchemy 2、PostgreSQL 18 |
| 上级设计 | [技术架构](../技术架构.md)、[技术模块设计索引](../modules/技术模块设计索引.md) |

## 1. 基本约定

- 路径使用小写复数和短横线；JSON 字段统一 `snake_case`。接口文档中的 `{id}` 都是 `public_id`，格式为 UUID，不暴露数据库自增 ID。
- 请求和响应使用 UTF-8。JSON 请求使用 `application/json`；文件导入使用 `multipart/form-data`；私有文件响应使用实际 MIME 类型。
- 枚举在 API 中使用稳定英文名称，如 `seeker`、`queued`；数据库内部 `SMALLINT` 值不出现在 API。
- 时间点使用 RFC 3339 UTC，例如 `2026-09-14T08:30:00Z`；业务日期使用 `YYYY-MM-DD`，并明确时区。金额、薪资和成本使用十进制字符串，避免 JSON 浮点误差。
- 未知值使用 `null` 并配套状态字段；未知、无限制、面议和数值零不能互相替代。
- 私有 JSON 和文件响应默认返回 `Cache-Control: private, no-store`。公开健康检查另行设计，不属于业务 API。
- 客户端不能提交 `account_id`、内部任务代次、结算状态、得分、成本或后台权限结果；这些值由服务端从会话和业务事实产生。

## 2. JSON 响应

单资源成功响应：

```json
{
  "data": {
    "id": "2db48353-32b6-4bc0-952d-d87c8c0bbfa2",
    "status": "available"
  },
  "meta": {
    "request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P",
    "server_time": "2026-09-14T08:30:00Z"
  }
}
```

列表成功响应：

```json
{
  "data": {
    "items": [],
    "page": {
      "next_cursor": null,
      "has_more": false
    }
  },
  "meta": {
    "request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P",
    "server_time": "2026-09-14T08:30:00Z"
  }
}
```

错误响应：

```json
{
  "error": {
    "code": "DOCUMENT_VERSION_CONFLICT",
    "message": "资料已被更新，请刷新后重新提交。",
    "request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P",
    "retryable": false,
    "action": "reload",
    "fields": [
      {
        "field": "base_revision",
        "code": "stale",
        "message": "当前版本为 4。"
      }
    ]
  }
}
```

`message` 是当前语言下可展示的说明，前端逻辑只依赖 `code`、`retryable` 和 `action`。生产环境不返回堆栈、SQL、供应商原始响应或其他账号信息。

## 3. HTTP 状态码

| 状态 | 用途 |
| --- | --- |
| 200 | 查询、幂等重放或同步更新成功 |
| 201 | 同步创建资源成功 |
| 202 | 异步任务已受理，或重复请求返回原进行中任务 |
| 204 | 退出、撤销或同步删除成功且无需响应体 |
| 303 | “去投递”事件提交成功后跳转到已保存原 JD |
| 400 | 请求无法按协议解析，不用于字段业务校验 |
| 401 | Web／浏览器会话缺失、失效或过期 |
| 403 | 当前身份或后台权限不允许该动作 |
| 404 | 资源不存在或不属于当前账号；两种情况不区分 |
| 409 | 幂等键冲突、revision 冲突、状态冲突或删除影响变化 |
| 410 | 一次性令牌、草稿或导出已经过期且不能恢复 |
| 413 | 文件或请求体超过限制 |
| 415 | Content-Type 或文件媒体类型不支持 |
| 422 | 字段、业务输入、Schema 或引用内容不合法 |
| 429 | 功能次数、请求频率、账号并发或全站预算限制 |
| 503 | 邮件、模型、队列、文件或聚合等依赖暂不可用 |

429 和可重试 503 返回 `Retry-After`；如果无法给出可靠等待时间，省略该头并在 `action` 返回 `retry_later`。

## 4. 认证与请求头

| 场景 | 凭据 | 写请求附加要求 |
| --- | --- | --- |
| Web | `purslyx_session` HttpOnly Cookie | `Origin` 白名单＋`X-CSRF-Token` |
| 篡改猴 | `Authorization: Bearer <browser_token>` | 固定 CORS 来源、scope 与令牌过期校验；不使用 Web Cookie |
| 公开邮件令牌 | JSON 请求体中的一次性原始令牌 | 限频；服务端只存摘要 |
| 管理端 | Web 会话＋实时后台权限 | 同 Web CSRF；每次请求重新检查权限 |

服务端为每个响应生成 `X-Request-ID`，同时写入 `meta.request_id` 或错误体。客户端可传符合格式的 `X-Request-ID` 作为关联建议，但服务端仍负责去重和安全过滤。

需要幂等的接口使用 `Idempotency-Key: <UUID>`：计次操作、任务重试、面试回答、浏览器草稿上传、后台状态／角色／次数变更、反馈提交和日志导出。服务端按认证主体＋动作＋键保存请求摘要；同键同请求返回原状态，同键不同请求返回 409。

## 5. 分页、筛选与排序

- 列表使用不透明游标：`cursor` 可选，`limit` 默认 20、最小 1、最大 100。客户端不能解析或修改游标。
- 默认排序为 `created_at DESC, id DESC`。接口只开放文档列出的排序键；不接受任意列名或 SQL 表达式。
- 时间点范围使用半开区间 `created_from <= t < created_to`；业务日期使用包含首尾的 `date_from`、`date_to`。
- 文本搜索先限制长度并规范化；后台用户邮箱搜索和日志关联 ID 搜索不得返回超出当前权限的数据。
- 任何分页响应都以本次查询条件生成游标。筛选变化后必须从第一页重新查询。

## 6. revision 与版本

可编辑父资源返回 `revision`。更新请求必须携带 `base_revision`；服务端执行条件更新并将 revision 加一。失败返回 409 和当前 revision，不自动覆盖。

确认后的资料、期望、报告输入、岗位版和面试输入使用不可变版本 `version_id`。修改会创建新版本；历史任务仍引用旧版本。`schema_version`、`rule_version`、`prompt_version`、`template_version` 等随结果返回，前端不得假定不同版本字段完全相同。

## 7. 异步任务

异步受理统一返回：

```json
{
  "data": {
    "task": {
      "id": "af52b918-a90f-49fd-b3fa-c80923fb3d97",
      "task_type": "analysis",
      "status": "queued",
      "current_step": null,
      "poll_after_ms": 2000
    },
    "usage_reservation": {
      "feature": "analysis",
      "reserved_count": 1,
      "remaining_after_reservation": 19
    },
    "input_versions": []
  },
  "meta": {
    "request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P",
    "server_time": "2026-09-14T08:30:00Z"
  }
}
```

免费异步任务的 `usage_reservation` 为 `null`。前端按 `poll_after_ms` 查询原 `task_id`，页面隐藏或后台时降低频率；网络恢复后仍查询原任务。任务状态固定为 `queued`、`running`、`retry_wait`、`needs_input`、`succeeded`、`failed`、`cancelled`。

## 8. 删除协议

适用资源：`documents`、`resumes`、`analyses`、`rewrites`、`interviews`、`facts` 和 `job-pool/items`。

1. `GET /api/v1/{resource}/{id}/deletion-impact` 返回受影响资源类型、数量、正在运行任务数量和不透明的 `impact_version`，同时通过 `ETag` 返回同一版本。
2. 客户端展示影响后调用 `DELETE`，携带 `If-Match: "<impact_version>"`。
3. 影响集合未变化时返回 204，并立即阻断顶层与后代访问；物理正文、文件和检查点异步清理。
4. 影响集合变化时返回 409 `RESOURCE_DELETION_CHANGED`，客户端重新读取影响。

删除接口不提供账号注销。删除不重置使用次数，也不抹除无正文的必要成本和审计事实。

## 9. Schema 与文档生成

- Pydantic Schema 是字段级接口实现来源，FastAPI 生成 OpenAPI 3.1；前端从已提交的 OpenAPI 生成 TypeScript 类型。
- 公共 Schema 只定义一次：`ApiMeta`、`ApiError`、`PageInfo`、`TaskSummary`、`UsageReservationSummary`、`VersionRef`、`DeletionImpact`。
- 请求 Schema 默认拒绝未知字段，更新接口不使用可任意扩展的字典。JSONB 业务内容同样绑定独立版本化 JSON Schema。
- OpenAPI 中每个操作必须有稳定 `operationId`、认证要求、成功响应、业务错误和至少一个脱敏示例。
- CI 检查 OpenAPI 变更；删除字段、改变含义、缩窄枚举或修改状态码属于破坏性变更，需要升级 API 版本或提供兼容期。

## 10. 安全与可观测性

- 所有资源查询同时限定认证账号、用途和未删除状态；UUID 不能替代授权。管理端菜单隐藏不能替代后台权限检查。
- 文件下载、报告、面试、导出和日志响应禁止公共缓存；浏览器草稿令牌不能出现在 URL、DOM、控制台或日志。
- URL 跳转只使用服务端保存且重新验证的 HTTPS 白名单地址；不接受客户端传入目标 URL。
- 请求体、响应体和模型输入默认不写应用日志。日志只记录 operationId、主体、状态码、业务码、耗时、大小和关联 ID。
- 限制 JSON 深度、字符串长度、列表元素数量、文件大小和分页范围；具体上限写在模块 Schema。

## 11. 实现检查

每个接口实现前检查：路径与方法唯一、认证类型明确、身份／权限明确、请求与响应 Schema 已版本化、幂等和 revision 规则明确、资源归属校验存在、业务错误可恢复、敏感字段未进入日志、异步结果有可轮询任务、删除后旧入口不可读。
