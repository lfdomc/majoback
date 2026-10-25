import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# En Railway: DATABASE_PATH=/data/costeo.db (el volumen se monta en /data)
DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "data" / "costeo.db"))
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columnas agregadas después de la primera versión: se crean solas en bases existentes.
COLUMNAS_NUEVAS = {
    "recetas": {
        "foto": "VARCHAR(120)",
        "descripcion_menu": "TEXT DEFAULT ''",
        "en_menu": "BOOLEAN DEFAULT 0",
        "arte": "VARCHAR(120)",
        "recorte": "VARCHAR(120)",
        "arte_en": "VARCHAR(120)",
        "estilo_arte": "VARCHAR(12) DEFAULT 'auto'",
        "quitar_fondo": "BOOLEAN DEFAULT 1",
        "nombre_en": "VARCHAR(160) DEFAULT ''",
        "descripcion_menu_en": "TEXT DEFAULT ''",
    },
}


def migrar():
    with engine.begin() as con:
        for tabla, cols in COLUMNAS_NUEVAS.items():
            existentes = {r[1] for r in con.exec_driver_sql(f"PRAGMA table_info({tabla})")}
            for col, tipo in cols.items():
                if existentes and col not in existentes:
                    con.exec_driver_sql(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
