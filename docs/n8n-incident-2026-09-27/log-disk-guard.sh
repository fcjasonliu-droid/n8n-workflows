#!/bin/bash
# log-disk-guard.sh
# 目的（2026-09-27 因 n8n 死循环写出 72GB 日志事故而建）：
#   1) 兜底任何"单文件无上限增长"的日志：超过阈值就原地截断并保留尾部
#   2) 磁盘低水位告警（可在 macOS 通知中心弹出）
#   3) 每 5 分钟留一条磁盘/日志体积历史，供事后追溯（此前正因缺历史而难定位）
#
# 安装：~/Library/LaunchAgents/ai.jason.log-disk-guard.plist（StartInterval=300）
# 原地截断用 `cat tmp > file`，保持 inode 不变，写入方(O_APPEND)的 fd 继续可用。

set -u

LOGDIR="/Users/jason/.hermes/logs"
STATE="$LOGDIR/log-disk-guard.log"
HIST="$LOGDIR/disk-usage-history.tsv"
ALERT="$LOGDIR/DISK-ALERT.txt"
LOWMARK="$LOGDIR/.log-disk-guard-last-level"

DATA_VOL="/System/Volumes/Data"
WARN_FREE_GB="${WARN_FREE_GB:-20}"
CRIT_FREE_GB="${CRIT_FREE_GB:-8}"

# 精确监视：路径:阈值字节:保留尾部字节
WATCH_FILES=(
  "/Users/jason/n8n/n8n.log:268435456:8388608"        # 256MiB 触发，保留尾部 8MiB
  "/Users/jason/n8n/n8n.err.log:134217728:4194304"    # 128MiB 触发，保留尾部 4MiB
)

# 目录通配监视：目录:阈值字节:保留尾部字节
WATCH_DIRS=(
  "$LOGDIR:536870912:8388608"                          # 本目录所有 *.log 超 512MiB 就截断
)

ts() { date '+%Y-%m-%d %H:%M:%S'; }
say() { echo "[$(ts)] $*" >> "$STATE"; }

notify() { # $1=title $2=message
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
}

cap_file() { # $1=path $2=max_bytes $3=keep_bytes
  local f="$1" max="$2" keep="$3" sz
  [ -f "$f" ] || return 0
  sz=$(stat -f %z "$f" 2>/dev/null || echo 0)
  [ "$sz" -gt "$max" ] || return 0
  local before=$(( sz / 1048576 ))
  tail -c "$keep" "$f" > "$f.guardtmp" 2>/dev/null || { rm -f "$f.guardtmp"; return 0; }
  cat "$f.guardtmp" > "$f" 2>/dev/null || true
  rm -f "$f.guardtmp"
  local after=$(( $(stat -f %z "$f" 2>/dev/null || echo 0) / 1048576 ))
  say "CAP $f: ${before}MiB -> ${after}MiB (阈值 $((max/1048576))MiB)"
}

free_gb() { df -g "$DATA_VOL" 2>/dev/null | tail -1 | awk '{print $4}'; }
used_gb() { df -g "$DATA_VOL" 2>/dev/null | tail -1 | awk '{print $3}'; }

FREE=$(free_gb); USED=$(used_gb)
[ -n "${FREE:-}" ] || { say "ERROR 无法读取磁盘信息"; exit 0; }

# ---- 1) 日志上限兜底 ----
for item in "${WATCH_FILES[@]}"; do
  IFS=':' read -r p max keep <<<"$item"
  cap_file "$p" "$max" "$keep"
done

for item in "${WATCH_DIRS[@]}"; do
  IFS=':' read -r d max keep <<<"$item"
  [ -d "$d" ] || continue
  while IFS= read -r f; do
    cap_file "$f" "$max" "$keep"
  done < <(find "$d" -maxdepth 1 -name '*.log' -type f 2>/dev/null)
done

# ---- 2) 磁盘水位 ----
LEVEL="ok"
if   [ "$FREE" -lt "$CRIT_FREE_GB" ]; then LEVEL="critical"
elif [ "$FREE" -lt "$WARN_FREE_GB" ]; then LEVEL="warn"
fi

