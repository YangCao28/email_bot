import os
import imaplib
import time
import email
from email.header import decode_header
from email.utils import parseaddr
import pymysql
import redis
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import uuid
import logging
import sys

# ========== Logging Setup ==========
os.makedirs("/var/log/email", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/email/pull_email.log')
    ]
)
log = logging.getLogger(__name__)

# ========== Load Env ==========
basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(basedir, '.env')
load_dotenv(dotenv_path, override=True)

# ========== MySQL Config ==========
DB_CONFIG = dict(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASS'),
    db=os.getenv('DB_NAME'),
    charset='utf8mb4'
)

# ========== Redis Config ==========
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
EMAIL_TASK_QUEUE = 'email_task_queue'

# ========== Functions ==========

def connect_with_retry(db_config, retries=5, delay=2):
    for attempt in range(1, retries + 1):
        try:
            conn = pymysql.connect(**db_config)
            log.info("✅ Connected to MySQL")
            return conn
        except pymysql.err.OperationalError as e:
            log.warning(f"❌ Attempt {attempt}: Failed to connect to MySQL - {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                log.error("🚨 All retries failed.")
                raise

def load_email_accounts():
    accounts = []
    for i in range(1, 20):
        host = os.getenv(f'EMAIL{i}_HOST')
        if not host:
            break
        port = int(os.getenv(f'EMAIL{i}_PORT', '993'))
        user = os.getenv(f'EMAIL{i}_USER')
        password = os.getenv(f'EMAIL{i}_PASS')
        accounts.append({
            'imap_host': host,
            'imap_port': port,
            'imap_user': user,
            'imap_pass': password,
        })
    log.info(f"🔐 Loaded {len(accounts)} email account(s).")
    return accounts

def decode_mime_words(s):
    if not s:
        return ''
    decoded_parts = decode_header(s)
    return ''.join(
        [part.decode(encoding or 'utf-8') if isinstance(part, bytes) else part for part, encoding in decoded_parts]
    )

def extract_email_content(msg):
    body_parts = []
    has_attachment = False

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_dispo = str(part.get("Content-Disposition") or "")

            if content_type == "text/plain" and "attachment" not in content_dispo:
                charset = part.get_content_charset() or "utf-8"
                try:
                    part_body = part.get_payload(decode=True).decode(charset, errors="ignore")
                    body_parts.append(part_body)
                except Exception as e:
                    log.warning(f"❗️ Failed to decode part: {e}")

            if "attachment" in content_dispo:
                has_attachment = True
        body = "\n".join(body_parts)
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="ignore")

    return body, has_attachment

def fetch_recent_emails(imap_host, imap_port, imap_user, imap_pass, db_conn, db_cursor, redis_client):
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(imap_user, imap_pass)
        mail.select('inbox')

        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        since_date = one_hour_ago.strftime('%d-%b-%Y')

        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        email_ids = messages[0].split()

        log.info(f"📥 {imap_user}: Found {len(email_ids)} new email(s) since {since_date}")

        for eid in reversed(email_ids):
            status, msg_data = mail.fetch(eid, '(RFC822)')
            if status != 'OK':
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            from_email = parseaddr(msg.get('From'))[1]
            to_email = imap_user
            message_id = msg.get('Message-ID') or str(uuid.uuid4())
            content, has_attachment = extract_email_content(msg)

            sql_check = "SELECT email_id, is_processed FROM emails WHERE message_id = %s"
            db_cursor.execute(sql_check, (message_id,))
            row = db_cursor.fetchone()

            if row:
                email_id, is_processed = row
                if is_processed == 0:
                    redis_client.rpush(EMAIL_TASK_QUEUE, email_id)
                    log.info(f"🔁 Existing email_id {email_id} from {from_email} pushed to Redis.")
                else:
                    log.debug(f"✅ email_id {email_id} already processed.")
            else:
                sql_insert = """
                    INSERT INTO emails (received_at, from_email, to_email, content, message_id, has_attachment)
                    VALUES (NOW(), %s, %s, %s, %s, %s)
                """
                db_cursor.execute(sql_insert, (from_email, to_email, content, message_id, has_attachment))
                db_conn.commit()
                new_email_id = db_cursor.lastrowid
                redis_client.rpush(EMAIL_TASK_QUEUE, new_email_id)
                log.info(f"🆕 New email_id {new_email_id} from {from_email} inserted and queued.")

        mail.logout()

    except Exception as e:
        log.exception(f"[{imap_user}] ❌ Error occurred while fetching emails: {e}")

# ========== Main ==========
if __name__ == '__main__':
    log.info("🚀 pull_email.py started.")
    db_conn = connect_with_retry(DB_CONFIG)
    db_cursor = db_conn.cursor()
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

    accounts = load_email_accounts()

    try:
        for account in accounts:
            fetch_recent_emails(
                account['imap_host'],
                account['imap_port'],
                account['imap_user'],
                account['imap_pass'],
                db_conn,
                db_cursor,
                redis_client
            )
    except KeyboardInterrupt:
        log.info("⛔️ Program interrupted by user.")
    finally:
        db_cursor.close()
        db_conn.close()
        log.info("🔚 MySQL connection closed. pull_email.py finished.")
