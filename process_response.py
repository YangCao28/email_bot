import os
import re
import time
import redis
import pymysql
import smtplib
import socket
import email.utils
import logging
from dotenv import load_dotenv
from email.mime.text import MIMEText 

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== 全局配置 & 读取 .env ==========
MAX_RETRIES = 3
RETRY_INTERVAL = 5

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(basedir, '.env')
load_dotenv(dotenv_path, override=True)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_QUEUE = os.getenv("REDIS_QUEUE", "email_task_queue")

DB_CONFIG = dict(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASS'),
    db=os.getenv('DB_NAME'),
    charset='utf8mb4'
)

# ========== 核心函数 ==========

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

def load_and_map_smtp_accounts():
    smtp_accounts = {}
    env = os.environ
    smtp_nums = set()

    for k in env.keys():
        m = re.match(r"SMTP(\d+)_HOST", k)
        if m:
            smtp_nums.add(m.group(1))

    for num in sorted(list(smtp_nums)):
        prefix = f"SMTP{num}"
        mapped_email = env.get(f"{prefix}_MAP_TO")
        if not mapped_email:
            logger.warning(f"⚠️ Warning: {prefix}_MAP_TO not set, skipping this config block.")
            continue
        
        mapped_email = mapped_email.strip().lower()

        smtp_accounts[mapped_email] = {
            "smtp_host": env.get(f"{prefix}_HOST", "mail.spacemail.com"),
            "smtp_port": int(env.get(f"{prefix}_PORT", 465)),
            "smtp_user": env.get(f"{prefix}_USER") or env.get(f"{prefix}_EMAIL"),
            "smtp_pass": env.get(f"{prefix}_PASS"),
        }
        if not smtp_accounts[mapped_email]["smtp_user"]:
             logger.warning(f"⚠️ Warning: {prefix}_USER not set for {mapped_email}, login might fail.")
    logger.info(f"Loaded SMTP accounts: {list(smtp_accounts.keys())}")
    return smtp_accounts

def get_reply_text(email_data, html=False):
    content = (email_data.get('content') or '').strip()
    content_lower = content.lower()

    if any(k in content_lower for k in ['爱你', '想你', '喜欢你']):
        base_reply = "我也最喜欢你……也最爱你！"
        signature = "from 你的星星"
    else:
        base_reply = (
            "您好！\n"
            "我是夏鸣星，很感谢您的来信。\n"
            "如果是紧急商务事项，请联系我的经纪人大梁。\n"
            "我将在看到邮件后及时回复您！感谢理解！\n"
            "夏鸣星"
        )
        signature = "Jesse Xia"

    if html:
        base_reply_html = base_reply.replace('\n', '<br>')
        content_html = content.replace('\n', '<br>')
        if signature:
            base_reply_html += "<br>" + signature
        reply = (
            f"{base_reply_html}<br><hr><br>"
            f"<blockquote style='color:gray; font-style:italic;'>{content_html}</blockquote>"
        )
    else:
        reply = (
            base_reply
            + ("\n" + signature if signature else "")
            + "\n\n-------------------- 原邮件内容 --------------------\n"
            + content
        )
    return reply

def send_auto_reply(to_user_email, smtp_cfg, reply_text, original_message_id=None, html=False):
    subtype = 'html' if html else 'plain'
    msg = MIMEText(reply_text, subtype, 'utf-8')
    msg['To'] = email.utils.formataddr(('User', to_user_email))
    msg['From'] = email.utils.formataddr(('Jesse Xia', smtp_cfg['smtp_user']))
    msg['Subject'] = "Re: 谢谢你的邮件"

    if original_message_id:
        msg['In-Reply-To'] = original_message_id
        msg['References'] = original_message_id

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

