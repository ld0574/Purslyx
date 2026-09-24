"""Vue 工程化前端入口和关键契约回归。"""

import json
from pathlib import Path

from server.app.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "web"


def read(path: str) -> str:
    return (WEB_ROOT / path).read_text(encoding="utf-8")


def test_vue_application_has_all_product_routes_and_views() -> None:
    """首版业务必须有独立 Vue 路由和实现文件，不能退回单个首页样稿。"""

    expected_views = {
        "HomeView.vue",
        "GuideView.vue",
        "AuthView.vue",
        "DashboardView.vue",
        "MaterialsView.vue",
        "PoolView.vue",
        "ReportView.vue",
        "RewriteView.vue",
        "VariantsView.vue",
        "InterviewView.vue",
        "TasksView.vue",
        "UsageView.vue",
        "StatsView.vue",
        "PermissionDeniedView.vue",
        "admin/AdminMetricsView.vue",
        "admin/AdminUsersView.vue",
        "admin/AdminRolesView.vue",
        "admin/AdminUsageView.vue",
        "admin/AdminJobPoolView.vue",
        "admin/AdminLogsView.vue",
    }
    for name in expected_views:
        assert (WEB_ROOT / "src" / "views" / name).is_file(), name
    router = read("src/router.ts")
    expected_routes = {
        "/app/login",
        "/app/register",
        "/app/seeker/resume",
        "/app/seeker/pool",
        "/app/seeker/rewrite",
        "/app/seeker/variants",
        "/app/seeker/interview",
        "/app/admin/metrics",
        "/app/admin/users",
        "/app/admin/roles",
        "/app/admin/usage",
        "/app/admin/job-pool",
        "/app/admin/logs",
    }
    assert all(route in router for route in expected_routes)
    assert "/app/:role(seeker|recruiter)/dashboard" in router
    assert "/app/:role(seeker|recruiter)/report" in router
    assert "/app/:role(seeker|recruiter)/tasks" in router
    assert "/app/:role(seeker|recruiter)/usage" in router
    assert "/app/:role(seeker|recruiter)/stats" in router
    assert "/guide" in router


def test_server_returns_spa_entry_for_every_frontend_entry_path() -> None:
    server_paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/" in server_paths
    assert "/guide" in server_paths
    assert "/app/{path:path}" in server_paths
    assert "/job-pool/items" in server_paths


def test_vite_and_typescript_are_the_production_frontend_contract() -> None:
    package = json.loads(read("package.json"))
    assert package["dependencies"]["vue"].startswith("^")
    assert "pinia" in package["dependencies"]
    assert "vue-router" in package["dependencies"]
    assert "element-plus" in package["dependencies"]
    assert "vue-tsc --noEmit" in package["scripts"]["build"]
    assert "vitest run" in package["scripts"]["test"]
    assert (WEB_ROOT / "package-lock.json").is_file()
    index = read("index.html")
    assert 'id="app"' in index
    assert 'src="/src/main.ts"' in index
    main = read("src/main.ts")
    assert "createApp(App)" in main
    assert "createPinia()" in main
    assert "app.use(router)" in main


def test_api_client_preserves_auth_csrf_and_idempotency_boundaries() -> None:
    client = read("src/services/api.ts")
    assert 'headers.Authorization = `Bearer ${session.token}`' in client
    assert 'headers["X-CSRF-Token"] = csrf' in client
    assert 'readCookie("purslyx_csrf")' in client
    assert 'headers["Idempotency-Key"] = options.idempotencyKey' in client
    assert 'credentials: "same-origin"' in client
    assert "redirect: options.redirect" in client


