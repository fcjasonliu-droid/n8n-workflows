# n8n 发布索引死循环事故（2026-09-27）

> 一句话：**坏版本数 ≥ `indexingBatchSize`（默认 10）时，n8n 的 WorkflowIndex 会退化成无限空转**，
> 以 ~2 GiB/小时刷同一行日志；再叠加 launchd `StandardOutPath` 没有轮转，43 小时写出 66.84 GiB，
> 把 250 GB 系统盘顶到 95%。

## 1. 实测数字（用于判断是不是同一个坑）

| 项 | 值 |
|---|---|
| 单文件日志峰值 | **72.73 GiB**（占数据卷 34%） |
| 系统盘可用 | 30 GiB → **11 GiB**（95% 满） |
| 单次会话写入 | 66.84 GiB / 43.4 小时 |
| 写入速率 | 约 6,000 行/秒 = **2.0–2.1 GiB/小时 ≈ 50 GiB/天** |
| 循环轮数 | **55,717,149** 轮（修复后 n8n 自己打印出来的） |
| 刷屏内容 | 只有一句，10 个 UUID 循环：`Workflow <uuid> has activeVersionId but no activeVersion nodes. Skipping published index.` |

## 2. 机理（n8n 2.38.5，源码级）

`dist/modules/workflow-index/workflow-index.service.js`：

```js
const LOOP_LIMIT = 1_000_000_000;

async buildIndexInternal(unindexedWorkflowFinder, dependencyType) {
  let processedCount = 0;
  while (processedCount < LOOP_LIMIT) {
    const workflows = await unindexedWorkflowFinder(this.batchSize);   // indexingBatchSize 默认 10
    if (workflows.length === 0) break;
    for (const workflow of workflows) {
      const publishedNodes = workflow.activeVersion?.nodes;
      if (!publishedNodes) {
        this.logger.warn(`Workflow ${workflow.id} has activeVersionId but no activeVersion nodes. Skipping published index.`);
        continue;                       // ← 索引不成功 → 永远留在"待索引"集合
      }
      await this.updateIndexForPublished(workflow, workflow.activeVersionId, publishedNodes);
    }
    processedCount += workflows.length;
    if (workflows.length < batchSize) break;   // ← 正常情况下唯一的出口
  }
}
```

`@n8n/config` 的 `workflows.config.js` 里：`this.indexingBatchSize = 10;`

**触发条件（关键）**：坏 workflow 数量 **≥ 10** 时，每轮 finder 都返回满批 10 个 →
`workflows.length < batchSize` 恒为假 → 唯一出口失效 → 空转到 10 亿上限。

**坏数 ≤ 9 时完全无害**（每次启动只刷一遍 9 行警告）—— 所以这个雷会潜伏很久，
然后在第 10 个 workflow 坏掉的那一刻突然爆发。本次就是如此（第 10 个坏数据出现在 9/25 14:20，
n8n 于 14:24 重启后开始暴走）。

## 3. 同一次事故里的第二个故障：缺 `shared_workflow` → 定时任务静默停跑

直写 SQL 建 workflow 时漏插 `shared_workflow`（项目归属）**不只**让 UI 看不到，还会让**启动激活静默失败**：

```
Activation of workflow "<name>" (<id>) did fail with error:
"Could not find any entity of type "SharedWorkflow" matching: {...}" | retry in 2 seconds
```

后果：`active=1` 查得到、UI 显示已激活，但**触发器从未注册**，`execution_entity` 永远 0 行。
本次 38 个 workflow 里 `active=1` 的有 26 个，**只有 6 个能真正激活**，其余 20 个（含多个每日/每周定时任务）
已静默停跑多日而无人察觉。

## 4. 修复（2026-09-27 实测有效）

### 4.1 止血：截断日志（不需要停服务）

```bash
: > ~/n8n/n8n.log        # 实测释放 72.77 GiB，进程 fd 继续可用
```

> ❌ 不要 `rm` —— 进程还开着 fd，删了**不释放空间**，只会留下一个看不见的已删除文件（`lsof +L1` 可见）。

### 4.2 修死循环 + 修静默停跑

执行 `fix-publish-index-loop.sql`（本目录，含两条 SQL 与验收查询），然后：

