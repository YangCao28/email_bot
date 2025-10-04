"""
Email Auto-Reply Service 
------------------------
* Simple email processing without rate limiting
* Redis queue processing with auto-expiring reply tracking
* MySQL logging and AI reply integration

Author: Updated 2025-10-01
"""

from __future__ import annotations

import os
import re
import time
import uuid
import redis
import pymysql
import smtplib
import socket
import email.utils
import logging
import requests
import traceback
from dotenv import load_dotenv
from email.mime.text import MIMEText
from requests.adapters import HTTPAdapter, Retry

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== 全局配置 & 读取 .env ==========
MAX_RETRIES = 3
RETRY_INTERVAL = 5  # seconds between retries

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(basedir, '.env')
load_dotenv(dotenv_path)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB   = int(os.getenv("REDIS_DB", 0))
REDIS_QUEUE = os.getenv("REDIS_QUEUE", "email_task_queue")
REPLIED_EMAILS_SET = 'replied_emails_set'  # Redis Set to track replied emails
REPLIED_TTL_DAYS = int(os.getenv("REPLIED_TTL_DAYS", 30))  # Auto expire after 30 days

# 邮件内容清理开关
ENABLE_EMAIL_CLEANING = os.getenv("ENABLE_EMAIL_CLEANING", "true").lower() == "true"

DB_CONFIG = dict(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASS'),
    db=os.getenv('DB_NAME'),
    charset='utf8mb4'
)

# --- misc constants ---
MAX_MESSAGE_ID_LEN = 255

def create_empty_response(message_id: str) -> dict:
    completion_id = str(uuid.uuid4())
    return {
        "message_id": message_id,
        "response_text": "你说了什么么？我好像没看见",
        "user_text": "",
        "emotion": "",
        "rag_docs": [],
        "completion_id": completion_id,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "model": ""
    }

# ========== Redis缓存管理 ==========

def add_to_replied_cache(rds: redis.Redis, email_uuid: str):
    """Add email UUID to replied cache with TTL for auto-expiration"""
    ttl_seconds = REPLIED_TTL_DAYS * 24 * 3600  # Convert days to seconds
    
    # Add to set and set TTL for the entire set
    rds.sadd(REPLIED_EMAILS_SET, email_uuid)
    rds.expire(REPLIED_EMAILS_SET, ttl_seconds)
    
    # Also set individual key with TTL as backup
    key = f"replied:{email_uuid}"
    rds.setex(key, ttl_seconds, "1")

def is_already_replied(rds: redis.Redis, email_uuid: str) -> bool:
    """Check if email has been replied to using both methods"""
    # Check in set first (faster)
    if rds.sismember(REPLIED_EMAILS_SET, email_uuid):
        return True
    
    # Check individual key as backup
    key = f"replied:{email_uuid}"
    return rds.exists(key) > 0

# ========== 数据库存取 ==========

def save_ai_response_to_email(
    db_conn,
    email_uuid: str,
    user_text: str,
    rag_docs: list[str],
    response_text: str,
    prompt: str,
    completion_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    processing_time_ms: int = None,
    model: str = None
):
    """Save AI response directly to emails table"""
    try:
        if not completion_id:
            completion_id = str(uuid.uuid4())
        rag_text = "\n".join(rag_docs)
        prompt_str = str(prompt)

        db_conn.ping(reconnect=True)
        with db_conn.cursor() as cursor:
            sql = """
            UPDATE emails SET 
                ai_user_text = %s,
                ai_rag_docs = %s,
                ai_response_text = %s,
                ai_prompt = %s,
                ai_completion_id = %s,
                ai_prompt_tokens = %s,
                ai_completion_tokens = %s,
                ai_total_tokens = %s,
                ai_processing_time_ms = %s,
                ai_model = %s,
                ai_processed_at = NOW(),
                is_processed = 1,
                response_status = 1,
                response_sent_at = NOW(),
                updated_at = NOW()
            WHERE uuid = %s
            """
            cursor.execute(sql, (
                user_text, rag_text, response_text, prompt_str, completion_id,
                prompt_tokens, completion_tokens, total_tokens,
                processing_time_ms, model, email_uuid
            ))
        db_conn.commit()
        logger.info(f"✅ Saved AI response to email UUID {email_uuid[:8]}..., completion_id={completion_id}")
    except Exception as e:
        logger.error(f"❌ Failed to save AI response: {e}", exc_info=True)
        db_conn.rollback()
        raise

