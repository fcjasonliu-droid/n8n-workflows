#!/usr/bin/env python3
"""
crm_send.py — 飞书 CRM 开发信发送脚本 (Pipeline v2, product-agnostic)

读 products/<active>/config.json 配置:
  - 客户档案/开发信记录/线索池 表 ID
  - SMTP/IMAP 配置
  - 多轮间隔 / 客户类型优先级
  - 6 套客户类型邮件模板

多轮逻辑:
  Round 1: 客户从未发送过 → A 模板
  Round 2: 距 Round 1 ≥ N 天, 未回信 → Follow-up 模板
  Round 3: 距 Round 2 ≥ M 天, 未回信 → Last Follow-up
  Round ≥ 4: 不再发

用法:
  python3 crm_send.py --product esd --dry-run
  python3 crm_send.py --product esd --daily-limit 50
  python3 crm_send.py --product esd --daily-limit 500 --round 2
"""
import argparse
import datetime as dt
import json
import os
import smtplib
import ssl
import subprocess
import sys
import time
from collections import Counter, defaultdict
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# === Pipeline 路径 ===
PIPELINE_ROOT = Path(__file__).parent.parent
PRODUCTS_DIR = PIPELINE_ROOT / "products"

# === 配置加载 ===
def load_config(product_code: str) -> dict:
    """读 products/<product_code>/config.json"""
    config_path = PRODUCTS_DIR / product_code / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"找不到产品配置: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def get_field_ids(cfg: dict) -> dict:
    """从 config 拉所有字段 ID. 没在 config 里的字段用兜底 (ESD 的 ID)."""
    fld = cfg["crm"]["fields"]
    # 注意: 这里是 ESD 当时的字段 ID. 复制给下个产品时,
    # 下个产品的字段 ID 可能不同, 需要更新 config.json.
    # 提供一个 ESD 兜底.
    ESD_FALLBACK_OUTREACH = {
        "公司名称": "fldcyOzj50", "客户档案ID": "fldBnSLWEm",
        "客户邮箱": "fldV2UHCj4", "国家": "fldMrhXobc",
        "发送状态": "fldJGFiiSL", "成功标记": "fldsh8yPyq",
        "退信原因": "fldbwJAk8N", "回信标记": "fldCS26QZX",
        "邮件主题": "fldW61abA7", "发送日期": "fldek7ixAA",
        "创建时间": "fldAqDbjFo", "跟进次数": "fld5nTYKJX",
        "AI模型": "fldW8h8gCJ", "是否成功": "fldLrMnjpD",
        "是否回信": "fldETY0AOU", "轮次": "fldw3CIi7F",
        "跟进状态": "fldOxtZn6k",
    }
    ESD_FALLBACK_KH = {
        "公司名称": "fldcyOzj50", "邮箱": "fldV2UHCj4",
        "国家": "fldMrhXobc", "客户类型": "fldtypeXYZ",
        "来源": "fldsourceXYZ", "保护客户": "fldprotectXYZ",
    }
    return {
        "outreach": ESD_FALLBACK_OUTREACH,
        "customer": ESD_FALLBACK_KH,
    }


# === 模板生成 (R1 / R2 / R3) ===
def template_for_round(base_tmpl: dict, round_num: int, product: str = "ESD") -> dict:
    if round_num == 1:
        return base_tmpl
    subject_base = base_tmpl.get("subject", "")
    body = base_tmpl.get("body", "")
    if round_num == 2:
        return {
            "subject": f"Re: {subject_base}",
            "body": (
                "Dear Sir/Madam,\n\n"
                f"Hope this message finds you well. I'm following up on the email I sent last month regarding our {product} products.\n\n"
                f"{body.split(chr(10)+chr(10), 1)[1] if chr(10)+chr(10) in body else body}\n\n"
                "If you have any questions or would like to discuss pricing / specs, please don't hesitate to reach out.\n\n"
                "Best regards,\n${FROM_NAME}\n${COMPANY}"
            ),
        }
    else:
        return {
            "subject": f"Last follow-up: {subject_base}",
            "body": (
                "Dear Sir/Madam,\n\n"
                "This is my last follow-up on our previous email. I understand timing may not have been right.\n\n"
                "If you would like to revisit this in the future, please feel free to reach out anytime. Our catalog and certifications remain available.\n\n"
                "Wishing you and your business all the best.\n\n"
                "Best regards,\n${FROM_NAME}\n${COMPANY}"
            ),
        }


