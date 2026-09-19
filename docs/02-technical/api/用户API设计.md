# Purslyx 用户 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 用户 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 已实现并通过 201 PostgreSQL 验收 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [用户模块设计](../modules/用户模块设计.md) |

## 1. 公共 Schema

### Account

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 否 | 账号 `public_id` |
| `email` | string | 否 | 当前账号邮箱；只向本人或有权管理员返回 |
| `registration_role` | enum | 否 | `seeker`、`recruiter`；创建后固定 |
| `status` | enum | 否 | `pending_verification`、`active`、`suspended` |
| `email_verified` | boolean | 否 | 是否已完成邮箱验证 |
| `admin_permissions` | string[] | 否 | 当前有效后台权限键；普通账号为空数组 |
| `created_at` | datetime | 否 | 注册时间 |
| `last_login_at` | datetime | 是 | 从未成功登录时为 `null` |
| `revision` | integer | 否 | 账号状态并发版本，最小 1 |

### AuthSession

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `account` | Account | 否 | 当前账号 |
| `csrf_token` | string | 否 | Web 写请求使用；不得写日志 |
| `session_expires_at` | datetime | 否 | 当前 Web 会话到期时间 |

密码长度为 8—128 个 Unicode 字符；服务端不静默截断。邮箱长度不超过 254，提交时去首尾空白并按服务端规则规范化。默认注册不要求邮箱所有权验证；`PURSLYX_REQUIRE_EMAIL_VERIFICATION=true` 时恢复邮件验证流程。响应不返回密码规则判断细节以外的认证内部信息。

## 2. 注册、验证与登录

### GET `/api/v1/auth/captcha`

公开接口，限频。返回注册使用的短期无状态算术验证码：

```json
{
  "data": {
    "captcha_id": "<签名后的验证码标识>",
    "question": "7 + 4 = ?",
    "expires_in": 300
  }
}
```

答案只在服务端用 `PURSLYX_TOKEN_SECRET` 校验，不写入数据库；验证码标识不可篡改，默认 5 分钟有效。

### POST `/api/v1/auth/register`

公开接口，限频并需要简单算术验证码。

请求：

```json
{
  "email": "seeker@example.test",
  "password": "correct horse battery staple",
  "registration_role": "seeker",
  "captcha_id": "<captcha_id>",
  "captcha_answer": "11"
}
```

默认关闭邮箱认证时，新账号成功返回 202，并直接进入工作台：

