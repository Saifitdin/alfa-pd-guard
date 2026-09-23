FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY config/ config/
COPY data/ data/
COPY certs/ certs/
# Russian Trusted CA нужен для AlfaGen; склеивается с certifi в один бандл,
# который использует только LLM-клиент. Системное доверие не трогается.
RUN python -c "import certifi,pathlib;pathlib.Path('/app/certs/ca-bundle.pem').write_bytes(pathlib.Path(certifi.where()).read_bytes()+b'\n'+pathlib.Path('/app/certs/russian_trusted_ca.pem').read_bytes())"

# Непривилегированный пользователь: контейнеру не нужны права root.
RUN useradd --create-home --uid 10001 pdguard && chown -R pdguard:pdguard /app
USER pdguard

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "pdguard.main:app", "--host", "0.0.0.0", "--port", "8000"]
