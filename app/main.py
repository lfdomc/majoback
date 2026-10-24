import json
import os
import threading
import re
import tempfile
from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import costing, models, schemas
from .auth import (crear_token, hash_clave, registrar_fallo, revisar_bloqueo, solo_admin,
                   usuario_actual, verificar_clave)
from .db import DB_PATH, Base, engine, get_db, migrar
from . import arte as arte_mod
from . import publicar
from .db import SessionLocal
from .imagenes import MEDIA_DIR, abrir, borrar_imagen, guardar_imagen, guardar_pil
from .seed import inicializar
from .units import ETIQUETAS, UNIDADES

Base.metadata.create_all(engine)
migrar()
inicializar()

app = FastAPI(title="Costeo de recetas", version="1.0")

origenes = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origenes, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])

Auth = Depends(usuario_actual)


def _guardar(db: Session, msg_dup: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, msg_dup)


# ------------------------------------------------------------------ sistema
@app.get("/api/salud")
def salud():
    return {"ok": True}


@app.get("/api/unidades")
def unidades():
    return [{"clave": k, "nombre": ETIQUETAS[k], "dimension": v[0], "factor": v[1]} for k, v in UNIDADES.items()]


# ------------------------------------------------------------------ auth
@app.post("/api/auth/ingresar")
def ingresar(datos: schemas.Login, request: Request, db: Session = Depends(get_db)):
    llave = f"{datos.usuario.lower()}|{request.client.host if request.client else ''}"
    revisar_bloqueo(llave)
    u = db.query(models.Usuario).filter(func.lower(models.Usuario.usuario) == datos.usuario.lower()).first()
    if not u or not u.activo or not verificar_clave(datos.clave, u.clave_hash):
        registrar_fallo(llave)
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    return {"token": crear_token(u), "usuario": _u(u)}


def _u(u: models.Usuario):
    return {"id": u.id, "usuario": u.usuario, "nombre": u.nombre, "es_admin": u.es_admin, "activo": u.activo}


@app.get("/api/auth/yo")
def yo(u=Auth):
    return _u(u)


@app.post("/api/auth/cambiar-clave")
def cambiar_clave(datos: schemas.CambioClave, u: models.Usuario = Auth, db: Session = Depends(get_db)):
    if not verificar_clave(datos.clave_actual, u.clave_hash):
        raise HTTPException(400, "La contraseña actual no coincide.")
    u.clave_hash = hash_clave(datos.clave_nueva)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ usuarios (admin)
@app.get("/api/usuarios")
def usuarios(_=Depends(solo_admin), db: Session = Depends(get_db)):
    return [_u(u) for u in db.query(models.Usuario).order_by(models.Usuario.usuario)]


@app.post("/api/usuarios")
def crear_usuario(d: schemas.UsuarioIn, _=Depends(solo_admin), db: Session = Depends(get_db)):
    if not d.clave:
        raise HTTPException(400, "Escriba una contraseña de al menos 6 caracteres.")
    u = models.Usuario(usuario=d.usuario.strip(), nombre=d.nombre, es_admin=d.es_admin, activo=d.activo,
                       clave_hash=hash_clave(d.clave))
    db.add(u)
    _guardar(db, "Ya existe un usuario con ese nombre.")
    return _u(u)


@app.put("/api/usuarios/{uid}")
def editar_usuario(uid: int, d: schemas.UsuarioIn, yo_: models.Usuario = Depends(solo_admin),
                   db: Session = Depends(get_db)):
    u = db.get(models.Usuario, uid) or _404("Usuario")
    if u.id == yo_.id and (not d.es_admin or not d.activo):
        raise HTTPException(400, "No puede quitarse a sí mismo el acceso de administrador.")
    u.usuario, u.nombre, u.es_admin, u.activo = d.usuario.strip(), d.nombre, d.es_admin, d.activo
    if d.clave:
        u.clave_hash = hash_clave(d.clave)
    _guardar(db, "Ya existe un usuario con ese nombre.")
    return _u(u)


@app.delete("/api/usuarios/{uid}")
def borrar_usuario(uid: int, yo_: models.Usuario = Depends(solo_admin), db: Session = Depends(get_db)):
    if uid == yo_.id:
        raise HTTPException(400, "No puede borrar su propio usuario.")
    u = db.get(models.Usuario, uid) or _404("Usuario")
    db.delete(u)
    db.commit()
    return {"ok": True}


