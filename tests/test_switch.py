import csv,io,json
import pytest
from decimal import Decimal
from app import store
from app.errors import DomainError
from app.pipeline import calculate,execute,load_sample
from app.eligibility import qualify,versions_from


@pytest.fixture
def switch_result():
    return calculate(load_sample("switch.csv"))


def parcel(result,pid):
    return next(r for r in result["stages"][5]["rows"] if r["parcel"]==pid)


def test_gap_scan_counts_for_timeliness(switch_result):
    "接口时效不等核验：空档来源仍进入计时，SLA 先算出来。"
    row=parcel(switch_result,"1001")
    assert row["working_hours"]!="0" and row["sla"]=="超时"
    assert row["provisional_expected"]=="12.00"


def test_gap_scan_cannot_form_official_payment(switch_result):
    "质控要求：空档来源待核验，正式赔付为 0。"
    row=parcel(switch_result,"1001")
    assert row["settlement_status"]=="待核验挂账" and row["expected"]=="0.00" and row["status"]=="待核验挂账"


def test_gap_scan_shows_interval_and_reason(switch_result):
    row=parcel(switch_result,"1001")
    assert "空档 [2026-11-03T00:00:00Z, 2026-11-04T00:00:00Z)" in row["qualification_interval"]
    assert "资格空档来源待核验" in row["non_adopted_reason"]


def test_duplicate_account_returns_one_settlement(switch_result):
    "旧、新账号回传同一包裹只形成一条计时事件，新账号被采用。"
    row=parcel(switch_result,"1002")
    assert row["scan_count"]==3
    assert row["adopted_version"]=="carrier-v2" and row["adopted_account"]=="ACCT-NEW"
    assert row["settlement_status"]=="正式可结算"
    assert switch_result["stages"][3]["metrics"]["duplicate_accounts"]==1


def test_superseded_scan_kept_as_evidence(switch_result):
    superseded=switch_result["stages"][3]["cumulative_superseded"]
    assert superseded and superseded[0]["scan_id"]=="1203"
    assert superseded[0]["_sources"]==[8]
    row=parcel(switch_result,"1002")
    assert 8 in row["_sources"]
    assert "1203" in row["duplicate_scan_ids"] and "1204" in row["duplicate_scan_ids"]


def test_old_account_after_switch_is_pending():
    versions=versions_from({})
    verdict=qualify({"scan_id":"x","carrier_version":"carrier-v1","event_utc":"2026-11-05T00:00:00Z"},versions)
    assert verdict["status"]=="pending" and verdict["reason"]=="OLD_ACCOUNT_AFTER_SWITCH"


def test_verified_gap_source_becomes_settleable():
    params={"verified_scans":["1401"]}
    result=calculate(load_sample("switch.csv"),params)
    row=parcel(result,"1004")
    assert row["settlement_status"]=="正式可结算"
    assert "空档来源已核验" in row["qualification_interval"] or row["qualification_status"]=="eligible"
    other=parcel(result,"1001")
    assert other["settlement_status"]=="待核验挂账"


def test_legacy_sample_still_settles(result):
    "既有样例无版本列，全部按基线采信，正式金额不变。"
    assert all(r["settlement_status"]=="正式可结算" for r in result["stages"][5]["rows"])
    assert result["stages"][5]["metrics"]["expected_total"]=="0.00"
    assert result["stages"][4]["metrics"]["compensation"]=="0.00"


def test_late_reupload_after_delivery_keeps_settlement():
    "换版后旧账号对已妥投包裹再补传妥投：不推翻既有结算，也不拖成挂账。"
    raw=(load_sample("switch.csv").decode().rstrip("\n")+
         "\n1601,1005,妥投,2026-11-07T00:00:00Z,2026-11-07T01:00:00Z,JP,24,200,5,旧账号补传,carrier-v1,ACCT-OLD\n")
    result=calculate(raw.encode())
    row=parcel(result,"1005")
    assert row["settlement_status"]=="正式可结算" and row["delivered_at"]=="2026-11-06T00:00:00+00:00"
    assert row["expected"]==parcel(calculate(load_sample("switch.csv")),"1005")["expected"]
    superseded=result["stages"][3]["cumulative_superseded"]
    assert any(s["scan_id"]=="1601" and s["reason"]=="REUPLOAD_AFTER_MILESTONE" for s in superseded)


def test_export_reads_same_adjudication(switch_result):
    from app.archive import build_archive
    run=execute(load_sample("switch.csv"))
    rows=list(csv.DictReader(io.StringIO(build_archive(run).lstrip("﻿"))))
    ledger=store.ledger(run)
    exported={r["parcel"]:r for r in rows if r["kind"]=="accepted"}
    for item in ledger:
        assert exported[item["parcel"]]["settlement_status"]==item["settlement_status"]
        assert exported[item["parcel"]]["expected"]==item["expected"]
    assert any(r["kind"]=="superseded" and r["source_lines"]=="[8]" for r in rows)


def test_rerun_does_not_add_records_or_change_amounts():
    raw=load_sample("switch.csv")
    first=execute(raw);second=execute(raw)
    assert first["run_id"]==second["run_id"] and len(store.history())==1
    before=store.get(first["run_id"])["stages"][5]["metrics"]["expected_total"]
    assert second["stages"][5]["metrics"]["expected_total"]==before


