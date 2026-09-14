# SDD 追溯矩阵：201 最小演示

更新时间：2026-09-15。此矩阵只覆盖明日可落地的纵向切片，不把完整首版 P0/P1 误写成已全部交付。

| 需求来源 | 可观察结果 | 接口契约 | 代码／数据 | 自动证据 |
| --- | --- | --- | --- | --- |
| F01、AC10 | 固定求职身份；不同账号不能读对方资料 | `POST /auth/register`、`POST /auth/login`、`GET /documents/{id}` | `api.py::register`、`security.py::current_web_account`；`documents.account_id` | `scripts/smoke_201.py`：跨账号 404 |
| F02、AC08 | 简历和 JD 先解析为可检查草稿 | `POST /documents`、`GET /documents/{id}` | `api.py::create_document_form`、`parse_document`；`documents`、`document_drafts`、`stored_files` | 正式冒烟导入两份文字资料；multipart 路径已实现 |
| F03、AC03 | 评分有固定维度、逐条要求、证据和条件状态 | `POST /job-pool/items`、`GET /analyses/{id}` | `api.py::_start_analysis`、`_persist_analysis_details`；`matching.py`；`analysis_*` 表 | 正式冒烟断言报告状态、维度和分数 |
| F09、AC11/12 | 任务先预留，成功结算，重复请求不重复扣减 | 同一分析键重复调用 `POST /job-pool/items/{id}/analyze` | `services.py::create_task`、`reserve_feature`、`settle_feature`；`async_tasks`、`usage_*` | 正式冒烟比较分析前后余额差为 1 |
| F12、AC17/18 | 岗位期望独立保存并生成版本快照 | `POST /preferences` | `api.py::create_preference`；`job_preferences`、`job_preference_versions` | 正式冒烟选择杭州前端期望 |
| F04 | 建议只改写原有段落，采用动作留痕 | `POST /rewrites`、`POST /rewrites/{id}/segments/{segment}/decisions` | `api.py::create_rewrite`、`decide_rewrite`；`resume_rewrite_*` 表 | 正式冒烟创建改写并采用一段 |
| F05 | 岗位版从冻结简历版本生成 PDF | `POST /resumes`、`POST /exports`、`GET /exports/{id}/file` | `api.py::create_resume_variant`、`create_resume_export`；`pdf_export.py` | 正式冒烟校验 `%PDF`，人工检查 A4 渲染 |
| F07、F13 | 浏览器岗位草稿去重，确认后可安全 303 跳转 | `/browser-auth/*`、`/browser/job-drafts`、`/job-pool/items`、`go-to-apply` | `api.py` 浏览器与跳转路由；`purslyx-job-capture.user.js`；`browser_job_drafts`、`apply_click_events` | 正式冒烟重复草稿命中同 ID、重复点击均 303 |
| F08 | 面试开场 3 个问题，刷新后恢复原会话 | `POST /interviews`、`GET /interviews/{id}`、`POST /interviews/{id}/answers` | `api.py::start_interview`、`submit_answer`；`interview_*` 表 | 正式冒烟断言 3 题并提交首题 |
| F10、F11 | 模型调用、反馈与统计不把正文写入成本日志 | `GET /usage`、`GET /stats/me`、`POST /feedback` | `services.py::model_call`；`model_call_attempts`、`product_feedback` | 正式 API 已实现；本次脚本未展开管理端 |

## 追溯结论

明日只承诺“需求 → Feature Spec → API → 实现 → 201 实测”的最小链路。未在本矩阵的完整并发竞争、真实邮件、真实平台页面和生产 Worker 验收，不作为本次演示已完成项。
