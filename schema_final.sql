-- ==========================================
-- Email Bot 最终数据库架构
-- 匹配更新后的Python代码
-- ==========================================

CREATE DATABASE IF NOT EXISTS `email_bot` 
DEFAULT CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE `email_bot`;

-- ==========================================
-- 发件人映射表
-- ==========================================
CREATE TABLE `email_senders` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `sender_uuid` CHAR(36) NOT NULL COMMENT '发件人UUID',
  `email_address` VARCHAR(255) NOT NULL COMMENT '发件人邮箱',
  `total_emails_sent` INT NOT NULL DEFAULT 0 COMMENT '发送总数',
  `total_emails_replied` INT NOT NULL DEFAULT 0 COMMENT '回复总数',
  `last_email_at` TIMESTAMP NULL COMMENT '最后邮件时间',
  `last_reply_at` TIMESTAMP NULL COMMENT '最后回复时间',
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  
  PRIMARY KEY (`id`),
  UNIQUE KEY `idx_sender_uuid` (`sender_uuid`),
  UNIQUE KEY `idx_email_address` (`email_address`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ==========================================
-- 邮件主表（包含所有信息）
-- ==========================================
CREATE TABLE `emails` (
  -- 主键和标识
  `uuid` CHAR(36) NOT NULL COMMENT '邮件UUID主键',
  `message_id` VARCHAR(500) NULL COMMENT '原始Message-ID',
  
  -- 关联和基本信息
  `sender_uuid` CHAR(36) NOT NULL COMMENT '发件人UUID',
  `from_email` VARCHAR(255) NOT NULL COMMENT '发件人邮箱',
  `to_email` VARCHAR(255) NOT NULL COMMENT '收件人邮箱',
  `subject` VARCHAR(500) NULL COMMENT '邮件主题',
  `content` LONGTEXT NOT NULL COMMENT '邮件正文',
  `content_hash` VARCHAR(64) NULL COMMENT '内容哈希',
  
  -- 附件信息
  `has_attachment` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否有附件',
  `attachment_count` INT NOT NULL DEFAULT 0 COMMENT '附件数量',
  `attachment_info` JSON NULL COMMENT '附件信息JSON格式',
  `total_attachment_size` BIGINT NOT NULL DEFAULT 0 COMMENT '附件总大小',
  
  -- 邮件处理状态
  `received_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '接收时间',
  `is_processed` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '处理状态',
  `retry_count` INT NOT NULL DEFAULT 0 COMMENT '重试次数',
  `last_error` TEXT NULL COMMENT '最后错误',
  
  -- AI处理信息
  `ai_user_text` LONGTEXT NULL COMMENT 'AI用户文本',
  `ai_rag_docs` LONGTEXT NULL COMMENT 'RAG文档',
  `ai_response_text` LONGTEXT NULL COMMENT 'AI回复',
  `ai_prompt` LONGTEXT NULL COMMENT 'AI提示词',
  `ai_completion_id` VARCHAR(100) NULL COMMENT 'AI完成ID',
  `ai_prompt_tokens` INT NOT NULL DEFAULT 0 COMMENT '提示词tokens',
  `ai_completion_tokens` INT NOT NULL DEFAULT 0 COMMENT '完成tokens',
  `ai_total_tokens` INT NOT NULL DEFAULT 0 COMMENT '总tokens',
  `ai_processing_time_ms` INT NULL COMMENT 'AI处理时间',
  `ai_model` VARCHAR(100) NULL COMMENT 'AI模型',
  `ai_processed_at` TIMESTAMP NULL COMMENT 'AI处理时间',
  
  -- 回复状态
  `response_sent_at` TIMESTAMP NULL COMMENT '回复发送时间',
  `response_status` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '回复状态',
  
  -- 时间戳
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  
  -- 索引
  PRIMARY KEY (`uuid`),
  UNIQUE KEY `idx_message_id` (`message_id`),
  KEY `idx_sender_uuid` (`sender_uuid`),
  KEY `idx_from_email` (`from_email`),
  KEY `idx_to_email` (`to_email`),
  KEY `idx_is_processed` (`is_processed`),
  KEY `idx_received_at` (`received_at`),
  KEY `idx_content_hash` (`content_hash`),
  
  -- 外键约束
  CONSTRAINT `fk_emails_sender` FOREIGN KEY (`sender_uuid`) REFERENCES `email_senders` (`sender_uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ==========================================
-- 示例数据插入
-- ==========================================
INSERT INTO `email_senders` (
    `sender_uuid`, `email_address`
) VALUES (
    UUID(), 
    'test@example.com'
);

-- ==========================================
-- 常用查询示例
-- ==========================================

-- 查看未处理的邮件
-- SELECT uuid, from_email, subject, has_attachment, received_at 
-- FROM emails 
-- WHERE is_processed = 0 
-- ORDER BY received_at ASC;

-- 查看带附件的邮件
-- SELECT uuid, from_email, subject, attachment_count, total_attachment_size, attachment_info
-- FROM emails 
-- WHERE has_attachment = 1;

-- 查看发件人统计
-- SELECT email_address, total_emails_sent, total_emails_replied, last_email_at
-- FROM email_senders 
-- ORDER BY total_emails_sent DESC;

-- 查看AI处理统计
-- SELECT COUNT(*) as processed_count, 
--        AVG(ai_total_tokens) as avg_tokens,
--        AVG(ai_processing_time_ms) as avg_time_ms
-- FROM emails 
-- WHERE ai_processed_at IS NOT NULL;