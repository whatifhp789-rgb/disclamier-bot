#!/usr/bin/env python3
# disclaimer_bot.py – Educational Disclaimer Bot (Single-Instance Polling)

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
BOT_TOKEN = "8845364296:AAEp8LIWzferAhwXlfNUIyRKY7u_YYnbwPk"  # Your token (keep as is)
DB_FILE = "disclaimer.db"
LOCK_FILE = "bot.lock"
DISCLAIMER_TEXT = "**⚠️ Disclaimer: This content is only for educational purposes.**"  # <-- BOLD full text
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
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

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
            logger.error(f"Telegram API error: {resp.status_code} - {resp.text}")
            return None
        data = resp.json()
        if not data.get('ok'):
            logger.error(f"Telegram API error: {data}")
            return None
        return data.get('result')
    except Exception as e:
        logger.error(f"Telegram call exception: {e}")
        return None

def send_reply(chat_id, reply_to_message_id, text):
    return call_telegram(
        "sendMessage",
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        text=text,
        parse_mode="Markdown"
    )

# ========== MESSAGE PROCESSOR ==========
def process_message(update_id, chat_id, message_id, text):
    logger.info(f"Processing message: update_id={update_id}, chat={chat_id}, msg={message_id}")

    if is_update_processed(update_id):
        logger.info(f"Update {update_id} already processed – skipping.")
        return

    if is_message_processed(chat_id, message_id):
        logger.info(f"Message {chat_id}/{message_id} already processed – skipping.")
        mark_update_processed(update_id)
        return

    if text and DISCLAIMER_TEXT in text:
        logger.info("Disclaimer already present in original message – skipping.")
        mark_update_processed(update_id)
        mark_message_processed(chat_id, message_id)
        return

    result = send_reply(chat_id, message_id, DISCLAIMER_TEXT)
    if result:
        logger.info(f"Disclaimer sent for message {chat_id}/{message_id}")
        mark_update_processed(update_id)
        mark_message_processed(chat_id, message_id)
    else:
        logger.error(f"Failed to send disclaimer for message {chat_id}/{message_id}")

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

                process_message(update_id, chat_id, message_id, text)

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
