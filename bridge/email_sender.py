#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/email_sender.py — SMTP 发送桥（一次最多发一封）

对接原脚本：/Users/jason/外贸/scripts/worker.py
  - 默认 dry-run：调原 worker.py --once --max 1 --dry-run（不真实发信）；
  - --apply：调原 worker.py --once --max 1（真实 SMTP 发送）。

stdout 单行 JSON：{ok, sent, failed, reason, dry_run}
  发送结果从原脚本 "结果: {...}" 那行 JSON 推断：
    dry_run → 预览成功 sent=1；sent → 真实发出 sent=1；
    failed_final/failed_retry → failed=1；empty/skipped/no_quota → 均 0 并给出 reason。
"""

import argparse
import json
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


WORKER_SCRIPT = ORIGIN_SCRIPTS_DIR / "worker.py"          # 原 worker.py 绝对路径


def _last_result_json(out):
    """从原 worker 输出中提取最后一个 "结果: {json}"，解析失败返回 None。"""
    matches = re.findall(r"结果[:：]\s*(\{.*\})", out, re.DOTALL)
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except (TypeError, ValueError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="SMTP 发送桥 — 调原 worker.py --once --max 1（默认 dry-run，--apply 真发）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--dry-run", action="store_true", help="干跑（默认；不真实发信）")
    parser.add_argument("--apply", action="store_true", help="真实发送一封邮件")
    args = parser.parse_args(argv)

    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    if args.dry_run and args.apply:
        fail("--dry-run 与 --apply 不能同时使用", exit_code=2)
    dry = not args.apply   # 只读优先：未显式 --apply 一律 dry-run

    # 薄封装：固定 --once --max 1，dry-run 时追加 --dry-run
    sub_args = ["--once", "--max", "1"]
    if dry:
        sub_args.append("--dry-run")

    result = call_script(WORKER_SCRIPT, sub_args, product)
    if not result["ok"]:
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[-400:] or "无输出"
        fail("worker.py 执行失败(rc={}): {}".format(result["returncode"], detail))

    out = result["stdout"] or ""
    res = _last_result_json(out)
    sent, failed, reason = 0, 0, ""
    if res is None:
        reason = "worker 无结果行输出"
    else:
        status = res.get("status", "")
        if status == "dry_run":
            sent, failed = 1, 0
            reason = "dry-run 预览，未真实发送"
        elif status == "sent":
            sent, failed = 1, 0
            reason = res.get("reason", "") or ""
        elif status in ("failed_final", "failed_retry"):
            sent, failed = 0, 1
            reason = str(res.get("error") or res.get("reason") or status)[:400]
        elif status == "skipped":
            sent, failed = 0, 0
            reason = res.get("reason", "速率限流/配额等原因跳过")
        elif status == "no_quota":
            sent, failed = 0, 0
            reason = res.get("reason", "日配额不足")
        elif status == "empty":
            sent, failed = 0, 0
            reason = "无 pending/ready 任务"
        else:
            sent, failed = 0, 0
            reason = "未识别状态: {}".format(status)

    emit_json({"ok": True,
               "sent": sent,
               "failed": failed,
               "reason": reason,
               "dry_run": dry})


if __name__ == "__main__":
    main()
