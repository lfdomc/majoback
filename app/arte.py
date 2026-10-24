"""Genera la imagen de anuncio de cada producto: fondo de color, foto recortada, nombre, precio y logo.

- El recorte del fondo usa el modelo «silueta» (U²-Net) con onnxruntime. Si no está disponible o la foto
  no tiene un objeto claro, la foto se coloca como una postal con borde blanco.
- Todo se dibuja con Pillow a 1080 × 1080 (sirve para el menú y para Instagram/Facebook).
"""
import colorsys
import io
import math
import os
import random
import threading
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

AQUI = os.path.dirname(__file__)
FUENTES = os.path.join(AQUI, "fuentes")
LADO = 1080
# Cambie este número cuando cambie el diseño: al arrancar, el servidor rehace todas las imágenes.
VERSION_ARTE = "5"
MODELO_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx"

_sesion = None
_candado = threading.Lock()


# ------------------------------------------------------------------ recorte de fondo
def _ruta_modelo():
    if os.getenv("MODELO_RECORTE"):
        return os.getenv("MODELO_RECORTE")
    from .imagenes import MEDIA_DIR
    return os.path.join(os.path.dirname(MEDIA_DIR), "modelos", "silueta.onnx")


def _modelo():
    """Carga (y descarga la primera vez) el modelo de recorte. Devuelve None si no se puede."""
    global _sesion
    if os.getenv("RECORTE_FONDO", "si").lower() in ("no", "0", "false"):
        return None
    if _sesion is not None:
        return _sesion or None
    with _candado:
        if _sesion is not None:
            return _sesion or None
        try:
            import onnxruntime as ort
            ruta = _ruta_modelo()
            if not os.path.exists(ruta):
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                print("Descargando modelo de recorte (44 MB, solo la primera vez)…")
                tmp = ruta + ".part"
                urllib.request.urlretrieve(MODELO_URL, tmp)
                os.replace(tmp, ruta)
            opciones = ort.SessionOptions()
            opciones.intra_op_num_threads = 2
            _sesion = ort.InferenceSession(ruta, opciones, providers=["CPUExecutionProvider"])
        except Exception as e:  # sin modelo: se usa el estilo postal
            print(f"Recorte de fondo no disponible: {e}")
            _sesion = False
    return _sesion or None


