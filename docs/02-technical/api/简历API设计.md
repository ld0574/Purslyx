# Purslyx 简历 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 简历 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 待评审；字段级契约，接口尚未实现 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [简历模块设计](../modules/简历模块设计.md) |

## 1. 资料 Schema

### DocumentSummary

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 否 | 资料标识 |
| `document_type` | enum | 否 | `resume`、`job_description` |
| `subject_type` | enum | 否 | `self_resume`、`candidate_resume`、`job_description` |
| `title` | string | 是 | 列表标题，最多 160 字符 |
| `status` | enum | 否 | `importing`、`available`、`failed` |
| `source_type` | enum | 否 | `text`、`pdf`、`doc`、`docx`、`form` |
| `latest_draft` | DraftRef | 是 | 当前待确认／失败草稿摘要 |
| `latest_version` | VersionRef | 是 | 最新确认版本摘要 |
| `revision` | integer | 否 | 元数据并发版本 |
| `created_at`、`updated_at` | datetime | 否 | 创建与更新时间 |

`DraftRef` 包含 `id`、`status`、`revision`、`missing_field_codes`、`failure_code`、`expires_at`；`VersionRef` 包含 `id`、`version_no`、`schema_version`、`confirmed_at`。

### DocumentContent

确认内容使用按资料类型区分的联合 Schema。

```json
{
  "schema_version": "document-content-v1",
  "sections": [
    {
      "section_key": "experience",
      "section_type": "experience",
      "title": "工作经历",
      "position": 1,
      "segments": [
        {
          "segment_key": "exp-1-summary",
          "text": "负责虚构示例项目的前端开发。",
          "source": "user_confirmed"
        }
      ]
    }
  ],
  "job_fields": null
}
```

`section_key`、`segment_key` 在同一版本中唯一，长度不超过 96；`position` 从 1 连续递增。简历使用 `sections`；JD 还可使用 `job_fields` 保存标题、公司、职责、要求和已披露条件。内容总字符上限在实现前通过样例冻结，服务端不得静默截断。

## 2. 资料导入与确认

### POST `/api/v1/documents`

需要已验证 Web 会话、CSRF 和 `Idempotency-Key`。使用 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `document_type` | enum | 是 | `resume` 或 `job_description` |
| `subject_type` | enum | 是 | 必须与账号身份及资料类型匹配 |
| `title` | string | 否 | 最多 160 字符 |
| `text` | string | 二选一 | 粘贴文本；与 `file` 恰好一个 |
| `file` | binary | 二选一 | PDF、DOC、DOCX，探测类型后判断 |

求职账号允许 `self_resume` 和手动 `job_description`；招聘账号允许 `candidate_resume` 和 `job_description`。单文件上限 20 MiB、文字型 PDF 最多 20 页、提取后的规范化正文最多 100,000 个 Unicode 字符，超出返回 413。成功返回 201 `DocumentSummary`；文本可直接生成待确认草稿，文件先保存为私有原件。

### POST `/api/v1/documents/{id}/parse`

需要 CSRF 和幂等键。请求体为 `{}`；服务端从资料记录读取并校验原始文件或文本，不接受客户端另传文件 ID。

成功返回 202 公共异步响应，`task_type=document_parse`、`usage_reservation=null`。单次解析执行上限为 10 分钟；超时或依赖故障进入可恢复失败并保留原输入。账号邮箱未验证、文件不可读、格式不支持或相同资料已有进行中解析时返回相应错误或原任务。

### GET `/api/v1/documents`

查询参数：`document_type`、`subject_type`、`status`、`cursor`、`limit`。返回 `DocumentSummary` 列表，不加载正文。

### GET `/api/v1/documents/{id}`

返回 `DocumentSummary`、可用版本列表、当前草稿内容及最新确认内容。查询参数 `version_id` 可读取指定可见版本；省略时返回最新版本。版本正文只向资料所有账号返回。

### POST `/api/v1/documents/{id}/versions`

需要 CSRF 和幂等键。请求：

```json
{
  "draft_id": "2eb4ab52-1862-4e82-a174-7a3e55c45cdd",
  "base_revision": 2,
  "title": "前端工程师简历",
  "content": {
    "schema_version": "document-content-v1",
    "sections": [],
    "job_fields": null
  }
}
```

成功返回 201 新 `DocumentVersion`，包含 `id`、`version_no`、`title`、`content`、`source_type`、`schema_version`、`confirmed_at`。已确认草稿不能再次确认；revision 或 Schema 冲突返回 409／422。

### GET `/api/v1/documents/{id}/file`

需要 Web 会话。返回原始文件流，设置安全文件名、`Content-Disposition: attachment` 和 `Cache-Control: private, no-store`。纯文本资料或文件已清理返回 404。

## 3. 岗位期望

### PreferenceVersion