LAST=$(cat "$LOWMARK" 2>/dev/null || echo "ok")
if [ "$LEVEL" != "ok" ] && [ "$LEVEL" != "$LAST" ]; then
  say "DISK $LEVEL: 可用 ${FREE}GiB / 已用 ${USED}GiB"
  if [ "$LEVEL" = "critical" ]; then
    notify "DSH 磁盘告急" "系统盘仅剩 ${FREE}GB，已保留日志尾部并压缩大日志，请尽快清理"
    # 应急：把监视中的日志压到最小，优先保住可用空间
    for item in "${WATCH_FILES[@]}"; do
      IFS=':' read -r p _ _ <<<"$item"
      cap_file "$p" 1048576 262144
    done
    printf '%s  可用=%sGiB  已用=%sGiB\n' "$(ts)" "$FREE" "$USED" > "$ALERT"
  else
    notify "DSH 磁盘偏低" "系统盘可用 ${FREE}GB（低于 ${WARN_FREE_GB}GB 警戒线）"
  fi
fi
echo "$LEVEL" > "$LOWMARK"

# ---- 3) 历史留痕（每次一条，周期性体积曲线） ----
N8N_LOG_MIB=$(( $(stat -f %z /Users/jason/n8n/n8n.log 2>/dev/null || echo 0) / 1048576 ))
LOGDIR_MIB=$(( $(du -sk "$LOGDIR" 2>/dev/null | awk '{print $1}') / 1024 ))
if [ ! -f "$HIST" ]; then
  printf 'timestamp\tfree_GiB\tused_GiB\tn8n_log_MiB\thermes_logs_MiB\n' > "$HIST"
fi
printf '%s\t%s\t%s\t%s\t%s\n' "$(ts)" "$FREE" "$USED" "$N8N_LOG_MIB" "$LOGDIR_MIB" >> "$HIST"

# 历史文件自身限长（最多 5000 行）
if [ "$(wc -l < "$HIST" 2>/dev/null || echo 0)" -gt 5000 ]; then
  tail -n 3000 "$HIST" > "$HIST.tmp" && cat "$HIST.tmp" > "$HIST" && rm -f "$HIST.tmp"
fi

# ---- 4) n8n 数据一致性自检（只读）----
# 背景：2026-09-25 事故 = 坏 workflow 数量达到 n8n 的 indexingBatchSize(=10)，
# 使 WorkflowIndex 的 while (processedCount < 1e9) 永远无法 break → 2 天刷出 72GB 日志。
# 这里做同类故障的早期预警：坏版本数 >= 9 就是差 1 个触雷。
N8N_DB="/Users/jason/.n8n/database.sqlite"
N8N_LOWMARK="$LOGDIR/.n8n-integrity-last-level"
N8N_HIST="$LOGDIR/n8n-integrity-history.tsv"

