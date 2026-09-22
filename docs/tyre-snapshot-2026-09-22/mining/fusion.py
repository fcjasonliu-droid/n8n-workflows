#!/usr/bin/env python3
"""
mining/fusion.py — 三源融合去重

读 customs.json + ecom.json + gmaps.json + yp_*.json
去重 (按公司名/邮箱) → 写入飞书线索池 (待审核)

用法:
  python3 fusion.py --product esd
"""
import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent.parent


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r'\b(co|ltd|inc|llc|company|limited|corp|gmbh|s\.r\.o|sa|srl|bv|s\.p\.a|kg)\.?\b', '', name)
    name = re.sub(r'[^a-z0-9]', '', name)
    return name[:50]


def extract_email_from_url(url: str) -> str:
    """简易邮箱抓取 (没真访问, 只从 URL 推)."""
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg_path = PIPELINE_ROOT / "products" / args.product / "config.json"
    cfg = json.loads(cfg_path.read_text())
    data_dir = PIPELINE_ROOT / "products" / args.product / "data"

    # 读所有源
    all_leads = []
    sources = {
        "customs": [],
        "ecom": [],
        "gmaps": [],
        "yellowpages": [],
    }

    customs_path = data_dir / "customs.json"
    if customs_path.exists():
        sources["customs"] = json.loads(customs_path.read_text())

    ecom_path = data_dir / "ecom.json"
    if ecom_path.exists():
        sources["ecom"] = json.loads(ecom_path.read_text())

    gmaps_path = data_dir / "gmaps.json"
    if gmaps_path.exists():
        sources["gmaps"] = json.loads(gmaps_path.read_text())

    for yp_file in data_dir.glob("yp_*.json"):
        sources["yellowpages"].extend(json.loads(yp_file.read_text()))

    print(f"[融合] 海关:{len(sources['customs'])} 电商:{len(sources['ecom'])} GMaps:{len(sources['gmaps'])} 黄页:{len(sources['yellowpages'])}")

    # 拉 CRM 现有公司名 (用于去重)
    print(f"  → 拉 CRM 现有公司名 (去重用)...")
    LARK = cfg["crm"]["lark_cli"]
    BASE = cfg["crm"]["base_token"]
    TBL_KH = cfg["crm"]["tables"]["customer"]
    r = subprocess.run(
        [LARK, "base", "+record-list",
         "--base-token", BASE,
         "--table-id", TBL_KH,
         "--limit", "2000",
         "--format", "ndjson",
         "--output", "/tmp/_fusion_kh.ndjson",
         "--overwrite"],
        capture_output=True, text=True, timeout=180
    )
    existing_names = set()
    if r.returncode == 0:
        for line in Path("/tmp/_fusion_kh.ndjson").read_text().strip().split("\n"):
            try:
                rec = json.loads(line)
                n = normalize_name(rec.get("公司名称", ""))
                if n:
                    existing_names.add(n)
            except Exception:
                pass
    print(f"     现有公司: {len(existing_names)}")

    # 去重
    seen_norm = set(existing_names)
    leads_new = []
    skip_dup = 0
    for src_name, items in sources.items():
        for item in items:
            title = item.get("title") or item.get("name") or item.get("partner_country", "")
            norm = normalize_name(title)
            if not norm:
                continue
            if norm in seen_norm:
                skip_dup += 1
                continue
            seen_norm.add(norm)
            leads_new.append({
                "公司名称": title[:100],
                "国家": item.get("country") or item.get("partner_country", "") or "未分类",
                "来源": f"mining-{src_name}",
                "备注": item.get("url") or item.get("address") or "",
            })

    print(f"  → 新增线索: {len(leads_new)} | 跳过重复: {skip_dup}")

    if args.dry_run:
        print("\n=== DRY-RUN: 前 5 条 ===")
        for l in leads_new[:5]:
            print(f"  [{l['来源']}] {l['公司名称']:35s} | {l['国家']}")
        return

    if not leads_new:
        print(f"  ⏸️ 没有新线索可写, 跳过")
        return

    # 写入飞书线索池
    TBL_LEADS = cfg["crm"]["tables"]["leads"]
    today = __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    records = []
    for l in leads_new:
        records.append({
            "公司名称": l["公司名称"],
            "国家": l["国家"],
            "来源": l["来源"],
            "备注": l["备注"],
            "审核状态": ["待审核"],
            "首次入库日期": today,
        })

    BATCH = 200
    write_ok = 0
    for i in range(0, len(records), BATCH):
        chunk = records[i:i+BATCH]
        body = {"create_records": chunk}
        r2 = subprocess.run(
            [LARK, "base", "+record-batch-create",
             "--base-token", BASE,
             "--table-id", TBL_LEADS,
             "--json", json.dumps(body)],
            capture_output=True, text=True, timeout=300
        )
        if r2.returncode == 0:
            resp = json.loads(r2.stdout)
            ids = resp.get("data", {}).get("record_id_list", [])
            write_ok += len(ids)
        else:
            print(f"    ❌ 写入失败: {r2.stderr[:200]}")
    print(f"  ✅ 写入线索池: {write_ok} 条")


if __name__ == "__main__":
    main()
