# Purslyx 面试 API 设计

| 项 | 内容 |
| --- | --- |
| 模块 | 面试 |
| 版本 | v0.1 |
| 更新日期 | 2026-09-14 |
| 状态 | 待评审；字段级契约，接口尚未实现 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [面试模块设计](../modules/面试模块设计.md) |

## 1. Schema

### InterviewSummary

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 否 | 会话标识 |
| `title` | string | 否 | 岗位与练习显示标题 |
| `status` | enum | 否 | `opening`、`awaiting_answer`、`processing`、`completed`、`ended_early`、`opening_failed` |
| `job_pool_item` | ResourceRef | 否 | 私有岗位摘要 |
| `analysis` | ResourceRef | 否 | 分析报告摘要 |
| `current_question_id` | UUID | 是 | 当前待答问题 |
| `answered_main_count` | integer | 否 | 0—3 |
| `answered_followup_count` | integer | 否 | 0—3 |
| `task` | TaskSummary | 是 | 当前短任务；等待回答时可空 |
| `revision` | integer | 否 | 会话并发版本 |
| `created_at`、`updated_at` | datetime | 否 | 时间点 |

### InterviewQuestion

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `id` | UUID | 否 | 轮次 ID，回答请求使用 |
| `question_type` | enum | 否 | `main`、`followup` |
| `main_no` | integer | 否 | 1—3 |
| `parent_question_id` | UUID | 是 | 追问指向主问题 |
| `question_text` | string | 否 | 问题正文 |
| `basis` | object | 否 | 与岗位／报告依据的结构化说明 |
| `status` | enum | 否 | `awaiting_answer`、`answered`、`skipped` |
| `answer` | InterviewAnswer | 是 | 已提交的最终回答 |
| `feedback` | InterviewFeedback | 是 | 当前轮次反馈 |

`InterviewFeedback` 包含 `status`、`content`、`needs_followup`、`task`、`schema_version` 和 `completed_at`。`InterviewResult` 包含 `completion_type=full|early`、已回答数量、结构化总结、未完成项、Schema 与时间。

## 2. 发起与列表

### POST `/api/v1/interviews`

仅已验证求职 Web，需要 CSRF 和 `Idempotency-Key`。

```json
{
  "job_pool_item_id": "0ce22d5a-8db0-45fb-b0da-c3718f8e9302",
  "analysis_id": "c4676e87-7d1b-4c5d-a0ef-fda62723298f",
  "resume_document_version_id": "1f523350-2faf-4f23-bf42-b4462f39fd59",
  "confirm_usage": true
}
```

岗位、可用报告和简历版本必须属于同一账号且输入关系一致。成功返回 202，包含 `InterviewSummary`、`task_type=interview_opening` 和 1 场预留。3 个主问题完整保存后才结算；开场失败释放预留。

### GET `/api/v1/interviews`

查询 `status`、`job_pool_item_id`、`created_from`、`created_to`、分页。返回会话摘要，默认最近更新在前。删除会话不返回。

### GET `/api/v1/interviews/{id}`

返回 `InterviewSummary`、有序问题、回答、反馈和最新汇总。`opening`／`processing` 返回当前任务；`awaiting_answer` 返回唯一当前问题和 `allowed_actions=[submit_answer, finish_early]`；已完成返回结果。

## 3. 回答与推进

### POST `/api/v1/interviews/{id}/answers`

需要 CSRF 和 `Idempotency-Key`。

```json
{
  "question_id": "08636bc1-7072-4f1c-a27a-b8332833a239",
  "answer_text": "我先定位首屏依赖，再用性能记录验证优化效果。",
  "base_revision": 4
}
```

`answer_text` 去除首尾空白后长度为 1—10,000 字符。只接受当前 `awaiting_answer` 问题；提交后回答不可覆盖，成功返回 202，包含更新后的会话、反馈任务和 `usage_reservation=null`。同键重放返回原回答；同一问题使用新键重复提交返回 409。

反馈成功后可能出现一条追问，也可能进入下一主问题。客户端只根据新的会话状态和 `current_question_id` 决定界面，不自行计算题号或追问次数。

### POST `/api/v1/interviews/{id}/finish`

需要 CSRF 和幂等键。

```json
{
  "base_revision": 6
}
```

只允许 `awaiting_answer` 状态提前结束。服务端把未答问题标为 `skipped`，创建 `interview_summary` 任务，返回 202；汇总仅使用实际回答并明确未完成项。已开场场次不退还。重复请求返回原汇总任务。

## 4. 状态与恢复

| 会话状态 | 页面行为 | 可用动作 |
| --- | --- | --- |
| `opening` | 显示生成 3 个问题 | 查询任务 |
| `opening_failed` | 显示失败且场次已释放 | 重试原任务 |
| `awaiting_answer` | 展示当前问题和已有历史 | 提交回答、提前结束 |
| `processing` | 展示本轮已提交回答和处理中 | 查询任务，不重复提交 |
| `completed` | 展示完整汇总 | 读取、删除 |
| `ended_early` | 展示已有回答汇总和未完成项 | 读取、删除 |

重新登录、换设备或刷新时只调用详情接口恢复。客户端本地未提交草稿不是服务端回答；服务端不会因为轮询、刷新或恢复再次计次。

## 5. 删除与错误

会话使用公共删除影响协议。删除成功后问题、回答、反馈、汇总、任务正文和检查点立即不可读，物理清理异步执行。

| 业务码 | HTTP | 说明 |
| --- | --- | --- |
| `INTERVIEW_ROLE_NOT_ALLOWED` | 403 | 招聘账号不能发起求职练习 |
| `INTERVIEW_SOURCE_UNAVAILABLE` | 404 | 岗位、报告或资料不可见 |
| `INTERVIEW_OPENING_FAILED` | 503 | 开场未交付，场次释放 |
| `INTERVIEW_NOT_AWAITING_ANSWER` | 409 | 返回当前会话状态 |
| `INTERVIEW_ROUND_CONFLICT` | 409 | 问题、顺序或 revision 已变化 |
| `INTERVIEW_ANSWER_INVALID` | 422 | 回答为空或超长 |
| `INTERVIEW_ALREADY_FINISHED` | 409 | 返回现有结果引用 |

## 6. 验证清单

覆盖 3 个主问题原子可见、开场失败释放、重复发起、每题最多 1 个追问、回答幂等、乱序提交、revision 冲突、处理中恢复、Worker 崩溃、完整／提前汇总、开场后不重复计次、提示注入、跨账号和删除迟到结果。