# === lark-cli helpers ===
def lark_records(lark_cli: str, base_token: str, table_id: str, limit=2000):
    """[自动加, 9-22] 拉表全部记录 (ndjson 输出)."""
    out_path = "/tmp/_send_pull.ndjson"
    if os.path.exists(out_path):
        os.remove(out_path)
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
    content = Path(out_path).read_text().strip()
    return [json.loads(line) for line in content.split('\n') if line.strip()]


def lark_batch_create(lark_cli: str, base_token: str, table_id: str, records):
    """[自动加, 9-22] 批量创建记录 (200/批)."""
    if not records:
        return []
    new_ids = []
    BATCH = 200
    for i in range(0, len(records), BATCH):
        chunk = records[i:i+BATCH]
        body = {"create_records": chunk}
        r = subprocess.run(
            [lark_cli, "base", "+record-batch-create",
             "--base-token", base_token,
             "--table-id", table_id,
             "--json", json.dumps(body)],
            capture_output=True, text=True, timeout=300
        )
        resp = json.loads(r.stdout)
        if not resp.get("ok"):
            raise RuntimeError(f"批量写入失败: {resp}")
        ids = resp.get("data", {}).get("record_id_list") or resp.get("data", {}).get("record_ids") or []
        new_ids.extend(ids)
    return new_ids


# === 黑名单 (防止老 launchd 时代漏写 CRM 的客户被重发) ===
def _load_local_blacklist(product_code: str) -> set:
    sent = set()
    bl_path = PRODUCTS_DIR / product_code / "data" / "sent_blacklist.json"
    if bl_path.exists():
        try:
            for e in json.loads(bl_path.read_text()):
                sent.add(str(e).lower())
        except Exception:
            pass
    # 兼容老路径
    old_queues = Path("/Users/jason/.hermes/profiles/foreign-trade/workspace/customer_research/outreach_queue")
    if old_queues.exists():
        for csv_file in old_queues.glob("send_log_*.csv"):
            try:
                with open(csv_file, "r", encoding="utf-8") as f:
                    next(f)
                    for line in f:
                        parts = line.split(",", 2)
                        if len(parts) >= 3:
                            email = parts[2].strip().strip('"').lower()
                            if "@" in email:
                                sent.add(email)
            except Exception:
                pass
    return sent


def _add_to_blacklist(product_code: str, emails: list):
    """[自动加, 9-22] 把刚发的邮箱写回 sent_blacklist.json (持久化)."""
    if not emails:
        return
    bl_path = PRODUCTS_DIR / product_code / "data" / "sent_blacklist.json"
    bl_path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if bl_path.exists():
        try:
            existing = set(json.loads(bl_path.read_text()))
        except Exception:
            pass
    for e in emails:
        if e:
            existing.add(str(e).strip().lower())
    bl_path.write_text(json.dumps(sorted(existing), ensure_ascii=False, indent=2))


# === 客户历史索引 ===
def build_history(outreach_records: list) -> dict:
    history = defaultdict(list)
    for r in outreach_records:
        cid = r.get("客户档案ID")
        if not cid:
            continue
        try:
            rnd = int(r.get("轮次") or 1)
        except (ValueError, TypeError):
            rnd = 1
        status = r.get("发送状态") or []
        if isinstance(status, list):
            status = ",".join(status)
        history[cid].append({
            "round": rnd,
            "sent_date": r.get("发送日期") or "",
            "status": status,
            "replied": status == "客户回复",
        })
    for cid in history:
        history[cid].sort(key=lambda x: (x["round"], x["sent_date"]))
    return history


def next_round(history: list, today: dt.date, max_round: int, intervals: dict) -> int:
    if not history:
        return 1
    for h in history:
        if h["replied"]:
            return 0
    max_r = max(h["round"] for h in history)
    if max_r >= max_round:
        return 0
    next_r = max_r + 1
    last_for_round = None
    for h in history:
        if h["round"] == max_r:
            if not last_for_round or h["sent_date"] > last_for_round["sent_date"]:
                last_for_round = h
    if not last_for_round:
        return next_r
    try:
        s = last_for_round["sent_date"].split(".")[0].replace("T", " ")
        if "+" in s:
            s = s.split("+")[0].strip()
        last_date = dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").date()
    except Exception:
        return next_r
    interval = intervals.get(str(next_r), intervals.get(next_r, 30))
    if (today - last_date).days >= interval:
        return next_r
    return 0


