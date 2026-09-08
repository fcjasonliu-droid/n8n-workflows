#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/stub.py — 占位段桥（报价/合同/生产/验货/报关/出货/收汇等）

给 n8n 后续真实实现预留接口：只 echo TODO（走 stderr，避免污染 stdout JSON）
并输出 {ok: true, stub: true, segment: ...}，stdout 保持单行 JSON。

stdout 单行 JSON：{ok, stub, segment, todo}
"""

import argparse
import sys

from common import (
    DEFAULT_PRODUCT,
    emit_json,
    load_product,
    fail,
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="占位段桥 — 为 n8n 后续真实实现预留接口（stub）")
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="产品 id（读取 products/<id>.json，默认 {}）".format(DEFAULT_PRODUCT))
    parser.add_argument("--segment", default="quotation",
                        help="业务段名称（如 quotation/contract/production/...，默认 quotation）")
    args = parser.parse_args(argv)

    product = load_product(args.product)
    if isinstance(product, dict) and product.get("ok") is False:
        fail(product.get("error", "产品档案加载失败"), exit_code=2)

    segment = args.segment
    todo = "TODO: 段 '{}' 暂未接入真实脚本，等待 n8n workflow 落地实现".format(segment)
    # TODO 提示走 stderr，保证 stdout 只有一行 JSON（n8n 可安全 json.loads）
    sys.stderr.write(todo + "\n")
    emit_json({"ok": True, "stub": True, "segment": segment, "todo": todo})


if __name__ == "__main__":
    main()
