import yfinance as yf
import anthropic
import requests
import os
import json
import time
import threading
import socket
import http.server
import warnings
from datetime import datetime, timezone, timedelta

socket.setdefaulttimeout(30)  # timeout global: nenhuma ligação de rede fica presa mais de 30s

import pandas as pd

try:
    import psycopg2
    PSYCOPG2_OK = True
except ImportError:
    PSYCOPG2_OK = False

warnings.filterwarnings("ignore")

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────
BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
FICHEIRO_CARTEIRA = os.path.join(BASE_DIR, "carteira.json")
FICHEIRO_DASHBOARD= os.path.join(BASE_DIR, "dados_dashboard.json")
SALDO_INICIAL     = 10000.0
THRESHOLD_ENTRADA     = 58   # score >= 58 → entra (slots normais)
THRESHOLD_PREMIUM     = 90   # score >= 90 → entra em slot premium (posições 5-6)
THRESHOLD_CORRELACAO  = 80   # score >= 80 → entra mesmo com activo correlacionado aberto
RSI_MIN_ENTRADA       = 25   # RSI mínimo para qualquer entrada (evita oversold extremo)
RSI_MAX_ENTRADA       = 75   # RSI máximo para qualquer entrada (evita overbought extremo)
CUSTO_OP          = 0.001    # 0.1% entrada + 0.1% saída
SL_MULT           = 2.0      # Stop Loss  = 2× ATR diário
TP_MULT           = 4.0      # Take Profit = 4× ATR diário (ratio 1:2)
CAPITAL_POR_OP    = 0.015    # 1.5% do saldo por operação
MAX_POSICOES      = 6        # máximo total (4 normais + 2 premium)
MAX_POSICOES_NORMAL = 4      # slots normais (threshold 58%)
PORT              = int(os.environ.get("PORT", 8080))
SEP               = "=" * 60
COOLDOWN_MIN      = 30       # minutos de espera após SL

# ─── ACTIVOS ──────────────────────────────────────────────────────────────────
ACTIVOS = [
    {
        "nome": "Bitcoin",  "ticker": "BTC-USD",  "simbolo": "BTC/USD",  "cripto": True,
        "perfil": "criptomoeda de alta volatilidade 24/7, sensível a ETFs institucionais e fluxos macro",
        "noticias_termos": ["Bitcoin price", "BTC crypto", "cryptocurrency"],
        "noticias_fallback": [
            "Bitcoin ETF inflows hit new record as institutional demand surges",
            "BTC consolidates above key support after recent rally",
            "Crypto market sentiment turns bullish on Fed pivot expectations",
            "Bitcoin hash rate reaches all-time high signaling network strength",
        ],
    },
    {
        "nome": "Ouro",     "ticker": "GC=F",     "simbolo": "XAU/USD",  "cripto": False,
        "perfil": "metal precioso safe-haven, correlacionado inverso ao dólar",
        "noticias_termos": ["gold price", "Federal Reserve", "XAU USD"],
        "noticias_fallback": [
            "Gold holds near record highs as central banks continue buying",
            "Federal Reserve signals cautious approach to rate cuts",
            "Dollar weakens boosting gold appeal as safe haven",
            "Geopolitical tensions drive safe-haven demand for gold",
        ],
    },
    {
        "nome": "Petróleo", "ticker": "CL=F",     "simbolo": "WTI/USD",  "cripto": False,
        "perfil": "commodity energética reactiva a OPEP e tensões geopolíticas",
        "noticias_termos": ["crude oil WTI", "OPEC production", "oil inventory"],
        "noticias_fallback": [
            "OPEC+ maintains production cuts amid global demand concerns",
            "US crude inventories show unexpected weekly drawdown",
            "Oil prices firm as Middle East supply risks persist",
            "Energy demand outlook improves on China recovery signals",
        ],
    },
    {
        "nome": "Prata",    "ticker": "SI=F",     "simbolo": "XAG/USD",  "cripto": False,
        "perfil": "metal precioso industrial, mais volátil que o ouro",
        "noticias_termos": ["silver price", "precious metals", "silver industrial"],
        "noticias_fallback": [
            "Silver demand surges driven by solar panel manufacturing",
            "Industrial metals rally as global manufacturing PMI improves",
            "Silver outperforms gold in risk-on environment",
            "Green energy transition continues driving silver demand",
        ],
    },
    {
        "nome": "Ethereum", "ticker": "ETH-USD",  "simbolo": "ETH/USD",  "cripto": True,
        "perfil": "plataforma de smart contracts, correlacionada com Bitcoin mas mais volátil",
        "noticias_termos": ["Ethereum price", "ETH crypto", "DeFi blockchain"],
        "noticias_fallback": [
            "Ethereum staking yields attract institutional interest",
            "DeFi activity surges on Ethereum network",
            "ETH upgrades improve scalability and reduce gas fees",
            "Ethereum ETF flows strengthen as institutional adoption grows",
        ],
    },
    {
        "nome": "EUR/USD",  "ticker": "EURUSD=X", "simbolo": "EUR/USD",  "cripto": False,
        "perfil": "par forex mais líquido do mundo, sensível a diferencial BCE/Fed",
        "noticias_termos": ["EUR USD forex", "European Central Bank", "dollar euro"],
        "noticias_fallback": [
            "ECB signals gradual rate cuts as inflation moderates",
            "Dollar weakens on improved risk appetite globally",
            "Euro strengthens on better-than-expected German PMI data",
            "Fed holds rates steady supporting dollar strength",
        ],
    },
    {
        "nome": "S&P 500",  "ticker": "ES=F",     "simbolo": "SPX",      "cripto": False,
        "perfil": "índice das 500 maiores empresas americanas, barómetro global",
        "noticias_termos": ["S&P 500 stocks", "US economy earnings", "Federal Reserve"],
        "noticias_fallback": [
            "S&P 500 approaches all-time highs on strong earnings season",
            "Fed pause expectations boost equities broadly",
            "Corporate profit margins expand despite macro headwinds",
            "Broad market rally driven by tech and financial sectors",
        ],
    },
    {
        "nome": "Nasdaq",   "ticker": "NQ=F",     "simbolo": "NQ100",    "cripto": False,
        "perfil": "índice tecnológico, sensível a taxas de juro e earnings de Big Tech",
        "noticias_termos": ["Nasdaq tech stocks", "AI earnings technology", "growth stocks"],
        "noticias_fallback": [
            "Nasdaq climbs on blowout earnings from major tech companies",
            "AI infrastructure spending cycle continues to drive valuations",
            "Rate cut expectations boost growth and technology stocks",
            "Big Tech outperforms as cloud revenue beats estimates",
        ],
    },
]

