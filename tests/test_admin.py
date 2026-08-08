import io
import json
import urllib.error

import pytest

from proctor import admin
from proctor.pricing import PriceBook
from proctor.term import Style


class FakeResponse(io.StringIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_urlopen(pages):
    """Return an urlopen stand-in that serves ``pages`` in order."""
    remaining = list(pages)
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(json.dumps(remaining.pop(0)))

    opener.calls = calls
    return opener


USAGE_PAGE = {
    "data": [
        {
            "results": [
                {
                    "model": "claude-opus-5",
                    "uncached_input_tokens": 4_000_000,
                    "cache_read_input_tokens": 1_000_000,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 500_000,
                        "ephemeral_1h_input_tokens": 0,
                    },
                    "output_tokens": 200_000,
                }
            ]
        }
    ],
    "has_more": False,
}

COST_PAGE = {"data": [{"results": [{"amount": "12345"}]}], "has_more": False}


def test_resolve_key_requires_an_admin_prefix(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-api03-not-admin")
    with pytest.raises(admin.AdminError, match="must start with"):
        admin.resolve_key()


def test_resolve_key_reports_a_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_ADMIN_KEY", raising=False)
    with pytest.raises(admin.AdminError, match="no admin key"):
        admin.resolve_key()


def test_fetch_aggregates_usage_and_converts_cents(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-admin-test")
    monkeypatch.setattr(
        admin.urllib.request, "urlopen", fake_urlopen([USAGE_PAGE, COST_PAGE])
    )

    report = admin.fetch(days=30)
    entry = report.usage[0]

    assert entry.model == "claude-opus-5"
    assert entry.uncached_input == 4_000_000
    assert entry.cache_write == 500_000
    assert entry.cache_hit_rate == pytest.approx(1 / 5.5)
    assert report.total_cost_usd == pytest.approx(123.45)


def test_fetch_follows_pagination(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-admin-test")
    first = {"data": USAGE_PAGE["data"], "has_more": True, "next_page": "cursor-2"}
    second = {"data": USAGE_PAGE["data"], "has_more": False}
    opener = fake_urlopen([first, second, COST_PAGE])
    monkeypatch.setattr(admin.urllib.request, "urlopen", opener)

    report = admin.fetch(days=7)
    assert report.usage[0].uncached_input == 8_000_000
    assert "page=cursor-2" in opener.calls[1]


def test_http_errors_become_readable_admin_errors(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-admin-test")

    def boom(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"error":"nope"}')
        )

    monkeypatch.setattr(admin.urllib.request, "urlopen", boom)
    with pytest.raises(admin.AdminError, match="HTTP 403"):
        admin.fetch(days=7)


def test_network_errors_become_readable_admin_errors(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY", "sk-ant-admin-test")

    def boom(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(admin.urllib.request, "urlopen", boom)
    with pytest.raises(admin.AdminError, match="could not reach"):
        admin.fetch(days=7)


def test_advisories_fire_only_on_low_hit_rates():
    prices = PriceBook()
    low = admin.OrgReport(
        days=30,
        usage=[
            admin.ModelUsage("claude-opus-5", uncached_input=9_000_000, cache_read=100)
        ],
    )
    assert admin.advisories(low, prices)

    high = admin.OrgReport(
        days=30,
        usage=[
            admin.ModelUsage(
                "claude-opus-5", uncached_input=1_000_000, cache_read=9_000_000
            )
        ],
    )
    assert admin.advisories(high, prices) == []


def test_render_produces_a_table():
    report = admin.OrgReport(
        days=30,
        usage=[admin.ModelUsage("claude-opus-5", uncached_input=10, cache_read=90)],
        total_cost_usd=42.0,
    )
    buffer = io.StringIO()
    admin.render(report, buffer, Style(False))
    output = buffer.getvalue()

    assert "claude-opus-5" in output
    assert "$42.00" in output
    assert "90%" in output
