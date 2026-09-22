#!/usr/bin/env python3
"""
mining/strip_curtain.py — 流利条 (PVC Strip Curtain) 拓客脚本

用途: 独立产品线 (从 ESD 拆出来), 通过 Google Maps / 海关 / 黄页 / 电商 4 个源
      挖掘 PVC 流利条买家, 写入 ESD-流利条 CRM 客户档案表.

设计原则:
  - 完全独立于 ESD CRM: 独立 base (Bi3GbbjTaaba2UspTKCc4azAneQ) / 独立脚本
  - 流利条买家决策人跟 ESD 不同 (仓库设施 vs 电子防护), 但场景有交叉
  - 复用 gmaps/customs/yellowpages/ecom 子模块, 不重复实现
  - dry-run 默认开, 真跑加 --write

用法:
  # 单源 + 单国家 dry-run
  python3 strip_curtain.py --source gmaps --country US --limit 10 --dry-run

  # 跑全部源 + 全部国家 (默认 dry-run)
  python3 strip_curtain.py --source all --country all

  # 真跑 + 写入 CRM
  python3 strip_curtain.py --source all --country all --write
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent.parent
PRODUCTS_ROOT = PIPELINE_ROOT / "products"
SCRIPTS_DIR = PIPELINE_ROOT / "scripts" / "mining"
DATA_DIR = PRODUCTS_ROOT / "strip_curtain" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = PRODUCTS_ROOT / "strip_curtain" / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_subprocess(cmd: list, timeout: int = 300) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stderr": "timeout"}
    except FileNotFoundError as e:
        return {"ok": False, "stderr": f"executable not found: {e}"}
    except Exception as e:
        return {"ok": False, "stderr": f"unexpected: {type(e).__name__}: {e}"}
    if r.returncode != 0:
        return {"ok": False, "stderr": (r.stderr or "")[:300]}
    try:
        return {"ok": True, "data": json.loads(r.stdout)}
    except Exception:
        return {"ok": True, "raw": r.stdout[-500:]}


def mine_gmaps(cfg: dict, country: str, limit: int) -> list:
    if not os.environ.get("GOOGLE_MAPS_API_KEY"):
        log(f"  ⚠️ {country}: GOOGLE_MAPS_API_KEY 未设, 跳过 GMaps")
        return []
    results = []
    keywords = cfg.get("keywords", [])[:3]  # 取前 3 个关键词
    for kw in keywords:
        cmd = ["python3", str(SCRIPTS_DIR / "gmaps.py"),
               "--product", "strip_curtain",
               "--country", country,
               "--keyword", f"{kw} {country}",
               "--limit", str(limit)]
        r = run_subprocess(cmd)
        if r.get("ok"):
            for p in (r.get("data") or {}).get("places", []):
                results.append({
                    "company": p.get("displayName", {}).get("text", ""),
                    "country": country,
                    "phone": p.get("nationalPhoneNumber", ""),
                    "website": p.get("websiteUri", ""),
                    "address": p.get("formattedAddress", ""),
                    "source": "谷歌地图搜索",
                    "customer_type": "B-工业品分销商",
                    "product_tag": "流利条",
                    "raw_keyword": kw,
                })
        time.sleep(1)
    return results


def mine_customs(cfg: dict, country: str, limit: int) -> list:
    """UN Comtrade: HS 3920 进口商. customs 不返公司, 仅做市场验证."""
    cmd = ["python3", str(SCRIPTS_DIR / "customs.py"),
           "--product", "strip_curtain",
           "--country", country,
           "--year", "2024",
           "--limit", str(limit)]
    r = run_subprocess(cmd)
    if not r.get("ok"):
        return []
    log(f"  ℹ️ {country}: HS3920 海关数据 = {len(r.get('data', []))} partner (市场验证用, 不入库)")
    return []


def mine_yellowpages(cfg: dict, country: str, limit: int) -> list:
    """黄页 (越南/全球)."""
    cmd = ["python3", str(SCRIPTS_DIR / "yellowpages.py"),
           "--product", "strip_curtain",
           "--country", country,
           "--keyword", "PVC strip curtain",
           "--limit", str(limit)]
    r = run_subprocess(cmd)
    if not r.get("ok"):
        return []
    return r.get("data", [])


def mine_ecom(cfg: dict, country: str, limit: int) -> list:
    """电商反推 (Alibaba/Amazon)."""
    cmd = ["python3", str(SCRIPTS_DIR / "ecommerce.py"),
           "--product", "strip_curtain",
           "--country", country,
           "--keyword", "PVC strip curtain",
           "--limit", str(limit)]
    r = run_subprocess(cmd)
    if not r.get("ok"):
        return []
    return r.get("data", [])


def write_to_crm(cfg: dict, leads: list) -> int:
    """批量写入流利条 CRM 客户档案表. 返回成功写入数."""
    if not leads:
        return 0
    # 加载 base token env
    base_token = os.environ.get(cfg["crm"]["base_token_env"])
    if not base_token:
        log(f"❌ 缺少 env var {cfg['crm']['base_token_env']}, 拒绝写入")
        return 0

    lark = "/Users/jason/.npm-global/bin/lark-cli"
    tbl = cfg["crm"]["tables"]["customer"]

    # 转 records (multi-select 用数组)
    records = []
    for l in leads:
        records.append({
            "公司名称": l.get("company", ""),
            "邮箱": l.get("email", ""),
            "国家": [l["country"]] if l.get("country") else ["其他"],
            "客户类型": [l.get("customer_type", "C-潜在经销商")],
            "来源": [l.get("source", "关键词搜索")],
            "产品标签": ["流利条"],
            "公司网站": l.get("website", ""),
            "首次建档": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
        })

    # 批量写入 (单次 ≤200)
    total = 0
    BATCH = 200
    for i in range(0, len(records), BATCH):
        chunk = records[i:i+BATCH]
        body = {"create_records": chunk}
        try:
            r = subprocess.run(
                [lark, "base", "+record-batch-create",
                 "--base-token", base_token,
                 "--table-id", tbl,
                 "--json", json.dumps(body)],
                capture_output=True, text=True, timeout=300
            )
            resp = json.loads(r.stdout)
            if not resp.get("ok"):
                log(f"  ❌ 批量写入失败: {resp.get('error', resp)[:200]}")
                continue
            ids = resp.get("data", {}).get("record_id_list") or []
            total += len(ids)
            log(f"  ✅ batch {i//BATCH+1}: +{len(ids)} 条")
        except Exception as e:
            log(f"  ❌ batch {i//BATCH+1} 异常: {e}")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="gmaps", choices=["gmaps", "customs", "yellowpages", "ecom", "all"])
    ap.add_argument("--country", default="US", help="ISO 2 国家码, 或 'all'")
    ap.add_argument("--limit", type=int, default=10, help="每个国家每个 keyword 上限")
    ap.add_argument("--dry-run", action="store_true", default=True, help="默认 dry-run")
    ap.add_argument("--write", action="store_true", help="真写入 CRM")
    args = ap.parse_args()

    # --write 自动关闭 dry-run
    if args.write:
        args.dry_run = False

    cfg = load_config()
    countries = cfg["countries_mining"] if args.country == "all" else [args.country]
    sources = ["gmaps", "customs", "yellowpages", "ecom"] if args.source == "all" else [args.source]
    keywords = cfg.get("keywords", [])

    log(f"开始流利条拓客: source={args.source} country={args.country} mode={'DRY-RUN' if args.dry_run else 'WRITE'}")
    log(f"  拓客国家: {countries}")
    log(f"  拓客源: {sources}")
    log(f"  关键词 (前5): {keywords[:5]}")

    all_leads = []
    for src in sources:
        log(f"\n=== 拓客源: {src} ===")
        for country in countries:
            log(f"  → {src} | {country}")
            if src == "gmaps":
                leads = mine_gmaps(cfg, country, args.limit)
            elif src == "customs":
                leads = mine_customs(cfg, country, args.limit)
            elif src == "yellowpages":
                leads = mine_yellowpages(cfg, country, args.limit)
            elif src == "ecom":
                leads = mine_ecom(cfg, country, args.limit)
            else:
                leads = []
            all_leads.extend(leads)
            log(f"    +{len(leads)} 条")

    log(f"\n汇总: {len(all_leads)} 条线索")
    if not all_leads:
        return

    # 去重 (按 company + country)
    seen = set()
    unique = []
    for l in all_leads:
        key = (l.get("company", "").lower().strip(), l.get("country", "").lower().strip())
        if key in seen or not l.get("company"):
            continue
        seen.add(key)
        unique.append(l)
    log(f"去重后: {len(unique)} 条 (原始 {len(all_leads)} 条)")

    # 保存到 data 目录
    out_file = DATA_DIR / f"strip_curtain_mining_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)
    log(f"已存: {out_file}")

    if args.dry_run:
        log("DRY-RUN 样例 (前 10 条):")
        for l in unique[:10]:
            log(f"  - [{l.get('source')}] {l.get('company')[:40]} | {l.get('country')} | {l.get('website','')}")
        log(f"\n📌 确认无误后用 --write 真写 CRM")
    else:
        log(f"\n写入 CRM base {cfg['crm'].get('base_token_env')} ...")
        written = write_to_crm(cfg, unique)
        log(f"✅ 写入完成: {written} 条")


if __name__ == "__main__":
    main()