def test_key_pages_keep_browser_and_business_contracts() -> None:
    home = read("src/views/HomeView.vue")
    guide = read("src/views/GuideView.vue")
    shell = read("src/components/AppShell.vue")
    footer = read("src/components/SiteFooter.vue")
    materials = read("src/views/MaterialsView.vue")
    pool = read("src/views/PoolView.vue")
    interview = read("src/views/InterviewView.vue")
    tasks = read("src/views/TasksView.vue")
    admin = "\n".join(read(f"src/views/admin/{name}") for name in ["AdminUsersView.vue", "AdminRolesView.vue", "AdminUsageView.vue", "AdminJobPoolView.vue", "AdminLogsView.vue"])
    assert 'src="/purslyx-logo.png"' in home
    assert 'alt="Purslyx 品牌标志"' in home
    assert 'to="/guide"' in home
    assert 'class="guide-link active"' in guide
    assert "首次匹配完整流程" in guide
    assert "同步 Purslyx" in guide
    assert "合并为一次模型请求" in guide
    assert (WEB_ROOT / "public" / "purslyx-logo.png").is_file()
    assert 'src="/purslyx-logo.png"' in shell
    assert 'src="/purslyx-logo.png"' in read("src/views/AuthView.vue")
    assert 'src="/favicon.svg"' not in shell
    assert 'src="/favicon.svg"' not in read("src/views/AuthView.vue")
    assert 'class="side-brand"' not in shell
    assert "SiteFooter" in shell
    assert "https://github.com/ld0574/Purslyx" in footer
    assert "Copyright © 2026 Purslyx Team" in footer
    for selector in ["formal-document-form", "document-draft-review", "preference-form", "recruiter-analysis-form"]:
        assert f'id="{selector}"' in materials
    assert 'class="materials-list-layout"' in materials
    assert 'class="materials-dialog' in materials
    assert "新增简历" in materials
    assert "新增岗位期望" in materials
    assert 'id="pool-form"' in pool
    assert "系统会自动使用全部有效岗位期望" in pool
    assert "选择匹配简历" in pool
    assert 'id="pool-search"' in pool
    assert 'class="pool-filter-panel"' in pool
    assert 'class="pool-advanced-filters"' in pool
    assert 'id="pool-salary-min"' in pool
    assert 'id="pool-created-from"' in pool
    assert 'id="pool-score-min"' in pool
    assert 'value="match_score"' in pool
    assert "<dialog" in pool
    assert "requestBatchMatch" in pool
    assert 'class="batch-selection"' in pool
    assert "详情" in pool
    assert "onBeforeUnmount" in pool
    assert "setInterval" in pool
    assert "scrollIntoView" in pool
    assert "analysisStatusText" in pool
    assert "下一页" in pool
    assert "missing_conditions" not in pool
    assert 'type: "document_version", job_document_version_id: jobVersion.id' in pool
    assert "/api/v1/job-pool/items/batch-analyze" in pool
    assert "preference_version_id" not in pool
    assert 'redirect: "manual"' in pool
    assert 'id="answer-form"' in interview
    assert 'data-action="retry-task"' in tasks
    for selector in ["admin-user-search", "admin-role-form", "admin-grant-form"]:
        assert f'id="{selector}"' in admin
    assert 'data-action="open-admin-user"' in admin
    assert 'data-action="export-logs"' in admin
    assert 'data-action="admin-log-type"' in admin
    assert 'id="admin-job-pool-search"' in admin
    assert "/api/v1/admin/job-pool/items" in admin
    assert "完整 JD 正文" in admin


def test_fastapi_serves_vite_dist_without_legacy_fallback() -> None:
    server = (PROJECT_ROOT / "src" / "server" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'WEB_DIST_ROOT = WEB_ROOT / "dist"' in server
    assert 'app.mount("/assets", StaticFiles' in server
    assert 'WEB_LOGO = WEB_DIST_ROOT / "purslyx-logo.png"' in server
    assert '@app.get("/purslyx-logo.png"' in server
    assert '@app.get("/app/{path:path}"' in server
    assert "WEB_BUILD_MISSING" in server
    assert "WEB_PAGE_ROOT" not in server


def test_admin_job_pool_permissions_are_seeded() -> None:
    seed = (PROJECT_ROOT / "src" / "server" / "app" / "seed.py").read_text(encoding="utf-8")
    assert '"admin.job_pool.read"' in seed
    assert '"admin.job_pool.manage"' in seed
