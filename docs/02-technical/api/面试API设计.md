# Purslyx 面试 API 设计

| 项 | 内容 |
| --- | --- |
| 版本 | v0.2，interview-rubric-v2 |
| 更新日期 | 2026-10-05 |
| 状态 | 实现与隔离 PostgreSQL/Worker 验收；未生产部署 |
| 公共规范 | [API 设计规范](API设计规范.md) |
| 模块设计 | [面试模块设计](../modules/面试模块设计.md) |

以下路径相对 `/api/v1`，成功正文位于公共响应的 `data` 中。写请求须使用有效求职账号、CSRF 和 `Idempotency-Key`。公开 ID 是字符串随机标识，不返回数据库自增主键。

## 1. 会话、问题和版本

会话详情返回 `id/title/status/revision/rubric_version/legacy/current_question_id/questions/summary/task` 及冻结输入引用。当前版本为 `interview-rubric-v2`；历史空版本视为旧版，永远不回填为 v2。

每道题返回原字段和：

| 字段 | 说明 |
| --- | --- |
| `question_kind` | experience / reasoning / scenario，在开场冻结，追问继承 |
| `answer` | 已提交正式回答，含 id、answer_text、created_at |
| `feedback` | 当前轮次有效反馈，未生成时为空 |
| `practice_state.eligible` | 是否可首次提交：新版已结束场次的已回答主题且无训练记录 |
| `practice_state.remaining` | 成功训练后为 0，否则为 1；不是允许另建失败回答 |
| `practice_state.practice` | 已提交训练及其任务；失败必须重试此任务 |

`task` 只指正式开场、反馈或总结，不包含独立重答训练。

## 2. 主问题反馈 v5

`feedback.content` 保存 `schema_version=interview-feedback-v5`、`rubric_version=interview-rubric-v2`、冻结题型及：

| 字段 | 说明 |
| --- | --- |
| `summary` | 最多两句结论 |
| `evaluation_dimensions` | 主问题五维对象，追问为 null |
| `followup_review` | 追问补充反馈，主问题为 null |
| `priority_actions` | 最多两条优先动作 |
| `knowledge_checks` | 最多三条专业疑点，含 claim_quote/note/verification，状态为 needs_verification |
| `answer_outline` | kind=quote 的核对原文与 kind=prompt 的待补充槽位 |
| `star_assessment` | 仅经历题主问题有情境/任务/行动/结果诊断，其他为 null |

五维键固定为 `relevance/specificity/ownership/outcome_evidence/communication`；每项含中文 label、status、feedback、evidence_quote、evidence_status。等级仅 strong/partial/missing；非缺失项必须精确引用本轮回答。引用校验先使用完整片段，校验后才截短至展示长度。异常结构或不匹配引用最多自动修复一次，仍失败时任务失败，不降级为用户缺失，不推进会话。

追问 `followup_review.supplemented` 保存 `evidence_source=answer|parent_answer` 和 quote，分别核对本轮/主回答；`remaining_questions` 为尚未澄清事项。追问不返回五维等级，不入指数，不再生成追问。专业核查不联网，不伪造文献，也不提供权威事实保证。

## 3. 整场总结 v3

`summary` 返回 `completion_type=full|early` 和 content。数值与版本位于 `summary.content`，模型复盘文字位于 `summary.content.content`。

三道主问题有效评价按 strong=100 / partial=50 / missing=0 直接平均十五项，形成 `practice_index`；五维分项与 `next_practice_focus` 由服务端计算。只有完整完成且三题评价有效的 v2 会话生成数值。追问、提前结束、旧记录、系统异常及训练不入数值；无指数返回 null，不返回零分替代异常。

## 4. 开场与读取

### POST `/interviews`

原格式保持不变：

```json
{
  "job_pool_item_id": "pool-public-id",
  "analysis_id": "analysis-public-id",
  "resume_document_version_id": "resume-version-public-id",
  "title": "岗位面试练习",
  "confirm_usage": true
}
```

