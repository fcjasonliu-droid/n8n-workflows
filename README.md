# ESD → n8n 迁移工作区

> 2026-09-08 启动。目标：把 `~/外贸/` 的 13 脚本 + launchd cron 邮件系统迁移到 n8n，
> 变成「1 个产品档案 + 1 个全链路 workflow」的可复用模板。

## 目录结构

```
~/esd-n8n/
├── products/           ← 产品档案 (参数化输入, 唯一事实源)
│   └── esd-rubber-mat.json
├── workflows/          ← n8n workflow JSON (父 + 子)
├── bridge/             ← n8n 与 原 ~/外贸/ 脚本的桥接包 (Codex 写)
├── docs/               ← 迁移文档 / schema
└── logs/               ← 运行日志
```

## 核心原则 (C 方案)

- **产品档案 = 参数化输入**：未来扩产品 = 复制 products/ 加一个 JSON + workflow 副本
- **9 段全链路**：客户开发 → 询盘 → 报价 → 合同 → 生产 → 验货 → 报关 → 出货 → 收汇
- **stub 占位**：现在没有真实数据流的段先建 stub (空节点 + TODO)，后面补

## 数据源 (参考不复制)

- 原系统：`~/外贸/` (SYSTEM_STATUS.md 是重建手册)
- crm-esd 飞书 base：`W6YCbR5rsaqJUxsGqjscKaAxn1f`
- 字段映射：`~/外贸/scripts/feishu_field_ids.json`
- 国家时区：`~/外贸/scripts/country_tz.json`
- 邮箱配置：`~/外贸/email_config.json` (密码在 Keychain: esd-aliyun-send-smtp / esd-aliyun-imap)
