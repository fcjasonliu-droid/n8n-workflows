#!/usr/bin/env python3
"""
mining/yellowpages.py — 黄页网站挖掘 (公开页面)

目标: 印度 JustDial / 巴西 Solutudo / 越南 YellowPages.vn / 全球 Yelp
输出: 公司名 + URL (二次访问拿联系方式)

用法:
  python3 yellowpages.py --product esd --country IN --keyword "ESD mat" --dry-run
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent.parent

YELLOWPAGES = {
    "IN": {"name": "JustDial", "url": "https://www.justdial.com/{city}/{kw}"},
    "BR": {"name": "Solutudo", "url": "https://www.solutudo.com.br/busca/{kw}/{city}"},
    "VN": {"name": "YellowPages.vn", "url": "https://www.yellowpages.vn/search?keyword={kw}&city={city}"},
    # 非洲本地黄页 (2026-09-18 实测 URL)
    "NG": {"name": "BusinessFinder NG", "url": "https://www.businessfinder.ng/search?q={kw}&city={city}"},
    "KE": {"name": "Yellow Pages Kenya", "url": "https://yellowpages.ke/en/search?q={kw}&city={city}"},
    "ZA": {"name": "SA Yellow Pages", "url": "https://www.yellowpages.co.za/search?what={kw}&where={city}"},
    "EG": {"name": "Yellow Pages Egypt", "url": "https://www.yellowpages.com.eg/en/search?what={kw}&where={city}"},
    "GH": {"name": "GhanaWeb BusinessDir", "url": "https://www.ghanaweb.com/businessdirectory/search?what={kw}"},
    "GLOBAL": {"name": "Yelp", "url": "https://www.yelp.com/search?find_desc={kw}&find_loc={city}"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--country", required=True)
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--city", default="")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg_path = PIPELINE_ROOT / "products" / args.product / "config.json"
    cfg = json.loads(cfg_path.read_text())

    yp = YELLOWPAGES.get(args.country.upper(), YELLOWPAGES["GLOBAL"])
    print(f"[黄页] {yp['name']} country={args.country} keyword={args.keyword}")

    url = yp["url"].format(
        kw=urllib.parse.quote(args.keyword),
        city=urllib.parse.quote(args.city) if args.city else ""
    )
    print(f"  → {url}")

    if args.dry_run:
        print(f"\n  DRY-RUN: 模拟返回 10 条占位")
        sample_leads = [
            {"platform": yp["name"], "title": f"[Sample {args.country}] {args.keyword} Co {i+1}",
             "url": f"https://example.com/listing/{i+1}",
             "keyword": args.keyword, "country": args.country,
             "source": f"{yp['name']} search (DRY-RUN)"}
            for i in range(10)
        ]
        for l in sample_leads:
            print(f"  - {l['title']}")
        return

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  ❌ 抓取失败: {e}")
        return

    # 简易解析 — 找 "company-name" 类标签
    leads = []
    name_re = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>([A-Z][^<]{5,80})</a>')
    seen = set()
    for m in name_re.finditer(html):
        url_m, title = m.group(1), m.group(2).strip()
        if url_m.startswith("#") or url_m.startswith("javascript"):
            continue
        if any(kw in title.lower() for kw in ("home", "about", "contact", "privacy", "terms")):
            continue
        if url_m in seen:
            continue
        seen.add(url_m)
        leads.append({
            "platform": yp["name"], "title": title, "url": url_m,
            "keyword": args.keyword, "country": args.country,
            "source": f"{yp['name']} search",
        })
        if len(leads) >= args.limit:
            break

    print(f"  → 抓到 {len(leads)} 条")

    if not leads:
        return

    out_path = Path(args.out) if args.out else PIPELINE_ROOT / "products" / args.product / "data" / f"yp_{args.country}_{args.keyword.replace(' ','_')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(leads, ensure_ascii=False, indent=2))
    print(f"  ✅ 写入 {out_path}")


if __name__ == "__main__":
    main()