CORRELACOES = [
    frozenset({"S&P 500", "Nasdaq"}),
    frozenset({"Ouro", "Prata"}),
    frozenset({"Bitcoin", "Ethereum"}),
]


# ─── HTTP SERVER ──────────────────────────────────────────────────────────────
def iniciar_servidor_http():
    class Handler(http.server.BaseHTTPRequestHandler):
        ROTAS = {
            "/":                     ("dashboard.html",        "text/html; charset=utf-8"),
            "/dashboard.html":       ("dashboard.html",        "text/html; charset=utf-8"),
            "/dados_dashboard.json": ("dados_dashboard.json",  "application/json; charset=utf-8"),
            "/diagnostico.html":     ("diagnostico.html",      "text/html; charset=utf-8"),
            "/diagnostico_dados.json": ("diagnostico_dados.json", "application/json; charset=utf-8"),
        }
        def do_GET(self):
            path = self.path.split("?")[0]
            if path not in self.ROTAS:
                self.send_error(404)
                return
            if path == "/diagnostico_dados.json":
                # SENSOR 2: regenera o diagnóstico a cada pedido, isolado do
                # robô — se falhar, fica a versão anterior em disco e seguimos.
                try:
                    import gerar_diagnostico
                    gerar_diagnostico.main()
                except (Exception, SystemExit) as e:
                    print(f"[diagnostico] regeneração falhou (a mostrar dados anteriores): {e}")
            nome, ctype = self.ROTAS[path]
            try:
                with open(os.path.join(BASE_DIR, nome), "rb") as f:
                    corpo = f.read()
            except FileNotFoundError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)
        def log_message(self, *_): pass

    http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


# ─── CARTEIRA ─────────────────────────────────────────────────────────────────
def carteira_vazia():
    return {
        "saldo":           SALDO_INICIAL,
        "custos_totais":   0.0,
        "posicoes_abertas": [],
        "historico":       [],
        "cooldowns":       {},
        "estatisticas": {
            "total_operacoes": 0, "ganhas": 0, "perdidas": 0,
            "win_rate": 0.0, "lucro_liquido_total": 0.0,
            "custos_totais": 0.0, "rentabilidade": 0.0,
        },
    }


def _normalizar_carteira(c):
    """Garante compatibilidade de campos após carregar de qualquer fonte."""
    if "posicao_aberta" in c:
        pos_antiga = c.pop("posicao_aberta")
        if pos_antiga and "posicoes_abertas" not in c:
            c["posicoes_abertas"] = [pos_antiga]
    c.setdefault("posicoes_abertas", [])
    c.setdefault("custos_totais", 0.0)
    c.setdefault("historico", [])
    c.setdefault("cooldowns", {})
    c.setdefault("estatisticas", carteira_vazia()["estatisticas"])
    for pos in c["posicoes_abertas"]:
        pos.setdefault("melhor_preco", pos["preco_entrada"])
        pos.setdefault("hora_melhor_preco", pos.get("hora_abertura", ""))
    return c


def carregar_carteira():
    # 1ª opção: base de dados PostgreSQL (sobrevive a deploys)
    c_db = _carregar_db()
    if c_db is not None:
        return _normalizar_carteira(c_db)
    # Fallback: ficheiro JSON local
    if os.path.exists(FICHEIRO_CARTEIRA):
        with open(FICHEIRO_CARTEIRA, "r") as f:
            c = json.load(f)
        return _normalizar_carteira(c)
    return carteira_vazia()


def guardar_carteira(carteira):
    _guardar_db(carteira)                                    # persistência primária
    with open(FICHEIRO_CARTEIRA, "w") as f:                  # backup local (fallback)
        json.dump(carteira, f, ensure_ascii=False, indent=2)


# ─── POSTGRESQL ───────────────────────────────────────────────────────────────
def _db_conn():
    """Retorna conexão psycopg2 ou None se DB indisponível."""
    url = os.environ.get("DATABASE_URL")
    if not url or not PSYCOPG2_OK:
        return None
    try:
        return psycopg2.connect(url, connect_timeout=8)
    except Exception as e:
        print(f"  ⚠ DB: ligação falhou ({e})")
        return None


def _init_db():
    """Cria tabela se não existir e semeia com carteira.json se vazia."""
    conn = _db_conn()
    if not conn:
        print("  ⚠ PostgreSQL não disponível — a usar carteira.json como fallback")
        return
    try:
        with conn:
            conn.cursor().execute("""
                CREATE TABLE IF NOT EXISTS carteira_estado (
                    id  INTEGER PRIMARY KEY DEFAULT 1,
                    dados JSONB  NOT NULL,
                    ts  TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT single_row CHECK (id = 1)
                )
            """)
        # Semeia DB com JSON local se ainda estiver vazia
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM carteira_estado")
            if cur.fetchone()[0] == 0 and os.path.exists(FICHEIRO_CARTEIRA):
                with open(FICHEIRO_CARTEIRA, "r") as f:
                    dados_locais = json.load(f)
                cur.execute(
                    "INSERT INTO carteira_estado (id, dados) VALUES (1, %s::jsonb)",
                    (json.dumps(dados_locais, ensure_ascii=False),),
                )
                print("  ✓ DB semeada com carteira.json local")
        print("  ✓ PostgreSQL ligado — carteira persistente entre deploys")
    except Exception as e:
        print(f"  ⚠ DB init: {e}")
    finally:
        conn.close()


def _carregar_db():
    """Lê carteira da base de dados. Retorna dict ou None."""
    conn = _db_conn()
    if not conn:
        return None
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT dados FROM carteira_estado WHERE id = 1")
            row = cur.fetchone()
            return row[0] if row else None   # psycopg2 converte JSONB → dict
    except Exception as e:
        print(f"  ⚠ DB leitura: {e}")
        return None
    finally:
        conn.close()


