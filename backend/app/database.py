"""Conexiones transaccionales que liberan el archivo al salir del contexto."""

import sqlite3


class Connection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execute("PRAGMA busy_timeout=5000")
        self.execute("PRAGMA journal_mode=WAL")

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()
