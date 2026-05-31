import yfinance as yf
import anthropic
import requests
import os
import json
import time
import threading
import http.server
import warnings
from datetime import datetime, timezone, timedelta

import pandas as pd

warnings.filterwarnings("ignore")

# ─── CONFIGURAÇÃO ─────────────────────────────────────────────────────────────
BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
FICHEIRO_CARTEIRA = os.path.join(BASE_DIR, "carteira.json")
FICHEIRO_DASHBOARD= os.path.join(BASE_DIR, "dados_dashboard.json")
SALDO_INICIAL     = 10000.0
THRESHOLD_ENTRADA     = 60   # score >= 60 → entra
THRESHOLD_CORRELACAO  = 80   # score >= 80 → entra mesmo com activo correlacionado aberto
RSI_MIN_ENTRADA       = 35   # RSI mínimo para qualquer entrada (evita oversold extremo)
RSI_MAX_ENTRADA       = 65   # RSI máximo para qualquer entrada (evita overbought extremo)
CUSTO_OP          = 0.001    # 0.1% entrada + 0.1% saída
SL_MULT           = 2.0      # Stop Loss  = 2× ATR diário
TP_MULT           = 4.0      # Take Profit = 4× ATR diário (ratio 1:2)
CAPITAL_POR_OP    = 0.02     # 2% do saldo por operação
MAX_POSICOES      = 2
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
        }
        def do_GET(self):
            path = self.path.split("?")[0]
            if path not in self.ROTAS:
                self.send_error(404)
                return
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


def carregar_carteira():
    if os.path.exists(FICHEIRO_CARTEIRA):
        with open(FICHEIRO_CARTEIRA, "r") as f:
            c = json.load(f)
        if "posicao_aberta" in c:
            pos_antiga = c.pop("posicao_aberta")
            if pos_antiga and "posicoes_abertas" not in c:
                c["posicoes_abertas"] = [pos_antiga]
        c.setdefault("posicoes_abertas", [])
        c.setdefault("custos_totais", 0.0)
        c.setdefault("historico", [])
        c.setdefault("cooldowns", {})
        c.setdefault("estatisticas", carteira_vazia()["estatisticas"])
        return c
    return carteira_vazia()


def guardar_carteira(carteira):
    with open(FICHEIRO_CARTEIRA, "w") as f:
        json.dump(carteira, f, ensure_ascii=False, indent=2)


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
def calcular_tendencia(ind):
    """ALTA: preco>MM50>MM200 | BAIXA: preco<MM50<MM200 | INDEFINIDA: resto.
    Fallback para MM100 quando MM200 é NaN (dados insuficientes em futuros)."""
    import math
    p, m50, m200 = ind["preco"], ind["mm50_d"], ind["mm200_d"]
    if not math.isnan(m200):
        if p > m50 and m50 > m200:
            return "ALTA"
        if p < m50 and m50 < m200:
            return "BAIXA"
        return "INDEFINIDA"
    # MM200 indisponível — usa MM100 como referência de tendência longa
    m100 = ind.get("mm100_d", float("nan"))
    if not math.isnan(m100):
        if p > m50 and m50 > m100:
            return "ALTA"
        if p < m50 and m50 < m100:
            return "BAIXA"
        return "INDEFINIDA"  # MM100 disponível mas condições mistas — genuinamente indefinida
    # MM100 também indisponível — último recurso: preço vs MM50
    if not math.isnan(m50):
        return "ALTA" if p > m50 else "BAIXA"
    return "INDEFINIDA"


