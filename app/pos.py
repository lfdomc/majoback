"""Punto de venta (POS): catálogo, registrar ventas (también las que se hicieron sin internet), anular,
cierre de caja y resumen de ventas con la ganancia real."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from . import categorias as categorias_mod
from . import costing, models
from .auth import usuario_actual
from .db import get_db

router = APIRouter(prefix="/api/pos")
CR = timezone(timedelta(hours=-6))  # Costa Rica no usa horario de verano
DIAS_MES = 365 / 12


def ahora_cr() -> datetime:
    return datetime.now(CR).replace(tzinfo=None)


def hoy_cr() -> date:
    return ahora_cr().date()


# ------------------------------------------------------------------ catálogo
@router.get("/catalogo")
def catalogo(_=Depends(usuario_actual), db: Session = Depends(get_db)):
    """Productos con precio, por categoría, en el orden de Categorías. El POS lo guarda para usarlo sin internet."""
    from .main import leer_negocio  # evitar import circular
    ctx = costing.cargar_contexto(db)
    neg = leer_negocio(db)
    prods = [r for r in ctx.recetas.values() if r["tipo"] == "producto" and r.get("precio_venta")]
    orden = {c["nombre"]: i for i, c in enumerate(categorias_mod.ordenadas(db, "producto"))}
    cats: dict[str, list] = defaultdict(list)
    for r in sorted(prods, key=lambda r: (not r.get("en_menu"), r["nombre"].lower())):
        cats[r["categoria"]].append({"id": r["id"], "nombre": r["nombre"], "precio": r["precio_venta"], "en_menu": bool(r.get("en_menu"))})
    claves = sorted(cats, key=lambda k: (orden.get(k, len(orden)), k.lower()))
    return {
        "negocio": {"nombre": neg.get("nombre") or "", "color": neg.get("color") or "#D94C72",
                    "sinpe": neg.get("sinpe") or neg.get("whatsapp") or neg.get("telefono") or "",
                    "sinpe_nombre": neg.get("sinpe_nombre") or ""},
        "iva_incluido": ctx.config["aplicar_iva"], "iva_pct": ctx.config["iva_pct"],
        "categorias": [{"nombre": k, "productos": cats[k]} for k in claves],
        "actualizado": ahora_cr().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ ventas
class ItemIn(BaseModel):
    receta_id: int
    cantidad: float = Field(gt=0, le=10000)
    precio_unitario: float = Field(ge=0, le=10_000_000)


class VentaIn(BaseModel):
    uid: str = Field(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9-]+$")
    fecha: datetime | None = None
    forma_pago: Literal["efectivo", "sinpe"] = "efectivo"
    items: list[ItemIn] = Field(min_length=1, max_length=200)
    recibido: float | None = Field(default=None, ge=0)
    notas: str = Field(default="", max_length=400)


class LoteIn(BaseModel):
    ventas: list[VentaIn] = Field(max_length=500)


def _venta_dict(v: models.Venta) -> dict:
    return {"id": v.id, "uid": v.uid, "fecha": v.fecha.isoformat(timespec="seconds"), "dia": v.dia.isoformat(),
            "forma_pago": v.forma_pago, "total": v.total, "total_sin_iva": v.total_sin_iva, "recibido": v.recibido,
            "vuelto": v.vuelto, "costo_ingredientes": v.costo_ingredientes, "costo_variable": v.costo_variable,
            "ganancia_bruta": v.total_sin_iva - v.costo_ingredientes - v.costo_variable, "notas": v.notas,
            "anulada": v.anulada,
            "items": [{"receta_id": i.receta_id, "nombre": i.nombre, "cantidad": i.cantidad, "precio_unitario": i.precio_unitario,
                       "costo_unitario": i.costo_unitario} for i in v.items]}


def _registrar(d: VentaIn, usuario: models.Usuario, db: Session, ctx) -> tuple[models.Venta, bool]:
    existente = db.query(models.Venta).filter(models.Venta.uid == d.uid).first()
    if existente:
        return existente, False
    cfg = ctx.config
    iva = cfg["iva_pct"] / 100 if cfg["aplicar_iva"] else 0
    g = cfg["negocio"]
    if d.fecha is None:
        fecha = ahora_cr()
    elif d.fecha.tzinfo is not None:
        fecha = d.fecha.astimezone(CR).replace(tzinfo=None)
    else:
        fecha = d.fecha
    if fecha > ahora_cr() + timedelta(minutes=10):  # reloj del teléfono adelantado
        fecha = ahora_cr()
    v = models.Venta(uid=d.uid, fecha=fecha, dia=fecha.date(), usuario_id=usuario.id, forma_pago=d.forma_pago,
                     notas=d.notas.strip())
    total = costo = unidades = 0.0
    for it in d.items:
        r = ctx.recetas.get(it.receta_id)
        if not r:
            raise HTTPException(400, f"El producto {it.receta_id} ya no existe. Actualice el catálogo.")
        try:
            cu = costing.costo_receta(r, ctx).get("costo_por_rinde") or 0.0
        except costing.ErrorConversion:
            cu = 0.0
        v.items.append(models.VentaItem(receta_id=r["id"], nombre=r["nombre"], categoria=r["categoria"], cantidad=it.cantidad,
                                        precio_unitario=it.precio_unitario, costo_unitario=cu))
        total += it.cantidad * it.precio_unitario
        costo += it.cantidad * cu
        unidades += it.cantidad
    v.total = round(total, 2)
    v.total_sin_iva = total / (1 + iva)
    v.costo_ingredientes = costo
    v.costo_variable = g["variable_unidad"] * unidades + v.total_sin_iva * g["variable_pct"]
    if d.forma_pago == "efectivo" and d.recibido is not None:
        v.recibido = d.recibido
        v.vuelto = max(0.0, d.recibido - total)
    db.add(v)
    db.flush()
    return v, True


@router.post("/ventas")
def registrar_venta(d: VentaIn, usuario=Depends(usuario_actual), db: Session = Depends(get_db)):
    ctx = costing.cargar_contexto(db)
    v, nueva = _registrar(d, usuario, db, ctx)
    db.commit()
    return {**_venta_dict(v), "nueva": nueva}


@router.post("/ventas/lote")
def registrar_lote(d: LoteIn, usuario=Depends(usuario_actual), db: Session = Depends(get_db)):
    """Ventas guardadas en el teléfono mientras no había internet. Cada una se guarda una sola vez (por su uid)."""
    ctx = costing.cargar_contexto(db)
    resultados = []
    for venta in d.ventas:
        try:
            v, nueva = _registrar(venta, usuario, db, ctx)
            db.commit()
            resultados.append({"uid": venta.uid, "ok": True, "id": v.id, "nueva": nueva})
        except HTTPException as e:
            db.rollback()
            resultados.append({"uid": venta.uid, "ok": False, "error": e.detail})
    return {"resultados": resultados}


@router.get("/ventas")
def listar_ventas(dia: date | None = None, _=Depends(usuario_actual), db: Session = Depends(get_db)):
    dia = dia or hoy_cr()
    ventas = db.query(models.Venta).options(selectinload(models.Venta.items)).filter(models.Venta.dia == dia)\
        .order_by(models.Venta.fecha.desc()).all()
    return [_venta_dict(v) for v in ventas]


@router.post("/ventas/{vid}/anular")
def anular(vid: int, d: dict | None = None, _=Depends(usuario_actual), db: Session = Depends(get_db)):
    v = db.get(models.Venta, vid)
    if not v:
        raise HTTPException(404, "Venta no encontrada.")
    v.anulada = not bool((d or {}).get("restaurar"))
    db.commit()
    return _venta_dict(v)


# ------------------------------------------------------------------ resumen
def rango(periodo: str, fecha: date) -> tuple[date, date]:
    if periodo == "dia":
        return fecha, fecha
    if periodo == "semana":
        ini = fecha - timedelta(days=fecha.weekday())
        return ini, ini + timedelta(days=6)
    if periodo == "mes":
        ini = fecha.replace(day=1)
        sig = (ini + timedelta(days=32)).replace(day=1)
        return ini, sig - timedelta(days=1)
    raise HTTPException(400, "Periodo no válido.")


def resumen_ventas(db: Session, desde: date, hasta: date, periodo: str = "rango") -> dict:
    ctx = costing.cargar_contexto(db)
    g = ctx.config["negocio"]
    ventas = db.query(models.Venta).options(selectinload(models.Venta.items))\
        .filter(models.Venta.dia >= desde, models.Venta.dia <= hasta, models.Venta.anulada.is_(False)).all()
    tot = {"total": 0.0, "sin_iva": 0.0, "ingredientes": 0.0, "variables": 0.0, "ventas": len(ventas), "unidades": 0.0}
    por_pago: dict[str, dict] = defaultdict(lambda: {"ventas": 0, "total": 0.0})
    por_prod: dict[str, dict] = defaultdict(lambda: {"cantidad": 0.0, "total": 0.0, "ganancia_bruta": 0.0, "categoria": ""})
    por_dia: dict[str, dict] = defaultdict(lambda: {"total": 0.0, "ganancia_bruta": 0.0, "ventas": 0})
    por_hora: dict[int, float] = defaultdict(float)
    for v in ventas:
        tot["total"] += v.total
        tot["sin_iva"] += v.total_sin_iva
        tot["ingredientes"] += v.costo_ingredientes
        tot["variables"] += v.costo_variable
        gb = v.total_sin_iva - v.costo_ingredientes - v.costo_variable
        por_pago[v.forma_pago]["ventas"] += 1
        por_pago[v.forma_pago]["total"] += v.total
        dd = por_dia[v.dia.isoformat()]
        dd["total"] += v.total
        dd["ganancia_bruta"] += gb
        dd["ventas"] += 1
        por_hora[v.fecha.hour] += v.total
        factor_iva = (v.total_sin_iva / v.total) if v.total else 1
        prop_var = (v.costo_variable / v.total_sin_iva) if v.total_sin_iva else 0
        for i in v.items:
            sub = i.cantidad * i.precio_unitario
            p = por_prod[i.nombre]
            p["categoria"] = i.categoria
            p["cantidad"] += i.cantidad
            p["total"] += sub
            p["ganancia_bruta"] += sub * factor_iva * (1 - prop_var) - i.cantidad * i.costo_unitario
            tot["unidades"] += i.cantidad
    ganancia_bruta = tot["sin_iva"] - tot["ingredientes"] - tot["variables"]
    dias = (hasta - desde).days + 1
    if periodo == "dia":
        fijos = g["fijos_mes"] / (ctx.config["dias_trabajo_mes"] or 26)
    elif periodo == "mes":
        fijos = g["fijos_mes"]
    else:
        fijos = g["fijos_mes"] * dias / DIAS_MES
    margen = ganancia_bruta / tot["sin_iva"] if tot["sin_iva"] else None
    equilibrio_mes = g["fijos_mes"] / margen if margen and margen > 0 else None
    return {
        "desde": desde.isoformat(), "hasta": hasta.isoformat(), "periodo": periodo,
        "ventas": tot["ventas"], "unidades": tot["unidades"], "total": tot["total"], "total_sin_iva": tot["sin_iva"],
        "ingredientes": tot["ingredientes"], "variables": tot["variables"], "ganancia_bruta": ganancia_bruta,
        "margen_bruto_pct": margen * 100 if margen is not None else None,
        "fijos_periodo": fijos, "ganancia_real": ganancia_bruta - fijos, "fijos_mes": g["fijos_mes"],
        "ticket_promedio": tot["total"] / tot["ventas"] if tot["ventas"] else None,
        "equilibrio_mes": equilibrio_mes,
        "por_pago": [{"forma_pago": k, **v} for k, v in sorted(por_pago.items())],
        "por_producto": sorted(({"nombre": k, **v} for k, v in por_prod.items()), key=lambda x: -x["total"]),
        "por_dia": [{"dia": (desde + timedelta(days=k)).isoformat(), **por_dia.get((desde + timedelta(days=k)).isoformat(),
                     {"total": 0.0, "ganancia_bruta": 0.0, "ventas": 0})} for k in range(min(dias, 62))],
        "por_hora": [{"hora": h, "total": por_hora.get(h, 0.0)} for h in range(24)],
    }


@router.get("/resumen")
def resumen(periodo: Literal["dia", "semana", "mes"] = "dia", fecha: date | None = None,
            _=Depends(usuario_actual), db: Session = Depends(get_db)):
    desde, hasta = rango(periodo, fecha or hoy_cr())
    return resumen_ventas(db, desde, hasta, periodo)


# ------------------------------------------------------------------ caja
class CierreIn(BaseModel):
    dia: date | None = None
    fondo_inicial: float = Field(default=0, ge=0)
    efectivo_contado: float = Field(ge=0)
    notas: str = Field(default="", max_length=400)


def _caja(db: Session, dia: date) -> dict:
    ventas = db.query(models.Venta).filter(models.Venta.dia == dia, models.Venta.anulada.is_(False)).all()
    efectivo = sum(v.total for v in ventas if v.forma_pago == "efectivo")
    sinpe = sum(v.total for v in ventas if v.forma_pago == "sinpe")
    cierres = db.query(models.CierreCaja).filter(models.CierreCaja.dia == dia).order_by(models.CierreCaja.creado.desc()).all()
    return {"dia": dia.isoformat(), "ventas": len(ventas), "efectivo": efectivo, "sinpe": sinpe, "total": efectivo + sinpe,
            "cierres": [{"id": c.id, "fondo_inicial": c.fondo_inicial, "efectivo_esperado": c.efectivo_esperado,
                         "efectivo_contado": c.efectivo_contado, "diferencia": c.efectivo_contado - c.efectivo_esperado,
                         "sinpe": c.sinpe, "ventas": c.ventas, "notas": c.notas,
                         "hora": (c.creado.replace(tzinfo=timezone.utc).astimezone(CR)).strftime("%H:%M")} for c in cierres]}


@router.get("/caja")
def caja(dia: date | None = None, _=Depends(usuario_actual), db: Session = Depends(get_db)):
    return _caja(db, dia or hoy_cr())


@router.post("/cierres")
def cerrar_caja(d: CierreIn, usuario=Depends(usuario_actual), db: Session = Depends(get_db)):
    dia = d.dia or hoy_cr()
    c = _caja(db, dia)
    db.add(models.CierreCaja(dia=dia, fondo_inicial=d.fondo_inicial, efectivo_esperado=d.fondo_inicial + c["efectivo"],
                             efectivo_contado=d.efectivo_contado, sinpe=c["sinpe"], ventas=c["ventas"], notas=d.notas.strip(),
                             usuario_id=usuario.id))
    db.commit()
    return _caja(db, dia)


def unidades_ultimos_30(db: Session) -> dict[int, float]:
    """Unidades vendidas por producto en los últimos 30 días (para Costos del negocio)."""
    desde = hoy_cr() - timedelta(days=29)
    filas = db.query(models.VentaItem.receta_id, models.VentaItem.cantidad).join(models.Venta)\
        .filter(models.Venta.dia >= desde, models.Venta.anulada.is_(False), models.VentaItem.receta_id.isnot(None)).all()
    acum: dict[int, float] = defaultdict(float)
    for rid, q in filas:
        acum[rid] += q
    return dict(acum)