def process_email(db_conn, db_cursor, email_id):
    sql_select = "SELECT from_email, to_email, content, is_processed, message_id FROM emails WHERE email_id=%s"
    db_cursor.execute(sql_select, (email_id,))
    row = db_cursor.fetchone()
    if not row:
        logger.warning(f"❓ Email_id {email_id} not found in database.")
        return None

    from_email, to_email, content, is_processed, message_id = row
    if is_processed:
        logger.info(f"⏩ Email_id {email_id} has already been processed.")
        return None

    to_email_norm = to_email.strip().lower()
    smtp_cfg = SMTP_ACCOUNTS.get(to_email_norm) or SMTP_ACCOUNTS.get('jesse0526@officalbusiness.com')
    if not smtp_cfg:
        logger.warning(f"🤷 No SMTP config found for to_email='{to_email_norm}' (email_id={email_id}). Check SMTP_MAP_TO in .env")
        return None

    email_data = {
        'from_email': from_email,
        'to_email': to_email,
        'content': content,
        'email_id': email_id,
        'message_id': message_id
    }
    return email_data, smtp_cfg

def process_with_retry(db_conn, db_cursor, email_data, smtp_cfg, email_id):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            reply_text = get_reply_text(email_data)
            send_auto_reply(
                email_data['from_email'],
                smtp_cfg,
                reply_text,
                original_message_id=email_data.get('message_id')
            )
            sql_update = "UPDATE emails SET is_processed=1, response=%s, response_time=NOW() WHERE email_id=%s"
            db_cursor.execute(sql_update, (reply_text, email_id))
            db_conn.commit()
            logger.info(f"✅ Replied and marked email_id {email_id} as processed. (Attempt {attempt})")
            return
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout) as e:
            logger.error(f"❌ Connection Error on attempt {attempt} for email_id {email_id}: {e}")
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ Authentication Error for {smtp_cfg['smtp_user']}: {e}. Check user/pass in .env.")
            logger.error("⚠️ This error is not recoverable, stopping retries for this email.")
            break
        except Exception as e:
            logger.error(f"❌ Unexpected error on attempt {attempt} for email_id {email_id}: {e}")

        db_conn.rollback()
        if attempt < MAX_RETRIES:
            logger.info(f"Retrying in {RETRY_INTERVAL} seconds...")
            time.sleep(RETRY_INTERVAL)
        else:
            logger.critical(f"🚨 Max retries reached for email_id {email_id}, giving up.")

def consume_tasks(db_conn, db_cursor, redis_client):
    logger.info("Listening for email tasks from Redis queue...")
    while True:
        try:
            item = redis_client.blpop(REDIS_QUEUE, timeout=20)
            if item is None:
                continue

            _, email_id_bytes = item
            email_id_str = email_id_bytes.decode('utf-8')
            email_id = int(email_id_str)

            result = process_email(db_conn, db_cursor, email_id)
            if result:
                email_data, smtp_cfg = result
                process_with_retry(db_conn, db_cursor, email_data, smtp_cfg, email_id)

        except (ValueError, TypeError):
            logger.error(f"Invalid message format in queue: {email_id_str}. Message should be an integer ID.")
        except Exception as e:
            logger.error(f"An error occurred in main consumer loop: {e}")
            time.sleep(5)

# ========== 主程序入口 ==========
def main():
    logger.info("Starting email auto-reply service...")

    global SMTP_ACCOUNTS
    SMTP_ACCOUNTS = load_and_map_smtp_accounts()
    if not SMTP_ACCOUNTS:
        logger.critical("🚨 CRITICAL: No SMTP accounts loaded. Exiting.")
        return
    logger.info(f"✅ Loaded SMTP accounts for: {list(SMTP_ACCOUNTS.keys())}")

    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    logger.info(f"Connecting to MySQL with config: {DB_CONFIG}")
    db_conn = connect_with_retry(DB_CONFIG)
    db_cursor = db_conn.cursor()

    try:
        consume_tasks(db_conn, db_cursor, redis_client)
    except KeyboardInterrupt:
        logger.info("\nGracefully shutting down...")
    finally:
        db_cursor.close()
        db_conn.close()
        logger.info("MySQL connection closed. Service stopped.")

if __name__ == '__main__':
    main()
