#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/customer_dev.py — 客户开发桥（拉飞书 CRM 视图 → 生成发送任务）

对接原脚本：/Users/jason/外贸/scripts/producer.py
  - 默认 dry-run：调原 producer.py --dry-run（拉取 CRM 视图并预览，不写任务文件）；
  - --apply：调原 producer.py 全量跑（真实生成 queue/pending 下的 task_*.json）。

stdout 单行 JSON：{ok, fetched, skipped, pending_dir, dry_run}
  - fetched：本次"预览/生成"的待发客户数（dry-run 时即预览条数；apply 时为实际写入数）；
  - skipped：无邮箱客户 + 已在队列中重复客户 的合计；
  - pending_dir：原系统 queue/pending 目录。
"""

import argparse
import re
import sys

from common import (
    DEFAULT_PRODUCT,
    ORIGIN_DIR,
    ORIGIN_SCRIPTS_DIR,
    call_script,
    emit_json,
    fail,
    load_product,
)


PRODUCER_SCRIPT = ORIGIN_SCRIPTS_DIR / "producer.py"      # 原 producer.py 绝对路径
PENDING_DIR = ORIGIN_DIR / "queue" / "pending"            # 原队列待发目录


def _first_int(pattern, text, default=0):
    """从原脚本输出中正则抓第一个整数，抓不到返回 default。"""
    m = re.search(pattern, text)
    if m:
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            return default
    return default


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="客户开发桥 — 调原 producer.py（拉 CRM 视图 → 生成任务）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--dry-run", action="store_true", help="预览模式（默认，不写任务文件）")
    parser.add_argument("--apply", action="store_true", help="全量执行（真实写入 queue/pending）")
    args = parser.parse_args(argv)

    # 校验产品档案存在；若 load_product 返回 ok:false 则按规范失败退出
    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    if args.dry_run and args.apply:
        fail("--dry-run 与 --apply 不能同时使用", exit_code=2)
    dry = not args.apply   # 只读优先：未显式 --apply 一律 dry-run

    # 薄封装：参数原样转给原 producer.py（dry-run 加 --dry-run；apply 全量不加）
    sub_args = ["--dry-run"] if dry else []
    result = call_script(PRODUCER_SCRIPT, sub_args, product)
    if not result["ok"]:
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[-400:] or "无输出"
        fail("producer.py 执行失败(rc={}): {}".format(result["returncode"], detail))

    out = result["stdout"] or ""
    valid_fetched = _first_int(r"拉取\s*(\d+)\s*个有效客户", out)          # CRM 视图拉到的有效客户
    to_send = _first_int(r"待生成任务[:：]?\s*(\d+)", out) or valid_fetched  # 待生成/预览条数
    written = _first_int(r"已写入\s*(\d+)\s*条任务", out)                   # apply 时真实写入数
    no_email_skipped = _first_int(r"跳过\s*(\d+)\s*个无邮箱", out)           # 无邮箱被跳过
    in_queue_skipped = _first_int(r"已在队列中[:：]?\s*(\d+)\s*个客户", out)  # 队列中重复被跳过

    fetched = written if (written and not dry) else to_send
    skipped = no_email_skipped + in_queue_skipped
    emit_json({"ok": True,
               "fetched": fetched,
               "skipped": skipped,
               "pending_dir": str(PENDING_DIR),
               "dry_run": dry})


if __name__ == "__main__":
    main()
