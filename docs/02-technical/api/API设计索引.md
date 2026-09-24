# Purslyx API 设计索引

| 项 | 内容 |
| --- | --- |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 已实现；与当前 FastAPI 路由和 OpenAPI 一起维护 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [技术模块设计索引](../modules/技术模块设计索引.md) |

## 文档入口

| 模块 | API 文档 | 主要路径 |
| --- | --- | --- |
| 用户 | [用户 API 设计](用户API设计.md) | `/auth/*`、`/me`、`/browser-auth/*`、`/admin/users*`、`/admin/roles*` |
| 简历 | [简历 API 设计](简历API设计.md) | `/documents*`、`/preferences*`、`/facts*`、`/rewrites*`、`/resumes*`、`/exports*` |
| 匹配池 | [匹配池 API 设计](匹配池API设计.md) | `/browser/job-drafts*`、`/job-pool/items*`、`/admin/job-pool/items*`、`/analyses*` |
| 面试 | [面试 API 设计](面试API设计.md) | `/interviews*` |
| 任务 | [任务 API 设计](任务API设计.md) | `/tasks*` |
| 用量 | [用量 API 设计](用量API设计.md) | `/usage`、`/admin/usage-grants*` |
| 统计 | [统计 API 设计](统计API设计.md) | `/dashboard`、`/stats/me`、`/feedback`、`/admin/metrics`、`/admin/costs`、`/admin/feedback*` |
| 日志 | [日志 API 设计](日志API设计.md) | `/admin/logs*`、`/admin/log-exports*` |

## 调用入口

```mermaid
flowchart LR
    Web[Vue Web] -->|Web Cookie + CSRF| API[/api/v1]
    Script[篡改猴] -->|Browser Bearer| Browser[/browser/*]
    Admin[管理页面] -->|Web Cookie + Admin Permission| AdminAPI[/admin/*]
    API --> Async[202 Task]
    Async --> Poll[GET /tasks/id]
    API --> File[私有文件流]
    API --> Redirect[303 原 JD]
```

## 路由总表

