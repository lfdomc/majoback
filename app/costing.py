"""Motor de costos.

Todo se calcula en la unidad más pequeña del ingrediente (g, ml o unidad):

  costo por unidad mínima = precio / (contenido en unidad mínima × (1 − merma de compra))
  costo de un ítem        = cantidad en unidad mínima × costo unitario / (1 − merma del ítem)
  costo de la receta      = Σ ítems / (1 − merma general de la receta)

Las sub-recetas se vuelven ingredientes: su costo por unidad = costo total / rendimiento.
"""
import math
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, selectinload

from . import models
from .units import MINIMA, ErrorConversion, convertir, dimension

BASES = ("menor", "promedio", "mayor")


class ReferenciaCircular(ErrorConversion):
    pass


def _f(merma):
    m = float(merma or 0)
    if m >= 100 or m < 0:
        raise ErrorConversion("La merma debe estar entre 0 y 99,9 %")
    return 1 - m / 100


# ---------------------------------------------------------------- snapshot
@dataclass
class Contexto:
    ingredientes: dict[int, dict]
    recetas: dict[int, dict]
    config: dict[str, float | str]
    _cache: dict = field(default_factory=dict)


def ing_dict(i: models.Ingrediente) -> dict:
    return {
        "id": i.id, "nombre": i.nombre, "categoria": i.categoria, "unidad_base": i.unidad_base,
        "peso_unidad_g": i.peso_unidad_g, "densidad_g_ml": i.densidad_g_ml, "base_costo": i.base_costo,
        "notas": i.notas, "actualizado": i.actualizado.isoformat() if i.actualizado else None,
        "presentaciones": [{
            "id": p.id, "proveedor_id": p.proveedor_id, "proveedor": p.proveedor.nombre if p.proveedor else None,
            "descripcion": p.descripcion, "precio": p.precio, "cantidad": p.cantidad, "unidad": p.unidad,
            "merma_pct": p.merma_pct, "preferida": p.preferida, "fecha": p.fecha.isoformat() if p.fecha else None,
        } for p in i.presentaciones],
    }


def rec_dict(r: models.Receta) -> dict:
    return {
        "id": r.id, "nombre": r.nombre, "tipo": r.tipo, "categoria": r.categoria,
        "rinde_cantidad": r.rinde_cantidad, "rinde_unidad": r.rinde_unidad, "merma_pct": r.merma_pct,
        "margen_objetivo_pct": r.margen_objetivo_pct, "precio_venta": r.precio_venta,
        "precio_competencia": r.precio_competencia, "notas": r.notas, "ventas_mes": r.ventas_mes,
        "foto": r.foto, "descripcion_menu": r.descripcion_menu or "", "en_menu": bool(r.en_menu),
        "nombre_en": r.nombre_en or "", "descripcion_menu_en": r.descripcion_menu_en or "",
        "arte": r.arte, "estilo_arte": r.estilo_arte or "auto", "quitar_fondo": r.quitar_fondo is not False,
        "actualizado": r.actualizado.isoformat() if r.actualizado else None,
        "items": [{"id": it.id, "ingrediente_id": it.ingrediente_id, "subreceta_id": it.subreceta_id,
                   "cantidad": it.cantidad, "unidad": it.unidad, "merma_pct": it.merma_pct} for it in r.items],
    }


def leer_config(db: Session) -> dict:
    cfg = dict(models.CONFIG_DEFECTO)
    for c in db.query(models.Configuracion).all():
        cfg[c.clave] = c.valor
    return {
        "margen_objetivo_pct": float(cfg["margen_objetivo_pct"]),
        "iva_pct": float(cfg["iva_pct"]),
        "aplicar_iva": cfg["aplicar_iva"] in ("1", "true", "True"),
        "redondeo": float(cfg["redondeo"]),
        "nombre_negocio": cfg["nombre_negocio"],
        "ventas_estimadas_mes": float(cfg.get("ventas_estimadas_mes") or 0),
        "dias_trabajo_mes": float(cfg.get("dias_trabajo_mes") or 26),
    }