# ========== 函数 ==========

def connect_with_retry(db_config, retries=5, delay=2):
    for attempt in range(1, retries + 1):
        try:
            conn = pymysql.connect(**db_config)
            logger.info("✅ Connected to MySQL")
            return conn
        except pymysql.err.OperationalError as e:
            logger.error(f"❌ Attempt {attempt}: Failed to connect to MySQL - {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                logger.critical("🚨 All MySQL connection retries failed.")
                raise

# --- SMTP accounts loader ---

def load_and_map_smtp_accounts():
    smtp_accounts: dict[str, dict] = {}
    env = os.environ
    smtp_nums = {m.group(1) for k in env.keys() if (m := re.match(r"SMTP(\d+)_HOST", k))}

    for num in sorted(smtp_nums):
        prefix = f"SMTP{num}"
        mapped_email = env.get(f"{prefix}_MAP_TO")
        if not mapped_email:
            logger.warning(f"⚠️ {prefix}_MAP_TO not set, skipping this config block.")
            continue
        mapped_email = mapped_email.strip().lower()
        smtp_accounts[mapped_email] = {
            "smtp_host": env.get(f"{prefix}_HOST", "mail.spacemail.com"),
            "smtp_port": int(env.get(f"{prefix}_PORT", 465)),
            "smtp_user": env.get(f"{prefix}_USER") or env.get(f"{prefix}_EMAIL"),
            "smtp_pass": env.get(f"{prefix}_PASS"),
        }
        if not smtp_accounts[mapped_email]["smtp_user"]:
            logger.warning(f"⚠️ {prefix}_USER not set for {mapped_email}, login might fail.")
    logger.info(f"Loaded SMTP accounts: {list(smtp_accounts.keys())}")
    return smtp_accounts

# ========== 🔁 HTTP AI 回复接口 ==========

def fetch_ai_reply(email_data: dict):
    api_url = os.getenv("AI_API_URL")
    if not api_url:
        raise RuntimeError("AI_API_URL not set in environment")

    raw_content = email_data.get("content") or ""
    if not raw_content:
        return create_empty_response(email_data.get("message_id") or f"email_{email_data['email_id']}")

    processed_text = raw_content
    
    # 如果启用了邮件清理功能
    if ENABLE_EMAIL_CLEANING:
        # ---- 净化邮件内容，去掉引用和历史回复 ----
        separator_patterns = [
            # 常见的邮件分隔线
            re.compile(r"[-_=]{10,}", re.MULTILINE),
            re.compile(r"[*]{10,}", re.MULTILINE),
            
            # 原始邮件标识（中英文）
            re.compile(r".*?(?:原始邮件|Original Message|原邮件|Original Email).*", re.IGNORECASE | re.MULTILINE),
            re.compile(r".*?(?:-----\s*原文\s*-----)", re.IGNORECASE | re.MULTILINE),
            re.compile(r".*?(?:-----\s*Original\s*-----)", re.IGNORECASE | re.MULTILINE),
            
            # 发件人信息行
            re.compile(r"^\s*(?:From|发件人|发自|Sent by|寄件者)\s*[:：].*", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*(?:To|收件人|发送给|Sent to|收件者)\s*[:：].*", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*(?:Date|时间|日期|发送时间|Sent)\s*[:：].*", re.IGNORECASE | re.MULTILINE),
            re.compile(r"^\s*(?:Subject|主题|标题|Re:|回复)\s*[:：].*", re.IGNORECASE | re.MULTILINE),
            
            # 表格形式的邮件头
            re.compile(r"^\s*\|\s*(?:发件人|主题|原始邮件|Subject|From|To|Date)\s*\|", re.IGNORECASE | re.MULTILINE),
            
            # 回复/转发标识
            re.compile(r".*?(?:在.*写道|wrote|writes|said|says)[:：]?\s*$", re.IGNORECASE | re.MULTILINE),
            re.compile(r".*?(?:On.*wrote|在.*发表|在.*回复).*", re.IGNORECASE | re.MULTILINE),
            
            # Gmail/Outlook 风格分隔
            re.compile(r"^\s*>+\s*.*", re.MULTILINE),  # 引用符号
            re.compile(r".*?(?:Begin forwarded message|转发的邮件开始).*", re.IGNORECASE | re.MULTILINE),
            
            # 邮件签名分隔
            re.compile(r"^\s*--\s*$", re.MULTILINE),
            re.compile(r"^\s*___+\s*$", re.MULTILINE),
            
            # 时间戳格式
            re.compile(r".*?(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}).*(?:wrote|发表|回复|说道).*", re.IGNORECASE | re.MULTILINE),
            
            # 移动设备发送标识
            re.compile(r".*?(?:Sent from my|从我的.*发送|发自我的).*", re.IGNORECASE | re.MULTILINE),
            
            # 通用分隔标记
            re.compile(r".*?(?:---+|===+|\*\*\*+).*(?:转发|回复|Forward|Reply).*", re.IGNORECASE | re.MULTILINE),
        ]

        # 应用分隔模式，只保留第一部分（当前邮件内容）
        for pattern in separator_patterns:
            split_result = pattern.split(processed_text, maxsplit=1)
            if len(split_result) > 1:
                processed_text = split_result[0]
                logger.debug(f"Applied pattern: {pattern.pattern}")
                break
        
        # 额外清理：移除空行过多的情况和引用行
        lines = processed_text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # 跳过明显的引用行
            if line.strip().startswith('>'):
                continue
            # 跳过只包含分隔符的行
            if re.match(r'^\s*[-_=*]{3,}\s*$', line):
                continue
            # 跳过邮件头信息行
            if re.match(r'^\s*(?:From|To|Date|Subject|发件人|收件人|日期|主题)\s*[:：]', line, re.IGNORECASE):
                continue
            
            cleaned_lines.append(line)
        
        # 重新组合，限制连续空行
        processed_text = '\n'.join(cleaned_lines)
        # 将多个连续换行符替换为最多两个
        processed_text = re.sub(r'\n{3,}', '\n\n', processed_text)
        
        logger.debug(f"Email content cleaned, original length: {len(raw_content)}, cleaned length: {len(processed_text)}")
    else:
        logger.debug("Email cleaning disabled, using raw content")
    
    processed_text = processed_text.strip()
    if not processed_text:
        return create_empty_response(email_data.get("message_id") or f"email_{email_data.get('email_uuid', 'unknown')}")

    # 准备附件信息
    attachments = []
    if email_data.get('has_attachment') and email_data.get('attachment_info'):
        try:
            import json
            attachment_info = email_data.get('attachment_info')
            if isinstance(attachment_info, str):
                attachments = json.loads(attachment_info)
            elif isinstance(attachment_info, list):
                attachments = attachment_info
            
            # 提取附件URL列表
            attachment_urls = []
            for att in attachments:
                if isinstance(att, dict) and att.get('url'):
                    attachment_urls.append({
                        'url': att['url'],
                        'filename': att.get('filename', 'unknown'),
                        'type': att.get('content_type', 'unknown'),
                        'size': att.get('size', 0)
                    })
            
            if attachment_urls:
                logger.info(f"Found {len(attachment_urls)} attachment(s) to send to AI")
                
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse attachment info: {e}")
            attachment_urls = []
    else:
        attachment_urls = []

    payload = {
        "message_id": email_data.get("message_id") or f"email_{email_data.get('email_uuid', 'unknown')}",
        "text": processed_text,
        "attachments": attachment_urls,  # 添加附件URL信息
        "has_attachments": len(attachment_urls) > 0  # 附件标识
    }

    session = getattr(fetch_ai_reply, "_session", None)
    if session is None:
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        session.mount("https://", HTTPAdapter(max_retries=retries))
        session.mount("http://", HTTPAdapter(max_retries=retries))
        fetch_ai_reply._session = session

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("AI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = int(os.getenv("AI_API_TIMEOUT", 15))
    resp = session.post(api_url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

# ========== 发邮件 ==========

def send_auto_reply(to_user_email: str, smtp_cfg: dict, reply_text: str, *, original_message_id: str | None = None, html: bool = False):
    subtype = 'html' if html else 'plain'
    msg = MIMEText(reply_text, subtype, 'utf-8')
    msg['To']   = email.utils.formataddr(('User', to_user_email))
    msg['From'] = email.utils.formataddr(('Jesse Xia', smtp_cfg['smtp_user']))
    msg['Subject'] = "Re: 谢谢你的邮件"

    if original_message_id:
        msg['In-Reply-To'] = original_message_id
        msg['References']  = original_message_id

    server = None
    try:
        if smtp_cfg['smtp_port'] == 465:
            server = smtplib.SMTP_SSL(smtp_cfg['smtp_host'], smtp_cfg['smtp_port'], timeout=20)
        else:
            server = smtplib.SMTP(smtp_cfg['smtp_host'], smtp_cfg['smtp_port'], timeout=20)
            server.starttls()
        server.login(smtp_cfg['smtp_user'], smtp_cfg['smtp_pass'])
        server.sendmail(smtp_cfg['smtp_user'], [to_user_email], msg.as_string())
        logger.info(f"📤 Successfully sent auto-reply to {to_user_email}")
    finally:
        if server:
            server.quit()

# ========== 拉取邮件记录 ==========

def process_email_by_uuid(db_conn, db_cursor, email_uuid: str):
    """Process email by UUID using new schema"""
    sql_select = """
        SELECT uuid, from_email, to_email, content, is_processed, message_id, 
               subject, has_attachment, attachment_info
        FROM emails 
        WHERE uuid = %s
    """
    db_conn.ping(reconnect=True)
    db_cursor.execute(sql_select, (email_uuid,))
    row = db_cursor.fetchone()
    if not row:
        logger.warning(f"❓ Email with UUID {email_uuid[:8]}... not found in database.")
        return None

    uuid_val, from_email, to_email, content, is_processed, message_id, subject, has_attachment, attachment_info = row
    if is_processed:
        logger.info(f"⏩ Email UUID {email_uuid[:8]}... has already been processed.")
        return None

    to_email_norm = to_email.strip().lower()
    smtp_cfg = SMTP_ACCOUNTS.get(to_email_norm) or SMTP_ACCOUNTS.get('jesse0526@officalbusiness.com')
    if not smtp_cfg:
        logger.warning(f"🤷 No SMTP config found for to_email='{to_email_norm}' (UUID: {email_uuid[:8]}...).")
        return None

    email_data = {
        'from_email': from_email,
        'to_email': to_email,
        'content': content,
        'subject': subject,
        'email_uuid': email_uuid,
        'message_id': message_id,
        'has_attachment': has_attachment,
        'attachment_info': attachment_info
    }
    return email_data, smtp_cfg

def process_email(db_conn, db_cursor, email_id: int):
    """Legacy function for processing by email_id - kept for backward compatibility"""
    sql_select = """
        SELECT uuid, from_email, to_email, content, is_processed, message_id,
               subject, has_attachment, attachment_info
        FROM emails 
        WHERE id = %s
    """
    db_conn.ping(reconnect=True)
    db_cursor.execute(sql_select, (email_id,))
    row = db_cursor.fetchone()
    if not row:
        logger.warning(f"❓ Email_id {email_id} not found in database.")
        return None

    uuid_val, from_email, to_email, content, is_processed, message_id, subject, has_attachment, attachment_info = row
    if is_processed:
        logger.info(f"⏩ Email_id {email_id} has already been processed.")
        return None

    to_email_norm = to_email.strip().lower()
    smtp_cfg = SMTP_ACCOUNTS.get(to_email_norm) or SMTP_ACCOUNTS.get('jesse0526@officalbusiness.com')
    if not smtp_cfg:
        logger.warning(f"🤷 No SMTP config found for to_email='{to_email_norm}' (email_id={email_id}).")
        return None

    email_data = {
        'from_email': from_email,
        'to_email': to_email,
        'content': content,
        'subject': subject,
        'email_uuid': uuid_val,
        'message_id': message_id,
        'has_attachment': has_attachment,
        'attachment_info': attachment_info
    }
    return email_data, smtp_cfg

# ========== 处理并发送 ==========

def process_with_retry_uuid(db_conn, db_cursor, rds: redis.Redis, email_data: dict, smtp_cfg: dict):
    """Core pipeline with UUID support: fetch AI, send reply, log DB, mark processed."""
    email_uuid = email_data.get('email_uuid')

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # === 拉 AI 回复 ===
            start_time = time.time()
            data = fetch_ai_reply(email_data)
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            response_text = data.get("response_text")
            if not response_text:
                raise ValueError(f"response_text is empty for email UUID {email_uuid[:8]}...")

            # === 发送邮件 ===
            send_auto_reply(
                email_data['from_email'],
                smtp_cfg,
                response_text,
                original_message_id=email_data.get('message_id'),
            )

            # === 保存AI响应到邮件表 ===
            save_ai_response_to_email(
                db_conn,
                email_uuid=email_uuid,
                user_text=data.get("user_text", ""),
                rag_docs=data.get("rag_docs", []),
                response_text=response_text,
                prompt=email_data.get("content", ""),
                completion_id=data.get("completion_id", ""),
                prompt_tokens=data.get("prompt_tokens", 0),
                completion_tokens=data.get("completion_tokens", 0),
                total_tokens=data.get("total_tokens", 0),
                processing_time_ms=processing_time_ms,
                model=data.get("model", "")
            )
            
            # === 添加到已回复缓存（带TTL自动淘汰） ===
            add_to_replied_cache(rds, email_uuid)
            logger.info(f"📝 Added email UUID {email_uuid[:8]}... to replied cache")
            
            logger.info(f"✅ Replied and marked email UUID {email_uuid[:8]}... as processed. (Attempt {attempt})")
            return  # success

        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout) as e:
            logger.error(f"❌ SMTP Connection Error on attempt {attempt} for email UUID {email_uuid[:8]}...: {e}")
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ SMTP Auth Error for {smtp_cfg['smtp_user']}: {e}")
            break  # credential wrong, no further retries
        except Exception as e:
            logger.error(f"❌ Unexpected error on attempt {attempt} for email UUID {email_uuid[:8]}...: {e}")

        db_conn.rollback()
        if attempt < MAX_RETRIES:
            logger.info(f"Retrying in {RETRY_INTERVAL} seconds...")
            time.sleep(RETRY_INTERVAL)
        else:
            logger.critical(f"🚨 Max retries reached for email UUID {email_uuid[:8]}..., giving up.")

def process_with_retry(db_conn, db_cursor, rds: redis.Redis, email_data: dict, smtp_cfg: dict, email_uuid: str):
    """Core pipeline for legacy compatibility: fetch AI, send reply, log DB, mark processed."""
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # === 拉 AI 回复 ===
            start_time = time.time()
            data = fetch_ai_reply(email_data)
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            response_text = data.get("response_text")
            if not response_text:
                raise ValueError(f"response_text is empty for email UUID {email_uuid[:8]}...")

            # === 发送邮件 ===
            send_auto_reply(
                email_data['from_email'],
                smtp_cfg,
                response_text,
                original_message_id=email_data.get('message_id'),
            )

            # === 保存AI响应到邮件表 ===
            save_ai_response_to_email(
                db_conn,
                email_uuid=email_uuid,
                user_text=data.get("user_text", ""),
                rag_docs=data.get("rag_docs", []),
                response_text=response_text,
                prompt=email_data.get("content", ""),
                completion_id=data.get("completion_id", ""),
                prompt_tokens=data.get("prompt_tokens", 0),
                completion_tokens=data.get("completion_tokens", 0),
                total_tokens=data.get("total_tokens", 0),
                processing_time_ms=processing_time_ms,
                model=data.get("model", "")
            )
            
            # === 添加到已回复缓存（带TTL自动淘汰） ===
            add_to_replied_cache(rds, email_uuid)
            logger.info(f"📝 Added email UUID {email_uuid[:8]}... to replied cache")
            
            logger.info(f"✅ Replied and marked email UUID {email_uuid[:8]}... as processed. (Attempt {attempt})")
            return  # success

        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout) as e:
            logger.error(f"❌ SMTP Connection Error on attempt {attempt} for email UUID {email_uuid[:8]}...: {e}")
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ SMTP Auth Error for {smtp_cfg['smtp_user']}: {e}")
            break  # credential wrong, no further retries
        except Exception as e:
            logger.error(f"❌ Unexpected error on attempt {attempt} for email UUID {email_uuid[:8]}...: {e}")

        db_conn.rollback()
        if attempt < MAX_RETRIES:
            logger.info(f"Retrying in {RETRY_INTERVAL} seconds...")
            time.sleep(RETRY_INTERVAL)
        else:
            logger.critical(f"🚨 Max retries reached for email UUID {email_uuid[:8]}..., giving up.")

# ========== 消费 Redis ==========

def consume_tasks(db_conn, db_cursor, rds: redis.Redis):
    logger.info("Listening for email tasks from Redis queue…")
    while True:
        try:
            item = rds.blpop(REDIS_QUEUE, timeout=20)
            if item is None:
                continue  # queue idle

            _, task_bytes = item
            task_str = task_bytes.decode('utf-8')
            
            # 尝试判断是UUID还是旧的email_id格式
            try:
                # 如果可以转换为int，说明是旧格式的email_id
                email_id = int(task_str)
                logger.info(f"Processing legacy email_id format: {email_id}")
                result = process_email(db_conn, db_cursor, email_id)
                if result:
                    email_data, smtp_cfg = result
                    email_uuid = email_data.get('email_uuid')
                    
                    # 检查是否已经回复过（使用新的缓存检查）
                    if email_uuid and is_already_replied(rds, email_uuid):
                        logger.info(f"⏭️ Email UUID {email_uuid[:8]}... already replied, skipping.")
                        continue
                    
                    process_with_retry(db_conn, db_cursor, rds, email_data, smtp_cfg, email_uuid)
            except ValueError:
                # 不能转换为int，说明是UUID格式
                email_uuid = task_str
                logger.info(f"Processing UUID format: {email_uuid[:8]}...")
                
                # 检查是否已经回复过（使用新的缓存检查）
                if is_already_replied(rds, email_uuid):
                    logger.info(f"⏭️ Email UUID {email_uuid[:8]}... already replied, skipping.")
                    continue
                    
                result = process_email_by_uuid(db_conn, db_cursor, email_uuid)
                if result:
                    email_data, smtp_cfg = result
                    process_with_retry_uuid(db_conn, db_cursor, rds, email_data, smtp_cfg)

        except Exception as e:
            logger.error(f"❌ Type: {type(e)} | Args: {e.args}")
            traceback.print_exc()
            time.sleep(5)

# ========== 主程序入口 ==========

def main():
    logger.info("Starting email auto-reply service…")

    global SMTP_ACCOUNTS
    SMTP_ACCOUNTS = load_and_map_smtp_accounts()
    if not SMTP_ACCOUNTS:
        logger.critical("🚨 CRITICAL: No SMTP accounts loaded. Exiting.")
        return
    logger.info(f"✅ Loaded SMTP accounts for: {list(SMTP_ACCOUNTS.keys())}")

    rds = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    logger.info(f"Connecting to MySQL with config: {DB_CONFIG}")
    db_conn = connect_with_retry(DB_CONFIG)
    db_cursor = db_conn.cursor()

    try:
        consume_tasks(db_conn, db_cursor, rds)
    except KeyboardInterrupt:
        logger.info("\nGracefully shutting down…")
    finally:
        db_cursor.close()
        db_conn.close()
        logger.info("MySQL connection closed. Service stopped.")


if __name__ == '__main__':
    main()
