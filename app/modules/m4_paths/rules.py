from collections import defaultdict
from app.values import instant
from app.reasons import reject, sources
from app import eligibility

PRIORITY = {"ACCEPT": 0, "DEPART": 1, "CUSTOMS": 2, "DELIVERED": 3}


def _utc(value):
    return instant(value).isoformat().replace("+00:00", "Z")


def _preference(row, versions):
    """同一逻辑扫描被新旧账号同时回传时，新版本账号优先。

    运营要求新账号扫描先进入时效计算；同刻同节点由高版本账号先占位，
    旧账号那条保留为“未采用”凭据，既不删除也不重复计时。
    """
    qualification = row.get("_qualification") or {}
    return (
        eligibility.rank_of(row, versions),
        1 if qualification.get("status") == eligibility.STATUS_ELIGIBLE else 0,
        str(row.get("scan_id")),
    )


def _collapse(events, versions, parameters):
    """把同一节点、同一发生时刻的多账号回传折叠成一条唯一计时事件。"""
    canonical = {}
    superseded = []
    for event in events:
        qualification = eligibility.qualify(event, versions, parameters)
        event = {**event, "_qualification": qualification}
        key = (event["event_utc"], event["node"])
        current = canonical.get(key)
        if current is None:
            canonical[key] = event
            continue
        winner, loser = sorted([current, event], key=lambda e: _preference(e, versions), reverse=True)
        merged_ids = set(winner.get("merged_scan_ids", [])) | {str(winner.get("scan_id")), str(loser.get("scan_id"))}
        merged = {
            **winner,
            "_sources": sorted(set(sources(winner)) | set(sources(loser))),
            "merged_scan_ids": sorted(merged_ids),
        }
        # 去重只影响采信；来源行完整并入唯一事件，不删除任何原件。
        canonical[key] = merged
        superseded.append({
            **loser,
            "reason": "DUPLICATE_ACCOUNT_SOURCE",
            "detail": f"同节点同刻已由 {winner.get('carrier_version') or eligibility.FALLBACK_VERSION} / {winner.get('account') or '未登记'} 采纳",
            "_adopted_scan_id": str(winner.get("scan_id")),
            "_adopted_version": winner.get("carrier_version") or eligibility.FALLBACK_VERSION,
        })
    return canonical, superseded


def _adjudication(events, versions):
    """汇总一个包裹的资格结论：唯一结算状态 + 资格区间 + 未采用原因。"""
    pending = [e["_qualification"] for e in events
               if e["_qualification"]["status"] != eligibility.STATUS_ELIGIBLE]
    # 采用版本取路径中资格最高的来源（换版后即新账号），而不是最早一条。
    adopted = max(events, key=lambda e: eligibility.rank_of(e, versions)) if events else {}
    lead = adopted.get("_qualification", {})
    intervals = []
    if lead.get("interval"):
        intervals.append(f"采用 {lead.get('version')} {lead['interval']}")
    gaps = eligibility.gap_windows(versions)
    touched = []
    for event in events:
        moment = instant(event["event_utc"])
        for start, end in gaps:
            if (start is None or moment >= start) and (end is None or moment < end):
                text = eligibility.window_text(start, end)
                if text not in touched:
                    touched.append(text)
    if touched:
        intervals.append(f"空档 {'; '.join(touched)}")
    reasons = []
    for item in pending:
        text = f"{item['reason_text']}（{item['version']} {item['interval']}）"
        if text not in reasons:
            reasons.append(text)
    return {
        "settlement_status": "待核验挂账" if pending else "正式可结算",
        "qualification_status": eligibility.STATUS_PENDING if pending else eligibility.STATUS_ELIGIBLE,
        "qualification_interval": "; ".join(intervals),
        "non_adopted_reason": "; ".join(reasons),
        "adopted_version": lead.get("version"),
        "adopted_account": lead.get("account"),
    }