def test_switch_snapshot_is_atomic_and_idempotent():
    run=execute(load_sample("switch.csv"))
    first=store.switch(run["run_id"],"首次切版")
    second=store.switch(run["run_id"],"重跑切版")
    assert first["official_total"]==second["official_total"]==run["stages"][5]["metrics"]["expected_total"]
    state=store.current_settlement()
    assert state["active"]==run["run_id"] and state["pending_records"]==2
    assert len(state["history"])==1


def test_failed_switch_keeps_old_settlement(monkeypatch):
    old=execute(load_sample("switch.csv"))
    store.switch(old["run_id"],"旧版")
    new=execute(load_sample("dirty.csv"))

    def boom(): raise RuntimeError("换版中途失败")
    monkeypatch.setattr(store,"_after_log",boom)
    with pytest.raises(RuntimeError):
        store.switch(new["run_id"],"新版")
    state=store.current_settlement()
    assert state["active"]==old["run_id"]
    assert state["official_total"]==old["stages"][5]["metrics"]["expected_total"]
    import sqlite3
    with sqlite3.connect(store.DATA_DIR/"records.db") as db:
        assert db.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]==1


def test_switch_requires_ready_run(client):
    p=client.post("/api/runs",json={"sample":"switch.csv"}).get_json()
    ok=client.post("/api/runs/"+p["run_id"]+"/switch",json={"note":"上线"})
    assert ok.status_code==200 and ok.get_json()["official_total"]==p["stages"][5]["metrics"]["expected_total"]
    assert client.get("/api/settlements").get_json()["active"]==p["run_id"]


def test_settlement_page_and_api_agree(client):
    p=client.post("/api/runs",json={"sample":"switch.csv"}).get_json()
    client.post("/api/runs/"+p["run_id"]+"/switch",json={})
    page=client.get("/settlements")
    assert page.status_code==200 and "待核验挂账" in page.get_data(as_text=True)
    api=client.get("/api/settlements").get_json()
    assert api["official_total"]==p["stages"][5]["metrics"]["expected_total"]


def test_optional_columns_header_only_after_base():
    raw=",".join(["scan_id","parcel","node","event_time","received_time","destination","promised_hours","declared_value","reported","note","carrier_version","account"])+"\n"
    with pytest.raises(DomainError) as e:
        calculate(raw.encode())
    assert e.value.code=="HEADER_ONLY"
    bad=("scan_id,account,parcel,node,event_time,received_time,destination,promised_hours,declared_value,reported,note\n"
         "1,ACCT,1,收件,2026-11-02T00:00:00Z,2026-11-02T01:00:00Z,JP,24,200,0,x\n")
    with pytest.raises(DomainError) as e:
        calculate(bad.encode())
    assert e.value.code=="BAD_HEADER"


def test_carrier_version_without_account_column():
    base=load_sample("switch.csv").decode().splitlines()
    header=base[0].replace(",account","")
    rows=[line.rsplit(",",1)[0] for line in base[1:]]
    result=calculate(("\n".join([header]+rows)+"\n").encode())
    assert result["stages"][3]["metrics"]["duplicate_accounts"]==1
    assert parcel(result,"1001")["settlement_status"]=="待核验挂账"


def test_unknown_version_is_pending_not_official():
    raw=(load_sample("switch.csv").decode()
         .replace("carrier-v2,ACCT-NEW","carrier-v9,ACCT-X",1))
    result=calculate(raw.encode())
    row=parcel(result,"1001")
    assert row["settlement_status"]=="待核验挂账" and row["expected"]=="0.00"
    assert any(q.get("reason")=="UNKNOWN_VERSION" for q in row["qualification"])


def test_empty_version_window_rejected():
    with pytest.raises(DomainError) as e:
        versions_from({"carrier_versions":{"carrier-v1":{"effective_from":"2026-11-05T00:00:00Z","effective_to":"2026-11-01T00:00:00Z"}}})
    assert e.value.code=="BAD_VERSIONS"


def test_superseded_row_survives_export_and_lineage(switch_result):
    "扫描原件凭据不可误删：去重后来源行仍计入采纳来源与归档。"
    m=switch_result["stages"][5]["metrics"]
    assert m["accepted_inputs"]==switch_result["stages"][0]["metrics"]["accepted"]
    assert m["balanced"] is True
    assert switch_result["stages"][6]["metrics"]["source_lines"]==m["accepted_inputs"]


def test_lineage_overlap_when_duplicate_source_rejected(switch_result):
    "同一来源行不能既被采纳又被隔离。"
    from app.modules.m6_compensation.rules import evaluate
    rows=[{**switch_result["stages"][5]["rows"][0]}]
    with pytest.raises(DomainError):
        evaluate(rows,{"quarantine":[{"_sources":rows[0]["_sources"][:1]}]})


def test_pending_rows_are_not_reported_as_passed(switch_result):
    rows={r["parcel"]:r for r in switch_result["stages"][5]["rows"]}
    assert rows["1001"]["status"]=="待核验挂账"
    assert rows["1002"]["status"]=="差异"
    assert switch_result["stages"][5]["metrics"]["pending_records"]==2