def cargar_contexto(db: Session) -> Contexto:
    ings = db.query(models.Ingrediente).options(
        selectinload(models.Ingrediente.presentaciones).selectinload(models.Presentacion.proveedor)).all()
    recs = db.query(models.Receta).options(selectinload(models.Receta.items)).all()
    cfg = leer_config(db)
    recetas = {r.id: rec_dict(r) for r in recs}
    cfg["negocio"] = gastos_negocio(db, recetas, cfg)
    return Contexto({i.id: ing_dict(i) for i in ings}, recetas, cfg)


# ---------------------------------------------------------------- costos del negocio
POR_MES = {"diaria": None, "semanal": 52 / 12, "quincenal": 2, "mensual": 1, "trimestral": 1 / 3, "semestral": 1 / 6, "anual": 1 / 12}


def a_mensual(monto: float, frecuencia: str, dias_trabajo: float) -> float:
    f = POR_MES.get(frecuencia, 1)
    return float(monto or 0) * (dias_trabajo if f is None else f)


def gastos_negocio(db: Session, recetas: dict, cfg: dict) -> dict:
    """Totales de costos fijos y variables y cómo se reparten entre los productos.

    - fijos_mes: suma mensual de los costos fijos activos.
    - variable_pct: fracción del precio (sin IVA) que se va en costos variables (% ponderado por cuántas ventas aplica).
    - variable_unidad: ₡ por cada producto vendido.
    - ventas_mes: ventas estimadas al mes (₡ sin IVA): suma de (precio × unidades) de los productos que tienen
      ventas estimadas; si ninguno tiene, el monto general de Ajustes.
    - fijo_pct: fracción de cada venta que se necesita para pagar los fijos (fijos_mes / ventas_mes).
    """
    iva = cfg["iva_pct"] / 100 if cfg["aplicar_iva"] else 0
    filas = db.query(models.CostoNegocio).filter(models.CostoNegocio.activo.is_(True)).all()
    dias = cfg.get("dias_trabajo_mes") or 26
    fijos = sum(a_mensual(c.monto, c.frecuencia, dias) for c in filas if c.tipo == "fijo")
    var_pct = sum((c.monto or 0) / 100 * (c.aplica_pct if c.aplica_pct is not None else 100) / 100
                  for c in filas if c.tipo == "variable" and c.modo == "porcentaje")
    var_uni = sum((c.monto or 0) * (c.aplica_pct if c.aplica_pct is not None else 100) / 100
                  for c in filas if c.tipo == "variable" and c.modo == "por_unidad")
    por_producto = sum(float(r["precio_venta"]) / (1 + iva) * float(r["ventas_mes"])
                       for r in recetas.values()
                       if r["tipo"] == "producto" and r.get("precio_venta") and r.get("ventas_mes"))
    unidades = sum(float(r["ventas_mes"]) for r in recetas.values() if r["tipo"] == "producto" and r.get("ventas_mes"))
    if por_producto > 0:
        ventas, fuente = por_producto, "productos"
    else:
        ventas, fuente = float(cfg.get("ventas_estimadas_mes") or 0), "general"
    return {"fijos_mes": fijos, "variable_pct": var_pct, "variable_unidad": var_uni, "ventas_mes": ventas,
            "unidades_mes": unidades, "fuente_ventas": fuente, "fijo_pct": (fijos / ventas) if ventas > 0 else None,
            "dias_trabajo_mes": dias}


# ---------------------------------------------------------------- ingredientes
def costo_presentacion(ing: dict, p: dict) -> dict:
    """Costo por unidad mínima de una presentación de compra."""
    try:
        contenido = convertir(float(p["cantidad"]), p["unidad"], ing["unidad_base"],
                              ing.get("peso_unidad_g"), ing.get("densidad_g_ml"), ing["nombre"])
        aprovechable = contenido * _f(p.get("merma_pct"))
        if aprovechable <= 0:
            raise ErrorConversion("El contenido debe ser mayor que cero")
        return {"contenido_base": contenido, "aprovechable_base": aprovechable,
                "costo_unitario": float(p["precio"]) / aprovechable, "error": None}
    except (ErrorConversion, TypeError, ValueError) as e:
        return {"contenido_base": None, "aprovechable_base": None, "costo_unitario": None, "error": str(e)}


