# Purslyx 匹配池 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 匹配池 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 已实现并通过 201 PostgreSQL 验收 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [匹配池模块设计](../modules/匹配池模块设计.md) |

## 1. 浏览器岗位草稿

### BrowserJobDraft

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 否 | 服务端草稿标识 |
| `platform` | enum | 否 | `boss`、`liepin` |
| `source_url` | string | 是 | 私有 HTTPS 原 JD；清理后为 `null` |
| `job_title` | string | 是 | 最多 200 字符 |
| `company_name` | string | 是 | 最多 200 字符 |
| `location_text` | string | 是 | 页面披露原文 |
| `work_mode` | enum | 是 | `onsite`、`hybrid`、`remote` |
| `salary_text` | string | 是 | 页面披露原文，不补猜口径 |
| `job_description_text` | string | 是 | 职责与要求正文 |
| `missing_field_codes` | string[] | 否 | 未获取字段的受控代码 |
| `status` | enum | 否 | `awaiting_confirmation`、`confirmed`、`expired`、`failed`；直接入池后为 `confirmed` |
| `expires_at`、`created_at` | datetime | 否 | 到期和上传时间 |

### POST `/api/v1/browser/job-drafts`

需要 Browser Bearer 和 `Idempotency-Key`。请求：

```json
{
  "platform": "boss",
  "source_url": "https://www.zhipin.com/job_detail/example.html",
  "capture_schema_version": "browser-job-capture-v1",
  "captured_at": "2026-09-14T08:30:00Z",
  "job_title": "前端工程师",
  "company_name": "示例科技",
  "location_text": "杭州",
  "work_mode": null,
  "salary_text": "20-30K",
  "job_description_text": "这是虚构的岗位说明。",
  "missing_field_codes": ["work_mode"]
}
```

服务端重新规范化 URL、校验平台域名，并从规范化字段计算内容指纹；不信任客户端摘要。正文建议上限 100,000 字符。首次创建返回 201；相同账号、平台、URL 与内容返回 200 原草稿。上传事务会同时创建确认 JD 版本和 `awaiting_requirements` 的匹配池岗位，不绑定简历、岗位期望、不创建分析任务、不预留次数；响应包含 `job_pool_item` 和 `pool_path`。

### GET `/api/v1/browser/job-drafts/{id}`

需要创建该草稿的 Browser Bearer。返回草稿当前状态；岗位上传时已经同步写入匹配池，不再提供 Web 确认入口。不返回简历、报告、用量或其他草稿。过期返回 410 `JOB_DRAFT_EXPIRED`。

## 2. 私有岗位

### JobPoolItem

```json
{
  "id": "0ce22d5a-8db0-45fb-b0da-c3718f8e9302",
  "source_type": "browser_capture",
  "platform": "boss",
  "job_title": "前端工程师",
  "company_name": "示例科技",
  "analysis_status": "awaiting_requirements",
  "missing_conditions": ["办公方式", "薪资"],
  "job_conditions": {"location": "杭州", "work_mode": null, "salary": "20-30K"},
  "analysis_summary": {"total": 0, "available": 0, "active": 0, "failed": 0},
  "job_document_version": {"id": "...", "version_no": 1},
  "preference_id": null,
  "latest_analysis": null,
  "apply_action": {
    "available": true,
    "click_token": "<短期签名令牌>",
    "expires_at": "2026-09-14T08:40:00Z"
  },
  "revision": 1,
  "captured_at": "2026-09-14T08:30:00Z",
  "created_at": "2026-09-14T08:30:00Z",
  "updated_at": "2026-09-14T08:30:00Z"
}
```

列表不返回 `source_url`、JD 正文或报告正文。详情额外返回已确认 JD 内容、输入版本历史和最近报告摘要。

### POST `/api/v1/job-pool/items`

仅求职 Web，会话已验证，需 CSRF 和幂等键。岗位入池只接受已确认的岗位版本：

```json
{
  "source": {
    "type": "document_version",
    "job_document_version_id": "ee90c239-aa8a-45d5-9e4a-c890f705667d"
  }
}
```

