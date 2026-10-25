"""Categorías de productos, sub-recetas e ingredientes.

La categoría se guarda como texto en cada receta o ingrediente. Aquí además se guarda
la lista ordenada (incluye categorías aún vacías) en la configuración "categorias".
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .auth import usuario_actual
from .db import get_db

TIPOS = ("producto", "subreceta", "ingrediente")

# Traducciones sugeridas para categorías comunes (se pueden cambiar en la pantalla de Categorías)
SUGERIDAS = {
    "postres": "Desserts", "frutas": "Fruit", "frutas frescas": "Fresh fruit", "helados": "Ice cream",
    "bebidas": "Drinks", "batidos": "Smoothies", "cafe": "Coffee", "café": "Coffee", "restaurante": "Food",
    "comidas": "Meals", "desayunos": "Breakfast", "almuerzos": "Lunch", "entradas": "Starters", "boquitas": "Snacks",
    "ensaladas": "Salads", "pizzas": "Pizzas", "hamburguesas": "Burgers", "repostería": "Pastries", "reposteria": "Pastries",
    "pasteles": "Cakes", "queques": "Cakes", "galletas": "Cookies", "combos": "Combos", "especiales": "Specials",
    "granizados": "Shaved ice", "copas": "Sundaes", "crepas": "Crepes", "waffles": "Waffles", "otros": "Other",
}


def _ingles_guardado(db: Session) -> dict:
    fila = db.get(models.Configuracion, "categorias_en")
    try:
        return json.loads(fila.valor) if fila else {}
    except ValueError:
        return {}


def _guardar_ingles(db: Session, d: dict):
    texto = json.dumps(d, ensure_ascii=False)
    fila = db.get(models.Configuracion, "categorias_en")
    if fila:
        fila.valor = texto
    else:
        db.add(models.Configuracion(clave="categorias_en", valor=texto))


def en_ingles(db: Session, nombre: str, guardado: dict | None = None) -> str:
    """Nombre de la categoría en inglés: el que se guardó, o una sugerencia, o el mismo nombre."""
    g = _ingles_guardado(db) if guardado is None else guardado
    return g.get(nombre) or SUGERIDAS.get(nombre.strip().lower(), "") or nombre
router = APIRouter(prefix="/api/categorias", dependencies=[Depends(usuario_actual)])


def _lista_guardada(db: Session) -> dict:
    fila = db.get(models.Configuracion, "categorias")
    try:
        d = json.loads(fila.valor) if fila else {}
    except ValueError:
        d = {}
    return {t: list(d.get(t, [])) for t in TIPOS}


def _guardar_lista(db: Session, d: dict):
    texto = json.dumps(d, ensure_ascii=False)
    fila = db.get(models.Configuracion, "categorias")
    if fila:
        fila.valor = texto
    else:
        db.add(models.Configuracion(clave="categorias", valor=texto))


def _filas(db: Session, tipo: str):
    """Consulta de los registros que usan categorías de ese tipo."""
    if tipo == "ingrediente":
        return models.Ingrediente, db.query(models.Ingrediente)
    return models.Receta, db.query(models.Receta).filter(models.Receta.tipo == tipo)


def _usos(db: Session, tipo: str) -> dict[str, int]:
    modelo, q = _filas(db, tipo)
    return dict(q.with_entities(modelo.categoria, func.count()).group_by(modelo.categoria).all())


def ordenadas(db: Session, tipo: str) -> list[dict]:
    """Lista en el orden elegido; las que estén en uso pero no en la lista van al final, en orden alfabético."""
    guardada = _lista_guardada(db)[tipo]
    usos = _usos(db, tipo)
    nombres = list(dict.fromkeys(guardada)) + sorted((n for n in usos if n not in guardada), key=str.lower)
    ing = _ingles_guardado(db) if tipo == "producto" else {}
    salida = []
    for n in nombres:
        if not n:
            continue
        fila = {"nombre": n, "usos": usos.get(n, 0)}
        if tipo == "producto":
            fila["en"] = ing.get(n, "")
            fila["en_sugerido"] = SUGERIDAS.get(n.strip().lower(), "")
        salida.append(fila)
    return salida


def _tipo(t: str) -> str:
    if t not in TIPOS:
        raise HTTPException(400, "Tipo de categoría no válido.")
    return t


def _nombre(n: str) -> str:
    n = " ".join((n or "").split())
    if not n:
        raise HTTPException(400, "Escriba el nombre de la categoría.")
    if len(n) > 80:
        raise HTTPException(400, "El nombre es muy largo (máximo 80 caracteres).")
    return n


class Nueva(BaseModel):
    nombre: str


class Renombrar(BaseModel):
    anterior: str
    nuevo: str


class Ingles(BaseModel):
    nombre: str
    en: str = Field(default="", max_length=80)


class Orden(BaseModel):
    nombres: list[str] = Field(default_factory=list)


@router.get("/{tipo}")
def listar(tipo: str, db: Session = Depends(get_db)):
    return ordenadas(db, _tipo(tipo))


@router.post("/{tipo}")
def crear(tipo: str, d: Nueva, db: Session = Depends(get_db)):
    tipo, nombre = _tipo(tipo), _nombre(d.nombre)
    actuales = [c["nombre"] for c in ordenadas(db, tipo)]
    if any(n.lower() == nombre.lower() for n in actuales):
        raise HTTPException(400, f"Ya existe la categoría «{nombre}».")
    lista = _lista_guardada(db)
    lista[tipo] = actuales + [nombre]
    _guardar_lista(db, lista)
    db.commit()
    return ordenadas(db, tipo)


@router.put("/{tipo}/renombrar")
def renombrar(tipo: str, d: Renombrar, db: Session = Depends(get_db)):
    """Cambia el nombre en todos los registros. Si el nombre nuevo ya existe, las une."""
    tipo, anterior, nuevo = _tipo(tipo), d.anterior, _nombre(d.nuevo)
    actuales = [c["nombre"] for c in ordenadas(db, tipo)]
    if anterior not in actuales:
        raise HTTPException(404, "Esa categoría no existe.")
    existente = next((n for n in actuales if n.lower() == nuevo.lower() and n != anterior), None)
    destino = existente or nuevo
    modelo, q = _filas(db, tipo)
    q.filter(modelo.categoria == anterior).update({modelo.categoria: destino}, synchronize_session=False)
    lista = _lista_guardada(db)
    lista[tipo] = [destino if n == anterior else n for n in actuales]
    lista[tipo] = list(dict.fromkeys(lista[tipo]))
    _guardar_lista(db, lista)
    if tipo == "producto":
        g = _ingles_guardado(db)
        if anterior in g:
            g.setdefault(destino, g.pop(anterior))
            _guardar_ingles(db, g)
    db.commit()
    return ordenadas(db, tipo)


@router.delete("/{tipo}")
def borrar(tipo: str, nombre: str, mover_a: str | None = None, db: Session = Depends(get_db)):
    """Borra la categoría. Si tiene registros, se pasan a `mover_a`."""
    tipo = _tipo(tipo)
    actuales = ordenadas(db, tipo)
    cat = next((c for c in actuales if c["nombre"] == nombre), None)
    if not cat:
        raise HTTPException(404, "Esa categoría no existe.")
    if cat["usos"]:
        if not mover_a or mover_a == nombre:
            raise HTTPException(400, f"«{nombre}» tiene {cat['usos']} registro(s). Elija a qué categoría pasarlos.")
        mover_a = _nombre(mover_a)
        modelo, q = _filas(db, tipo)
        q.filter(modelo.categoria == nombre).update({modelo.categoria: mover_a}, synchronize_session=False)
    lista = _lista_guardada(db)
    nombres = [c["nombre"] for c in actuales if c["nombre"] != nombre]
    if mover_a and mover_a not in nombres:
        nombres.append(mover_a)
    lista[tipo] = nombres
    _guardar_lista(db, lista)
    db.commit()
    return ordenadas(db, tipo)


@router.put("/{tipo}/ingles")
def poner_ingles(tipo: str, d: Ingles, db: Session = Depends(get_db)):
    """Nombre en inglés de una categoría de productos (para el menú en inglés)."""
    if _tipo(tipo) != "producto":
        raise HTTPException(400, "Solo las categorías de productos tienen nombre en inglés.")
    g = _ingles_guardado(db)
    en = " ".join(d.en.split())
    if en:
        g[d.nombre] = en
    else:
        g.pop(d.nombre, None)
    _guardar_ingles(db, g)
    db.commit()
    return ordenadas(db, tipo)


@router.put("/{tipo}/orden")
def ordenar(tipo: str, d: Orden, db: Session = Depends(get_db)):
    tipo = _tipo(tipo)
    actuales = [c["nombre"] for c in ordenadas(db, tipo)]
    nuevo = [n for n in d.nombres if n in actuales]
    nuevo += [n for n in actuales if n not in nuevo]
    lista = _lista_guardada(db)
    lista[tipo] = nuevo
    _guardar_lista(db, lista)
    db.commit()
    return ordenadas(db, tipo)
