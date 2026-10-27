from types import SimpleNamespace

import pytest
from defusedxml import ElementTree

from sales_backend.security.local_qr import company_entry_scene, company_web_origin, qr_svg


@pytest.mark.parametrize(
    "code,expected",
    [
        ("demo-sales-workspace", "c=demo-sales-workspace"),
        ("legacy_code.1", "c=legacy_code.1"),
        ("a" * 30, "c=" + "a" * 30),
        ("a" * 31, None),
        ("", None),
        ("company&other=value", None),
        ("company=other", None),
        ("公司一", None),
        ("company%26other", None),
        ("company/name", None),
        ("company\x00", None),
    ],
)
def test_mini_program_scene_never_splits_or_misdecodes_historical_company_codes(code, expected):
    assert company_entry_scene(code) == expected


def test_local_qr_returns_self_contained_svg_without_embedding_secret_or_external_resource():
    uri = "otpauth://totp/SalesBuddy?secret=JBSWY3DPEHPK3PXP&issuer=SalesBuddy"
    rendered = qr_svg(uri)
    document = ElementTree.fromstring(rendered)
    assert document.tag == "{http://www.w3.org/2000/svg}svg"
    assert document.find("{http://www.w3.org/2000/svg}path") is not None
    assert "secret=" not in rendered and "otpauth" not in rendered
    assert "href=" not in rendered and "script" not in rendered and "foreignObject" not in rendered
    assert rendered == qr_svg(uri)
    assert rendered != qr_svg(uri + "2")


@pytest.mark.parametrize(
    "configured",
    [
        "http://public.example",
        "https://user:pass@public.example",
        "https://public.example/path",
        "https://public.example?redirect=1",
        "https://public.example#fragment",
        "https://public.example:bad",
        "https://public.example:70000",
        "https://public.example\\evil",
        "https://public.example /",
        "javascript:alert(1)",
        "//public.example",
    ],
)
def test_company_qr_rejects_non_origin_or_insecure_production_configuration(configured):
    settings = SimpleNamespace(app_env="production", public_web_origin=configured)
    assert company_web_origin(settings, "https://attacker.invalid/") is None


@pytest.mark.parametrize(
    "app_env,configured,request_origin,expected",
    [
        ("production", "https://sales.example:8443/", "https://attacker.invalid/", "https://sales.example:8443"),
        ("production", "", "https://attacker.invalid/", None),
        ("production", "", "http://127.0.0.1:18800/", None),
        ("development", "", "http://127.0.0.1:18800/", "http://127.0.0.1:18800"),
        ("test", "", "http://[::1]:18800/", "http://[::1]:18800"),
        ("test", "", "https://test.invalid/", None),
        ("test", "", "http://localhost.attacker.invalid/", None),
    ],
)
def test_company_qr_origin_comes_from_config_or_development_loopback_only(
    app_env, configured, request_origin, expected
):
    assert (
        company_web_origin(SimpleNamespace(app_env=app_env, public_web_origin=configured), request_origin) == expected
    )