只入池返回 201 `JobPoolItem`。浏览器脚本已经在上传接口完成入池；该接口用于手动岗位。分析必须另行调用匹配接口；岗位始终先保存，不绑定简历或岗位期望，也不会暗中调用模型。

### GET `/api/v1/job-pool/items`

查询 `search`、`analysis_status`、`platform`、`created_from`、`created_to`、`cursor`、`limit`。`search` 会匹配岗位名称、公司和来源链接；默认按入池时间倒序返回紧凑摘要，响应包含 `captured_at`、岗位条件和具体 `missing_conditions`。

### GET `/api/v1/job-pool/items/{id}`

返回完整 `JobPoolItem`、确认 JD、所用期望、历史分析列表和最新任务。只有当前保存的原链接仍通过白名单校验时才返回可用 `apply_action`；不直接返回可由客户端替换的目标链接。

### POST `/api/v1/job-pool/items/batch-analyze`

需要 CSRF、幂等键和求职 Web 会话。一次请求可以提交多个岗位，但只选择一份当前简历；服务端自动读取当前账号全部 `active` 且不关联候选人资料的岗位期望，并为每个“岗位 × 岗位期望”创建一份分析任务。每份任务消耗 1 次分析。次数或队列不足时允许部分成功，响应在 `blocked` 中逐项说明原因。

```json
{
  "pool_item_ids": ["pool-1", "pool-2", "pool-3"],
  "resume_document_version_id": "1f523350-2faf-4f23-bf42-b4462f39fd59",
  "confirm_usage": true
}
```

响应包含 `requested_items`、`preference_count`、`analysis_count`、`started_count`、`tasks`、`blocked` 和刷新后的 `job_pool_items`。例如 3 个岗位、2 条有效期望会提交 6 次匹配。

### POST `/api/v1/job-pool/items/{id}/analyze`

需要 CSRF 和幂等键。

```json
{
  "resume_document_version_id": "1f523350-2faf-4f23-bf42-b4462f39fd59",
  "base_revision": 1,
  "confirm_usage": true
}
```

这是批量接口的单岗位快捷入口，仍然自动使用全部有效岗位期望；成功返回 202 和任务集合。主动使用新简历会为每条期望生成新报告并分别计次；恢复原失败任务应调用任务重试接口。

## 3. 分析报告

### AnalysisReport

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 否 | 报告标识 |
| `context_type` | enum | 否 | `seeker_pool`、`recruiter_single` |
| `status` | enum | 否 | `queued`、`running`、`available`、`failed` |
| `task` | TaskSummary | 否 | 关联任务摘要 |
| `input_versions` | VersionRef[] | 否 | 简历、JD 和可选期望版本 |
| `job_category` | enum | 否 | `engineering`、`product`、`operations`、`general` |
| `ability_score` | decimal string | 是 | 0—100，资料不足时为 `null` |
| `evidence_coverage` | decimal string | 是 | 0—1，原始精度 |
| `dimensions` | AnalysisDimension[] | 否 | 固定规则中的适用维度 |
| `conditions` | ConditionResult[] | 否 | 岗位方向、地点、方式、薪资 |
| `verification_items` | VerificationItem[] | 否 | 从证据缺口和未知／冲突条件整理出的待核实事项 |
| `interview_questions` | InterviewQuestion[] | 否 | 按要求缺口优先生成的针对性面试问题及其依据 |
| `overall_advice` | object | 是 | Schema 校验的结论与下一步 |
| `scoring_rule_version` | string | 否 | 评分计算规则 |
| `result_schema_version` | string | 否 | 报告 Schema |
| `prompt_version` | string | 否 | 生成提示词 |
| `completed_at` | datetime | 是 | 可查看时存在 |

`AnalysisDimension` 包含 `key`、`label`、`base_weight`、`effective_weight`、`score`、`evidence_status`、`summary`、`requirements`。每个要求包含 JD 引用、`supported`／`partially_supported`／`gap`／`needs_confirmation` 状态及简历证据。前端展示服务端计算值，不能自行重算或把未知写成差距。