def costos_ingrediente(ing: dict) -> dict:
    """Costo por unidad mínima según cada base (menor, promedio, mayor, preferida)."""
    filas = []
    for p in ing.get("presentaciones", []):
        filas.append({**p, **costo_presentacion(ing, p)})
    validas = [f for f in filas if f["costo_unitario"] is not None]
    res = {"presentaciones": filas, "menor": None, "mayor": None, "promedio": None,
           "preferida": None, "seleccionado": None, "presentacion_usada": None,
           "error": None if validas else "Sin precio de compra registrado"}
    if not validas:
        return res
    menor = min(validas, key=lambda f: f["costo_unitario"])
    mayor = max(validas, key=lambda f: f["costo_unitario"])
    pref = next((f for f in validas if f.get("preferida")), menor)
    res.update(menor=menor["costo_unitario"], mayor=mayor["costo_unitario"],
               promedio=sum(f["costo_unitario"] for f in validas) / len(validas),
               preferida=pref["costo_unitario"])
    base = ing.get("base_costo") or "preferida"
    usada = {"menor": menor, "mayor": mayor}.get(base, pref)
    res["seleccionado"] = res[base] if base in res else res["preferida"]
    res["presentacion_usada"] = usada["id"] if usada else None
    return res


# ---------------------------------------------------------------- recetas
def _rinde_base(rec: dict) -> tuple[float, str]:
    dim = dimension(rec["rinde_unidad"])
    unidad_min = MINIMA[dim]
    cant = convertir(float(rec["rinde_cantidad"] or 0), rec["rinde_unidad"], unidad_min)
    if cant <= 0:
        raise ErrorConversion(f"El rendimiento de «{rec['nombre']}» debe ser mayor que cero")
    return cant, unidad_min


def costo_receta(rec: dict, ctx: Contexto, pila: tuple = ()) -> dict:
    rid = rec.get("id")
    if rid is not None and rid in ctx._cache:
        return ctx._cache[rid]
    if rid is not None and rid in pila:
        raise ReferenciaCircular(f"Referencia circular: «{rec['nombre']}» termina usándose a sí misma")
    pila = pila + ((rid,) if rid is not None else ())

    items, errores = [], []
    tot = {"seleccionado": 0.0, "menor": 0.0, "promedio": 0.0, "mayor": 0.0}
    for idx, it in enumerate(rec.get("items", [])):
        fila = {"indice": idx, "ingrediente_id": it.get("ingrediente_id"), "subreceta_id": it.get("subreceta_id"),
                "cantidad": it.get("cantidad"), "unidad": it.get("unidad"), "merma_pct": it.get("merma_pct") or 0,
                "nombre": None, "tipo": None, "cantidad_base": None, "unidad_base": None,
                "costo_unitario": None, "costo": None, "costo_menor": None, "costo_mayor": None,
                "costo_promedio": None, "error": None}
        try:
            if it.get("ingrediente_id"):
                ing = ctx.ingredientes.get(int(it["ingrediente_id"]))
                if not ing:
                    raise ErrorConversion("Ingrediente no encontrado")
                fila.update(nombre=ing["nombre"], tipo="ingrediente", unidad_base=ing["unidad_base"])
                q = convertir(float(it["cantidad"] or 0), it["unidad"], ing["unidad_base"],
                              ing.get("peso_unidad_g"), ing.get("densidad_g_ml"), ing["nombre"])
                c = costos_ingrediente(ing)
                if c["error"]:
                    raise ErrorConversion(c["error"])
                unit = {"seleccionado": c["seleccionado"], "menor": c["menor"],
                        "promedio": c["promedio"], "mayor": c["mayor"]}
            elif it.get("subreceta_id"):
                sub = ctx.recetas.get(int(it["subreceta_id"]))
                if not sub:
                    raise ErrorConversion("Sub-receta no encontrada")
                if rid is not None and sub["id"] == rid:
                    raise ErrorConversion("Una receta no puede usarse a sí misma")
                fila.update(nombre=sub["nombre"], tipo="subreceta")
                rb, umin = _rinde_base(sub)
                fila["unidad_base"] = umin
                q = convertir(float(it["cantidad"] or 0), it["unidad"], umin, nombre=sub["nombre"])
                cs = costo_receta(sub, ctx, pila)
                if cs["errores"]:
                    raise ErrorConversion(f"La sub-receta tiene errores: {cs['errores'][0]}")
                unit = {k: cs["totales"][k] / rb for k in tot}
            else:
                raise ErrorConversion("Elija un ingrediente o sub-receta")
            f = _f(it.get("merma_pct"))
            fila["cantidad_base"] = q
            fila["costo_unitario"] = unit["seleccionado"]
            fila["costo"] = q * unit["seleccionado"] / f
            fila["costo_menor"] = q * unit["menor"] / f
            fila["costo_promedio"] = q * unit["promedio"] / f
            fila["costo_mayor"] = q * unit["mayor"] / f
            for k in tot:
                tot[k] += fila["costo" if k == "seleccionado" else f"costo_{k}"]
        except ReferenciaCircular:
            raise
        except (ErrorConversion, TypeError, ValueError) as e:
            fila["error"] = str(e)
            errores.append(f"{fila['nombre'] or 'Ítem ' + str(idx + 1)}: {e}")
        items.append(fila)

    try:
        fg = _f(rec.get("merma_pct"))
    except ErrorConversion as e:
        errores.append(str(e))
        fg = 1
    subtotal = tot["seleccionado"]
    totales = {k: v / fg for k, v in tot.items()}
    for fila in items:
        fila["participacion"] = (fila["costo"] / subtotal) if fila["costo"] and subtotal else 0

    try:
        rb, umin = _rinde_base(rec)
        rinde_cant = float(rec["rinde_cantidad"])
    except (ErrorConversion, TypeError, ValueError) as e:
        errores.append(str(e))
        rb, umin, rinde_cant = None, None, None

    res = {
        "items": items, "errores": errores, "subtotal_ingredientes": subtotal,
        "merma_general": totales["seleccionado"] - subtotal, "totales": totales,
        "rinde_base": rb, "unidad_minima": umin,
        "costo_por_rinde": (totales["seleccionado"] / rinde_cant) if rinde_cant else None,
        "costo_por_unidad_minima": (totales["seleccionado"] / rb) if rb else None,
    }
    res["precios"] = precios(rec, res, ctx.config)
    if rid is not None:
        ctx._cache[rid] = res
    return res


