"""模型价格与成本的纯计算函数。

这里不依赖数据库，便于在不连接 201 PostgreSQL 的单元测试中验证精度、缓存输入和
推理 token 的计费规则。数据库服务只负责选择价格版本和持久化结果。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Any


MILLION = Decimal("1000000")
MONEY_QUANTUM = Decimal("0.00000001")


def decimal_value(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    """把外部数字安全转换成 Decimal，拒绝 NaN、无穷和无法解释的字符串。"""

    if value in (None, ""):
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default
    if not result.is_finite() or result < 0:
        return default
    return result


def calculate_cost(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    input_usd_per_million: Any,
    cached_input_usd_per_million: Any = None,
    output_usd_per_million: Any,
    cached_input_tokens: int | None = None,
) -> Decimal | None:
    """按供应商价格计算一次调用成本。

    `cached_input_tokens` 是输入 token 的子集，不会与普通输入重复计费；缺少价格或
    token 解释不了时返回 None，由预算层转为未知成本持有，而不是当作零成本。
    """

    if input_tokens is None or output_tokens is None or input_tokens < 0 or output_tokens < 0:
        return None
    input_price = decimal_value(input_usd_per_million)
    output_price = decimal_value(output_usd_per_million)
    if input_price is None or output_price is None:
        return None
    cached = cached_input_tokens or 0
    if cached < 0 or cached > input_tokens:
        return None
    cached_price = decimal_value(cached_input_usd_per_million, default=input_price)
    if cached_price is None:
        return None
    uncached_tokens = input_tokens - cached
    cost = (
        Decimal(uncached_tokens) * input_price
        + Decimal(cached) * cached_price
        + Decimal(output_tokens) * output_price
    ) / MILLION
    return cost.quantize(MONEY_QUANTUM)


def token_upper_bound(
    *,
    input_tokens: int,
    output_tokens: int,
    input_usd_per_million: Any,
    output_usd_per_million: Any,
) -> Decimal | None:
    """计算预算预留上界，向上取整到金额精度以避免浮点低估。"""

    value = calculate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
    )
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_UP) if value is not None else None
