# 跨境包裹扫描补传与妥投时效核验

供跨境集运客服质控员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18247 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 扫描采信：`/scans`，`/api/scans`。
- 2. 节点映射：`/nodes`，`/api/nodes`。
- 3. 时区归整：`/timezones`，`/api/timezones`。
- 4. 路径裁决：`/paths`，`/api/paths`。
- 5. 时效计算：`/timeliness`，`/api/timeliness`。
- 6. 赔付对账：`/compensation`，`/api/compensation`。
- 7. 批次归档：`/archives`，`/api/archives`。
- 8. 结算台账：`/settlements`，`/api/settlements`（换版与唯一结算裁定）。

## 核心规则

以事件发生时刻重建路径，补传不改变既有妥投时间；承诺时钟按目的地节假日停表。

承运商换版期间，资格区间按 [start,end) 裁决。空档扫描仍先进入时效计算并给出暂算金额，但来源待核验，不形成正式赔付；新旧账号重复回传的同一扫描只计一次，新账号优先，旧原件保留为未采用凭据。页面、结算台账与导出读取同一份裁定。换版在单事务内完成快照与切换，失败时旧版结算继续有效；同源重跑与重复换版不新增记录、不改正式金额。

## 演示

1. 打开首页，选择「换版·空档批」，核对结算裁定、资格区间与未采用原因。
2. 切换模块查看源行及裁决依据。
3. 点「提交换版」后进入 `/settlements` 查看唯一生效台账与换版记录。
4. 重跑相同样例，批次号与行数保持一致。
5. 复核后导出一份证据CSV。版本已变化的复核提交返回409。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv、switch.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。`carrier_version`/`account` 为可选追加列。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次；`POST /api/runs/<id>/review`需要version与decision；`POST /api/runs/<id>/switch`事务内换版；`GET /api/settlements`返回唯一结算台账；`GET /api/runs/<id>/archive`返回唯一证据导出。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。
