"""统一的 UTC 时钟。

单独抽出来是为了让“到期”可以被测试和演示：业务代码只调用 ``now_utc``，
测试可以替换这个函数来跨过到期时间。
"""
from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
