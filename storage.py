"""
Where your data lives.

    FileStorage      - JSON files on your own computer (used when you run it locally)
    PostgresStorage  - a Postgres database online (used when the app is deployed)

Both keep two small documents:
    "ledger"  - all accounts and journal entries (the same JSON your terminal program uses)
    "pin"     - the accountant PIN (salted hash only, never the PIN itself)
"""
import os


class ConflictError(Exception):
    """The data was changed somewhere else (another phone/laptop) after this page loaded it."""


class FileStorage:
    name = "files on this computer"
    local = True

    def __init__(self, paths):
        self.paths = paths  # {"ledger": path, "pin": path}

    def read(self, doc):
        path = self.paths[doc]
        if not os.path.exists(path):
            return None, 0
        with open(path, encoding="utf-8") as f:
            return f.read(), 0

    def write(self, doc, text, expected_version=0):
        path = self.paths[doc]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return 0


class PostgresStorage:
    name = "online database"
    local = False

    def __init__(self, url):
        self.url = url
        self._ready = False

    def _connect(self):
        import psycopg
        conn = psycopg.connect(self.url, connect_timeout=30)
        if not self._ready:
            conn.execute("""CREATE TABLE IF NOT EXISTS app_docs (
                                name       text PRIMARY KEY,
                                body       text NOT NULL,
                                version    integer NOT NULL DEFAULT 1,
                                updated_at timestamptz NOT NULL DEFAULT now())""")
            conn.commit()
            self._ready = True
        return conn

    def read(self, doc):
        with self._connect() as conn:  # commits and closes the connection on exit
            row = conn.execute("SELECT body, version FROM app_docs WHERE name = %s", (doc,)).fetchone()
        return (row[0], row[1]) if row else (None, 0)

    def write(self, doc, text, expected_version=0):
        """Saves only if nobody else saved in between (compares version numbers)."""
        with self._connect() as conn:
            if expected_version == 0:
                row = conn.execute(
                    "INSERT INTO app_docs (name, body, version) VALUES (%s, %s, 1) "
                    "ON CONFLICT (name) DO NOTHING RETURNING version", (doc, text)).fetchone()
            else:
                row = conn.execute(
                    "UPDATE app_docs SET body = %s, version = version + 1, updated_at = now() "
                    "WHERE name = %s AND version = %s RETURNING version",
                    (text, doc, expected_version)).fetchone()
        if row is None:
            raise ConflictError("The data was changed on another device. Please reload the page.")
        return row[0]
