# 明日 SDD 演示脚本

## 开场（30 秒）

“今天不展示一个做不完的平台，而展示一个能落地的纵向切片：需求先变成 Feature Spec，再变成接口和数据库事实，最后用 201 环境的真实 HTTP 冒烟证明它能跑。”

## 1. 先确认环境（30 秒）

浏览器打开 `http://127.0.0.1:8001/health`，现场指出：

```json
{
  "status": "ok",
  "database": {"backend": "postgresql"},
  "environment": "201"
}
```

补一句：“本项目禁止 SQLite；密码不在代码里，按 `本地开发环境.md` 注入进程。”

## 2. 页面走最短链路（2 分钟）

打开 `http://127.0.0.1:8001/`，点击“确认资料并生成匹配报告”。页面会完成：

1. 提交简历文字；
2. 提交岗位 JD；
3. 确认两份资料；
4. 对已确认版本生成报告。

展示输出中的 `resume_version`、`job_version`、`ability_score`、`evidence_coverage` 和 `conditions`。

## 3. 讲 SDD 追溯（2 分钟）

打开 [Feature Spec](../01-product/specs/Feature%20Spec-资料确认与可复核匹配.md) 和 [追溯矩阵](../02-technical/SDD追溯矩阵-最小演示.md)：

- 需求：用户要“知道哪些岗位值得投，并且有依据”；
- 接口：`POST /documents`、`POST /documents/{id}/versions`、`POST /job-pool/items`；
- 数据：草稿和确认版本分开，报告保存输入版本与规则版本；
- 实现：`api.py` 负责权限和事务，`matching.py` 计算分数；
- 验收：同键重试不多扣，跨账号返回 404，删除后旧入口失效。

## 4. 展示正式能力（2 分钟）

运行：

```bash
PURSLYX_BASE_URL=http://127.0.0.1:8001 \
PYTHONPATH=src .venv/bin/python scripts/smoke_201.py
```

只需要看最后一行 JSON：`environment=201`、`analysis_status=available`、`pdf_ready=true`、`browser_draft_deduped=true`、`apply_status=303`、`cross_account_isolation=true`。

## 5. 收尾边界（1 分钟）

“今天交付的是可复核的 SDD 最小闭环，不把本地确定性模型说成真实模型效果，也不把 303 说成平台已投递。下一步可以替换模型和 Worker，但不需要推翻资料版本、任务、用量和报告契约。”