def recortar(img: Image.Image) -> Image.Image | None:
    """Quita el fondo. Devuelve RGBA recortado al objeto, o None si no hay un objeto claro."""
    import numpy as np
    sesion = _modelo()
    if sesion is None:
        return None
    rgb = img.convert("RGB")
    chica = rgb.resize((320, 320), Image.LANCZOS)
    a = np.asarray(chica).astype(np.float32)
    a = a / max(float(a.max()), 1e-6)
    a = (a - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    a = a.transpose(2, 0, 1)[None].astype(np.float32)
    salida = sesion.run(None, {sesion.get_inputs()[0].name: a})[0][0, 0]
    m = (salida - salida.min()) / (salida.max() - salida.min() + 1e-6)
    cobertura = float((m > 0.5).mean())
    if cobertura < 0.06 or cobertura > 0.9:  # no encontró un objeto separado del fondo
        return None
    mascara = Image.fromarray((m * 255).astype("uint8")).resize(rgb.size, Image.LANCZOS)
    mascara = mascara.point(lambda v: 0 if v < 18 else (255 if v > 235 else v))
    out = rgb.convert("RGBA")
    out.putalpha(mascara)
    caja = mascara.point(lambda v: 255 if v > 24 else 0).getbbox()
    return out.crop(caja) if caja else None


def gorro() -> Image.Image:
    """Gorro de chef dibujado (para negocios que prefieren no usar la imagen del logo)."""
    return Image.open(os.path.join(AQUI, "graficos", "gorro.png")).convert("RGBA")


# ------------------------------------------------------------------ colores
def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hls(rgb, h=None, l=None, s=None, dh=0.0):
    H, L, S = colorsys.rgb_to_hls(*[c / 255 for c in rgb])
    H = ((h if h is not None else H) + dh) % 1
    r, g, b = colorsys.hls_to_rgb(H, L if l is None else l, S if s is None else s)
    return (round(r * 255), round(g * 255), round(b * 255))


def paleta(color: str) -> dict:
    base = _hex(color if color and len(color) == 7 else "#D94C72")
    _, L, S = colorsys.rgb_to_hls(*[c / 255 for c in base])
    return {
        "base": base,
        "oscuro": _hls(base, l=max(0.16, L * 0.42), s=min(1, S * 0.9)),
        "claro": _hls(base, l=0.93, s=min(0.75, S * 0.7)),
        "medio": _hls(base, l=min(0.8, L + 0.12)),
        "comp": _hls(base, dh=0.47, l=0.52, s=min(0.75, max(0.45, S))),
        "acento": _hls(base, dh=0.14, l=0.6, s=0.95),
    }


# ------------------------------------------------------------------ texto
def _fuente(nombre, tam):
    return ImageFont.truetype(os.path.join(FUENTES, nombre), tam)


def _ajustar_titulo(draw, texto, ancho_max, tam_max=150, tam_min=56, lineas_max=3, fuente="LilitaOne.woff"):
    """Elige el tamaño más grande y el corte en líneas más parejo que quepa en el ancho."""
    palabras = texto.split()
    for tam in range(tam_max, tam_min - 1, -4):
        f = _fuente(fuente, tam)
        for n in range(1, min(lineas_max, len(palabras)) + 1):
            mejor = None
            for cortes in _cortes(len(palabras), n):
                lineas = [" ".join(palabras[a:b]) for a, b in cortes]
                anchos = [draw.textlength(l, font=f) for l in lineas]
                if max(anchos) <= ancho_max:
                    puntaje = max(anchos) - min(anchos)
                    if mejor is None or puntaje < mejor[0]:
                        mejor = (puntaje, lineas)
            if mejor:
                return f, mejor[1], tam
    f = _fuente(fuente, tam_min)
    return f, [texto], tam_min


def _cortes(n, partes):
    if partes == 1:
        yield [(0, n)]
        return
    for i in range(1, n - partes + 2):
        for resto in _cortes(n - i, partes - 1):
            yield [(0, i)] + [(a + i, b + i) for a, b in resto]


def _fin_titulo(texto, y, ancho, tam_max=150):
    """Dónde termina el título, sin dibujarlo (para acomodar el fondo)."""
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    _, lineas, tam = _ajustar_titulo(d, texto.upper(), ancho, tam_max=tam_max)
    return y + len(lineas) * int(tam * 0.98)


def _titulo(lienzo, texto, x, y, ancho, color, borde=None, sombra=None, centrado=False, tam_max=150):
    d = ImageDraw.Draw(lienzo)
    f, lineas, tam = _ajustar_titulo(d, texto.upper(), ancho, tam_max=tam_max)
    alto = int(tam * 0.98)
    for i, l in enumerate(lineas):
        w = d.textlength(l, font=f)
        px = x + (ancho - w) / 2 if centrado else x
        py = y + i * alto
        if sombra:
            d.text((px + 7, py + 9), l, font=f, fill=sombra)
        d.text((px, py), l, font=f, fill=color, stroke_width=10 if borde else 0, stroke_fill=borde)
    return y + len(lineas) * alto


def _precio_txt(precio):
    return f"{round(precio):,}".replace(",", " ")


def _sticker_precio(lienzo, precio, cx, cy, r, fondo, tinta, giro=-10):
    if not precio:
        return
    # Precio grande y pegado al costado más cercano, para que tape menos el producto
    r = int(r * 1.4)
    borde = 26
    cx = LADO - r - borde if cx >= LADO / 2 else r + borde
    cy = min(max(cy, r + borde), LADO - r - borde)
    capa = Image.new("RGBA", (r * 2 + 40, r * 2 + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    c = r + 20
    # borde ondulado tipo etiqueta
    pts = []
    for k in range(72):
        ang = 2 * math.pi * k / 72
        rr = r + (8 if k % 2 == 0 else 0)
        pts.append((c + rr * math.cos(ang), c + rr * math.sin(ang)))
    d.polygon(pts, fill=fondo)
    num = _precio_txt(precio)
    tam = int(r * 0.66)
    while True:
        f_num = _fuente("Bricolage-800.woff", tam)
        f_sim = _fuente("Bricolage-800-ext.woff", int(tam * 0.8))
        w_sim = d.textlength("₡", font=f_sim)
        w_num = d.textlength(num, font=f_num)
        total = w_sim + 4 + w_num
        if total <= r * 1.7 or tam <= 30:
            break
        tam -= 3
    x0 = c - total / 2
    d.text((x0, c - tam * 0.62 + tam * 0.12), "₡", font=f_sim, fill=tinta)
    d.text((x0 + w_sim + 4, c - tam * 0.62), num, font=f_num, fill=tinta)
    capa = capa.rotate(giro, resample=Image.BICUBIC, expand=False)
    sombra = capa.split()[3].point(lambda a: a * 0.35).filter(ImageFilter.GaussianBlur(10))
    lienzo.paste((0, 0, 0), (int(cx - c + 6), int(cy - c + 10)), sombra)
    lienzo.alpha_composite(capa, (int(cx - c), int(cy - c)))


class Marca:
    """Cómo firmar la imagen: logo en círculo, gorro sobre el nombre, o solo el nombre."""
    def __init__(self, modo="logo", imagen=None, nombre="", eslogan=""):
        self.modo, self.imagen, self.nombre, self.eslogan = modo, imagen, nombre or "", (eslogan or "")[:24]


def _espaciado(d, xy, texto, fuente, fill, track=6):
    x, y = xy
    for ch in texto:
        d.text((x, y), ch, font=fuente, fill=fill)
        x += d.textlength(ch, font=fuente) + track
    return x


def _ancho_espaciado(d, texto, fuente, track=6):
    return sum(d.textlength(ch, font=fuente) + track for ch in texto) - track


def _logo(lienzo, marca, _sin_uso, x, y, tam, tinta, centrado=False):
    """Firma de la marca. Logo de imagen en círculo blanco; si no, el logotipo escrito
    (nombre en letra manuscrita, con el gorro de chef encima si se eligió) en una placa blanca."""
    d = ImageDraw.Draw(lienzo)
    if marca.modo == "logo" and marca.imagen is not None:
        logo = marca.imagen
        sombra = Image.new("L", (tam + 60, tam + 60), 0)
        ImageDraw.Draw(sombra).ellipse([30, 36, 30 + tam, 36 + tam], fill=90)
        sombra = sombra.filter(ImageFilter.GaussianBlur(12))
        px = int(x - tam / 2) if centrado else x
        lienzo.paste((0, 0, 0), (px - 30, y - 30), sombra)
        d.ellipse([px, y, px + tam, y + tam], fill=(255, 255, 255))
        dentro = ImageOps.contain(logo.convert("RGBA"), (int(tam * 0.78), int(tam * 0.78)), Image.LANCZOS)
        mascara = Image.new("L", dentro.size, 0)
        ImageDraw.Draw(mascara).ellipse([0, 0, dentro.width, dentro.height], fill=255)
        if logo.width == logo.height:  # logos cuadrados se recortan en círculo
            dentro.putalpha(ImageChops.multiply(dentro.split()[3], mascara))
        lienzo.alpha_composite(dentro, (px + (tam - dentro.width) // 2, y + (tam - dentro.height) // 2))
        return
    if not marca.nombre:
        return
    con_gorro = marca.modo == "gorro"
    tam_nombre = 84
    f = _fuente("Allura.woff", tam_nombre)
    w = d.textlength(marca.nombre, font=f)
    while w > 600 and tam_nombre > 44:
        tam_nombre -= 4
        f = _fuente("Allura.woff", tam_nombre)
        w = d.textlength(marca.nombre, font=f)
    f_es = _fuente("Bricolage-800.woff", 24)
    w_es = _ancho_espaciado(d, marca.eslogan, f_es, 5) if marca.eslogan else 0
    margen_izq = 34
    ancho = int(max(w, w_es) + margen_izq * 2)
    alto = 112 + (34 if marca.eslogan else 0)
    base_y = y + tam - alto + 10  # el logotipo termina donde terminaría el círculo del logo
    px = int(x - ancho / 2) if centrado else x
    px = max(24, min(px, LADO - 24 - ancho))
    # Letras blancas con borde del color de la marca (sin placa), con una sombra suave para que resalten
    tx = px + margen_izq + (max(w, w_es) - w) / 2
    ex = px + (ancho - w_es) / 2
    sombra = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(sombra)
    ds.text((tx + 3, base_y + 10), marca.nombre, font=f, fill=(0, 0, 0, 110), stroke_width=7, stroke_fill=(0, 0, 0, 110))
    lienzo.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(6)))
    d.text((tx, base_y + 6), marca.nombre, font=f, fill=(255, 255, 255), stroke_width=6, stroke_fill=tinta)
    if marca.eslogan:
        x_es = ex
        for ch in marca.eslogan:
            d.text((x_es, base_y + 106), ch, font=f_es, fill=(255, 255, 255), stroke_width=4, stroke_fill=tinta)
            x_es += d.textlength(ch, font=f_es) + 5
    if con_gorro:
        g = gorro()
        gris = ImageOps.grayscale(g)
        g2 = ImageOps.colorize(gris, black=tinta, white=(255, 255, 255)).convert("RGBA")
        g2.putalpha(g.split()[3])
        g2 = ImageOps.contain(g2, (int(tam_nombre * 1.15), int(tam_nombre * 1.15)), Image.LANCZOS)
        lienzo.alpha_composite(g2, (int(tx - tam_nombre * 0.22), int(base_y - g2.height * 0.62)))


def _producto(lienzo, recorte, foto, caja, giro=0):
    """Coloca el producto: recortado con sombra, o como postal con borde blanco."""
    x0, y0, x1, y1 = caja
    if recorte is not None:
        p = ImageOps.contain(recorte, (x1 - x0, y1 - y0), Image.LANCZOS)
        px = x0 + (x1 - x0 - p.width) // 2
        py = y1 - p.height
        sombra = Image.new("L", lienzo.size, 0)
        ImageDraw.Draw(sombra).ellipse([px + p.width * 0.1, y1 - 34, px + p.width * 0.9, y1 + 30], fill=120)
        lienzo.paste((0, 0, 0), (0, 0), sombra.filter(ImageFilter.GaussianBlur(22)))
        contorno = p.split()[3].filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(2))
        blanco = Image.new("RGBA", p.size, (255, 255, 255, 0))
        blanco.putalpha(contorno.point(lambda a: int(a * 0.85)))
        lienzo.alpha_composite(blanco, (px, py))
        lienzo.alpha_composite(p, (px, py))
        return
    # postal
    w, h = x1 - x0, y1 - y0
    lado = int(min(w, h) * 0.92)
    f = ImageOps.fit(foto.convert("RGB"), (lado, lado), Image.LANCZOS)
    marco = Image.new("RGBA", (lado + 36, lado + 36), (255, 255, 255, 255))
    marco.paste(f, (18, 18))
    m = Image.new("L", marco.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, marco.width, marco.height], radius=40, fill=255)
    marco.putalpha(m)
    m2 = Image.new("L", f.size, 0)
    ImageDraw.Draw(m2).rounded_rectangle([0, 0, lado, lado], radius=26, fill=255)
    interior = f.convert("RGBA")
    interior.putalpha(m2)
    marco.alpha_composite(interior, (18, 18))
    marco = marco.rotate(giro or -4, resample=Image.BICUBIC, expand=True)
    sombra = marco.split()[3].point(lambda a: a * 0.4).filter(ImageFilter.GaussianBlur(18))
    px = x0 + (w - marco.width) // 2
    py = y0 + (h - marco.height) // 2 + 10
    lienzo.paste((0, 0, 0), (px + 10, py + 18), sombra)
    lienzo.alpha_composite(marco, (px, py))


def _confeti(d, rnd, colores, n=14, zona=(0, 0, LADO, LADO)):
    for _ in range(n):
        x = rnd.randint(zona[0], zona[2])
        y = rnd.randint(zona[1], zona[3])
        r = rnd.choice([8, 10, 14, 18])
        c = rnd.choice(colores)
        if rnd.random() < 0.5:
            d.ellipse([x - r, y - r, x + r, y + r], fill=c)
        else:
            d.ellipse([x - r, y - r, x + r, y + r], outline=c, width=5)


def _ola(d, y, amp, fase, color, hacia_abajo=True, periodos=1.6):
    pts = []
    for x in range(0, LADO + 21, 20):
        pts.append((x, y + amp * math.sin(fase + x / LADO * periodos * 2 * math.pi)))
    pts += [(LADO, LADO if hacia_abajo else 0), (0, LADO if hacia_abajo else 0)]
    d.polygon(pts, fill=color)


# ------------------------------------------------------------------ estilos
def _estilo_ola(c, rnd, p, recorte, foto, nombre, precio, logo, negocio):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["claro"])
    _ola(d, _fin_titulo(nombre, 64, 940) + 60, 40, rnd.random() * 6, p["base"], hacia_abajo=False)
    _ola(d, 760, 45, rnd.random() * 6, p["comp"])
    _ola(d, 860, 35, rnd.random() * 6, p["medio"])
    _confeti(d, rnd, [p["acento"], (255, 255, 255), p["comp"]], 12, (40, 330, 1040, 700))
    fin = _titulo(c, nombre, 70, 64, 940, (255, 255, 255), sombra=p["oscuro"])
    _producto(c, recorte, foto, (130, max(fin + 10, 330), 950, 1000))
    _sticker_precio(c, precio, 900, max(fin + 180, 470), 112, p["acento"], p["oscuro"])
    _logo(c, logo, negocio, 56, 870, 150, p["oscuro"])


