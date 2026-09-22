# 二手轮胎(tyre)业务快照 — 2026-09-22

来源:`~/.hermes/profiles/foreign-trade/workspace/pipeline_v2/`

## 包含文件
- `config.json` — 产品配置(邮箱/CRM/HS/客户类型/邮件模板)
- `SEND_GATE.lock` — 开发信发送闸门
- `*.py` — 6 个核心脚本(normalize/send/imap_poll/manual_import/healthcheck/classify)
- `mining/` — 5 个数据挖掘脚本(customs/ecommerce/fusion/gmaps/yellowpages)

## 关键数字(9-22 12:00)
- 客户档案:475(覆盖 26+ 国非洲 + PK + SC)
- 今日 R1:100 封已发(5+95),全成功
- 待发 R1:372(预计 4 天 @daily_limit=100)
- 邮箱链路:`send@mail.esdroll.com`(push)→ `fcjasonliu@gmail.com`(Gmail App Password)
- R2/R3 间隔:15/30 天(原 30/60)
- ty 飞书 base:`W0GvbfX32aSkpxsWisrcPvfCn36`
  - 客户档案表:`tblpWzZsEU6hGL9G`
  - 开发信记录表:`tblG2C0G9rtys0te`
  - 线索池表:`tbljNG56Npx90eGz`
  - 客户标签表:`tbl6mmfn5LCPslMl`
  - ty 仪表盘:`blk7BgFrFozmsXOZ`(11 block)

## 不上传的内容(已在 .gitignore 排除或留本地)
- `data/` 所有 CSV/JSON(海关 + 黄页数据,本地够用)
- `logs/` 所有 audit 日志
- `~/.zshrc` / `~/.hermes/.env` / `~/.hermes/profiles/foreign-trade/.env`(密码)
- n8n 数据库 (`/Users/jason/.n8n/database.sqlite`,含 token)

## 9-22 审查修复
- `crm_send.py`:fail_errors 字段加 `fail_count_total` 总数 + `str(e)[:200]` 截断
- `config.json`:加 `_meta.rounds_intervals_changed_at = "2026-09-22"`,intervals 改 15/30