def pick_candidates(customers, history, blacklist, limit, max_round, intervals, priority_map, force_round=None, today=None):
    """[自动加, 9-22] 跳过保护客户+黑名单+已发 → 选候选客户. 优先级 A→E."""
    today = today or dt.date.today()
    candidates = []
    for c in customers:
        if c.get("保护客户"):
            continue
        email = (c.get("邮箱") or "").strip().lower()
        if not email or "@" not in email:
            continue
        if email in blacklist:
            continue
        cust_history = history.get(c["record_id"], [])

        if force_round is not None:
            if force_round == 1:
                rnd = 1
            else:
                prev = [h for h in cust_history if h["round"] == force_round - 1]
                if not prev:
                    continue
                latest_prev = max(prev, key=lambda x: x["sent_date"])
                try:
                    s = latest_prev["sent_date"].split(".")[0].replace("T", " ")
                    if "+" in s:
                        s = s.split("+")[0].strip()
                    last_date = dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").date()
                except Exception:
                    continue
                interval = intervals.get(str(force_round), intervals.get(force_round, 30))
                if (today - last_date).days < interval:
                    continue
            rnd = force_round
        else:
            rnd = next_round(cust_history, today, max_round, intervals)
        if rnd == 0:
            continue

        ctype = c.get("客户类型")
        if isinstance(ctype, list):
            ctype = ctype[0] if ctype else ""
        priority = priority_map.get(ctype, 9)
        candidates.append((priority, rnd, c))
    candidates.sort(key=lambda x: (x[0], x[1]))
    return [(c, r) for _, r, c in candidates[:limit]]


# === SMTP 发送 ===
def load_smtp_cfg(cfg):
    """把 product config 的 smtp_xxx_env 字段 转 crm_send 期望的 smtp dict"""
    return {
        "host": cfg.get("smtp_host"),
        "port": cfg.get("smtp_port", 465),
        "user_env": cfg.get("smtp_user_env", "PUSH_SMTP_USER"),
        "pass_env": cfg.get("smtp_pass_env", "PUSH_SMTP_PASS"),
        "reply_to": cfg.get("reply_to", cfg.get("from_email")),
        "from_name": cfg.get("from_name", cfg.get("company_name", "Sales")),
        "attachments": cfg.get("attachments", []),
    }


# === 开发信闸门 (2026-09-20 Jason 下令: 不发, 先建流程) ===
GATE_LOCK = PIPELINE_ROOT / "data" / "SEND_GATE.lock"


