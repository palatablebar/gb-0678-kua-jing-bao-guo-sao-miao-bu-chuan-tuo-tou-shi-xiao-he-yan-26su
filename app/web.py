from flask import render_template
from app.config import MODULES,TITLE,SHORT
from app.pipeline import execute,load_sample
from app import store
LABELS={'scan_id': '扫描号', 'parcel': '包裹号', 'node': '节点', 'event_time': '发生时间', 'received_time': '上传时间', 'destination': '目的地', 'zone': '时区', 'event_utc': '发生 UTC', 'received_utc': '上传 UTC', 'upload_lag_hours': '补传时差 / h', 'accepted_at': '收件 UTC', 'delivered_at': '妥投 UTC', 'delivery_uploaded_at': '妥投上传 UTC', 'scan_count': '扫描数', 'path': '节点路径', 'working_hours': '计时 / h', 'promised_hours': '承诺 / h', 'late_hours': '超时 / h', 'expected': '正式赔付 / 元', 'provisional_expected': '暂算赔付 / 元', 'sla': '时效结论', 'reported': '申报 / 元', 'difference': '差额 / 元', 'status': '对账结论', 'settlement_status': '结算裁定', 'qualification_interval': '资格区间', 'non_adopted_reason': '未采用原因', 'qualification_status': '资格状态', 'adopted_version': '采用版本', 'adopted_account': '采用账号', 'accepted': '采纳扫描', 'late_uploads': '迟到上传', 'parcels': '包裹数', 'late_parcels': '超时包裹', 'compensation': '正式合计', 'provisional_compensation': '暂算合计', 'delivered': '妥投数', 'path_errors': '路径异常', 'records': '归档数', 'source_lines': '源行数', 'sha256': '摘要', 'ready': '可归档', 'differences': '差异数', 'duplicate_accounts': '去重账号回传', 'pending_verification': '待核验挂账', 'pending_records': '挂账记录', 'provisional_total': '暂算合计', 'accepted_inputs': '采纳来源行', 'quarantine_inputs': '隔离来源行', 'output_records': '结果记录', 'input': '输入行', 'excluded': '排除行', 'balanced': '行数平衡', 'expected_total': '正式合计', 'reported_total': '申报合计', 'differences': '差异项', 'blank': '空行', 'repeated_headers': '分段表头', 'encoding': '编码', 'duplicate': '重复行', 'quarantine': '隔离行', 'accepted': '采纳行', 'sha256': '校验摘要', 'ready': '可归档', 'run_id': '批次', 'rule_version': '规则版本', 'switched_at': '换版时刻', 'note': '备注', 'active': '当前生效批次'}
def register_pages(app):
    def page(index):
        run=store.latest() or execute(load_sample("dirty.csv"))
        stage=run["stages"][index]
        rows=stage["rows"]
        columns=[['scan_id', 'parcel', 'node', 'event_time', 'received_time'], ['scan_id', 'parcel', 'node', 'destination', 'zone'], ['scan_id', 'parcel', 'event_utc', 'received_utc', 'upload_lag_hours'], ['parcel', 'accepted_at', 'delivered_at', 'delivery_uploaded_at', 'scan_count', 'path', 'settlement_status', 'qualification_interval', 'non_adopted_reason'], ['parcel', 'accepted_at', 'delivered_at', 'working_hours', 'promised_hours', 'late_hours', 'expected', 'provisional_expected', 'settlement_status'], ['parcel', 'expected', 'provisional_expected', 'reported', 'difference', 'status'], ['parcel', 'delivered_at', 'sla', 'expected', 'status']][index]
        return render_template(f"m{index+1}.html",title=TITLE,short=SHORT,modules=MODULES,index=index,run=run,stage=stage,rows=rows,columns=columns[:12],history=store.history(),labels=LABELS,ledger=store.ledger(run),settlement=store.current_settlement())
    app.add_url_rule("/",view_func=lambda:page(4))
    for index,(slug,name) in enumerate(MODULES):
        app.add_url_rule("/"+slug,endpoint="page_"+slug,view_func=lambda index=index:page(index))
    app.add_url_rule("/settlements",view_func=settlements_page)
    return app
def settlements_page():
    state=store.current_settlement()
    return render_template("settlements.html",title=TITLE,short=SHORT,modules=MODULES,index=-1,state=state,labels=LABELS,run=None,stage={"metrics":{},"rows":[],"cumulative_quarantine":[],"cumulative_superseded":[]},ledger=state.get("ledger",[]),settlement=state)
