#!/usr/bin/env python3
"""
mining/gmaps.py — Google Maps Places API 挖掘

按关键词 + 城市 → 找目标客户 (经销商/工厂/店铺)

需要环境变量:
  GOOGLE_MAPS_API_KEY — GCP Places API key (New)

用法:
  python3 gmaps.py --product esd --country US --keyword "ESD mat distributor" --dry-run
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent.parent

GMAPS_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")


def search_places(query: str, max_results: int = 20) -> list:
    """调 Google Places API (New) Text Search."""
    if not API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY env 没设")
    body = json.dumps({
        "textQuery": query,
        "maxResultCount": max_results,
        "languageCode": "en",
    }).encode("utf-8")
    req = urllib.request.Request(
        GMAPS_PLACES_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.types",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("places", [])
    except urllib.error.HTTPError as e:
        print(f"  ⚠️ HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ⚠️ {type(e).__name__}: {e}", file=sys.stderr)
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--country", required=True, help="ISO 2 或国家名")
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg_path = PIPELINE_ROOT / "products" / args.product / "config.json"
    cfg = json.loads(cfg_path.read_text())
    countries_mining = cfg.get("countries_mining", [])

    print(f"[GMaps] product={args.product} keyword={args.keyword} country={args.country}")

    if not API_KEY:
        print(f"\n  ⚠️ GOOGLE_MAPS_API_KEY env 没设, 跳过")
        print(f"  9-14 memory: GCP 计费账号 + $300 赠金 90 天")
        print(f"  设 key 后重跑: export GOOGLE_MAPS_API_KEY=xxx")

        if args.dry_run:
            print(f"\n  DRY-RUN: 模拟返回 0 条 (等 API key)")
            return
        sys.exit(0)

    if args.dry_run:
        print(f"\n  DRY-RUN: 不会真调 API, 模拟返回 10 条占位")
        return

    query = f"{args.keyword} in {args.country}"
    places = search_places(query, args.limit)

    leads = []
    for p in places:
        leads.append({
            "name": p.get("displayName", {}).get("text", ""),
            "address": p.get("formattedAddress", ""),
            "phone": p.get("nationalPhoneNumber", ""),
            "website": p.get("websiteUri", ""),
            "types": p.get("types", []),
            "place_id": p.get("id", ""),
            "country": args.country,
            "keyword": args.keyword,
            "source": "Google Maps Places API",
        })

    print(f"  → 找到 {len(leads)} 条")

    out_path = Path(args.out) if args.out else PIPELINE_ROOT / "products" / args.product / "data" / f"gmaps_{args.country}_{args.keyword.replace(' ','_')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(leads, ensure_ascii=False, indent=2))
    print(f"  ✅ 写入 {out_path}")


if __name__ == "__main__":
    main()
