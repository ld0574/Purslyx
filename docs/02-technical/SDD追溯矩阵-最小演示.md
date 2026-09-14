# SDD 追溯矩阵：201 最小演示

更新时间：2026-09-15。此矩阵只覆盖明日可落地的纵向切片，不把完整首版 P0/P1 误写成已全部交付。

| 需求来源 | 可观察结果 | 接口契约 | 代码／数据 | 自动证据 |
| --- | --- | --- | --- | --- |
| F01、AC10 | 固定求职身份；不同账号不能读对方资料 | `POST /auth/register`、`POST /auth/login`、`GET /documents/{id}` | `api.py::register`、`security.py::current_web_account`；`documents.account_id` | `scripts/smoke_201.py`：跨账号 404 |
| F02、AC08 | 简历和 JD 先解析为可检查草稿；支持 PDF/DOC/DOCX 上传，失败时保留输入 | `POST /documents`、`GET /documents/{id}` | `api.py::_read_document_request`、`DocumentUploadMeta`、`parse_document`；`documents`、`document_drafts`、`stored_files`；`index.html::decorateDocumentForms` | 正式冒烟校验 multipart 文件导入；工作台支持文字与文件两条入口 |
| F03、AC03 | 评分有固定维度、逐条要求、证据、条件状态，并给出可执行的待核实事项和针对性面试问题 | `POST /job-pool/items`、`GET /analyses/{id}` | `api.py::_start_analysis`、`_persist_analysis_details`、`_analysis_view`；`matching.py`；`model_provider.py`；`analysis_*` 表 | 正式冒烟断言报告状态、维度、分数、`verification_items` 和 `interview_questions`；正式报告页展示跟进区 |
| F09、AC11/12 | 任务先预留，成功结算，重复请求不重复扣减 | 同一分析键重复调用 `POST /job-pool/items/{id}/analyze` | `services.py::create_task`、`reserve_feature`、`settle_feature`；`async_tasks`、`usage_*` | 正式冒烟比较分析前后余额差为 1 |
| F09 | 任务中心可恢复读取，状态变化可用 ETag/304 判断；失败任务沿用原输入重试 | `GET /api/v1/tasks`、`GET /api/v1/tasks/{id}`、`POST /api/v1/tasks/{id}/retry` | `api.py::list_tasks`、`get_task`、`retry_task`；`services.py::task_view` | 正式冒烟断言任务详情 304；页面已接通查看和重试 |
| F12、AC17/18 | 岗位期望独立保存并生成版本快照 | `POST /preferences` | `api.py::create_preference`；`job_preferences`、`job_preference_versions` | 正式冒烟选择杭州前端期望 |
| F04 | 建议只改写原有段落，采用动作留痕 | `POST /rewrites`、`POST /rewrites/{id}/segments/{segment}/decisions` | `api.py::create_rewrite`、`decide_rewrite`；`resume_rewrite_*` 表 | 正式冒烟创建改写并采用一段 |
| F05 | 岗位版从冻结简历版本生成 PDF；内容、模块顺序和排版保存为不可变新版本 | `POST /resumes`、`POST /resumes/{id}/versions`、`POST /exports`、`GET /exports/{id}/file` | `api.py::create_resume_variant`、`create_resume_variant_version`、`create_resume_export`；`_pool_by_pk`；`pdf_export.py`；`resume_variant_versions` | 正式冒烟保存第二版、同键重试不重复生成，并校验 `%PDF`；单元测试锁定内部岗位主键查询 |
| 删除边界 | 删除资料后派生正文与 PDF 立即不可访问，活动任务不再写回 | `GET /documents/{id}/deletion-impact`、`DELETE /documents/{id}` | `api.py::delete_document`、`_mark_tasks_cancelled`；`deleted_at`、`StoredFile` | 正式冒烟断言分析／改写／面试／岗位版 404，导出过期且文件 404 |
| F07、F13 | 浏览器岗位草稿去重，确认后可安全 303 跳转 | `/browser-auth/*`、`/browser/job-drafts`、`/job-pool/items`、`go-to-apply` | `api.py` 浏览器与跳转路由；`purslyx-job-capture.user.js`；`browser_job_drafts`、`apply_click_events` | 正式冒烟重复草稿命中同 ID、重复点击均 303 |
| F08 | 面试开场 3 个问题，逐题回答与反馈可恢复；支持完整完成和提前结束总结 | `POST /interviews`、`GET /interviews/{id}`、`POST /interviews/{id}/answers`、`POST /interviews/{id}/finish` | `api.py::start_interview`、`submit_answer`、`finish_interview`；`worker.py`；`interview_*` 表 | 正式冒烟断言 3 题、首题回答后仍可继续，并断言 `completion_type=early`；单元测试锁定 `full/early` 契约 |
| F10、F11 | 模型调用、反馈、统计和管理日志不把正文写入成本日志；反馈状态可处理 | `GET /usage`、`GET /stats/me`、`POST /feedback`；`GET/PUT /admin/feedback/{id}`；`GET /admin/logs/*`、`POST /admin/log-exports` | `services.py::model_call`；`model_call_attempts`、`product_feedback`、`admin_audit_events`、`log_exports`；`index.html::adminLogsPage` | 正式 API 与管理端页面已接通；日志导出和招聘／管理员端到端复核列为现场补充项 |

## 追溯结论

明日只承诺“需求 → Feature Spec → API → 实现 → 201 实测”的最小链路。未在本矩阵的完整并发竞争、真实邮件、真实平台页面和生产 Worker 验收，不作为本次演示已完成项。
