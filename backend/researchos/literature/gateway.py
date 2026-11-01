"""One guarded call to one source: cache, cross-process pacing, cooldown, quota, retry.

Pacing state lives in PostgreSQL so that several API processes share it. arXiv and Crossref
are serialized with a session-level advisory lock held for the whole request; the lock is
tied to the connection, so a crashed process releases it. Everything here runs on a dedicated
autocommit connection, so no transaction is open during the network call.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

from researchos.config import Settings
from researchos.db import get_engine
from researchos.literature import sources as http
from researchos.literature.types import BadResponse, Record, SourceOutcome

log = logging.getLogger("researchos.literature")

LOCK_KEYS = {"arxiv": 740_001, "crossref": 740_002}
NAMES = {"arxiv": "arXiv", "openalex": "OpenAlex", "crossref": "Crossref"}
OPENALEX_SPACING = 0.2
DEFAULT_COOLDOWN = 60.0
DEFAULT_CROSSREF_INTERVAL = 1.0
DEFAULT_SEARCH_COST_USD = 0.001
RETRY_PAUSE = 0.3

Parse = Callable[[bytes], tuple[list[Record], int | None]]


def cache_key(source: str, op: str, params: dict) -> str:
    raw = json.dumps([source, op, params], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _seconds(headers, name: str) -> float | None:
    raw = headers.get(name, "").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def _crossref_interval(headers) -> float | None:
    """`x-rate-limit-interval: 1s` with `x-rate-limit-limit: 1` means one request per second."""
    raw = headers.get("x-rate-limit-interval", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", raw)
    limit = _seconds(headers, "x-rate-limit-limit")
    if not match or not limit or limit <= 0:
        return None
    unit = {"ms": 0.001, "s": 1.0, "m": 60.0}[match.group(2)]
    return float(match.group(1)) * unit / limit


# --- cache -------------------------------------------------------------------------


def _cache_get(conn: Connection, key: str) -> SourceOutcome | None:
    row = conn.execute(
        text(
            "SELECT source, payload, total, fetched_at FROM search_cache "
            "WHERE key = :key AND expires_at > now()"
        ),
        {"key": key},
    ).first()
    if row is None:
        return None
    return SourceOutcome(
        source=row.source,
        status="ok",
        records=[Record.from_dict(r) for r in row.payload["records"]],
        total=row.total,
        cached=True,
        fetched_at=_iso(row.fetched_at),
    )


def _cache_put(conn: Connection, key: str, source: str, records, total, ttl: int) -> str | None:
    if ttl <= 0:
        return None
    payload = json.dumps({"records": [r.to_dict() for r in records]})
    row = conn.execute(
        text(
            "INSERT INTO search_cache (key, source, payload, total, fetched_at, expires_at) "
            "VALUES (:key, :source, CAST(:payload AS jsonb), :total, now(), "
            "now() + make_interval(secs => :ttl)) "
            "ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload, total = EXCLUDED.total, "
            "fetched_at = EXCLUDED.fetched_at, expires_at = EXCLUDED.expires_at "
            "RETURNING fetched_at"
        ),
        {"key": key, "source": source, "payload": payload, "total": total, "ttl": ttl},
    ).first()
    # Expired rows are removed as a side effect of writing, so the table stays bounded.
    conn.execute(text("DELETE FROM search_cache WHERE expires_at < now()"))
    return _iso(row.fetched_at) if row else None


# --- shared state ------------------------------------------------------------------


def _state(conn: Connection, source: str):
    conn.execute(
        text(
            "INSERT INTO source_state (source, updated_at) VALUES (:s, now()) "
            "ON CONFLICT (source) DO NOTHING"
        ),
        {"s": source},
    )
    return conn.execute(
        text("SELECT *, now() AS db_now FROM source_state WHERE source = :s"), {"s": source}
    ).one()


def _update_state(conn: Connection, source: str, assignments: dict[str, str], params: dict) -> None:
    if not assignments:
        return
    # Column names and expressions come from this module, never from request data.
    sets = ", ".join(f"{column} = {expr}" for column, expr in assignments.items())
    conn.execute(
        text(f"UPDATE source_state SET {sets}, updated_at = now() WHERE source = :s"),  # noqa: S608
        {"s": source, **params},
    )


def _acquire(conn: Connection, key: int, deadline: float) -> bool:
    while True:
        if conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _interval(settings: Settings, source: str, learned: float | None) -> float:
    if source == "arxiv":
        return settings.arxiv_min_interval_seconds
    if source == "crossref":
        return learned or DEFAULT_CROSSREF_INTERVAL
    return OPENALEX_SPACING


# --- the call ----------------------------------------------------------------------


def call_source(
    settings: Settings,
    source: str,
    op: str,
    key: str,
    ttl: int,
    request: http.Request | None,
    parse: Parse,
) -> SourceOutcome:
    started = time.monotonic()
    try:
        outcome = _call(settings, source, op, key, ttl, request, parse)
    except Exception as exc:  # one source's bug or outage must not take the others down
        log.error("source call failed: %s", type(exc).__name__, extra={"source": source})
        outcome = SourceOutcome(
            source, "unavailable", message=f"{NAMES[source]} 暂时无法使用，请稍后再试"
        )
    outcome.duration_ms = round((time.monotonic() - started) * 1000)
    return outcome


def _call(settings, source, op, key, ttl, request, parse) -> SourceOutcome:
    name = NAMES[source]
    if request is None:
        return SourceOutcome(
            source, "bad_request", message=f"{name} 无法检索这个查询（没有可用的词）"
        )
    engine = get_engine()
    deadline = time.monotonic() + settings.search_max_wait_seconds
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        if (hit := _cache_get(conn, key)) is not None:
            return hit
        lock = LOCK_KEYS.get(source)
        locked = False
        try:
            if lock is not None:
                if not _acquire(conn, lock, deadline):
                    return SourceOutcome(
                        source, "throttled", message=f"{name} 正在处理其他请求，请稍后再试"
                    )
                locked = True
                if (hit := _cache_get(conn, key)) is not None:  # filled while we waited
                    return hit
            state = _state(conn, source)
            blocked = _blocked(state, source, name)
            if blocked is not None:
                return blocked
            interval = _interval(settings, source, state.interval_seconds)
            throttled = _pace(conn, state, source, name, interval, deadline)
            if throttled is not None:
                return throttled
            return _execute(conn, settings, source, op, key, ttl, request, parse, interval)
        finally:
            if locked:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock})
                except Exception:
                    conn.invalidate()  # closing the session is what releases the lock


def _blocked(state, source: str, name: str) -> SourceOutcome | None:
    now = state.db_now
    if state.cooldown_until and state.cooldown_until > now:
        return SourceOutcome(
            source,
            "cooling_down",
            message=f"{name} 之前限制了请求频率，暂停到 {_iso(state.cooldown_until)}",
            extra={"until": _iso(state.cooldown_until)},
        )
    if (
        source == "openalex"
        and state.quota_remaining_usd is not None
        and state.quota_reset_at
        and state.quota_reset_at > now
        and state.quota_remaining_usd < (state.cost_usd or DEFAULT_SEARCH_COST_USD)
    ):
        return SourceOutcome(
            source,
            "quota_exhausted",
            message=f"{name} 今日额度已用完，将在 {_iso(state.quota_reset_at)} 重置",
            extra={"reset_at": _iso(state.quota_reset_at)},
        )
    return None


def _pace(conn, state, source, name, interval, deadline) -> SourceOutcome | None:
    """Wait for the next permitted send time, or refuse when that is too far away."""
    wait = 0.0
    if source == "openalex":
        # Reserve a slot atomically so that concurrent processes queue up instead of bursting.
        row = conn.execute(
            text(
                "UPDATE source_state SET next_allowed_at = "
                "greatest(coalesce(next_allowed_at, now()), now()) + make_interval(secs => :gap) "
                "WHERE source = :s RETURNING "
                "greatest(coalesce(next_allowed_at, now()), now()) - make_interval(secs => :gap) "
                "- now() AS wait"
            ),
            {"s": source, "gap": OPENALEX_SPACING},
        ).one()
        wait = max(row.wait.total_seconds(), 0.0)
    elif state.next_allowed_at and state.next_allowed_at > state.db_now:
        wait = (state.next_allowed_at - state.db_now).total_seconds()
    if wait > 0:
        if time.monotonic() + wait > deadline:
            return SourceOutcome(
                source,
                "throttled",
                message=f"{name} 要求两次请求间隔至少 {interval:g} 秒，现在请求太密集，请稍后再试",
            )
        time.sleep(wait)
    return None


def _execute(conn, settings, source, op, key, ttl, request, parse, interval) -> SourceOutcome:
    name = NAMES[source]
    timeout = settings.search_source_timeout_seconds
    attempts = 0
    result = None
    failure = ""
    while attempts < 2:
        attempts += 1
        try:
            result = http.fetch(request, timeout)
            failure = ""
        except http.FetchTimeout:
            result, failure = None, "timeout"
        except http.FetchUnavailable:
            result, failure = None, "unavailable"
        except BadResponse as exc:
            _finish(conn, source, interval, {})
            return SourceOutcome(source, "bad_response", message=f"{name} 的响应不可用：{exc}")
        if result is not None and result.status < 500:
            break
        if result is not None:
            failure = "unavailable"
        if attempts == 1:
            time.sleep(max(RETRY_PAUSE, interval if source in LOCK_KEYS else 0.0))
    learned = None
    if result is not None and source == "crossref":
        learned = _crossref_interval(result.headers)
    extra_state: dict = {"interval": learned}

    if failure:
        _finish(conn, source, interval, extra_state)
        if failure == "timeout":
            message = f"{name} {timeout:g} 秒内没有响应，已重试 1 次"
            return SourceOutcome(source, "timeout", message=message)
        return SourceOutcome(source, "unavailable", message=f"{name} 暂时无法访问，已重试 1 次")

    assert result is not None
    status = result.status
    if status == 429:
        return _rate_limited(conn, source, name, interval, result)
    if status == 404 and op == "lookup":
        _finish(conn, source, interval, extra_state)
        return SourceOutcome(source, "not_found", message=f"{name} 没有这条记录")
    if status == 400:
        _finish(conn, source, interval, extra_state)
        reason = http.error_reason(result.body)
        return SourceOutcome(
            source, "bad_request", message=f"{name} 拒绝了请求" + (f"：{reason}" if reason else "")
        )
    if not 200 <= status < 300:
        _finish(conn, source, interval, extra_state)
        return SourceOutcome(source, "bad_response", message=f"{name} 返回了 HTTP {status}")
    try:
        records, total = parse(result.body)
    except BadResponse as exc:
        _finish(conn, source, interval, extra_state)
        return SourceOutcome(source, "bad_response", message=f"{name} 的响应不可用：{exc}")
    if source == "openalex":
        extra_state.update(_openalex_quota(result.headers))
    _finish(conn, source, interval, extra_state)
    fetched_at = _cache_put(conn, key, source, records, total, ttl)
    return SourceOutcome(
        source, "ok", records=records, total=total, fetched_at=fetched_at or _iso(datetime.now(UTC))
    )


def _openalex_quota(headers) -> dict:
    remaining = _seconds(headers, "x-ratelimit-remaining-usd")
    reset = _seconds(headers, "x-ratelimit-reset")
    cost = _seconds(headers, "x-ratelimit-cost-usd")
    return {"remaining": remaining, "reset": reset, "cost": cost}


def _finish(conn: Connection, source: str, interval: float, learned: dict) -> None:
    """Record when the next request may start (counted from the end of this one) and any
    quota or interval the response taught us."""
    assignments: dict[str, str] = {}
    params: dict = {}
    if source in LOCK_KEYS:
        assignments["next_allowed_at"] = "now() + make_interval(secs => :gap)"
        params["gap"] = learned.get("interval") or interval
    if learned.get("interval"):
        assignments["interval_seconds"] = ":learned_interval"
        params["learned_interval"] = learned["interval"]
    if learned.get("remaining") is not None:
        assignments["quota_remaining_usd"] = ":remaining"
        params["remaining"] = learned["remaining"]
        if learned.get("reset") is not None:
            assignments["quota_reset_at"] = "now() + make_interval(secs => :reset)"
            params["reset"] = learned["reset"]
    if learned.get("cost"):
        assignments["cost_usd"] = ":cost"
        params["cost"] = learned["cost"]
    _update_state(conn, source, assignments, params)


def _rate_limited(conn, source: str, name: str, interval: float, result) -> SourceOutcome:
    headers = result.headers
    if source == "openalex":
        remaining = _seconds(headers, "x-ratelimit-remaining")
        remaining_usd = _seconds(headers, "x-ratelimit-remaining-usd")
        cost = _seconds(headers, "x-ratelimit-cost-usd") or DEFAULT_SEARCH_COST_USD
        reset = _seconds(headers, "x-ratelimit-reset")
        exhausted = remaining == 0 or (remaining_usd is not None and remaining_usd < cost)
        if exhausted and reset is not None:
            _finish(
                conn,
                source,
                interval,
                {"remaining": remaining_usd if remaining_usd is not None else 0.0, "reset": reset},
            )
            reset_at = datetime.now(UTC) + timedelta(seconds=reset)
            return SourceOutcome(
                source,
                "quota_exhausted",
                message=f"{name} 今日额度已用完，将在 {_iso(reset_at)} 重置",
                extra={"reset_at": _iso(reset_at)},
            )
    wait = http.retry_after_seconds(headers, DEFAULT_COOLDOWN)
    _finish(conn, source, interval, {})
    conn.execute(
        text(
            "UPDATE source_state SET cooldown_until = now() + make_interval(secs => :wait), "
            "cooldown_reason = 'rate_limited', updated_at = now() WHERE source = :s"
        ),
        {"s": source, "wait": wait},
    )
    return SourceOutcome(
        source,
        "rate_limited",
        message=f"{name} 限制了请求频率，约 {round(wait)} 秒后再试",
        extra={"retry_after": round(wait)},
    )
