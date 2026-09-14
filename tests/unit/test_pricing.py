from decimal import Decimal

from server.app.pricing import calculate_cost, decimal_value, token_upper_bound


def test_decimal_cost_does_not_double_count_cached_input() -> None:
    assert calculate_cost(
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
        output_tokens=100_000,
        input_usd_per_million="1.00",
        cached_input_usd_per_million="0.25",
        output_usd_per_million="2.00",
    ) == Decimal("0.90000000")


def test_unknown_price_is_not_zero() -> None:
    assert decimal_value("not-a-number") is None
    assert token_upper_bound(
        input_tokens=10,
        output_tokens=10,
        input_usd_per_million=None,
        output_usd_per_million="1",
    ) is None
