#!/usr/bin/env python3
# disclaimer_bot.py – Edit Mode + Test Command

import os
import sys
import json
import time
import sqlite3
import logging
import threading
import requests
import fcntl
from flask import Flask, request

# ========== CONFIG ==========
BOT_TOKEN = "8845364296:AAEp8LIWzferAhwXlfNUIyRKY7u_YYnbwPk"
OWNER_IDS = [8754004223]  # <-- REPLACE WITH YOUR TELEGRAM ID
DB_FILE = "disclaimer.db"
LOCK_FILE = "bot.lock"
DEFAULT_DISCLAIMER = "\n\n⚠️ Disclaimer: This content is only for educational purposes."
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - PID:%(process)d - THREAD:%(thread)d - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ========== DATABASE SETUP ==========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS processed_updates (
            update_id INTEGER PRIMARY KEY,
            processed_at INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS processed_messages (
            chat_id INTEGER,
            message_id INTEGER,
            processed_at INTEGER,
            PRIMARY KEY (chat_id, message_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    c.execute("INSERT OR IGNORE INTO bot_state (key, value) VALUES ('disclaimer', ?)", (DEFAULT_DISCLAIMER,))
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

def get_disclaimer():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM bot_state WHERE key='disclaimer'")
    row = c.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_DISCLAIMER

def set_disclaimer(text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("REPLACE INTO bot_state (key, value) VALUES ('disclaimer', ?)", (text,))
    conn.commit()
    conn.close()

def reset_disclaimer():
    set_disclaimer(DEFAULT_DISCLAIMER)

def get_offset():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM bot_state WHERE key='offset'")
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 0

def set_offset(offset):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("REPLACE INTO bot_state (key, value) VALUES ('offset', ?)", (str(offset),))
    conn.commit()
    conn.close()

def is_update_processed(update_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed_updates WHERE update_id=?", (update_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_update_processed(update_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO processed_updates (update_id, processed_at) VALUES (?, ?)",
              (update_id, int(time.time())))
    conn.commit()
    conn.close()

def is_message_processed(chat_id, message_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    row = c.fetchone()
    conn.close()
    return row is not None

def mark_message_processed(chat_id, message_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO processed_messages (chat_id, message_id, processed_at) VALUES (?, ?, ?)",
              (chat_id, message_id, int(time.time())))
    conn.commit()
    conn.close()

# ========== TELEGRAM HELPERS ==========
def call_telegram(method, **kwargs):
    url = f"{API_URL}/{method}"
    try:
        resp = requests.post(url, json=kwargs, timeout=30)
        if resp.status_code != 200:
            logger.error(f"API error {resp.status_code}: {resp.text}")
            return None
        data = resp.json()
        if not data.get('ok'):
            logger.error(f"API error: {data}")
            return None
        return data.get('result')
    except Exception as e:
        logger.error(f"call_telegram exception: {e}")
        return None

def edit_message(chat_id, message_id, new_text):
    logger.info(f"Editing message {chat_id}/{message_id}")
    result = call_telegram(
        "editMessageText",
        chat_id=chat_id,
        message_id=message_id,
        text=new_text,
        parse_mode="HTML"
    )
    if result:
        logger.info("Edit successful")
    else:
        logger.error("Edit failed! Check admin permissions.")
    return result

def edit_caption(chat_id, message_id, new_caption):
    logger.info(f"Editing caption {chat_id}/{message_id}")
    result = call_telegram(
        "editMessageCaption",
        chat_id=chat_id,
        message_id=message_id,
        caption=new_caption,
        parse_mode="HTML"
    )
    if result:
        logger.info("Caption edit successful")
    else:
        logger.error("Caption edit failed!")
    return result

def send_message(chat_id, text):
    return call_telegram("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")

# ========== MESSAGE PROCESSOR ==========
def process_message(update_id, chat_id, message_id, text, caption):
    logger.info(f"Processing: update_id={update_id}, chat={chat_id}, msg={message_id}")

    if is_update_processed(update_id):
        logger.info(f"Update {update_id} already processed – skipping.")
        return

    if is_message_processed(chat_id, message_id):
        logger.info(f"Message {chat_id}/{message_id} already processed – skipping.")
        mark_update_processed(update_id)
        return

    current_content = text if text is not None else caption
    if not current_content:
        logger.info("No text/caption – skipping.")
        mark_update_processed(update_id)
        mark_message_processed(chat_id, message_id)
        return

    # ---- ADMIN COMMANDS ----
    if text and text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/getdisclaimer":
            current = get_disclaimer()
            send_message(chat_id, f"📜 Current Disclaimer:\n{current}")
            mark_update_processed(update_id)
            mark_message_processed(chat_id, message_id)
            return

        # ---- /testedit ----
        if cmd == "/testedit":
            # Send a test message, then edit it
            sent = send_message(chat_id, "Testing edit capability...")
            if sent:
                time.sleep(2)
                edit_result = edit_message(chat_id, sent['message_id'], "✅ Edit successful! Bot can edit messages.")
                if edit_result:
                    send_message(chat_id, "✅ Bot can edit messages!")
                else:
                    send_message(chat_id, "❌ Edit failed. Please make bot admin with 'Edit messages' permission.")
            else:
                send_message(chat_id, "❌ Could not send test message.")
            mark_update_processed(update_id)
            mark_message_processed(chat_id, message_id)
            return

        if chat_id not in OWNER_IDS:
            send_message(chat_id, "❌ Not authorized.")
            mark_update_processed(update_id)
            mark_message_processed(chat_id, message_id)
            return

        if cmd == "/setdisclaimer":
            if not args:
                send_message(chat_id, "Usage: /setdisclaimer <text>")
            else:
                set_disclaimer(args)
                send_message(chat_id, f"✅ Disclaimer updated!\n\n{args}")
            mark_update_processed(update_id)
            mark_message_processed(chat_id, message_id)
            return

        if cmd == "/resetdisclaimer":
            reset_disclaimer()
            send_message(chat_id, f"✅ Reset to default.\n\n{DEFAULT_DISCLAIMER}")
            mark_update_processed(update_id)
            mark_message_processed(chat_id, message_id)
            return

        # Other commands – ignore
        mark_update_processed(update_id)
        mark_message_processed(chat_id, message_id)
        return

    # ---- NORMAL MESSAGE ----
    disclaimer = get_disclaimer()
    if disclaimer in current_content:
        logger.info("Disclaimer already present – skipping.")
        mark_update_processed(update_id)
        mark_message_processed(chat_id, message_id)
        return

    new_content = current_content + disclaimer

    # Edit text or caption
    if text is not None:
        result = edit_message(chat_id, message_id, new_content)
    else:
        result = edit_caption(chat_id, message_id, new_content)

    if result:
        logger.info(f"Disclaimer appended to {chat_id}/{message_id}")
        mark_update_processed(update_id)
        mark_message_processed(chat_id, message_id)
    else:
        logger.error(f"Failed to edit {chat_id}/{message_id}")

# ========== POLLING LOOP ==========
def polling_loop():
    offset = get_offset()
    logger.info(f"Polling loop started with offset={offset}")

    while True:
        try:
            payload = {
                "timeout": 30,
                "offset": offset,
                "allowed_updates": json.dumps(["message"])
            }
            url = f"{API_URL}/getUpdates"
            resp = requests.get(url, params=payload, timeout=35)
            if resp.status_code != 200:
                logger.error(f"getUpdates error: {resp.status_code}")
                time.sleep(5)
                continue
            data = resp.json()
            if not data.get('ok'):
                logger.error(f"getUpdates error: {data}")
                time.sleep(5)
                continue

            results = data.get('result', [])
            for update in results:
                update_id = update['update_id']
                if 'message' not in update:
                    if update_id >= offset:
                        offset = update_id + 1
                        set_offset(offset)
                    continue

                msg = update['message']
                chat_id = msg['chat']['id']
                message_id = msg['message_id']
                text = msg.get('text', '')
                caption = msg.get('caption', '')

                process_message(update_id, chat_id, message_id, text, caption)

                if update_id >= offset:
                    offset = update_id + 1
                    set_offset(offset)

            if results:
                last_update_id = results[-1]['update_id']
                if last_update_id >= offset:
                    offset = last_update_id + 1
                    set_offset(offset)

        except Exception as e:
            logger.error(f"Polling loop exception: {e}", exc_info=True)
            time.sleep(5)

# ========== FLASK HEALTH CHECK ==========
@app.route('/health')
def health():
    return "OK"

# ========== MAIN ==========
if __name__ == "__main__":
    init_db()
    call_telegram("deleteWebhook", drop_pending_updates=True)
    time.sleep(1)

    me = call_telegram("getMe")
    if not me:
        logger.error("Invalid bot token. Exiting.")
        sys.exit(1)
    logger.info(f"Bot @{me['username']} started.")

    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("Lock acquired – starting polling thread.")
        threading.Thread(target=polling_loop, daemon=True).start()
    except IOError:
        logger.warning("Lock file held – another instance is polling. Skipping polling.")
    except Exception as e:
        logger.error(f"Lock error: {e} – starting polling anyway.")
        threading.Thread(target=polling_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