def _guardar_db(carteira):
    """Grava carteira na base de dados (UPSERT)."""
    conn = _db_conn()
    if not conn:
        return
    try:
        with conn:
            conn.cursor().execute("""
                INSERT INTO carteira_estado (id, dados, ts)
                VALUES (1, %s::jsonb, NOW())
                ON CONFLICT (id) DO UPDATE
                    SET dados = EXCLUDED.dados,
                        ts    = NOW()
            """, (json.dumps(carteira, ensure_ascii=False),))
    except Exception as e:
        print(f"  ⚠ DB escrita: {e}")
    finally:
        conn.close()


def atualizar_estatisticas(carteira):
    hist  = carteira.get("historico", [])
    total = len(hist)
    gan   = sum(1 for h in hist if h.get("resultado") == "GANHOU")
    per   = total - gan
    lucro = sum(h.get("lucro_liquido", h.get("lucro", 0)) for h in hist)
    rent  = (carteira["saldo"] - SALDO_INICIAL) / SALDO_INICIAL * 100
    carteira["estatisticas"] = {
        "total_operacoes":     total,
        "ganhas":              gan,
        "perdidas":            per,
        "win_rate":            round(gan / total * 100, 1) if total > 0 else 0.0,
        "lucro_liquido_total": round(lucro, 2),
        "custos_totais":       round(carteira.get("custos_totais", 0), 2),
        "rentabilidade":       round(rent, 2),
    }
    return carteira


# ─── FILTROS ──────────────────────────────────────────────────────────────────
def calcular_tendencia(ind, ticker=""):
    """Cascata MM200→MM100→MM50→MM20.
    Para GC=F e CL=F (Ouro/Petróleo) nunca retorna INDEFINIDA — usa a MM
    mais curta disponível quando as longas dão sinal misto.
    Retorna (tendencia: str, nota: str) — nota indica qual MM foi usada."""
    import math
    _CASCATA = {"GC=F", "CL=F"}
    cascata  = ticker in _CASCATA
    p    = ind["preco"]
    m20  = ind.get("mm20_d",  float("nan"))
    m50  = ind.get("mm50_d",  float("nan"))
    m100 = ind.get("mm100_d", float("nan"))
    m200 = ind.get("mm200_d", float("nan"))

    # MM200 disponível
    if not math.isnan(m200) and not math.isnan(m50):
        if p > m50 and m50 > m200: return "ALTA",  ""
        if p < m50 and m50 < m200: return "BAIXA", ""
        if not cascata:             return "INDEFINIDA", ""
        # GC=F/CL=F: sinal misto → cai para MM100

    # MM100
    if not math.isnan(m100) and not math.isnan(m50):
        if p > m50 and m50 > m100: return "ALTA",  "via MM100"
        if p < m50 and m50 < m100: return "BAIXA", "via MM100"
        if not cascata:             return "INDEFINIDA", ""
        # GC=F/CL=F: sinal misto → cai para MM50

    # MM50 sozinho
    if not math.isnan(m50):
        tend = "ALTA" if p > m50 else "BAIXA"
        return tend, ("via MM50" if cascata else "")

    # MM20 (último recurso)
    if not math.isnan(m20):
        tend = "ALTA" if p > m20 else "BAIXA"
        return tend, "via MM20"

    return "INDEFINIDA", ""


def sessao_operacional(activo_cfg):
    """Retorna (pode_operar: bool, nome_sessao: str)"""
    if activo_cfg.get("cripto", False):
        return True, "24/7"
    dt = datetime.now(timezone.utc)
    t  = dt.hour * 60 + dt.minute   # minutos desde meia-noite UTC
    if 6 * 60 + 45 <= t < 12 * 60:  # 06:45–12:00 UTC  (Londra + pré-abertura)
        return True, "LONDRA"
    if 13 * 60 <= t < 21 * 60:      # 13:00–21:00 UTC  (Nova Iorque)
        return True, "NEW YORK"
    return False, f"FECHADO({dt.hour:02d}h)"


def activos_correlacionados(a, b):
    return any(a in g and b in g for g in CORRELACOES)


def em_cooldown(carteira, nome):
    ts = carteira.get("cooldowns", {}).get(nome)
    if not ts:
        return False
    ultimo_sl = datetime.fromisoformat(ts)
    return (datetime.now() - ultimo_sl) < timedelta(minutes=COOLDOWN_MIN)


def registar_cooldown(carteira, nome):
    carteira.setdefault("cooldowns", {})[nome] = datetime.now().isoformat(timespec="seconds")
    return carteira


# ─── MERCADO ──────────────────────────────────────────────────────────────────
def obter_indicadores(ticker):
    # Dados horários — preço, RSI, MACD, Bollinger, Stoch, Volume
    dh     = yf.download(ticker, period="60d", interval="1h", progress=False)
    ch     = dh["Close"].squeeze()
    hh     = dh["High"].squeeze()
    lh     = dh["Low"].squeeze()
    vh     = dh["Volume"].squeeze()

    preco   = ch.iloc[-1].item()
    var_24h = (preco - ch.iloc[-24].item()) / ch.iloc[-24].item() * 100

    delta    = ch.diff()
    ganho    = delta.where(delta > 0, 0).rolling(14).mean()
    perda    = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi_s    = 100 - (100 / (1 + ganho / perda))
    rsi      = rsi_s.iloc[-1].item()
    rsi_prev = rsi_s.iloc[-2].item()

    ema12    = ch.ewm(span=12).mean()
    ema26    = ch.ewm(span=26).mean()
    macd_l   = ema12 - ema26
    sig      = macd_l.ewm(span=9).mean()
    hist_s   = macd_l - sig
    macd_hist      = hist_s.iloc[-1].item()
    macd_hist_prev = hist_s.iloc[-2].item()

    mm20h  = ch.rolling(20).mean()
    std20h = ch.rolling(20).std()
    bb_up  = (mm20h + 2 * std20h).iloc[-1].item()
    bb_dn  = (mm20h - 2 * std20h).iloc[-1].item()
    bb_mid = mm20h.iloc[-1].item()

    l14h   = lh.rolling(14).min()
    h14h   = hh.rolling(14).max()
    stk_s  = 100 * (ch - l14h) / (h14h - l14h)
    stoch_k      = stk_s.iloc[-1].item()
    stoch_k_prev = stk_s.iloc[-2].item()

    vol_med   = vh.rolling(20).mean().iloc[-1].item()
    vol_ratio = vh.iloc[-1].item() / vol_med if vol_med > 0 else 1.0

    # Dados diários — MM50, MM200, ATR diário
    dd    = yf.download(ticker, period="400d", interval="1d", progress=False)
    cd    = dd["Close"].squeeze()
    hd    = dd["High"].squeeze()
    ld    = dd["Low"].squeeze()

    mm20_d  = cd.rolling(20).mean().iloc[-1].item()
    mm50_d  = cd.rolling(50).mean().iloc[-1].item()
    mm100_d = cd.rolling(100).mean().iloc[-1].item()
    mm200_d = cd.rolling(200).mean().iloc[-1].item()

    tr1d  = hd - ld
    tr2d  = (hd - cd.shift(1)).abs()
    tr3d  = (ld - cd.shift(1)).abs()
    atr_d = pd.concat([tr1d, tr2d, tr3d], axis=1).max(axis=1).rolling(14).mean().iloc[-1].item()

    return {
        "preco": preco, "var_24h": var_24h,
        "rsi": rsi, "rsi_prev": rsi_prev,
        "macd_hist": macd_hist, "macd_hist_prev": macd_hist_prev,
        "bb_up": bb_up, "bb_mid": bb_mid, "bb_dn": bb_dn,
        "stoch_k": stoch_k, "stoch_k_prev": stoch_k_prev,
        "vol_ratio": vol_ratio,
        "mm20_d": mm20_d, "mm50_d": mm50_d, "mm100_d": mm100_d, "mm200_d": mm200_d,
        "atr_d": atr_d,
    }


