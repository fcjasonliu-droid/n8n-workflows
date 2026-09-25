#!/usr/bin/env python3
"""
healthcheck.py — 链路健康检查

每日检查:
  - n8n 是否存活
  - SMTP 是否可连接
  - IMAP 是否可连接
  - 上次发送/回复/规整 的时间

输出: 一行 JSON 到 stdout, 同时追加到 logs/health_*.log
"""
import argparse
import datetime as dt
import json
import os
import smtplib
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

PIPELINE_ROOT = Path(__file__).parent.parent
PRODUCTS_DIR = PIPELINE_ROOT / "products"
sys.path.insert(0, str(PIPELINE_ROOT / "scripts"))


def main():
    """[自动加, 9-22] 入口: 跑健康检查 (n8n workflow 用)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    args = ap.parse_args()

    cfg = json.loads((PIPELINE_ROOT / "products" / args.product / "config.json").read_text())
    health = {
        "ts": dt.datetime.now().isoformat(),
        "product": args.product,
        "checks": {},
    }

    # 1) n8n
    try:
        with urllib.request.urlopen("http://127.0.0.1:5678/healthz", timeout=5) as r:
            health["checks"]["n8n"] = "ok" if r.status == 200 else f"http {r.status}"
    except Exception as e:
        health["checks"]["n8n"] = f"FAIL: {e}"

    # 2) SMTP
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=ctx, timeout=10) as s:
            s.login(os.environ.get(cfg["smtp_user_env"], ""), os.environ.get(cfg["smtp_pass_env"], ""))
            health["checks"]["smtp"] = "ok"
    except Exception as e:
        health["checks"]["smtp"] = f"FAIL: {type(e).__name__}"

    # 3) IMAP
    try:
        import imaplib
        ctx = ssl.create_default_context()
        m = imaplib.IMAP4_SSL(cfg["imap_host"], 993, ssl_context=ctx)
        m.login(os.environ.get(cfg["imap_user_env"], ""), os.environ.get(cfg["imap_pass_env"], ""))
        m.select("INBOX", readonly=True)
        typ, data = m.search(None, "ALL")
        n_msgs = len(data[0].split()) if data and data[0] else 0
        m.logout()
        health["checks"]["imap"] = f"ok ({n_msgs} msgs)"
    except Exception as e:
        health["checks"]["imap"] = f"FAIL: {type(e).__name__}: {e}"

    # 4) CRM (read 1 record)
    try:
        r = subprocess.run(
            [cfg["crm"]["lark_cli"], "base", "+record-list",
             "--base-token", cfg["crm"]["base_token"],
             "--table-id", cfg["crm"]["tables"]["customer"],
             "--limit", "1",
             "--format", "ndjson",
             "--output", "/tmp/_health_kh.ndjson",
             "--overwrite"],
            capture_output=True, text=True, timeout=30
        )
        health["checks"]["crm"] = "ok" if r.returncode == 0 else f"FAIL: {r.stderr[:100]}"
    except Exception as e:
        health["checks"]["crm"] = f"FAIL: {e}"

    print(json.dumps(health, ensure_ascii=False, indent=2))

    # 写 log
    log_path = PIPELINE_ROOT / "products" / args.product / "logs" / f"health_{dt.date.today().strftime('%Y%m%d')}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(health, ensure_ascii=False) + "\n")

    # 任一 FAIL 退出非 0
    if any("FAIL" in str(v) for v in health["checks"].values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
