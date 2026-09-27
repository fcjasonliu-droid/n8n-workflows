-- ============================================================================
-- n8n 发布索引死循环 / 激活静默失败 修复脚本
-- 事故复盘见同目录 README.md（2026-09-27，n8n 2.38.5 实测）
--
-- 用法：
--   sqlite3 ~/.n8n/database.sqlite < fix-publish-index-loop.sql
--   launchctl kickstart -k gui/$(id -u)/ai.n8n     # 必须重启，激活与缓存都发生在启动时
--   bash n8n-integrity-check.sh                    # 回查，三条死亡条件必须全为 0
--
-- 说明：本脚本只补"缺失的行"，不修改 active / id / 触发器，也不删除任何内容；
--       执行前请先备份：sqlite3 ~/.n8n/database.sqlite ".backup '<路径>'"
-- ============================================================================

BEGIN IMMEDIATE;

-- ---------------------------------------------------------------------------
-- A. 补缺失的版本记录
--    条件：workflow_entity.activeVersionId 指向的版本在 workflow_history 里不存在
--    内容：nodes / connections / name 直接取自 workflow_entity（即当前实际定义）
--    这是死循环的直接触发源（坏数 ≥ indexingBatchSize(10)），也是激活失败的原因。
-- ---------------------------------------------------------------------------
INSERT INTO workflow_history
  (versionId, workflowId, authors, createdAt, updatedAt, nodes, connections, name, autosaved, description, nodeGroups)
SELECT w.activeVersionId,
       w.id,
       'Jason Liu',
       COALESCE(w.updatedAt, strftime('%Y-%m-%d %H:%M:%f','now')),
       COALESCE(w.updatedAt, strftime('%Y-%m-%d %H:%M:%f','now')),
       w.nodes,
       w.connections,
       w.name,
       0,
       COALESCE(w.description, ''),
       COALESCE(w.nodeGroups, '[]')
FROM workflow_entity w
WHERE w.activeVersionId IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM workflow_history h WHERE h.versionId = w.activeVersionId);

-- ---------------------------------------------------------------------------
-- B. 补缺失的项目归属
--    条件：workflow_entity 里没有任何 shared_workflow 行
--    漏这行 → 启动激活静默失败（Could not find any entity of type "SharedWorkflow"）、
--             触发器永不注册、execution_entity 永远 0 行，且 UI/公共 API 看不到该 workflow。
--    role 必须是 workflow:owner；projectId 用该实例的 personal project id。
-- ---------------------------------------------------------------------------
INSERT INTO shared_workflow (workflowId, projectId, role, createdAt, updatedAt)
SELECT w.id,
       'R1H6r96y3pxpx0lc',           -- ← 改成你实例的 projectId（见下方查询）
       'workflow:owner',
       strftime('%Y-%m-%d %H:%M:%f','now'),
       strftime('%Y-%m-%d %H:%M:%f','now')
FROM workflow_entity w
WHERE NOT EXISTS (SELECT 1 FROM shared_workflow s WHERE s.workflowId = w.id);

COMMIT;

-- ============================================================================
-- 验收（三条死亡条件必须全为 0）
-- ============================================================================

-- A 坏版本：≥10 会触发死循环；1..9 会让对应 workflow 激活失败
SELECT 'A 坏版本' AS 条件, COUNT(*) AS 数量
FROM workflow_entity w
WHERE w.activeVersionId IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM workflow_history h WHERE h.versionId = w.activeVersionId);

-- B 缺归属：>0 表示有 workflow 的定时任务根本没在跑
SELECT 'B 缺归属(active)' AS 条件, COUNT(*) AS 数量
FROM workflow_entity w
WHERE w.active = 1
  AND NOT EXISTS (SELECT 1 FROM shared_workflow s WHERE s.workflowId = w.id);

-- C 缺发布行：只影响 publish/unpublish 语义，不影响调度
SELECT 'C 缺发布行' AS 条件, COUNT(*) AS 数量
FROM workflow_entity w
WHERE w.activeVersionId IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM workflow_published_version p WHERE p.workflowId = w.id);

-- 辅助：本实例可用的 projectId / personal project
-- SELECT id, name, type FROM project;

-- 辅助：逐条列出仍未健康的 workflow（便于人工确认）
-- SELECT w.id, w.name, w.active,
--        (SELECT COUNT(*) FROM workflow_history h WHERE h.versionId = w.activeVersionId) AS ver_ok,
--        (SELECT COUNT(*) FROM shared_workflow s WHERE s.workflowId = w.id)                AS owner_ok
-- FROM workflow_entity w
-- WHERE ver_ok = 0 OR owner_ok = 0;
