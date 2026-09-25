#!/usr/bin/env python3
"""
imap_poll.py — 拉回信 + 标记开发信记录 (Pipeline v2, product-agnostic)

读 products/<product>/config.json 配置:
  - IMAP host/port/user_env/pass_env
  - CRM 表 ID (开发信记录 + 客户档案)

拉 IMAP 过去 N 小时新邮件, 匹配"开发信记录.客户邮箱", 命中则更新:
  - 发送状态 = "客户回复"
  - 是否回信 = "是"
  - 跟进状态 = "已转化"

用法:
  python3 imap_poll.py --product esd --dry-run    # 只看, 不写
  python3 imap_poll.py --product esd               # 拉 + 写
  python3 imap_poll.py --product esd --hours 48    # 拉过去 48 小时

环境变量 (按产品):
  - esd:    IMAP_PASS = coco@esdroll.com 阿里云企业邮箱密码
  - tire:   GMAIL_USER = fcjasonliu@gmail.com
            GMAIL_APP_PASS = Gmail App Password (16 位, 非登录密码!)

Gmail App Password 设置:
  Google 账号 → 安全性 → 两步验证 → 应用专用密码 → 选"邮件 + 其他设备" → 生成
"""
import argparse
import datetime as dt
import email
from email.header import decode_header
import imaplib
import json
import os
import re
import subprocess
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent
PRODUCTS_DIR = PIPELINE_ROOT / "products"


def load_config(product_code: str) -> dict:
    config_path = PRODUCTS_DIR / product_code / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"找不到产品配置: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def list_records(lark_cli: str, base_token: str, table_id: str, limit=2000):
    """[自动加, 9-22] 拉表全部记录."""
    out_path = "/tmp/_imap_pull.ndjson"
    if Path(out_path).exists():
        Path(out_path).unlink()
    r = subprocess.run(
        [lark_cli, "base", "+record-list",
         "--base-token", base_token,
         "--table-id", table_id,
         "--limit", str(limit),
         "--format", "ndjson",
         "--output", out_path,
         "--overwrite"],
        capture_output=True, text=True, timeout=300
    )
    if r.returncode != 0:
        raise RuntimeError(f"record-list 失败: {r.stderr}")
    return [json.loads(line) for line in Path(out_path).read_text().strip().split('\n') if line.strip()]


def update_records(lark_cli: str, base_token: str, table_id: str, updates):
    """[自动加, 9-22] 批量更新 (200/批)."""
    if not updates:
        return
    BATCH = 200
    for i in range(0, len(updates), BATCH):
        chunk = updates[i:i+BATCH]
        update_obj = {u["record_id"]: u["fields"] for u in chunk}
        r = subprocess.run(
            [lark_cli, "base", "+record-batch-update",
             "--base-token", base_token,
             "--table-id", table_id,
             "--json", json.dumps({"update_records": update_obj})],
            capture_output=True, text=True, timeout=300
        )
        resp = json.loads(r.stdout)
        if not resp.get("ok"):
            raise RuntimeError(f"批量更新失败: {resp}")


def decode_mime(s):
    """解码邮件 subject/from 里的 =?utf-8?...=."""
    if not s:
        return ""
    parts = decode_header(s)
    decoded = []
    for content, enc in parts:
        if isinstance(content, bytes):
            decoded.append(content.decode(enc or "utf-8", errors="ignore"))
        else:
            decoded.append(content)
    return "".join(decoded)