def _404(que):
    raise HTTPException(404, f"{que} no encontrado.")


# ------------------------------------------------------------------ configuración
@app.get("/api/configuracion")
def ver_config(_=Auth, db: Session = Depends(get_db)):
    return costing.leer_config(db)


@app.put("/api/configuracion")
def guardar_config(d: schemas.ConfigIn, tareas: BackgroundTasks, _=Depends(solo_admin), db: Session = Depends(get_db)):
    for k, v in d.model_dump().items():
        val = ("1" if v else "0") if isinstance(v, bool) else str(v)
        fila = db.get(models.Configuracion, k)
        if fila:
            fila.valor = val
        else:
            db.add(models.Configuracion(clave=k, valor=val))
    db.commit()
    # El nombre del negocio es uno solo: el de Ajustes y el de «Menú y negocio» se mantienen iguales
    nombre = d.nombre_negocio.strip()
    negocio = leer_negocio(db)
    if nombre and negocio.get("nombre") != nombre:
        negocio["nombre"] = nombre
        _guardar_negocio(db, negocio)
        tareas.add_task(regenerar_todas)
    return costing.leer_config(db)


# ------------------------------------------------------------------ proveedores
@app.get("/api/proveedores")
def proveedores(_=Auth, db: Session = Depends(get_db)):
    usos = dict(db.query(models.Presentacion.proveedor_id, func.count()).group_by(models.Presentacion.proveedor_id).all())
    return [{"id": p.id, "nombre": p.nombre, "contacto": p.contacto, "notas": p.notas, "presentaciones": usos.get(p.id, 0)}
            for p in db.query(models.Proveedor).order_by(models.Proveedor.nombre)]


@app.post("/api/proveedores")
def crear_proveedor(d: schemas.ProveedorIn, _=Auth, db: Session = Depends(get_db)):
    p = models.Proveedor(**d.model_dump())
    db.add(p)
    _guardar(db, "Ya existe un proveedor con ese nombre.")
    return {"id": p.id, **d.model_dump()}


@app.put("/api/proveedores/{pid}")
def editar_proveedor(pid: int, d: schemas.ProveedorIn, _=Auth, db: Session = Depends(get_db)):
    p = db.get(models.Proveedor, pid) or _404("Proveedor")
    for k, v in d.model_dump().items():
        setattr(p, k, v)
    _guardar(db, "Ya existe un proveedor con ese nombre.")
    return {"id": p.id, **d.model_dump()}


@app.delete("/api/proveedores/{pid}")
def borrar_proveedor(pid: int, _=Auth, db: Session = Depends(get_db)):
    p = db.get(models.Proveedor, pid) or _404("Proveedor")
    db.delete(p)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ ingredientes
def _ing_respuesta(ing: dict, usos: int | None = None):
    c = costing.costos_ingrediente(ing)
    return {**ing, "presentaciones": c["presentaciones"], "costo": {k: c[k] for k in
            ("menor", "mayor", "promedio", "preferida", "seleccionado", "presentacion_usada", "error")},
            **({"usos": usos} if usos is not None else {})}


@app.get("/api/ingredientes")
def ingredientes(_=Auth, db: Session = Depends(get_db)):
    ctx = costing.cargar_contexto(db)
    usos = dict(db.query(models.RecetaItem.ingrediente_id, func.count(func.distinct(models.RecetaItem.receta_id)))
                .group_by(models.RecetaItem.ingrediente_id).all())
    return sorted([_ing_respuesta(i, usos.get(i["id"], 0)) for i in ctx.ingredientes.values()],
                  key=lambda x: x["nombre"].lower())


@app.get("/api/ingredientes/{iid}")
def ingrediente(iid: int, _=Auth, db: Session = Depends(get_db)):
    i = db.get(models.Ingrediente, iid) or _404("Ingrediente")
    recetas = db.query(models.Receta.id, models.Receta.nombre).join(
        models.RecetaItem, models.RecetaItem.receta_id == models.Receta.id).filter(
        models.RecetaItem.ingrediente_id == iid).distinct().all()
    return {**_ing_respuesta(costing.ing_dict(i)), "recetas": [{"id": r.id, "nombre": r.nombre} for r in recetas]}


