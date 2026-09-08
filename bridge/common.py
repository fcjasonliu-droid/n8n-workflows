#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge 公共工具库 — ESD → n8n 迁移的薄封装层

约定（见 docs/bridge-spec.md）：
  - 本包是唯一允许接触原脚本/队列/Keychain 的层，不重写原业务逻辑；
  - 一律用 /usr/bin/python3 通过 subprocess 调 /Users/jason/外贸/scripts 下的原脚本；
  - 输出：stdout 只有一行 JSON（{ok: bool, ...}），失败时 {ok: false, error: "..."} 且退出码非 0；
  - 只读优先：未显式传 --apply 的脚本一律 dry-run。

本文件提供三个核心函数：
  - load_product(product_id): 读 ../products/<product_id>.json（找不到返回 ok:false 字典）
  - call_script(script_path, args, product): subprocess 调原脚本（超时 120s，stdout 尝试 json.loads）
  - env_with_home(): 构造子进程环境（HERMES_HOME + os.environ）

也可独立运行做自测：/usr/bin/python3 common.py --self-test
"""

import json
import os
import subprocess
import sys
from pathlib import Path


# ---------- 包内路径常量 ----------
BASE_DIR = Path(__file__).resolve().parent.parent        # /Users/jason/esd-n8n
PRODUCTS_DIR = BASE_DIR / "products"                     # 产品档案目录（唯一事实源）
ORIGIN_DIR = Path("/Users/jason/外贸")                    # 原脚本系统根目录
ORIGIN_SCRIPTS_DIR = ORIGIN_DIR / "scripts"               # 原脚本目录
HERMES_HOME = "/Users/jason/.hermes"                      # Hermes 环境变量家目录
DEFAULT_PRODUCT = "esd-rubber-mat"                        # 默认产品 id（验收/自测用）
PYTHON = "/usr/bin/python3"                               # macOS 系统自带 Python
CALL_TIMEOUT_SECONDS = 120                                # 调用原脚本的超时上限


def env_with_home():
    """构造子进程环境变量：os.environ + HERMES_HOME，并固定 UTF-8 输出编码。

    密码明文一律由原脚本经 keychain.py 读取，本层不触碰。
    """
    env = dict(os.environ)
    env["HERMES_HOME"] = HERMES_HOME
    env["PYTHONIOENCODING"] = "utf-8"   # 保证中文 stdout 解析稳定
    return env


def load_product(product_id):
    """读取 ../products/<product_id>.json 产品档案。

    成功：返回产品档案 dict（原样 JSON，未做改动）；
    失败（缺参数/文件不存在/解析错误）：返回 {"ok": False, "error": "..."} 字典。
    """
    if not product_id:
        return {"ok": False, "error": "缺少 --product 产品参数"}
    path = PRODUCTS_DIR / "{}.json".format(product_id)
    if not path.exists():
        return {"ok": False, "error": "产品档案不存在: {}".format(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "产品档案 JSON 解析失败 {}: {}".format(path, exc)}


def call_script(script_path, args=None, product=None, timeout=CALL_TIMEOUT_SECONDS):
    """用 /usr/bin/python3 subprocess 调用原脚本（薄封装核心）。

    返回统一结果字典：
      {ok: bool, returncode: int|None, stdout: str, stderr: str, payload: dict|None}
    - ok=False 表示调用本身失败（脚本不存在/超时/找不到解释器/退出码非 0）；
    - payload 仅在 stdout 可整体或末行被 json.loads 解析时非空（原脚本多为人文输出，
      此时 payload 为 None，由各 wrapper 自己正则解析）。
    """
    args = list(args or [])
    script = Path(script_path)
    cmd = [PYTHON, str(script)] + [str(a) for a in args]
    if not script.exists():
        return {"ok": False, "returncode": None,
                "stdout": "", "stderr": "原脚本不存在: {}".format(script), "payload": None}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
            env=env_with_home(),
            cwd=str(ORIGIN_DIR),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None,
                "stdout": "", "stderr": "调用超时({}s): {}".format(timeout, cmd), "payload": None}
    except FileNotFoundError:
        return {"ok": False, "returncode": None,
                "stdout": "", "stderr": "找不到解释器: {}".format(PYTHON), "payload": None}

    out = proc.stdout or ""
    err = proc.stderr or ""
    payload = None
    clean = out.strip()
    if clean:
        # 原脚本若整段可解析成 JSON 则直接采用；否则尝试解析最后一行
        for candidate in (clean, clean.splitlines()[-1]):
            try:
                payload = json.loads(candidate)
                break
            except Exception:
                continue
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": out, "stderr": err, "payload": payload}


def emit_json(payload, exit_code=0):
    """向 stdout 打印单行 JSON（供 n8n Execute Command 解析）并退出。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    sys.exit(exit_code)


def fail(error, exit_code=1, **extra):
    """输出失败 JSON {ok: false, error: "..."} + 非 0 退出码。"""
    payload = {"ok": False, "error": error}
    payload.update(extra)
    emit_json(payload, exit_code=exit_code)


def _self_test():
    """自测：验证产品档案可读 + 原脚本目录可见。"""
    product = load_product(DEFAULT_PRODUCT)
    product_ok = isinstance(product, dict) and product.get("ok") is not False
    if not product_ok:
        error = product.get("error", "product 加载失败") if isinstance(product, dict) else "product 加载失败"
        emit_json({"ok": False, "product_found": False, "error": error}, exit_code=1)
    if not ORIGIN_SCRIPTS_DIR.is_dir():
        emit_json({"ok": False, "product_found": True,
                   "error": "原脚本目录不存在: {}".format(ORIGIN_SCRIPTS_DIR)}, exit_code=1)
    emit_json({"ok": True, "product_found": True})


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        sys.stderr.write("用法: /usr/bin/python3 common.py --self-test\n")
        fail("缺少 --self-test 参数")