def _milestones(valid):
    """每个节点只保留最早的有效扫描作为计时节点。

    换版后旧账号常对已妥投包裹再补传一条“妥投”。按“补传不改变既有妥投时间”，
    后到的同节点扫描只作补传凭据，不参与计时、不改变结算裁定。
    """
    milestones = []; later = []; seen = set()
    for event in valid:
        if event["node"] in seen:
            later.append(event)
            continue
        seen.add(event["node"]); milestones.append(event)
    return milestones, later


def evaluate(rows, context):
    parameters = (context or {}).get("parameters", {})
    versions = eligibility.versions_from(parameters)
    groups = defaultdict(list); good = []; bad = []; superseded = []
    for r in rows: groups[r["parcel"]].append(r)
    for parcel, events in sorted(groups.items()):
        events = sorted(events, key=lambda r: (instant(r["event_utc"]), PRIORITY[r["node"]], r["scan_id"]))
        canonical, duplicate_sources = _collapse(events, versions, parameters)
        superseded.extend(duplicate_sources)
        ordered = sorted(canonical.values(), key=lambda r: (instant(r["event_utc"]), PRIORITY[r["node"]], r["scan_id"]))
        seen = {}; valid = []; last = -1
        for e in ordered:
            time = e["event_utc"]
            if time in seen and seen[time] != e["node"]: bad.append(reject(e, "SIMULTANEOUS_CONFLICT", "同刻扫描节点冲突")); continue
            seen[time] = e["node"]
            if PRIORITY[e["node"]] < last: bad.append(reject(e, "PATH_REGRESSION", "扫描路径倒退")); continue
            last = PRIORITY[e["node"]]; valid.append(e)
        accepts = [e for e in valid if e["node"] == "ACCEPT"]
        if not accepts:
            bad.extend(reject(e, "MISSING_ACCEPT", "缺少收件扫描") for e in valid); continue
        milestones, late_rescans = _milestones(valid)
        for e in late_rescans:
            earlier = next(m for m in milestones if m["node"] == e["node"])
            superseded.append({
                **e,
                "reason": "REUPLOAD_AFTER_MILESTONE",
                "detail": f"同节点已按 {_utc(earlier['event_utc'])} 采信，补传不改变既有节点时刻",
                "_adopted_scan_id": str(earlier.get("scan_id")),
                "_adopted_version": earlier.get("carrier_version") or eligibility.FALLBACK_VERSION,
            })
        delivery = next((e for e in milestones if e["node"] == "DELIVERED"), None)
        first = next(e for e in milestones if e["node"] == "ACCEPT")
        source_lines = sorted({line for e in valid for line in sources(e)})
        duplicate_ids = sorted({sid for e in milestones for sid in e.get("merged_scan_ids", [])})
        verdict = _adjudication(milestones, versions)
        notes = [verdict["non_adopted_reason"]] if verdict["non_adopted_reason"] else []
        if duplicate_ids:
            notes.append("重复账号回传已折叠：" + "、".join(duplicate_ids))
        if late_rescans:
            notes.append("同节点补传未采用：" + "、".join(str(e.get("scan_id")) for e in late_rescans))
        verdict["non_adopted_reason"] = "；".join(notes)
        good.append({
            **first,
            "_sources": source_lines,
            "accepted_at": first["event_utc"],
            "delivered_at": delivery["event_utc"] if delivery else None,
            "delivery_uploaded_at": delivery["received_utc"] if delivery else None,
            "scan_count": len(valid),
            "path": " → ".join(e["node"] for e in milestones),
            "reported": delivery["reported"] if delivery else first["reported"],
            "qualification": [e["_qualification"] for e in milestones],
            "duplicate_scan_ids": duplicate_ids,
            **verdict,
        })
    return {
        "rows": good,
        "quarantine": bad,
        "superseded": superseded,
        "metrics": {
            "parcels": len(good),
            "delivered": sum(r["delivered_at"] is not None for r in good),
            "path_errors": len(bad),
            "duplicate_accounts": sum(s["reason"] == "DUPLICATE_ACCOUNT_SOURCE" for s in superseded),
            "reupload_after_milestone": sum(s["reason"] == "REUPLOAD_AFTER_MILESTONE" for s in superseded),
            "pending_verification": sum(r["settlement_status"] == "待核验挂账" for r in good),
        },
    }
