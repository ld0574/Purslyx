"""逻辑引用巡检的规则覆盖与持久化契约。"""

from __future__ import annotations

from server.app.integrity import ACCOUNT_OWNED_MODELS, REFERENCE_RULES, run_integrity_scan
from server.app.models import Base, IntegrityScanFinding, IntegrityScanRun


def test_every_account_owned_model_is_scanned() -> None:
    expected = {
        mapper.class_
        for mapper in Base.registry.mappers
        if "account_id" in mapper.columns and mapper.class_ not in {IntegrityScanFinding}
    }
    assert expected <= set(ACCOUNT_OWNED_MODELS)


def test_reference_rules_cover_core_cross_account_boundaries() -> None:
    names = {rule.name for rule in REFERENCE_RULES}
    assert {
        "document_version.document",
        "job_pool.resume_version",
        "analysis.job_pool",
        "rewrite.analysis",
        "variant.job_pool",
        "interview.analysis",
        "usage_reservation.task",
        "checkpoint.task",
        "account_role.role",
    } <= names
    assert len(names) == len(REFERENCE_RULES)
    assert len(names) >= 55


def test_scan_persists_only_bounded_findings(monkeypatch) -> None:
    findings = [
        {
            "finding_type": "orphan_reference",
            "resource_type": "documents",
            "resource_public_id": f"resource-{index}",
            "details": {"rule": "sample", "reference_value": index},
        }
        for index in range(3)
    ]
    monkeypatch.setattr(
        "server.app.integrity.collect_integrity_findings",
        lambda _db: (findings, {"checked_rows": 10, "checked_rules": 2}),
    )

    added: list[object] = []

    class FakeSession:
        def add(self, item) -> None:
            added.append(item)

        def flush(self) -> None:
            for item in added:
                if isinstance(item, IntegrityScanRun) and item.id is None:
                    item.id = 7

    run = run_integrity_scan(FakeSession(), max_persisted_findings=2)  # type: ignore[arg-type]
    persisted = [item for item in added if isinstance(item, IntegrityScanFinding)]
    assert run.status == "findings"
    assert run.summary == {
        "checked_rows": 10,
        "checked_rules": 2,
        "finding_count": 3,
        "persisted_finding_count": 2,
        "truncated": True,
        "by_type": {"orphan_reference": 3},
    }
    assert len(persisted) == 2
    assert all(item.scan_run_id == 7 for item in persisted)
