import os
import sys
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

os.chdir(os.path.join(os.path.dirname(__file__), '..'))
conn = sqlite3.connect('instance/nest.sqlite3')
cur = conn.cursor()

# Backfill created_at
cur.execute("SELECT id, created_at FROM users WHERE created_at IS NOT NULL")
for row in cur.fetchall():
    try:
        dt = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
        conn.execute("INSERT OR IGNORE INTO daily_active_users (user_id, active_date) VALUES (?, ?)", (row[0], dt.strftime('%Y-%m-%d')))
    except:
        logger.exception("Failed to backfill created_at activity for user %s", row[0])

# Backfill last_login
cur.execute("SELECT id, last_login FROM users WHERE last_login IS NOT NULL")
for row in cur.fetchall():
    try:
        dt = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
        conn.execute("INSERT OR IGNORE INTO daily_active_users (user_id, active_date) VALUES (?, ?)", (row[0], dt.strftime('%Y-%m-%d')))
    except:
        logger.exception("Failed to backfill last_login activity for user %s", row[0])

conn.commit()
conn.close()