def obter_noticias(activo_cfg):
    api_key  = os.environ.get("NEWSAPI_KEY")
    noticias = []
    if api_key:
        try:
            for termo in activo_cfg["noticias_termos"]:
                r = requests.get(
                    f"https://newsapi.org/v2/everything?q={termo}&language=en&sortBy=publishedAt&pageSize=3",
                    headers={"X-Api-Key": api_key}, timeout=5,
                )
                if r.status_code == 200:
                    for a in r.json().get("articles", []):
                        t = a.get("title", "")
                        if t and t not in noticias and len(t) > 10:
                            noticias.append(t)
        except Exception:
            pass
    if len(noticias) < 4:
        noticias += activo_cfg["noticias_fallback"]
    return noticias[:6]


# ─── ANÁLISE ──────────────────────────────────────────────────────────────────
def analisar_noticias(noticias, activo_cfg, tendencia):
    """Chama Claude apenas para avaliar o sentimento das notícias."""
    nome     = activo_cfg["nome"]
    direcao  = "COMPRAR (alta)" if tendencia == "ALTA" else "VENDER (baixa)"
    texto    = "\n".join(f"  {i+1}. {n}" for i, n in enumerate(noticias))
    prompt   = (
        f"Analisa o sentimento das notícias para {nome} em relação a uma posição {direcao}.\n\n"
        f"Notícias:\n{texto}\n\n"
        "Responde APENAS com JSON válido sem markdown:\n"
        '{"sentimento":"POSITIVO ou NEGATIVO ou NEUTRO","raciocinio":"1 frase directa"}'
    )
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=25.0)
    resp   = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )
    texto_resp = resp.content[0].text.strip()
    if "```" in texto_resp:
        texto_resp = texto_resp.split("```")[1]
        if texto_resp.startswith("json"):
            texto_resp = texto_resp[4:]
    return json.loads(texto_resp.strip())


def calcular_score(ind, tendencia, sentimento):
    """
    100 pontos máximo — retorna (score: int, breakdown: dict):
      RSI favorável      +20
      MACD hist          +20
      Stochastic         +15
      Volume ≥ 0.7×      +15
      Bollinger          +15
      Notícias           +15
    """
    bd       = {"rsi": 0, "macd": 0, "stoch": 0, "volume": 0, "bollinger": 0, "noticias": 0}
    bb_range = ind["bb_up"] - ind["bb_dn"]

    if tendencia == "ALTA":
        if ind["rsi"] < 55:
            bd["rsi"] = 20
        if ind["macd_hist"] > 0 or ind["macd_hist"] > ind["macd_hist_prev"]:
            bd["macd"] = 20
        if ind["stoch_k"] < 50 and ind["stoch_k"] > ind["stoch_k_prev"]:
            bd["stoch"] = 15
        if ind["vol_ratio"] >= 0.7:
            bd["volume"] = 15
        if bb_range > 0 and (ind["preco"] - ind["bb_dn"]) / bb_range <= 0.35:
            bd["bollinger"] = 15
        if sentimento == "POSITIVO":
            bd["noticias"] = 15
        elif sentimento == "NEUTRO":
            bd["noticias"] = 7
    else:  # BAIXA
        if ind["rsi"] > 45:
            bd["rsi"] = 20
        if ind["macd_hist"] < 0 or ind["macd_hist"] < ind["macd_hist_prev"]:
            bd["macd"] = 20
        if ind["stoch_k"] > 50 and ind["stoch_k"] < ind["stoch_k_prev"]:
            bd["stoch"] = 15
        if ind["vol_ratio"] >= 0.7:
            bd["volume"] = 15
        if bb_range > 0 and (ind["preco"] - ind["bb_dn"]) / bb_range >= 0.65:
            bd["bollinger"] = 15
        if sentimento == "NEGATIVO":
            bd["noticias"] = 15
        elif sentimento == "NEUTRO":
            bd["noticias"] = 7

    score = min(sum(bd.values()), 100)
    return score, bd