校验成功报告、岗位与报告简历冻结版本的同账号关系。返回 202 `{interview, task}`，创建时即固定 v2。开场恰好保存三道主题后结算一场；失败释放场次预留。重复请求不重复扣次。

### GET `/interviews`

支持 status、cursor 和 limit（最多 100）。只返回本账号未删除的 v2 会话，按更新时间分页。旧记录不删除、不重算。

### GET `/interviews/{id}`

返回详情并恢复任务状态。旧公开链接仍可识别 `legacy=true`，新版客户端只展示保留说明及新练习入口，不展示旧正文。

## 5. 正式回答和提前结束

### POST `/interviews/{id}/answers`

```json
{
  "question_id": "question-public-id",
  "answer_text": "我先定位依赖，再用性能记录验证优化效果。",
  "base_revision": 4
}
```

正文规范化后不能为空，原输入限 8000 字符。只接受当前待答题，正式回答不可覆盖。返回 202 `{interview, answer}`，正式任务位于 interview.task；重复同键同正文返回已保存回答与会话，同键不同正文冲突。反馈有效才推进；校验失败保留回答和原任务。

### POST `/interviews/{id}/finish`

请求 `{"base_revision": 6}`。仅等待回答时允许提前结束，未答题标 skipped，返回 202 会话详情。总结只使用实际回答，不生成指数，已开场场次不退还。

## 6. 免费独立重答

### POST `/interviews/{id}/questions/{question_id}/practice`

必须携带 `Idempotency-Key`，请求：

```json
{"answer_text": "根据建议补充后的真实回答或明确标注的拟议方案。"}
```

仅 v2 已完整完成或提前结束场次的已回答主题可提交，限 8000 字符。返回 202 `{practice, task}`；即使内联已完成，也使用同一响应格式。practice 包含 id、question_id、status、answer_text、feedback、comparison、rubric_version、task、completed_at。

每场每主题只允许一条训练；账号＋幂等键也唯一。同键同内容返回原记录，不同内容冲突；不同键重复领取返回 `INTERVIEW_PRACTICE_EXISTS`。失败记录保留同一份答案，不能另交文本；成功后 remaining=0 且不可覆盖。

不预留/结算用户面试次数，但计模型成本、接受预算、并发及限流。训练失败不能改写已完成会话，任何训练都不参与原总结和指数。

### GET `/interviews/{id}/practices`

返回 `{items: [practice]}`。只读本账号该场未删除训练，旧场次为空列表。

### 轮询与重试

复用 `GET /tasks/{task_id}` 和 `POST /tasks/{task_id}/retry`，重试仍需幂等键，正文例如 `{"reason":"resume_interview_practice"}`。失败只重试原任务及原训练答案；任务中心类型为 `interview_practice`。刷新也可由会话详情恢复训练。删除或来源不可用后禁止重试。

comparison 返回 `baseline_available` 与各维 `before/after/change`；change 为 improved/unchanged/weaker/unavailable，客户端用中文展示。无有效原评价不能声称提升，下降如实展示。

## 7. 状态、删除和错误

会话状态包含 opening、awaiting_answer、processing、completed、ended_early、opening_failed、feedback_failed、summary_failed。训练状态为 queued、processing、available、failed。等待用户回答不占 Worker，生成失败保留请求 ID 与重试入口。

会话删除影响接口新增 `affected.practices`。删除会话、来源简历、报告或岗位立即隐藏训练并取消任务，迟到 Worker 不能回写。账号隔离及账号状态检查覆盖读取、提交、重试与保存。

常见业务码：`INTERVIEW_EVALUATION_INVALID`（系统评价校验异常，非回答不足）、`INTERVIEW_PRACTICE_UNAVAILABLE`、`INTERVIEW_PRACTICE_EXISTS`、`IDEMPOTENCY_CONFLICT`、`TASK_PENDING_LIMIT`、`TASK_CONCURRENCY_LIMIT` 和全站预算错误。前端展示中文说明及请求 ID，不暴露异常正文。
