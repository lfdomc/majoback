"""Costos del negocio (fijos y variables) y resumen: ganancia del mes y punto de equilibrio."""
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import costing, models
from .auth import usuario_actual
from .db import get_db

router = APIRouter(prefix="/api/costos", dependencies=[Depends(usuario_actual)])

FRECUENCIAS = ("diaria", "semanal", "quincenal", "mensual", "trimestral", "semestral", "anual")

# Costos más comunes de un negocio de comida en Costa Rica. Se cargan en ₡0 para que solo llene los que tiene.
COMUNES_FIJOS = [
    ("Alquiler del local", "mensual", "Si trabaja desde la casa, ponga la parte que corresponde al negocio."),
    ("Electricidad", "mensual", "Recibo del ICE o la cooperativa (la parte del negocio)."),
    ("Agua", "mensual", "AyA, municipalidad o ASADA."),
    ("Internet y teléfono", "mensual", ""),
    ("Gas (cilindros)", "mensual", ""),
    ("Salarios", "mensual", "Lo que se paga al personal, incluida su propia paga si trabaja en el negocio."),
    ("Cargas sociales (CCSS)", "mensual", "La parte patronal sobre los salarios."),
    ("Póliza de riesgos del trabajo (INS)", "anual", ""),
    ("Patente municipal", "trimestral", ""),
    ("Permiso sanitario y otros permisos", "anual", "Si dura varios años, divida el costo entre los años."),
    ("Contador", "mensual", ""),
    ("Publicidad y redes sociales", "mensual", ""),
    ("Sitio web, dominio y sistemas", "mensual", "Dominio, servidor, aplicaciones."),
    ("Mantenimiento de equipo", "mensual", ""),
    ("Desgaste de equipo (depreciación)", "mensual", "Ej.: refrigeradora de ₡600 000 que dura 5 años = ₡10 000 al mes."),
    ("Transporte y combustible", "mensual", "Compras en el súper, vueltas del negocio."),
    ("Limpieza y desechables generales", "mensual", "Jabón, cloro, bolsas de basura, guantes."),
    ("Seguro del local o equipo", "anual", ""),
    ("Imprevistos", "mensual", "Un monto de reserva para gastos inesperados."),
]
COMUNES_VARIABLES = [
    ("Comisión del datáfono (tarjetas)", "porcentaje", 50, "Porcentaje que cobra el banco; «aplica a» es la parte de las ventas que se paga con tarjeta."),
    ("Plataformas de entrega (Uber Eats, DiDi Food…)", "porcentaje", 20, "Comisión de la plataforma; «aplica a» es la parte de las ventas que llega por ahí."),
    ("Comisión de vendedores o revendedores", "porcentaje", 100, ""),
    ("Bolsas, servilletas y cucharas extra", "por_unidad", 100, "Lo que no está ya en la receta."),
    ("Entrega a domicilio", "por_unidad", 30, "Costo por producto entregado; «aplica a» es la parte que se lleva a domicilio."),
    ("Pérdidas y producto regalado", "porcentaje", 100, "Muestras, producto que se daña o no se vende."),
]


_candado = threading.Lock()


def sembrar(db: Session):
    """Carga los costos comunes la primera vez (en ₡0). Con candado: la pantalla pide dos cosas a la vez."""
    if db.get(models.Configuracion, "costos_sembrados"):
        return
    with _candado:
        db.expire_all()
        if db.get(models.Configuracion, "costos_sembrados"):
            return
        try:
            if db.query(models.CostoNegocio).count() == 0:
                for i, (n, f, nota) in enumerate(COMUNES_FIJOS):
                    db.add(models.CostoNegocio(tipo="fijo", nombre=n, frecuencia=f, monto=0, notas=nota, orden=i))
                for i, (n, m, aplica, nota) in enumerate(COMUNES_VARIABLES):
                    db.add(models.CostoNegocio(tipo="variable", nombre=n, modo=m, aplica_pct=aplica, monto=0, notas=nota, orden=i))
            db.add(models.Configuracion(clave="costos_sembrados", valor="1"))
            db.commit()
        except IntegrityError:
            db.rollback()