@app.post("/api/ingredientes/calcular")
def calcular_ingrediente(d: schemas.IngredienteIn, _=Auth):
    """Vista previa del costo por unidad mínima mientras se edita."""
    data = d.model_dump()
    for n, p in enumerate(data["presentaciones"]):
        p["id"] = p.get("id") or -(n + 1)
        p["proveedor"] = None
    return _ing_respuesta({**data, "id": None})


def _aplicar_ingrediente(i: models.Ingrediente, d: schemas.IngredienteIn):
    for k in ("nombre", "categoria", "unidad_base", "peso_unidad_g", "densidad_g_ml", "base_costo", "notas"):
        setattr(i, k, getattr(d, k).strip() if isinstance(getattr(d, k), str) else getattr(d, k))
    actuales = {p.id: p for p in i.presentaciones}
    nuevas = []
    hay_pref = False
    for p in d.presentaciones:
        obj = actuales.get(p.id) if p.id else None
        obj = obj or models.Presentacion()
        for k in ("proveedor_id", "descripcion", "precio", "cantidad", "unidad", "merma_pct", "fecha"):
            setattr(obj, k, getattr(p, k))
        obj.preferida = p.preferida and not hay_pref
        hay_pref = hay_pref or obj.preferida
        nuevas.append(obj)
    i.presentaciones = nuevas
    i.actualizado = datetime.utcnow()


@app.post("/api/ingredientes")
def crear_ingrediente(d: schemas.IngredienteIn, _=Auth, db: Session = Depends(get_db)):
    i = models.Ingrediente()
    _aplicar_ingrediente(i, d)
    db.add(i)
    _guardar(db, "Ya existe un ingrediente con ese nombre.")
    return {"id": i.id}


@app.put("/api/ingredientes/{iid}")
def editar_ingrediente(iid: int, d: schemas.IngredienteIn, _=Auth, db: Session = Depends(get_db)):
    i = db.get(models.Ingrediente, iid) or _404("Ingrediente")
    _aplicar_ingrediente(i, d)
    _guardar(db, "Ya existe un ingrediente con ese nombre.")
    return {"id": i.id}


@app.delete("/api/ingredientes/{iid}")
def borrar_ingrediente(iid: int, _=Auth, db: Session = Depends(get_db)):
    i = db.get(models.Ingrediente, iid) or _404("Ingrediente")
    usan = db.query(models.Receta.nombre).join(models.RecetaItem, models.RecetaItem.receta_id == models.Receta.id)\
        .filter(models.RecetaItem.ingrediente_id == iid).distinct().all()
    if usan:
        raise HTTPException(409, "No se puede borrar: se usa en " + ", ".join(r.nombre for r in usan))
    db.delete(i)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ recetas
def _resumen(r: dict, ctx):
    try:
        c = costing.costo_receta(r, ctx)
    except costing.ErrorConversion as e:
        c = {"errores": [str(e)], "totales": {}, "costo_por_rinde": None, "precios": {}}
    p = c.get("precios", {})
    return {k: r[k] for k in ("id", "nombre", "tipo", "categoria", "rinde_cantidad", "rinde_unidad",
                              "precio_venta", "precio_competencia", "actualizado", "foto", "en_menu", "arte")} | {
        "n_items": len(r["items"]), "costo_total": c["totales"].get("seleccionado"),
        "costo_por_rinde": c.get("costo_por_rinde"), "precio_sugerido": p.get("precio_sugerido"),
        "margen_real_pct": p.get("margen_real_pct"), "ganancia": p.get("ganancia"),
        "diferencia_competencia": p.get("diferencia_competencia"), "errores": c["errores"]}


@app.get("/api/recetas")
def recetas(_=Auth, db: Session = Depends(get_db)):
    ctx = costing.cargar_contexto(db)
    return sorted([_resumen(r, ctx) for r in ctx.recetas.values()], key=lambda x: x["nombre"].lower())


@app.get("/api/recetas/{rid}")
def receta(rid: int, _=Auth, db: Session = Depends(get_db)):
    ctx = costing.cargar_contexto(db)
    r = ctx.recetas.get(rid) or _404("Receta")
    usada_en = [{"id": x["id"], "nombre": x["nombre"]} for x in ctx.recetas.values()
                if any(it["subreceta_id"] == rid for it in x["items"])]
    return {**r, "calculo": costing.costo_receta(r, ctx), "usada_en": usada_en}


