import sqlite3,json
from datetime import datetime,timezone
from pathlib import Path
from app.config import DATA_DIR,RULE_VERSION
from app.errors import DomainError
SCHEMA="""
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核');
CREATE TABLE IF NOT EXISTS settlements (run_id TEXT PRIMARY KEY,rule_version TEXT NOT NULL,official_total TEXT NOT NULL,provisional_total TEXT NOT NULL,pending_records INTEGER NOT NULL,records INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS switches (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,rule_version TEXT NOT NULL,switched_at TEXT NOT NULL,note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS carrier_state (id INTEGER PRIMARY KEY CHECK(id=1),run_id TEXT NOT NULL,rule_version TEXT NOT NULL,switched_at TEXT NOT NULL);
"""
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db
def save(run_id,payload):
    # 同源重跑复用已有批次：不新增记录，也不重写已存金额。
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)
def ledger(run):
    """从批次结果中取唯一结算裁定；页面、导出与换版都读这一份。"""
    stage=run["stages"][5]
    rows=stage["rows"]
    return [{
        "parcel":r.get("parcel"),
        "settlement_status":r.get("settlement_status","正式可结算"),
        "qualification_status":r.get("qualification_status"),
        "qualification_interval":r.get("qualification_interval",""),
        "non_adopted_reason":r.get("non_adopted_reason",""),
        "adopted_version":r.get("adopted_version"),
        "adopted_account":r.get("adopted_account"),
        "expected":r.get("expected","0.00"),
        "provisional_expected":r.get("provisional_expected",r.get("expected","0.00")),
        "reported":r.get("reported","0"),
        "difference":r.get("difference","0"),
        "status":r.get("status"),
    } for r in rows]
def _snapshot(db,run):
    from decimal import Decimal
    rows=ledger(run)
    official=sum((Decimal(str(r["expected"])) for r in rows),Decimal(0))
    provisional=sum((Decimal(str(r["provisional_expected"])) for r in rows),Decimal(0))
    pending=sum(r["settlement_status"]=="待核验挂账" for r in rows)
    now=datetime.now(timezone.utc).isoformat()
    db.execute("INSERT OR IGNORE INTO settlements(run_id,rule_version,official_total,provisional_total,pending_records,records,created_at) VALUES (?,?,?,?,?,?,?)",(run["run_id"],RULE_VERSION,str(official),str(provisional),pending,len(rows),now))
    return {"official_total":str(official),"provisional_total":str(provisional),"pending_records":pending,"records":len(rows)}
def _after_log():
    """事务内测试缝：中途失败时整个换版必须回滚，旧版结算保持有效。"""
def switch(run_id,note=""):
    """原子换版：先写入本版结算快照，再切换当前生效版本。

    任何一步失败都回滚，carrier_state 继续指向旧版，旧版结算记录原样保留。
    对已生效的同一批次重复提交只读回原快照，不重复记账、不改正式金额。
    """
    run=get(run_id)
    stage=run["stages"][-1]["metrics"]
    if not stage.get("ready"): raise DomainError("NOT_READY","批次未就绪，不能换版")
    db=connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        active=db.execute("SELECT run_id,rule_version FROM carrier_state WHERE id=1").fetchone()
        if active and active[0]==run_id and active[1]==RULE_VERSION:
            db.rollback()
            return _replay(run_id)
        snapshot=_snapshot(db,run)
        db.execute("INSERT INTO switches(run_id,rule_version,switched_at,note) VALUES (?,?,?,?)",(run_id,RULE_VERSION,datetime.now(timezone.utc).isoformat(),note))
        _after_log()
        db.execute("INSERT INTO carrier_state(id,run_id,rule_version,switched_at) VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id,rule_version=excluded.rule_version,switched_at=excluded.switched_at",(run_id,RULE_VERSION,datetime.now(timezone.utc).isoformat()))
        db.commit()
        return {**snapshot,"run_id":run_id,"rule_version":RULE_VERSION,"ledger":ledger(run)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
def _replay(run_id):
    with connect() as db:
        snapped=db.execute("SELECT official_total,provisional_total,pending_records,records FROM settlements WHERE run_id=?",(run_id,)).fetchone()
    run=get(run_id)
    payload={"run_id":run_id,"rule_version":RULE_VERSION,"ledger":ledger(run)}
    if snapped: payload.update(official_total=snapped[0],provisional_total=snapped[1],pending_records=snapped[2],records=snapped[3])
    return payload
def current_settlement():
    with connect() as db:
        row=db.execute("SELECT run_id,rule_version,switched_at FROM carrier_state WHERE id=1").fetchone()
        if not row:return {"active":None,"ledger":[],"history":[]}
        run_id,rule_version,switched_at=row
        snap=db.execute("SELECT official_total,provisional_total,pending_records,records FROM settlements WHERE run_id=?",(run_id,)).fetchone()
        history=[{"run_id":i,"rule_version":v,"switched_at":t,"note":n} for i,v,t,n in db.execute("SELECT run_id,rule_version,switched_at,note FROM switches ORDER BY id DESC")]
    run=get(run_id)
    payload={"active":run_id,"rule_version":rule_version,"switched_at":switched_at,"ledger":ledger(run),"history":history}
    if snap: payload.update(official_total=snap[0],provisional_total=snap[1],pending_records=snap[2],records=snap[3])
    return payload
