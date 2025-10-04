-- ==========================================
-- Email Bot 简化数据库架构 v2.1
-- ==========================================
-- 设计说明：
-- 1. 只保留emails表，合并附件和AI日志信息
-- 2. 使用UUID作为邮件的唯一标识符  
-- 3. 根据from_email建立发件人映射表（简化版）
-- 4. 所有信息集中在一个表中，简化查询和维护
-- ==========================================

-- 创建数据库
CREATE DATABASE IF NOT EXISTS `email_bot` 
DEFAULT CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE `email_bot`;

-- ==========================================
-- 发件人映射表 (email_senders) - 简化版
-- ==========================================
CREATE TABLE `email_senders` (
  `id` INT NOT NULL AUTO_INCREMENT COMMENT '发件人ID',
  `sender_uuid` CHAR(36) NOT NULL COMMENT '发件人UUID',
  `email_address` VARCHAR(255) NOT NULL COMMENT '发件人邮箱地址',
  `total_emails_sent` INT NOT NULL DEFAULT 0 COMMENT '发送邮件总数',
  `total_emails_replied` INT NOT NULL DEFAULT 0 COMMENT '已回复邮件总数',
  `last_email_at` TIMESTAMP NULL DEFAULT NULL COMMENT '最后发邮件时间',
  `last_reply_at` TIMESTAMP NULL DEFAULT NULL COMMENT '最后回复时间',
  `notes` TEXT NULL DEFAULT NULL COMMENT '备注信息',
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  
  -- 主键和索引
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_sender_uuid` (`sender_uuid`),
  UNIQUE KEY `idx_email_address` (`email_address`),
  KEY `idx_last_email_at` (`last_email_at`)
) ENGINE=InnoDB 
DEFAULT CHARSET=utf8mb4 
COLLATE=utf8mb4_unicode_ci 
COMMENT='发件人映射和统计表';

-- ==========================================
-- 主表：邮件表 (emails) - 包含所有信息
-- ==========================================
CREATE TABLE `emails` (
  `uuid` CHAR(36) NOT NULL COMMENT '邮件UUID主键',
  `message_id` VARCHAR(500) NULL DEFAULT NULL COMMENT '原始邮件Message-ID',
  `sender_uuid` CHAR(36) NOT NULL COMMENT '发件人UUID（关联email_senders表）',
  `from_email` VARCHAR(255) NOT NULL COMMENT '发件人邮箱地址',
  `to_email` VARCHAR(255) NOT NULL COMMENT '收件人邮箱地址',
  `subject` VARCHAR(500) NULL DEFAULT NULL COMMENT '邮件主题',
  `content` LONGTEXT NOT NULL COMMENT '邮件正文内容',
  `content_hash` VARCHAR(64) NULL DEFAULT NULL COMMENT '内容SHA256哈希值（用于去重）',
  
  -- === 附件相关字段（合并自email_attachments表） ===
  `has_attachment` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否包含附件',
  `attachment_count` INT NOT NULL DEFAULT 0 COMMENT '附件数量',
  `attachment_info` JSON NULL DEFAULT NULL COMMENT '附件信息JSON格式: [{filename, url, size, type, hash}]',
  `total_attachment_size` BIGINT NOT NULL DEFAULT 0 COMMENT '附件总大小（字节）',
  
  -- === 邮件处理状态 ===
  `received_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '邮件接收时间',
  `is_processed` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '处理状态 (0=未处理, 1=已处理, 2=处理中, 3=失败)',
  `retry_count` INT NOT NULL DEFAULT 0 COMMENT '重试次数',
  `last_error` TEXT NULL DEFAULT NULL COMMENT '最后一次错误信息',
  
  -- === AI处理相关字段（合并自ai_chat_logs表） ===
  `ai_user_text` LONGTEXT NULL DEFAULT NULL COMMENT 'AI处理的用户文本',
  `ai_rag_docs` LONGTEXT NULL DEFAULT NULL COMMENT 'RAG检索到的相关文档',
  `ai_response_text` LONGTEXT NULL DEFAULT NULL COMMENT 'AI生成的回复文本',
  `ai_prompt` LONGTEXT NULL DEFAULT NULL COMMENT '发送给AI的完整提示词',
  `ai_completion_id` VARCHAR(100) NULL DEFAULT NULL COMMENT 'AI API返回的完成ID',
  `ai_prompt_tokens` INT NOT NULL DEFAULT 0 COMMENT '提示词消耗的token数量',
  `ai_completion_tokens` INT NOT NULL DEFAULT 0 COMMENT '完成回复消耗的token数量',
  `ai_total_tokens` INT NOT NULL DEFAULT 0 COMMENT '总消耗token数量',
  `ai_processing_time_ms` INT NULL DEFAULT NULL COMMENT 'AI处理时间（毫秒）',
  `ai_model` VARCHAR(100) NULL DEFAULT NULL COMMENT '使用的AI模型名称',
  `ai_processed_at` TIMESTAMP NULL DEFAULT NULL COMMENT 'AI处理完成时间',
  
  -- === 回复状态 ===
  `response_sent_at` TIMESTAMP NULL DEFAULT NULL COMMENT '回复发送时间',
  `response_status` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '回复状态 (0=未发送, 1=已发送, 2=发送失败)',
  
  -- === 时间戳 ===
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
  
  -- 主键和索引
  PRIMARY KEY (`uuid`),
  UNIQUE KEY `idx_message_id` (`message_id`),
  KEY `idx_sender_uuid` (`sender_uuid`),
  KEY `idx_from_email` (`from_email`),
  KEY `idx_to_email` (`to_email`),
  KEY `idx_is_processed` (`is_processed`),
  KEY `idx_received_at` (`received_at`),
  KEY `idx_content_hash` (`content_hash`),
  KEY `idx_has_attachment` (`has_attachment`),
  KEY `idx_response_sent_at` (`response_sent_at`),
  KEY `idx_ai_completion_id` (`ai_completion_id`),
  KEY `idx_response_status` (`response_status`),
  
  -- 外键约束
  CONSTRAINT `fk_emails_sender` 
    FOREIGN KEY (`sender_uuid`) 
    REFERENCES `email_senders` (`sender_uuid`) 
    ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB 
DEFAULT CHARSET=utf8mb4 
COLLATE=utf8mb4_unicode_ci 
COMMENT='邮件主表 - 包含所有邮件、附件、AI处理信息';

-- ==========================================
-- 创建视图：未处理邮件视图
-- ==========================================
CREATE VIEW `v_pending_emails` AS
SELECT 
    e.`uuid`,
    e.`from_email`,
    e.`to_email`,
    e.`subject`,
    e.`has_attachment`,
    e.`attachment_count`,
    e.`received_at`,
    e.`retry_count`,
    e.`last_error`,
    s.`total_emails_sent`,
    s.`total_emails_replied`
FROM `emails` e
LEFT JOIN `email_senders` s ON e.`sender_uuid` = s.`sender_uuid`
WHERE e.`is_processed` = 0
ORDER BY e.`received_at` ASC;

-- ==========================================
-- 创建视图：发件人统计视图
-- ==========================================
CREATE VIEW `v_sender_stats` AS
SELECT 
    s.`sender_uuid`,
    s.`email_address`,
    s.`total_emails_sent`,
    s.`total_emails_replied`,
    COUNT(e.`uuid`) as `total_emails_received`,
    SUM(CASE WHEN e.`is_processed` = 1 THEN 1 ELSE 0 END) as `emails_processed`,
    SUM(CASE WHEN e.`response_status` = 1 THEN 1 ELSE 0 END) as `emails_replied`,
    SUM(e.`has_attachment`) as `emails_with_attachments`,
    AVG(e.`ai_processing_time_ms`) as `avg_ai_processing_time`,
    SUM(e.`ai_total_tokens`) as `total_tokens_used`,
    MAX(e.`received_at`) as `last_email_received_at`,
    MAX(e.`response_sent_at`) as `last_response_sent_at`
FROM `email_senders` s
LEFT JOIN `emails` e ON s.`sender_uuid` = e.`sender_uuid`
GROUP BY s.`sender_uuid`, s.`email_address`, s.`total_emails_sent`, s.`total_emails_replied`
ORDER BY `total_emails_received` DESC;

-- ==========================================
-- 创建触发器：自动维护发件人统计
-- ==========================================
DELIMITER $$

-- 插入邮件时自动创建或更新发件人记录
CREATE TRIGGER `tr_emails_insert_update_sender` 
AFTER INSERT ON `emails`
FOR EACH ROW
BEGIN
    DECLARE sender_exists INT DEFAULT 0;
    
    -- 检查发件人是否存在
    SELECT COUNT(*) INTO sender_exists 
    FROM `email_senders` 
    WHERE `email_address` = NEW.`from_email`;
    
    IF sender_exists = 0 THEN
        -- 创建新的发件人记录
        INSERT INTO `email_senders` (
            `sender_uuid`, `email_address`, 
            `total_emails_sent`, `last_email_at`
        ) VALUES (
            NEW.`sender_uuid`,
            NEW.`from_email`,
            1,
            NEW.`received_at`
        );
    ELSE
        -- 更新现有发件人记录
        UPDATE `email_senders` 
        SET 
            `total_emails_sent` = `total_emails_sent` + 1,
            `last_email_at` = NEW.`received_at`,
            `updated_at` = CURRENT_TIMESTAMP
        WHERE `email_address` = NEW.`from_email`;
    END IF;
END$$

-- 更新邮件回复状态时同步发件人统计
CREATE TRIGGER `tr_emails_update_reply_stats` 
AFTER UPDATE ON `emails`
FOR EACH ROW
BEGIN
    IF OLD.`response_status` != NEW.`response_status` AND NEW.`response_status` = 1 THEN
        -- 邮件被成功回复时更新发件人统计
        UPDATE `email_senders` 
        SET 
            `total_emails_replied` = `total_emails_replied` + 1,
            `last_reply_at` = NEW.`response_sent_at`,
            `updated_at` = CURRENT_TIMESTAMP
        WHERE `sender_uuid` = NEW.`sender_uuid`;
    END IF;
END$$

DELIMITER ;

-- ==========================================
-- 存储过程：清理过期数据
-- ==========================================
DELIMITER $$
CREATE PROCEDURE `CleanupOldEmails`(IN days_to_keep INT)
BEGIN
    DECLARE cleanup_date DATE DEFAULT DATE_SUB(CURDATE(), INTERVAL days_to_keep DAY);
    
    -- 删除指定天数前已处理的邮件
    DELETE FROM `emails` 
    WHERE `is_processed` = 1 
    AND DATE(`created_at`) < cleanup_date;
    
    -- 清理没有邮件记录的发件人
    DELETE FROM `email_senders` 
    WHERE `sender_uuid` NOT IN (
        SELECT DISTINCT `sender_uuid` FROM `emails`
    );
    
    SELECT CONCAT('Cleanup completed at ', NOW(), ', removed emails older than ', cleanup_date) as result;
END$$
DELIMITER ;

-- ==========================================
-- 存储过程：获取发件人邮件统计
-- ==========================================
DELIMITER $$
CREATE PROCEDURE `GetSenderEmailStats`(IN sender_email VARCHAR(255))
BEGIN
    SELECT 
        s.*,
        COUNT(e.`uuid`) as `total_emails`,
        SUM(CASE WHEN e.`is_processed` = 1 THEN 1 ELSE 0 END) as `processed_emails`,
        SUM(CASE WHEN e.`response_status` = 1 THEN 1 ELSE 0 END) as `replied_emails`,
        SUM(e.`has_attachment`) as `emails_with_attachments`,
        AVG(e.`ai_total_tokens`) as `avg_tokens_per_email`,
        MAX(e.`received_at`) as `latest_email_received`
    FROM `email_senders` s
    LEFT JOIN `emails` e ON s.`sender_uuid` = e.`sender_uuid`
    WHERE s.`email_address` = sender_email
    GROUP BY s.`id`;
END$$
DELIMITER ;

-- ==========================================
-- 示例数据插入
-- ==========================================
-- 插入示例发件人
INSERT INTO `email_senders` (
    `sender_uuid`, `email_address`
) VALUES (
    UUID(), 
    'test@example.com'
);

-- ==========================================
-- 性能优化建议
-- ==========================================
-- 1. 定期执行 CALL CleanupOldEmails(30) 清理30天前的数据
-- 2. 监控 attachment_info JSON字段的查询性能
-- 3. 考虑对高频发件人添加专门的缓存机制
-- 4. 定期分析慢查询日志优化索引