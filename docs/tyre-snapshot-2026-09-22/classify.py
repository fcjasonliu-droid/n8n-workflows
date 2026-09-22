#!/usr/bin/env python3
"""
classify.py — 退信原因归类 (text → select)

按老日报里 daily_report.py 的 BOUNCE_CATEGORIES 分类逻辑保留,
映射到新 select 字段的 7 个选项.

用法:
    from classify import classify_bounce
    cat = classify_bounce("550 Mailbox not found")
    # => "邮箱地址不存在"
"""
from typing import Dict, List, Tuple


# select 选项名 (与 CRM 字段严格一致)
CATEGORIES: List[str] = [
    "邮箱地址不存在",
    "域名MX/解析失败",
    "邮箱被禁用/冻结",
    "反垃圾拦截",
    "邮件过大/附件",
    "临时失败(可重试)",
    "其他",
]


# (类别, 关键词列表) — 按优先级匹配, 命中即返回
_RULES: List[Tuple[str, List[str]]] = [
    ("邮箱地址不存在", [
        "address not found", "user unknown", "no such user", "no such recipient",
        "mailbox not found", "mailbox unavailable", "recipient rejected",
        "does not exist", "550", "5.1.1", "5.1.2",
        "地址不存在", "用户不存在", "收件人不存在",
    ]),
    ("域名MX/解析失败", [
        "domain not found", "no mx record", "no mx", "host not found", "host unknown",
        "dns query", "nxdomain", "无法解析", "域名不存在", "mx 记录", "no dns",
    ]),
    ("邮箱被禁用/冻结", [
        "mailbox disabled", "mailbox locked", "account disabled", "account locked",
        "frozen", "suspended", "deactivated", "no longer active", "closed",
        "邮箱被禁用", "账户已停用", "账户已锁定",
    ]),
    ("反垃圾拦截", [
        "spam", "blacklist", "blocked", "rejected by content", "policy",
        "554", "5.7.1", "5.7.0", "spf", "dkim", "dmarc",
        "反垃圾", "被拦截", "被拒绝", "内容违规", "黑名单",
    ]),
    ("邮件过大/附件", [
        "message too large", "size limit", "attachment too", "exceeds size",
        "message size", "邮件过大", "附件过大",
    ]),
    ("临时失败(可重试)", [
        "try again later", "temporary failure", "deferred", "4xx",
        "timed out", "timeout", "temporarily unavailable", "service unavailable",
        "稍后重试", "临时失败",
    ]),
]


def classify_bounce(raw_error: str) -> str:
    """根据原始错误文本归类, 返回 select 选项名.
    未命中任何规则返回 '其他'."""
    if not raw_error:
        return "其他"
    e = raw_error.lower()
    for category, keywords in _RULES:
        for kw in keywords:
            if kw.lower() in e:
                return category
    return "其他"


def classify_bulk(raw_errors: List[str]) -> Dict[str, int]:
    """批量归类 + 计数. 用于发送后写 CRM 前统计."""
    counts = {c: 0 for c in CATEGORIES}
    for err in raw_errors:
        counts[classify_bounce(err)] += 1
    return counts


if __name__ == "__main__":
    # smoke test
    tests = [
        ("550 Mailbox not found", "邮箱地址不存在"),
        ("554 5.7.1 spam content rejected", "反垃圾拦截"),
        ("timed out", "临时失败(可重试)"),
        ("Mailbox disabled", "邮箱被禁用/冻结"),
        ("domain not found", "域名MX/解析失败"),
        ("size limit exceeded", "邮件过大/附件"),
        ("unknown error", "其他"),
    ]
    for raw, expected in tests:
        got = classify_bounce(raw)
        ok = "✅" if got == expected else "❌"
        print(f"{ok}  {raw!r:50s} → {got}")
