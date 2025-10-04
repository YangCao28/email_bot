import os
import imaplib
import time
import email
from email.header import decode_header
from email.utils import parseaddr
import pymysql
import redis
from datetime import datetime, timedel                log.info(f"🖼️ Detected image: {decoded_filename} ({content_type}, {file_size} byt                if has_attachment:
                    log.info(f"🖼️ Email has {attachment_count} image(s), total size: {total_attachment_size} bytes"))")a, timezone
from dotenv import load_dotenv
import uuid
import logging
import sys
import hashlib
import json

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
REPLIED_EMAILS_SET = 'replied_emails_set'  # Redis Set to track replied emails
REPLIED_TTL_DAYS = int(os.getenv("REPLIED_TTL_DAYS", 30))  # Auto expire after 30 days

# ========== Functions ==========

def generate_email_uuid(message_id, from_email, content):
    """Generate consistent UUID for email based on message_id and content"""
    if message_id:
        uuid_source = f"{message_id}:{from_email}:{content[:100]}"
    else:
        uuid_source = f"{from_email}:{content[:200]}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, uuid_source))

def check_if_already_replied(redis_client, email_uuid):
    """Check if email has already been replied to using dual cache strategy"""
    # Check in set first (faster)
    if redis_client.sismember(REPLIED_EMAILS_SET, email_uuid):
        return True
    
    # Check individual key as backup
    key = f"replied:{email_uuid}"
    return redis_client.exists(key) > 0

def get_or_create_sender_uuid(db_cursor, from_email):
    """Get existing sender UUID or create new sender record"""
    # Check if sender already exists
    db_cursor.execute("SELECT sender_uuid FROM email_senders WHERE email_address = %s", (from_email,))
    result = db_cursor.fetchone()
    
    if result:
        return result[0]
    else:
        # Create new sender record
        sender_uuid = str(uuid.uuid4())
        db_cursor.execute("""
            INSERT INTO email_senders (sender_uuid, email_address, total_emails_sent, last_email_at) 
            VALUES (%s, %s, 1, NOW())
        """, (sender_uuid, from_email))
        return sender_uuid

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

def decode_mime_words(header_value):
    """
    解码邮件头字段（如 Subject），支持 UTF-8、GB2312、GBK 等编码。
    """
    if not header_value:
        return ""

    decoded_fragments = decode_header(header_value)
    decoded_string = ""

    for fragment, charset in decoded_fragments:
        try:
            if charset:
                decoded_string += fragment.decode(charset, errors="ignore")
            elif isinstance(fragment, bytes):
                # 没标明编码时尝试 utf-8 → gbk
                try:
                    decoded_string += fragment.decode("utf-8", errors="ignore")
                except:
                    decoded_string += fragment.decode("gbk", errors="ignore")
            else:
                decoded_string += fragment
        except Exception as e:
            logging.warning(f"❗️ Header decode error: {e}")
            decoded_string += str(fragment)

    return decoded_string.strip()

def extract_email_content(msg):
    """Extract email content and detect image attachments only"""
    body_parts = []
    attachments = []
    has_attachment = False

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_dispo = str(part.get("Content-Disposition") or "")
            filename = part.get_filename()

            # Extract text content with proper encoding
            if content_type == "text/plain" and "attachment" not in content_dispo:
                charset = part.get_content_charset() or "utf-8"
                try:
                    part_body = part.get_payload(decode=True).decode(charset, errors="ignore")
                    body_parts.append(part_body)
                except Exception as e:
                    log.warning(f"❗️ Failed to decode part: {e}")

            # Only detect image attachments
            if content_type.startswith('image/'):
                has_attachment = True
                
                # Extract image attachment info with proper text decoding
                if filename:
                    decoded_filename = decode_mime_words(filename)
                else:
                    # Generate filename based on image type
                    ext_map = {
                        'image/jpeg': '.jpg',
                        'image/jpg': '.jpg',
                        'image/png': '.png',
                        'image/gif': '.gif',
                        'image/bmp': '.bmp',
                        'image/webp': '.webp',
                        'image/tiff': '.tiff'
                    }
                    ext = ext_map.get(content_type, '.img')
                    decoded_filename = f"image{len(attachments)+1}{ext}"
                
                # Get image file size if available
                payload = part.get_payload(decode=True)
                file_size = len(payload) if payload else 0
                
                attachment_info = {
                    'filename': decoded_filename,
                    'content_type': content_type,
                    'size': file_size,
                    'url': '',  # Will be filled when uploaded to storage
                    'hash': hashlib.sha256(payload).hexdigest() if payload else ''
                }
                attachments.append(attachment_info)
                
                log.info(f"�️ Detected image: {decoded_filename} ({content_type}, {file_size} bytes)")

        body = "\n".join(body_parts)
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            body = msg.get_payload(decode=True).decode(charset, errors="ignore")
        except Exception as e:
            logging.warning(f"❗️ Failed to decode body: {e}")
            body = ""

    # 解码主题字段
    raw_subject = msg.get("Subject", "")
    subject = decode_mime_words(raw_subject)

    # 合并主题与正文
    body_with_subject = f"Subject: {subject}\n\n{body.strip()}" if subject else body.strip()

    return body_with_subject, has_attachment, attachments

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
            subject = decode_mime_words(msg.get('Subject', ''))
            content, has_attachment, attachments = extract_email_content(msg)
            
            # Generate email UUID
            email_uuid = generate_email_uuid(message_id, from_email, content)
            
            # Check if already replied using Redis Set
            if check_if_already_replied(redis_client, email_uuid):
                log.info(f"⏭️ Email UUID {email_uuid[:8]}... already replied, skipping.")
                continue

            # Check if email already exists in database
            sql_check = "SELECT uuid, is_processed FROM emails WHERE message_id = %s OR uuid = %s"
            db_cursor.execute(sql_check, (message_id, email_uuid))
            row = db_cursor.fetchone()

            if row:
                existing_uuid, is_processed = row
                if is_processed == 0:
                    redis_client.rpush(EMAIL_TASK_QUEUE, existing_uuid)
                    log.info(f"🔁 Existing email UUID {existing_uuid[:8]}... from {from_email} pushed to Redis.")
                else:
                    log.debug(f"✅ Email UUID {existing_uuid[:8]}... already processed.")
            else:
                # Get or create sender UUID
                sender_uuid = get_or_create_sender_uuid(db_cursor, from_email)
                
                # Prepare attachment info JSON
                attachment_info_json = json.dumps(attachments) if attachments else None
                attachment_count = len(attachments)
                total_attachment_size = sum(att.get('size', 0) for att in attachments)
                content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
                
                # Insert new email with UUID and all attachment info
                sql_insert = """
                    INSERT INTO emails (
                        uuid, message_id, sender_uuid, from_email, to_email, 
                        subject, content, content_hash, has_attachment, 
                        attachment_count, attachment_info, total_attachment_size, 
                        received_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                """
                db_cursor.execute(sql_insert, (
                    email_uuid, message_id, sender_uuid, from_email, to_email,
                    subject, content, content_hash, has_attachment,
                    attachment_count, attachment_info_json, total_attachment_size
                ))
                db_conn.commit()
                
                # Push UUID to Redis queue for processing
                redis_client.rpush(EMAIL_TASK_QUEUE, email_uuid)
                
                log.info(f"🆕 New email UUID {email_uuid[:8]}... from {from_email} inserted and queued.")
                if has_attachment:
                    log.info(f"�️ Email has {attachment_count} image(s), total size: {total_attachment_size} bytes")

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