def _fila(c: models.CostoNegocio, dias: float) -> dict:
    d = {k: getattr(c, k) for k in ("id", "tipo", "nombre", "monto", "frecuencia", "modo", "aplica_pct", "activo", "notas", "orden")}
    if c.tipo == "fijo":
        d["mensual"] = costing.a_mensual(c.monto, c.frecuencia, dias)
    return d


class CostoIn(BaseModel):
    tipo: Literal["fijo", "variable"] = "fijo"
    nombre: str = Field(min_length=1, max_length=120)
    monto: float = Field(default=0, ge=0)
    frecuencia: Literal["diaria", "semanal", "quincenal", "mensual", "trimestral", "semestral", "anual"] = "mensual"
    modo: Literal["porcentaje", "por_unidad"] = "porcentaje"
    aplica_pct: float = Field(default=100, ge=0, le=100)
    activo: bool = True
    notas: str = Field(default="", max_length=400)


class AjustesIn(BaseModel):
    ventas_estimadas_mes: float = Field(default=0, ge=0)
    dias_trabajo_mes: float = Field(default=26, gt=0, le=31)
    reparto_fijos: Literal["unidad", "precio"] = "unidad"
    unidades_estimadas_mes: float = Field(default=0, ge=0)


class VentaIn(BaseModel):
    ventas_mes: float | None = Field(default=None, ge=0, le=1_000_000)


@router.get("")
def listar(db: Session = Depends(get_db)):
    sembrar(db)
    dias = costing.leer_config(db)["dias_trabajo_mes"]
    filas = db.query(models.CostoNegocio).order_by(models.CostoNegocio.tipo, models.CostoNegocio.orden, models.CostoNegocio.id).all()
    return [_fila(c, dias) for c in filas]


@router.post("")
def crear(d: CostoIn, db: Session = Depends(get_db)):
    orden = db.query(models.CostoNegocio).filter(models.CostoNegocio.tipo == d.tipo).count()
    c = models.CostoNegocio(**d.model_dump(), orden=orden)
    c.nombre = c.nombre.strip()
    db.add(c)
    db.commit()
    return _fila(c, costing.leer_config(db)["dias_trabajo_mes"])


