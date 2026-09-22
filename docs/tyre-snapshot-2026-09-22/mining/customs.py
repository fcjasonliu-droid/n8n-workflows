#!/usr/bin/env python3
"""
mining/customs.py — UN Comtrade 海关数据挖掘 (免费 preview + 免费 full API)

按 HS 编码 + 进口国 → 拉 partner 来源国 (竞品) + value (市场大小)
数据用来选市场, 不是找客户 (UN Comtrade 无公司邮箱).

3 个运行模式:
  1. **mock** (默认无 key): 返回占位, 跟旧版兼容
  2. **preview** (无 key): 走 comtradeapi.un.org/public/v1/preview (无需账号, 限速严)
  3. **full** (有 key): 走 comtradeapi.un.org/data/v1/get, 注册免费拿 key

用法:
  # mock (无需任何凭据)
  python3 customs.py --product tire --country NG --year 2024 --dry-run

  # preview (无需 key, 限速)
  python3 customs.py --product tire --country NG --year 2024 --mode preview --dry-run

  # full (需要 COMTRADE_API_KEY env)
  python3 customs.py --product tire --country NG --year 2024 --mode full --out leads.json

注册 Free API key 步骤 (5 分钟):
  1. https://comtradedeveloper.un.org/ 用邮箱注册
  2. Products → Free APIs → Subscribe
  3. 拿到的 key 写入 ~/.zshrc: export COMTRADE_API_KEY=...
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PIPELINE_ROOT / "scripts"))

PREVIEW_API = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
FULL_API = "https://comtradeapi.un.org/data/v1/get/C/A/HS"


def fetch_mock(reporter, hs_code, year):
    """占位 (无 key 时兜底)"""
    return [{
        "_placeholder": True,
        "partnerDesc": f"[{reporter}] Sample partner",
        "primaryValue": 1000000.0 * (hash(reporter + str(year)) % 100),
        "_note": "需要 COMTRADE_API_KEY (https://comtradedeveloper.un.org/)",
    }]


def fetch_preview(reporter_iso3, hs_code, year, flow="M"):
    """UN Comtrade Free Preview API (无需 key, 限速严)"""
    params = (
        f"reporterCode={reporter_iso3}&"
        f"period={year}&"
        f"cmdCode={hs_code}&"
        f"flowCode={flow}&"
        f"maxRecords=500"
    )
    url = f"{PREVIEW_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "PipelineV2/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            records = data.get("data", [])
            return records
    except urllib.error.HTTPError as e:
        print(f"  ⚠️ Preview HTTP {e.code}: {reporter_iso3} {year}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ⚠️ Preview {type(e).__name__}: {e}", file=sys.stderr)
        return []


def fetch_full(reporter_iso3, hs_code, year, flow, api_key, clean=True):
    """UN Comtrade Full API (需免费 key).

    ⚠️ 关键: 原始响应是 partnerCode × partner2Code × motCode × customsCode 的
    多维立方体, 边际/明细单元格重叠 → **naive 求和会重复计数** (2026-09-20 实测
    DE 401691 无过滤 $174M vs 过滤后 $4.8M, 差 36 倍).
    必须加 `motCode=0 & partner2Code=0 & customsCode=C00` → 每 partner 恰好 1 行.
    """
    clean_frag = "&motCode=0&partner2Code=0&customsCode=C00" if clean else ""
    params = (
        f"reporterCode={reporter_iso3}&"
        f"period={year}&"
        f"cmdCode={hs_code}&"
        f"flowCode={flow}"
        f"{clean_frag}&"
        f"maxRecords=5000"
    )
    url = f"{FULL_API}?{params}&subscription-key={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": "PipelineV2/1.0"})
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("data", []), None
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503):
                time.sleep(6 * (attempt + 1))
                continue
            return [], last_err
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(4 * (attempt + 1))
    return [], last_err


def load_partner_areas() -> dict:
    """加载官方 M49 → ISO3/name 映射 (partnerAreas.json).
    返回 {m49_int: {'iso3':..., 'name':...}}, 无文件时回落硬编码表."""
    ref = Path(__file__).parent / "partnerAreas.json"
    out = {0: {"iso3": "World", "name": "World"}}
    if ref.exists():
        try:
            areas = json.loads(ref.read_text())["results"]
            for a in areas:
                c = a.get("PartnerCode")
                if c is None:
                    continue
                out[int(c)] = {
                    "iso3": a.get("PartnerCodeIsoAlpha3") or a.get("PartnerCodeIsoAlpha2") or f"M49={c}",
                    "name": a.get("PartnerDesc") or str(c),
                }
        except Exception:
            pass
    return out


# ISO 2 → ISO 3 numeric (UN Comtrade 用 M49 numeric code)
# 非洲 tyre 主力 41 国 + 通用
ISO2_TO_M49 = {
    # 非洲
    "DZ": 12, "AO": 24, "BJ": 204, "BW": 72, "BF": 854, "BI": 108, "CM": 120,
    "CV": 132, "TD": 148, "CG": 178, "CD": 180, "CI": 384, "EG": 818, "ET": 231,
    "GA": 266, "GM": 270, "GH": 288, "GN": 324, "GQ": 226, "KE": 404, "LS": 426,
    "LR": 430, "LY": 434, "MG": 450, "MW": 454, "ML": 466, "MR": 478, "MU": 480,
    "MA": 504, "MZ": 508, "NA": 516, "NE": 562, "NG": 566, "RW": 646, "ST": 678,
    "SN": 686, "SC": 690, "SL": 694, "SO": 706, "ZA": 710, "SS": 728, "SD": 729,
    "SZ": 748, "TZ": 834, "TG": 768, "TN": 788, "UG": 800, "ZM": 894, "ZW": 716,
    # 其他通用
    "US": 840, "DE": 276, "GB": 826, "FR": 251, "IT": 381, "NL": 528, "PL": 616,
    "MY": 458, "TH": 764, "VN": 704, "IN": 356, "BR": 76, "AE": 784, "JP": 392,
    "KR": 410, "AU": 36, "CA": 124, "MX": 484, "ES": 724, "PT": 620, "SE": 752,
    "CH": 756, "BE": 56, "AT": 40, "NO": 578, "DK": 208,
}


def m49_from_iso2(iso2: str) -> int:
    return ISO2_TO_M49.get(iso2.upper(), 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--country", action="append", required=True)
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--mode", choices=["auto", "mock", "preview", "full"], default="auto",
                    help="auto=full if COMTRADE_API_KEY set else preview")
    ap.add_argument("--include-parent", action="store_true",
                    help="追 4 位父码 (默认不追, 避免广口径污染)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg_path = PIPELINE_ROOT / "products" / args.product / "config.json"
    cfg = json.loads(cfg_path.read_text())
    hs_code = cfg.get("hs_code", "4016.91")
    # 支持多 HS code: "4012.20" 单码 或 "4016.91/4016.99" 双码
    # 6 位子码 ("401691"), 也尝试父码 ("4016") 看哪个返数据多
    raw_codes = hs_code.replace(".", "").replace("/", ",").replace(" ", "").split(",")
    hs_codes = []
    for c in raw_codes:
        if c and c not in hs_codes:
            hs_codes.append(c)
    parent = hs_codes[0][:4] if hs_codes else None
    # 父码兜底仅在单国数据为 0 时启用 (避免 ESD 4016 拉回 $41B 全橡胶制品)
    # 默认只跑子码, 除非 --include-parent
    if args.include_parent and parent and parent not in hs_codes:
        hs_codes.append(parent)

    api_key = os.environ.get("COMTRADE_API_KEY", "").strip()
    if args.mode == "auto":
        mode = "full" if api_key else "preview"
    else:
        mode = args.mode
    if mode == "mock" or (mode == "full" and not api_key):
        mode = "preview"  # mock 不再默认, 自动降级到 preview

    leads = []
    stats = []
    errors = []
    areas = load_partner_areas()

    def iso3_of(code):
        e = areas.get(int(code)) if code is not None else None
        if e:
            return e["iso3"], e["name"]
        # 回落硬编码反向表
        for k, v in ISO2_TO_M49.items():
            if v == code:
                return k, k
        return f"M49={code}", ""

    print(f"[海关挖掘] product={args.product} hs={','.join(hs_codes)} year={args.year} mode={mode} countries={args.country}")
    if mode == "full":
        print("  clean-filter: motCode=0 & partner2Code=0 & customsCode=C00 (每 partner 1 行)")

    for c2 in args.country:
        m49 = m49_from_iso2(c2)
        if not m49:
            print(f"  ⚠️ {c2}: 不在 ISO2 → M49 表, 跳过")
            errors.append({"country": c2, "reason": "no_m49_mapping"})
            continue
        # 多 HS code: 按 partner 聚合, 保留 _hs 拆解
        agg = {}      # partnerCode -> {amount_usd, net_wgt_kg, _hs:{}}
        hs_n = {}
        world = 0.0
        for hc in hs_codes:
            if mode == "preview":
                records, err = fetch_preview(m49, hc, args.year, flow="M"), None
            elif mode == "full":
                records, err = fetch_full(m49, hc, args.year, flow="M", api_key=api_key)
            else:
                records, err = fetch_mock(c2, hc, args.year), None
            hs_n[hc] = len(records)
            if err:
                errors.append({"country": c2, "hs": hc, "reason": err})
            for r in records:
                if r.get("_placeholder"):
                    continue
                pc = r.get("partnerCode")
                if pc is None:
                    continue
                pc = int(pc)
                val = float(r.get("primaryValue") or 0.0)
                wgt = float(r.get("netWgt") or 0.0)
                if pc == 0:                      # World 合计
                    world += val
                    continue
                slot = agg.setdefault(pc, {"amount_usd": 0.0, "net_wgt_kg": 0.0, "_hs": {}})
                slot["amount_usd"] += val
                slot["net_wgt_kg"] += wgt
                slot["_hs"][hc] = round(slot["_hs"].get(hc, 0.0) + val, 2)
            time.sleep(2 if mode == "full" else 1)

        rows = []
        for pc, sums in sorted(agg.items(), key=lambda x: -x[1]["amount_usd"])[:args.limit]:
            iso3, name = iso3_of(pc)
            rows.append({
                "country": c2,
                "country_m49": m49,
                "partner_code": pc,
                "partner_iso3": iso3,
                "partner_name": name,
                "amount_usd": round(sums["amount_usd"], 2),
                "net_wgt_kg": round(sums["net_wgt_kg"], 2),
                "_hs": sums["_hs"],
                "year": args.year,
                "hs_code": ",".join(hs_codes),
                "source": f"UN Comtrade ({mode})",
            })
        leads.extend(rows)
        stats.append({
            "iso2": c2, "m49": m49, "hs_records": hs_n, "n_partners": len(agg),
            "world_import_usd": round(world, 2),
            "top_partner": rows[0]["partner_iso3"] if rows else None,
            "top_partner_usd": rows[0]["amount_usd"] if rows else 0,
        })
        wsv = f"${world:,.0f}" if world else "n/a"
        top = f"{rows[0]['partner_iso3']} ${rows[0]['amount_usd']:,.0f}" if rows else "-"
        print(f"  [{c2}] hs={hs_n} partners={len(agg):3d} world={wsv} top={top}")

    print(f"\n  → 聚合 {len(leads)} 条 (每国 top {args.limit} 来源)")

    if args.dry_run:
        print("\n=== DRY-RUN: 前 8 条 ===")
        for l in leads[:8]:
            print(f"  {l['country']} <- {l['partner_iso3']:6s} ({l['partner_name'][:18]:18s}) ${l['amount_usd']:>14,.0f}")
        # 国别汇总 (用真 world 值, 非 top-N 求和)
        print("\n=== 国别市场 (world 合计 = 全部 partner) ===")
        for s in sorted(stats, key=lambda x: -x["world_import_usd"]):
            w = f"${s['world_import_usd']:,.0f}" if s["world_import_usd"] else "n/a"
            print(f"  {s['iso2']}: world={w:>18s} partners={s['n_partners']:3d} top={s['top_partner'] or '-'}")
        if errors:
            print(f"\n  ⚠️ errors: {errors[:5]}")
        return

    out_path = Path(args.out) if args.out else PIPELINE_ROOT / "products" / args.product / "data" / f"customs_{args.year}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "product": args.product,
        "hs_code": ",".join(hs_codes),
        "year": args.year,
        "mode": mode,
        "aggregation_filter": "motCode=0 & partner2Code=0 & customsCode=C00" if mode == "full" else None,
        "leads": leads,
        "stats": stats,
        "errors": errors,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"  ✅ 写入 {out_path} ({len(leads)} 条, {len(stats)} 国)")


if __name__ == "__main__":
    main()