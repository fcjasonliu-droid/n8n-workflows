#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/inquiry_receive.py — 询盘接收桥（IMAP 扫退信 + 客户回复）

对接原脚本：/Users/jason/外贸/scripts/imap_poll.py
  - 默认 dry-run：调原 imap_poll.py（只扫描 UNSEEN，不标已读、不改 queue）；
  - --apply：调原 imap_poll.py --mark（扫描并标记已读 + 回写 queue 中匹配任务）。

stdout 单行 JSON：{ok, scanned, bounced, replied, dry_run}
  - scanned：本次扫描到的已分类邮件数（退信 + 真人回复 + 自动回复，原脚本未输出
    UNSEEN 总数，故用已分类数近似）；
  - bounced：退信数；replied：真人回复 + 自动回复合计。
"""

import argparse
import re
import sys

from common import (
    DEFAULT_PRODUCT,
    ORIGIN_SCRIPTS_DIR,
    call_script,
    emit_json,
    fail,
    load_product,
)


IMAP_POLL_SCRIPT = ORIGIN_SCRIPTS_DIR / "imap_poll.py"    # 原 imap_poll.py 绝对路径


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
        description="询盘接收桥 — 调原 imap_poll.py（默认 dry-run 不标已读，--apply 才 --mark）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--dry-run", action="store_true", help="干跑（默认；只扫描不标已读）")
    parser.add_argument("--apply", action="store_true", help="真实扫描并标记已读/回写 queue")
    args = parser.parse_args(argv)

    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    if args.dry_run and args.apply:
        fail("--dry-run 与 --apply 不能同时使用", exit_code=2)
    dry = not args.apply   # 只读优先：未显式 --apply 一律不标已读

    # 薄封装：apply 才加 --mark（标记已读 + 回写任务），默认不加
    sub_args = ["--mark"] if not dry else []
    result = call_script(IMAP_POLL_SCRIPT, sub_args, product)
    if not result["ok"]:
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[-400:] or "无输出"
        fail("imap_poll.py 执行失败(rc={}): {}".format(result["returncode"], detail))

    out = result["stdout"] or ""
    bounced = _first_int(r"退信[:：]?\s*(\d+)\s*封", out)
    replied_human = _first_int(r"真人回复[:：]?\s*(\d+)\s*封", out)
    replied_auto = _first_int(r"自动回复[:：]?\s*(\d+)\s*封", out)
    errors = _first_int(r"错误[:：]?\s*(\d+)\s*个", out)

    if errors:
        # 原脚本对 IMAP 连接等错误只打印不退出；bridge 认为扫描未成功应报失败
        fail("imap_poll.py 扫描报错 {} 个（可能 IMAP 连接失败）".format(errors),
             scanned=bounced + replied_human + replied_auto,
             bounced=bounced,
             replied=replied_human + replied_auto)

    scanned = bounced + replied_human + replied_auto
    emit_json({"ok": True,
               "scanned": scanned,
               "bounced": bounced,
               "replied": replied_human + replied_auto,
               "dry_run": dry})


if __name__ == "__main__":
    main()
