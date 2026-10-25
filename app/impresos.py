"""Diseños para imprimir o compartir con el código QR del menú.

Cada formato devuelve una imagen RGB a tamaño real de impresión (300 ppp salvo el banner).
Usa el nombre, la frase, el color, el lugar y los contactos del negocio.
"""
import io
import math
import re

import qrcode
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from qrcode.constants import ERROR_CORRECT_H

from . import arte

# formato: (nombre visible, ancho px, alto px, ppp, descripción)
FORMATOS = {
    "mesa": ("Tarjeta de mesa", 1240, 1748, 300, "A6 vertical (10,5 × 14,8 cm). Para porta-menús o habladores en las mesas."),
    "afiliado": ("Sticker para negocios aliados", 1181, 1181, 300, "Cuadrado de 10 × 10 cm. Para pegar en vitrinas, cajas o mostradores."),
    "afiche": ("Afiche", 2480, 3508, 300, "Hoja A4 vertical. Para paredes, puertas o carteleras."),
    "banner": ("Banner horizontal", 4500, 1500, 150, "Proporción 3:1 (75 × 25 cm). Para mostrador, toldo o rotulación."),
    "tarjeta": ("Tarjeta de presentación", 1063, 591, 300, "9 × 5 cm. Para entregar con cada pedido."),
    "estado": ("Estado / historia", 1080, 1920, 72, "1080 × 1920 px. Para estados de WhatsApp e historias de Instagram."),
}

BLANCO = (255, 255, 255)


# ------------------------------------------------------------------ datos
def _host(url: str) -> str:
    h = re.sub(r"^https?://", "", url or "").rstrip("/")
    return h[4:] if h.startswith("www.") else h


def _instagram(v: str) -> str:
    v = (v or "").strip().rstrip("/")
    if not v:
        return ""
    m = re.search(r"instagram\.com/([^/?#]+)", v)
    return "@" + (m.group(1) if m else v.lstrip("@"))


def _telefono(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d.startswith("506"):
        d = d[3:]
    return f"{d[:4]}-{d[4:]}" if len(d) == 8 else (v or "").strip()


class Datos:
    def __init__(self, negocio: dict, url: str, categorias: list[str]):
        self.nombre = (negocio.get("nombre") or "Nuestro menú").strip()
        self.eslogan = (negocio.get("eslogan") or "").strip()
        self.lugar = (negocio.get("lugar") or "").strip()
        self.whatsapp = _telefono(negocio.get("whatsapp") or negocio.get("telefono") or "")
        self.instagram = _instagram(negocio.get("instagram"))
        self.url = url
        self.web = _host(url)
        self.cats = [c for c in categorias if c][:4]
        self.gorro = (negocio.get("distintivo") or "logo") == "gorro"
        p = arte.paleta(negocio.get("color") or "#D94C72")
        self.base, self.oscuro, self.claro, self.acento = p["base"], p["oscuro"], p["claro"], p["acento"]
        self.logo = negocio.get("_logo_img")  # imagen PIL si el modo es "logo"


# ------------------------------------------------------------------ piezas
def _f(nombre, tam):
    return arte._fuente(nombre, max(8, int(tam)))


def _degradado(c, caja, arriba, abajo):
    x0, y0, x1, y1 = caja
    d = ImageDraw.Draw(c)
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0 - 1)
        d.line([(x0, y), (x1, y)], fill=tuple(round(arriba[i] + (abajo[i] - arriba[i]) * t) for i in range(3)))


