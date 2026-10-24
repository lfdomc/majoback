"""Unidades de medida y conversión a la unidad más pequeña (g, ml, unidad)."""

# unidad -> (dimensión, factor a la unidad mínima de esa dimensión)
UNIDADES: dict[str, tuple[str, float]] = {
    "mg": ("masa", 0.001),
    "g": ("masa", 1.0),
    "kg": ("masa", 1000.0),
    "oz": ("masa", 28.3495),
    "lb": ("masa", 453.592),
    "ml": ("volumen", 1.0),
    "l": ("volumen", 1000.0),
    "oz_fl": ("volumen", 29.5735),
    "cdita": ("volumen", 5.0),
    "cda": ("volumen", 15.0),
    "taza": ("volumen", 240.0),
    "gal": ("volumen", 3785.41),
    "unidad": ("unidad", 1.0),
    "docena": ("unidad", 12.0),
    "porcion": ("unidad", 1.0),
}

ETIQUETAS = {
    "mg": "miligramos", "g": "gramos", "kg": "kilos", "oz": "onzas (peso)", "lb": "libras",
    "ml": "mililitros", "l": "litros", "oz_fl": "onzas líquidas", "cdita": "cucharaditas",
    "cda": "cucharadas", "taza": "tazas", "gal": "galones", "unidad": "unidades",
    "docena": "docenas", "porcion": "porciones",
}

MINIMA = {"masa": "g", "volumen": "ml", "unidad": "unidad"}


class ErrorConversion(ValueError):
    pass


def dimension(unidad: str) -> str:
    if unidad not in UNIDADES:
        raise ErrorConversion(f"Unidad desconocida: {unidad}")
    return UNIDADES[unidad][0]


def convertir(cantidad: float, desde: str, hacia: str, peso_unidad_g: float | None = None,
              densidad_g_ml: float | None = None, nombre: str = "") -> float:
    """Convierte `cantidad` de la unidad `desde` a la unidad `hacia`.

    Entre dimensiones distintas usa:
      - densidad_g_ml: gramos que pesa 1 ml (masa <-> volumen)
      - peso_unidad_g: gramos aprovechables de 1 unidad (unidad <-> masa)
    """
    d_from, f_from = UNIDADES.get(desde, (None, None))
    d_to, f_to = UNIDADES.get(hacia, (None, None))
    if d_from is None:
        raise ErrorConversion(f"Unidad desconocida: {desde}")
    if d_to is None:
        raise ErrorConversion(f"Unidad desconocida: {hacia}")
    q = cantidad * f_from  # en g, ml o unidades
    if d_from != d_to:
        q = _cruzar(q, d_from, d_to, peso_unidad_g, densidad_g_ml, nombre)
    return q / f_to


def _cruzar(q, d_from, d_to, peso, dens, nombre):
    quien = f" para «{nombre}»" if nombre else ""
    falta_dens = ErrorConversion(f"Falta la densidad (g por ml){quien} para pasar de {d_from} a {d_to}")
    falta_peso = ErrorConversion(f"Falta el peso por unidad (g){quien} para pasar de {d_from} a {d_to}")
    # primero llevar todo a gramos
    if d_from == "masa":
        g = q
    elif d_from == "volumen":
        if not dens:
            raise falta_dens
        g = q * dens
    else:  # unidad
        if not peso:
            raise falta_peso
        g = q * peso
    if d_to == "masa":
        return g
    if d_to == "volumen":
        if not dens:
            raise falta_dens
        return g / dens
    if not peso:
        raise falta_peso
    return g / peso