def check_send_gate(product_code: str, force: bool = False) -> tuple:
    """
    返回 (allowed: bool, reason: str).
    闸门规则:
      1. SEND_GATE.lock 存在 → 全部拒 (除非 force=True)
      2. product_code 在 lock 里的 deny 列表 → 拒 (单产品锁)
      3. product_code 在 lock 里的 allow 列表 → 放
      4. 都不在 → 拒 (默认拒, 显式 allow 才发)
    """
    if not GATE_LOCK.exists():
        return False, f"GATE: lock 文件不存在 ({GATE_LOCK}), 默认拒发. 创建并加入 allow 才能真发."
    try:
        cfg_lock = json.loads(GATE_LOCK.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"GATE: lock 文件 JSON 解析失败 ({e}), 默认拒发."
    if not isinstance(cfg_lock, dict):
        return False, "GATE: lock 必须是 JSON 对象 {allow: [...], deny: [...], note: '...'}"
    if cfg_lock.get("global_block") is True and not force:
        return False, f"GATE: global_block=True, 全部拒. note: {cfg_lock.get('note','')}"
    deny = cfg_lock.get("deny", []) or []
    if product_code in deny and not force:
        return False, f"GATE: 产品 '{product_code}' 在 deny 列表, 拒发. note: {cfg_lock.get('note','')}"
    allow = cfg_lock.get("allow", []) or []
    if not allow:
        return False, f"GATE: allow 列表为空, 默认拒发. note: {cfg_lock.get('note','')}"
    if "*" not in allow and product_code not in allow and not force:
        return False, f"GATE: 产品 '{product_code}' 不在 allow 列表 {allow}, 拒发."
    return True, f"GATE: 放行 (product={product_code}, note={cfg_lock.get('note','')})"


def send_one(customer, template, smtp_cfg, product_code: str = "", force: bool = False) -> tuple:
    """真发一封. 默认拒, 必须闸门放行 + force=True."""
    allowed, reason = check_send_gate(product_code, force=force)
    if not allowed:
        return False, f"BLOCKED_BY_GATE: {reason}", template.get("subject", "")

    email = customer["邮箱"]
    subject = template["subject"]
    body = template["body"]

    host = smtp_cfg["host"]
    port = smtp_cfg["port"]
    user = os.environ.get(smtp_cfg["user_env"], smtp_cfg["user_env_default"])
    pwd = os.environ.get(smtp_cfg["pass_env"], "")
    reply_to = smtp_cfg["reply_to"]
    from_name = smtp_cfg["from_name"]
    attachments = smtp_cfg.get("attachments", [])

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = email
    msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for path_str in attachments:
        path = Path(path_str)
        if path.exists():
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(part)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
            smtp.login(user, pwd)
            smtp.sendmail(user, [email], msg.as_string())
        return True, None, subject
    except Exception as e:
        return False, str(e), subject


def get_template(cfg, customer_type, round_num):
    if isinstance(customer_type, list):
        customer_type = customer_type[0] if customer_type else ""
    templates = cfg.get("email_templates", {}).get(str(round_num), {})
    base = templates.get(customer_type)
    if not base:
        # 兜底用 C-潜在经销商 模板
        base = templates.get("C-潜在经销商") or templates.get(list(templates.keys())[0] if templates else "", {})
    return template_for_round(base or {"subject": "Inquiry", "body": "Hello,"}, round_num, product=cfg.get("product_name", ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, help="如 esd / tire")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--daily-limit", type=int, default=None, help="每日上限")
    ap.add_argument("--round", type=int, choices=[1, 2, 3], default=None)
    ap.add_argument("--i-have-permission", action="store_true",
                    help="显式绕过 SEND_GATE 闸门. 闸门 + 此 flag 才能真发.")
    args = ap.parse_args()

    cfg = load_config(args.product)
    daily_limit = args.daily_limit or cfg.get("daily_limit", 500)
    max_round = cfg["rounds"]["max_round"]
    intervals = cfg["rounds"]["intervals_days"]
    priority_map = cfg.get("customer_priority", {})

    smtp_cfg = {
        "host": cfg["smtp_host"],
        "port": cfg["smtp_port"],
        "user_env": cfg["smtp_user_env"],
        "user_env_default": cfg["from_email"],
        "pass_env": cfg["smtp_pass_env"],
        "reply_to": cfg["reply_to"],
        "from_name": cfg["from_name"],
        "attachments": cfg.get("attachments", []),
    }

    crm_cfg = cfg["crm"]
    tbl_cust = crm_cfg["tables"]["customer"]
    tbl_outreach = crm_cfg["tables"]["outreach"]

    today = dt.date.today()
    today_str = today.strftime("%Y-%m-%d")
    print(f"[{dt.datetime.now().isoformat()}] crm_send --product={args.product} (dry_run={args.dry_run}, limit={daily_limit}, round={args.round})")

    print("  → 拉客户档案...")
    customers = lark_records(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_cust)
    print(f"     客户档案: {len(customers)}")

    print("  → 拉开发信记录...")
    outreach = lark_records(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_outreach)
    print(f"     开发信记录: {len(outreach)}")

    # 今日已发数
    today_sent = 0
    today_per_round = Counter()
    for r in outreach:
        sd = r.get("发送日期") or ""
        if sd.startswith(today_str):
            status = r.get("发送状态") or []
            if isinstance(status, list):
                status = ",".join(status)
            if status == "已发送":
                today_sent += 1
                try:
                    rnd = int(r.get("轮次") or 1)
                except (ValueError, TypeError):
                    rnd = 1
                today_per_round[rnd] += 1
    print(f"  → 今日已发: {today_sent} / {daily_limit} | 按轮次: {dict(today_per_round)}")

    remaining = daily_limit - today_sent
    if remaining <= 0:
        print(f"⏸️  今日配额已满 ({today_sent}), 跳过")
        return

    history = build_history(outreach)
    blacklist = _load_local_blacklist(args.product)
    print(f"  → 本地黑名单: {len(blacklist)} 个邮箱")

    candidates = pick_candidates(customers, history, blacklist, remaining,
                                 max_round, intervals, priority_map,
                                 force_round=args.round, today=today)
    rnd_counter = Counter(r for _, r in candidates)
    print(f"  → 候选客户: {len(candidates)} (按轮次: {dict(rnd_counter)})")

    if args.dry_run:
        print(f"\n=== DRY-RUN ({args.product}): 计划发送 (按轮次分组预览) ===")
        for rnd in sorted(rnd_counter.keys()):
            print(f"\n--- Round {rnd} (计划 {rnd_counter[rnd]} 封) ---")
            for c, _ in [x for x in candidates if x[1] == rnd][:5]:
                ctype = c.get("客户类型") or ""
                if isinstance(ctype, list):
                    ctype = ctype[0] if ctype else ""
                print(f"  R{rnd} | {c.get('公司名称','?'):40s} | {c.get('邮箱'):40s} | {ctype}")
        print(f"\nDRY-RUN 完成. {len(candidates)} 条候选, 不会真发.")
        return

    # === 真发前闸门预检 (2026-09-20 Jason 下令) ===
    print("\n=== SEND GATE CHECK ===")
    allowed, reason = check_send_gate(args.product, force=args.i_have_permission)
    print(f"  闸门: {'✅ 放行' if allowed else '🚫 拒发'}")
    print(f"  reason: {reason}")
    if not allowed:
        print(f"\n🚫 全局拒发. 如要真发: (1) 编辑 {GATE_LOCK} 加入 allow/{args.product}")
        print(f"   (2) 跑命令时加 --i-have-permission flag")
        print(f"   当前 dry_run 计划 ({len(candidates)} 封) 不写入, 不扣额度.")
        return

    # 真发
    from classify import classify_bounce  # 延迟 import (避免干跑时没装)

    success_log, fail_log, out_to_write = [], [], []
    for i, (c, rnd) in enumerate(candidates, 1):
        ctype = c.get("客户类型") or ""
        if isinstance(ctype, list):
            ctype = ctype[0] if ctype else ""
        template = get_template(cfg, ctype, rnd)
        ok, err, subject = send_one(c, template, smtp_cfg, product_code=args.product, force=args.i_have_permission)

        country = c.get("国家") or ["未分类"]
        if isinstance(country, list):
            country_str = country[0] if country else "未分类"
        else:
            country_str = country if country else "未分类"

        send_date = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if rnd < max_round:
            follow_status = f"等待跟进R{rnd + 1}"
        else:
            follow_status = cfg["rounds"]["follow_status_labels"][2]  # 跟进完结

        common = {
            "客户档案ID": c["record_id"],
            "公司名称": c.get("公司名称") or "",
            "客户邮箱": c["邮箱"],
            "国家": country_str,
            "发送日期": send_date,
            "AI模型": cfg.get("ai_model", ""),
            "轮次": rnd,
            "跟进状态": [follow_status],
        }

        if ok:
            success_log.append((c["record_id"], rnd))
            out_to_write.append({
                **common,
                "发送状态": ["已发送"],
                "成功标记": ["发送成功"],
                "是否成功": "OK",
                "邮件主题": subject,
                "跟进次数": str(rnd),
                "回信标记": ["未回信"],
                "是否回信": "否",
            })
        else:
            fail_log.append((c["record_id"], rnd, err))
            cat = classify_bounce(err)
            out_to_write.append({
                **common,
                "发送状态": ["发送失败"],
                "成功标记": ["发送未成功"],
                "是否成功": "FAIL",
                "退信原因": [cat],
                "邮件主题": subject,
                "跟进次数": str(rnd),
            })
        print(f"  [{i}/{len(candidates)}] R{rnd} {'✅' if ok else '❌'} {c.get('公司名称','?'):35s} | {c.get('邮箱'):40s} | {('OK' if ok else cat)}")
        time.sleep(1.5)

    # 回写 CRM
    print(f"\n  → 写回开发信记录 {len(out_to_write)} 条...")
    new_ids = lark_batch_create(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_outreach, out_to_write)
    print(f"     ✅ 写入成功 {len(new_ids)} 条")

    # 黑名单
    sent_emails = [c["邮箱"] for c, _ in candidates]
    _add_to_blacklist(args.product, sent_emails)
    print(f"     黑名单已更新 ({len(sent_emails)} 邮箱)")

    # 审计
    log_dir = PRODUCTS_DIR / args.product / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "ts": dt.datetime.now().isoformat(),
        "product": args.product,
        "today_sent_before": today_sent,
        "today_sent_after": today_sent + len(success_log),
        "rounds": dict(Counter(r for _, r in success_log)),
        "attempted": len(candidates),
        "success": len(success_log),
        "failed": len(fail_log),
        "bounce_categories": dict(Counter([classify_bounce(e) for _, _, e in fail_log])),
        "fail_count_total": len(fail_log),
        "fail_errors": [str(e)[:200] for _, _, e in fail_log][:10],
    }
    audit_file = log_dir / f"send_audit_{today.strftime('%Y%m%d')}.jsonl"
    with open(audit_file, "a") as f:
        f.write(json.dumps(audit, ensure_ascii=False) + "\n")
    print(f"\n✅ 完成 [{args.product}]. 审计: {audit_file}")
    print(f"   尝试 {len(candidates)} | 成功 {len(success_log)} | 失败 {len(fail_log)}")
    print(f"   按轮次成功: {dict(Counter(r for _, r in success_log))}")


if __name__ == "__main__":
    main()
