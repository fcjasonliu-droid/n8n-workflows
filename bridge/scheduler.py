#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/scheduler.py — 时区调度桥（按客户时区把 pending 移到 ready）

对接原脚本：/Users/jason/外贸/scripts/scheduler.py
  - 默认：调原 scheduler.py --tick --dry-run（只统计"会移动"的条数，不真移文件）；
  - --preview：调原 scheduler.py --preview（打印每封任务的就绪/待发预览）；
  - --apply：调原 scheduler.py --tick（真实把到期任务移到 queue/ready）。

stdout 单行 JSON：{ok, moved, ready_dir, mode}
  - moved：本次实际/预计移动到 ready/ 的任务数（preview 模式下为"✅ 就绪"条数）；
  - ready_dir：原系统 queue/ready 目录。
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


SCHEDULER_SCRIPT = ORIGIN_SCRIPTS_DIR / "scheduler.py"    # 原 scheduler.py 绝对路径
READY_DIR = ORIGIN_DIR / "queue" / "ready"                # 原系统就绪目录


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
        description="时区调度桥 — 调原 scheduler.py --tick（--preview 预览，--apply 真实移动）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--preview", action="store_true", help="预览 pending 中各任务调度时间（只读）")
    parser.add_argument("--apply", action="store_true", help="真实执行 tick（移动到期任务到 ready/）")
    parser.add_argument("--dry-run", action="store_true", help="tick 干跑（默认；不移动文件）")
    args = parser.parse_args(argv)

    # 校验产品档案存在；load_product 返回 ok:false 时按规范失败退出
    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    if args.preview and args.apply:
        fail("--preview 与 --apply 不能同时使用", exit_code=2)

    # 只读优先：默认 tick --dry-run；--apply 才真移；--preview 纯预览
    if args.preview:
        mode = "preview"
        sub_args = ["--preview"]
    elif args.apply:
        mode = "tick"
        sub_args = ["--tick"]
    else:
        mode = "tick-dry-run"
        sub_args = ["--tick", "--dry-run"]

    result = call_script(SCHEDULER_SCRIPT, sub_args, product)
    if not result["ok"]:
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[-400:] or "无输出"
        fail("scheduler.py 执行失败(rc={}): {}".format(result["returncode"], detail))

    out = result["stdout"] or ""
    if mode == "preview":
        # 预览模式：原脚本逐行打印 "✅ 就绪"（此刻到期）或 "⏳ 待发"
        moved = len(re.findall(r"✅\s*就绪", out))
    else:
        # tick 模式：原脚本打印 "[结果] 标注 N 条, 移到 ready/ M 条"
        moved = _first_int(r"移到\s*ready[/／]\s*(\d+)\s*条", out)

    emit_json({"ok": True,
               "moved": moved,
               "ready_dir": str(READY_DIR),
               "mode": mode})


if __name__ == "__main__":
    main()
