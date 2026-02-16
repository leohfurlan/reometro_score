import os, sqlite3
from datetime import datetime

DB_NAME = "users_reoscore.db"

def _db_path():
    if os.path.exists(os.path.join("instance", DB_NAME)):
        return os.path.join("instance", DB_NAME)
    return DB_NAME

def get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn

def ensure_schema():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS aprendizado_local (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chave_original TEXT UNIQUE NOT NULL,
        lote_novo TEXT NOT NULL,
        massa_nova TEXT NOT NULL,
        usuario_log TEXT,
        data_log TEXT
    )
    """)
    conn.commit()
    conn.close()

def upsert(chave_original, lote_novo, massa_nova, usuario=None):
    ensure_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO aprendizado_local (chave_original, lote_novo, massa_nova, usuario_log, data_log)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(chave_original) DO UPDATE SET
        lote_novo=excluded.lote_novo,
        massa_nova=excluded.massa_nova,
        usuario_log=excluded.usuario_log,
        data_log=excluded.data_log
    """, (
        str(chave_original).strip().upper(),
        str(lote_novo).strip().upper(),
        str(massa_nova).strip().upper(),
        (usuario or "").strip(),
        datetime.now().isoformat(timespec="seconds"),
    ))
    conn.commit()
    conn.close()

def load_map():
    ensure_schema()
    conn = get_conn()
    rows = conn.execute("SELECT chave_original, lote_novo, massa_nova FROM aprendizado_local").fetchall()
    conn.close()
    return {r["chave_original"].strip().upper(): {"lote_real": r["lote_novo"], "massa": r["massa_nova"]} for r in rows}
