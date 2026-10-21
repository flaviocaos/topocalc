# TopoCalc

![TopoCalc](assets/topocalc_logo.png)

Pós-processamento GNSS a partir dos arquivos brutos **`.GNS` da Hi-Target**
(D8 Pro, V90 e outros), do jeito que o topógrafo envia do campo:

```
.GNS  →  RINEX 3.04  →  linhas de base (RTKLIB)  →  ajustamento em rede  →  coordenadas finais
                                                                         →  relatório .docx/.pdf, planilha .xlsx,
                                                                            KML, CSV e RINEX
```

Funciona pela interface web (Render ou local) e pela linha de comando.

## O que ele faz

1. **Lê os `.GNS`** — cabeçalho (data, altura da antena, receptor, série) e
   as observações brutas nos dois formatos internos usados pela Hi-Target:
   - placa **NovAtel** (D8 Pro): observações e efemérides GLONASS/Galileo pelo
     RTKLIB; efemérides GPS (log GPSEPHEM) por decodificador próprio;
   - placa **Trimble** (V90): registro **RT27** por decodificador próprio —
     não existe conversor aberto para esse registro — e efemérides GPS pelo
     RTKLIB (RT17).
2. **Monta o projeto**: agrupa por dia, sugere o nome do marco pelo nome do
   arquivo, calcula posição aproximada (SPP), avisa quando dois arquivos estão
   no mesmo lugar com nomes diferentes e propõe as linhas de base (pares que
   rastrearam juntos ≥ 30 min).
3. **Revisão pelo usuário**: nomes dos marcos, alturas de antena, marcos de
   controle (UTM ou geográficas; h e/ou H) e quais linhas processar.
4. **Processa as linhas de base** com o RTKLIB demo5, a partir do controle,
   em ondas (cada linha usa como base um ponto já determinado):
   - linhas < 5 km: L1+L2, ambiguidades fixadas para frente e para trás;
   - linhas longas: ionosfera e troposfera estimadas, solução flutuante e
     tentativa de fixação nos dois sentidos. A fixa só é aceita se ida e volta
     concordarem e se bater com a flutuante — em linhas longas o RTKLIB às
     vezes "fixa" ambiguidades erradas, e esse teste as descarta.
5. **Ajusta a rede** (mínimos quadrados 3D, um ou vários controles, teste
   global, teste w de Baarda por vetor, fechamento de triângulos). Vetores
   suspeitos podem ser excluídos e a rede reajustada sem reprocessar.
6. **Altimetria**: H = h − N, com EGM96 embutido e deslocado para coincidir com
   a ondulação observada nos controles que têm H.
7. **Órbitas precisas automáticas**: para cada dia de rastreio o TopoCalc baixa
   órbitas (SP3) e relógios (CLK) do IGS — finais multi-GNSS (CODE, GFZ, Wuhan,
   CNES) ou rápidos (GFZ, Wuhan, SHAO, CODE) — dos servidores abertos IGN, ENSG,
   BKG e GFZ (CDDIS com login Earthdata opcional). Se ainda não existirem,
   usa as efemérides transmitidas e avisa no relatório.
8. **Calibração de antenas (ANTEX)**: igs20 embutido (satélites e antenas
   Hi-Target/CHC), ngs20 baixado da NGS e ANTEX enviado pelo usuário. O modelo
   de antena de cada arquivo é escolhido na revisão.
9. **PPP da base**: um marco de controle pode ser calculado por PPP estático
   (órbitas e relógios precisos), quando não há coordenada conhecida.
10. **Sistemas de saída**: referencial do controle (WGS 84, SIRGAS 2000, ITRF),
    Camacupa 2015 / UTM (Angola, transformação EPSG ±3 m) e plano topográfico
    local (tangente na origem).
7. **Entregáveis**: relatório técnico (.docx e .pdf), planilha (.xlsx com
   coordenadas, geodésicas, linhas de base, resíduos, fechamentos, arquivos e
   controle), KML (pontos + vetores coloridos por solução), CSV e RINEX
   (obs + navegação por dia, com marco, antena e posição no cabeçalho).

### Validação

Testado com o levantamento HCB (Angola, out/2026: 12 arquivos, 3 dias, 15
linhas de 0,04 a 35 km, receptores D8 Pro e V90). Comparado ao processamento
no Leica Infinity com órbitas precisas: diferenças de 0–2 cm na horizontal e
até ~9 cm na altura elipsoidal nos pontos a 50 km do controle — dentro das
precisões (2σ) que o próprio TopoCalc informa para esses pontos. Para linhas
acima de ~20 km, envie órbitas e relógios precisos (SP3 + CLK) para melhorar.

## Interface web

```bash
pip install -r requirements.txt
# RTKLIB: veja "RTKLIB" abaixo
python -m topocalc.web_app          # http://127.0.0.1:5000
```

Fluxo: **enviar .GNS → análise (segundos) → revisão → processamento (minutos,
com barra de progresso) → resultados → baixar pacote**. A página de progresso
pode ser fechada; o processamento continua no servidor.

### Variáveis de ambiente

