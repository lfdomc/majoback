FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Modelo para quitar el fondo de las fotos (se descarga una vez al construir la imagen)
RUN mkdir -p /app/modelos && python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/danielgatis/rembg/releases/download/v0.0.0/silueta.onnx', '/app/modelos/silueta.onnx')"
ENV MODELO_RECORTE=/app/modelos/silueta.onnx
COPY app ./app
# Railway entrega el puerto en $PORT. La base y las imágenes viven en el volumen montado en /data
ENV DATABASE_PATH=/data/costeo.db
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