| 模块 | 方法 | 路径 | 认证 | 结果 |
| --- | --- | --- | --- | --- |
| 用户 | GET/POST | `/auth/captcha`、`/auth/register`、`/auth/login`、`/auth/verify-email`、`/auth/resend-verification`、`/auth/forgot-password`、`/auth/reset-password`、`/auth/request-account-recovery`、`/auth/recover-account` | 公开 | 注册验证码；其余同步或中性受理 |
| 用户 | POST | `/auth/logout`、`/auth/browser-codes` | Web | 退出或一次性浏览器授权码 |
| 用户 | GET | `/me` | Web | 当前账号、固定身份与后台权限 |
| 用户 | POST | `/browser-auth/exchange` | 一次性授权码 | 浏览器令牌 |
| 用户 | DELETE | `/browser-auth/session` | 浏览器会话 | 撤销自身浏览器会话 |
| 用户 | GET | `/admin/users`、`/admin/users/{id}` | `admin.users.read` | 用户概要 |
| 用户 | PUT | `/admin/users/{id}/status` | `admin.users.manage_status` | 账号暂停或恢复 |
| 用户 | PUT | `/admin/users/{id}/roles` | `admin.roles.manage` | 完整替换账号后台角色 |
| 用户 | GET／POST | `/admin/roles` | `admin.roles.manage` | 角色列表或创建角色 |
| 用户 | PUT | `/admin/roles/{id}` | `admin.roles.manage` | 更新角色与权限组合 |
| 简历 | POST | `/documents`、`/documents/{id}/parse`、`/documents/{id}/versions` | Web | 创建资料、解析任务或确认版本 |
| 简历 | GET | `/documents`、`/documents/{id}`、`/documents/{id}/file` | Web | 资料列表、详情或私有文件 |
| 简历 | GET／POST | `/preferences` | Web | 期望列表或创建期望 |
| 简历 | GET／PUT／DELETE | `/preferences/{id}` | Web | 期望详情、版本更新或删除 |
| 简历 | GET／POST | `/facts` | 求职 Web | 事实列表或创建事实 |
| 简历 | POST | `/facts/{id}/versions` | 求职 Web | 创建事实版本 |
| 简历 | POST | `/rewrites`、`/rewrites/{id}/segments/{segment_id}/decisions` | 求职 Web | 改写任务或段落决定 |
| 简历 | GET | `/rewrites/{id}` | 求职 Web | 改写详情 |
| 简历 | POST | `/resumes`、`/resumes/{id}/versions`、`/exports` | 求职 Web | 岗位版、版本或导出任务 |
| 简历 | GET | `/resumes`、`/resumes/{id}`、`/exports/{id}/file` | 求职 Web | 岗位版列表、详情或 PDF |
| 匹配池 | POST | `/browser/job-drafts` | 浏览器会话 | 抓取岗位并直接写入匹配池 |
| 匹配池 | GET | `/browser/job-drafts/{id}` | 浏览器会话 | 草稿状态 |
| 匹配池 | POST | `/job-pool/items` | 求职 Web | 手动岗位入池 |
| 匹配池 | POST | `/job-pool/items/batch-analyze`、`/job-pool/items/{id}/analyze` | 求职 Web | 批量或单岗位匹配任务 |
| 匹配池 | GET | `/job-pool/items`、`/job-pool/items/{id}` | 求职 Web | 岗位列表或详情 |
| 匹配池管理 | GET | `/admin/job-pool/items`、`/admin/job-pool/items/{id}` | `admin.job_pool.read` | 跨账号抓取岗位摘要或详情 |
| 匹配池管理 | GET／DELETE | `/admin/job-pool/items/{id}/deletion-impact`、`/admin/job-pool/items/{id}` | `admin.job_pool.manage` | 下架影响预览或受控下架 |
| 匹配池 | POST | `/job-pool/items/{id}/go-to-apply` | 求职 Web | 303 跳转 |
| 匹配池 | POST | `/analyses` | 招聘 Web | 单人分析任务 |
| 匹配池 | GET | `/analyses`、`/analyses/{id}` | Web | 本账号报告列表或详情 |
| 面试 | POST | `/interviews`、`/interviews/{id}/answers`、`/interviews/{id}/finish` | 求职 Web | 开场、回答或结束会话 |
| 面试 | GET | `/interviews`、`/interviews/{id}` | 求职 Web | 会话列表或详情 |
| 任务 | GET | `/tasks/{id}` | Web | 执行状态 |
| 任务 | POST | `/tasks/{id}/retry` | Web | 恢复原任务 |
| 用量 | GET | `/usage` | Web | 本账号适用功能余额与流水 |
| 用量 | GET | `/admin/usage-grants` | `admin.usage.grant` | 发放记录 |
| 用量 | POST | `/admin/users/{id}/usage-grants` | `admin.usage.grant` | 追加适用功能次数 |
| 统计 | GET | `/stats/me` | Web | 个人事实统计 |
| 统计 | POST | `/feedback` | Web | 提交产品反馈 |
| 统计 | GET | `/admin/metrics`、`/admin/costs`、`/admin/feedback`、`/admin/feedback/{id}` | `admin.stats.read` | 聚合、成本或反馈 |
| 统计 | PUT | `/admin/feedback/{id}` | `admin.stats.read` | 更新反馈状态 |
| 日志 | GET | `/admin/logs/operations`、`/admin/logs/security`、`/admin/logs/tasks`、`/admin/logs/{type}/{id}` | 分类日志权限 | 脱敏日志 |
| 日志 | POST | `/admin/log-exports` | 导出及分类权限 | 日志导出任务 |
| 日志 | GET | `/admin/log-exports/{id}`、`/admin/log-exports/{id}/file` | 导出及分类权限 | 导出状态或私有文件 |

所有路径自动加 `/api/v1`。删除影响和确认删除采用公共规范中的通用协议，不在路由总表重复列出。

## 实施顺序

1. 公共 Schema、统一异常、请求上下文、Web 会话和 CSRF。
2. 用户接口以及浏览器授权码换取流程。
3. 简历导入、确认版本、期望与私有文件。
4. 用量和任务的异步受理／轮询契约。
5. 招聘单人分析与求职匹配池，两站草稿上传和 303 跳转。
6. 改写、岗位版、PDF 与面试。
7. 个人统计、管理端、日志导出和巡检。

首版 OpenAPI 必须覆盖本索引全部路由。产品页面可以只组合这些接口，不为页面临时创建绕过模块权限或返回私有正文的聚合接口。
