from server.app.config import Settings


def test_only_postgres_url_is_accepted() -> None:
    configured = Settings(database_url="postgresql+psycopg://u:p@10.10.10.201:5432/purslyx")
    assert configured.require_postgres_url().startswith("postgresql+")


def test_sqlite_is_rejected() -> None:
    configured = Settings(database_url="sqlite:///./purslyx.db")
    try:
        configured.require_postgres_url()
    except RuntimeError as exc:
        assert "SQLite" in str(exc)
    else:
        raise AssertionError("SQLite URL must be rejected")


def test_missing_database_url_is_rejected() -> None:
    configured = Settings(database_url="")
    try:
        configured.require_postgres_url()
    except RuntimeError as exc:
        assert "DATABASE_URL" in str(exc)
    else:
        raise AssertionError("missing DATABASE_URL must be rejected")
