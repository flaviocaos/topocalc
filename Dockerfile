# Dockerfile do TopoCalc — inclui LibreOffice (necessário para gerar PDF)
FROM python:3.11-slim

# Dependências de sistema: LibreOffice (conversão docx->pdf) e fontes
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

ENV PORT=8000
EXPOSE 8000

# gunicorn serve o Flask app em produção (em vez do servidor de dev do Flask)
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT} --timeout 120 topocalc.web_app:app"]
