# Dockerfile do TopoCalc
#  - estágio 1 compila o RTKLIB demo5 (convbin + rnx2rtkp) numa versão fixa
#  - estágio 2: Python + LibreOffice (PDF) + o app

FROM python:3.11-slim AS rtklib
ARG RTKLIB_REPO=https://github.com/rtklibexplorer/RTKLIB.git
ARG RTKLIB_COMMIT=81943c19ac5ab60ad34355bfbe12292f11a29894
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q . && git remote add origin "$RTKLIB_REPO" \
    && git fetch -q --depth 1 origin "$RTKLIB_COMMIT" && git checkout -q FETCH_HEAD \
    && make -s -C app/consapp/convbin/gcc \
    && make -s -C app/consapp/rnx2rtkp/gcc LDLIBS="-lm -lpthread" \
    && mkdir -p /out && cp app/consapp/convbin/gcc/convbin app/consapp/rnx2rtkp/gcc/rnx2rtkp /out/

FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer fonts-dejavu fonts-liberation fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=rtklib /out/ /app/topocalc/bin/

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    TOPOCALC_DATA_DIR=/data/projetos \
    TOPOCALC_RTKLIB_DIR=/app/topocalc/bin \
    MPLCONFIGDIR=/tmp/mpl \
    HOME=/tmp
RUN mkdir -p /data/projetos && python -c "import topocalc.gnss.rtklib as r; assert r.available()"
EXPOSE 8000

# Um único processo (o estado das tarefas em segundo plano fica na memória
# do processo) com várias threads para atender a interface enquanto processa.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT} --timeout 600 topocalc.web_app:app"]