```bash
launchctl kickstart -k gui/$(id -u)/ai.n8n      # 必须重启：激活与缓存都发生在启动时
```

**效果是立即的**：finder 每轮重新查库，坏数从 10 掉到 9 后，正在跑的循环下一轮就 `break`（实测 30 秒内日志增长归零），
日志里出现 `Finished building workflow dependency index. Processed 0 draft workflows, 55717149 published workflows.`

### 4.3 验收

```bash
bash n8n-integrity-check.sh          # 退出码 0/1/2，三条死亡条件必须全为 0
grep -c "Skipping published index" ~/n8n/n8n.log      # 重启后新增应为 0
grep -c "did fail with error" ~/n8n/n8n.log           # 只剩"无触发器的占位 workflow"才正常
```

## 5. 四条走不通的"自举"路径（版本行已丢失时，别再试）

| 路径 | 实测结果 |
|---|---|
| `n8n publish:workflow --id=X` | `Version "<v>" not found for workflow "<id>"`（无版本可发布） |
| `POST /api/v1/workflows/{id}/activate` | `HTTP 404 {"message":"Version not found"}` |
| `n8n import:workflow --input=...` | 常规模式不支持 `--activeState=fromJson`；不带则报 `User attempted to deactivate a workflow without permissions`；`--userId` 与 `--projectId` 不能同用 |
| `PUT /api/v1/workflows/{id}` | 返回 200 但**不写 `workflow_history`**（该配置 `useWorkflowPublicationService=false`） |
| `unpublish:workflow --id=X` | 能成功，但会把 `active=0` **和** `activeVersionId=NULL` 一起清掉 —— 回滚时必须两个字段一起补回 |

> 另外：自定义 UUID 型 id 的 workflow 若缺 `shared_workflow`，公共 API `GET /workflows/{id}` 直接 **404**
> （项目作用域过滤），所以"用 API 修"在这批脏数据上根本不可用。
>
> ⚠️ 更正一条旧经验：**直插 `workflow_history` 不是反模式**，实测「直插 + 重启 n8n」完全有效
> （本次 10 个死 workflow 全部恢复激活）。关键是**必须重启**让 n8n 重读 DB。

## 6. 每次直写 n8n DB 之后必跑

```bash
bash n8n-integrity-check.sh
```

| 条件 | 判据 | 后果 |
|---|---|---|
| A 坏版本 | `activeVersionId` 非空但 `workflow_history` 无该 `versionId` | **≥10 触发死循环**；<10 则该 workflow 激活失败 |
| B 缺归属 | `active=1` 但无 `shared_workflow` 行 | 启动激活静默失败，cron 永不注册 |
| C 缺发布行 | 无 `workflow_published_version` 行 | publish/unpublish 语义不完整 |

护栏脚本 `log-disk-guard.sh`（launchd `ai.jason.log-disk-guard`，每 5 分钟）已把这三条做成常驻预警：
**坏版本 ≥ 9 即 critical**（差 1 个就触雷），并负责日志硬上限、磁盘水位告警、磁盘历史留痕。

## 7. 取证手法（→ `n8n-log-flood-forensics.py`）

1. **偏移切片**：按文件百分比抽样，统计"同一句刷屏"占比。本次 2.5% 偏移之后 100% 是刷屏 → 立刻判定是死循环，而非"日志偏多"。
2. **反向扫描会话边界**：从 EOF 往前找 `Initializing n8n process`，得到每个会话的字节跨度。本次只有 2 个标记 → 上一个会话一口气写了 66.84 GiB，直接锁定爆发起点。
3. **反推时间**：`~/.n8n/n8nEventLog-*.log` 是按启动轮转的 JSON 事件日志，自带 `ts`，天然给出会话起止（配合第 2 步的字节偏移即可算出速率）。
4. **不要依赖 df 历史**：macOS 不留磁盘历史，且 `df` 的 Avail 受 purgeable 影响会跳变 —— 用常驻护栏留痕（`log-disk-guard.sh` 每 5 分钟写一行）。

## 8. 一句话教训

**n8n 的"直写 SQLite"路线，每写一行都必须把 `workflow_entity` / `shared_workflow` /
`workflow_history` / `workflow_published_version` 四张表当成一个整体**；漏插不会立刻报错，
而是以"静默停跑"或"两天后刷爆磁盘"的形式出现。
