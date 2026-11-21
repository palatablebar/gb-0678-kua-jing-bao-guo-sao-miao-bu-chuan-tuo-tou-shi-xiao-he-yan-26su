import csv,io,json
def build_archive(run):
    """唯一证据导出。

    页面展示的结算裁定与导出的逐行裁定来自同一份 m6 结果，不允许二次推导。
    被新账号取代的旧账号扫描仍以 superseded 行保留，原件来源行不因去重而删除。
    """
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence","parcel","settlement_status","qualification_interval","non_adopted_reason","adopted_version","expected","provisional_expected"])
    meta=run["manifest"];stage=run["stages"][-1]
    for row in stage["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False),row.get("parcel",""),row.get("settlement_status","正式可结算"),row.get("qualification_interval",""),row.get("non_adopted_reason",""),row.get("adopted_version",""),row.get("expected",""),row.get("provisional_expected",row.get("expected",""))])
    for row in stage.get("cumulative_superseded",[]):
        adopted=str(row.get("_adopted_version",""));adopted_id=str(row.get("_adopted_scan_id",""))
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"superseded",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False),row.get("parcel",""),"未采用","",f"同节点同刻已由 {adopted} / {adopted_id} 采纳","",  "", ""])
    for row in stage.get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False),row.get("parcel",""),"不形成结算","","","","",""])
    return "﻿"+stream.getvalue()