# ─── GESTÃO DE POSIÇÕES ───────────────────────────────────────────────────────
def verificar_posicoes(carteira, precos):
    posicoes    = carteira["posicoes_abertas"]
    manter      = []
    fechados_sl = []

    for pos in posicoes:
        nome  = pos["activo"]
        preco = precos.get(nome)
        if preco is None:
            print(f"  ⚠  [{nome}] preço indisponível — SL/TP não verificado")
            manter.append(pos)
            continue

        # ── TRAILING STOP: actualiza melhor_preco e SL ────────────────────
        trailing_mult = _TRAILING_MULT.get(nome, 1.0)
        atr           = pos.get("atr_d", 0)
        melhor        = pos.get("melhor_preco", pos["preco_entrada"])
        agora_str     = datetime.now().strftime("%Y-%m-%d %H:%M")

        if pos["tipo"] == "VENDER":
            if preco < melhor:
                pos["melhor_preco"]      = round(preco, 4)
                pos["hora_melhor_preco"] = agora_str
                melhor = preco
            novo_sl = round(melhor + atr * trailing_mult, 4)
            if novo_sl < pos["stop_loss"]:
                sl_anterior = pos["stop_loss"]
                pos["stop_loss"] = novo_sl
                lp = (pos["preco_entrada"] - novo_sl) * pos["contratos"]
                sinal_lp = "+" if lp >= 0 else ""
                print(f"  [TRAILING] {nome} VENDER | Melhor: ${melhor:.4f} | "
                      f"SL: ${sl_anterior:.4f} → ${novo_sl:.4f} | "
                      f"Lucro protegido: {sinal_lp}${lp:.2f}")
        else:  # COMPRAR
            if preco > melhor:
                pos["melhor_preco"]      = round(preco, 4)
                pos["hora_melhor_preco"] = agora_str
                melhor = preco
            novo_sl = round(melhor - atr * trailing_mult, 4)
            if novo_sl > pos["stop_loss"]:
                sl_anterior = pos["stop_loss"]
                pos["stop_loss"] = novo_sl
                lp = (novo_sl - pos["preco_entrada"]) * pos["contratos"]
                sinal_lp = "+" if lp >= 0 else ""
                print(f"  [TRAILING] {nome} COMPRAR | Melhor: ${melhor:.4f} | "
                      f"SL: ${sl_anterior:.4f} → ${novo_sl:.4f} | "
                      f"Lucro protegido: {sinal_lp}${lp:.2f}")

        # ── VERIFICAR FECHO ───────────────────────────────────────────────
        resultado   = None
        lucro_bruto = 0.0
        preco_fecho = preco

        if pos["tipo"] == "COMPRAR":
            if preco >= pos["take_profit"]:
                lucro_bruto = (pos["take_profit"] - pos["preco_entrada"]) * pos["contratos"]
                preco_fecho = pos["take_profit"]
                resultado   = "GANHOU"
            elif preco <= pos["stop_loss"]:
                lucro_bruto = (pos["stop_loss"] - pos["preco_entrada"]) * pos["contratos"]
                preco_fecho = pos["stop_loss"]
                resultado   = "PENDENTE_SL"   # recalculado após custo
        elif pos["tipo"] == "VENDER":
            if preco <= pos["take_profit"]:
                lucro_bruto = (pos["preco_entrada"] - pos["take_profit"]) * pos["contratos"]
                preco_fecho = pos["take_profit"]
                resultado   = "GANHOU"
            elif preco >= pos["stop_loss"]:
                lucro_bruto = (pos["preco_entrada"] - pos["stop_loss"]) * pos["contratos"]
                preco_fecho = pos["stop_loss"]
                resultado   = "PENDENTE_SL"   # recalculado após custo

        if resultado:
            # Motivo do fecho
            if preco_fecho == pos["take_profit"]:
                motivo_fecho = "Atingiu Take Profit"
            elif abs(pos["stop_loss"] - pos.get("sl_original", pos["stop_loss"])) > 0.0001:
                motivo_fecho = "Trailing Stop activado"
            else:
                motivo_fecho = "Atingiu Stop Loss"
            try:
                hora_ab     = datetime.strptime(pos["hora_abertura"], "%Y-%m-%d %H:%M")
                duracao_min = int((datetime.now() - hora_ab).total_seconds() / 60)
            except Exception:
                hora_ab     = None
                duracao_min = 0

            minutos_ate_pico = None
            if hora_ab is not None and pos.get("hora_melhor_preco"):
                try:
                    hora_pico = datetime.strptime(pos["hora_melhor_preco"], "%Y-%m-%d %H:%M")
                    minutos_ate_pico = int((hora_pico - hora_ab).total_seconds() / 60)
                except Exception:
                    minutos_ate_pico = None

            custo_fecho   = preco_fecho * pos["contratos"] * CUSTO_OP
            lucro_liquido = lucro_bruto - custo_fecho
            if resultado == "PENDENTE_SL":
                resultado = "GANHOU" if lucro_liquido >= 0 else "PERDEU"
            carteira["saldo"]         += lucro_liquido
            carteira["custos_totais"] += custo_fecho
            carteira["historico"].append({
                "activo":             nome,
                "tipo":               pos["tipo"],
                "entrada":            pos["preco_entrada"],
                "saida":              round(preco_fecho, 4),
                "lucro_bruto":        round(lucro_bruto, 2),
                "custos":             round(pos.get("custo_entrada", 0) + custo_fecho, 4),
                "lucro_liquido":      round(lucro_liquido, 2),
                "lucro":              round(lucro_liquido, 2),
                "resultado":          resultado,
                "hora_abertura":      pos["hora_abertura"],
                "hora_fecho":         datetime.now().strftime("%Y-%m-%d %H:%M"),
                "hora":               datetime.now().strftime("%Y-%m-%d %H:%M"),
                "motivo_fecho":       motivo_fecho,
                "duracao":            duracao_min,
                "score_entrada":      pos.get("score_entrada", 0),
                "raciocinio_entrada": pos.get("raciocinio_entrada", ""),
                "melhor_preco":       pos.get("melhor_preco"),
                "hora_melhor_preco":  pos.get("hora_melhor_preco"),
                "minutos_ate_pico":   minutos_ate_pico,
            })
            sinal = "+" if lucro_liquido >= 0 else ""
            print(f"  ✓ FECHADA [{nome}] {resultado} ({motivo_fecho}) | "
                  f"Bruto:${lucro_bruto:.2f} Custos:${custo_fecho:.2f} Líquido:${sinal}{lucro_liquido:.2f}")
            if resultado == "PERDEU":
                fechados_sl.append(nome)
        else:
            if pos["tipo"] == "COMPRAR":
                pnl = (preco - pos["preco_entrada"]) * pos["contratos"]
            else:
                pnl = (pos["preco_entrada"] - preco) * pos["contratos"]
            sinal = "+" if pnl >= 0 else ""
            dist_sl = abs(preco - pos["stop_loss"])
            dist_tp = abs(preco - pos["take_profit"])
            print(f"  ↔ ABERTA [{nome}] {pos['tipo']} @ {pos['preco_entrada']:.4f} "
                  f"| Actual: {preco:.4f} | PnL:{sinal}${pnl:.2f} "
                  f"| SL-dist:{dist_sl:.4f} TP-dist:{dist_tp:.4f}")
            pos["preco_atual"] = round(preco, 4)
            pos["pnl_atual"]   = round(pnl, 2)
            manter.append(pos)

    carteira["posicoes_abertas"] = manter
    for nome in fechados_sl:
        carteira = registar_cooldown(carteira, nome)
    return carteira


