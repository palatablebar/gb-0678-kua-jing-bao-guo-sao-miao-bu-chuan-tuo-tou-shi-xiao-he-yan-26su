# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

换版与结算：

- `POST /api/runs/<id>/switch`（body 可选 `note`）：事务内写结算快照并切换生效版本；未就绪批次返回 422，失败整体回滚，旧版结算保留。
- `GET /api/settlements`：当前生效批次、正式/暂算合计、挂账记录数、唯一结算台账与换版记录。
- `GET /settlements`：同一份裁定的页面视图，读取与导出一致的 m6 结果。

逐行裁定字段：`settlement_status`（正式可结算 / 待核验挂账）、`qualification_interval`、`non_adopted_reason`、`adopted_version`、`adopted_account`、`expected`（正式）、`provisional_expected`（暂算）。导出 CSV 另有 `kind=superseded` 行保存被取代的原件来源。

参数：`carrier_versions` 覆盖版本区间，`verified_scans` 列出已核验扫描号，`as_of`、`hourly_compensation`、`holidays` 同前。区间一律 [start,end)；`carrier_version`/`account` 为可选追加列，位于十个必填列之后。