@app.post("/api/recetas/calcular")
def calcular_receta(d: schemas.RecetaBorrador, _=Auth, db: Session = Depends(get_db)):
    """Calcula un borrador sin guardarlo (para la vista en vivo)."""
    ctx = costing.cargar_contexto(db)
    data = d.model_dump()
    if data.get("id") is not None:
        ctx.recetas[data["id"]] = data  # usar la versión en edición
    try:
        return costing.costo_receta({**data, "id": data.get("id")}, ctx)
    except costing.ErrorConversion as e:
        raise HTTPException(400, str(e))


def _aplicar_receta(r: models.Receta, d: schemas.RecetaIn, db: Session):
    for k in ("nombre", "tipo", "categoria", "rinde_cantidad", "rinde_unidad", "merma_pct",
              "margen_objetivo_pct", "precio_venta", "precio_competencia", "notas",
              "descripcion_menu", "en_menu"):
        v = getattr(d, k)
        setattr(r, k, v.strip() if isinstance(v, str) else v)
    r.items = [models.RecetaItem(orden=n, ingrediente_id=it.ingrediente_id if not it.subreceta_id else None,
                                 subreceta_id=it.subreceta_id, cantidad=it.cantidad, unidad=it.unidad,
                                 merma_pct=it.merma_pct)
               for n, it in enumerate(d.items) if it.ingrediente_id or it.subreceta_id]
    r.actualizado = datetime.utcnow()


@app.post("/api/recetas")
def crear_receta(d: schemas.RecetaIn, _=Auth, db: Session = Depends(get_db)):
    r = models.Receta()
    _aplicar_receta(r, d, db)
    db.add(r)
    _guardar(db, "Ya existe una receta con ese nombre.")
    return {"id": r.id}


@app.put("/api/recetas/{rid}")
def editar_receta(rid: int, d: schemas.RecetaIn, _=Auth, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid) or _404("Receta")
    if any(it.subreceta_id == rid for it in d.items):
        raise HTTPException(400, "Una receta no puede usarse a sí misma.")
    respaldo = costing.rec_dict(r)
    _aplicar_receta(r, d, db)
    _guardar(db, "Ya existe una receta con ese nombre.")
    ctx = costing.cargar_contexto(db)
    try:
        for x in ctx.recetas.values():
            costing.costo_receta(x, ctx)
    except costing.ErrorConversion as e:
        # revertir si se creó una referencia circular
        _aplicar_receta(r, schemas.RecetaIn(**{k: v for k, v in respaldo.items() if k not in ("id", "actualizado", "foto")}), db)
        db.commit()
        raise HTTPException(400, str(e))
    if r.foto and (respaldo["nombre"] != r.nombre or respaldo["precio_venta"] != r.precio_venta):
        generar_arte(db, r)
        db.commit()
    return {"id": r.id}


@app.post("/api/recetas/{rid}/duplicar")
def duplicar_receta(rid: int, _=Auth, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid) or _404("Receta")
    base = costing.rec_dict(r)
    nombre = f"{r.nombre} (copia)"
    n = 2
    while db.query(models.Receta).filter(models.Receta.nombre == nombre).first():
        nombre = f"{r.nombre} (copia {n})"
        n += 1
    nueva = models.Receta()
    _aplicar_receta(nueva, schemas.RecetaIn(**{**{k: v for k, v in base.items() if k not in ("id", "actualizado", "foto")},
                                              "nombre": nombre}), db)
    db.add(nueva)
    db.commit()
    return {"id": nueva.id}


@app.delete("/api/recetas/{rid}")
def borrar_receta(rid: int, _=Auth, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid) or _404("Receta")
    usan = db.query(models.Receta.nombre).join(models.RecetaItem, models.RecetaItem.receta_id == models.Receta.id)\
        .filter(models.RecetaItem.subreceta_id == rid).distinct().all()
    if usan:
        raise HTTPException(409, "No se puede borrar: se usa como sub-receta en " + ", ".join(x.nombre for x in usan))
    for img in (r.foto, r.recorte, r.arte):
        borrar_imagen(img)
    db.delete(r)
    db.commit()
    return {"ok": True}


@app.get("/api/recetas/{rid}/produccion")
def produccion(rid: int, cantidad: float, _=Auth, db: Session = Depends(get_db)):
    if cantidad <= 0:
        raise HTTPException(400, "La cantidad debe ser mayor que cero.")
    ctx = costing.cargar_contexto(db)
    r = ctx.recetas.get(rid) or _404("Receta")
    try:
        return costing.produccion(r, ctx, cantidad)
    except costing.ErrorConversion as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ resumen y respaldo
