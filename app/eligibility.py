"""承运商账号资格区间。

换版期间常出现一段没有任何账号具备完整资格的“空档”。空档扫描仍可参与
时效计时（避免运营等待），但来源待核验，不能直接形成正式赔付。

版本区间统一按 [effective_from, effective_to) 处理；缺省端表示不设边界。
未携带 carrier_version 的历史扫描落到 carrier-legacy（全区间），因此旧样例
的行为保持不变。
"""
from app.errors import DomainError
from app.values import instant
from datetime import timezone

DEFAULT_VERSIONS = {
    "carrier-legacy": {"account": "legacy", "effective_from": None, "effective_to": None, "rank": 0},
    "carrier-v1": {"account": "ACCT-OLD", "effective_from": None, "effective_to": "2026-11-03T00:00:00Z", "rank": 1},
    "carrier-v2": {"account": "ACCT-NEW", "effective_from": "2026-11-04T00:00:00Z", "effective_to": None, "rank": 2},
}
KNOWN_VERSIONS = frozenset(DEFAULT_VERSIONS)
FALLBACK_VERSION = "carrier-legacy"

STATUS_ELIGIBLE = "eligible"
STATUS_PENDING = "pending"

REASON_TEXT = {
    "WITHIN_QUALIFICATION": "落在资格区间内",
    "LEGACY_BASELINE": "历史批次无版本列，按基线采信",
    "QUALIFICATION_VERIFIED": "空档来源已核验",
    "GAP_SOURCE": "资格空档来源待核验",
    "OLD_ACCOUNT_AFTER_SWITCH": "换版后旧账号来源待核验",
    "OUTSIDE_QUALIFICATION": "超出账号资格区间",
    "LEGACY_CLAIM": "显式声明历史基线版本，待核验",
    "UNKNOWN_VERSION": "版本未登记",
    "MISSING_EVENT_TIME": "缺少事件时刻",
}


def _ready(meta, key):
    """把版本定义转成可比较的内部结构，不修改调用方传入的对象。"""
    item = dict(meta)
    item["version"] = key
    item.setdefault("account", item.get("account") or key)
    item.setdefault("rank", 0)
    try:
        item["from_dt"] = instant(item["effective_from"]) if item.get("effective_from") else None
        item["to_dt"] = instant(item["effective_to"]) if item.get("effective_to") else None
    except DomainError:
        raise DomainError("BAD_VERSIONS", f"版本 {key} 的资格区间不是合法时间")
    if item["from_dt"] and item["to_dt"] and item["to_dt"] <= item["from_dt"]:
        raise DomainError("BAD_VERSIONS", f"版本 {key} 的资格区间为空")
    return item


def versions_from(parameters=None):
    """合并内置版本与参数覆盖，返回可直接查询的版本表。"""
    parameters = parameters or {}
    overrides = parameters.get("carrier_versions") or {}
    if not isinstance(overrides, dict):
        raise DomainError("BAD_VERSIONS", "carrier_versions 需要对象")
    versions = {key: dict(value) for key, value in DEFAULT_VERSIONS.items()}
    for key, value in overrides.items():
        if not isinstance(value, dict):
            raise DomainError("BAD_VERSIONS", "版本定义需要对象")
        merged = {**versions.get(str(key), {}), **value}
        merged.setdefault("rank", 10 + len(versions))
        versions[str(key)] = merged
    return {key: _ready(meta, key) for key, meta in versions.items()}


def rank_of(row, versions):
    meta = versions.get(str(row.get("carrier_version") or FALLBACK_VERSION), {})
    return int(meta.get("rank", 0))


def _covers(meta, event):
    if meta.get("from_dt") and event < meta["from_dt"]:
        return False
    if meta.get("to_dt") and event >= meta["to_dt"]:
        return False
    return True


def _active_versions(event, versions):
    """事件时刻真正覆盖它的正式版本；carrier-legacy 只作历史兜底，不算资格覆盖。"""
    return [
        (key, meta)
        for key, meta in versions.items()
        if key != FALLBACK_VERSION and _covers(meta, event)
    ]


def interval_text(meta):
    start = meta.get("effective_from") or "不限"
    end = meta.get("effective_to") or "不限"
    return f"[{start}, {end})"


def _result(status, reason, meta, version):
    return {
        "status": status,
        "reason": reason,
        "reason_text": REASON_TEXT.get(reason, reason),
        "version": version,
        "account": meta.get("account") or "",
        "effective_from": meta.get("effective_from"),
        "effective_to": meta.get("effective_to"),
        "interval": interval_text(meta),
    }


def gap_windows(versions):
    """版本区间之间无人覆盖的窗口，按 [start,end) 列出。

    只有在已有一条区间结束、而下一段区间尚未开始的确定时刻之间才算空档；
    起点不限的版本视为从最初就覆盖，不产生“前段空档”。
    """
    spans = [
        (meta["from_dt"], meta["to_dt"])
        for key, meta in versions.items()
        if key != FALLBACK_VERSION
    ]
    gaps = []
    reached = None
    for start, end in sorted(spans, key=lambda span: (span[0] is not None, span[0].isoformat() if span[0] else "")):
        if reached is not None and start is not None and start > reached:
            gaps.append((reached, start))
        if end is None:
            return gaps
        if start is None:
            reached = end if reached is None else min(reached, end)
        else:
            reached = end if reached is None else max(reached, end)
    return gaps


def _utc_text(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else "不限"


def window_text(start, end):
    return f"[{_utc_text(start)}, {_utc_text(end)})"


def gap_text(versions):
    return "; ".join(window_text(start, end) for start, end in gap_windows(versions))


def qualify(row, versions, parameters=None):
    """给单条扫描附上资格结论。

    status=eligible 才可形成正式赔付；pending 只会进入暂算金额，正式金额为 0。
    """
    parameters = parameters or {}
    version = str(row.get("carrier_version") or FALLBACK_VERSION)
    meta = versions.get(version)
    verified = {str(value) for value in (parameters.get("verified_scans") or [])}
    if str(row.get("scan_id")) in verified:
        base = meta or {"account": row.get("account") or "", "effective_from": None, "effective_to": None}
        return _result(STATUS_ELIGIBLE, "QUALIFICATION_VERIFIED", base, version)
    if not meta:
        return _result(STATUS_PENDING, "UNKNOWN_VERSION", {}, version)
    try:
        event = instant(row.get("event_utc") or row.get("event_time"))
    except DomainError:
        return _result(STATUS_PENDING, "MISSING_EVENT_TIME", meta, version)
    if _covers(meta, event):
        if version == FALLBACK_VERSION:
            # 未携带版本列的既有批次按基线采信；显式声明旧基线则待核验。
            if not row.get("carrier_version"):
                return _result(STATUS_ELIGIBLE, "LEGACY_BASELINE", meta, version)
            return _result(STATUS_PENDING, "LEGACY_CLAIM", meta, version)
        return _result(STATUS_ELIGIBLE, "WITHIN_QUALIFICATION", meta, version)
    active = _active_versions(event, versions)
    if not active:
        reason = "GAP_SOURCE"
    elif rank_of(row, versions) < max(int(item[1].get("rank", 0)) for item in active):
        reason = "OLD_ACCOUNT_AFTER_SWITCH"
    else:
        reason = "OUTSIDE_QUALIFICATION"
    return _result(STATUS_PENDING, reason, meta, version)
