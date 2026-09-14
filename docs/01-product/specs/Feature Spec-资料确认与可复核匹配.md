# Feature Spec：资料确认与可复核匹配

| 项目 | 内容 |
| --- | --- |
| 版本 | v0.1-demo |
| 日期 | 2026-09-15 |
| 状态 | 已实现，可现场演示 |
| 对应需求 | F01、F02、F03、F09、F12；演示用 F04、F05 的后续结果链路 |
| 运行基线 | `docs/02-technical/deployment/本地开发环境.md`；直连 201 PostgreSQL |

## 1. 目标

用一条 5 分钟以内的纵向链路演示 SDD：把产品需求拆成接口契约，再落到数据库事实、业务代码和可执行验收。用户提交一份简历和一份岗位 JD，检查解析草稿，确认不可变版本，最后得到可以重新读取的匹配报告。

报告必须能回答三件事：匹配分从哪里来、每条判断依据是什么、当前岗位条件哪些已匹配或仍未知。模型只负责结构化整理，能力分和覆盖率由 `matching.py` 按固定规则计算。

## 2. 用户故事

作为求职者，我希望把简历和目标 JD 交给 Purslyx，在确认系统整理结果后得到一份有证据的匹配报告；即使刷新页面或重新发起请求，也不会丢失已确认版本，也不会因为重复点击多扣一次使用次数。

## 3. 本次切片范围

### 必须完成

1. 健康检查能证明服务连接的是 201 PostgreSQL。
2. 资料只允许使用文本、文字型 PDF、DOC 或 DOCX；本次页面演示使用文本。
3. 解析结果先落为待确认草稿，确认后生成带 `version_no` 的不可变版本。
4. 分析固定引用一份简历版本、一份 JD 版本和可选的一条岗位期望版本。
5. 报告落库并可通过报告详情接口重新读取，包含能力维度、逐条要求、证据覆盖率、条件状态和整体建议。
6. 正式写接口要求 Web 会话、CSRF 和 `Idempotency-Key`；不同账号只能访问自己的资源。

### 明确不作为本次启动前置条件

真实模型调用、Celery/Redis Worker、SMTP 邮件、真实平台登录、自动投递和外部平台投递回执。正式 API 已保留这些扩展点；明天先使用本地确定性模型完成可复核流程。

## 4. 接口契约

所有正式接口前缀为 `/api/v1`。

| 步骤 | 方法与路径 | 输入／输出要点 | 代码落点 |
| --- | --- | --- | --- |
| 注册与登录 | `POST /auth/register`、`POST /auth/login` | 固定求职身份；返回 Web Bearer 和 CSRF 令牌 | `api.py::register`、`api.py::login` |
| 导入资料 | `POST /documents` | JSON 文本或 multipart 文件；返回 `latest_draft` | `api.py::create_document_form` |
| 查看草稿 | `GET /documents/{id}` | 返回草稿正文、revision 和历史版本 | `api.py::get_document` |
| 确认版本 | `POST /documents/{id}/versions` | `draft_id + base_revision + content`；返回不可变版本 | `api.py::create_document_version` |
| 岗位入池并分析 | `POST /job-pool/items` | 引用 JD 版本、简历版本、岗位期望版本；确认后计一次分析 | `api.py::create_pool_item`、`_start_analysis` |
| 读取报告 | `GET /analyses/{id}` | 返回报告和固定规则版本 | `api.py::get_analysis_report` |
| 演示短入口 | `POST /api/v1/demo/*` | `/` 页面用隔离演示账号快速复现前三步 | `main.py` |

## 5. 不变量与错误恢复

- 版本确认只读取当前账号、当前资料下的草稿；错误的 `version_id` 返回 404，不泄露其他资料。
- 分析拒绝把简历版本当成 JD，或把 JD 版本当成简历；来源删除后旧入口不可读。
- 同一账号、同一动作、同一 `Idempotency-Key` 的相同请求返回原任务；请求摘要不同返回 409。
- 计次任务先预留、成功交付后结算，失败释放；轮询、刷新和重复提交不再次扣减。
- 报告缺少要求证据时显示“待确认”，不能把未知写成不具备，也不能声称录用概率或投递成功率。
- 服务启动拒绝空的、SQLite 或其他非 PostgreSQL `DATABASE_URL`；密码只由本地开发环境进程注入。

## 6. 验收标准

| 编号 | Given / When / Then |
| --- | --- |
| FS-01 | Given 服务启动；When 请求 `/health`；Then `environment=201` 且 `database.backend=postgresql`。 |
| FS-02 | Given 合法简历和 JD；When 重复导入同一资料；Then 两次返回同一个资料 ID；同键不同正文返回 `IDEMPOTENCY_CONFLICT`。 |
| FS-03 | Given 待确认草稿；When 以正确 revision 确认；Then 生成版本 1，报告只引用该版本；用其他资料的版本 ID 读取返回 404。 |
| FS-04 | Given 已确认版本；When 创建岗位池分析并重复提交同一分析键；Then 报告可读取且分析余额只减少 1。 |
| FS-05 | Given 报告有逐条要求；When 查看详情；Then 能看到固定维度、原文证据、覆盖率和条件状态，且结果已落库。 |
| FS-06 | Given 缺少认证、CSRF 或账号不是资源所有者；When 请求写入或读取；Then 分别得到稳定错误，不创建跨账号数据。 |
