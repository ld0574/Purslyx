"""Purslyx 本地 PostgreSQL Worker。

这个 Worker 不依赖 Celery/Redis：事务 outbox 由 PostgreSQL 持久化，Worker 使用
``FOR UPDATE SKIP LOCKED`` 认领，执行代次用租约保护。以后接入外部
队列时可以复用这里的 Handler 和同一组状态迁移，不改变 HTTP 契约。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .errors import DomainError, NotFoundError
from .matching import merge_preference_contents
from .model_provider import ModelResult, get_model_provider, merge_model_results
from .models import (
    Account,
    Analysis,
    Document,
    DocumentDraft,
    DocumentVersion,
    Export,
    FactVersion,
    Interview,
    InterviewAnswer,
    InterviewFeedback,
    InterviewQuestion,
    InterviewSummary,
    JobPoolItem,
    LogExport,
    PreferenceVersion,
    ResumeVariantVersion,
    Rewrite,
    RewriteEvidence,
    RewriteSegment,
    StoredFile,
    Task,
    TaskAttempt,
    TaskOutbox,
    UsageReservation,
)
from .parsing import extract_file_text, sha256_bytes
from .pdf_export import render_resume_pdf
from .services import (
    claim_outbox_batch,
    claim_task_execution,
    fail_task,
    mark_outbox_published,
    recover_expired_leases,
    run_local_task,
    utcnow,
)
from .storage import ensure_storage_capacity, private_path, storage_key

SUPPORTED_TASK_TYPES = {
    "document_parse",
    "analysis",
    "rewrite",
    "resume_export",
    "interview_opening",
    "interview_feedback",
    "interview_summary",
    "log_export",
}

LOGGER = logging.getLogger("purslyx.worker")


def _input(task: Task, name: str) -> Any:
    value = (task.input_data or {}).get(name)
    if value in (None, ""):
        raise DomainError("TASK_INPUT_INVALID", f"任务缺少 {name}", 409, "retry")
    return value


def _account(db: Session, task: Task) -> Account:
    account = db.get(Account, task.account_id)
    if account is None or account.status in {"deleted", "purged"}:
        raise NotFoundError("任务所属账号不存在")
    return account


def _reservation(db: Session, task: Task) -> UsageReservation | None:
    return db.scalar(
        select(UsageReservation).where(
            UsageReservation.task_id == task.id,
            UsageReservation.status == "reserved",
        )
    )


def _run_model(
    db: Session,
    task: Task,
    attempt: TaskAttempt,
    reservation: UsageReservation | None,
    work: Callable[[], dict[str, Any] | ModelResult],
    *,
    owner: str,
    feature: str | None = None,
    cost_feature: str | None,
    on_success: Callable[[dict[str, Any]], None] | None = None,
    task_result: dict[str, Any] | None = None,
    estimated_input_tokens: int = 12000,
    estimated_output_tokens: int = 4000,
) -> None:
    """统一执行模型类任务，保留 provider/token 元数据并结算预算。"""

    run_local_task(
        db,
        task,
        reservation,
        work,
        on_success=on_success,
        feature=feature,
        cost_feature=cost_feature,
        attempt=attempt,
        lease_owner=owner,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        task_result=task_result,
    )


class TaskWorker:
    """一个进程内的 PostgreSQL Worker。"""

    def __init__(
        self,
        *,
        owner: str,
        batch_size: int = 10,
        lease_seconds: int = 600,
        outbox_lease_seconds: int = 60,
    ) -> None:
        self.owner = owner
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.outbox_lease_seconds = outbox_lease_seconds

    def run_once(self) -> int:
        """恢复过期租约并处理一批事件，返回本次处理的事件数。"""

        with SessionLocal() as db:
            recover_expired_leases(db, limit=self.batch_size)
            events = claim_outbox_batch(
                db,
                owner=self.owner,
                limit=self.batch_size,
                lease_seconds=self.outbox_lease_seconds,
            )
            event_ids = [event.id for event in events]
            db.commit()
        for event_id in event_ids:
            self._process_event(event_id)
        return len(event_ids)

    def _process_event(self, event_id: int) -> None:
        with SessionLocal() as db:
            event = db.scalar(
                select(TaskOutbox).where(
                    TaskOutbox.id == event_id,
                    TaskOutbox.status == "claimed",
                    TaskOutbox.claimed_by == self.owner,
                )
            )
            if event is None:
                return
            task = db.get(Task, event.task_id)
            if task is None or task.deleted_at is not None:
                event.status = "published"
                event.claimed_by = None
                event.claimed_at = None
                event.published_at = utcnow()
                db.commit()
                return
            claimed = claim_task_execution(
                db,
                task.id,
                owner=self.owner,
                lease_seconds=self.lease_seconds,
            )
            if claimed is None:
                # 已经由内联执行路径完成，或尚未到 retry_at；不要吞掉未来的事件。
                if task.status in {"queued", "retry_wait"} and task.next_retry_at is not None:
                    event.status = "pending"
                    event.claimed_by = None
                    event.claimed_at = None
                    event.available_at = task.next_retry_at
                else:
                    mark_outbox_published(db, task.id)
                db.commit()
                return
            _, attempt = claimed
            db.commit()

        task_id = 0
        attempt_id = 0
        task_public_id = ""
        task_type = ""
        try:
            with SessionLocal() as db:
                task = db.get(Task, task.id)
                attempt = db.get(TaskAttempt, attempt.id)
                if task is None or attempt is None:
                    return
                task_id = task.id
                attempt_id = attempt.id
                task_public_id = task.public_id
                task_type = task.task_type
                reservation = _reservation(db, task)
                self._dispatch(db, task, attempt, reservation)
        except Exception as exc:
            LOGGER.exception(
                "worker task failed event_id=%s task_id=%s task_public_id=%s attempt_id=%s task_type=%s error_type=%s error_code=%s",
                event_id,
                task_id or "unknown",
                task_public_id or "unknown",
                attempt_id or "unknown",
                task_type or "unknown",
                type(exc).__name__,
                getattr(exc, "code", "TASK_EXECUTION_FAILED"),
            )
            if task_id and attempt_id:
                try:
                    self._record_failure(task_id=task_id, attempt_id=attempt_id, error=exc)
                except Exception:
                    LOGGER.exception(
                        "worker could not persist failure event_id=%s task_id=%s attempt_id=%s",
                        event_id,
                        task_id,
                        attempt_id,
                    )

    def _record_failure(self, *, task_id: int, attempt_id: int, error: Exception) -> None:
        """兜底写失败状态；模型任务通常已经由 run_local_task 完成这一步。"""

        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None:
                return
            reservation = _reservation(db, task)
            failed_task = fail_task(
                db,
                task.id,
                reservation.id if reservation else None,
                error,
                attempt_id=attempt_id,
            )
            # 只有当前代次真正把任务推进到 failed 时才改业务占位。删除流程或新代次
            # 已经取消／接管任务后，迟到 Worker 只能结束自己的记录，不能复活旧状态。
            if failed_task.status == "failed":
                self._mark_business_failed(db, failed_task, error)
            mark_outbox_published(db, task.id)
            db.commit()

    def _mark_business_failed(self, db: Session, task: Task, error: Exception) -> None:
        data = task.input_data or {}
        code = getattr(error, "code", "TASK_EXECUTION_FAILED")
        if task.task_type == "document_parse":
            document = db.scalar(select(Document).where(Document.public_id == data.get("document_id"), Document.account_id == task.account_id))
            if document is not None and document.deleted_at is None:
                document.status = "failed"
                document.failure_code = code
        elif task.task_type == "analysis":
            analysis = db.scalar(select(Analysis).where(Analysis.task_id == task.id, Analysis.account_id == task.account_id))
            if analysis is not None and analysis.deleted_at is None:
                analysis.status = "failed"
                if analysis.job_pool_item_id:
                    pool = db.get(JobPoolItem, analysis.job_pool_item_id)
                    if pool is not None and pool.deleted_at is None:
                        pool.analysis_status = "failed"
                        pool.blocking_reasons = ["分析失败，可重试"]
        elif task.task_type == "rewrite":
            item = db.scalar(select(Rewrite).where(Rewrite.task_id == task.id, Rewrite.account_id == task.account_id))
            if item is not None and item.deleted_at is None:
                item.status = "failed"
        elif task.task_type == "resume_export":
            item = db.scalar(select(Export).where(Export.task_id == task.id, Export.account_id == task.account_id))
            if item is not None:
                item.status = "failed"
                item.failure_code = code
        elif task.task_type == "log_export":
            item = db.scalar(select(LogExport).where(LogExport.task_id == task.id, LogExport.account_id == task.account_id))
            if item is not None and item.status != "expired":
                item.status = "failed"
        elif task.task_type.startswith("interview_"):
            if task.task_type == "interview_opening":
                statement = select(Interview).where(Interview.task_id == task.id, Interview.account_id == task.account_id)
            else:
                statement = select(Interview).where(
                    Interview.public_id == data.get("interview_id"),
                    Interview.account_id == task.account_id,
                )
            item = db.scalar(statement)
            if item is not None and item.deleted_at is None:
                item.status = {
                    "interview_opening": "opening_failed",
                    "interview_feedback": "feedback_failed",
                    "interview_summary": "summary_failed",
                }.get(task.task_type, "failed")
                item.revision += 1

    def _dispatch(
        self,
        db: Session,
        task: Task,
        attempt: TaskAttempt,
        reservation: UsageReservation | None,
    ) -> None:
        if task.task_type not in SUPPORTED_TASK_TYPES:
            raise DomainError("TASK_TYPE_UNSUPPORTED", "当前 Worker 不支持该任务类型", 503, "retry")
        handler = getattr(self, f"_handle_{task.task_type}")
        handler(db, task, attempt, reservation)

    def _handle_document_parse(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        from .api import _latest_draft, _missing_document_fields

        document_id = str(_input(task, "document_id"))
        document = db.scalar(select(Document).where(Document.public_id == document_id, Document.account_id == task.account_id, Document.deleted_at.is_(None)))
        if document is None:
            raise DomainError("DOCUMENT_SOURCE_DELETED", "资料已删除，任务结果不会写回", 409)

        def work() -> ModelResult:
            content_text = document.raw_text
            if not content_text and document.file_path:
                content_text = extract_file_text(private_path(document.file_path, settings.data_dir), document.source_type)
            if not content_text:
                raise DomainError("DOCUMENT_CONTENT_UNREADABLE", "原始资料无法读取", 422, "paste_text")
            provider = get_model_provider()
            return provider.extract_resume(content_text) if document.document_type == "resume" else provider.extract_job(content_text)

        task_result: dict[str, Any] = {
            "resource_type": "document",
            "resource_id": document.public_id,
            "path": f"/api/v1/documents/{document.public_id}",
        }

        def save_result(value: dict[str, Any]) -> None:
            fresh = db.scalar(select(Document).where(Document.id == document.id, Document.account_id == task.account_id, Document.deleted_at.is_(None)))
            if fresh is None:
                raise DomainError("DOCUMENT_SOURCE_DELETED", "资料已删除，任务结果不会写回", 409)
            draft = _latest_draft(db, fresh.id, task.account_id)
            if draft is None:
                draft = DocumentDraft(account_id=task.account_id, document_id=fresh.id, revision=0)
                db.add(draft)
            draft.content = value
            draft.revision = (draft.revision or 0) + 1
            draft.status = "unconfirmed"
            draft.missing_field_codes = _missing_document_fields(value, fresh.document_type)
            fresh.draft_content = value
            fresh.draft_revision = draft.revision
            fresh.status = "available"
            task_result.update({"document_id": fresh.public_id, "draft_id": draft.public_id})

        _run_model(db, task, attempt, reservation, work, owner=self.owner, cost_feature="document_parse", on_success=save_result, task_result=task_result)

    def _handle_analysis(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        from .api import _persist_analysis_details

        analysis = db.scalar(select(Analysis).where(Analysis.task_id == task.id, Analysis.account_id == task.account_id, Analysis.deleted_at.is_(None)))
        if analysis is None:
            raise DomainError("ANALYSIS_NOT_FOUND", "分析占位不存在", 409, "retry")
        resume_version = db.get(DocumentVersion, analysis.resume_version_id)
        job_version = db.get(DocumentVersion, analysis.job_version_id)
        account = _account(db, task)
        if resume_version is None or job_version is None:
            raise DomainError("ANALYSIS_SOURCE_DELETED", "分析输入版本已不可用", 409)
        task_data = task.input_data if isinstance(task.input_data, dict) else {}
        preference_version_ids = [str(value) for value in task_data.get("preference_version_ids", []) if value]
        if preference_version_ids:
            preference_rows = list(
                db.scalars(
                    select(PreferenceVersion).where(
                        PreferenceVersion.account_id == task.account_id,
                        PreferenceVersion.public_id.in_(preference_version_ids),
                    )
                ).all()
            )
            preference_by_id = {row.public_id: row for row in preference_rows}
            if any(preference_by_id.get(value) is None or preference_by_id[value].deleted_at is not None for value in preference_version_ids):
                raise DomainError("ANALYSIS_SOURCE_DELETED", "岗位期望版本已不可用", 409)
            preferences = [preference_by_id[value] for value in preference_version_ids]
        else:
            preference = db.get(PreferenceVersion, analysis.preference_version_id) if analysis.preference_version_id else None
            if preference is not None and preference.deleted_at is not None:
                raise DomainError("ANALYSIS_SOURCE_DELETED", "岗位期望版本已不可用", 409)
            preferences = [preference] if preference else []
        preference_content = (
            preferences[0].content
            if len(preferences) == 1
            else merge_preference_contents([item.content for item in preferences])
            if preferences
            else None
        )

        def work() -> ModelResult:
            return get_model_provider().analyze(
                resume_version.content,
                job_version.content,
                preference_content,
                analysis.context_type,
            )

        def save_result(report: dict[str, Any]) -> None:
            if analysis.deleted_at is not None:
                raise DomainError("ANALYSIS_SOURCE_DELETED", "分析已删除，结果不会写回", 409)
            if len(preferences) > 1:
                report = {
                    **report,
                    "preference_bundle": {
                        "strategy": "any",
                        "preference_count": len(preferences),
                        "preference_version_ids": preference_version_ids,
                    },
                }
            analysis.status = "available"
            analysis.job_category = report.get("job_category", "general")
            analysis.ability_score = report.get("ability_score")
            analysis.evidence_coverage = report.get("evidence_coverage")
            analysis.result = report
            analysis.scoring_rule_version = report.get("scoring_rule_version", "ability-v0.2")
            analysis.result_schema_version = report.get("result_schema_version", "analysis-result-v1")
            analysis.prompt_version = report.get("prompt_version", "analysis-local-v1")
            analysis.completed_at = utcnow()
            _persist_analysis_details(
                db,
                account,
                analysis,
                report,
                resume_version.id,
                preference_content,
                job_version.content,
            )
            if analysis.job_pool_item_id:
                pool = db.get(JobPoolItem, analysis.job_pool_item_id)
                if pool is not None and pool.deleted_at is None:
                    pool.analysis_status = "available"
                    pool.revision += 1

        # API 进程只会把任务提交为 queued；Worker 领取后同步业务状态，
        # 让匹配池能区分“排队中”和“模型正在执行”，也便于定位 Worker 未启动。
        analysis.status = "running"
        if analysis.job_pool_item_id:
            pool = db.get(JobPoolItem, analysis.job_pool_item_id)
            if pool is not None and pool.deleted_at is None:
                pool.analysis_status = "running"
                pool.revision += 1
        db.commit()

        _run_model(
            db,
            task,
            attempt,
            reservation,
            work,
            owner=self.owner,
            feature="analysis",
            cost_feature="analysis",
            on_success=save_result,
            task_result={
                "resource_type": "analysis",
                "resource_id": analysis.public_id,
                "path": f"/api/v1/analyses/{analysis.public_id}",
            },
        )

    def _handle_rewrite(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        from .api import _analysis, _resume_segments, _version

        account = _account(db, task)
        data = task.input_data or {}
        analysis = _analysis(db, task.account_id, str(_input(task, "analysis_id")))
        resume_version = _version(db, task.account_id, str(_input(task, "resume_version_id")))
        job_version = db.get(DocumentVersion, analysis.job_version_id)
        item = db.scalar(select(Rewrite).where(Rewrite.task_id == task.id, Rewrite.account_id == task.account_id, Rewrite.deleted_at.is_(None)))
        if item is None or job_version is None:
            raise DomainError("REWRITE_SOURCE_INVALID", "改写输入已不可用", 409)
        all_segments = {str(row.get("segment_key")): row for row in _resume_segments(resume_version.content)}
        selected = [all_segments[key] for key in data.get("segment_keys", []) if key in all_segments]
        facts: list[dict[str, Any]] = []
        for public_id in data.get("fact_version_ids", []):
            fact = db.scalar(select(FactVersion).where(FactVersion.public_id == public_id, FactVersion.account_id == task.account_id, FactVersion.deleted_at.is_(None)))
            if fact is not None:
                facts.append({"fact_text": fact.fact_text, "source_type": fact.source_type, "id": fact.public_id})

        def work() -> ModelResult:
            return get_model_provider().rewrite(selected, job_version.content.get("job_fields") or {}, facts)

        def save_result(value: dict[str, Any]) -> None:
            item.status = "available"
            item.segments = value.get("segments", [])
            for segment in value.get("segments", []):
                row = RewriteSegment(
                    account_id=account.id,
                    rewrite_id=item.id,
                    segment_key=str(segment.get("source_segment_key")),
                    original_text=str(segment.get("original_text", "")),
                    suggested_text=segment.get("suggested_text"),
                    rationale=segment.get("rationale"),
                    status="available",
                )
                db.add(row)
                db.flush()
                for evidence in segment.get("evidence", []):
                    db.add(
                        RewriteEvidence(
                            account_id=account.id,
                            rewrite_segment_id=row.id,
                            source_type=str(evidence.get("source_type") or "fact"),
                            source_id=str(evidence.get("source_id") or evidence.get("fact_text") or "resume"),
                            quote=evidence.get("fact_text") or evidence.get("quote"),
                        )
                    )

        _run_model(
            db,
            task,
            attempt,
            reservation,
            work,
            owner=self.owner,
            feature="rewrite",
            cost_feature="rewrite",
            on_success=save_result,
            task_result={
                "resource_type": "rewrite",
                "resource_id": item.public_id,
                "path": f"/api/v1/rewrites/{item.public_id}",
            },
        )

    def _handle_resume_export(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        export = db.scalar(select(Export).where(Export.task_id == task.id, Export.account_id == task.account_id))
        version = db.scalar(select(ResumeVariantVersion).where(ResumeVariantVersion.public_id == _input(task, "resume_variant_version_id"), ResumeVariantVersion.account_id == task.account_id))
        if export is None or version is None:
            raise DomainError("EXPORT_SOURCE_INVALID", "岗位版简历版本已不可用", 409)
        output_dir = settings.export_dir.resolve()
        output_path = output_dir / f"{export.public_id}.pdf"
        render_path = output_dir / f".{export.public_id}.pdf.rendering"

        def work() -> dict[str, Any]:
            render_path.unlink(missing_ok=True)
            page_count = render_resume_pdf(version.content, version.layout, render_path, "岗位版简历")
            byte_size = render_path.stat().st_size
            ensure_storage_capacity(db, byte_size)
            render_path.replace(output_path)
            return {
                "export_id": export.public_id,
                "file_ready": True,
                "file_path": storage_key(output_path, settings.data_dir),
                "byte_size": byte_size,
                "content_hash": sha256_bytes(output_path.read_bytes()),
                "page_count": page_count,
            }

        def save_result(value: dict[str, Any]) -> None:
            export.file_path = value["file_path"]
            export.content_hash = value["content_hash"]
            export.page_count = int(value["page_count"])
            export.status = "available"
            export.completed_at = utcnow()
            db.add(
                StoredFile(
                    account_id=task.account_id,
                    purpose="resume_pdf",
                    original_filename="purslyx-resume.pdf",
                    media_type="application/pdf",
                    byte_size=int(value["byte_size"]),
                    sha256=value["content_hash"],
                    storage_key=value["file_path"],
                    status="available",
                )
            )

        try:
            _run_model(
                db,
                task,
                attempt,
                reservation,
                work,
                owner=self.owner,
                cost_feature=None,
                on_success=save_result,
                task_result={
                    "resource_type": "export",
                    "resource_id": export.public_id,
                    "path": f"/api/v1/exports/{export.public_id}",
                    "export_id": export.public_id,
                    "file_ready": True,
                },
            )
        except Exception:
            render_path.unlink(missing_ok=True)
            if output_path.exists() and export.status != "available":
                output_path.unlink(missing_ok=True)
            raise

    def _handle_interview_opening(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        interview = db.scalar(select(Interview).where(Interview.task_id == task.id, Interview.account_id == task.account_id, Interview.deleted_at.is_(None)))
        if interview is None:
            raise DomainError("INTERVIEW_SOURCE_INVALID", "面试占位不存在", 409)
        analysis = db.get(Analysis, interview.analysis_id)
        resume_version = db.get(DocumentVersion, interview.resume_version_id)
        if analysis is None or resume_version is None or analysis.deleted_at is not None:
            raise DomainError("INTERVIEW_SOURCE_INVALID", "面试输入已不可用", 409)

        def work() -> ModelResult:
            return get_model_provider().opening_questions(analysis.result or {}, resume_version.content)

        def save_questions(value: dict[str, Any]) -> None:
            questions = value.get("questions", [])[:3]
            if len(questions) != 3:
                raise DomainError("INTERVIEW_OPENING_FAILED", "开场没有生成完整的 3 个主问题", 503, "retry")
            interview.status = "awaiting_answer"
            interview.questions = []
            for position, question in enumerate(questions, start=1):
                row = InterviewQuestion(
                    account_id=task.account_id,
                    interview_id=interview.id,
                    question_type="main",
                    main_no=position,
                    position_no=position,
                    question_text=str(question.get("question_text", "")),
                    basis=question.get("basis") or {"rule_version": "interview-question-basis-v1"},
                    status="awaiting_answer",
                )
                db.add(row)
                db.flush()
                interview.questions.append({"id": row.public_id, "question_type": row.question_type, "main_no": row.main_no, "question_text": row.question_text})
            interview.current_question_id = interview.questions[0]["id"]
            interview.usage_settled = True

        _run_model(
            db,
            task,
            attempt,
            reservation,
            work,
            owner=self.owner,
            feature="interview",
            cost_feature="interview",
            on_success=save_questions,
            task_result={
                "resource_type": "interview",
                "resource_id": interview.public_id,
                "path": f"/api/v1/interviews/{interview.public_id}",
            },
        )

    def _handle_interview_feedback(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        interview = db.scalar(select(Interview).where(Interview.public_id == _input(task, "interview_id"), Interview.account_id == task.account_id, Interview.deleted_at.is_(None)))
        question = db.scalar(select(InterviewQuestion).where(InterviewQuestion.public_id == _input(task, "question_id"), InterviewQuestion.account_id == task.account_id))
        if interview is None or question is None or question.interview_id != interview.id:
            raise DomainError("INTERVIEW_SOURCE_INVALID", "面试回答输入已不可用", 409)
        answer = db.scalar(select(InterviewAnswer).where(InterviewAnswer.interview_id == interview.id, InterviewAnswer.question_id == question.id, InterviewAnswer.account_id == task.account_id))
        if answer is None:
            raise DomainError("INTERVIEW_ANSWER_NOT_FOUND", "面试回答不存在", 409)

        def work() -> ModelResult:
            provider = get_model_provider()
            feedback_result = provider.feedback({"question_text": question.question_text, "question_type": question.question_type}, answer.answer_text)
            if feedback_result.value.get("needs_followup"):
                return feedback_result
            pending_main = db.scalar(
                select(InterviewQuestion.id)
                .where(
                    InterviewQuestion.interview_id == interview.id,
                    InterviewQuestion.question_type == "main",
                    InterviewQuestion.status == "awaiting_answer",
                )
                .limit(1)
            )
            if pending_main is not None:
                return feedback_result
            questions = db.scalars(select(InterviewQuestion).where(InterviewQuestion.interview_id == interview.id).order_by(InterviewQuestion.position_no)).all()
            answers = db.scalars(select(InterviewAnswer).where(InterviewAnswer.interview_id == interview.id).order_by(InterviewAnswer.created_at)).all()
            question_values = [{"id": row.public_id, "main_no": row.main_no, "question_type": row.question_type, "question_text": row.question_text} for row in questions]
            answer_values = [{"question_id": db.get(InterviewQuestion, row.question_id).public_id, "answer_text": row.answer_text} for row in answers]
            summary_result = provider.summary(question_values, answer_values, "full")
            return merge_model_results(
                feedback_result,
                summary_result,
                value={"feedback": feedback_result.value, "summary": summary_result.value, "method": "STAR"},
            )

        def save_feedback(value: dict[str, Any]) -> None:
            summary_value = value.get("summary") if isinstance(value.get("summary"), dict) else None
            value = value.get("feedback") if isinstance(value.get("feedback"), dict) else value
            feedback = db.scalar(
                select(InterviewFeedback).where(
                    InterviewFeedback.interview_id == interview.id,
                    InterviewFeedback.question_id == question.id,
                )
            )
            if feedback is None:
                feedback = InterviewFeedback(account_id=task.account_id, interview_id=interview.id, question_id=question.id)
                db.add(feedback)
            feedback.status = "available"
            feedback.content = value.get("content")
            feedback.needs_followup = bool(value.get("needs_followup"))
            feedback.completed_at = utcnow()
            if value.get("needs_followup") and question.question_type == "main":
                existing = db.scalar(select(InterviewQuestion).where(InterviewQuestion.interview_id == interview.id, InterviewQuestion.parent_question_id == question.id))
                if existing is None:
                    last_position = db.scalar(select(InterviewQuestion.position_no).where(InterviewQuestion.interview_id == interview.id).order_by(InterviewQuestion.position_no.desc()).limit(1)) or 0
                    followup_question = str(value.get("followup_question") or "请再补充你本人采取的具体行动和可以核对的结果。").strip()[:800]
                    existing = InterviewQuestion(account_id=task.account_id, interview_id=interview.id, question_type="followup", main_no=question.main_no, parent_question_id=question.id, position_no=int(last_position) + 1, question_text=followup_question, basis={"parent_question_id": question.public_id, "method": "STAR", "rule_version": "interview-followup-v2"}, status="awaiting_answer")
                    db.add(existing)
                    db.flush()
                interview.current_question_id = existing.public_id
                interview.status = "awaiting_answer"
                interview.revision += 1
            else:
                next_main = db.scalar(select(InterviewQuestion).where(InterviewQuestion.interview_id == interview.id, InterviewQuestion.question_type == "main", InterviewQuestion.status == "awaiting_answer").order_by(InterviewQuestion.main_no))
                if next_main is not None:
                    interview.current_question_id = next_main.public_id
                    interview.status = "awaiting_answer"
                    interview.revision += 1
                else:
                    if summary_value is None:
                        raise DomainError("INTERVIEW_SUMMARY_FAILED", "完整面试总结未生成，请重试", 503, "retry")
                    self._save_summary_value(db, interview, "full", summary_value)
                    task_result.update({"summary": "full"})

        task_result: dict[str, Any] = {
            "resource_type": "interview",
            "resource_id": interview.public_id,
            "path": f"/api/v1/interviews/{interview.public_id}",
        }
        _run_model(
            db,
            task,
            attempt,
            reservation,
            work,
            owner=self.owner,
            cost_feature="interview_feedback",
            estimated_input_tokens=24000,
            estimated_output_tokens=8000,
            on_success=save_feedback,
            task_result=task_result,
        )

    def _save_summary_value(self, db: Session, interview: Interview, completion_type: str, value: dict[str, Any]) -> None:
        """写入已经生成的总结，避免在反馈任务里重复调用且不记账。"""

        summary = db.scalar(select(InterviewSummary).where(InterviewSummary.interview_id == interview.id))
        if summary is None:
            db.add(InterviewSummary(account_id=interview.account_id, interview_id=interview.id, completion_type=completion_type, content=value))
        else:
            summary.completion_type = completion_type
            summary.content = value
        interview.summary = value
        interview.status = "completed" if completion_type == "full" else "ended_early"
        interview.current_question_id = None
        interview.revision += 1

    def _save_summary(self, db: Session, interview: Interview, completion_type: str) -> None:
        """为独立的提前结束任务生成并写入总结。"""

        questions = db.scalars(select(InterviewQuestion).where(InterviewQuestion.interview_id == interview.id).order_by(InterviewQuestion.position_no)).all()
        answers = db.scalars(select(InterviewAnswer).where(InterviewAnswer.interview_id == interview.id).order_by(InterviewAnswer.created_at)).all()
        question_values = [{"id": row.public_id, "main_no": row.main_no, "question_type": row.question_type, "question_text": row.question_text} for row in questions]
        answer_values = [{"question_id": db.get(InterviewQuestion, row.question_id).public_id, "answer_text": row.answer_text} for row in answers]
        value = get_model_provider().summary(question_values, answer_values, completion_type).value
        self._save_summary_value(db, interview, completion_type, value)

    def _handle_interview_summary(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        interview = db.scalar(select(Interview).where(Interview.public_id == _input(task, "interview_id"), Interview.account_id == task.account_id, Interview.deleted_at.is_(None)))
        if interview is None:
            raise DomainError("INTERVIEW_SOURCE_INVALID", "面试会话不存在", 409)
        completion_type = str((task.input_data or {}).get("completion_type") or "early")

        def work() -> dict[str, Any]:
            questions = db.scalars(select(InterviewQuestion).where(InterviewQuestion.interview_id == interview.id).order_by(InterviewQuestion.position_no)).all()
            answers = db.scalars(select(InterviewAnswer).where(InterviewAnswer.interview_id == interview.id).order_by(InterviewAnswer.created_at)).all()
            values = [{"id": row.public_id, "main_no": row.main_no, "question_type": row.question_type, "question_text": row.question_text} for row in questions]
            answer_values = [{"question_id": db.get(InterviewQuestion, row.question_id).public_id, "answer_text": row.answer_text} for row in answers]
            return get_model_provider().summary(values, answer_values, completion_type)

        def save_result(value: dict[str, Any]) -> None:
            summary = db.scalar(select(InterviewSummary).where(InterviewSummary.interview_id == interview.id))
            if summary is None:
                db.add(InterviewSummary(account_id=task.account_id, interview_id=interview.id, completion_type=completion_type, content=value))
            else:
                summary.completion_type = completion_type
                summary.content = value
            interview.summary = value
            interview.status = "completed" if completion_type == "full" else "ended_early"
            interview.current_question_id = None
            interview.revision += 1

        _run_model(
            db,
            task,
            attempt,
            reservation,
            work,
            owner=self.owner,
            cost_feature="interview_summary",
            on_success=save_result,
            task_result={
                "resource_type": "interview",
                "resource_id": interview.public_id,
                "path": f"/api/v1/interviews/{interview.public_id}",
            },
        )

    def _handle_log_export(self, db: Session, task: Task, attempt: TaskAttempt, reservation: UsageReservation | None) -> None:
        """处理已由管理端创建的日志导出占位；没有正文进入任务 result。"""

        item = db.scalar(select(LogExport).where(LogExport.task_id == task.id, LogExport.account_id == task.account_id))
        if item is None:
            raise DomainError("LOG_EXPORT_NOT_FOUND", "日志导出占位不存在", 409)
        item.status = "exporting"
        export_format = str((item.filters or {}).get("export_format") or "jsonl")
        from .api import _log_export_work

        def save_result(value: dict[str, Any]) -> None:
            item.file_path = value["path"]
            item.filters = {**(item.filters or {}), "row_count": value["row_count"]}
            item.status = "downloadable"
            item.expires_at = utcnow() + timedelta(days=1)
            db.add(StoredFile(account_id=task.account_id, purpose="log_export", original_filename=f"purslyx-logs.{export_format}", media_type="text/csv" if export_format == "csv" else "application/x-ndjson", byte_size=int(value["byte_size"]), sha256=value["sha256"], storage_key=value["path"], status="available"))

        _run_model(
            db,
            task,
            attempt,
            reservation,
            lambda: _log_export_work(db, item),
            owner=self.owner,
            cost_feature=None,
            on_success=save_result,
            task_result={
                "resource_type": "log_export",
                "resource_id": item.public_id,
                "path": f"/api/v1/admin/log-exports/{item.public_id}",
                "export_id": item.public_id,
                "file_ready": True,
            },
        )


def task_status_snapshot(task: Task) -> dict[str, Any]:
    """Worker CLI 使用的安全任务摘要。"""

    return {"id": task.public_id, "task_type": task.task_type, "status": task.status, "retry_count": task.retry_count}