def _estilo_rayas(c, rnd, p, recorte, foto, nombre, precio, logo, negocio):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["base"])
    franja = _hls(p["base"], l=min(0.85, colorsys.rgb_to_hls(*[v / 255 for v in p["base"]])[1] + 0.07))
    for y in range(600, LADO, 56):
        d.rectangle([0, y, LADO, y + 28], fill=franja)
    for k in range(5):  # notas de círculos grandes de fondo
        r = rnd.randint(70, 150)
        x, y = rnd.randint(-40, LADO + 40), rnd.randint(-40, 560)
        d.ellipse([x - r, y - r, x + r, y + r], outline=_hls(p["base"], l=0.8), width=6)
    _confeti(d, rnd, [p["acento"], (255, 255, 255)], 10, (40, 260, 1040, 640))
    fin = _titulo(c, nombre, 70, 60, 940, (255, 255, 255), sombra=p["oscuro"], centrado=True)
    _producto(c, recorte, foto, (150, max(fin + 10, 300), 930, 930))
    _sticker_precio(c, precio, 170, 640, 108, p["acento"], p["oscuro"], giro=8)
    _logo(c, logo, negocio, LADO // 2, 920, 140, p["oscuro"], centrado=True)


def _estilo_sol(c, rnd, p, recorte, foto, nombre, precio, logo, negocio):
    # degradado radial del tono claro al color de marca
    cx, cy = LADO // 2, 620
    grad = Image.radial_gradient("L").resize((LADO * 2, LADO * 2), Image.BILINEAR)
    grad = grad.crop((LADO - cx, LADO - cy, LADO - cx + LADO, LADO - cy + LADO))
    fondo = Image.composite(Image.new("RGBA", (LADO, LADO), p["base"] + (255,)),
                            Image.new("RGBA", (LADO, LADO), p["medio"] + (255,)), grad.point(lambda v: min(255, int(v * 1.3))))
    c.alpha_composite(fondo)
    rayos = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    dr = ImageDraw.Draw(rayos)
    for k in range(24):
        a0 = 2 * math.pi * k / 24
        a1 = a0 + math.pi / 24
        dr.polygon([(cx, cy), (cx + 1600 * math.cos(a0), cy + 1600 * math.sin(a0)),
                    (cx + 1600 * math.cos(a1), cy + 1600 * math.sin(a1))], fill=(255, 255, 255, 34))
    c.alpha_composite(rayos)
    d = ImageDraw.Draw(c)
    d.ellipse([cx - 330, cy - 330, cx + 330, cy + 330], fill=_hls(p["base"], l=0.86) + (255,))
    _confeti(d, rnd, [p["acento"], (255, 255, 255), p["comp"]], 14, (40, 280, 1040, 1040))
    fin = _titulo(c, nombre, 60, 56, 960, (255, 255, 255), borde=p["oscuro"], centrado=True, tam_max=160)
    _producto(c, recorte, foto, (140, max(fin + 10, 320), 940, 990))
    _sticker_precio(c, precio, 880, 900, 110, p["acento"], p["oscuro"], giro=-8)
    _logo(c, logo, negocio, 60, 880, 150, p["oscuro"])


def _con_alfa(rgb, a):
    return tuple(rgb) + (a,)


def _capa(c, dibujar):
    """Dibuja con transparencia sobre una capa aparte y la funde con el lienzo."""
    capa = Image.new("RGBA", c.size, (0, 0, 0, 0))
    dibujar(ImageDraw.Draw(capa))
    c.alpha_composite(capa)


def _estilo_puntos(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["claro"])
    for fila, y in enumerate(range(-20, LADO + 60, 72)):
        for x in range(-20 + (36 if fila % 2 else 0), LADO + 60, 72):
            d.ellipse([x - 13, y - 13, x + 13, y + 13], fill=p["medio"])
    d.rounded_rectangle([40, 40, LADO - 40, LADO - 40], radius=48, outline=p["base"], width=10)
    fin = _titulo(c, nombre, 80, 76, 920, p["base"], borde=(255, 255, 255), centrado=True)
    _producto(c, recorte, foto, (150, max(fin + 10, 320), 930, 990))
    _sticker_precio(c, precio, 870, 860, 108, p["acento"], p["oscuro"], giro=10)
    _logo(c, marca, None, 70, 870, 150, p["oscuro"])


def _estilo_diagonal(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["base"])
    d.polygon([(0, 760), (LADO, 420), (LADO, LADO), (0, LADO)], fill=p["comp"])
    d.polygon([(0, 730), (LADO, 390), (LADO, 420), (0, 760)], fill=(255, 255, 255))
    _confeti(d, rnd, [p["acento"], (255, 255, 255)], 10, (40, 240, 1040, 600))
    fin = _titulo(c, nombre, 70, 64, 900, (255, 255, 255), sombra=p["oscuro"])
    _producto(c, recorte, foto, (160, max(fin + 10, 320), 940, 1000))
    _sticker_precio(c, precio, 900, 350 if fin < 260 else fin + 150, 108, p["acento"], p["oscuro"])
    _logo(c, marca, None, 56, 880, 150, p["oscuro"])


def _estilo_marco(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["base"])
    d.rounded_rectangle([46, 46, LADO - 46, LADO - 46], radius=40, fill=p["claro"])
    d.rounded_rectangle([66, 66, LADO - 66, LADO - 66], radius=30, outline=p["medio"], width=4)
    fin = _titulo(c, nombre, 100, 100, 880, p["oscuro"], centrado=True, tam_max=130)
    d.line([(LADO // 2 - 90, fin + 20), (LADO // 2 + 90, fin + 20)], fill=p["base"], width=6)
    _producto(c, recorte, foto, (170, fin + 30, 910, 960))
    _sticker_precio(c, precio, 880, 880, 100, p["base"], (255, 255, 255), giro=-6)
    _logo(c, marca, None, 90, 850, 140, p["oscuro"])


def _estilo_confeti(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["oscuro"])
    colores = [p["acento"], p["comp"], p["medio"], (255, 255, 255), p["base"]]
    for _k in range(70):
        x, y = rnd.randint(0, LADO), rnd.randint(0, LADO)
        col = rnd.choice(colores)
        if rnd.random() < 0.5:
            r = rnd.randint(5, 12)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        else:
            w, h, ang = rnd.randint(14, 30), rnd.randint(6, 10), rnd.random() * math.pi
            pts = [(x + w * math.cos(ang) - h * math.sin(ang), y + w * math.sin(ang) + h * math.cos(ang)),
                   (x - w * math.cos(ang) - h * math.sin(ang), y - w * math.sin(ang) + h * math.cos(ang)),
                   (x - w * math.cos(ang) + h * math.sin(ang), y - w * math.sin(ang) - h * math.cos(ang)),
                   (x + w * math.cos(ang) + h * math.sin(ang), y + w * math.sin(ang) - h * math.cos(ang))]
            d.polygon(pts, fill=col)
    _capa(c, lambda dd: dd.ellipse([180, 330, 900, 1050], fill=_con_alfa(p["base"], 120)))
    fin = _titulo(c, nombre, 70, 60, 940, (255, 255, 255), sombra=p["base"], centrado=True)
    _producto(c, recorte, foto, (150, max(fin + 10, 310), 930, 990))
    _sticker_precio(c, precio, 180, 650, 106, p["acento"], p["oscuro"], giro=8)
    _logo(c, marca, None, LADO - 230, 870, 150, p["oscuro"])


def _estilo_arco(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["base"])
    fin = _fin_titulo(nombre, 60, 940)
    top = max(fin + 20, 300)
    d.rounded_rectangle([170, top, 910, LADO + 200], radius=370, fill=p["claro"])
    d.rounded_rectangle([190, top + 20, 890, LADO + 200], radius=350, outline=p["medio"], width=5)
    _titulo(c, nombre, 70, 60, 940, (255, 255, 255), sombra=p["oscuro"], centrado=True)
    _producto(c, recorte, foto, (200, top + 60, 880, 1000))
    _sticker_precio(c, precio, 920, top + 185, 104, p["acento"], p["oscuro"], giro=-8)
    _logo(c, marca, None, 46, 880, 150, p["oscuro"])


def _estilo_cuadros(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=(255, 255, 255))
    t = 90
    def mantel(dd):
        for k in range(0, LADO, t * 2):
            dd.rectangle([k, 0, k + t, LADO], fill=_con_alfa(p["base"], 110))
            dd.rectangle([0, k, LADO, k + t], fill=_con_alfa(p["base"], 110))
    _capa(c, mantel)
    d = ImageDraw.Draw(c)
    d.rounded_rectangle([60, 50, LADO - 60, _fin_titulo(nombre, 70, 900) + 30], radius=36, fill=p["oscuro"])
    fin = _titulo(c, nombre, 90, 70, 900, (255, 255, 255), centrado=True)
    _producto(c, recorte, foto, (150, fin + 50, 930, 1000))
    _sticker_precio(c, precio, 890, fin + 200, 106, p["acento"], p["oscuro"])
    _logo(c, marca, None, 56, 880, 150, p["oscuro"])


def _estilo_burbujas(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    grad = Image.linear_gradient("L").resize((LADO, LADO))
    c.alpha_composite(Image.composite(Image.new("RGBA", (LADO, LADO), p["comp"] + (255,)),
                                      Image.new("RGBA", (LADO, LADO), p["base"] + (255,)), grad))
    def burbujas(dd):
        for _k in range(22):
            r = rnd.randint(30, 150)
            x, y = rnd.randint(-50, LADO + 50), rnd.randint(-50, LADO + 50)
            dd.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(18, 50)))
    _capa(c, burbujas)
    fin = _titulo(c, nombre, 70, 64, 940, (255, 255, 255), sombra=p["oscuro"], centrado=True)
    _producto(c, recorte, foto, (150, max(fin + 10, 310), 930, 990))
    _sticker_precio(c, precio, 880, 880, 106, (255, 255, 255), p["oscuro"], giro=-10)
    _logo(c, marca, None, 56, 880, 150, p["oscuro"])


def _hoja(d, cx, cy, largo, ancho, ang, color, vena):
    pts = []
    for k in range(0, 41):
        t = k / 40
        x = (t - 0.5) * largo
        y = math.sin(t * math.pi) * ancho / 2
        pts.append((x, y))
    pts += [(x, -y) for x, y in reversed(pts)]
    ca, sa = math.cos(ang), math.sin(ang)
    rot = [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]
    d.polygon(rot, fill=color)
    d.line([(cx - largo / 2 * ca, cy - largo / 2 * sa), (cx + largo / 2 * ca, cy + largo / 2 * sa)], fill=vena, width=4)


def _estilo_tropical(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["base"])
    verde, verde2 = (46, 122, 74), (82, 160, 96)
    for (cx, cy, l, a, ang, col) in [(60, 980, 420, 140, -0.9, verde), (190, 1040, 360, 120, -1.3, verde2),
                                     (1030, 960, 420, 140, -2.3, verde), (900, 1050, 340, 110, -1.9, verde2),
                                     (1040, 260, 300, 100, 2.5, verde2), (40, 330, 280, 96, 0.6, verde2)]:
        _hoja(d, cx, cy, l, a, ang, col, (255, 255, 255, 120) if False else _hls(col, l=0.7))
    d.ellipse([LADO - 230, 120, LADO - 90, 260], fill=p["acento"])
    fin = _titulo(c, nombre, 70, 60, 900, (255, 255, 255), sombra=p["oscuro"])
    _producto(c, recorte, foto, (150, max(fin + 10, 310), 930, 990))
    _sticker_precio(c, precio, 880, max(fin + 165, 450), 104, (255, 255, 255), p["oscuro"], giro=8)
    _logo(c, marca, None, LADO // 2, 890, 140, p["oscuro"], centrado=True)


def _estilo_minimal(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["claro"])
    fin = _titulo(c, nombre, 80, 80, 920, p["base"], tam_max=120)
    d.line([(84, fin + 22), (260, fin + 22)], fill=p["base"], width=6)
    _producto(c, recorte, foto, (180, fin + (170 if precio else 40), 940, 1000))
    if precio:
        f_num = _fuente("Bricolage-800.woff", 112)
        f_sim = _fuente("Bricolage-800-ext.woff", 90)
        d.text((84, fin + 56), "₡", font=f_sim, fill=p["base"])
        d.text((84 + d.textlength("₡", font=f_sim) + 8, fin + 40), _precio_txt(precio), font=f_num, fill=p["base"])
    _logo(c, marca, None, 70, 880, 140, p["oscuro"])


def _estilo_goteo(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["claro"])
    fin = _fin_titulo(nombre, 50, 940)
    base_y = fin + 40
    d.rectangle([0, 0, LADO, base_y], fill=p["base"])
    x = 0
    while x < LADO:
        w = rnd.randint(70, 130)
        largo = rnd.randint(30, 150)
        d.rounded_rectangle([x + 8, base_y - 20, x + w - 8, base_y + largo], radius=(w - 16) // 2, fill=p["base"])
        d.ellipse([x + w - 20, base_y - 12, x + w + 20, base_y + 18], fill=p["base"])
        x += w
    _titulo(c, nombre, 70, 50, 940, (255, 255, 255), sombra=p["oscuro"], centrado=True)
    _producto(c, recorte, foto, (150, base_y + 60, 930, 1000))
    _sticker_precio(c, precio, 900, base_y + 210, 104, p["acento"], p["oscuro"])
    _logo(c, marca, None, 56, 880, 150, p["oscuro"])


def _estilo_estallido(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=p["comp"])
    fin = _fin_titulo(nombre, 60, 940)
    cx, cy = LADO // 2, max(fin + 330, 640)
    pts = []
    for k in range(40):
        ang = math.pi * 2 * k / 40
        r = 400 if k % 2 == 0 else 320
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=p["acento"])
    d.ellipse([cx - 290, cy - 290, cx + 290, cy + 290], fill=(255, 255, 255))
    _titulo(c, nombre, 70, 60, 940, (255, 255, 255), borde=p["oscuro"], centrado=True)
    _producto(c, recorte, foto, (cx - 330, cy - 300, cx + 330, min(cy + 330, 1010)))
    _sticker_precio(c, precio, 900, 900, 104, p["base"], (255, 255, 255), giro=-8)
    _logo(c, marca, None, 56, 880, 150, p["oscuro"])


def _estilo_pizarra(c, rnd, p, recorte, foto, nombre, precio, marca, _):
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, LADO, LADO], fill=(38, 42, 40))
    def tiza(dd):
        for _k in range(900):
            x, y = rnd.randint(0, LADO), rnd.randint(0, LADO)
            dd.point((x, y), fill=(255, 255, 255, rnd.randint(10, 40)))
    _capa(c, tiza)
    d = ImageDraw.Draw(c)
    d.rectangle([24, 24, LADO - 24, LADO - 24], outline=(150, 110, 70), width=18)
    fin = _titulo(c, nombre, 80, 70, 920, (250, 246, 236), centrado=True)
    d.line([(LADO // 2 - 140, fin + 20), (LADO // 2 + 140, fin + 20)], fill=p["acento"], width=5)
    _producto(c, recorte, foto, (160, fin + 30, 920, 990))
    _sticker_precio(c, precio, 880, 880, 100, p["acento"], (38, 42, 40), giro=-8)
    _logo(c, marca, None, 70, 870, 150, p["oscuro"])


DIBUJAR = {"ola": _estilo_ola, "rayas": _estilo_rayas, "sol": _estilo_sol, "puntos": _estilo_puntos,
           "diagonal": _estilo_diagonal, "marco": _estilo_marco, "confeti": _estilo_confeti, "arco": _estilo_arco,
           "cuadros": _estilo_cuadros, "burbujas": _estilo_burbujas, "tropical": _estilo_tropical,
           "minimal": _estilo_minimal, "goteo": _estilo_goteo, "estallido": _estilo_estallido, "pizarra": _estilo_pizarra}
NOMBRES = {"ola": "Olas", "rayas": "Rayas", "sol": "Sol", "puntos": "Puntos", "diagonal": "Diagonal", "marco": "Marco",
           "confeti": "Confeti", "arco": "Arco", "cuadros": "Mantel", "burbujas": "Burbujas", "tropical": "Tropical",
           "minimal": "Minimalista", "goteo": "Goteo", "estallido": "Estallido", "pizarra": "Pizarra"}
ESTILOS = tuple(DIBUJAR)


def estilo_para(estilo: str | None, rid: int) -> str:
    return estilo if estilo in ESTILOS else ESTILOS[rid % len(ESTILOS)]


def componer(foto: Image.Image, recorte: Image.Image | None, nombre: str, precio: float | None,
             color: str, logo: Image.Image | None, negocio: str, estilo: str, semilla: int,
             modo: str = "logo", eslogan: str = "") -> Image.Image:
    c = Image.new("RGBA", (LADO, LADO), (255, 255, 255, 255))
    marca = Marca(modo, logo, negocio, eslogan)
    DIBUJAR[estilo](c, random.Random(semilla), paleta(color), recorte, foto, nombre, precio, marca, negocio)
    return c.convert("RGB")


def a_jpeg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=88, optimize=True, progressive=True)
    return b.getvalue()
