"""Conexiones transaccionales que liberan el archivo al salir del contexto."""

import sqlite3


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()
