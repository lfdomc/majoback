"""Reportes descargables en Excel (.xlsx) y PDF: productos, ingredientes, recetas y lista de compras.

Cada reporte se arma una sola vez como una lista de secciones (título + tabla) y luego se
exporta al formato pedido, para que el Excel y el PDF digan exactamente lo mismo.
"""
import io
import os
from datetime import datetime


FUENTES = os.path.join(os.path.dirname(__file__), "fuentes")

# tipos de columna: texto | colones | colones_unit | pct | num | cantidad
class Seccion:
    def __init__(self, titulo, columnas, filas, total=None, nota="", hoja=None):
        self.titulo, self.columnas, self.filas, self.total, self.nota = titulo, columnas, filas, total, nota
        self.hoja = hoja or titulo


class Reporte:
    def __init__(self, titulo, subtitulo, secciones, horizontal=False):
        self.titulo, self.subtitulo, self.secciones, self.horizontal = titulo, subtitulo, secciones, horizontal


# ------------------------------------------------------------------ formato de valores
def cant(v):
    """Cantidad sin decimales de más: 1 / 2,5 / 0,25."""
    v = float(v or 0)
    return _num(v, 0) if v.is_integer() else _num(v, 2).rstrip("0").rstrip(",")


def _num(v, dec=0):
    s = f"{v:,.{dec}f}"
    return s.replace(",", " ").replace(".", ",")  # 1 234,56 (Costa Rica)


def texto(v, tipo):
    if v is None or v == "":
        return "—"
    if tipo == "colones":
        return ("-" if v < 0 else "") + "₡" + _num(abs(round(v)))
    if tipo == "colones_unit":
        a = abs(v)
        dec = 0 if a >= 100 else 1 if a >= 10 else 2 if a >= 1 else 3
        return ("-" if v < 0 else "") + "₡" + _num(a, dec)
    if tipo == "pct":
        return _num(v, 1) + " %"
    if tipo == "num":
        return _num(v, 0 if float(v).is_integer() else 2)
    return str(v)


CORTAS = {"unidad": "unid.", "porcion": "porción", "docena": "doc.", "oz_fl": "oz líq.", "cdita": "cdita", "cda": "cda", "l": "L"}


def unidad(u, cantidad=None):
    return CORTAS.get(u, u or "")


def cantidad_legible(q, u):
    """3200 g -> 3,2 kg; 1500 ml -> 1,5 L."""
    if q is None:
        return "—"
    if u == "g" and q >= 1000:
        return f"{_num(q / 1000, 2)} kg"
    if u == "ml" and q >= 1000:
        return f"{_num(q / 1000, 2)} L"
    return f"{_num(q, 0 if float(q).is_integer() or q >= 100 else 1)} {unidad(u)}"


