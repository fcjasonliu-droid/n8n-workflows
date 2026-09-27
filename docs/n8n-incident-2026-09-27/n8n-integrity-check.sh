#!/bin/bash
# n8n 数据一致性体检（只读）—— 任何直写 n8n SQLite 之后必须跑一次
#
# 背景：2026-09-27 事故。坏版本数达到 n8n 的 indexingBatchSize(默认 10) 时，
# WorkflowIndex 的 while (processedCount < LOOP_LIMIT=1e9) 唯一出口失效 → 无限刷日志，
# 43 小时写出 66.84 GiB（约 50 GiB/天），把系统盘顶到 95%。
#
# 退出码：0 = ok / 1 = warn（有脏数据但不致命）/ 2 = critical（坏版本 >= 9，再加 1 个就触雷）
#
# 用法：bash n8n-integrity-check.sh [--quiet]

set -u
DB="${N8N_DB:-$HOME/.n8n/database.sqlite}"
PORT="${N8N_PORT:-5678}"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

[ -f "$DB" ] || { echo "✗ 找不到数据库: $DB"; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { echo "✗ 需要 sqlite3"; exit 1; }

q() { sqlite3 "file:$DB?mode=ro" "$1" 2>/dev/null; }

BADVER=$(q "SELECT COUNT(*) FROM workflow_entity w WHERE w.activeVersionId IS NOT NULL AND NOT EXISTS (SELECT 1 FROM workflow_history h WHERE h.versionId = w.activeVersionId);")
NOOWN=$(q "SELECT COUNT(*) FROM workflow_entity w WHERE w.active = 1 AND NOT EXISTS (SELECT 1 FROM shared_workflow s WHERE s.workflowId = w.id);")
NOHIST=$(q "SELECT COUNT(*) FROM workflow_entity w WHERE EXISTS (SELECT 1 FROM workflow_history h WHERE h.workflowId = w.id) AND NOT EXISTS (SELECT 1 FROM workflow_history h2 WHERE h2.workflowId = w.id AND h2.versionId = w.activeVersionId);")
NOPUB=$(q "SELECT COUNT(*) FROM workflow_entity w WHERE w.activeVersionId IS NOT NULL AND NOT EXISTS (SELECT 1 FROM workflow_published_version p WHERE p.workflowId = w.id);")
TOTAL=$(q "SELECT COUNT(*) FROM workflow_entity;")
ACTIVE=$(q "SELECT COUNT(*) FROM workflow_entity WHERE active=1;")
LOG=$(ls -l "$HOME/n8n/n8n.log" 2>/dev/null | awk '{printf "%.1f", $5/1048576}')

BADVER=${BADVER:-0}; NOOWN=${NOOWN:-0}; NOHIST=${NOHIST:-0}; NOPUB=${NOPUB:-0}

if [ "$QUIET" -eq 0 ]; then
  echo "=== n8n 一致性体检 $(date '+%Y-%m-%d %H:%M:%S') ==="
  echo "  workflow 总数 / active : ${TOTAL:-?} / ${ACTIVE:-?}"
  echo "  n8n.log 大小            : ${LOG:-?} MiB"
  echo
  echo "  [A] 有 activeVersionId 但无 workflow_history 版本行 : ${BADVER}"
  echo "      → 死循环触发源 + 该 workflow 激活会失败（阈值 = indexingBatchSize = 10）"
  echo "  [B] active=1 但无 shared_workflow 归属              : ${NOOWN}"
  echo "      → 启动激活静默失败：Could not find any entity of type \"SharedWorkflow\"，cron 不注册"
  echo "  [C] 有历史版本但没有一个是当前 activeVersionId      : ${NOHIST}"
  echo "  [D] 缺 workflow_published_version 行（影响 publish 语义）: ${NOPUB}"
  echo
fi

RC=0
if [ "$BADVER" -ge 9 ]; then
  echo "🔴 CRITICAL: 坏版本 ${BADVER} 个（>=9）。再加 1 个就会重现 72GB 日志刷屏，立刻修："
  echo "     references/publish-index-death-loop-20260927.md → 「修复配方」第 2 步"
  RC=2
elif [ "$BADVER" -ge 1 ] || [ "$NOOWN" -ge 1 ]; then
  echo "🟡 WARN: 坏版本 ${BADVER} 个 / 缺归属 ${NOOWN} 个（未到死循环阈值，但相关 workflow 不会正常跑）"
  RC=1
else
  [ "$QUIET" -eq 0 ] && echo "🟢 OK: 三条死亡条件全部为 0"
fi

# 附带：日志尾部是否在刷屏（症状复核）
if [ -n "${LOG:-}" ]; then
  RATE=$(stat -f %z "$HOME/n8n/n8n.log" 2>/dev/null || echo 0)
  if [ "$RATE" -gt 268435456 ]; then
    echo "🟡 n8n.log 已 >256MiB，护栏（ai.jason.log-disk-guard）会在下个 5 分钟内自动压到 8MiB"
    [ "$RC" -eq 0 ] && RC=1
  fi
fi

exit "$RC"
