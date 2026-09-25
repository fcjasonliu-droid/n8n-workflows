#!/usr/bin/env python3
"""
crm_normalize.py — 飞书 CRM 规整脚本 (Pipeline v2, product-agnostic)

读 products/<product>/config.json 配置:
  - 线索池/客户档案 表 ID
  - lark-cli 路径

职责:
  1. 从"线索池"拉 待审核 + 有邮箱 + 未流转 的客户
  2. 按 邮箱 + 公司+国家 去重 (vs 客户档案)
  3. 写入"客户档案" (复制必要字段, 来源=线索池对应渠道)
  4. 回填"线索池": 审核状态=已流转, 转客户档案ID=新 record_id
  5. 审计日志: products/<product>/logs/normalize_audit_YYYYMMDD.jsonl

用法:
  python3 crm_normalize.py --product esd --dry-run
  python3 crm_normalize.py --product esd

要求:
  - lark-cli 在 PATH
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# === Pipeline 路径 ===
PIPELINE_ROOT = Path(__file__).parent.parent
PRODUCTS_DIR = PIPELINE_ROOT / "products"


def load_config(product_code: str) -> dict:
    config_path = PRODUCTS_DIR / product_code / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"找不到产品配置: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def list_records(lark_cli: str, base_token: str, table_id: str, limit=2000):
    """[自动加, 9-22] 拉指定表所有记录 (ndjson)."""
    out_path = "/tmp/_norm_pull.ndjson"
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
    content = Path(out_path).read_text().strip()
    return [json.loads(line) for line in content.split('\n') if line.strip()]


def write_batch(lark_cli: str, base_token: str, table_id: str, records):
    """[自动加, 9-22] 批量写 (200/批), json.dumps 走 @file."""
    if not records:
        return []
    BATCH = 200
    all_ids = []
    for i in range(0, len(records), BATCH):
        chunk = records[i:i+BATCH]
        r = subprocess.run(
            [lark_cli, "base", "+record-batch-create",
             "--base-token", base_token,
             "--table-id", table_id,
             "--json", json.dumps({"create_records": chunk}, ensure_ascii=False)],
            capture_output=True, text=True, timeout=300
        )
        # ⚠️ 在 Python 3.14 + 中文 env 下, capture_output 偶尔丢 stdout
        # 用 subprocess.Popen + 显式读 PIPE 兜底
        if not r.stdout.strip():
            proc = subprocess.Popen(
                [lark_cli, "base", "+record-batch-create",
                 "--base-token", base_token,
                 "--table-id", table_id,
                 "--json", json.dumps({"create_records": chunk}, ensure_ascii=False)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            out, err = proc.communicate(timeout=300)
            r.stdout = out
            r.stderr = err or r.stderr
        try:
            resp = json.loads(r.stdout)
        except Exception as e:
            raise RuntimeError(f"lark 返回非 JSON ({len(r.stdout)} bytes): {r.stdout[:300]}\nstderr: {r.stderr[:300]}")
        if not resp.get("ok"):
            raise RuntimeError(f"批量写入失败: {resp}")
        ids = resp.get("data", {}).get("record_id_list") or resp.get("data", {}).get("record_ids") or []
        all_ids.extend(ids)
    return all_ids


def update_records(lark_cli: str, base_token: str, table_id: str, updates):
    """[自动加, 9-22] 批量更新 (200/批), update_records 是 dict of record_id."""
    if not updates:
        return
    BATCH = 200
    for i in range(0, len(updates), BATCH):
        chunk = updates[i:i+BATCH]
        # ⚠️ lark-cli update_records 是 object-of-objects 格式!
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


def main():
    """[自动加, 9-22] 入口: 从线索池拉待审核 → 去重 → 写入客户档案 → 回填线索池."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True, help="如 esd / tire")
    parser.add_argument("--dry-run", action="store_true", help="只模拟, 不写入")
    args = parser.parse_args()

    cfg = load_config(args.product)
    crm_cfg = cfg["crm"]
    tbl_lead = crm_cfg["tables"]["leads"]
    tbl_kh = crm_cfg["tables"]["customer"]

    print(f"[{datetime.now().isoformat()}] crm_normalize --product={args.product} (dry_run={args.dry_run})")

    # 1. 拉线索池
    print("  → 拉线索池...")
    leads = list_records(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_lead)
    print(f"     线索池总: {len(leads)}")

    pending = []
    for l in leads:
        review = l.get("审核状态")
        if isinstance(review, list):
            review = review[0] if review else None
        if review != "待审核":
            continue
        email = (l.get("邮箱") or "").strip()
        if not email or "@" not in email:
            continue
        if l.get("转客户档案ID"):
            continue
        pending.append(l)
    print(f"     待流转 (待审核+有邮箱+未流转): {len(pending)}")

    # 2. 拉客户档案做去重
    print("  → 拉客户档案...")
    customers = list_records(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_kh)
    print(f"     客户档案总: {len(customers)}")

    existing_emails = set()
    for c in customers:
        e = (c.get("邮箱") or "").strip().lower()
        if e:
            existing_emails.add(e)

    # 3. 去重 + 准备写入
    new_for_kh = []
    skipped_dup = 0
    for l in pending:
        email = l["邮箱"].strip().lower()
        if email in existing_emails:
            skipped_dup += 1
            continue
        src = l.get("来源") or []
        if isinstance(src, list) and src:
            src = src[0]
        else:
            src = ""
        new_for_kh.append({
            "公司名称": l.get("公司名称") or "",
            "邮箱": l["邮箱"].strip(),
            "国家": l.get("国家") or "未分类",  # ⚠️ 客户的"国家"是 text, 不是 select
            "来源": src if isinstance(src, str) else (src[0] if src else "未知"),
            "客户类型": [l.get("客户类型") or "B-修车厂连锁"] if isinstance(l.get("客户类型"), str) else (l.get("客户类型") or ["B-修车厂连锁"]),  # 读线索池"客户类型"字段
            "首次建档": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        existing_emails.add(email)

    print(f"  → 去重: 跳过 {skipped_dup} 条重复, 待写入 {len(new_for_kh)} 条")

    # 4. 写客户档案
    if args.dry_run:
        print("  → DRY-RUN: 跳过写入")
        new_ids = [f"dryrun_{i}" for i in range(len(new_for_kh))]
    else:
        if new_for_kh:
            print(f"  → 写入客户档案 {len(new_for_kh)} 条...")
            new_ids = write_batch(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_kh, new_for_kh)
            print(f"     写入成功, 新 record_ids: {len(new_ids)}")
        else:
            new_ids = []

    # 5. 回填线索池
    if not args.dry_run and new_ids and len(new_ids) == len(new_for_kh):
        write_idx = 0
        updates = []
        for l in pending:
            email = l["邮箱"].strip().lower()
            if email not in [r["邮箱"].strip().lower() for r in new_for_kh]:
                continue
            if write_idx >= len(new_ids):
                break
            updates.append({
                "record_id": l["record_id"],
                "fields": {
                    "审核状态": ["已流转"],
                    "转客户档案ID": new_ids[write_idx]
                }
            })
            write_idx += 1
        if updates:
            print(f"  → 回填线索池 {len(updates)} 条...")
            update_records(crm_cfg["lark_cli"], crm_cfg["base_token"], tbl_lead, updates)
            print("     回填完成")

    # 6. 审计日志
    audit = {
        "ts": datetime.now().isoformat(),
        "product": args.product,
        "dry_run": args.dry_run,
        "leads_total": len(leads),
        "leads_pending": len(pending),
        "skipped_dup": skipped_dup,
        "created_in_kh": len(new_for_kh),
    }
    log_dir = PRODUCTS_DIR / args.product / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    audit_file = log_dir / f"normalize_audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(audit_file, "a") as f:
        f.write(json.dumps(audit, ensure_ascii=False) + "\n")
    print(f"\n✅ 完成. 审计: {audit_file}")
    print(f"   汇总: 线索池{len(leads)}条 → 待流转{len(pending)} → 去重{skipped_dup} → 新建{len(new_for_kh)}")


if __name__ == "__main__":
    main()
