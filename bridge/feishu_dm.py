#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/feishu_dm.py — 飞书 DM 通知桥

对接原脚本：/Users/jason/外贸/scripts/feishu_dm.py（发送给 Jason 的 DM）
  - --text "<内容>"：指定消息内容，转发为原脚本的位置参数；
  - 默认 dry-run：不调用原脚本、不真发 DM，输出 delivered:false；
  - --apply：调原 feishu_dm.py 真实发送。

stdout 单行 JSON：{ok, delivered, dry_run}
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


FEISHU_DM_SCRIPT = ORIGIN_SCRIPTS_DIR / "feishu_dm.py"    # 原 feishu_dm.py 绝对路径


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="飞书 DM 通知桥 — 调原 feishu_dm.py（默认 dry-run，--apply 真发）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--text", default=None, help="DM 消息内容（缺省用原脚本默认测试文案）")
    parser.add_argument("--dry-run", action="store_true", help="干跑（默认；不真发 DM）")
    parser.add_argument("--apply", action="store_true", help="真实发送 DM")
    args = parser.parse_args(argv)

    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    if args.dry_run and args.apply:
        fail("--dry-run 与 --apply 不能同时使用", exit_code=2)
    dry = not args.apply   # 只读优先：未显式 --apply 一律不真发

    if dry:
        emit_json({"ok": True, "delivered": False, "dry_run": True})

    # apply：把 --text 内容原样作为原脚本位置参数（原脚本 main 用 " ".join(sys.argv[1:])）
    sub_args = [args.text] if args.text else []
    result = call_script(FEISHU_DM_SCRIPT, sub_args, product)
    if not result["ok"]:
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[-400:] or "无输出"
        fail("feishu_dm.py 执行失败(rc={}): {}".format(result["returncode"], detail))

    out = result["stdout"] or ""
    if "发送成功" in out:
        emit_json({"ok": True, "delivered": True, "dry_run": False})
    else:
        detail = out.strip()[-300:] or "原脚本无输出"
        fail("feishu_dm.py 未确认发送成功: {}".format(detail))


if __name__ == "__main__":
    main()