_SL_TP_ACTIVO = {
    # nome        : (sl_mult, tp_mult)
    "Bitcoin"  : (1.0, 2.0),
    "Ethereum" : (1.0, 2.0),
    "Ouro"     : (1.5, 3.0),
    "Petróleo" : (1.0, 2.0),
    "Prata"    : (1.0, 2.0),
    "EUR/USD"  : (1.0, 2.0),
    "S&P 500"  : (1.5, 2.5),
    "Nasdaq"   : (1.5, 2.5),
}

_TRAILING_MULT = {
    "Bitcoin"  : 1.0,
    "Ethereum" : 1.0,
    "Ouro"     : 0.8,
    "Petróleo" : 1.0,
    "Prata"    : 0.8,
    "EUR/USD"  : 0.5,
    "S&P 500"  : 0.8,
    "Nasdaq"   : 0.8,
}


def calcular_sl_tp(activo, preco, atr_diario, tipo):
    """Devolve (stop_loss, take_profit) com multiplicadores específicos por activo."""
    sl_mult, tp_mult = _SL_TP_ACTIVO.get(activo, (SL_MULT, TP_MULT))
    if tipo == "COMPRAR":
        stop_loss   = round(preco - sl_mult * atr_diario, 4)
        take_profit = round(preco + tp_mult * atr_diario, 4)
    else:
        stop_loss   = round(preco + sl_mult * atr_diario, 4)
        take_profit = round(preco - tp_mult * atr_diario, 4)
    return stop_loss, take_profit


def abrir_posicao(carteira, activo, tipo, preco, atr_d, permitir_correlacao=False):
    if len(carteira["posicoes_abertas"]) >= MAX_POSICOES:
        return carteira, False, "máximo de posições atingido"

    nomes_abertos = {p["activo"] for p in carteira["posicoes_abertas"]}
    for nome_ab in nomes_abertos:
        if activos_correlacionados(activo, nome_ab):
            if not permitir_correlacao:
                return carteira, False, f"correlacionado com {nome_ab}"
            print(f"  ⚡ [{activo}] entra com correlação (par {nome_ab}) — threshold {THRESHOLD_CORRELACAO}% ✓")

    stop_loss, take_profit = calcular_sl_tp(activo, preco, atr_d, tipo)

    risco = abs(preco - stop_loss)
    if risco <= 0:
        return carteira, False, "risco a zero"

    contratos     = round(carteira["saldo"] * CAPITAL_POR_OP / risco, 6)
    custo_entrada = preco * contratos * CUSTO_OP
    carteira["saldo"]         -= custo_entrada
    carteira["custos_totais"] += custo_entrada
    carteira["posicoes_abertas"].append({
        "activo":             activo,
        "tipo":               tipo,
        "preco_entrada":      round(preco, 4),
        "stop_loss":          round(stop_loss, 4),
        "sl_original":        round(stop_loss, 4),
        "take_profit":        round(take_profit, 4),
        "contratos":          contratos,
        "atr_d":              round(atr_d, 4),
        "hora_abertura":      datetime.now().strftime("%Y-%m-%d %H:%M"),
        "custo_entrada":      round(custo_entrada, 4),
        "melhor_preco":       round(preco, 4),
        "hora_melhor_preco":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        "score_entrada":      0,
        "raciocinio_entrada": "",
    })
    print(f"  ★ ABERTA [{activo}] {tipo} @ {preco:.4f} | "
          f"SL:{stop_loss:.4f} TP:{take_profit:.4f} | ATR-d:{atr_d:.4f} Custo:${custo_entrada:.2f}")
    return carteira, True, f"{tipo} @ {preco:.4f}"