def extract_email(from_str: str) -> str:
    """'Coco <coco@esdroll.com>' → 'coco@esdroll.com'"""
    m = re.search(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', from_str or "", re.IGNORECASE)
    return (m.group(0) if m else "").lower()


def fetch_imap(cfg: dict, since_hours: int = 24) -> list:
    """连 IMAP 拉 since_hours 内所有邮件. 返回 [{from_email, subject, date}, ...]"""
    import ssl
    host = cfg["imap_host"]
    port = cfg.get("imap_port", 993)
    user = os.environ["cfg.imap_user_env"] if False else os.environ.get(cfg["imap_user_env"], "")
    pwd = os.environ.get(cfg["imap_pass_env"], "")

    if not user:
        raise RuntimeError(f"环境变量 {cfg['imap_user_env']} 未设置")
    if not pwd:
        raise RuntimeError(f"环境变量 {cfg['imap_pass_env']} 未设置")

    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    M.login(user, pwd)
    M.select("INBOX", readonly=True)

    # 拉 N 小时前的邮件 (SINCE 用日期, 所以算 since_date)
    since_date = (dt.datetime.now() - dt.timedelta(hours=since_hours + 24)).strftime("%d-%b-%Y")
    typ, data = M.search(None, f'(SINCE {since_date})')
    if not data or not data[0]:
        M.logout()
        return []

    msgs = []
    for num in data[0].split():
        typ, msg_data = M.fetch(num, "(RFC822)")
        if typ != "OK":
            continue
        raw = msg_data[0][1] if msg_data[0] else b""
        try:
            msg = email.message_from_bytes(raw)
        except Exception:
            continue
        from_email = extract_email(msg.get("From", ""))
        subject = decode_mime(msg.get("Subject", ""))
        date_str = msg.get("Date", "")
        # 只取过去 N 小时内的
        try:
            date_dt = parsedate_to_datetime(date_str)
            if date_dt.tzinfo:
                date_dt = date_dt.astimezone(dt.timezone.utc).replace(tzinfo=None)
            if (dt.datetime.now(dt.timezone.utc) - date_dt).total_seconds() > since_hours * 3600:
                continue
        except Exception:
            pass
        if from_email:
            msgs.append({
                "from_email": from_email,
                "subject": subject[:200],
                "date": date_str,
            })
    M.logout()
    return msgs


def main():
    """[自动加, 9-22] 入口: 拉 IMAP → 匹配邮箱 → 写开发信记录(回复) + 客户档案(保护=true)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, help="如 esd / tire")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    cfg = load_config(args.product)
    crm_cfg = cfg["crm"]
    tbl_outreach = crm_cfg["tables"]["outreach"]
    tbl_customer = crm_cfg["tables"]["customer"]   # 【9-22 修复】标保护客户需要
    lark_cli = crm_cfg["lark_cli"]
    base_token = crm_cfg["base_token"]

    print(f"[{dt.datetime.now().isoformat()}] imap_poll --product={args.product} (hours={args.hours}, dry_run={args.dry_run})")

    # 1. 拉 IMAP
    print(f"  → 连 IMAP {cfg['imap_host']}:{cfg.get('imap_port', 993)} ...")
    try:
        msgs = fetch_imap(cfg, args.hours)
    except Exception as e:
        print(f"  ❌ IMAP 失败: {type(e).__name__}: {e}")
        # 不算致命: gmail 网络偶尔抖, n8n 下次会重跑
        sys.exit(0)
    print(f"     拉到 {len(msgs)} 封新邮件")

    if not msgs:
        print(f"  → 无新邮件, 跳过")
        return

    # 2. 拉开发信记录, 建 email → record_id 索引
    print("  → 拉开发信记录 (匹配邮箱)...")
    outreach = list_records(lark_cli, base_token, tbl_outreach)

    # 索引: 邮箱 → [record_id, ...] (一个邮箱可能发过多次)
    email_to_records = {}
    for r in outreach:
        e = (r.get("客户邮箱") or "").strip().lower()
        if not e:
            continue
        email_to_records.setdefault(e, []).append(r["record_id"])

    # 【9-22 修复】拉客户档案表, 建 email → customer_record_id 索引, 用于标保护客户
    print("  → 拉客户档案 (匹配邮箱 + 标保护客户)...")
    customers = list_records(lark_cli, base_token, tbl_customer)
    email_to_customer = {}   # email → customer record_id
    for c in customers:
        em = (c.get("邮箱") or "").strip().lower()
        if em:
            email_to_customer[em] = c["record_id"]

    # 3. 匹配: 邮件 from_email 在我们发过的列表里 → 标"客户回复"
    updates = []
    matched_emails = set()
    for m in msgs:
        e = m["from_email"]
        if e in email_to_records:
            matched_emails.add(e)
            for rec_id in email_to_records[e]:
                updates.append({
                    "record_id": rec_id,
                    "fields": {
                        "发送状态": ["客户回复"],
                        "是否回信": "是",
                        "跟进状态": ["已转化"],
                    }
                })

    print(f"  → 匹配: {len(matched_emails)} 个客户回复了 (去重), {len(updates)} 条记录待更新")

    if args.dry_run:
        print("\n=== DRY-RUN: 命中的客户 ===")
        for e in sorted(matched_emails):
            subj = next((m["subject"] for m in msgs if m["from_email"] == e), "")
            print(f"  {e:40s} | {subj[:60]}")
        return

    if not updates:
        return

    # 4. 写回 CRM (开发信记录表)
    update_records(lark_cli, base_token, tbl_outreach, updates)
    print(f"  ✅ 更新 {len(updates)} 条开发信记录")

    # 【9-22 修复】5. 标真人回复客户的「保护客户=true」+「回复类型=真人回复」+「最后回信时间=now」
    # 目的: pick_candidates 跳过保护客户, 后续 R2/R3 不再重复发
    cust_updates = []
    for e in matched_emails:
        cid = email_to_customer.get(e)
        if cid:
            cust_updates.append({
                "record_id": cid,
                "fields": {
                    "保护客户": True,
                    "回复类型": ["真人回复"],
                    "最后回信时间": dt.datetime.now().isoformat(timespec='seconds'),
                }
            })
    if cust_updates:
        try:
            update_records(lark_cli, base_token, tbl_customer, cust_updates)
            print(f"  ✅ 标保护客户 {len(cust_updates)} 条 (回复后不再重复发送)")
        except Exception as e:
            print(f"  ⚠️ 标保护客户失败 (non-fatal): {e}")

    # 5. 审计日志
    audit = {
        "ts": dt.datetime.now().isoformat(),
        "product": args.product,
        "imap_total": len(msgs),
        "matched_unique": len(matched_emails),
        "updated": len(updates),
        "matched_emails": sorted(matched_emails),
    }
    log_dir = PRODUCTS_DIR / args.product / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    audit_file = log_dir / f"imap_poll_audit_{dt.datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(audit_file, "a") as f:
        f.write(json.dumps(audit, ensure_ascii=False) + "\n")
    print(f"  审计: {audit_file}")


if __name__ == "__main__":
    main()