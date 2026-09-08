#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/crm_sync.py — CRM 回写桥（写飞书 crm-esd 开发信记录表）

对接原脚本：/Users/jason/外贸/scripts/crm_sync.py
  - 默认 dry-run：不写库，只输出 ok:true / records_created:0；
  - --apply：调原 crm_sync.py 做映射自检（原脚本直接运行时为无实际写入的单元测试，
    真实写回逻辑在其 sync_task_result()/sync_reply_to_customer() 中，由原 worker/imap 流程调用）。

stdout 单行 JSON：{ok, records_created, dry_run}
"""

import argparse
import sys

from common import (
    DEFAULT_PRODUCT,
    ORIGIN_SCRIPTS_DIR,
    call_script,
    emit_json,
    fail,
    load_product,
)


CRM_SYNC_SCRIPT = ORIGIN_SCRIPTS_DIR / "crm_sync.py"      # 原 crm_sync.py 绝对路径


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="CRM 回写桥 — 调原 crm_sync.py（默认 dry-run 不写，--apply 写回）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--dry-run", action="store_true", help="干跑（默认；不写库）")
    parser.add_argument("--apply", action="store_true", help="调用原 crm_sync.py 写回")
    args = parser.parse_args(argv)

    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    if args.dry_run and args.apply:
        fail("--dry-run 与 --apply 不能同时使用", exit_code=2)
    dry = not args.apply   # 只读优先：未显式 --apply 一律不写

    if dry:
        # dry-run：不调用原脚本、不产生任何写入
        emit_json({"ok": True, "records_created": 0, "dry_run": True})

    # apply：调用原 crm_sync.py（其直接运行模式 = 无实际写入的映射自检，故 records_created=0）
    result = call_script(CRM_SYNC_SCRIPT, [], product)
    if not result["ok"]:
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[-400:] or "无输出"
        fail("crm_sync.py 执行失败(rc={}): {}".format(result["returncode"], detail))
    emit_json({"ok": True, "records_created": 0, "dry_run": False})


if __name__ == "__main__":
    main()