```json
{
  "id": "823e0e45-9536-441c-8b4b-a3834d8aee41",
  "version_no": 2,
  "job_title": {
    "status": "specified",
    "value": "前端工程师",
    "strength": "important"
  },
  "locations": {
    "status": "specified",
    "values": ["杭州"],
    "strength": "required"
  },
  "work_mode": {
    "status": "specified",
    "value": "remote",
    "strength": "prefer"
  },
  "salary": {
    "status": "specified",
    "min": "20000.00",
    "max": "30000.00",
    "currency": "CNY",
    "period": "monthly",
    "tax_basis": "pre_tax",
    "salary_months": null,
    "strength": "important"
  },
  "source_type": "self_confirmed",
  "confirmed_at": "2026-09-14T08:30:00Z"
}
```

`status` 分别支持 `specified`、`unknown`、`unrestricted`；薪资额外支持 `negotiable`。只有 `specified` 才允许相应数值；强度为 `prefer`、`important`、`required`。`locations.values` 最多 10 项，每项 1—80 字符，不跨期望记录拼接。

### GET `/api/v1/preferences`

返回当前账号的有效期望。求职账号默认返回本人多条期望；招聘账号必须传 `subject_document_id`，只返回该候选人明确提供的期望。列表项包含 `id`、`display_name`、`context`、`is_default`、`status`、`revision` 和最新 `PreferenceVersion`。

### POST `/api/v1/preferences`

需要 CSRF 和幂等键。请求包含 `display_name`、`context`、可选 `subject_document_id`、`is_default` 及完整 `preference`。求职例子“前端＋杭州＋远程＋20–30K”是一条请求；后端岗位必须另建一条。成功返回 201。

### GET `/api/v1/preferences/{id}`

返回父记录、全部可见版本和当前最新版本。

### PUT `/api/v1/preferences/{id}`

请求包含 `base_revision`、`display_name`、`is_default`、`status` 及完整 `preference`。成功创建新不可变版本并返回 200 更新后的父记录。修改免费，不自动重跑旧分析。

### DELETE `/api/v1/preferences/{id}`

采用公共删除影响协议。删除后不能用于新分析，旧报告仍显示当时的受限版本快照；正文清理后仅保留最小解释状态。

## 4. 事实与改写

### POST `/api/v1/facts`

需要求职身份、CSRF 和幂等键。请求：

```json
{
  "document_id": "7f6a2776-68c0-4d71-b844-160d8a71317b",
  "source_document_version_id": "1f523350-2faf-4f23-bf42-b4462f39fd59",
  "fact_category": "achievement",
  "fact_text": "将虚构示例项目的首屏加载时间从 3.2 秒降到 1.8 秒。",
  "source_segment_key": "project-1-result",
  "source_type": "user_added"
}
```

`fact_text` 为用户确认的事实，最多 2000 字符；用户补充时 `source_segment_key` 可空。成功返回 201 `Fact` 和首个 `FactVersion`，不消耗 AI 次数。

### GET `/api/v1/facts`

查询 `document_id`、`status`、分页，返回事实摘要和最新版本。正文只在本账号详情中返回。

### POST `/api/v1/facts/{id}/versions`

需要 CSRF 和幂等键。请求包含新的 `fact_category`、`fact_text`、来源字段和 `base_revision`；成功创建新版本。不得更新旧版本正文。

### POST `/api/v1/rewrites`

需要求职身份、CSRF 和幂等键。

```json
{
  "analysis_id": "c4676e87-7d1b-4c5d-a0ef-fda62723298f",
  "source_resume_version_id": "1f523350-2faf-4f23-bf42-b4462f39fd59",
  "segment_keys": ["project-1-summary", "project-1-result"],
  "fact_version_ids": ["0df21039-c3af-4b2a-9fb6-781ca7ca0011"],
  "confirm_usage": true
}
```

至少选择 1 个、最多 20 个段落；事实版本必须属于同一账号并仍有效。成功返回 202 `task_type=rewrite` 和 1 次改写预留。

### GET `/api/v1/rewrites/{id}`

返回改写状态、任务摘要、输入版本、规则／提示词版本及有序段落：

```json
{
  "id": "7aed3917-b8b5-4d6b-93ec-a3b2d58ef58d",
  "status": "available",
  "segments": [
    {
      "id": "11e92fef-b8dc-49f6-932a-c6fbb3825cac",
      "source_segment_key": "project-1-summary",
      "original_text": "负责前端开发。",
      "suggested_text": "负责示例项目的前端交付与性能优化。",
      "rationale": "补充职责范围，未增加不存在的经历。",
      "evidence": [],
      "current_decision": null
    }
  ]
}
```

### POST `/api/v1/rewrites/{id}/segments/{segment_id}/decisions`

需要 CSRF 和幂等键。请求：

```json
{
  "decision": "adopt",
  "edited_text": "负责示例项目的前端交付，并完成首屏性能优化。",
  "base_decision_no": 0
}
```

