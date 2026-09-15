"""多页面前端入口的静态契约回归。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "src" / "web"


def test_workbench_has_independent_page_entries() -> None:
    """每个首版工作台模块都必须有独立 HTML 入口，而不是只在单页里切换。"""

    expected_pages = {
        "home.html",
        "login.html",
        "register.html",
        "seeker-dashboard.html",
        "seeker-resume.html",
        "seeker-pool.html",
        "seeker-report.html",
        "seeker-rewrite.html",
        "seeker-variants.html",
        "seeker-interview.html",
        "seeker-tasks.html",
        "seeker-usage.html",
        "seeker-stats.html",
        "recruiter-dashboard.html",
        "recruiter-materials.html",
        "recruiter-report.html",
        "recruiter-tasks.html",
        "recruiter-usage.html",
        "recruiter-stats.html",
        "admin-metrics.html",
        "admin-users.html",
        "admin-roles.html",
        "admin-usage.html",
        "admin-logs.html",
    }
    page_root = WEB_ROOT / "pages"
    assert {path.name for path in page_root.glob("*.html")} >= expected_pages
    for name in expected_pages:
        content = (page_root / name).read_text(encoding="utf-8")
        assert 'id="app"' in content
        assert 'data-route-view=' in content
        assert "<h1>" in content
        assert content.count("<h2>") >= 2
        assert 'href="/assets/styles.css"' in content
        assert 'src="/assets/workbench.js"' in content


def test_workbench_navigation_uses_real_page_loads() -> None:
    """多页面菜单必须进入真实 URL，不能继续只在当前文档里替换内容。"""

    script_text = (WEB_ROOT / "assets" / "workbench.js").read_text(encoding="utf-8")
    assert 'if (pageNode.matches("a[href]")) return;' in script_text
    assert "window.location.assign(routeFor(pageNode.dataset.page));" in script_text


def test_public_home_is_a_product_entry() -> None:
    """公共首页只提供正式产品能力与认证入口，不再承载匿名业务短链路。"""

    home_text = (WEB_ROOT / "pages" / "home.html").read_text(encoding="utf-8")
    script_text = (WEB_ROOT / "assets" / "workbench.js").read_text(encoding="utf-8")
    assert 'href="/app/login"' in home_text
    assert 'href="/app/register"' in home_text
    assert "function homePage" in script_text
    assert 'id="demo-form"' not in home_text
    assert 'id="demo-form"' not in script_text
    assert "/api/v1/demo" not in script_text
    assert "SDD" not in home_text
    assert "SDD" not in script_text


def test_shared_workbench_assets_are_executable_contract() -> None:
    """共享资源必须存在，所有页面才能复用同一份 API 和视觉实现。"""

    stylesheet = WEB_ROOT / "assets" / "styles.css"
    script = WEB_ROOT / "assets" / "workbench.js"
    assert stylesheet.is_file() and "--brand:" in stylesheet.read_text(encoding="utf-8")
    script_text = script.read_text(encoding="utf-8")
    assert "function routeFor" in script_text
    assert "async function loadWorkspace" in script_text
    assert "localStorage.getItem(SESSION_KEY)" in script_text