if [ -f "$N8N_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
  BADVER=$(sqlite3 "file:$N8N_DB?mode=ro" \
    "SELECT COUNT(*) FROM workflow_entity w WHERE w.activeVersionId IS NOT NULL AND NOT EXISTS (SELECT 1 FROM workflow_history h WHERE h.versionId=w.activeVersionId);" 2>/dev/null)
  NOOWN=$(sqlite3 "file:$N8N_DB?mode=ro" \
    "SELECT COUNT(*) FROM workflow_entity w WHERE w.active=1 AND NOT EXISTS (SELECT 1 FROM shared_workflow s WHERE s.workflowId=w.id);" 2>/dev/null)
  BADVER=${BADVER:-0}; NOOWN=${NOOWN:-0}

  NLEVEL="ok"
  if   [ "$BADVER" -ge 9 ]; then NLEVEL="critical"
  elif [ "$BADVER" -ge 1 ] || [ "$NOOWN" -ge 1 ]; then NLEVEL="warn"
  fi

  NLAST=$(cat "$N8N_LOWMARK" 2>/dev/null || echo "ok")
  if [ "$NLEVEL" != "ok" ] && [ "$NLEVEL" != "$NLAST" ]; then
    say "N8N $NLEVEL: 缺版本记录 ${BADVER} 个 / 活跃但缺归属 ${NOOWN} 个"
    if [ "$NLEVEL" = "critical" ]; then
      notify "n8n 数据异常（接近死循环阈值）" "缺版本记录 ${BADVER} 个（>=9），再加 1 个就会重现 72GB 日志刷屏，请立即修复"
    else
      notify "n8n 数据异常" "缺版本记录 ${BADVER} 个 / 缺归属 ${NOOWN} 个（会导致激活失败），建议修复"
    fi
  fi
  echo "$NLEVEL" > "$N8N_LOWMARK"

  [ -f "$N8N_HIST" ] || printf 'timestamp\tbad_version\tno_owner\tlevel\n' > "$N8N_HIST"
  printf '%s\t%s\t%s\t%s\n' "$(ts)" "$BADVER" "$NOOWN" "$NLEVEL" >> "$N8N_HIST"
  if [ "$(wc -l < "$N8N_HIST" 2>/dev/null || echo 0)" -gt 5000 ]; then
    tail -n 3000 "$N8N_HIST" > "$N8N_HIST.tmp" && cat "$N8N_HIST.tmp" > "$N8N_HIST" && rm -f "$N8N_HIST.tmp"
  fi
fi

# ---- 5) 航拍屏保/壁纸素材监控（macOS 会自动下载 ~14GB）----
# 背景：2026-09-27 这些素材被自动下载了 14GB（32 个 4K .mov），已清空。
# 只要有人在 系统设置→墙纸 里碰到航拍分类，macOS 就会重新下载，这里做回归预警。
AERIAL_DIR="/Library/Application Support/com.apple.idleassetsd"
AERIAL_LOWMARK="$LOGDIR/.aerial-last-level"

if [ -d "$AERIAL_DIR" ]; then
  AERIAL_MB=$(( $(du -sk "$AERIAL_DIR" 2>/dev/null | awk '{print $1}') / 1024 ))
  ALEVEL="ok"
  [ "$AERIAL_MB" -ge 2048 ] && ALEVEL="warn"
  [ "$AERIAL_MB" -ge 8192 ] && ALEVEL="critical"
  ALAST=$(cat "$AERIAL_LOWMARK" 2>/dev/null || echo "ok")
  if [ "$ALEVEL" != "ok" ] && [ "$ALEVEL" != "$ALAST" ]; then
    say "AERIAL $ALEVEL: 航拍素材又涨到 ${AERIAL_MB}MiB（2026-09-27 曾清空 14GB）"
    notify "航拍素材又被下载了" "当前 ${AERIAL_MB}MB。不需要的话请勿在 系统设置→墙纸 里选择航拍分类；清理命令已记在 guard 脚本注释里"
  fi
  echo "$ALEVEL" > "$AERIAL_LOWMARK"
fi

# ---- 6) 官方桌面版架构自检（防 2026-09-26 那种"自动更新装错架构"）----
# 背景：codex/ChatGPT 桌面版同版本号有 arm64 与 x64 两种构建，自动更新在 Intel 机器上
# 拿到 arm64 版后 app 直接打不开（"incorrect executable format"），启动台也会不再收录它。
# 上游 bug: openai/codex#11467 / #12941
APP_DIR="/Applications/ChatGPT.app"
APP_BIN="$APP_DIR/Contents/MacOS/ChatGPT"
ARCH_LOWMARK="$LOGDIR/.app-arch-last-level"

if [ -f "$APP_BIN" ]; then
  HOST_ARCH="$(uname -m)"
  APP_ARCHS="$(lipo -archs "$APP_BIN" 2>/dev/null || echo unknown)"
  # 只有 Intel 机器会因为拿到 arm64-only 构建而彻底打不开；
  # Apple Silicon 机器可以用 Rosetta 跑 x86_64，故不做告警（避免误报）。
  MISMATCH=0
  if [ "$HOST_ARCH" = "x86_64" ] && ! echo "$APP_ARCHS" | tr ' ' '\n' | grep -qx "x86_64"; then
    MISMATCH=1
  fi
  if [ "$MISMATCH" = "1" ]; then
    if [ "$(cat "$ARCH_LOWMARK" 2>/dev/null || echo ok)" != "mismatch" ]; then
      say "APP-ARCH mismatch: app=${APP_ARCHS} host=${HOST_ARCH}（桌面版会打不开）"
      notify "Codex 桌面版又被装成 ${APP_ARCHS} 了" "本机是 ${HOST_ARCH}，app 打不开。重装: curl -L -o /tmp/c.dmg https://persistent.oaistatic.com/codex-app-prod/Codex-latest-x64.dmg && hdiutil attach /tmp/c.dmg && ditto '/Volumes/ChatGPT Installer/ChatGPT.app' /Applications/ChatGPT.app && hdiutil detach '/Volumes/ChatGPT Installer'"
    fi
    echo "mismatch" > "$ARCH_LOWMARK"
  else
    echo "ok" > "$ARCH_LOWMARK"
  fi
fi

exit 0
