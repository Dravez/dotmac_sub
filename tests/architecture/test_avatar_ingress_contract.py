"""Keep the avatar multipart cap at its exact public ingress location."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AVATAR_PATH = "/api/v1/auth/me/avatar"


def _location(source: str) -> str:
    marker = f"location = {AVATAR_PATH} {{"
    assert source.count(marker) == 1
    return source.split(marker, 1)[1].split("}", 1)[0]


def test_avatar_upload_cap_is_exact_and_chunked_safe_in_both_edge_configs() -> None:
    for relative_path, upstream in (
        ("nginx/selfcare.dotmac.io.conf", "http://dotmac_sub_app"),
        ("deploy/nginx/selfcare.dotmac.io", "http://127.0.0.1:8001"),
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        location = _location(source)
        assert "client_max_body_size 3m;" in location
        assert "proxy_request_buffering on;" in location
        assert f"proxy_pass {upstream};" in location
        for header in ("Host", "X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
            assert f"proxy_set_header {header} " in location
        assert "proxy_connect_timeout 60s;" in location
        assert "proxy_send_timeout " in location
        assert "proxy_read_timeout " in location
        assert source.count("client_max_body_size 3m;") == 1


def test_avatar_policy_is_checked_at_startup_before_serving() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    preflight = source.split("def _startup_preflight()", 1)[1].split(
        "def _prewarm_admin_dashboard()", 1
    )[0]
    assert "require_compatible_avatar_policy()" in preflight
    lifespan = source.split("async def lifespan(", 1)[1].split("app = FastAPI", 1)[0]
    assert lifespan.index("_startup_preflight()") < lifespan.index(
        "_load_deferred_api_routers(app)"
    )