| variável | padrão | uso |
|---|---|---|
| `TOPOCALC_PASSWORD` | — | se definida, pede senha (qualquer usuário). **Defina no Render.** |
| `TOPOCALC_DATA_DIR` | `<tmp>/topocalc_projects` | pasta dos projetos |
| `TOPOCALC_KEEP_DAYS` | `7` | apaga projetos mais antigos que isso |
| `TOPOCALC_RTKLIB_DIR` | — | pasta com `convbin` e `rnx2rtkp` (se não estiverem em `topocalc/bin` nem no PATH) |
| `TOPOCALC_WORKERS` | `2` | linhas de base processadas em paralelo |
| `TOPOCALC_MAX_UPLOAD_MB` | `1500` | limite do envio |
| `TOPOCALC_CACHE_DIR` | `<tmp>/topocalc_cache` | cache de órbitas e ANTEX |
| `TOPOCALC_EARTHDATA_USER` / `_PASSWORD` | — | login Earthdata (NASA) para usar também o CDDIS |

Depois do deploy, abra `/diagnostico?data=AAAA-MM-DD` para conferir se o
servidor consegue baixar as órbitas daquele dia e as calibrações de antena.

## Linha de comando

```bash
# ver o que há nos arquivos
python -m topocalc.cli analisar -i dados/*.GNS -p ./projeto

# processar (controle + correções de nome e altura)
python -m topocalc.cli processar -p ./projeto \
    --controle "HBD01;utm;683668.738;9067058.188;33S;656.4086;648.3203" \
    --renomear HBD1=HBD01 --renomear HCB02=HCB2 --renomear HC10=HCB10 \
    --altura HCB22810.GNS=1.694

# excluir vetores e reajustar
python -m topocalc.cli reajustar -p ./projeto --excluir HCB22810-HCB62810
```

Controle: `"NOME;utm;E;N;FUSO;h;H"` ou `"NOME;geo;LAT;LON;h;H"` (H opcional).

## RTKLIB

O processamento usa o **RTKLIB demo5** (rtklibexplorer), versão fixa
`81943c19`. O `Dockerfile` já compila e instala. Para rodar fora do Docker:

```bash
git clone https://github.com/rtklibexplorer/RTKLIB.git && cd RTKLIB
git checkout 81943c19ac5ab60ad34355bfbe12292f11a29894
make -C app/consapp/convbin/gcc
make -C app/consapp/rnx2rtkp/gcc LDLIBS="-lm -lpthread"
cp app/consapp/convbin/gcc/convbin app/consapp/rnx2rtkp/gcc/rnx2rtkp <TopoCalc>/topocalc/bin/
```

## Deploy no Render

O `render.yaml` cria um serviço Docker. Observações:

- **Plano**: o processamento usa CPU. No *free* (0,1 CPU) um levantamento de
  um dia leva dezenas de minutos e o serviço hiberna sem uso. Use *starter*
  ou, melhor, *standard*.
- **Senha**: defina `TOPOCALC_PASSWORD` no painel — sem ela qualquer pessoa
  com o link pode enviar arquivos.
- **Arquivos**: o disco do Render é apagado a cada deploy/reinício. Baixe o
  pacote ao terminar, ou adicione um disco persistente montado em `/data`.
- Um único processo (`gunicorn -w 1 --threads 8`): as tarefas rodam em
  segundo plano dentro dele. Não aumente `-w`.

## Limitações conhecidas

- **Calibração de antena**: os `.GNS` não trazem o modelo da antena; ele
  precisa ser escolhido na revisão (sem escolha, nenhuma calibração é aplicada).
- Sem órbitas precisas (dias muito recentes), o erro de órbita entra com
  ~0,1–0,5 ppm.
- PPP sem resolução de ambiguidades: precisão típica de 2–3 cm (horizontal) e
  4–6 cm (vertical) com 4 h ou mais.
- GLONASS e BeiDou ficam fora do processamento (GPS + Galileo): o viés
  GLONASS entre receptores de fabricantes diferentes atrapalha a fixação, e
  o BeiDou desses receptores só grava uma frequência.
- Formato `.GNS` obtido por engenharia reversa (D8 Pro e V90, firmware
  100.0/90.0). Outros modelos podem precisar de ajustes.

## Estrutura

```
topocalc/
  gnss/
    gns_file.py      cabeçalho do .GNS e detecção do formato interno
    rt27.py          decodificador Trimble RT27 → RINEX 3.04
    novatel_eph.py   efemérides GPS NovAtel (GPSEPHEM) → RINEX nav
    rinex_tools.py   conversão por arquivo, junção de navegação, cabeçalhos
    rtklib.py        chamadas ao convbin / rnx2rtkp, SPP
    baseline.py      processamento e validação de uma linha de base
    adjust.py        ajustamento em rede, testes estatísticos, triângulos
    geodesy.py       ECEF/geodésicas/UTM, EGM96
    orbits.py        download de órbitas/relógios precisos (IGS)
    antenna.py       calibração de antenas (ANTEX)
    crs.py           referencial, Camacupa 2015, plano topográfico local
    project.py       orquestração (análise, processamento, reajuste)
  reports/           relatório docx/pdf, xlsx, kml, csv, zip RINEX
  templates/, static/  interface web
  web_app.py         Flask
  cli.py             linha de comando
  data/egm96_15min.npz, data/igs20_topocalc.atx.gz
tests/               testes (o de dados reais usa TOPOCALC_TEST_DATA)
```

```bash
pip install pytest && python -m pytest -q tests
TOPOCALC_TEST_DATA=/caminho/dos/GNS python -m pytest -q tests/test_real_data.py
```
