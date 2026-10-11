"""跨日机会、手动状态、完整集合分页；只在隔离 PostgreSQL Schema 运行。"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import test_auto_matching_postgres as auto_cases
from sqlalchemy import func, select
from test_auto_matching_postgres import capture, enable, run_worker

fx = auto_cases.fx
migrated = auto_cases.migrated

pytestmark = pytest.mark.skipif(os.getenv("PURSLYX_TEST_POSTGRES") != "1", reason="Use isolated smoke script")
NOW = datetime(2026, 10, 11, 3, tzinfo=timezone.utc)


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"] if status != 204 else None


def result(score=85):
    return {"ability_score": score, "evidence_coverage": 0.8, "dimensions": [], "conditions": [], "verification_items": []}


def seed(fx, db, *, source=None, title="合成岗位", company="合成公司", completed=None, created=None, report=None):
    from server.app.models import Analysis, Document, DocumentVersion, JobPoolItem

    job = Document(account_id=fx["account_id"], title=title, document_type="job_description",
                   subject_type="job_description", source_type="text", status="confirmed")
    db.add(job)
    db.flush()
    fields = {"title": title, "company_name": company, "salary_text": "20–30K/月·14薪",
              "location_text": "上海", "work_mode": "hybrid", "source_text": title + "\n合成 JD 原文"}
    version = DocumentVersion(account_id=fx["account_id"], document_id=job.id, version_no=1, content={"job_fields": fields})
    db.add(version)
    db.flush()
    pool = JobPoolItem(account_id=fx["account_id"], source_type="browser_capture" if source else "manual",
                       platform="boss" if source else None, source_url=f"https://www.zhipin.com/job_detail/{source}.html" if source else None,
                       source_url_hash=source, job_title=title, company_name=company, job_fields=fields,
                       job_document_id=job.id, job_document_version_id=version.id,
                       resume_version_id=fx["resume_version_id"], analysis_status="available",
                       created_at=created or NOW)
    db.add(pool)
    db.flush()
    analysis = Analysis(account_id=fx["account_id"], context_type="seeker_pool", status="available",
                        job_pool_item_id=pool.id, resume_version_id=fx["resume_version_id"], job_version_id=version.id,
                        result=result() if report is None else report, completed_at=completed or NOW,
                        created_at=created or NOW)
    db.add(analysis)
    db.flush()
    return pool, analysis


def opportunities(fx, **params):
    return checked(fx["client"].get("/api/v1/job-pool/opportunities", headers=fx["headers"], params=params))


def handling(fx, pool_id, status="pending", revision=0, key=None, confirm=False, **extras):
    return fx["client"].patch(f"/api/v1/job-pool/items/{pool_id}/handling-status",
                              headers={**fx["headers"], "Idempotency-Key": key or uuid.uuid4().hex},
                              json={"status": status, "base_revision": revision, "confirm_applied": confirm, **extras})


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("server.app.job_opportunities.utcnow", lambda: NOW)
    monkeypatch.setattr("server.app.api.now_utc", lambda: NOW)


def test_shanghai_30_day_boundaries_completion_not_capture(fx):
    from server.app.db import SessionLocal

    start = datetime(2026, 9, 11, 16, tzinfo=timezone.utc)
    end = datetime(2026, 10, 11, 16, tzinfo=timezone.utc)
    with SessionLocal() as db:
        before, _ = seed(fx, db, completed=start - timedelta(microseconds=1))
        first, _ = seed(fx, db, completed=start)
        last, _ = seed(fx, db, completed=end - timedelta(microseconds=1))
        future, _ = seed(fx, db, completed=end)
        old_pool, _ = seed(fx, db, created=NOW - timedelta(days=90), completed=NOW)
        ids = [row.public_id for row in (before, first, last, future, old_pool)]
        db.commit()
    view = opportunities(fx)
    assert {item["id"] for item in view["items"]} == set(ids[1:3] + ids[4:])
    assert view["range"] == {"days": 30, "from": "2026-09-12", "to": "2026-10-11"}
    assert view["timezone"] == "Asia/Shanghai"
    assert view["progress"]["completed"] == 5  # 原今日口径仍包括今天入池的岗位。
    assert checked(fx["client"].get(f"/api/v1/job-pool/items/{ids[0]}", headers=fx["headers"]))["id"] == ids[0]


@pytest.mark.parametrize("invalid", [None, {}, [], {"unexpected": True},
                                   {**result(), "conditions": None},
                                   {**result(), "dimensions": [{"requirements": None}]},
                                   {**result(), "dimensions": [{"requirements": [{"evidence": [None]}]}]}])
def test_same_source_latest_valid_report_and_jd_snapshot(fx, invalid):
    from server.app.db import SessionLocal

    source = "a" * 64
    with SessionLocal() as db:
        old, analysis = seed(fx, db, source=source, title="旧版工程师", completed=NOW - timedelta(days=1), created=NOW - timedelta(days=2))
        newest, broken = seed(fx, db, source=source, title="新版运营", completed=NOW, created=NOW)
        broken.result = invalid
        old_id, new_id, report_id = old.public_id, newest.public_id, analysis.public_id
        db.commit()
    checked(handling(fx, new_id, "verifying"))
    rows = opportunities(fx)["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == old_id and row["analysis_id"] == report_id
    assert row["job_title"] == "旧版工程师"
    assert row["handling"]["status"] == "verifying"
    assert row["source_update"]["updated"] and row["apply_pool_item_id"] == new_id
    detail = checked(fx["client"].get(f"/api/v1/analyses/{report_id}", headers=fx["headers"]))
    assert detail["job_snapshot"]["title"] == "旧版工程师"
    assert detail["job_snapshot"]["content"]["job_fields"]["source_text"].startswith("旧版工程师")
    assert detail["job_snapshot"]["source_update"]["updated"]
    assert not opportunities(fx, search="新版")["items"]


def test_same_source_identical_content_and_missing_snapshot_fields(fx):
    from server.app.db import SessionLocal
    from server.app.models import DocumentVersion

    with SessionLocal() as db:
        old, analysis = seed(fx, db, source="same-content", created=NOW - timedelta(days=1))
        latest, broken = seed(fx, db, source="same-content", created=NOW)
        broken.status = "failed"
        old_id, analysis_id = old.public_id, analysis.public_id
        db.commit()
    assert not opportunities(fx)["items"][0]["source_update"]["updated"]
    detail = checked(fx["client"].get(f"/api/v1/analyses/{analysis_id}", headers=fx["headers"]))
    assert not detail["job_snapshot"]["source_update"]["updated"]
    with SessionLocal() as db:
        frozen = db.get(DocumentVersion, analysis.job_version_id)
        frozen.content = {"job_fields": {"source_text": "未披露名称的旧 JD"}}
        db.commit()
    row = opportunities(fx)["items"][0]
    assert row["id"] == old_id and row["job_title"] is None and row["company_name"] is None
    assert row["source_update"]["updated"]
    assert not opportunities(fx, search="合成岗位")["items"]


def test_four_states_confirmation_undo_and_independent_revision(fx):
    from server.app.db import SessionLocal
    from server.app.models import JobPoolItem

    with SessionLocal() as db:
        pool, _ = seed(fx, db)
        pool_id, pool_revision = pool.public_id, pool.revision
        db.commit()
    assert handling(fx, pool_id, "applied").status_code == 422
    checked(handling(fx, pool_id, "verifying"))
    assert opportunities(fx)["items"][0]["handling"]["revision"] == 1
    assert handling(fx, pool_id, "applied", revision=0, confirm=True).status_code == 409
    applied = checked(handling(fx, pool_id, "applied", revision=1, confirm=True))
    assert applied["previous_status"] == "verifying" and applied["handling"]["revision"] == 2
    assert opportunities(fx)["counts"]["total"] == 0
    assert opportunities(fx, view="handled")["items"][0]["handling"]["status"] == "applied"
    checked(handling(fx, pool_id, "verifying", revision=2))  # 撤销
    checked(handling(fx, pool_id, "dismissed", revision=3))
    checked(handling(fx, pool_id, "pending", revision=4))  # 已处理恢复待投
    assert opportunities(fx)["items"][0]["handling"]["status"] == "pending"
    with SessionLocal() as db:
        assert db.scalar(select(JobPoolItem.revision).where(JobPoolItem.public_id == pool_id)) == pool_revision


def test_idempotency_does_not_overwrite_later_device_edit(fx):
    from server.app.db import SessionLocal

    with SessionLocal() as db:
        pool, _ = seed(fx, db)
        pool_id = pool.public_id
        db.commit()
    first = checked(handling(fx, pool_id, "verifying", key="stable-key"))
    assert checked(handling(fx, pool_id, "verifying", key="stable-key"))["replayed"]
    assert handling(fx, pool_id, "dismissed", key="stable-key").status_code == 409
    checked(handling(fx, pool_id, "dismissed", revision=first["handling"]["revision"]))
    replay = checked(handling(fx, pool_id, "verifying", key="stable-key"))
    assert replay["handling"]["status"] == "dismissed" and replay["handling"]["revision"] == 2
    assert replay["previous_status"] is None


def test_concurrent_requests_same_source_one_wins(fx):
    from server.app.db import SessionLocal
    from server.app.models import JobHandlingState

    with SessionLocal() as db:
        a, _ = seed(fx, db, source="b" * 64)
        b, _ = seed(fx, db, source="b" * 64, title="同源新版")
        ids = [a.public_id, b.public_id]
        db.commit()
    barrier = threading.Barrier(2)

    def update(index):
        barrier.wait(timeout=10)
        return handling(fx, ids[index], "verifying" if index else "dismissed").status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        codes = list(executor.map(update, range(2)))
    assert sorted(codes) == [200, 409]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(JobHandlingState).where(JobHandlingState.account_id == fx["account_id"])) == 1


def test_complete_collection_over_500_counts_sort_search_and_pagination(fx):
    from server.app.db import SessionLocal
    from server.app.models import Analysis, JobPoolItem

    with SessionLocal() as db:
        first, template = seed(fx, db, title="目标工程师")
        pools = [JobPoolItem(account_id=fx["account_id"], job_title="目标工程师", source_type="manual",
                            job_fields=first.job_fields, job_document_id=first.job_document_id,
                            job_document_version_id=first.job_document_version_id, analysis_status="available") for _ in range(522)]
        db.add_all(pools)
        db.flush()
        analyses = [Analysis(account_id=fx["account_id"], context_type="seeker_pool", status="available",
                            job_pool_item_id=pool.id, resume_version_id=fx["resume_version_id"], job_version_id=template.job_version_id,
                            completed_at=NOW, result=result(60 if index % 2 else 85)) for index, pool in enumerate(pools)]
        db.add_all(analyses)
        db.flush()
        expected = [template, *analyses]
        expected.sort(key=lambda row: (0 if row.result["ability_score"] >= 75 else 1, -row.id))
        expected_ids = [row.public_id for row in expected]
        db.commit()
    page = opportunities(fx, search="目标", limit=20)
    assert page["counts"]["total"] == 523
    assert page["counts"]["tiers"] == {"priority": 262, "consider": 261, "verify": 0, "defer": 0}
    found = [row["analysis_id"] for row in page["items"]]
    while page["page"]["has_more"]:
        page = opportunities(fx, search="目标", limit=100, cursor=page["page"]["next_cursor"])
        found.extend(row["analysis_id"] for row in page["items"])
    assert found == expected_ids
    filtered = opportunities(fx, tier="consider", search="目标", limit=100)
    assert filtered["counts"]["total"] == 261 and len(filtered["items"]) == 100
    assert filtered["counts"]["tiers"]["priority"] == 262
    stale = fx["client"].get("/api/v1/job-pool/opportunities", headers=fx["headers"],
                             params={"cursor": filtered["page"]["next_cursor"], "tier": "priority", "search": "目标"})
    assert stale.status_code == 409 and stale.json()["error"]["request_id"]


def test_status_never_calls_model_changes_balance_tasks_or_clicks(fx, monkeypatch):
    from server.app.db import SessionLocal
    from server.app.models import (
        ApplyClick,
        ModelCall,
        Task,
        UsageBalance,
        UsageLedger,
        UsageReservation,
    )

    monkeypatch.setattr(fx["provider"], "analyze", lambda *a, **kw: pytest.fail("Handling called model"))

    def facts(db):
        return ([db.scalar(select(func.count()).select_from(model).where(model.account_id == fx["account_id"])) for model in (Task, ModelCall, ApplyClick, UsageLedger, UsageReservation)],
                list(db.execute(select(UsageBalance.feature, UsageBalance.granted, UsageBalance.consumed, UsageBalance.reserved).where(UsageBalance.account_id == fx["account_id"]))))

    with SessionLocal() as db:
        a, _ = seed(fx, db)
        b, _ = seed(fx, db)  # 同标题的手工岗位不可合并。
        ids = [a.public_id, b.public_id]
        db.commit()
        before = facts(db)
    checked(handling(fx, ids[0], "applied", confirm=True))
    assert len(opportunities(fx)["items"]) == 1 and opportunities(fx)["items"][0]["id"] == ids[1]
    with SessionLocal() as db:
        assert facts(db) == before


def test_web_csrf_idempotency_isolation_and_deleted_job(fx):
    from server.app.db import SessionLocal

    with SessionLocal() as db:
        pool, _ = seed(fx, db)
        pool_id = pool.public_id
        db.commit()
    path = f"/api/v1/job-pool/items/{pool_id}/handling-status"
    body = {"status": "dismissed", "base_revision": 0}
    assert fx["client"].patch(path, headers={**fx["browser_headers"], "Idempotency-Key": "x"}, json=body).status_code == 401
    assert fx["client"].patch(path, headers={"Authorization": fx["headers"]["Authorization"], "Idempotency-Key": "x"}, json=body).status_code == 403
    assert fx["client"].patch(path, headers=fx["headers"], json=body).status_code == 400
    assert handling(fx, str(uuid.uuid4()), "dismissed").status_code == 404
    # 别的账号不能用当前会话读写，即使已知来源键和公开 ID。
    with SessionLocal() as db:
        pool.account_id = fx["account_id"] + 100000
        db.add(pool)
        db.commit()
    assert opportunities(fx)["items"] == []
    assert handling(fx, pool_id).status_code == 404


def test_deleted_source_cannot_mutate_but_live_sibling_keeps_state(fx):
    from server.app.db import SessionLocal
    from server.app.models import JobPoolItem

    with SessionLocal() as db:
        a, _ = seed(fx, db, source="c" * 64)
        b, _ = seed(fx, db, source="c" * 64)
        ids = [a.public_id, b.public_id]
        db.commit()
    checked(handling(fx, ids[0], "applied", confirm=True, key="before-delete"))
    impact = checked(fx["client"].get(f"/api/v1/job-pool/items/{ids[0]}/deletion-impact", headers=fx["headers"]))
    checked(fx["client"].delete(f"/api/v1/job-pool/items/{ids[0]}", headers={**fx["headers"], "If-Match": '"' + impact["impact_version"] + '"'}), 204)
    assert handling(fx, ids[0], "applied", confirm=True, key="before-delete").status_code == 404
    assert opportunities(fx)["items"] == []
    assert opportunities(fx, view="handled")["items"][0]["id"] == ids[1]
    with SessionLocal() as db:
        assert db.scalar(select(JobPoolItem.deleted_at).where(JobPoolItem.public_id == ids[0])) is not None


def test_apply_click_independent_and_automatic_worker_regression(fx):
    checked(enable(fx))
    draft = checked(capture(fx, job="opportunity-worker"), 201)
    assert run_worker(fx) >= 1
    row = opportunities(fx)["items"][0]
    assert row["id"] == draft["job_pool_item"]["id"] and row["handling"]["status"] == "pending"
    response = fx["client"].post(f"/api/v1/job-pool/items/{row['apply_pool_item_id']}/go-to-apply",
                                 headers={**fx["headers"], "Idempotency-Key": uuid.uuid4().hex},
                                 json={"click_token": row["apply_action"]["click_token"]}, follow_redirects=False)
    assert response.status_code == 303
    assert opportunities(fx)["items"][0]["handling"]["status"] == "pending"
    checked(handling(fx, row["id"], "applied", confirm=True))
    assert opportunities(fx)["items"] == []


def test_normalized_link_inherits_state_and_immutable_key(fx):
    from server.app.db import SessionLocal
    from server.app.models import JobPoolItem

    a = checked(capture(fx, job="normalized", source_url="https://www.zhipin.com/job_detail/same-source.html?tracking=a"), 201)
    a_id = a["job_pool_item"]["id"]
    checked(handling(fx, a_id, "dismissed"))
    b = checked(capture(fx, job="normalized", source_url="https://www.zhipin.com/job_detail/same-source.html?tracking=b",
                        job_description_text="新的完整 JD，负责 React 质量验证与交付"), 201)
    b_id = b["job_pool_item"]["id"]
    detail = checked(fx["client"].get(f"/api/v1/job-pool/items/{b_id}", headers=fx["headers"]))
    assert detail["handling"]["status"] == "dismissed" and a_id != b_id
    matching = checked(fx["client"].get("/api/v1/job-pool/items", params={"handling_status": "dismissed"}, headers=fx["headers"]))
    assert {row["id"] for row in matching["items"]} == {a_id, b_id}
    with SessionLocal() as db:
        row = db.scalar(select(JobPoolItem).where(JobPoolItem.public_id == b_id))
        row.opportunity_key = "manual:overwrite"
        with pytest.raises(ValueError, match="immutable"):
            db.flush()
        db.rollback()


def test_delete_before_automatic_worker_no_report_or_state_revival(fx):
    from server.app.db import SessionLocal
    from server.app.models import Analysis, JobHandlingState, JobPoolItem

    checked(enable(fx))
    captured = checked(capture(fx, job="deleted-opportunity"), 201)
    pool_id = captured["job_pool_item"]["id"]
    checked(handling(fx, pool_id, "verifying"))
    path = f"/api/v1/job-pool/items/{pool_id}"
    impact = checked(fx["client"].get(path + "/deletion-impact", headers=fx["headers"]))
    checked(fx["client"].delete(path, headers={**fx["headers"], "If-Match": '"' + impact["impact_version"] + '"'}), 204)
    run_worker(fx)
    assert opportunities(fx, view="all")["items"] == []
    assert handling(fx, pool_id, "pending", revision=1).status_code == 404
    with SessionLocal() as db:
        row = db.scalar(select(JobPoolItem).where(JobPoolItem.public_id == pool_id))
        assert row.deleted_at is not None
        assert db.scalar(select(Analysis.result).where(Analysis.job_pool_item_id == row.id)) is None
        state = db.scalar(select(JobHandlingState).where(JobHandlingState.account_id == fx["account_id"]))
        assert state.status == "verifying" and state.revision == 1  # 仅保留无正文的历史元数据。