# ------------------------------------------------------------------ Excel
def a_excel(rep: Reporte, negocio: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    formatos = {"colones": '"₡"#,##0', "colones_unit": '"₡"#,##0.000', "pct": '0.0"%"', "num": '#,##0.##'}
    verde = PatternFill("solid", fgColor="1F3A2C")
    suave = PatternFill("solid", fgColor="EEF4F0")
    linea = Side(style="thin", color="D5DED8")
    wb = Workbook()
    wb.remove(wb.active)
    usadas = set()
    for sec in rep.secciones:
        nombre = "".join(ch for ch in sec.hoja if ch not in '[]:*?/\\')[:31] or "Hoja"
        base, k = nombre, 2
        while nombre.lower() in usadas:
            nombre = f"{base[:28]} {k}"
            k += 1
        usadas.add(nombre.lower())
        ws = wb.create_sheet(nombre)
        ws["A1"] = f"{rep.titulo} · {negocio}" if negocio else rep.titulo
        ws["A1"].font = Font(bold=True, size=14, color="1F3A2C")
        ws["A2"] = sec.titulo if sec.titulo != rep.titulo else rep.subtitulo
        ws["A2"].font = Font(size=11, color="5B6B61")
        fila0 = 4
        for j, (titulo, tipo) in enumerate(sec.columnas, 1):
            cel = ws.cell(row=fila0, column=j, value=titulo)
            cel.font = Font(bold=True, color="FFFFFF")
            cel.fill = verde
            cel.alignment = Alignment(horizontal="right" if tipo not in ("texto", "cantidad") else "left", vertical="center", wrap_text=True)
        ws.row_dimensions[fila0].height = 30
        for i, fila in enumerate(sec.filas, fila0 + 1):
            for j, ((_, tipo), v) in enumerate(zip(sec.columnas, fila), 1):
                cel = ws.cell(row=i, column=j, value=v if v != "" else None)
                if tipo in formatos and isinstance(v, (int, float)):
                    cel.number_format = formatos[tipo]
                cel.border = Border(bottom=linea)
                if (i - fila0) % 2 == 0:
                    cel.fill = suave
        if sec.total:
            i = fila0 + len(sec.filas) + 1
            for j, ((_, tipo), v) in enumerate(zip(sec.columnas, sec.total), 1):
                cel = ws.cell(row=i, column=j, value=v if v != "" else None)
                cel.font = Font(bold=True)
                cel.border = Border(top=Side(style="medium", color="1F3A2C"))
                if tipo in formatos and isinstance(v, (int, float)):
                    cel.number_format = formatos[tipo]
        if sec.nota:
            i = fila0 + len(sec.filas) + (3 if sec.total else 2)
            ws.cell(row=i, column=1, value=sec.nota).font = Font(italic=True, color="5B6B61")
        for j, (titulo, tipo) in enumerate(sec.columnas, 1):
            largo = max([len(str(titulo))] + [len(texto(f[j - 1], tipo)) for f in sec.filas[:300]])
            ws.column_dimensions[get_column_letter(j)].width = min(48, max(10, largo + 3))
        ws.freeze_panes = ws.cell(row=fila0 + 1, column=1)
        ws.sheet_view.showGridLines = False
        ws.print_options.horizontalCentered = True
        ws.page_setup.orientation = "landscape" if rep.horizontal else "portrait"
        ws.page_setup.fitToWidth = 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF
_registradas = False


def _fuentes_pdf():
    global _registradas
    if _registradas:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont("Texto", os.path.join(FUENTES, "LiberationSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("Texto-Negrita", os.path.join(FUENTES, "LiberationSans-Bold.ttf")))
    from reportlab.lib.fonts import addMapping
    addMapping("Texto", 0, 0, "Texto")
    addMapping("Texto", 1, 0, "Texto-Negrita")
    _registradas = True


def a_pdf(rep: Reporte, negocio: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _fuentes_pdf()
    tam = landscape(A4) if rep.horizontal else A4
    buf = io.BytesIO()
    fecha = datetime.now().strftime("%d/%m/%Y")

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("Texto", 8)
        canvas.setFillColor(colors.HexColor("#6B7A70"))
        canvas.drawString(15 * mm, 10 * mm, f"{negocio} · {rep.titulo} · {fecha}")
        canvas.drawRightString(tam[0] - 15 * mm, 10 * mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=tam, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm,
                            title=f"{rep.titulo} · {negocio}", author=negocio)
    st_t = ParagraphStyle("t", fontName="Texto-Negrita", fontSize=18, leading=22, textColor=colors.HexColor("#1F3A2C"))
    st_s = ParagraphStyle("s", fontName="Texto", fontSize=10, leading=13, textColor=colors.HexColor("#5B6B61"))
    st_h = ParagraphStyle("h", fontName="Texto-Negrita", fontSize=12.5, leading=16, textColor=colors.HexColor("#1F3A2C"), spaceBefore=6)
    st_c = ParagraphStyle("c", fontName="Texto", fontSize=8.6, leading=10.5)
    st_cr = ParagraphStyle("cr", parent=st_c, alignment=TA_RIGHT)
    st_hd = ParagraphStyle("hd", fontName="Texto-Negrita", fontSize=8.4, leading=10, textColor=colors.white)
    st_hdr = ParagraphStyle("hdr", parent=st_hd, alignment=TA_RIGHT)
    st_n = ParagraphStyle("n", fontName="Texto", fontSize=8.5, leading=11, textColor=colors.HexColor("#5B6B61"))

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    hist = [Paragraph(esc(f"{rep.titulo} · {negocio}" if negocio else rep.titulo), st_t),
            Paragraph(esc(f"{rep.subtitulo} · {fecha}"), st_s), Spacer(1, 5 * mm)]
    ancho_util = tam[0] - 30 * mm
    for sec in rep.secciones:
        derecha = [tipo not in ("texto", "cantidad") for _, tipo in sec.columnas]
        datos = [[Paragraph(esc(t), st_hdr if d else st_hd) for (t, _), d in zip(sec.columnas, derecha)]]
        for f in sec.filas:
            datos.append([Paragraph(esc(texto(v, tipo)), st_cr if d else st_c) for (v, (_, tipo), d) in zip(f, sec.columnas, derecha)])
        if sec.total:
            datos.append([Paragraph("<b>" + esc(texto(v, tipo) if v not in (None, "") else "") + "</b>", st_cr if d else st_c)
                          for (v, (_, tipo), d) in zip(sec.total, sec.columnas, derecha)])
        # anchos: columnas de texto más anchas
        pesos = [3.2 if tipo == "texto" and j == 0 else 2.0 if tipo in ("texto", "cantidad") else 1.25 for j, (_, tipo) in enumerate(sec.columnas)]
        anchos = [ancho_util * p / sum(pesos) for p in pesos]
        tabla = Table(datos, colWidths=anchos, repeatRows=1)
        estilo = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A2C")),
                  ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#D5DED8")),
                  ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
        for i in range(1, len(datos)):
            if i % 2 == 0:
                estilo.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F3F7F4")))
        if sec.total:
            estilo += [("LINEABOVE", (0, -1), (-1, -1), 1.2, colors.HexColor("#1F3A2C")),
                       ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E6EFE9"))]
        tabla.setStyle(TableStyle(estilo))
        bloque = [Paragraph(esc(sec.titulo), st_h), Spacer(1, 2 * mm), tabla]
        if sec.nota:
            bloque += [Spacer(1, 1.5 * mm), Paragraph(esc(sec.nota), st_n)]
        hist.append(KeepTogether(bloque) if len(sec.filas) < 25 else bloque[0])
        if len(sec.filas) >= 25:
            hist += bloque[1:]
        hist.append(Spacer(1, 6 * mm))
    doc.build(hist, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()


def exportar(rep: Reporte, negocio: str, formato: str) -> tuple[bytes, str, str]:
    if formato == "xlsx":
        return a_excel(rep, negocio), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    return a_pdf(rep, negocio), "application/pdf", "pdf"
