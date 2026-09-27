#!/usr/bin/env python3
"""n8n 日志刷屏/暴走取证工具（只读）

用法:
  python3 n8n-log-flood-forensics.py <日志路径> [--samples 41] [--window-mb 4]

做三件事（2026-09-27 事故中用来定位根因的手法）:
  1) 偏移切片：按文件百分比抽样，统计"同一句刷屏"占比 → 区分「死循环刷屏」与「日志偏多」
  2) 反向扫描：从 EOF 往前找会话起点（如 Initializing n8n process）→ 会话字节跨度
  3) 实时速率：同一文件在 30 秒内的增长 → 折算 MiB/小时、GiB/天

示例输出解读:
  偏移 2.5% 之后 100% 是刷屏行  →  死循环，不是正常日志
  只有 2 个会话起点，前一个跨 66.84 GiB →  爆发点在那个会话
  2.1 GiB/小时 →  50 GiB/天，磁盘会在一夜之间被顶满
"""
import os
import sys
import time
import argparse

FLUSH_DEFAULT = b"has activeVersionId but no activeVersion nodes"
SESSION_MARKERS = [b"Initializing n8n process", b"n8n ready on"]


def human(n: float) -> str:
    return f"{n / 1024 ** 3:.2f} GiB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.1f} MiB"


def scan_slices(path: str, samples: int, window_mb: int) -> None:
    size = os.path.getsize(path)
    window = window_mb * 1024 * 1024
    step = max(size // samples, 1)
    print(f"[1] 偏移切片扫描  ({human(size)}, {samples} 个采样点, 每个 {window_mb} MiB)")
    print(f"    {'偏移%':>6} {'偏移':>10} {'刷屏行占比':>10} {'采样行数':>8}  样本")
    with open(path, "rb") as f:
        for i in range(samples + 1):
            off = min(i * step, max(size - 1, 0))
            f.seek(off)
            data = f.read(window)
            lines = [l for l in data.split(b"\n")[1:-1] if l.strip()]
            if not lines:
                continue
            hit = sum(1 for l in lines if FLUSH_DEFAULT in l)
            sample = lines[len(lines) // 2][:90].decode("utf-8", "replace")
            print(f"    {off / size * 100:6.1f} {human(off):>10} {hit / len(lines) * 100:9.1f}% {len(lines):>8}  {sample}")


def scan_sessions(path: str, block_mb: int = 64, keep: int = 10) -> None:
    size = os.path.getsize(path)
    block = block_mb * 1024 * 1024
    print(f"\n[2] 反向扫描会话起点（每个会话写了多少）")
    found, pos, carry = [], size, b""
    with open(path, "rb") as f:
        while pos > 0 and len(found) < keep:
            start = max(0, pos - block)
            f.seek(start)
            data = f.read(pos - start)
            buf = data + carry
            best = -1
            for marker in SESSION_MARKERS:
                idx = buf.rfind(marker)
                if idx > best:
                    best = idx
            if best != -1:
                found.append(start + best)
                carry = buf[:best]
            else:
                carry = buf[-64:]
            pos = start
    if not found:
        print("    未找到会话标记 —— 可能整个文件都是同一会话的产物（本身就是暴走信号）")
        return
    prev = size
    for i, off in enumerate(found):
        print(f"    第{i + 1}近的会话起点 offset={off:,}  该会话写入 {human(prev - off)}")
        prev = off
    if len(found) >= 2:
        span = found[0] - found[1]
        print(f"    → 最近一个完整会话跨 {human(span)}；若远超其它会话，爆发点就在这里")


def rate(path: str, seconds: int = 30) -> None:
    print(f"\n[3] 实时写入速率（{seconds}s 采样）")
    try:
        a = os.path.getsize(path)
    except OSError as e:
        print(f"    读取失败: {e}")
        return
    time.sleep(seconds)
    b = os.path.getsize(path)
    d = b - a
    print(f"    {seconds}s 增长 {human(d)}")
    print(f"    折算: {d * 3600 / seconds / 1024 ** 3:.2f} GiB/小时  |  {d * 86400 / seconds / 1024 ** 3:.1f} GiB/天")
    if d * 86400 / seconds / 1024 ** 3 > 5:
        print("    🔴 这不是正常日志速率 —— 按 scripts/n8n-integrity-check.sh 查死循环条件")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="日志路径，如 ~/n8n/n8n.log")
    ap.add_argument("--samples", type=int, default=41)
    ap.add_argument("--window-mb", type=int, default=4)
    ap.add_argument("--secs", type=int, default=30)
    ap.add_argument("--no-rate", action="store_true", help="跳过实时速率采样")
    a = ap.parse_args()

    path = os.path.expanduser(a.log)
    if not os.path.isfile(path):
        print(f"✗ 文件不存在: {path}")
        return 1
    print(f"目标: {path}")
    scan_slices(path, a.samples, a.window_mb)
    scan_sessions(path)
    if not a.no_rate:
        rate(path, a.secs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
