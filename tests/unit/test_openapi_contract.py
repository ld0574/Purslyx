"""字段级 API 文档中的端点必须全部存在于 OpenAPI。"""

from __future__ import annotations

import re
from pathlib import Path

from server.app.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_DOCS = PROJECT_ROOT / "docs/02-technical/api"
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _normalise_path(path: str) -> str:
    """参数名可以因实现语义细化而不同，路径层级必须一致。"""

    return re.sub(r"\{[^}]+\}", "{}", path)


def test_every_documented_endpoint_exists_in_openapi() -> None:
    implemented = {
        (method.upper(), _normalise_path(path))
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in METHODS
    }
    documented: set[tuple[str, str]] = set()
    for path in API_DOCS.glob("*API设计.md"):
        content = path.read_text(encoding="utf-8")
        documented.update(
            (method, _normalise_path(endpoint))
            for method, endpoint in re.findall(
                r"\b(GET|POST|PUT|PATCH|DELETE)\s+`(/api/v1/[^` ?]+)",
                content,
            )
        )
    assert len(documented) >= 75
    assert documented <= implemented


def test_openapi_has_full_business_surface_and_no_demo_routes() -> None:
    paths = set(app.openapi()["paths"])
    business_paths = {path for path in paths if path.startswith("/api/v1/")}
    assert len(business_paths) >= 77
    assert not any(path.startswith("/api/v1/demo") for path in business_paths)