@app.get("/api/resumen")
def resumen(_=Auth, db: Session = Depends(get_db)):
    ctx = costing.cargar_contexto(db)
    sin_precio = [{"id": i["id"], "nombre": i["nombre"]} for i in ctx.ingredientes.values()
                  if costing.costos_ingrediente(i)["error"]]
    productos = [_resumen(r, ctx) for r in ctx.recetas.values() if r["tipo"] == "producto"]
    return {"ingredientes": len(ctx.ingredientes), "recetas": len(ctx.recetas),
            "productos": sorted(productos, key=lambda x: x["nombre"].lower()),
            "sin_precio": sorted(sin_precio, key=lambda x: x["nombre"].lower()),
            "config": ctx.config}


@app.get("/api/respaldo")
def respaldo(tareas: BackgroundTasks, _=Depends(solo_admin)):
    """Descarga una copia de la base de datos SQLite."""
    import sqlite3
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp.name)
    src.backup(dst)
    src.close()
    dst.close()
    tareas.add_task(os.remove, tmp.name)
    nombre = f"costeo-respaldo-{datetime.now():%Y%m%d-%H%M}.db"
    return FileResponse(tmp.name, filename=nombre, media_type="application/octet-stream")


# ------------------------------------------------------------------ fotos, negocio y menú público
@app.post("/api/recetas/{rid}/foto")
async def subir_foto(rid: int, archivo: UploadFile = File(...), _=Auth, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid) or _404("Receta")
    nombre = guardar_imagen(await archivo.read(), max_lado=1400)
    borrar_imagen(r.foto)
    r.foto = nombre
    _actualizar_recorte(r)
    generar_arte(db, r)
    db.commit()
    return {"foto": nombre, "arte": r.arte, "recortada": bool(r.recorte)}


def _actualizar_recorte(r: models.Receta):
    """Quita el fondo de la foto (si está activado) y guarda el recorte para reutilizarlo."""
    borrar_imagen(r.recorte)
    r.recorte = None
    if r.foto and r.quitar_fondo is not False:
        try:
            rec = arte_mod.recortar(abrir(r.foto))
        except Exception as e:
            print(f"No se pudo recortar la foto de {r.nombre}: {e}")
            rec = None
        if rec is not None:
            r.recorte = guardar_pil(rec, "WEBP", quality=90)


def generar_arte(db: Session, r: models.Receta, negocio: dict | None = None):
    """Compone la imagen tipo anuncio a partir de la foto, el nombre, el precio y el logo."""
    if not r.foto:
        borrar_imagen(r.arte)
        r.arte = None
        return
    negocio = negocio or leer_negocio(db)
    try:
        foto = abrir(r.foto)
        rec = abrir(r.recorte).convert("RGBA") if r.recorte else None
        modo = negocio.get("distintivo") or "logo"
        logo = abrir(negocio["logo"]) if modo == "logo" and negocio.get("logo") else None
        if modo == "logo" and logo is None:
            modo = "ninguno"  # sin imagen subida: se firma con el nombre escrito
        precio = r.precio_venta if negocio.get("mostrar_precios", True) else None
        img = arte_mod.componer(foto, rec, r.nombre, precio, negocio.get("color") or "#D94C72", logo,
                                negocio.get("nombre") or "", arte_mod.estilo_para(r.estilo_arte, r.id), r.id,
                                modo=modo, eslogan=negocio.get("eslogan") or "")
    except Exception as e:
        print(f"No se pudo generar la imagen de {r.nombre}: {e}")
        return
    borrar_imagen(r.arte)
    r.arte = guardar_pil(img, "JPEG", quality=88, optimize=True, progressive=True)


def regenerar_todas():
    """Vuelve a componer todas las imágenes (cuando cambia el logo, el color o el nombre del negocio)."""
    db = SessionLocal()
    try:
        negocio = leer_negocio(db)
        for r in db.query(models.Receta).filter(models.Receta.foto.isnot(None)).all():
            generar_arte(db, r, negocio)
            db.commit()
    finally:
        db.close()
    publicar.programar(_menu_actual, retraso=1.0)