def _paisaje(c, caja, s, base_y_rel=0.62, molinos=True):
    """Paisaje de Tilarán: sol, molinos, colina, lago y pasto, dentro de la caja (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = caja
    w, h = x1 - x0, y1 - y0
    d = ImageDraw.Draw(c)
    cy = y0 + h * base_y_rel
    # sol
    r = h * 0.2
    sx, sy = x0 + w * 0.82, cy - h * 0.02
    d.ellipse([sx - r * 1.4, sy - r * 1.4, sx + r * 1.4, sy + r * 1.4], fill=(242, 140, 110))
    d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 186, 92))
    if molinos:
        for fx, fs in ((0.08, 1), (0.2, 0.78), (0.7, 0.85), (0.93, 0.7)):
            alto = h * 0.36 * fs
            bx, by = x0 + w * fx, cy + h * 0.02
            g = max(2, int(s * 3.2))
            d.line([(bx, by), (bx, by - alto)], fill=BLANCO, width=g)
            for a in (90, 210, 330):
                ra = math.radians(a)
                d.line([(bx, by - alto), (bx + alto * 0.42 * math.cos(ra), by - alto - alto * 0.42 * math.sin(ra))], fill=BLANCO, width=g)
    paso = max(4, w // 120)
    d.polygon([(x0, cy)] + [(x, cy - h * 0.1 * math.sin((x - x0) / w * math.pi)) for x in range(x0, x1 + paso, paso)]
              + [(x1, y1), (x0, y1)], fill=(232, 124, 106))
    lago = cy + h * 0.1
    d.rectangle([x0, lago, x1, y1], fill=(79, 163, 218))
    pasto = cy + h * 0.24
    d.polygon([(x0, pasto)] + [(x, pasto - h * 0.045 * math.sin((x - x0) / (w / 4))) for x in range(x0, x1 + paso, paso)]
              + [(x1, y1), (x0, y1)], fill=(110, 154, 52))


def _marca(c, D: Datos, cx, y, ancho_max, tam, tinta, eslogan=True, color_letra=BLANCO):
    """Logotipo: nombre en letra manuscrita (blanca con borde), gorro sobre la inicial y la frase debajo.
    Devuelve la coordenada y donde termina."""
    d = ImageDraw.Draw(c)
    if D.logo is not None:  # logo de imagen en círculo blanco
        lado = int(tam * 1.25)
        d.ellipse([cx - lado / 2, y, cx + lado / 2, y + lado], fill=BLANCO)
        dentro = ImageOps.contain(D.logo.convert("RGBA"), (int(lado * 0.78), int(lado * 0.78)), Image.LANCZOS)
        c.alpha_composite(dentro, (int(cx - dentro.width / 2), int(y + (lado - dentro.height) / 2)))
        y += lado + tam * 0.1
        f = _f("Allura.woff", tam * 0.8)
        w = d.textlength(D.nombre, font=f)
        d.text((cx - w / 2, y), D.nombre, font=f, fill=color_letra, stroke_width=max(2, int(tam * 0.035)), stroke_fill=tinta)
        y += tam * 0.85
    else:
        f = _f("Allura.woff", tam)
        while d.textlength(D.nombre, font=f) > ancho_max and tam > 20:
            tam *= 0.94
            f = _f("Allura.woff", tam)
        w = d.textlength(D.nombre, font=f)
        tx = cx - w / 2
        borde = max(2, int(tam * 0.042))
        if D.gorro:
            g = arte.gorro()
            g = ImageOps.contain(g, (int(tam * 0.62), int(tam * 0.62)), Image.LANCZOS)
            col = ImageOps.colorize(ImageOps.grayscale(g), black=tinta, white=BLANCO).convert("RGBA")
            col.putalpha(g.split()[3])
            y_g = y
            y += tam * 0.32
            c.alpha_composite(col, (int(tx + tam * 0.1), int(y_g)))
        sombra = Image.new("RGBA", c.size, (0, 0, 0, 0))
        ImageDraw.Draw(sombra).text((tx + tam * 0.02, y + tam * 0.06), D.nombre, font=f, fill=(0, 0, 0, 110),
                                    stroke_width=borde + 1, stroke_fill=(0, 0, 0, 110))
        c.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(max(2, tam * 0.04))))
        d.text((tx, y), D.nombre, font=f, fill=color_letra, stroke_width=borde, stroke_fill=tinta)
        y += tam * 1.08
    if eslogan and D.eslogan:
        fe = _f("Bricolage-800.woff", tam * 0.2)
        track = tam * 0.04
        we = arte._ancho_espaciado(d, D.eslogan, fe, track)
        arte._espaciado(d, (cx - we / 2, y), D.eslogan, fe, color_letra, track)
        y += tam * 0.32
    return y


def _qr_img(url, lado, oscuro):
    q = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=0, box_size=10)
    q.add_data(url)
    q.make(fit=True)
    img = q.make_image(fill_color=oscuro, back_color="white").convert("RGB")
    return img.resize((lado, lado), Image.NEAREST)


def _qr(c, D: Datos, x, y, lado, marco=True):
    """QR en una tarjeta blanca redondeada, con el gorro (o una inicial) al centro."""
    d = ImageDraw.Draw(c)
    pad = int(lado * 0.07)
    if marco:
        sombra = Image.new("RGBA", c.size, (0, 0, 0, 0))
        ImageDraw.Draw(sombra).rounded_rectangle([x - pad, y - pad + lado * 0.03, x + lado + pad, y + lado + pad + lado * 0.03],
                                                radius=pad * 1.6, fill=(0, 0, 0, 70))
        c.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(lado * 0.03)))
        d.rounded_rectangle([x - pad, y - pad, x + lado + pad, y + lado + pad], radius=pad * 1.6, fill=BLANCO)
    q = _qr_img(D.url, lado, D.oscuro)
    c.paste(q, (int(x), int(y)))
    # centro
    r = lado * 0.12
    cx, cy = x + lado / 2, y + lado / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BLANCO)
    d.ellipse([cx - r * 0.86, cy - r * 0.86, cx + r * 0.86, cy + r * 0.86], fill=D.base)
    if D.logo is not None:
        dentro = ImageOps.contain(D.logo.convert("RGBA"), (int(r * 1.3), int(r * 1.3)), Image.LANCZOS)
        d.ellipse([cx - r * 0.86, cy - r * 0.86, cx + r * 0.86, cy + r * 0.86], fill=BLANCO)
        c.alpha_composite(dentro, (int(cx - dentro.width / 2), int(cy - dentro.height / 2)))
    else:
        g = arte.gorro()
        g = ImageOps.contain(g, (int(r * 1.15), int(r * 1.15)), Image.LANCZOS)
        blanco = Image.new("RGBA", g.size, BLANCO + (0,))
        blanco.putalpha(g.split()[3])
        c.alpha_composite(blanco, (int(cx - g.width / 2), int(cy - g.height / 2 - r * 0.04)))
    return y + lado + pad


def _texto_centro(c, texto, cx, y, fuente, tam, color, ancho_max=None):
    d = ImageDraw.Draw(c)
    f = _f(fuente, tam)
    if ancho_max:
        while d.textlength(texto, font=f) > ancho_max and tam > 10:
            tam *= 0.95
            f = _f(fuente, tam)
    w = d.textlength(texto, font=f)
    d.text((cx - w / 2, y), texto, font=f, fill=color)
    return y + tam * 1.22


def _icono_whatsapp(c, x, y, tam):
    d = ImageDraw.Draw(c)
    verde = (37, 211, 102)
    d.ellipse([x, y, x + tam, y + tam], fill=verde)
    d.polygon([(x + tam * 0.12, y + tam * 0.98), (x + tam * 0.2, y + tam * 0.66), (x + tam * 0.42, y + tam * 0.86)], fill=verde)
    # auricular
    s = tam
    d.ellipse([x + s * 0.24, y + s * 0.24, x + s * 0.76, y + s * 0.76], outline=BLANCO, width=max(2, int(s * 0.075)))
    d.rectangle([x + s * 0.5, y + s * 0.5, x + s * 0.8, y + s * 0.8], fill=verde)
    d.ellipse([x + s * 0.3, y + s * 0.27, x + s * 0.44, y + s * 0.43], fill=BLANCO)
    d.ellipse([x + s * 0.55, y + s * 0.56, x + s * 0.71, y + s * 0.7], fill=BLANCO)


def _icono_instagram(c, x, y, tam, color):
    d = ImageDraw.Draw(c)
    g = max(2, int(tam * 0.1))
    d.rounded_rectangle([x, y, x + tam, y + tam], radius=tam * 0.28, outline=color, width=g)
    d.ellipse([x + tam * 0.27, y + tam * 0.27, x + tam * 0.73, y + tam * 0.73], outline=color, width=g)
    d.ellipse([x + tam * 0.7, y + tam * 0.16, x + tam * 0.82, y + tam * 0.28], fill=color)


def _icono_web(c, x, y, tam, color):
    d = ImageDraw.Draw(c)
    g = max(2, int(tam * 0.08))
    d.ellipse([x, y, x + tam, y + tam], outline=color, width=g)
    d.ellipse([x + tam * 0.28, y, x + tam * 0.72, y + tam], outline=color, width=g)
    d.line([(x, y + tam / 2), (x + tam, y + tam / 2)], fill=color, width=g)


def _contactos(D: Datos, web=True):
    filas = []
    if D.whatsapp:
        filas.append(("wa", D.whatsapp))
    if D.instagram:
        filas.append(("ig", D.instagram))
    if D.web and web:
        filas.append(("web", D.web))
    return filas


def _fila_contactos(c, D, cx, y, tam, color, ancho_max, sep=None, web=True):
    """Contactos en una línea centrada con íconos."""
    d = ImageDraw.Draw(c)
    filas = _contactos(D, web)
    if not filas:
        return y
    while True:
        f = _f("PublicSans-700.woff", tam)
        sep_ = sep if sep is not None else tam * 1.4
        anchos = [tam * 1.25 + d.textlength(t, font=f) for _, t in filas]
        total = sum(anchos) + sep_ * (len(filas) - 1)
        if total <= ancho_max or tam < 12:
            break
        tam *= 0.92
    x = cx - total / 2
    for (k, t), w in zip(filas, anchos):
        ic = tam * 1.0
        iy = y + tam * 0.12
        if k == "wa":
            _icono_whatsapp(c, x, iy, ic)
        elif k == "ig":
            _icono_instagram(c, x, iy, ic, color)
        else:
            _icono_web(c, x, iy, ic, color)
        d.text((x + tam * 1.25, y), t, font=f, fill=color)
        x += w + sep_
    return y + tam * 1.5


def _lista_contactos(c, D, x, y, tam, color):
    """Contactos uno debajo del otro, alineados a la izquierda."""
    d = ImageDraw.Draw(c)
    f = _f("PublicSans-700.woff", tam)
    for k, t in _contactos(D):
        if k == "wa":
            _icono_whatsapp(c, x, y + tam * 0.12, tam)
        elif k == "ig":
            _icono_instagram(c, x, y + tam * 0.12, tam, color)
        else:
            _icono_web(c, x, y + tam * 0.12, tam, color)
        d.text((x + tam * 1.35, y), t, font=f, fill=color)
        y += tam * 1.6
    return y


def _pildora(c, texto, cx, y, tam, fondo, color):
    d = ImageDraw.Draw(c)
    f = _f("Bricolage-800.woff", tam)
    w = d.textlength(texto, font=f)
    px, py = tam * 0.8, tam * 0.42
    d.rounded_rectangle([cx - w / 2 - px, y, cx + w / 2 + px, y + tam + py * 2], radius=(tam + py * 2) / 2, fill=fondo)
    d.text((cx - w / 2, y + py - tam * 0.1), texto, font=f, fill=color)
    return y + tam + py * 2


def _cats(D):
    return "  ·  ".join(D.cats)


# ------------------------------------------------------------------ formatos
def _mesa(D: Datos, W, H):
    c = Image.new("RGBA", (W, H), D.claro + (255,))
    s = W / 1240
    alto_cab = int(H * 0.36)
    _degradado(c, (0, 0, W, alto_cab), D.oscuro, D.base)
    _paisaje(c, (0, int(alto_cab * 0.45), W, alto_cab), s, base_y_rel=0.5)
    y = _marca(c, D, W / 2, 70 * s, W * 0.86, 210 * s, D.oscuro)
    if D.lugar:
        _pildora(c, D.lugar, W / 2, y + 6 * s, 34 * s, BLANCO, D.oscuro)
    # ola de transición
    d = ImageDraw.Draw(c)
    d.polygon([(0, alto_cab - 30 * s)] + [(x, alto_cab - 30 * s + 26 * s * math.sin(x / W * math.tau)) for x in range(0, W + 8, 8)]
              + [(W, H), (0, H)], fill=D.claro)
    y = alto_cab + 40 * s
    y = _texto_centro(c, "Vea nuestro menú", W / 2, y, "Bricolage-800.woff", 92 * s, D.oscuro, W * 0.9)
    y = _texto_centro(c, "Escanee el código con la cámara de su teléfono", W / 2, y + 2 * s, "PublicSans-500.woff", 36 * s, D.oscuro, W * 0.88)
    lado = int(560 * s)
    y = _qr(c, D, (W - lado) / 2, y + 60 * s, lado)
    if D.cats:
        y = _texto_centro(c, _cats(D), W / 2, y + 56 * s, "Bricolage-800.woff", 40 * s, D.base, W * 0.9)
    _fila_contactos(c, D, W / 2, H - 120 * s, 38 * s, D.oscuro, W * 0.9)
    return c


def _afiliado(D: Datos, W, H):
    c = Image.new("RGBA", (W, H), D.base + (255,))
    s = W / 1181
    _degradado(c, (0, 0, W, H), D.base, D.oscuro)
    d = ImageDraw.Draw(c)
    # confeti suave
    import random
    rnd = random.Random(7)
    for k in range(28):
        r = rnd.uniform(6, 15) * s
        x = rnd.uniform(0, W * 0.13) if k % 2 else rnd.uniform(W * 0.87, W)
        y = rnd.uniform(H * 0.08, H * 0.92)
        d.ellipse([x - r, y - r, x + r, y + r], fill=rnd.choice([D.acento, D.claro, (255, 186, 92)]))
    m = 34 * s
    d.rounded_rectangle([m, m, W - m, H - m], radius=60 * s, outline=BLANCO, width=int(6 * s))
    y = _texto_centro(c, "¿Antojo de algo dulce?", W / 2, 92 * s, "Bricolage-800.woff", 72 * s, BLANCO, W * 0.84)
    y = _marca(c, D, W / 2, y + 4 * s, W * 0.8, 170 * s, D.oscuro)
    lado = int(420 * s)
    qx = W / 2 - lado / 2
    yq = y + 30 * s
    _qr(c, D, qx, yq, lado)
    y = yq + lado + 70 * s
    _texto_centro(c, "Escanee y pida por WhatsApp", W / 2, y, "Bricolage-800.woff", 44 * s, BLANCO, W * 0.84)
    return c


def _afiche(D: Datos, W, H):
    c = Image.new("RGBA", (W, H), D.claro + (255,))
    s = W / 2480
    alto_cab = int(H * 0.42)
    _degradado(c, (0, 0, W, alto_cab), D.oscuro, D.base)
    _paisaje(c, (0, int(alto_cab * 0.38), W, alto_cab + int(60 * s)), s * 1.6, base_y_rel=0.55)
    y = 150 * s
    if D.lugar:
        y = _pildora(c, D.lugar, W / 2, y, 64 * s, BLANCO, D.oscuro) + 40 * s
    _marca(c, D, W / 2, y, W * 0.86, 420 * s, D.oscuro)
    y = alto_cab + 150 * s
    y = _texto_centro(c, "Nuestro menú,", W / 2, y, "Bricolage-800.woff", 170 * s, D.oscuro, W * 0.9)
    y = _texto_centro(c, "en su teléfono", W / 2, y - 20 * s, "Bricolage-800.woff", 170 * s, D.base, W * 0.9)
    lado = int(840 * s)
    y = _qr(c, D, (W - lado) / 2, y + 80 * s, lado)
    y = _texto_centro(c, "Abra la cámara y apunte al código", W / 2, y + 50 * s, "PublicSans-500.woff", 60 * s, D.oscuro, W * 0.9)
    if D.cats:
        _texto_centro(c, _cats(D), W / 2, y + 20 * s, "Bricolage-800.woff", 72 * s, D.base, W * 0.9)
    d = ImageDraw.Draw(c)
    d.rectangle([0, H - 230 * s, W, H], fill=D.oscuro)
    _fila_contactos(c, D, W / 2, H - 160 * s, 66 * s, BLANCO, W * 0.9)
    return c


def _banner(D: Datos, W, H):
    c = Image.new("RGBA", (W, H), D.oscuro + (255,))
    s = H / 1500
    _degradado(c, (0, 0, W, H), D.oscuro, D.base)
    _paisaje(c, (0, int(H * 0.45), W, H), s * 2.2, base_y_rel=0.42)
    # zona de texto (izquierda)
    zona = W * 0.62
    y = 170 * s
    y = _marca(c, D, zona / 2 + 60 * s, y, zona * 0.9, 440 * s, D.oscuro)
    if D.cats:
        _pildora(c, _cats(D), zona / 2 + 60 * s, y + 50 * s, 76 * s, BLANCO, D.oscuro)
    # tarjeta del QR (derecha)
    lado = int(860 * s)
    tx0, ty0 = W * 0.7, (H - lado) / 2 - 110 * s
    d = ImageDraw.Draw(c)
    ancho_t = lado + 200 * s
    cx = tx0 + lado / 2
    d.rounded_rectangle([cx - ancho_t / 2, ty0 - 90 * s, cx + ancho_t / 2, ty0 + lado + 330 * s], radius=80 * s, fill=BLANCO)
    _qr(c, D, tx0, ty0, lado, marco=False)
    y = _texto_centro(c, "Escanee y vea el menú", cx, ty0 + lado + 60 * s, "Bricolage-800.woff", 80 * s, D.oscuro, ancho_t * 0.88)
    if D.whatsapp:
        f = _f("PublicSans-700.woff", 64 * s)
        t = D.whatsapp
        w = d.textlength(t, font=f) + 80 * s
        x = cx - w / 2
        _icono_whatsapp(c, x, y + 14 * s, 62 * s)
        d.text((x + 80 * s, y + 6 * s), t, font=f, fill=D.oscuro)
    return c


def _tarjeta(D: Datos, W, H):
    c = Image.new("RGBA", (W, H), BLANCO + (255,))
    s = W / 1063
    d = ImageDraw.Draw(c)
    izq = int(W * 0.6)
    _degradado(c, (0, 0, izq, H), D.oscuro, D.base)
    _paisaje(c, (0, int(H * 0.62), izq, H), s * 0.8, base_y_rel=0.3, molinos=False)
    y = _marca(c, D, izq / 2, 48 * s, izq * 0.86, 120 * s, D.oscuro)
    if D.lugar:
        _texto_centro(c, D.lugar, izq / 2, y + 2 * s, "PublicSans-700.woff", 26 * s, BLANCO, izq * 0.9)
    lado = int(300 * s)
    qx = izq + (W - izq - lado) / 2
    _qr(c, D, qx, 52 * s, lado, marco=False)
    y = 52 * s + lado + 18 * s
    y = _texto_centro(c, "Vea el menú", izq + (W - izq) / 2, y, "Bricolage-800.woff", 30 * s, D.oscuro, (W - izq) * 0.9)
    if D.whatsapp:
        f = _f("PublicSans-700.woff", 26 * s)
        w = d.textlength(D.whatsapp, font=f) + 34 * s
        x = izq + (W - izq - w) / 2
        _icono_whatsapp(c, x, y + 4 * s, 26 * s)
        d.text((x + 34 * s, y), D.whatsapp, font=f, fill=D.oscuro)
    return c


def _estado(D: Datos, W, H):
    c = Image.new("RGBA", (W, H), D.claro + (255,))
    s = W / 1080
    _degradado(c, (0, 0, W, H), D.oscuro, D.base)
    _paisaje(c, (0, int(H * 0.8), W, H), s * 1.2, base_y_rel=0.32)
    y = 190 * s
    if D.lugar:
        y = _pildora(c, D.lugar, W / 2, y, 34 * s, BLANCO, D.oscuro) + 30 * s
    y = _marca(c, D, W / 2, y, W * 0.86, 200 * s, D.oscuro)
    y = _texto_centro(c, "¡Ya puede ver nuestro menú!", W / 2, y + 40 * s, "Bricolage-800.woff", 64 * s, BLANCO, W * 0.88)
    lado = int(520 * s)
    y = _qr(c, D, (W - lado) / 2, y + 60 * s, lado)
    y = _texto_centro(c, D.web or "", W / 2, y + 40 * s, "Bricolage-800.woff", 52 * s, BLANCO, W * 0.88)
    _fila_contactos(c, D, W / 2, y + 16 * s, 42 * s, BLANCO, W * 0.9, web=False)
    return c


DIBUJAR = {"mesa": _mesa, "afiliado": _afiliado, "afiche": _afiche, "banner": _banner, "tarjeta": _tarjeta, "estado": _estado}


def generar(formato: str, negocio: dict, url: str, categorias: list[str]) -> Image.Image:
    _, W, H, _, _ = FORMATOS[formato]
    return DIBUJAR[formato](Datos(negocio, url, categorias), W, H).convert("RGB")


def exportar(img: Image.Image, formato: str, tipo: str) -> tuple[bytes, str]:
    """Devuelve (bytes, media_type). tipo: png | pdf | vista (JPEG pequeño para la vista previa)."""
    buf = io.BytesIO()
    if tipo == "pdf":
        img.save(buf, "PDF", resolution=float(FORMATOS[formato][3]))
        return buf.getvalue(), "application/pdf"
    if tipo == "vista":
        v = img.copy()
        v.thumbnail((640, 640), Image.LANCZOS)
        v.save(buf, "JPEG", quality=82)
        return buf.getvalue(), "image/jpeg"
    img.save(buf, "PNG", optimize=True, dpi=(FORMATOS[formato][3],) * 2)
    return buf.getvalue(), "image/png"