```json
{
  "data": {
    "status": "registered",
    "registration_ready": true,
    "message": "注册成功，正在进入工作台。"
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

`registration_role` 必须为 `seeker` 或 `recruiter`。已有账号不得借重复注册改变身份。

当 `PURSLYX_REQUIRE_EMAIL_VERIFICATION=true` 时，新账号回到待验证状态并发送验证邮件；已有邮箱仍返回中性受理结果，不返回账号 ID。

### POST `/api/v1/auth/resend-verification`

请求 `{ "email": "seeker@example.test" }`，成功统一返回 202 `verification_requested`。旧的未消费验证令牌撤销；限频不泄露账号是否存在。

### POST `/api/v1/auth/verify-email`

请求 `{ "token": "<一次性邮件令牌>" }`。成功返回 200，`data` 包含 `account` 和本次试用发放后的 `usage` 摘要；同一令牌已成功消费时返回当前完成状态，不重复发放。无效、过期或已被替换返回 410 `AUTH_TOKEN_INVALID_OR_EXPIRED`。

### POST `/api/v1/auth/login`

请求：

```json
{
  "email": "seeker@example.test",
  "password": "correct horse battery staple"
}
```

成功返回 200 `AuthSession`，同时设置 `purslyx_session` Cookie。凭据错误返回 401 `AUTH_INVALID_CREDENTIALS`；开启邮箱认证且未验证时返回 403 `AUTH_EMAIL_UNVERIFIED`；暂停返回 403 `AUTH_ACCOUNT_SUSPENDED`。响应不会区分邮箱不存在与密码错误。

### POST `/api/v1/auth/logout`

需要 Web 会话、Origin 和 CSRF。成功撤销当前会话、清除 Cookie，返回 204；重复退出也返回 204。

### POST `/api/v1/auth/forgot-password`

请求 `{ "email": "seeker@example.test" }`，成功统一返回 202 `reset_requested`，不泄露账号是否存在。

### POST `/api/v1/auth/reset-password`

请求：

```json
{
  "token": "<一次性重置令牌>",
  "new_password": "another correct horse battery staple"
}
```

成功更新密码并撤销该账号全部有效 Web 会话，返回 204。令牌错误或过期返回 410；新密码不合规则返回 422。

### POST `/api/v1/auth/request-account-recovery`

请求 `{ "email": "seeker@example.test" }`，成功统一返回 202 `recovery_requested`。只有暂停账号会收到恢复邮件；重复申请撤销同类旧令牌并受邮件限频保护，响应不泄露账号是否存在或当前状态。

### POST `/api/v1/auth/recover-account`

请求 `{ "token": "<一次性账号恢复令牌>" }`。有效令牌将暂停账号恢复为 `active`、递增 revision 并返回 204；不创建登录会话，用户仍须正常登录。令牌已成功消费时返回 204，无效、过期或已被替换返回 410 `AUTH_TOKEN_INVALID_OR_EXPIRED`。

默认邮箱验证链接有效 24 小时，密码重置和账号恢复链接有效 30 分钟，Web 会话有效 7 天。默认值由服务端配置管理，只影响新签发记录。

### GET `/api/v1/me`

需要 Web 会话。成功返回 200 `Account`，用于恢复固定身份工作台和管理端菜单。会话无效返回 401。

## 3. 浏览器会话

### POST `/api/v1/auth/browser-codes`

需要 Web 会话、Origin、CSRF 和求职身份。请求：

```json
{
  "origin": "https://purslyx.example",
  "nonce": "s7X...base64url...dQ"
}
```

成功返回 201：

```json
{
  "data": {
    "authorization_code": "<只返回一次>",
    "nonce": "s7X...base64url...dQ",
    "expires_at": "2026-09-14T08:31:00Z"
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

`origin` 必须是配置中的 Purslyx 产品来源；`nonce` 为 32—128 字符 base64url 随机值。

### POST `/api/v1/browser-auth/exchange`

公开 CORS 端点，只接受允许的脚本来源和一次性授权码。请求包含 `authorization_code`、`origin` 和原 `nonce`。成功返回 201：

```json
{
  "data": {
    "browser_token": "<只返回一次>",
    "token_type": "Bearer",
    "scope": ["job_drafts.create", "job_drafts.read_own", "browser_session.revoke_self"],
    "scope_version": 1,
    "expires_at": "2026-09-14T12:30:00Z"
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

授权码消费后不可重放。浏览器令牌不得进入 URL、DOM、控制台或普通日志。

### DELETE `/api/v1/browser-auth/session`

需要 Browser Bearer。撤销当前浏览器会话并返回 204；重复撤销返回 204。该令牌之后访问草稿接口返回 401 `AUTH_SESSION_EXPIRED`。

## 4. 管理用户

### GET `/api/v1/admin/users`

需要 `admin.users.read`。查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `q` | string | 无 | 邮箱前缀／完整邮箱，最多 120 字符 |
| `registration_role` | enum | 无 | `seeker`、`recruiter` |
| `status` | enum | 无 | 三种账号状态 |
| `cursor` | string | 无 | 不透明游标 |
| `limit` | integer | 20 | 1—100 |

列表项包含 Account 的非权限字段、有效后台角色名、适用功能余额摘要和最近业务活动时间；不含简历、JD、报告或反馈正文。

### GET `/api/v1/admin/users/{id}`

需要 `admin.users.read`。返回账号概要、有效后台角色、用量余额及按类型汇总的资料／任务数量。跨模块概要是受限投影，不返回任何正文。找不到或资源不可见返回 404。

### PUT `/api/v1/admin/users/{id}/status`

需要 `admin.users.manage_status`、CSRF 和 `Idempotency-Key`。

```json
{
  "status": "suspended",
  "base_revision": 3,
  "reason": "内测账号异常请求，人工暂停。"
}
```

只允许 `active ↔ suspended`；不得用此接口完成验证、注销或改变注册身份。暂停成功撤销有效 Web／浏览器会话并发送一次性恢复邮件；邮件失败不回滚暂停结果，用户可从暂停提示页重新申请。返回 200 更新后的 Account；revision 冲突、自改限制或非法状态返回 409／422。

## 5. 管理角色与权限

### GET `/api/v1/admin/roles`

需要 `admin.roles.manage`。返回角色列表及权限目录：

```json
{
  "data": {
    "items": [
      {
        "id": "c186cb8a-58b5-4f46-9898-13dbc599546e",
        "name": "运营管理员",
        "description": "查看用户、统计和反馈",
        "is_builtin": false,
        "status": "active",
        "permission_keys": ["admin.users.read", "admin.stats.read"],
        "member_count": 2,
        "revision": 1
      }
    ],
    "permission_catalog": []
  },
  "meta": {"request_id": "01K5A2MZ6B2JQJ7G2XAJ5PSM8P", "server_time": "2026-09-14T08:30:00Z"}
}
```

权限目录至少包含技术架构确定的九个 `admin.*` 键，并返回显示名和说明。

### POST `/api/v1/admin/roles`

需要 `admin.roles.manage`、CSRF 和幂等键。请求包含 `name`（1—80）、可选 `description`（最多 300）、非空 `permission_keys` 和必填 `reason`。不得创建内置角色或授予操作者不拥有的权限。成功返回 201 新角色。

### PUT `/api/v1/admin/roles/{id}`

请求包含 `name`、`description`、`permission_keys`、`status`、`base_revision` 和 `reason` 的完整目标状态。成功返回 200；内置超级管理员、越权权限或 revision 冲突返回 409 `ADMIN_ROLE_CONFLICT`。

### PUT `/api/v1/admin/users/{id}/roles`

需要 `admin.roles.manage`、CSRF 和幂等键。

```json
{
  "role_ids": ["c186cb8a-58b5-4f46-9898-13dbc599546e"],
  "base_revision": 3,
  "reason": "负责本轮内测反馈。"
}
```

这是目标账号有效角色的完整替换。操作者不能修改自己、不能授予超过自身的权限，不能移除最后一位超级管理员。返回更新后的角色列表和 revision。

## 6. 错误与限频

| 业务码 | HTTP | 适用接口 |
| --- | --- | --- |
| `AUTH_INVALID_CREDENTIALS` | 401 | 登录 |
| `AUTH_EMAIL_UNVERIFIED` | 403 | 登录和需要验证邮箱的功能 |
| `AUTH_ACCOUNT_SUSPENDED` | 403 | 所有私有接口 |
| `AUTH_TOKEN_INVALID_OR_EXPIRED` | 410 | 验证、重置和账号恢复 |
| `AUTH_SESSION_EXPIRED` | 401 | Web／浏览器会话 |
| `AUTH_BROWSER_SCOPE_FORBIDDEN` | 403 | 浏览器端点 |
| `AUTH_RATE_LIMITED` | 429 | 注册、登录、邮件请求和换取令牌 |
| `ADMIN_PERMISSION_DENIED` | 403 | 管理端 |
| `ADMIN_ROLE_CONFLICT` | 409 | 角色和账号角色变更 |

注册、登录、邮件和浏览器授权的具体限频数值由部署配置提供，响应使用统一 429 和 `Retry-After`。浏览器授权码默认 1 分钟、浏览器会话默认 4 小时；客户端不得根据 202 中性响应判断账号是否存在。

## 7. 验证清单

接口测试覆盖固定注册身份、重复邮箱中性响应、验证并发消费、试用只发一次、登录会话轮换、重置撤销旧会话、暂停邮件恢复及令牌并发消费、浏览器授权码重放、scope 越权、暂停即时失效、管理员自改、权限上限和最后超级管理员保护。OpenAPI 示例只能使用 `.test` 邮箱和虚构账号。
