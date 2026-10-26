import json
import os
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet

ROOT = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent.parent / '.local'))


def connect():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(ROOT / 'app.db', timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    return db


def init():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, profile TEXT NOT NULL, tokens TEXT, google_sub TEXT);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, user_id TEXT, csrf TEXT NOT NULL, state TEXT, verifier TEXT, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS achievements(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, data TEXT NOT NULL, event_id TEXT, etag TEXT, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, achievement_id TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, role TEXT, text TEXT);
        CREATE TABLE IF NOT EXISTS demo_events(user_id TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(user_id,id));
        ''')
    os.chmod(ROOT / 'app.db', 0o600)


def cipher():
    path = ROOT / 'token.key'
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


def encode_tokens(tokens):
    return cipher().encrypt(json.dumps(tokens).encode()).decode()


def decode_tokens(value):
    return json.loads(cipher().decrypt(value.encode()))
