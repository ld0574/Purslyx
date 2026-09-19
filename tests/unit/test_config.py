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


def test_other_postgres_host_is_rejected() -> None:
    configured = Settings(database_url="postgresql+psycopg://u:p@10.10.10.202:5432/purslyx")
    try:
        configured.require_postgres_url()
    except RuntimeError as exc:
        assert "201" in str(exc)
    else:
        raise AssertionError("the local environment must stay on the 201 PostgreSQL host")


def test_configured_container_database_host_is_accepted() -> None:
    configured = Settings(
        database_url="postgresql+psycopg://u@host.docker.internal:5432/purslyx",
        allowed_database_hosts="host.docker.internal",
    )
    assert configured.require_postgres_url().startswith("postgresql+")


def test_execution_mode_is_explicit() -> None:
    Settings(execution_mode="inline").require_execution_mode()
    Settings(execution_mode="worker").require_execution_mode()
    try:
        Settings(execution_mode="queue").require_execution_mode()
    except RuntimeError as exc:
        assert "inline" in str(exc)
    else:
        raise AssertionError("unsupported execution mode must be rejected")
