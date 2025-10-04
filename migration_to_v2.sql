-- ==========================================
-- 数据迁移脚本：迁移到简化架构（移除不需要的字段）
-- ==========================================
-- 执行前请备份原数据库！

USE `email_bot`;

-- ==========================================
-- 第一步：备份现有数据
-- ==========================================
CREATE TABLE IF NOT EXISTS `emails_backup` AS SELECT * FROM `emails`;
CREATE TABLE IF NOT EXISTS `chat_logs_backup` AS SELECT * FROM `chat_logs`;

-- ==========================================
-- 第二步：创建发件人映射（简化版）
-- ==========================================

-- 为每个unique的from_email创建sender记录（只保留核心字段）
INSERT IGNORE INTO `email_senders` (
    `sender_uuid`,
    `email_address`,
    `total_emails_sent`,
    `last_email_at`
)
SELECT 
    UUID() as `sender_uuid`,
    eb.`from_email` as `email_address`,
    COUNT(*) as `total_emails_sent`,
    MAX(eb.`received_at`) as `last_email_at`
FROM `emails_backup` eb
GROUP BY eb.`from_email`;

-- ==========================================
-- 第三步：迁移邮件数据到新表
-- ==========================================

-- 先清空emails表（如果存在）
TRUNCATE TABLE `emails`;

-- 迁移邮件数据，合并附件和AI信息
INSERT INTO `emails` (
    `uuid`,
    `message_id`,
    `sender_uuid`,
    `from_email`,
    `to_email`,
    `subject`,
    `content`,
    `content_hash`,
    `has_attachment`,
    `attachment_count`,
    `attachment_info`,
    `total_attachment_size`,
    `received_at`,
    `is_processed`,
    `retry_count`,
    `last_error`,
    `ai_user_text`,
    `ai_rag_docs`,
    `ai_response_text`,
    `ai_prompt`,
    `ai_completion_id`,
    `ai_prompt_tokens`,
    `ai_completion_tokens`,
    `ai_total_tokens`,
    `ai_processing_time_ms`,
    `ai_model`,
    `ai_processed_at`,
    `response_sent_at`,
    `response_status`,
    `created_at`,
    `updated_at`
)
SELECT 
    COALESCE(eb.`email_uuid`, UUID()) as `uuid`,
    eb.`message_id`,
    s.`sender_uuid`,
    eb.`from_email`,
    eb.`to_email`,
    -- 从content中提取subject（假设第一行是subject）
    SUBSTRING_INDEX(eb.`content`, '\n', 1) as `subject`,
    eb.`content`,
    SHA2(eb.`content`, 256) as `content_hash`,
    COALESCE(eb.`has_attachment`, 0),
    COALESCE(eb.`has_attachment`, 0) as `attachment_count`,
    -- 如果有附件，创建基本的JSON结构
    CASE 
        WHEN COALESCE(eb.`has_attachment`, 0) = 1 
        THEN JSON_ARRAY(JSON_OBJECT('filename', 'unknown', 'url', '', 'size', 0, 'type', 'unknown'))
        ELSE NULL 
    END as `attachment_info`,
    0 as `total_attachment_size`,
    COALESCE(eb.`received_at`, eb.`created_at`, NOW()),
    COALESCE(eb.`is_processed`, 0),
    0 as `retry_count`,
    NULL as `last_error`,
    -- 从chat_logs合并AI信息
    clb.`user_text` as `ai_user_text`,
    clb.`rag_docs` as `ai_rag_docs`,
    COALESCE(eb.`response`, clb.`response_text`) as `ai_response_text`,
    clb.`prompt` as `ai_prompt`,
    clb.`completion_id` as `ai_completion_id`,
    COALESCE(clb.`prompt_tokens`, 0) as `ai_prompt_tokens`,
    COALESCE(clb.`completion_tokens`, 0) as `ai_completion_tokens`,
    COALESCE(clb.`total_tokens`, 0) as `ai_total_tokens`,
    NULL as `ai_processing_time_ms`,
    NULL as `ai_model`,
    clb.`created_at` as `ai_processed_at`,
    eb.`response_time` as `response_sent_at`,
    CASE 
        WHEN eb.`response` IS NOT NULL AND eb.`response` != '' THEN 1 
        ELSE 0 
    END as `response_status`,
    COALESCE(eb.`created_at`, NOW()),
    COALESCE(eb.`updated_at`, NOW())
FROM `emails_backup` eb
LEFT JOIN `email_senders` s ON eb.`from_email` = s.`email_address`
LEFT JOIN `chat_logs_backup` clb ON eb.`message_id` = clb.`message_id`
WHERE s.`sender_uuid` IS NOT NULL;

-- ==========================================
-- 第四步：更新发件人统计信息
-- ==========================================

-- 更新发件人的邮件统计
UPDATE `email_senders` s
SET 
    `total_emails_sent` = (
        SELECT COUNT(*) 
        FROM `emails` e 
        WHERE e.`sender_uuid` = s.`sender_uuid`
    ),
    `total_emails_replied` = (
        SELECT COUNT(*) 
        FROM `emails` e 
        WHERE e.`sender_uuid` = s.`sender_uuid` 
        AND e.`response_status` = 1
    ),
    `last_email_at` = (
        SELECT MAX(e.`received_at`) 
        FROM `emails` e 
        WHERE e.`sender_uuid` = s.`sender_uuid`
    ),
    `last_reply_at` = (
        SELECT MAX(e.`response_sent_at`) 
        FROM `emails` e 
        WHERE e.`sender_uuid` = s.`sender_uuid` 
        AND e.`response_status` = 1
    );

-- ==========================================
-- 第五步：验证迁移结果
-- ==========================================

-- 检查邮件数据迁移结果
SELECT 
    'emails' as table_name,
    (SELECT COUNT(*) FROM `emails_backup`) as original_count,
    (SELECT COUNT(*) FROM `emails`) as migrated_count,
    CASE 
        WHEN (SELECT COUNT(*) FROM `emails_backup`) = (SELECT COUNT(*) FROM `emails`) 
        THEN '✅ 完全迁移' 
        ELSE '⚠️ 数据数量不匹配，请检查' 
    END as status
UNION ALL
SELECT 
    'senders' as table_name,
    (SELECT COUNT(DISTINCT from_email) FROM `emails_backup`) as original_count,
    (SELECT COUNT(*) FROM `email_senders`) as migrated_count,
    CASE 
        WHEN (SELECT COUNT(DISTINCT from_email) FROM `emails_backup`) = (SELECT COUNT(*) FROM `email_senders`) 
        THEN '✅ 发件人映射完成' 
        ELSE '⚠️ 发件人映射不匹配' 
    END as status;

-- 显示发件人统计前10名
SELECT 
    s.`email_address`,
    s.`total_emails_sent`,
    s.`total_emails_replied`,
    s.`last_email_at`
FROM `email_senders` s
ORDER BY s.`total_emails_sent` DESC
LIMIT 10;

-- ==========================================
-- 显示迁移完成信息
-- ==========================================
SELECT 
    '🎉 简化架构迁移完成' as message,
    NOW() as completed_at,
    '已移除display_name、domain、白名单/黑名单字段' as note;