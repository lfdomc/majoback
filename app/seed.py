"""Crea el usuario administrador y carga los datos iniciales la primera vez."""
import json
import os
import secrets
from datetime import date
from pathlib import Path

from . import models
from .auth import hash_clave
from .db import SessionLocal

SEED = Path(__file__).parent / "seed_data" / "seed.json"


def inicializar():
    db = SessionLocal()
    try:
        _admin(db)
        if os.getenv("CARGAR_DATOS_INICIALES", "si").lower() in ("si", "sí", "1", "true", "yes") \
                and db.query(models.Ingrediente).count() == 0 and SEED.exists():
            cargar(db, json.loads(SEED.read_text(encoding="utf-8")))
            print("Datos iniciales cargados.")
    finally:
        db.close()


def _admin(db):
    if db.query(models.Usuario).count():
        return
    usuario = os.getenv("ADMIN_USUARIO", "admin")
    clave = os.getenv("ADMIN_CLAVE")
    if not clave:
        clave = secrets.token_urlsafe(9)
        print(f"*** Usuario administrador creado: {usuario} / {clave}  (defina ADMIN_CLAVE para elegirla) ***")
    db.add(models.Usuario(usuario=usuario, nombre="Administrador", es_admin=True, clave_hash=hash_clave(clave)))
    db.commit()


def cargar(db, data):
    provs = {}
    for n in data["proveedores"]:
        p = models.Proveedor(nombre=n)
        db.add(p)
        provs[n] = p
    db.flush()
    ings = {}
    for i in data["ingredientes"]:
        obj = models.Ingrediente(nombre=i["nombre"], categoria=i["categoria"], unidad_base=i["unidad_base"],
                                 peso_unidad_g=i.get("peso_unidad_g"), densidad_g_ml=i.get("densidad_g_ml"),
                                 base_costo="preferida", notas=i.get("notas", ""))
        for p in i["presentaciones"]:
            obj.presentaciones.append(models.Presentacion(
                proveedor_id=provs[p["proveedor"]].id if p.get("proveedor") in provs else None,
                descripcion=p.get("descripcion", ""), precio=p["precio"], cantidad=p["cantidad"],
                unidad=p["unidad"], merma_pct=p.get("merma_pct", 0), preferida=p.get("preferida", False),
                fecha=date.fromisoformat(p["fecha"]) if p.get("fecha") else None))
        db.add(obj)
        ings[i["nombre"].lower()] = obj
    db.flush()
    recs = {}
    for r in data["recetas"]:
        obj = models.Receta(nombre=r["nombre"], tipo=r["tipo"], categoria=r.get("categoria", "General"),
                            rinde_cantidad=r["rinde_cantidad"], rinde_unidad=r["rinde_unidad"],
                            precio_competencia=r.get("precio_competencia"), precio_venta=r.get("precio_venta"),
                            notas=r.get("notas", ""), descripcion_menu=r.get("descripcion_menu", ""),
                            en_menu=r.get("en_menu", False))
        db.add(obj)
        recs[r["nombre"].lower()] = obj
    db.flush()
    for r in data["recetas"]:
        obj = recs[r["nombre"].lower()]
        for n, it in enumerate(r["items"]):
            ref = ings if it["tipo"] == "ingrediente" else recs
            destino = ref.get(it["nombre"].lower())
            if not destino:
                continue
            obj.items.append(models.RecetaItem(
                orden=n, cantidad=it["cantidad"], unidad=it["unidad"], merma_pct=it.get("merma_pct", 0),
                ingrediente_id=destino.id if it["tipo"] == "ingrediente" else None,
                subreceta_id=destino.id if it["tipo"] == "receta" else None))
    db.commit()