`ConditionResult` 包含 `condition`、期望值、JD 值、强度、`matched`／`conflicted`／`unknown`／`not_applicable` 和说明。条件冲突不改变能力分。

`VerificationItem` 包含 `id`、`kind`、`status`、定位字段、`reason` 和可直接使用的 `question`；只收录能力要求的缺口／待确认项及未知／冲突条件。`InterviewQuestion` 包含 `id`、`question_type`、`priority`、`question_text` 和 `basis`，其中 `basis` 至少记录对应要求、维度、发现类型、证据段落及 `rule_version`。两组字段由服务端生成，前端只负责展示。

### POST `/api/v1/analyses`

招聘身份用此接口创建单人分析：

```json
{
  "context_type": "recruiter_single",
  "resume_document_version_id": "...",
  "job_document_version_id": "...",
  "preference_version_id": null,
  "confirm_usage": true
}
```

求职匹配池分析使用岗位子资源接口，不直接传 `seeker_pool` 绕过岗位归属。招聘请求成功返回 202 和 1 次分析预留；候选人期望未披露时 `preference_version_id=null`，条件结果为未知。

### GET `/api/v1/analyses`

查询 `context_type`、`status`、`document_id`、时间和分页。求职报告列表主要供匹配池详情组合，产品不在“简历”菜单建立重复 Tab。

### GET `/api/v1/analyses/{id}`

运行中返回报告摘要与任务；可用时返回完整 `AnalysisReport`；失败返回稳定错误及原任务恢复动作。来源已删除或越权统一返回 404。

## 4. 去投递跳转

### POST `/api/v1/job-pool/items/{id}/go-to-apply`

仅求职 Web。页面使用同源 `<form method="post" target="_blank">`，Content-Type 为 `application/x-www-form-urlencoded`：

```text
click_token=<岗位详情返回的短期签名令牌>
csrf_token=<当前 Web CSRF>
```

服务端从令牌解析一次性 UUID、岗位 ID、当前链接摘要和过期时间，重新检查账号、岗位、平台、HTTPS 与域名白名单。事件提交成功后返回 303 和 `Location`；相同令牌重试不增加计数。JSON 调用可以把 CSRF 放请求头，但仍只返回 303。记录失败、链接变化或不受支持时返回错误且不跳转。

## 5. 删除与错误

岗位和分析使用公共删除协议。删除岗位立即关闭报告、原 JD 和去投递入口；已记录点击只保留无原链接的统计事实。

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `JOB_CAPTURE_PAGE_UNSUPPORTED` | 422 | 页面或 URL 不支持，提示手动粘贴 |
| `JOB_CAPTURE_SESSION_REQUIRED` | 401 | 浏览器会话失效，本机保留草稿 |
| `JOB_CAPTURE_SCHEMA_UNSUPPORTED` | 422 | 脚本 Schema 版本不支持 |
| `JOB_DRAFT_EXPIRED` | 410 | 草稿已过期或正文已清理 |
| `JOB_ANALYSIS_REQUIREMENTS_MISSING` | 422 | 岗位保留，补齐版本后再分析 |
| `ANALYSIS_ROLE_MISMATCH` | 403 | 身份与分析上下文不符 |
| `ANALYSIS_EVIDENCE_INVALID` | 503 | 模型引用无法核对，不发布报告 |
| `ANALYSIS_INPUT_STALE` | 409 | 岗位 revision 或输入已变化 |
| `APPLY_URL_UNSUPPORTED` | 422 | 当前保存链接不安全或不支持 |
| `APPLY_CLICK_CONFLICT` | 409 | 点击令牌过期、岗位或链接摘要变化 |

## 6. 验证清单

覆盖 BOSS／猎聘 URL、延迟 DOM、重复渲染、未登录续传、浏览器 scope、草稿去重和过期、手动 JD、只入池不分析、缺次数保留岗位、双端身份、版本冻结、四类量表、代码分数、引用校验、条件冲突、重新分析、点击表单 303、令牌重放、错误域名和删除后旧入口。