@app.get("/api/arte/estilos")
def estilos_arte(_=Auth):
    return [{"clave": "auto", "nombre": "Automático"}] + [{"clave": k, "nombre": arte_mod.NOMBRES[k]} for k in arte_mod.ESTILOS]


@app.post("/api/recetas/{rid}/arte")
def rehacer_arte(rid: int, d: dict, _=Auth, db: Session = Depends(get_db)):
    """Cambia el estilo o el recorte de fondo y vuelve a generar la imagen."""
    r = db.get(models.Receta, rid) or _404("Receta")
    if not r.foto:
        raise HTTPException(400, "Primero suba una foto del producto.")
    estilo = d.get("estilo")
    if estilo is not None:
        if estilo != "auto" and estilo not in arte_mod.ESTILOS:
            raise HTTPException(400, "Estilo no válido.")
        r.estilo_arte = estilo
    if "quitar_fondo" in d and bool(d["quitar_fondo"]) != (r.quitar_fondo is not False):
        r.quitar_fondo = bool(d["quitar_fondo"])
        _actualizar_recorte(r)
    elif d.get("recortar_de_nuevo"):
        _actualizar_recorte(r)
    generar_arte(db, r)
    db.commit()
    return {"arte": r.arte, "estilo_arte": r.estilo_arte, "quitar_fondo": r.quitar_fondo, "recortada": bool(r.recorte)}


@app.patch("/api/recetas/{rid}/menu")
def cambiar_en_menu(rid: int, d: dict, _=Auth, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid) or _404("Receta")
    r.en_menu = bool(d.get("en_menu"))
    db.commit()
    return {"en_menu": r.en_menu}


@app.patch("/api/recetas/{rid}/precios")
def cambiar_precios(rid: int, d: schemas.PreciosIn, _=Auth, db: Session = Depends(get_db)):
    """Cambia rápido el precio de venta o el de la competencia desde la lista."""
    r = db.get(models.Receta, rid) or _404("Receta")
    datos = d.model_dump(exclude_unset=True)
    cambio_venta = "precio_venta" in datos and datos["precio_venta"] != r.precio_venta
    for k, v in datos.items():
        setattr(r, k, v)
    db.commit()
    if cambio_venta and r.foto:
        generar_arte(db, r)
        db.commit()
    ctx = costing.cargar_contexto(db)
    return _resumen(ctx.recetas[rid], ctx)


@app.delete("/api/recetas/{rid}/foto")
def quitar_foto(rid: int, _=Auth, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid) or _404("Receta")
    for img in (r.foto, r.recorte, r.arte):
        borrar_imagen(img)
    r.foto = r.recorte = r.arte = None
    db.commit()
    return {"ok": True}


def leer_negocio(db: Session) -> dict:
    fila = db.get(models.Configuracion, "negocio")
    datos = schemas.NegocioIn().model_dump()
    if fila:
        try:
            datos.update(json.loads(fila.valor))
        except ValueError:
            pass
    if not datos.get("nombre"):
        datos["nombre"] = costing.leer_config(db)["nombre_negocio"]
    datos.setdefault("logo", None)
    return datos


def _guardar_negocio(db: Session, datos: dict):
    fila = db.get(models.Configuracion, "negocio")
    texto = json.dumps(datos, ensure_ascii=False)
    if fila:
        fila.valor = texto
    else:
        db.add(models.Configuracion(clave="negocio", valor=texto))
    nombre = db.get(models.Configuracion, "nombre_negocio")
    if datos.get("nombre"):
        if nombre:
            nombre.valor = datos["nombre"]
        else:
            db.add(models.Configuracion(clave="nombre_negocio", valor=datos["nombre"]))
    db.commit()


@app.get("/api/negocio")
def ver_negocio(_=Auth, db: Session = Depends(get_db)):
    return leer_negocio(db)


@app.put("/api/negocio")
def guardar_negocio(d: schemas.NegocioIn, tareas: BackgroundTasks, _=Depends(solo_admin), db: Session = Depends(get_db)):
    antes = leer_negocio(db)
    datos = {**antes, **d.model_dump()}
    _guardar_negocio(db, datos)
    if any(antes.get(k) != datos.get(k) for k in ("color", "nombre", "mostrar_precios", "distintivo", "eslogan")):
        tareas.add_task(regenerar_todas)
    return datos