def precios(rec: dict, costo: dict, cfg: dict) -> dict:
    """Precio sugerido y margen real. El costo de referencia es por unidad de rendimiento (p. ej. por porción)."""
    c = costo.get("costo_por_rinde")
    margen = rec.get("margen_objetivo_pct")
    margen = float(margen) if margen not in (None, "") else cfg["margen_objetivo_pct"]
    iva = cfg["iva_pct"] / 100 if cfg["aplicar_iva"] else 0
    out = {"margen_objetivo_pct": margen, "iva_pct": cfg["iva_pct"] if cfg["aplicar_iva"] else 0,
           "precio_sugerido_sin_iva": None, "precio_sugerido": None, "precio_venta": rec.get("precio_venta"),
           "precio_venta_sin_iva": None, "ganancia": None, "margen_real_pct": None, "costo_sobre_venta_pct": None,
           "precio_competencia": rec.get("precio_competencia"), "diferencia_competencia": None}
    if c is None:
        return out
    if margen < 100:
        sin = c / (1 - margen / 100)
        con = sin * (1 + iva)
        r = cfg.get("redondeo") or 0
        out["precio_sugerido_sin_iva"] = sin
        out["precio_sugerido"] = math.ceil(con / r) * r if r else con
    pv = rec.get("precio_venta")
    if pv:
        pv = float(pv)
        pvs = pv / (1 + iva)
        out["precio_venta_sin_iva"] = pvs
        out["ganancia"] = pvs - c
        out["margen_real_pct"] = (pvs - c) / pvs * 100 if pvs else None
        out["costo_sobre_venta_pct"] = c / pvs * 100 if pvs else None
    ref = pv or out["precio_sugerido"]
    if rec.get("precio_competencia") and ref:
        out["diferencia_competencia"] = ref - float(rec["precio_competencia"])

    # ---- ganancia real: además de ingredientes, los costos variables y la parte de los costos fijos
    g = cfg.get("negocio")
    if g and rec.get("tipo", "producto") == "producto":
        out["variable_pct"] = g["variable_pct"] * 100
        out["variable_unidad"] = g["variable_unidad"]
        out["fijo_pct"] = g["fijo_pct"] * 100 if g["fijo_pct"] is not None else None
        # precio mínimo para no perder (cubre ingredientes, variables y su parte de los fijos)
        resto = 1 - g["variable_pct"] - (g["fijo_pct"] or 0)
        if resto > 0:
            eq = (c + g["variable_unidad"]) / resto
            out["precio_equilibrio_sin_iva"] = eq
            out["precio_equilibrio"] = eq * (1 + iva)
        else:
            out["precio_equilibrio_sin_iva"] = out["precio_equilibrio"] = None
        base = out["precio_venta_sin_iva"]
        if base:
            var = g["variable_unidad"] + base * g["variable_pct"]
            fijo = base * g["fijo_pct"] if g["fijo_pct"] is not None else None
            out["costo_variable"] = var
            out["costo_fijo"] = fijo
            out["contribucion"] = base - c - var                      # lo que deja para pagar fijos y ganar
            out["costo_real"] = c + var + (fijo or 0)
            out["ganancia_real"] = base - out["costo_real"] if fijo is not None else None
            out["margen_neto_pct"] = out["ganancia_real"] / base * 100 if fijo is not None else None
    return out


