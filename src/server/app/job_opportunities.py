"""近 30 个上海自然日的跨日机会；处理状态独立于评分、任务和投递点击。"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from .application_recommendations import TIER_LABELS, application_recommendation
from .errors import DomainError
from .models import Analysis, Document, DocumentVersion, JobHandlingState, JobPoolItem
from .services import payload_hash, utcnow

HANDLING_VERSION = "job-handling-v1"
HANDLING_LABELS = {"pending": "待投", "verifying": "待核实", "applied": "本人确认已投", "dismissed": "不感兴趣"}


def handling_view(state: JobHandlingState | None) -> dict[str, Any]:
    status = state.status if state else "pending"
    return {"status": status, "label": HANDLING_LABELS[status], "revision": state.revision if state else 0,
            "updated_at": state.updated_at.isoformat() if state else None}


def state_for(db: Session, item: JobPoolItem) -> JobHandlingState | None:
    return db.scalar(select(JobHandlingState).where(JobHandlingState.account_id == item.account_id,
                                                   JobHandlingState.opportunity_key == item.opportunity_key))


def job_conditions(fields: dict[str, Any]) -> dict[str, Any]:
    salary = fields.get("salary")
    return {"salary": fields.get("salary_text") or (salary.get("display") or salary.get("text") if isinstance(salary, dict) and salary.get("status") == "specified" else None),
            "location": fields.get("location_text") or "、".join(fields.get("locations") or []),
            "work_mode": fields.get("work_mode"), "experience": fields.get("experience_text"), "education": fields.get("education_text")}


def source_update_view(latest: JobPoolItem | None, report_version_id: int | None,
                       report_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    updated = bool(latest and report_version_id and latest.job_document_version_id != report_version_id)
    if updated and report_fields is not None:
        # 相同 JD 仅因访问追踪参数再次入池，不应被宣称为正文更新。
        def content_fields(value: dict[str, Any]) -> dict[str, Any]:
            return {key: item for key, item in value.items() if key not in {"source_url", "missing_field_codes"}}

        updated = content_fields(latest.job_fields or {}) != content_fields(report_fields)
    return {"updated": updated, "latest_pool_item_id": latest.public_id if latest else None,
            "message": "JD 已更新，建议基于旧版；重新匹配需确认扣次" if updated else None}


def recent_range(now: datetime | None = None) -> tuple[datetime, datetime]:
    today = (now or utcnow()).astimezone(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=29), today + timedelta(days=1)


def _cursor_encode(row: dict[str, Any], scope: str) -> str:
    value = {"v": 1, "scope": scope, "rank": row["_rank"], "at": row["completed_at"], "id": row["_analysis_id"]}
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def _cursor_decode(value: str, scope: str) -> tuple[int, float, int]:
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if not isinstance(data, dict) or set(data) != {"v", "scope", "rank", "at", "id"}:
            raise ValueError("Invalid cursor")
        if type(data["v"]) is not int or data["v"] != 1 or type(data["rank"]) is not int or data["rank"] not in range(4) or type(data["id"]) is not int or data["id"] < 1:
            raise ValueError("Invalid cursor")
        at = datetime.fromisoformat(data["at"])
        if at.tzinfo is None:
            raise ValueError("Missing timezone")
        if data["scope"] != scope:
            raise DomainError("OPPORTUNITY_CURSOR_STALE", "筛选或日期已变化，请返回第一页", 409, "refresh")
        return data["rank"], -at.timestamp(), -data["id"]
    except DomainError:
        raise
    except (ValueError, TypeError, KeyError, binascii.Error, OverflowError, UnicodeError):
        raise DomainError("OPPORTUNITY_CURSOR_INVALID", "机会分页游标无效，请返回第一页", 422, "refresh") from None


def readable_report(value: Any) -> bool:
    """不让损坏/JSON null 的新结果遮住同源的旧有效报告。"""
    if not isinstance(value, dict) or not {"ability_score", "evidence_coverage"} <= value.keys():
        return False
    for key in ("conditions", "dimensions", "verification_items"):
        rows = value.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return False
    for dimension in value.get("dimensions", []):
        rows = dimension.get("requirements", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return False
        for row in rows:
            evidence = row.get("evidence", [])
            if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
                return False
    return True


def list_opportunities(db: Session, account_id: int, *, view: str = "unhandled", status: str | None = None,
                       tier: str | None = None, search: str | None = None, cursor: str | None = None,
                       limit: int = 20, now: datetime | None = None) -> dict[str, Any]:
    start, end = recent_range(now)
    scope = payload_hash({"account": account_id, "view": view, "status": status, "tier": tier,
                          "search": (search or "").strip().casefold(), "from": start.isoformat()})
    after = _cursor_decode(cursor, scope) if cursor else None
    resume = aliased(DocumentVersion)
    resume_document, job_document = aliased(Document), aliased(Document)
    # 先找每个来源的最新有效报告，再按报告完成时间筛选；新的失败任务不抹去旧有效依据。
    statement = (
        select(Analysis, JobPoolItem, DocumentVersion, JobHandlingState)
        .join(JobPoolItem, JobPoolItem.id == Analysis.job_pool_item_id)
        .join(DocumentVersion, DocumentVersion.id == Analysis.job_version_id)
        .join(job_document, job_document.id == DocumentVersion.document_id)
        .join(resume, resume.id == Analysis.resume_version_id)
        .join(resume_document, resume_document.id == resume.document_id)
        .outerjoin(JobHandlingState, and_(JobHandlingState.account_id == account_id,
                                         JobHandlingState.opportunity_key == JobPoolItem.opportunity_key))
        .where(Analysis.account_id == account_id, JobPoolItem.account_id == account_id,
               DocumentVersion.account_id == account_id, job_document.account_id == account_id,
               resume.account_id == account_id, resume_document.account_id == account_id,
               Analysis.deleted_at.is_(None), JobPoolItem.deleted_at.is_(None),
               DocumentVersion.deleted_at.is_(None), job_document.deleted_at.is_(None),
               resume.deleted_at.is_(None), resume_document.deleted_at.is_(None),
               Analysis.status.in_(["available", "succeeded"]), Analysis.completed_at.is_not(None),
               Analysis.result.is_not(None))
        .order_by(Analysis.completed_at.desc(), Analysis.id.desc())
    )
    ranks = {key: index for index, key in enumerate(TIER_LABELS)}
    tiers = dict.fromkeys(TIER_LABELS, 0)
    status_counts = dict.fromkeys(HANDLING_LABELS, 0)
    candidates = []
    seen: set[str] = set()
    keyword = (search or "").strip().casefold()
    for analysis, pool, version, state in db.execute(statement.execution_options(yield_per=100)):
        if pool.opportunity_key in seen or not readable_report(analysis.result):
            continue
        seen.add(pool.opportunity_key)
        if not start <= analysis.completed_at < end:
            continue
        fields = (version.content or {}).get("job_fields") or {}
        title, company = fields.get("title"), fields.get("company_name")
        if keyword and keyword not in f"{title or ''} {company or ''}".casefold():
            continue
        handling = handling_view(state)
        status_counts[handling["status"]] += 1
        if view == "unhandled" and handling["status"] not in {"pending", "verifying"}:
            continue
        if view == "handled" and handling["status"] not in {"applied", "dismissed"}:
            continue
        if status and handling["status"] != status:
            continue
        recommendation = application_recommendation(analysis.result)
        tiers[recommendation["tier"]] += 1
        if tier and recommendation["tier"] != tier:
            continue
        candidates.append({"id": pool.public_id, "analysis_id": analysis.public_id,
                           "job_title": title, "company_name": company, "job_conditions": job_conditions(fields),
                           "platform": pool.platform, "source_url": pool.source_url,
                           "handling": handling, "recommendation": recommendation,
                           "completed_at": analysis.completed_at.isoformat(),
                           "_key": pool.opportunity_key, "_version_id": analysis.job_version_id, "_fields": fields,
                           "_analysis_id": analysis.id, "_rank": ranks[recommendation["tier"]]})
    def sort_key(item: dict[str, Any]) -> tuple[int, float, int]:
        return item["_rank"], -datetime.fromisoformat(item["completed_at"]).timestamp(), -item["_analysis_id"]
    candidates.sort(key=sort_key)
    total = len(candidates)
    remaining = [item for item in candidates if after is None or sort_key(item) > after]
    page_items = remaining[:limit]
    has_more = len(remaining) > limit
    next_cursor = _cursor_encode(page_items[-1], scope) if has_more and page_items else None
    keys = [item["_key"] for item in page_items]
    latest_by_key: dict[str, JobPoolItem] = {}
    if keys:
        for pool in db.scalars(select(JobPoolItem).where(JobPoolItem.account_id == account_id,
                                                       JobPoolItem.deleted_at.is_(None),
                                                       JobPoolItem.opportunity_key.in_(keys))
                               .order_by(JobPoolItem.created_at.desc(), JobPoolItem.id.desc())):
            latest_by_key.setdefault(pool.opportunity_key, pool)
    from .api import _apply_action

    for item in page_items:
        latest = latest_by_key.get(item["_key"])
        item["source_update"] = source_update_view(latest, item["_version_id"], item["_fields"])
        item["apply_action"] = _apply_action(latest) if latest else {"available": False}
        item["apply_pool_item_id"] = latest.public_id if latest else item["id"]
        item["source_url"] = latest.source_url if latest else item["source_url"]
        for name in ("_key", "_version_id", "_fields", "_analysis_id", "_rank"):
            del item[name]
    return {"items": page_items, "page": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
            "counts": {"total": total, "statuses": status_counts, "tiers": tiers},
            "range": {"days": 30, "from": start.date().isoformat(), "to": (end - timedelta(days=1)).date().isoformat()},
            "timezone": "Asia/Shanghai", "version": HANDLING_VERSION}