@router.put("/{cid}")
def editar(cid: int, d: CostoIn, db: Session = Depends(get_db)):
    c = db.get(models.CostoNegocio, cid)
    if not c:
        raise HTTPException(404, "Costo no encontrado.")
    for k, v in d.model_dump().items():
        setattr(c, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    return _fila(c, costing.leer_config(db)["dias_trabajo_mes"])


@router.delete("/{cid}")
def borrar(cid: int, db: Session = Depends(get_db)):
    c = db.get(models.CostoNegocio, cid)
    if not c:
        raise HTTPException(404, "Costo no encontrado.")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.put("/ajustes/generales")
def ajustes(d: AjustesIn, db: Session = Depends(get_db)):
    for k, v in d.model_dump().items():
        fila = db.get(models.Configuracion, k)
        if fila:
            fila.valor = str(v)
        else:
            db.add(models.Configuracion(clave=k, valor=str(v)))
    db.commit()
    return resumen(db)


@router.put("/ventas/{rid}")
def ventas_producto(rid: int, d: VentaIn, db: Session = Depends(get_db)):
    r = db.get(models.Receta, rid)
    if not r or r.tipo != "producto":
        raise HTTPException(404, "Producto no encontrado.")
    r.ventas_mes = d.ventas_mes or None
    db.commit()
    return resumen(db)


@router.post("/usar-ventas-reales")
def usar_ventas_reales(db: Session = Depends(get_db)):
    """Pone en cada producto las unidades vendidas en el POS en los últimos 30 días."""
    from .pos import unidades_ultimos_30
    u = unidades_ultimos_30(db)
    if not u:
        raise HTTPException(400, "Todavía no hay ventas en el POS de los últimos 30 días.")
    for r in db.query(models.Receta).filter(models.Receta.tipo == "producto").all():
        r.ventas_mes = u.get(r.id) or None
    db.commit()
    return resumen(db)


@router.get("/resumen")
def resumen(db: Session = Depends(get_db)):
    """Cuadro del mes: ventas, costos, ganancia y punto de equilibrio, y el detalle por producto."""
    sembrar(db)
    ctx = costing.cargar_contexto(db)
    cfg, g = ctx.config, ctx.config["negocio"]
    iva = cfg["iva_pct"] / 100 if cfg["aplicar_iva"] else 0
    productos, aporte_total, ingredientes_total, variables_total, ventas_prod = [], 0.0, 0.0, 0.0, 0.0
    for r in sorted((r for r in ctx.recetas.values() if r["tipo"] == "producto"), key=lambda r: (r["categoria"].lower(), r["nombre"].lower())):
        try:
            c = costing.costo_receta(r, ctx)
        except costing.ErrorConversion:
            c = {"costo_por_rinde": None, "precios": {}, "errores": ["Error en la receta"]}
        p = c.get("precios", {})
        u = float(r.get("ventas_mes") or 0)
        fila = {"id": r["id"], "nombre": r["nombre"], "categoria": r["categoria"], "ventas_mes": r.get("ventas_mes"),
                "precio_venta": r.get("precio_venta"), "costo": c.get("costo_por_rinde"),
                "costo_variable": p.get("costo_variable"), "costo_fijo": p.get("costo_fijo"),
                "contribucion": p.get("contribucion"), "ganancia_real": p.get("ganancia_real"),
                "margen_neto_pct": p.get("margen_neto_pct"), "precio_equilibrio": p.get("precio_equilibrio"),
                "errores": bool(c.get("errores"))}
        if u and p.get("precio_venta_sin_iva") and c.get("costo_por_rinde") is not None:
            ventas_prod += p["precio_venta_sin_iva"] * u
            aporte_total += p["contribucion"] * u
            ingredientes_total += c["costo_por_rinde"] * u
            variables_total += p["costo_variable"] * u
            fila["aporte_mes"] = p["contribucion"] * u
        productos.append(fila)

    ventas = g["ventas_mes"]
    if g["fuente_ventas"] == "productos":
        contribucion_pct = aporte_total / ventas_prod if ventas_prod else None
    else:
        # sin ventas por producto: promedio simple de los productos con precio
        pcts = [(x["contribucion"] / (x["precio_venta"] / (1 + iva))) for x in productos if x["contribucion"] is not None and x["precio_venta"]]
        contribucion_pct = sum(pcts) / len(pcts) if pcts else None
        aporte_total = ventas * contribucion_pct if contribucion_pct is not None else 0
        ingredientes_total = variables_total = None
    equilibrio = g["fijos_mes"] / contribucion_pct if contribucion_pct and contribucion_pct > 0 else None
    # en unidades: cuántos productos hay que vender al mes, con el aporte promedio por producto
    unidades_con_precio = sum(float(x["ventas_mes"] or 0) for x in productos if x.get("aporte_mes") is not None)
    aporte_unidad = (aporte_total / unidades_con_precio) if g["fuente_ventas"] == "productos" and unidades_con_precio else None
    equilibrio_unidades = g["fijos_mes"] / aporte_unidad if aporte_unidad and aporte_unidad > 0 else None
    return {
        "fijos_mes": g["fijos_mes"], "variable_pct": g["variable_pct"] * 100, "variable_unidad": g["variable_unidad"],
        "ventas_mes": ventas, "unidades_mes": g["unidades_mes"], "fuente_ventas": g["fuente_ventas"],
        "fijo_pct": g["fijo_pct"] * 100 if g["fijo_pct"] is not None else None,
        "contribucion_pct": contribucion_pct * 100 if contribucion_pct is not None else None,
        "ingredientes_mes": ingredientes_total, "variables_mes": variables_total,
        "ganancia_mes": (aporte_total - g["fijos_mes"]) if ventas else None,
        "equilibrio_mes": equilibrio, "equilibrio_dia": equilibrio / g["dias_trabajo_mes"] if equilibrio else None,
        "dias_trabajo_mes": g["dias_trabajo_mes"], "ventas_estimadas_mes": cfg["ventas_estimadas_mes"],
        "iva_incluido": cfg["aplicar_iva"], "productos": productos,
        "productos_con_precio": g["productos_con_precio"], "productos_con_unidades": g["productos_con_unidades"],
        "reparto": g["reparto"], "fijo_unidad": g["fijo_unidad"], "unidades_reparto": g["unidades_reparto"],
        "fuente_unidades": g["fuente_unidades"], "unidades_estimadas_mes": cfg["unidades_estimadas_mes"],
        "equilibrio_unidades": equilibrio_unidades,
    }