# ---------------------------------------------------------------- producción
def explotar(rec: dict, ctx: Contexto, factor: float, acum: dict, pila=()):
    """Acumula cantidades de ingredientes crudos (unidad mínima) necesarias para `factor` veces la receta."""
    if rec["id"] in pila:
        raise ReferenciaCircular("Referencia circular entre sub-recetas")
    fg = _f(rec.get("merma_pct"))
    for it in rec["items"]:
        f = _f(it.get("merma_pct")) * fg
        if it.get("ingrediente_id"):
            ing = ctx.ingredientes[it["ingrediente_id"]]
            q = convertir(float(it["cantidad"]), it["unidad"], ing["unidad_base"],
                          ing.get("peso_unidad_g"), ing.get("densidad_g_ml"), ing["nombre"])
            acum[ing["id"]] = acum.get(ing["id"], 0) + q * factor / f
        elif it.get("subreceta_id"):
            sub = ctx.recetas[it["subreceta_id"]]
            rb, umin = _rinde_base(sub)
            q = convertir(float(it["cantidad"]), it["unidad"], umin, nombre=sub["nombre"])
            explotar(sub, ctx, factor * (q / f) / rb, acum, pila + (rec["id"],))


def produccion(rec: dict, ctx: Contexto, cantidad: float) -> dict:
    """Lista de compras para producir `cantidad` en la unidad de rendimiento de la receta."""
    res = compras([(rec, cantidad)], ctx)
    return {"cantidad": cantidad, "unidad": rec["rinde_unidad"], "items": res["items"], "costo_total": res["costo_total"]}


def compras(pedidos: list, ctx: Contexto) -> dict:
    """Lista de compras combinada para varios productos: pedidos = [(receta, cantidad), ...]."""
    acum: dict[int, float] = {}
    for rec, cantidad in pedidos:
        explotar(rec, ctx, float(cantidad) / float(rec["rinde_cantidad"]), acum)
    filas, total, total_compra = [], 0.0, 0.0
    for iid, q in sorted(acum.items(), key=lambda kv: ctx.ingredientes[kv[0]]["nombre"]):
        ing = ctx.ingredientes[iid]
        c = costos_ingrediente(ing)
        pres = next((p for p in c["presentaciones"] if p["id"] == c["presentacion_usada"]), None)
        n_compras = math.ceil(q / pres["aprovechable_base"] - 1e-9) if pres and pres["aprovechable_base"] else None
        costo = q * c["seleccionado"] if c["seleccionado"] is not None else None
        total += costo or 0
        filas.append({"ingrediente_id": iid, "nombre": ing["nombre"], "cantidad_base": q,
                      "unidad_base": ing["unidad_base"], "costo": costo,
                      "presentacion": (pres["descripcion"] or f"{pres['cantidad']} {pres['unidad']}") if pres else None,
                      "proveedor": pres["proveedor"] if pres else None,
                      "comprar": n_compras, "costo_compra": n_compras * pres["precio"] if n_compras else None})
        total_compra += (n_compras * pres["precio"]) if n_compras else 0
    return {"items": filas, "costo_total": total, "costo_compra_total": total_compra}
