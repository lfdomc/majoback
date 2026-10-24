import os, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["ADMIN_CLAVE"] = "clave123"
os.environ["SECRET_KEY"] = "x" * 40
os.environ["CARGAR_DATOS_INICIALES"] = "no"
os.environ["RECORTE_FONDO"] = "no"

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.units import convertir, ErrorConversion

c = TestClient(app)


@pytest.fixture(scope="module")
def h():
    t = c.post("/api/auth/ingresar", json={"usuario": "admin", "clave": "clave123"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def test_conversiones():
    assert convertir(1, "kg", "g") == 1000
    assert round(convertir(2, "oz_fl", "ml"), 2) == 59.15
    assert convertir(3, "unidad", "g", peso_unidad_g=100) == 300
    assert convertir(500, "ml", "g", densidad_g_ml=1.03) == pytest.approx(515)
    with pytest.raises(ErrorConversion):
        convertir(1, "unidad", "g")


def test_sin_sesion():
    assert c.get("/api/ingredientes").status_code == 401
    assert c.post("/api/auth/ingresar", json={"usuario": "admin", "clave": "mala"}).status_code == 401


def test_flujo_completo(h):
    # Sandía: 4 kg a ₡1600 con 20 % de merma -> ₡0,50 por gramo limpio
    r = c.post("/api/ingredientes", headers=h, json={"nombre": "Sandía", "unidad_base": "g", "presentaciones": [
        {"precio": 1600, "cantidad": 4, "unidad": "kg", "merma_pct": 20, "preferida": True}]})
    sandia = r.json()["id"]
    ing = c.get(f"/api/ingredientes/{sandia}", headers=h).json()
    assert ing["costo"]["seleccionado"] == pytest.approx(0.5)

    env = c.post("/api/ingredientes", headers=h, json={"nombre": "Envase", "unidad_base": "unidad",
                 "presentaciones": [{"precio": 5500, "cantidad": 50, "unidad": "unidad"}]}).json()["id"]
    sub = c.post("/api/recetas", headers=h, json={"nombre": "Sandía picada", "tipo": "subreceta",
                 "rinde_cantidad": 1, "rinde_unidad": "kg",
                 "items": [{"ingrediente_id": sandia, "cantidad": 1000, "unidad": "g"}]}).json()["id"]
    prod = c.post("/api/recetas", headers=h, json={
        "nombre": "Vaso de sandía", "rinde_cantidad": 2, "rinde_unidad": "porcion", "margen_objetivo_pct": 60,
        "precio_venta": 1000,
        "items": [{"subreceta_id": sub, "cantidad": 400, "unidad": "g", "merma_pct": 0},
                  {"ingrediente_id": env, "cantidad": 2, "unidad": "unidad"}]}).json()["id"]
    d = c.get(f"/api/recetas/{prod}", headers=h).json()["calculo"]
    # 400 g × 0,5 = 200 ; 2 envases × 110 = 220 ; total 420 ; por porción 210
    assert d["totales"]["seleccionado"] == pytest.approx(420)
    assert d["costo_por_rinde"] == pytest.approx(210)
    assert d["precios"]["precio_sugerido_sin_iva"] == pytest.approx(525)
    assert d["precios"]["precio_sugerido"] == 550  # redondeo a ₡50
    assert d["precios"]["margen_real_pct"] == pytest.approx(79)

    p = c.get(f"/api/recetas/{prod}/produccion?cantidad=20", headers=h).json()
    s = next(x for x in p["items"] if x["nombre"] == "Sandía")
    assert s["cantidad_base"] == pytest.approx(4000)
    assert s["comprar"] == 2  # 2 sandías de 3200 g limpios

    # referencia circular
    r = c.put(f"/api/recetas/{sub}", headers=h, json={"nombre": "Sandía picada", "tipo": "subreceta",
              "rinde_cantidad": 1, "rinde_unidad": "kg", "items": [{"subreceta_id": prod, "cantidad": 1, "unidad": "porcion"}]})
    assert r.status_code == 400
    # no se puede borrar algo en uso
    assert c.delete(f"/api/ingredientes/{sandia}", headers=h).status_code == 409


def _png():
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (2000, 1000), (220, 80, 110)).save(b, "PNG")
    return b.getvalue()


def test_menu_publico(h):
    rid = c.post("/api/recetas", headers=h, json={"nombre": "Copa test", "precio_venta": 1500, "en_menu": True,
                 "descripcion_menu": "Rica", "categoria": "Postres", "items": []}).json()["id"]
    c.post("/api/recetas", headers=h, json={"nombre": "Oculta", "en_menu": False, "items": []})
    r = c.post(f"/api/recetas/{rid}/foto", headers=h, files={"archivo": ("f.png", _png(), "image/png")})
    assert r.status_code == 200
    assert c.post(f"/api/recetas/{rid}/foto", headers=h, files={"archivo": ("x.png", b"no soy imagen", "image/png")}).status_code == 400
    c.put("/api/negocio", headers=h, json={"nombre": "Dolce", "whatsapp": "8888-8888", "instagram": "dolce.cr", "color": "#2E6A4C"})
    c.post("/api/negocio/logo", headers=h, files={"archivo": ("l.png", _png(), "image/png")})

    m = c.get("/api/publico/menu").json()  # sin sesión
    assert m["negocio"]["nombre"] == "Dolce" and m["negocio"]["logo"].startswith("/media/")
    prods = [p for cat in m["categorias"] for p in cat["productos"]]
    assert [p["nombre"] for p in prods] == ["Copa test"]
    assert prods[0]["precio"] == 1500 and "costo" not in str(m).lower()
    img = c.get(prods[0]["foto"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"  # imagen tipo anuncio
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(img.content)).size == (1080, 1080)
    assert c.get("/media/../app/main.py").status_code == 404
    assert c.put("/api/negocio", json={"nombre": "x"}).status_code == 401



def test_arte_generada(h):
    import io
    from PIL import Image
    rid = c.post("/api/recetas", headers=h, json={"nombre": "Banana hawaiana", "precio_venta": 2800, "en_menu": True, "items": []}).json()["id"]
    r = c.post(f"/api/recetas/{rid}/foto", headers=h, files={"archivo": ("f.png", _png(), "image/png")}).json()
    assert r["arte"].endswith(".jpg")
    img = Image.open(io.BytesIO(c.get(f"/media/{r['arte']}").content))
    assert img.size == (1080, 1080)
    # cambiar estilo y nombre regenera la imagen
    r2 = c.post(f"/api/recetas/{rid}/arte", headers=h, json={"estilo": "rayas"}).json()
    assert r2["arte"] != r["arte"] and r2["estilo_arte"] == "rayas"
    assert c.post(f"/api/recetas/{rid}/arte", headers=h, json={"estilo": "neon"}).status_code == 400
    c.put(f"/api/recetas/{rid}", headers=h, json={"nombre": "Banana hawaiana grande", "precio_venta": 3000, "en_menu": True, "items": []})
    d = c.get(f"/api/recetas/{rid}", headers=h).json()
    assert d["arte"] != r2["arte"]
    menu = c.get("/api/publico/menu").json()
    p = [x for cat in menu["categorias"] for x in cat["productos"] if x["id"] == rid][0]
    assert p["es_arte"] and p["foto"] == f"/media/{d['arte']}"
    # quitar la foto borra también la imagen generada
    c.delete(f"/api/recetas/{rid}/foto", headers=h)
    assert c.get(f"/media/{d['arte']}").status_code == 404



def test_nombre_unico(h):
    cfg = c.get("/api/configuracion", headers=h).json()
    c.put("/api/configuracion", headers=h, json={**cfg, "nombre_negocio": "Dolce Marju"})
    assert c.get("/api/publico/menu").json()["negocio"]["nombre"] == "Dolce Marju"
    n = c.get("/api/negocio", headers=h).json()
    n.pop("logo", None)
    c.put("/api/negocio", headers=h, json={**n, "nombre": "Dolce Marju by Diego"})
    assert c.get("/api/configuracion", headers=h).json()["nombre_negocio"] == "Dolce Marju by Diego"


def test_publica_menu_en_r2(h, monkeypatch):
    """Con las variables R2 puestas, un cambio publica menu.json e imágenes (con un S3 de prueba)."""
    import json, time, boto3
    from moto.server import ThreadedMotoServer
    from app import publicar
    srv = ThreadedMotoServer(port=5077, verbose=False); srv.start()
    try:
        for k, v in {"R2_ACCOUNT_ID": "x", "R2_ACCESS_KEY_ID": "a", "R2_SECRET_ACCESS_KEY": "b",
                     "R2_BUCKET": "menu", "R2_PUBLIC_URL": "https://pub.ejemplo.dev/"}.items():
            monkeypatch.setenv(k, v)
        s3 = boto3.client("s3", endpoint_url="http://localhost:5077", aws_access_key_id="a",
                          aws_secret_access_key="b", region_name="us-east-1")
        s3.create_bucket(Bucket="menu")
        monkeypatch.setattr(publicar, "_cliente", s3); monkeypatch.setattr(publicar, "_subidas", None)
        r = c.post("/api/publicacion", headers=h).json()
        assert r["activo"] and r["error"] is None and r["ultima"]
        menu = json.loads(s3.get_object(Bucket="menu", Key="menu.json")["Body"].read())
        assert "categorias" in menu and "publicado" in menu
        fotos = [p["foto"] for cat in menu["categorias"] for p in cat["productos"] if p["foto"]]
        for f in fotos:
            assert f.startswith("https://pub.ejemplo.dev/media/")
            s3.head_object(Bucket="menu", Key=f.split(".dev/")[1])
        # un cambio cualquiera dispara la publicación automática (con unos segundos de espera)
        antes = publicar.estado["ultima"]
        time.sleep(1.1)
        c.put("/api/negocio", headers=h, json={**c.get("/api/negocio", headers=h).json(), "horario": "Todos los días"})
        for _ in range(60):
            if publicar.estado["ultima"] != antes:
                break
            time.sleep(0.1)
        menu = json.loads(s3.get_object(Bucket="menu", Key="menu.json")["Body"].read())
        assert menu["negocio"]["horario"] == "Todos los días"
    finally:
        srv.stop()


def test_categorias(h):
    c.post("/api/recetas", headers=h, json={"nombre": "Prueba cat 1", "categoria": "Batidos", "items": []})
    c.post("/api/recetas", headers=h, json={"nombre": "Prueba cat 2", "categoria": "Batidos", "items": []})
    lista = c.get("/api/categorias/producto", headers=h).json()
    assert {"nombre": "Batidos", "usos": 2} in lista
    # crear vacía, no duplicar
    lista = c.post("/api/categorias/producto", headers=h, json={"nombre": "Café"}).json()
    assert lista[-1] == {"nombre": "Café", "usos": 0}
    assert c.post("/api/categorias/producto", headers=h, json={"nombre": "café"}).status_code == 400
    # renombrar cambia los productos
    c.put("/api/categorias/producto/renombrar", headers=h, json={"anterior": "Batidos", "nuevo": "Bebidas"})
    nombres = {r["nombre"]: r["categoria"] for r in c.get("/api/recetas", headers=h).json()}
    assert nombres["Prueba cat 1"] == "Bebidas"
    # orden
    lista = c.put("/api/categorias/producto/orden", headers=h, json={"nombres": ["Café", "Bebidas"]}).json()
    assert [x["nombre"] for x in lista][:2] == ["Café", "Bebidas"]
    # borrar con productos exige destino
    assert c.delete("/api/categorias/producto", headers=h, params={"nombre": "Bebidas"}).status_code == 400
    lista = c.delete("/api/categorias/producto", headers=h, params={"nombre": "Bebidas", "mover_a": "Café"}).json()
    assert {"nombre": "Café", "usos": 2} in lista and all(x["nombre"] != "Bebidas" for x in lista)
    # ingredientes también
    assert c.get("/api/categorias/ingrediente", headers=h).status_code == 200
    assert c.get("/api/categorias/otra", headers=h).status_code == 400
