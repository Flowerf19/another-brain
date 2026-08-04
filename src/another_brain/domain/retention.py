"""Durable importance-to-expiry policy."""

TTL_SECONDS = {
    5: 365 * 86_400,
    4: 180 * 86_400,
    3: 90 * 86_400,
    2: 30 * 86_400,
    1: 7 * 86_400,
}


def expires_at_ms(importance: int, now_ms: int) -> int:
    return now_ms + TTL_SECONDS[importance] * 1_000
