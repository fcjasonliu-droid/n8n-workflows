#!/usr/bin/env python3
"""
manual_import.py — 人工线索批量导入工具

用法:
  1. 把线索写成 CSV (header: 公司名称,邮箱,国家,客户类型,来源,备注,联系人,电话,WhatsApp)
  2. python3 manual_import.py --product tire --csv my_leads.csv --dry-run
  3. python3 manual_import.py --product tire --csv my_leads.csv

或者一行一公司:
  python3 manual_import.py --product tire \
    --add "Auto Parts Lagos Ltd,sales@autopartslagos.ng,NG,A-进口贸易商,Yellowpage,Lagos Island 进口商"
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent
PRODUCTS_DIR = PIPELINE_ROOT / "products"


def load_config(product_code):
    return json.loads((PRODUCTS_DIR / product_code / "config.json").read_text(encoding="utf-8"))


def is_valid_email(email):
    if not email:
        return False
    return bool(re.match(r'^[\w.+-]+@[\w.-]+\.[a-z]{2,}$', email.strip(), re.IGNORECASE))


def safe_lark_run(args, timeout=300):
    """跑 lark-cli, capture_output 偶尔丢中文 stdout 时用 Popen 兜底."""
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if not r.stdout.strip():
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=timeout)
        r.stdout = out
        r.stderr = err or r.stderr
    return r


def write_to_leads(cfg, leads):
    LARK = cfg["crm"]["lark_cli"]
    BASE = cfg["crm"]["base_token"]
    TBL = cfg["crm"]["tables"]["leads"]
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    records = []
    for l in leads:
        if not is_valid_email(l.get("邮箱", "")):
            print(f"  ⚠️ 跳过 (邮箱无效): {l.get('公司名称','?')}")
            continue
        records.append({
                "公司名称": l.get("公司名称", "")[:100],
                "邮箱": l["邮箱"].strip(),
                "国家": [l.get("国家") or "未分类"],
                "审核状态": ["待审核"],
                "来源": [l.get("来源") or "人工"],
                "备注": l.get("备注", "")[:500],
                "客户类型": [l.get("客户类型") or "未分类"],
                "首次入库日期": today,
            })

    if not records:
        print("  → 没有有效记录可写")
        return 0

    BATCH = 200
    write_ok = 0
    for i in range(0, len(records), BATCH):
        chunk = records[i:i+BATCH]
        r = safe_lark_run(
            [LARK, "base", "+record-batch-create",
             "--base-token", BASE,
             "--table-id", TBL,
             "--json", json.dumps({"create_records": chunk}, ensure_ascii=False)],
            timeout=300
        )
        try:
            resp = json.loads(r.stdout)
            if resp.get("ok"):
                ids = resp.get("data", {}).get("record_id_list", [])
                write_ok += len(ids)
            else:
                print(f"    ❌ batch 失败: {resp}")
        except Exception as e:
            print(f"    ❌ parse fail: {r.stdout[:200]}")
    print(f"  ✅ 写入线索池: {write_ok}/{len(records)} 条")
    return write_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--csv", help="CSV 文件 (含 header)")
    ap.add_argument("--add", action="append", help="单条添加, 格式: 公司名,邮箱,国家,客户类型,来源,备注")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.product)
    leads = []

    if args.csv:
        if not Path(args.csv).exists():
            print(f"❌ CSV 不存在: {args.csv}")
            sys.exit(1)
        with open(args.csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                leads.append({
                    "公司名称": row.get("公司名称", "").strip(),
                    "邮箱": row.get("邮箱", "").strip(),
                    "国家": row.get("国家", "").strip(),
                    "客户类型": row.get("客户类型", "").strip(),
                    "来源": row.get("来源", "人工").strip(),
                    "备注": row.get("备注", "").strip(),
                })
        print(f"  CSV 读入: {len(leads)} 条")

    if args.add:
        for line in args.add:
            parts = line.split(",")
            if len(parts) < 3:
                print(f"  ⚠️ 跳过 (字段不足): {line}")
                continue
            leads.append({
                "公司名称": parts[0].strip(),
                "邮箱": parts[1].strip(),
                "国家": parts[2].strip() if len(parts) > 2 else "未分类",
                "客户类型": parts[3].strip() if len(parts) > 3 else "",
                "来源": parts[4].strip() if len(parts) > 4 else "人工",
                "备注": ",".join(parts[5:]).strip() if len(parts) > 5 else "",
            })

    if not leads:
        print("❌ 没有任何线索 (--csv 或 --add)")
        return

    valid = [l for l in leads if is_valid_email(l.get("邮箱", ""))]
    invalid = [l for l in leads if not is_valid_email(l.get("邮箱", ""))]
    print(f"\n  有效: {len(valid)} | 无效: {len(invalid)}")
    if invalid:
        print(f"  ⚠️ 无效邮箱样例:")
        for l in invalid[:5]:
            print(f"    - {l.get('公司名称','?')}: '{l.get('邮箱','?')}'")

    if args.dry_run:
        print("\n=== DRY-RUN: 前 5 条 ===")
        for l in valid[:5]:
            print(f"  {l.get('公司名称','?'):35s} | {l.get('邮箱'):40s} | {l.get('国家')} | {l.get('客户类型')}")
        return

    write_to_leads(cfg, valid)


if __name__ == "__main__":
    main()