# ─── DASHBOARD ────────────────────────────────────────────────────────────────
def atualizar_dashboard(carteira, novas_analises=None):
    dados = {"carteira": {}, "analises": [], "posicao_aberta": None}
    if os.path.exists(FICHEIRO_DASHBOARD):
        try:
            with open(FICHEIRO_DASHBOARD, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass

    stats    = carteira.get("estatisticas", {})
    posicoes = carteira.get("posicoes_abertas", [])

    posicoes_dash = []
    for pos in posicoes:
        p = dict(pos)
        if pos["tipo"] == "VENDER":
            lp = (pos["preco_entrada"] - pos["stop_loss"]) * pos["contratos"]
        else:
            lp = (pos["stop_loss"] - pos["preco_entrada"]) * pos["contratos"]
        p["sl_atual"]        = pos["stop_loss"]
        p["lucro_protegido"] = round(lp, 2)
        posicoes_dash.append(p)

    dados["carteira"] = {
        "saldo":              carteira["saldo"],
        "custos_totais":      carteira.get("custos_totais", 0),
        "posicoes_abertas":   posicoes_dash,
        "estatisticas":       stats,
        "historico":          carteira.get("historico", []),
        "rentabilidade":      stats.get("rentabilidade", 0),
        "total_operacoes":    stats.get("total_operacoes", 0),
        "operacoes_ganhas":   stats.get("ganhas", 0),
        "operacoes_perdidas": stats.get("perdidas", 0),
        "lucro_total":        stats.get("lucro_liquido_total", 0),
        "win_rate":           stats.get("win_rate", 0),
    }
    dados["posicao_aberta"] = posicoes_dash[0] if posicoes_dash else None

    if novas_analises:
        dados["analises"].extend(novas_analises)
        dados["analises"] = dados["analises"][-500:]

    with open(FICHEIRO_DASHBOARD, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ─── CICLO PRINCIPAL ──────────────────────────────────────────────────────────
def executar_ciclo():
    hora_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")
    print(f"\n{SEP}")
    print(f"=== CICLO 15MIN — {hora_utc} ===")
    print(SEP)

    carteira         = carregar_carteira()
    precos_atuais    = {}
    resultados       = []
    analises_novos   = []
    analise_por_nome = {}   # nome -> índice em analises_novos (para actualizar decisao_final)

    for cfg in ACTIVOS:
        nome   = cfg["nome"]
        ticker = cfg["ticker"]
        print(f"[{nome:<10}] ", end="", flush=True)

        try:
            ind = obter_indicadores(ticker)
            precos_atuais[nome] = ind["preco"]
        except Exception as e:
            print(f"ERRO dados: {e}")
            continue

        tend, tend_nota     = calcular_tendencia(ind, cfg["ticker"])
        pode_operar, sessao = sessao_operacional(cfg)
        cooldown_ativo      = em_cooldown(carteira, nome)
        ja_tem_posicao      = any(p["activo"] == nome for p in carteira["posicoes_abertas"])

        direcao = {"ALTA": "COMPRAR", "BAIXA": "VENDER"}.get(tend)

        # Notícias apenas quando pode entrar (poupa API calls)
        score      = 0
        sentimento = "NEUTRO"
        raciocinio = ""
        pode_entrar = direcao and pode_operar and not cooldown_ativo and not ja_tem_posicao

        if pode_entrar:
            noticias = obter_noticias(cfg)
            try:
                res        = analisar_noticias(noticias, cfg, tend)
                sentimento = res.get("sentimento", "NEUTRO")
                raciocinio = res.get("raciocinio", "")
            except Exception:
                sentimento = "NEUTRO"

        # Score calculado sempre — para INDEFINIDA usa direcção de curto prazo (só display)
        if direcao:
            score, breakdown = calcular_score(ind, tend, sentimento)
        else:
            dir_hint = "ALTA" if ind["macd_hist"] > ind["macd_hist_prev"] else "BAIXA"
            score, breakdown = calcular_score(ind, dir_hint, "NEUTRO")

        # Motivos de skip para o log
        skips = []
        if ja_tem_posicao:               skips.append("posição aberta")
        if not direcao:                  skips.append("tend.INDEFINIDA")
        if not pode_operar:              skips.append(sessao)
        if cooldown_ativo:               skips.append("cooldown SL")
        if tend_nota:                    skips.append(tend_nota)
        skip_str = f"  [{', '.join(skips)}]" if skips else ""

        flag = " ★" if (pode_entrar and score >= THRESHOLD_ENTRADA) else ""
        print(f"${ind['preco']:<12.4f} | {tend:<10} | {score:>3}% | {sessao:<14} | "
              f"{(direcao or 'AGUARDAR'):<8}{flag}{skip_str}")

        # Decisão preliminar (actualizada no loop de execução se for candidato)
        if ja_tem_posicao:
            decisao_parcial = "AGUARDOU — posição já aberta"
        elif not direcao:
            decisao_parcial = "AGUARDOU — tendência INDEFINIDA"
        elif not pode_operar:
            decisao_parcial = f"AGUARDOU — fora de sessão ({sessao})"
        elif cooldown_ativo:
            decisao_parcial = "AGUARDOU — cooldown SL activo"
        elif not (RSI_MIN_ENTRADA <= ind["rsi"] <= RSI_MAX_ENTRADA):
            decisao_parcial = (
                f"REJEITADO — RSI {ind['rsi']:.1f} fora de "
                f"[{RSI_MIN_ENTRADA},{RSI_MAX_ENTRADA}]"
            )
        elif score < THRESHOLD_ENTRADA:
            decisao_parcial = f"AGUARDOU — score {score}% < {THRESHOLD_ENTRADA}%"
        else:
            decisao_parcial = f"CANDIDATO — score {score}% ≥ {THRESHOLD_ENTRADA}%"

        resultados.append((cfg, ind, tend, score, direcao, pode_entrar, sessao, sentimento))
        analise_por_nome[nome] = len(analises_novos)
        analises_novos.append({
            "hora":                datetime.now().strftime("%Y-%m-%d %H:%M"),
            "activo":              nome,
            "simbolo":             cfg["simbolo"],
            "preco":               round(ind["preco"], 4),
            "rsi":                 round(ind["rsi"], 1),
            "decisao":             direcao or "AGUARDAR",
            "score":               score,
            "confianca":           score,
            "tendencia":           tend,
            "sessao":              sessao,
            "risco":               "ALTO" if score >= 75 else "MEDIO" if score >= 60 else "BAIXO",
            "raciocinio":          raciocinio,
            "sentimento_noticias": sentimento,
            "factores_positivos":  [],
            "factores_negativos":  [],
            "score_breakdown":     {**breakdown, "sessao": 0},
            "filtros_aplicados": {
                "tendencia":  tend,
                "rsi_ok":     RSI_MIN_ENTRADA <= ind["rsi"] <= RSI_MAX_ENTRADA,
                "sessao_ok":  pode_operar,
                "score_ok":   score >= THRESHOLD_ENTRADA,
                "correlacao": "livre",
            },
            "decisao_final":       decisao_parcial,
        })

    # Verificar SL/TP nas posições abertas
    if precos_atuais:
        carteira = verificar_posicoes(carteira, precos_atuais)

    # Seleccionar e executar melhores oportunidades
    candidatos = sorted(
        [(cfg, ind, score, direcao)
         for cfg, ind, tend, score, direcao, pode, sessao, sent in resultados
         if pode and direcao and score >= THRESHOLD_ENTRADA],
        key=lambda x: x[2], reverse=True,
    )

    nomes_posicoes = {p["activo"] for p in carteira["posicoes_abertas"]}
    acoes = []

    n_normais_ab = min(len(carteira["posicoes_abertas"]), MAX_POSICOES_NORMAL)
    n_premium_ab = max(0, len(carteira["posicoes_abertas"]) - MAX_POSICOES_NORMAL)
    print(f"[SLOTS]        {n_normais_ab}/{MAX_POSICOES_NORMAL} normais | {n_premium_ab}/2 premium")

    for cfg, ind, score, direcao in candidatos:
        nome = cfg["nome"]
        if nome in nomes_posicoes:
            continue

        n_abertas    = len(carteira["posicoes_abertas"])
        slot_premium = n_abertas >= MAX_POSICOES_NORMAL

        if n_abertas >= MAX_POSICOES:
            break

        # Verificar threshold para slot premium
        if slot_premium and score < THRESHOLD_PREMIUM:
            acoes.append(
                f"[CHEIO] {nome} score:{score}% — slots normais cheios, "
                f"score insuficiente para premium ({score}% < {THRESHOLD_PREMIUM}%)"
            )
            idx = analise_por_nome.get(nome)
            if idx is not None:
                analises_novos[idx]["decisao_final"] = (
                    f"REJEITADO — slots normais cheios, score {score}% < {THRESHOLD_PREMIUM}% premium"
                )
            continue

        if em_cooldown(carteira, nome):
            acoes.append(f"REJEITADO {nome}: cooldown SL activo")
            continue
        rsi = ind["rsi"]
        if not (RSI_MIN_ENTRADA <= rsi <= RSI_MAX_ENTRADA):
            acoes.append(
                f"[REJEITADO] {nome}: RSI extremo {rsi:.0f} fora de "
                f"[{RSI_MIN_ENTRADA},{RSI_MAX_ENTRADA}] — aguarda normalização"
            )
            idx = analise_por_nome.get(nome)
            if idx is not None:
                analises_novos[idx]["decisao_final"] = (
                    f"REJEITADO — RSI extremo {rsi:.1f} fora de "
                    f"[{RSI_MIN_ENTRADA},{RSI_MAX_ENTRADA}]"
                )
            continue
        corr = next((n for n in nomes_posicoes if activos_correlacionados(nome, n)), None)
        entrada_com_correlacao = False
        if corr:
            idx_corr = analise_por_nome.get(nome)
            if score < THRESHOLD_CORRELACAO:
                acoes.append(
                    f"[REJEITADO] {nome}: correlacionado com {corr} "
                    f"(score {score}% < {THRESHOLD_CORRELACAO}% exigido)"
                )
                if idx_corr is not None:
                    analises_novos[idx_corr]["filtros_aplicados"]["correlacao"] = "bloqueado"
                    analises_novos[idx_corr]["decisao_final"] = (
                        f"REJEITADO — correlacionado com {corr} "
                        f"(score {score}% < {THRESHOLD_CORRELACAO}%)"
                    )
                continue
            acoes.append(
                f"[CORRELAÇÃO] {corr} já aberto → threshold {nome} sobe para {THRESHOLD_CORRELACAO}%"
            )
            entrada_com_correlacao = True
            if idx_corr is not None:
                analises_novos[idx_corr]["filtros_aplicados"]["correlacao"] = (
                    f"threshold {THRESHOLD_CORRELACAO}%"
                )

        carteira, aberta, motivo = abrir_posicao(
            carteira, nome, direcao, ind["preco"], ind["atr_d"],
            permitir_correlacao=entrada_com_correlacao,
        )
        idx_ab = analise_por_nome.get(nome)
        if aberta:
            nomes_posicoes.add(nome)
            # Gravar score e raciocínio na posição recém-aberta
            if carteira["posicoes_abertas"]:
                ultima = carteira["posicoes_abertas"][-1]
                ultima["score_entrada"] = score
                if idx_ab is not None:
                    ultima["raciocinio_entrada"] = analises_novos[idx_ab].get("raciocinio", "")
            slot_num = len(carteira["posicoes_abertas"])
            if slot_premium:
                n_prem = slot_num - MAX_POSICOES_NORMAL
                acoes.append(
                    f"[SLOT PREMIUM] {nome} score:{score}% >= {THRESHOLD_PREMIUM}% → ENTROU "
                    f"(slot premium {n_prem}/2)"
                )
            elif entrada_com_correlacao:
                acoes.append(
                    f"[ENTROU] {nome} {direcao} @ {ind['preco']:.4f}  "
                    f"score:{score}% >= {THRESHOLD_CORRELACAO}% threshold correlação ✓"
                )
            else:
                acoes.append(
                    f"[NORMAL] {nome} {direcao} score:{score}% — slot {slot_num}/{MAX_POSICOES_NORMAL}"
                )
            if idx_ab is not None:
                analises_novos[idx_ab]["decisao_final"] = (
                    f"ENTROU {direcao} @ {ind['preco']:.4f} — score {score}%"
                )
        else:
            acoes.append(f"REJEITADO {nome}: {motivo}")
            if idx_ab is not None:
                analises_novos[idx_ab]["decisao_final"] = f"REJEITADO — {motivo}"

    if not acoes:
        if candidatos:
            acoes.append("AGUARDOU — posições cheias ou correlação")
        else:
            melhor = max((score for _, _, _, score, _, _, _, _ in resultados), default=0)
            razao  = (f"score máx {melhor}% < {THRESHOLD_ENTRADA}%" if melhor < THRESHOLD_ENTRADA
                      else "sem tendência definida ou fora de sessão")
            acoes.append(f"AGUARDOU — {razao}")

    print()
    for a in acoes:
        print(f"[ACÇÃO]        {a}")

    atualizar_estatisticas(carteira)
    stats   = carteira["estatisticas"]
    pos_str = ", ".join(f"{p['activo']} {p['tipo']}" for p in carteira["posicoes_abertas"]) or "—"
    print(f"[CARTEIRA]     ${carteira['saldo']:.2f} ({stats['rentabilidade']:+.2f}%) | "
          f"Posições: {pos_str} | Lucro: ${stats['lucro_liquido_total']:.2f} | "
          f"Custos: ${stats['custos_totais']:.2f}")
    print(SEP)

    guardar_carteira(carteira)
    atualizar_dashboard(carteira, analises_novos)
    print(f"  → {len(analises_novos)} análises | próximo ciclo em 15 minutos\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    _init_db()   # cria tabela + semeia com JSON local se DB estiver vazia
    print(f"\n{SEP}")
    print(f"  ROBOTRADING ESTRATÉGIA v3")
    print(f"  8 activos | Ciclo 15min | Slots: {MAX_POSICOES_NORMAL} normais≥{THRESHOLD_ENTRADA}% + 2 premium≥{THRESHOLD_PREMIUM}%")
    print(f"  SL/TP por activo | Capital/op={CAPITAL_POR_OP*100:.1f}% | Cooldown={COOLDOWN_MIN}min após SL")
    print(f"  RSI válido: [{RSI_MIN_ENTRADA},{RSI_MAX_ENTRADA}] | Corr threshold: {THRESHOLD_CORRELACAO}%")
    print(f"  Sessões: Londra 06:45-12 UTC | NY 13-21 UTC | Cripto 24/7")
    print(f"  {' | '.join(a['nome'] for a in ACTIVOS)}")
    print(f"{SEP}\n")

    while True:
        try:
            executar_ciclo()
            time.sleep(900)
        except KeyboardInterrupt:
            print("\nRobô parado.")
            break
        except Exception as e:
            print(f"\n[ERRO CICLO] {e}")
            print("  A tentar novamente em 5 minutos...\n")
            time.sleep(300)


if __name__ == "__main__":
    main()
