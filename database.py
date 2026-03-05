import sqlite3

def init_db():
    conn = sqlite3.connect("drowsiness.db")
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT,
        role TEXT,
        is_verified INTEGER DEFAULT 0,
        is_approved INTEGER DEFAULT 0
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        date_time TEXT,
        status TEXT
    )
    """)

    conn.commit()
    conn.close()