`decision` 为 `adopt`、`keep_original`、`revert`。`adopt` 可带最多 5000 字符的 `edited_text`；带编辑文字的采用即界面“编辑后采用”，其他决定的 `edited_text` 必须为 `null`。`base_decision_no` 为客户端当前看到的最后事件号，尚无决定时为 0。每次操作追加一条事件并返回新事件号与当前决定；并发变化返回 409，已采用文字再次编辑时追加新采用事件。

## 5. 岗位版简历与 PDF

### POST `/api/v1/resumes`

需要求职身份、CSRF 和幂等键。请求包含 `job_pool_item_id`、`source_resume_version_id`、可选 `rewrite_id` 和 `title`。成功返回 201 `ResumeVariant`，状态 `editing`、revision 为 1。

### GET `/api/v1/resumes`

查询 `job_pool_item_id`、`status`、分页，返回岗位版摘要、最新版本、最新导出状态和更新时间。

### GET `/api/v1/resumes/{id}`

返回父记录、版本列表及指定或最新 `ResumeVariantVersion`。版本包含：

```json
{
  "content": {
    "schema_version": "resume-variant-content-v1",
    "sections": []
  },
  "layout": {
    "schema_version": "resume-layout-v1",
    "section_order": [],
    "font_family": "noto_sans_sc",
    "font_size_pt": "10.50",
    "line_height": "1.40",
    "section_spacing_pt": "8.00",
    "bold_segment_keys": []
  },
  "template_version": "resume-template-v1"
}
```

样式只接受服务端白名单和范围；不接受 HTML、CSS、脚本、外部字体 URL 或任意坐标。

首版只允许 `font_family=noto_sans_sc`，字体包版本为 `noto-sans-sc-v1`；`font_size_pt` 为 9—12、默认 10.5，`line_height` 为 1.2—1.8、默认 1.4，`section_spacing_pt` 为 4—16、默认 8。模板固定为 `resume-template-v1`，具体渲染镜像按部署摘要锁定。

### POST `/api/v1/resumes/{id}/versions`

请求包含 `base_revision`、完整 `content`、完整 `layout`、`template_version` 和可选 `rewrite_id`。成功返回 201 新不可变版本并更新父 revision。冲突返回 409，前端保留本地编辑。

### POST `/api/v1/exports`

需要 CSRF 和幂等键。请求 `{ "resume_version_id": "..." }`。成功返回 202 `task_type=pdf_export`，不预留 AI 次数。同成品键已有可下载文件时返回 200 原导出。

### GET `/api/v1/exports/{id}/file`

只有状态 `downloadable` 时返回同一 PDF 文件；生成中返回 409 `EXPORT_NOT_READY` 和原任务 ID，失败返回 503 `EXPORT_RENDER_FAILED`。响应含 `Content-Type: application/pdf`、安全文件名、`ETag` 和禁止公共缓存头。

## 6. 删除与错误

资料、事实、改写和岗位版使用公共删除影响协议。删除资料会影响版本、报告引用、改写、岗位版、导出和活动任务；影响集合变化返回 409。成功删除后旧文件、正文和任务结果立即不可读。

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `DOCUMENT_ROLE_MISMATCH` | 403 | 固定身份与资料用途不符 |
| `DOCUMENT_FORMAT_UNSUPPORTED` | 415 | 媒体类型不支持 |
| `DOCUMENT_TOO_LARGE` | 413 | 文件、页数或正文超限 |
| `DOCUMENT_CONTENT_UNREADABLE` | 422 | 加密、扫描、空白或正文不可读 |
| `DOCUMENT_PARSE_FAILED` | 503 | 解析依赖失败，保留输入并按建议恢复 |
| `STORAGE_CAPACITY_LIMIT` | 503 | 本机存储达到保护阈值，暂停新上传或导出 |
| `DOCUMENT_VERSION_CONFLICT` | 409 | 草稿或资料 revision 过期 |
| `PREFERENCE_INCOMPLETE` | 422 | 状态与值不一致、薪资倒置或缺口径 |
| `FACT_SOURCE_INVALID` | 422 | 事实来源段落或版本不合法 |
| `REWRITE_EVIDENCE_INVALID` | 422 | 无依据建议不能发布 |
| `REWRITE_SEGMENT_CONFLICT` | 409 | 段落失效或决定事件号已经变化 |
| `REWRITE_DECISION_INVALID` | 422 | 决定、编辑文字或基础事件号不合法 |
| `RESUME_VERSION_CONFLICT` | 409 | 岗位版基础版本过期 |
| `EXPORT_NOT_READY` | 409 | PDF 仍在生成 |
| `EXPORT_RENDER_FAILED` | 503 | 渲染失败，可恢复原任务 |

## 7. 验证清单

覆盖四种输入、错误 MIME、扫描／空白／加密／超限文件、存储容量保护、身份用途、两账号文件访问、草稿并发、确认版本不可变、多条期望不串字段、多城市与未知状态、事实来源、改写引用、采用／编辑／撤回、岗位版 revision、样式白名单、长文分页、同版本预览下载和删除期间迟到 Worker。
