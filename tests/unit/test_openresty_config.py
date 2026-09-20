"""OpenResty reverse-proxy safety contract tests."""

from pathlib import Path

CONFIG = Path(__file__).parents[2] / "infra" / "openresty" / "purslyx.conf"


def test_openresty_trusts_only_cloudflare_real_ip_ranges() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert "real_ip_header CF-Connecting-IP;" in text
    assert "real_ip_recursive on;" in text
    assert "set_real_ip_from 0.0.0.0/0;" not in text
    assert "set_real_ip_from ::/0;" not in text
    assert text.count("set_real_ip_from ") >= 21


def test_openresty_forwards_the_normalized_client_ip() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert "proxy_set_header X-Real-IP $remote_addr;" in text
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in text


def test_openresty_uses_one_canonical_web_origin() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert "server_name www.purslyx.com;" in text
    assert "return 301 https://purslyx.com$request_uri;" in text
