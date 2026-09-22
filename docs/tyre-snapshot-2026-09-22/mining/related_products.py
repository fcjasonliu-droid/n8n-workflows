#!/usr/bin/env python3
"""
mining/related_products.py — ESD 关联产品拓客脚本

用途: 围绕 ESD mat 的 3 个关联产品 (粘尘垫/防静电帘/防静电抗疲劳地垫) + 流利条,
      从 Google Maps / 海关 / 电商 / 黄页 4 个源挖掘潜在买家, 写入 ESD 客户档案表.

设计原则:
  - 关联产品买家进 ESD 客户池, 打"产品标签" (multi-select)
  - 流利条只打标签, 不作为关联拓客关键词 (单独标签但走同 CRM)
  - 复用现有 customs.py / gmaps.py / yellowpages.py 的实现, 调用而非重写

用法:
  # 单源 + 单国家 dry-run (20 条)
  python3 related_products.py --source gmaps --country US --category tacky_mat --limit 20 --dry-run

  # 跑全部源 + 全部国家 (真实入库)
  python3 related_products.py --source all --country all --dry-run
  python3 related_products.py --source all --country all    # 真跑
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
DATA_DIR = PIPELINE_ROOT / "products" / "esd" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# === 关联产品配置 (来自 products/esd/config.json) ===
RELATED_CATEGORIES = [
    {
        "code": "tacky_mat",
        "label": "粘尘垫",
        "source_label": "关联-粘尘垫",
        "customer_type": "E-终端用户/工厂",
        "keywords_en": ["tacky mat", "sticky mat", "cleanroom mat", "dust control mat"],
        "hs_code": "3918.10.90",
    },
    {
        "code": "esd_curtain",
        "label": "防静电帘",
        "source_label": "关联-防静电帘",
        "customer_type": "E-终端用户/工厂",
        "keywords_en": ["ESD curtain", "anti-static curtain", "static dissipative curtain", "PVC ESD curtain"],
        "hs_code": "3920.10/3921.13",
    },
    {
        "code": "anti_fatigue_mat",
        "label": "防静电抗疲劳地垫",
        "source_label": "关联-防静电抗疲劳地垫",
        "customer_type": "B-工业品分销商",
        "keywords_en": ["anti-fatigue mat", "ESD anti-fatigue mat", "conductive anti-fatigue"],
        "hs_code": "4016.91.00",
    },
]

STRIP_CURTAIN = {
    "code": "strip_curtain",
    "label": "流利条",
    "source_label": "关联-流利条",  # 注: config 写"流利条" 作为 source_label, 不带"关联-"前缀
    "customer_type": "B-工业品分销商",
    "keywords_en": ["PVC strip curtain", "strip door", "vinyl strip curtain", "freezer curtain"],
    "hs_code": "3920.10/3921.90",
}

# 注意: 流利条不在关联拓客里, 但仍可通过 source="流利条" 入库 (来自独立挖掘)
# 这里只跑 RELATED_CATEGORIES (3 个关联产品)

# 拓客国清单 (跟 ESD config 一致)
COUNTRIES = ["US", "DE", "GB", "FR", "IT", "NL", "PL", "MY", "TH", "VN", "IN", "BR", "AE", "ZA"]


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_subprocess(cmd: list, timeout: int = 300) -> dict:
    """调子脚本 (customs/gmaps/yellowpages/ecom), 解析 stdout JSON."""
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


def mine_gmaps(category: dict, country: str, limit: int) -> list:
    """Google Maps: 关键词搜索 + 国家."""
    if not os.environ.get("GOOGLE_MAPS_API_KEY"):
        log(f"  ⚠️ {country}/{category['code']}: GOOGLE_MAPS_API_KEY 未设, 跳过 GMaps")
        return []
    results = []
    for kw in category["keywords_en"][:2]:  # 每个 category 最多 2 个关键词
        cmd = ["python3", str(SCRIPTS_DIR / "gmaps.py"),
               "--product", "esd",
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
                    "category_code": category["code"],
                    "category_label": category["label"],
                    "source_label": category["source_label"],
                    "customer_type": category["customer_type"],
                    "source": "谷歌地图搜索",
                    "raw_keyword": kw,
                })
        time.sleep(1)
    return results


def mine_customs(category: dict, country: str, limit: int) -> list:
    """UN Comtrade: HS 编码 + 进口国 → 找 top 进口商. (comtrade 不返公司, 仅给 partner 国)
    这里只拿统计, 真实公司要去 google maps 二次挖掘, 此处仅做市场验证."""
    hs = category["hs_code"].split("/")[0].replace(".", "")
    cmd = ["python3", str(SCRIPTS_DIR / "customs.py"),
           "--product", "esd",
           "--country", country,
           "--hs-code", hs,
           "--year", "2024",
           "--limit", str(limit)]
    r = run_subprocess(cmd)
    if not r.get("ok"):
        return []
    # customs 返回 partner 来源国, 不是公司名 → 不能直接入库
    # 仅做市场规模验证, 实际公司靠 GMaps 二次挖
    log(f"  ℹ️ {country}/{category['code']}: HS{hs} 海关数据 = {len(r.get('data', []))} partner (仅参考市场规模)")
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="gmaps", choices=["gmaps", "customs", "yellowpages", "ecom", "all"])
    ap.add_argument("--country", default="US", help="ISO 2 国家码, 或 'all'")
    ap.add_argument("--category", default="all",
                    choices=["all", "tacky_mat", "esd_curtain", "anti_fatigue_mat"])
    ap.add_argument("--limit", type=int, default=10, help="每个国家每个 keyword 上限")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = DATA_DIR / f"related_mining_{run_id}.json"

    log(f"开始关联产品拓客: source={args.source} country={args.country} category={args.category} dry_run={args.dry_run}")

    # 选择 categories
    cats = RELATED_CATEGORIES if args.category == "all" else [c for c in RELATED_CATEGORIES if c["code"] == args.category]
    # 选择 countries
    countries = COUNTRIES if args.country == "all" else [args.country]
    # 选择 sources
    sources = ["gmaps", "customs", "yellowpages", "ecom"] if args.source == "all" else [args.source]

    all_leads = []
    for cat in cats:
        log(f"\n=== {cat['label']} ({cat['code']}) ===")
        for country in countries:
            for src in sources:
                log(f"  → {src} | {country} | {cat['code']}")
                if src == "gmaps":
                    leads = mine_gmaps(cat, country, args.limit)
                elif src == "customs":
                    leads = mine_customs(cat, country, args.limit)
                else:
                    log(f"    ⚠️ {src} 暂未实现, 跳过")
                    leads = []
                all_leads.extend(leads)
                log(f"    +{len(leads)} 条")

    # 输出
    log(f"\n汇总: {len(all_leads)} 条关联产品线索")
    if all_leads:
        cat_count = {}
        for l in all_leads:
            cat_count[l["category_label"]] = cat_count.get(l["category_label"], 0) + 1
        log(f"按关联产品: {cat_count}")

    if args.dry_run:
        log(f"DRY-RUN: 数据未写入, 仅预览")
        log(f"样例 (前 5 条):")
        for l in all_leads[:5]:
            log(f"  - [{l['category_label']}] {l['company']} ({l['country']}) {l.get('website','')}")
    else:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(all_leads, f, ensure_ascii=False, indent=2)
        log(f"✅ 已写入: {out_file}")
        log(f"下一步: 用入库脚本写入 CRM 客户档案表 (crm_import_related.py 后续开发)")


if __name__ == "__main__":
    main()
