from datetime import datetime, timezone

import pytest

from proctor.pricing import Price, PriceBook, PriceRule
from proctor.types import Turn


@pytest.fixture
def prices():
    return PriceBook()


@pytest.mark.parametrize(
    ("model", "expected_input", "expected_output"),
    [
        ("claude-opus-5", 5.0, 25.0),
        ("claude-opus-4-8", 5.0, 25.0),
        ("claude-opus-4-5-20251101", 5.0, 25.0),
        ("claude-fable-5", 10.0, 50.0),
        ("claude-mythos-5", 10.0, 50.0),
        ("claude-mythos-preview", 10.0, 50.0),
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-haiku-4-5-20251001", 1.0, 5.0),
        ("claude-3-5-haiku-20241022", 0.8, 4.0),
    ],
)
def test_known_models_price_correctly(prices, model, expected_input, expected_output):
    price = prices.for_model(model)
    assert price.input == expected_input
    assert price.output == expected_output


def test_legacy_opus_is_not_swallowed_by_the_generic_opus_rule(prices):
    """Opus 4.0/4.1 and Claude 3 Opus predate the Opus-tier price cut."""
    assert prices.for_model("claude-opus-4-1-20250805").input == 15.0
    assert prices.for_model("claude-opus-4-20250514").input == 15.0
    assert prices.for_model("claude-3-opus-20240229").input == 15.0
    # ...but current Opus still gets the current rate.
    assert prices.for_model("claude-opus-5").input == 5.0


def test_sonnet_5_intro_pricing_expires(prices):
    before = datetime(2026, 8, 15, tzinfo=timezone.utc)
    after = datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert prices.for_model("claude-sonnet-5", before).input == 2.0
    assert prices.for_model("claude-sonnet-5", after).input == 3.0


def test_unknown_model_falls_back(prices):
    assert prices.for_model("some-future-model") is prices.fallback


def test_cache_multipliers_follow_the_published_ratios():
    price = Price.from_input_output(5.0, 25.0)
    assert price.cache_write_5m == 6.25
    assert price.cache_write_1h == 10.0
    assert price.cache_read == 0.5


def test_turn_cost_sums_every_billing_channel(prices):
    turn = Turn(
        ts=None,
        model="claude-opus-5",
        input_tokens=1_000_000,
        cache_write_5m=1_000_000,
        cache_write_1h=1_000_000,
        cache_read=1_000_000,
        output_tokens=1_000_000,
    )
    # 5 + 6.25 + 10 + 0.5 + 25
    assert prices.turn_cost(turn) == pytest.approx(46.75)


def test_overrides_take_precedence_over_builtins():
    book = PriceBook.from_overrides({"opus": {"input": 1.0, "output": 2.0}})
    assert book.for_model("claude-opus-5").input == 1.0
    # Non-overridden models keep their built-in rates.
    assert book.for_model("claude-haiku-4-5").input == 1.0
    assert book.for_model("claude-sonnet-4-6").output == 15.0


def test_explicit_cache_rates_in_overrides_are_respected():
    book = PriceBook.from_overrides(
        {
            "custom": {
                "input": 1.0,
                "output": 2.0,
                "cache_write_5m": 9.0,
                "cache_write_1h": 9.5,
                "cache_read": 0.01,
            }
        }
    )
    assert book.for_model("custom-model").cache_write_5m == 9.0


def test_rule_with_until_ignores_naive_timestamps_gracefully():
    rule = PriceRule("x", Price.from_input_output(1, 1), until=datetime(2026, 1, 1))
    assert rule.applies("x", datetime(2025, 1, 1)) is True
    assert rule.applies("x", datetime(2027, 1, 1)) is False
