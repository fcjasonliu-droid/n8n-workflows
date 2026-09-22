#!/usr/bin/env python3
"""
mining/ecommerce.py — 电商反推 (Alibaba / Amazon / eBay)

按关键词搜索公开商品列表, 找卖同类产品的卖家 → 入 CRM 线索池

用法:
  python3 ecommerce.py --product esd --platform alibaba --keyword "ESD rubber mat" --dry-run
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent.parent

PLATFORMS = {
    "alibaba": {
        "url": "https://www.alibaba.com/trade/search?SearchText={kw}",
        "name_re": re.compile(r'<a[^>]+href="//([^"]+\.alibaba\.com[^"]+)"[^>]*title="([^"]+)"'),
    },
    "amazon": {
        "url": "https://www.amazon.com/s?k={kw}&i=industrial",
        "name_re": re.compile(r'<span[^>]+class="a-size-medium[^"]*"[^>]*>([^<]+)</span>'),
    },
    "ebay": {
        "url": "https://www.ebay.com/sch/i.html?_nkw={kw}&_sacat=0",
        "name_re": re.compile(r'<h3[^>]+class="s-item__title"[^>]*>\s*<span[^>]*>([^<]+)</span>'),
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--platform", required=True, choices=list(PLATFORMS.keys()) + ["all"])
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg_path = PIPELINE_ROOT / "products" / args.product / "config.json"
    cfg = json.loads(cfg_path.read_text())
    print(f"[电商反推] product={args.product} platform={args.platform} keyword={args.keyword}")

    platforms = list(PLATFORMS.keys()) if args.platform == "all" else [args.platform]
    leads = []

    for plat in platforms:
        cfg_p = PLATFORMS[plat]
        url = cfg_p["url"].format(kw=urllib.parse.quote(args.keyword))
        print(f"  → {plat}: {url}")
        if args.dry_run:
            for i in range(10):
                leads.append({
                    "platform": plat, "title": f"[Sample] {args.keyword} #{i+1} - {plat}",
                    "url": f"https://{plat}.com/listing/{i+1}",
                    "keyword": args.keyword,
                    "source": f"{plat} search (DRY-RUN)",
                })
            continue

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"    ❌ {plat} 抓取失败: {e}")
            continue

        seen = set()
        for m in cfg_p["name_re"].finditer(html):
            title = (m.group(-1) if m.lastindex else m.group(0)).strip()[:200]
            url_m = m.group(1) if m.lastindex and m.lastindex >= 1 else url
            if title in seen:
                continue
            seen.add(title)
            leads.append({
                "platform": plat, "title": title, "url": url_m,
                "keyword": args.keyword, "source": f"{plat} search",
            })
            if sum(1 for x in leads if x["platform"] == plat) >= args.limit:
                break

        time.sleep(1)

    print(f"\n  → 抓到 {len(leads)} 条")

    if args.dry_run:
        print("\n=== DRY-RUN: 前 5 条 ===")
        for l in leads[:5]:
            print(f"  [{l['platform']}] {l['title']}")
        return

    if not leads:
        return
    out_path = Path(args.out) if args.out else PIPELINE_ROOT / "products" / args.product / "data" / f"ecom_{args.keyword.replace(' ','_')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(leads, ensure_ascii=False, indent=2))
    print(f"  ✅ 写入 {out_path}")


if __name__ == "__main__":
    main()
