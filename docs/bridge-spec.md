# Bridge 包规格 — ESD → n8n 迁移

> 目标读者：Codex（写代码执行者）。背景：把 `/Users/jason/外贸/` 的 13 脚本邮件系统
> 迁移到 n8n。n8n workflow 用 `Execute Command` 节点调 bridge 脚本，bridge 是唯一允许
> 接触原脚本/队列/Keychain 的薄封装层。产物目录 `/Users/jason/esd-n8n/`（git repo）。

## 核心约束（必须遵守）

1. **bridge = 薄封装**。不要重写原业务逻辑。优先 `subprocess` 调原脚本
   （`/Users/jason/外贸/scripts/*.py`），原脚本自己处理路径/环境/Keychain。
2. **输入**：所有 bridge 脚本接受 `--product <product_id>`（从
   `/Users/jason/esd-n8n/products/<product_id>.json` 读配置）。
3. **输出**：stdout 一行 JSON（`{ok: bool, ...}`），供 n8n 解析。失败时
   `{ok: false, error: "..."}` + exit code 非 0。
4. **只读优先**：未显式传 `--apply` 的脚本一律 dry-run（不真发信/不真写库）。
5. Python 用 `/usr/bin/python3`（macOS 系统自带，无第三方依赖，与原脚本一致）。
6. 不触碰 Keychain 密码明文：读取走原 `keychain.py`（subprocess 调原脚本时自动处理）。
7. 每个 bridge 脚本是**独立文件**，可单独 `python3 xx.py --product esd-rubber-mat [--apply]` 运行。

## 交付物（8 个文件）

### 0. `__init__.py` — 空

### 1. `common.py` — 共享工具
- `load_product(product_id)` → dict（读 `../products/<id>.json`，找不到返回 ok:false）
- `call_script(script_path, args, product)` → dict（subprocess 调原脚本，
  超时 120s，stdout 尝试 json.loads）
- `env_with_home()` → dict（`HERMES_HOME=/Users/jason/.hermes` + os.environ）

### 2. `customer_dev.py` — 客户开发（拉 crm 视图 → 生成任务）
- 调原 `producer.py`（默认 `--dry-run`，`--apply` 时全量跑）
- 输出 `{ok, fetched, skipped, pending_dir}`（dry-run 时 fetched=预览条数）

### 3. `scheduler.py` — 时区调度
- 调原 `scheduler.py --tick`（`--preview` 预览）
- 输出 `{ok, moved, ready_dir}`

### 4. `email_sender.py` — SMTP 发送
- 调原 `worker.py --once --max 1`（默认 `--dry-run`，`--apply` 真发）
- 输出 `{ok, sent, failed, reason}`（从原脚本 stdout/退出码推断）

### 5. `crm_sync.py` — CRM 回写（写飞书 crm-esd 开发信记录表）
- 默认 dry-run 不写；`--apply` 时调原 `crm_sync.py` 写回
- 输出 `{ok, records_created}`

### 6. `inquiry_receive.py` — 询盘接收（IMAP）
- 调原 `imap_poll.py`（默认 dry-run 扫描不标已读；`--apply` 才 `--mark`）
- 输出 `{ok, scanned, bounced, replied}`

### 7. `feishu_dm.py` — 飞书 DM 通知
- 参数 `--text "<内容>"`；调原 `feishu_dm.py`
- 输出 `{ok, delivered}`

### 8. `stub.py` — 占位段（报价/合同/生产/验货/报关/出货/收汇）
- 参数 `--segment <name>`；只 echo TODO + `{ok:true, stub:true, segment:...}`
- 给 n8n 里后续真实实现预留接口

## 验收标准

```bash
cd /Users/jason/esd-n8n/bridge
/usr/bin/python3 common.py --self-test          # 打印 {ok:true, product_found:true}
/usr/bin/python3 customer_dev.py --product esd-rubber-mat --dry-run   # {ok:true, fetched:N}
/usr/bin/python3 scheduler.py --product esd-rubber-mat --preview      # {ok:true, ...}
/usr/bin/python3 email_sender.py --product esd-rubber-mat --dry-run   # {ok:true} 不真发
/usr/bin/python3 crm_sync.py --product esd-rubber-mat --dry-run       # {ok:true} 不真写
/usr/bin/python3 inquiry_receive.py --product esd-rubber-mat --dry-run # {ok:true} 不标已读
/usr/bin/python3 feishu_dm.py --product esd-rubber-mat --text "测试"  # 可选: 需 --apply
/usr/bin/python3 stub.py --product esd-rubber-mat --segment quotation # {ok:true, stub:true}
```

所有验收命令**必须真实跑通**（dry-run 不真发信/不真写库/不真发 DM），
把输出贴到回复里。禁止伪造输出。