def sessao_operacional(activo_cfg):
    """Retorna (pode_operar: bool, nome_sessao: str)"""
    if activo_cfg.get("cripto", False):
        return True, "24/7"
    h = datetime.now(timezone.utc).hour
    if 7 <= h < 12:
        return True, "LONDRA"
    if 13 <= h < 21:
        return True, "NEW YORK"
    return False, f"FECHADO({h:02d}h)"


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
        "mm50_d": mm50_d, "mm100_d": mm100_d, "mm200_d": mm200_d,
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
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
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
    100 pontos máximo:
      RSI favorável      +20
      MACD hist          +20
      Stochastic         +15
      Volume ≥ 0.7×      +15
      Bollinger          +15
      Notícias           +15
    """
    score   = 0
    bb_range = ind["bb_up"] - ind["bb_dn"]

    if tendencia == "ALTA":
        # RSI: não sobrecomprado (tem espaço para subir)
        if ind["rsi"] < 55:
            score += 20

        # MACD: histograma positivo ou a recuperar
        if ind["macd_hist"] > 0 or ind["macd_hist"] > ind["macd_hist_prev"]:
            score += 20

        # Stochastic: a virar de zona de sobrevenda
        if ind["stoch_k"] < 50 and ind["stoch_k"] > ind["stoch_k_prev"]:
            score += 15

        # Volume
        if ind["vol_ratio"] >= 0.7:
            score += 15

        # Bollinger: preço no terço inferior (toca banda inferior)
        if bb_range > 0 and (ind["preco"] - ind["bb_dn"]) / bb_range <= 0.35:
            score += 15

        # Notícias
        if sentimento == "POSITIVO":
            score += 15
        elif sentimento == "NEUTRO":
            score += 7

    else:  # BAIXA
        # RSI: não sobrevendido (tem espaço para descer)
        if ind["rsi"] > 45:
            score += 20

        # MACD: histograma negativo ou a deteriorar
        if ind["macd_hist"] < 0 or ind["macd_hist"] < ind["macd_hist_prev"]:
            score += 20

        # Stochastic: a virar de zona de sobrecompra
        if ind["stoch_k"] > 50 and ind["stoch_k"] < ind["stoch_k_prev"]:
            score += 15

        # Volume
        if ind["vol_ratio"] >= 0.7:
            score += 15

        # Bollinger: preço no terço superior (toca banda superior)
        if bb_range > 0 and (ind["preco"] - ind["bb_dn"]) / bb_range >= 0.65:
            score += 15

        # Notícias
        if sentimento == "NEGATIVO":
            score += 15
        elif sentimento == "NEUTRO":
            score += 7

    return min(score, 100)


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
                resultado   = "PERDEU"
        elif pos["tipo"] == "VENDER":
            if preco <= pos["take_profit"]:
                lucro_bruto = (pos["preco_entrada"] - pos["take_profit"]) * pos["contratos"]
                preco_fecho = pos["take_profit"]
                resultado   = "GANHOU"
            elif preco >= pos["stop_loss"]:
                lucro_bruto = (pos["preco_entrada"] - pos["stop_loss"]) * pos["contratos"]
                preco_fecho = pos["stop_loss"]
                resultado   = "PERDEU"

        if resultado:
            custo_fecho   = preco_fecho * pos["contratos"] * CUSTO_OP
            lucro_liquido = lucro_bruto - custo_fecho
            carteira["saldo"]         += lucro_liquido
            carteira["custos_totais"] += custo_fecho
            carteira["historico"].append({
                "activo":        nome,
                "tipo":          pos["tipo"],
                "entrada":       pos["preco_entrada"],
                "saida":         round(preco_fecho, 4),
                "lucro_bruto":   round(lucro_bruto, 2),
                "custos":        round(pos.get("custo_entrada", 0) + custo_fecho, 4),
                "lucro_liquido": round(lucro_liquido, 2),
                "lucro":         round(lucro_liquido, 2),
                "resultado":     resultado,
                "hora_abertura": pos["hora_abertura"],
                "hora_fecho":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                "hora":          datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            sinal = "+" if lucro_liquido >= 0 else ""
            print(f"  ✓ FECHADA [{nome}] {resultado} | "
                  f"Bruto:${lucro_bruto:.2f} Custos:${custo_fecho:.2f} Líquido:${sinal}{lucro_liquido:.2f}")
            if resultado == "PERDEU":
                fechados_sl.append(nome)
        else:
            # Mostrar PnL flutuante
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
        "activo":        activo,
        "tipo":          tipo,
        "preco_entrada": round(preco, 4),
        "stop_loss":     round(stop_loss, 4),
        "take_profit":   round(take_profit, 4),
        "contratos":     contratos,
        "atr_d":         round(atr_d, 4),
        "hora_abertura": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "custo_entrada": round(custo_entrada, 4),
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
    dados["carteira"] = {
        "saldo":              carteira["saldo"],
        "custos_totais":      carteira.get("custos_totais", 0),
        "posicoes_abertas":   posicoes,
        "estatisticas":       stats,
        "historico":          carteira.get("historico", []),
        "rentabilidade":      stats.get("rentabilidade", 0),
        "total_operacoes":    stats.get("total_operacoes", 0),
        "operacoes_ganhas":   stats.get("ganhas", 0),
        "operacoes_perdidas": stats.get("perdidas", 0),
        "lucro_total":        stats.get("lucro_liquido_total", 0),
        "win_rate":           stats.get("win_rate", 0),
    }
    dados["posicao_aberta"] = posicoes[0] if posicoes else None

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

    carteira       = carregar_carteira()
    precos_atuais  = {}
    resultados     = []   # (cfg, ind, tend, score, direcao, pode_operar, sessao_nome)
    analises_novos = []

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

        tend                = calcular_tendencia(ind)
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
            score = calcular_score(ind, tend, sentimento)
        else:
            # Indica direcção sugerida pelos indicadores de curto prazo
            dir_hint = "ALTA" if ind["macd_hist"] > ind["macd_hist_prev"] else "BAIXA"
            score = calcular_score(ind, dir_hint, "NEUTRO")

        # Motivos de skip para o log
        skips = []
        if ja_tem_posicao:               skips.append("posição aberta")
        if not direcao:                  skips.append("tend.INDEFINIDA")
        if not pode_operar:              skips.append(sessao)
        if cooldown_ativo:               skips.append("cooldown SL")
        skip_str = f"  [{', '.join(skips)}]" if skips else ""

        flag = " ★" if (pode_entrar and score >= THRESHOLD_ENTRADA) else ""
        print(f"${ind['preco']:<12.4f} | {tend:<10} | {score:>3}% | {sessao:<14} | "
              f"{(direcao or 'AGUARDAR'):<8}{flag}{skip_str}")

        resultados.append((cfg, ind, tend, score, direcao, pode_entrar, sessao, sentimento))
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

    for cfg, ind, score, direcao in candidatos:
        nome = cfg["nome"]
        if nome in nomes_posicoes:
            continue
        if len(carteira["posicoes_abertas"]) >= MAX_POSICOES:
            break
        if em_cooldown(carteira, nome):
            acoes.append(f"REJEITADO {nome}: cooldown SL activo")
            continue
        rsi = ind["rsi"]
        if not (RSI_MIN_ENTRADA <= rsi <= RSI_MAX_ENTRADA):
            acoes.append(
                f"[REJEITADO] {nome}: RSI extremo {rsi:.0f} fora de "
                f"[{RSI_MIN_ENTRADA},{RSI_MAX_ENTRADA}] — aguarda normalização"
            )
            continue
        corr = next((n for n in nomes_posicoes if activos_correlacionados(nome, n)), None)
        entrada_com_correlacao = False
        if corr:
            if score < THRESHOLD_CORRELACAO:
                acoes.append(
                    f"[REJEITADO] {nome}: correlacionado com {corr} "
                    f"(score {score}% < {THRESHOLD_CORRELACAO}% exigido)"
                )
                continue
            acoes.append(
                f"[CORRELAÇÃO] {corr} já aberto → threshold {nome} sobe para {THRESHOLD_CORRELACAO}%"
            )
            entrada_com_correlacao = True

        carteira, aberta, motivo = abrir_posicao(
            carteira, nome, direcao, ind["preco"], ind["atr_d"],
            permitir_correlacao=entrada_com_correlacao,
        )
        if aberta:
            nomes_posicoes.add(nome)
            if entrada_com_correlacao:
                acoes.append(
                    f"[ENTROU] {nome} {direcao} @ {ind['preco']:.4f}  "
                    f"score:{score}% >= {THRESHOLD_CORRELACAO}% threshold correlação ✓"
                )
            else:
                acoes.append(f"ENTROU {nome} {direcao} @ {ind['preco']:.4f}  score:{score}%")
        else:
            acoes.append(f"REJEITADO {nome}: {motivo}")

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
    print(f"\n{SEP}")
    print(f"  ROBOTRADING ESTRATÉGIA v3")
    print(f"  8 activos | Ciclo 15min | Score ≥{THRESHOLD_ENTRADA}% | Tendência obrigatória")
    print(f"  SL/TP por activo | Capital/op=2% | Cooldown={COOLDOWN_MIN}min após SL")
    print(f"  RSI válido: [{RSI_MIN_ENTRADA},{RSI_MAX_ENTRADA}] | Corr threshold: {THRESHOLD_CORRELACAO}%")
    print(f"  Sessões: Londra 07-12 UTC | NY 13-21 UTC | Cripto 24/7")
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