@app.post("/api/negocio/logo")
async def subir_logo(tareas: BackgroundTasks, archivo: UploadFile = File(...), _=Depends(solo_admin), db: Session = Depends(get_db)):
    datos = leer_negocio(db)
    nombre = guardar_imagen(await archivo.read(), max_lado=600, transparencia=True)
    borrar_imagen(datos.get("logo"))
    datos["logo"] = nombre
    _guardar_negocio(db, datos)
    tareas.add_task(regenerar_todas)
    return datos


@app.delete("/api/negocio/logo")
def quitar_logo(tareas: BackgroundTasks, _=Depends(solo_admin), db: Session = Depends(get_db)):
    datos = leer_negocio(db)
    borrar_imagen(datos.get("logo"))
    datos["logo"] = None
    _guardar_negocio(db, datos)
    tareas.add_task(regenerar_todas)
    return datos


@app.get("/api/publico/menu")
def menu_publico(db: Session = Depends(get_db)):
    """Menú para clientes: sin costos ni márgenes, solo lo que se marcó para mostrar."""
    return armar_menu(db)


def armar_menu(db: Session) -> dict:
    negocio = leer_negocio(db)
    recetas = db.query(models.Receta).filter(models.Receta.tipo == "producto", models.Receta.en_menu.is_(True))\
        .order_by(models.Receta.categoria, models.Receta.nombre).all()
    cats: dict[str, list] = {}
    for r in recetas:
        cats.setdefault(r.categoria or "Otros", []).append({
            "id": r.id, "nombre": r.nombre, "descripcion": r.descripcion_menu or "",
            "precio": r.precio_venta if negocio.get("mostrar_precios", True) else None,
            "foto": f"/media/{r.arte or r.foto}" if r.foto else None, "es_arte": bool(r.arte)})
    publico = {k: v for k, v in negocio.items() if k != "mostrar_precios"}
    publico["logo"] = f"/media/{negocio['logo']}" if negocio.get("logo") else None
    return {"negocio": publico, "categorias": [{"nombre": k, "productos": v} for k, v in cats.items()]}


@app.get("/media/{nombre}")
def media(nombre: str):
    if not re.fullmatch(r"[0-9a-f]{32}\.(webp|png|jpg)", nombre):
        raise HTTPException(404, "Imagen no encontrada.")
    ruta = os.path.join(MEDIA_DIR, nombre)
    if not os.path.exists(ruta):
        raise HTTPException(404, "Imagen no encontrada.")
    return FileResponse(ruta, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _menu_actual() -> dict:
    db = SessionLocal()
    try:
        return armar_menu(db)
    finally:
        db.close()


@app.get("/api/publicacion")
def estado_publicacion(_=Auth):
    return publicar.info()


@app.post("/api/publicacion")
def publicar_menu(_=Auth):
    """Publica el menú ya mismo (además de la publicación automática después de cada cambio)."""
    return publicar.publicar_ahora(_menu_actual)


NO_PUBLICAN = ("/api/auth", "/api/recetas/calcular", "/api/publicacion", "/api/produccion")


@app.middleware("http")
async def _publicar_despues_de_cambios(request: Request, call_next):
    resp = await call_next(request)
    ruta = request.url.path
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and ruta.startswith("/api/")
            and resp.status_code < 400 and not ruta.startswith(NO_PUBLICAN)):
        publicar.programar(_menu_actual)
    return resp


def _actualizar_imagenes_si_cambio_el_diseno():
    """Si el diseño de las imágenes cambió desde la última vez, las rehace todas (en segundo plano)."""
    db = SessionLocal()
    try:
        fila = db.get(models.Configuracion, "version_arte")
        if fila and fila.valor == arte_mod.VERSION_ARTE:
            return
        if fila:
            fila.valor = arte_mod.VERSION_ARTE
        else:
            db.add(models.Configuracion(clave="version_arte", valor=arte_mod.VERSION_ARTE))
        db.commit()
    finally:
        db.close()
    print("Diseño de imágenes actualizado: regenerando las imágenes de los productos…")
    regenerar_todas()


def _tareas_al_arrancar():
    _actualizar_imagenes_si_cambio_el_diseno()
    if publicar.activo() and not publicar.estado["ultima"]:
        publicar.publicar_ahora(_menu_actual)


@app.on_event("startup")
def _al_arrancar():
    threading.Thread(target=_tareas_al_arrancar, daemon=True).start()
