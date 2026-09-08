#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""bridge/healthcheck.py — CRM 同步健康检查桥

对比本地已发数量与飞书开发信记录表数量, 判断 crm_sync 是否在正常写回。
用法:
  python3 healthcheck.py --product esd-rubber-mat            # 检查, 输出 {ok, healthy, ...}
  python3 healthcheck.py --product esd-rubber-mat --apply    # 不一致时发飞书 DM 报警

stdout 单行 JSON:
  healthy=true  → {ok:true, healthy:true, local_sent:N, feishu_records:M}
  healthy=false → {ok:true, healthy:false, local_sent:N, feishu_records:M, drift:D, alert:"..."}
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ORIGIN_BASE = Path("/Users/jason/外贸")
SENT_DIR = ORIGIN_BASE / "queue" / "sent"
sys.path.insert(0, str(ORIGIN_BASE / "scripts"))

# 修复 PATH (与 cron_tick.sh 相同): lark-cli 需要 node
os.environ["PATH"] = "/Users/jason/.local/bin:/Users/jason/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
os.environ["HERMES_HOME"] = "/Users/jason/.hermes"

LARK = "/Users/jason/.npm-global/bin/lark-cli"
APP = "W6YCbR5rsaqJUxsGqjscKaAxn1f"
OUTREACH_TABLE = "tblPGxlebpgrb1rQ"

# 允许的漂移阈值: 正在发信的瞬间 sent 可能先于飞书, 容忍小漂移
DRIFT_TOLERANCE = 5


def count_local_sent():
    """统计本地 sent 目录 task 数 (send_history 最后状态=sent)"""
    if not SENT_DIR.exists():
        return 0
    count = 0
    for f in glob.glob(str(SENT_DIR / "task_*.json")):
        try:
            task = json.loads(open(f).read())
            sh = task.get("send_history", [])
            if sh and sh[-1].get("status") == "sent":
                count += 1
        except Exception:
            pass
    return count


def count_feishu_records():
    """统计飞书开发信记录表记录数"""
    env = {**os.environ, "HERMES_HOME": "/Users/jason/.hermes"}
    r = subprocess.run([LARK, "base", "+record-list",
        "--base-token", APP, "--table-id", OUTREACH_TABLE,
        "--limit", "2000", "--format", "ndjson",
        "--output", "/tmp/healthcheck_records.ndjson",
        "--overwrite", "--as", "bot"],
        capture_output=True, text=True, env=env, cwd='/', timeout=90)
    nd = "/tmp/healthcheck_records.ndjson"
    if os.path.exists(nd):
        return len([l for l in open(nd).read().strip().split("\n") if l.strip()])
    return -1


def send_alert(text):
    """飞书 DM 报警 - 调原 feishu_dm.py"""
    env = {**os.environ, "HERMES_HOME": "/Users/jason/.hermes"}
    try:
        r = subprocess.run(["/usr/bin/python3", str(ORIGIN_BASE / "scripts" / "feishu_dm.py"),
            "--text", text], capture_output=True, text=True, env=env, cwd='/', timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="CRM 同步健康检查")
    parser.add_argument("--product", default="esd-rubber-mat")
    parser.add_argument("--apply", action="store_true", help="不一致时发飞书 DM 报警")
    args = parser.parse_args(argv)

    local_sent = count_local_sent()
    feishu_records = count_feishu_records()
    drift = local_sent - feishu_records

    if feishu_records < 0:
        out = {"ok": False, "error": "无法读取飞书记录数", "local_sent": local_sent}
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)

    healthy = abs(drift) <= DRIFT_TOLERANCE
    out = {
        "ok": True,
        "healthy": healthy,
        "local_sent": local_sent,
        "feishu_records": feishu_records,
        "drift": drift,
        "tolerance": DRIFT_TOLERANCE,
    }
    if not healthy:
        direction = "飞书落后" if drift > 0 else "飞书超前"
        out["alert"] = f"⚠️ CRM同步异常: 本地已发 {local_sent} 封, 飞书记录 {feishu_records} 条, {direction} {abs(drift)} 条 (容忍 {DRIFT_TOLERANCE})"
        if args.apply:
            out["alert_sent"] = send_alert(out["alert"])
    else:
        out["note"] = f"CRM 同步正常: 本地 {local_sent} / 飞书 {feishu_records}, 漂移 {drift}"

    print(json.dumps(out, ensure_ascii=False))
    return 0 if healthy else 2


if __name__ == "__main__":
    sys.exit(main())
