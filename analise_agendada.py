import yfinance as yf
import anthropic
import requests
import os
import json
import time
import threading
import http.server
import warnings
from datetime import datetime, timezone

import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR           = os.path.dirname(os.path.abspath(__file__))
FICHEIRO_CARTEIRA  = os.path.join(BASE_DIR, "carteira.json")
FICHEIRO_DASHBOARD = os.path.join(BASE_DIR, "dados_dashboard.json")
SALDO_INICIAL      = 10000.0
THRESHOLD_ENTRADA  = 58      # score >= 58 → entra
THRESHOLD_MONITOR  = 45      # score >= 45 → monitoriza
CUSTO_OP           = 0.001   # 0.1% por abertura/fecho
SL_MULT            = 1.5     # stop = 1.5x ATR
TP_MULT            = 3.0     # take profit = 3x ATR
CAPITAL_POR_OP     = 0.02    # 2% saldo por operação
MAX_POSICOES       = 2
PORT               = int(os.environ.get("PORT", 8080))
SEP                = "=" * 60

# ─── ACTIVOS (ordem de prioridade) ───────────────────────────────────────────
ACTIVOS = [
    {
        "nome": "Bitcoin",   "ticker": "BTC-USD", "simbolo": "BTC/USD",
        "perfil": "criptomoeda de alta volatilidade 24/7, sensível a ETFs institucionais e fluxos macro",
        "noticias_termos": ["Bitcoin price", "BTC crypto", "cryptocurrency ETF"],
        "noticias_fallback": [
            "Bitcoin ETF inflows hit new record as institutional demand surges",
            "BTC consolidates above key support after recent rally",
            "Crypto market sentiment turns bullish on Fed pivot expectations",
            "Bitcoin hash rate reaches all-time high signaling network strength",
            "Digital asset adoption accelerates across major financial institutions",
        ],
    },
    {
        "nome": "Ouro",      "ticker": "GC=F",    "simbolo": "XAU/USD",
        "perfil": "metal precioso safe-haven, correlacionado inverso ao dólar e positivo a tensões geopolíticas",
        "noticias_termos": ["gold price", "Federal Reserve", "XAU USD"],
        "noticias_fallback": [
            "Gold holds near record highs as central banks continue buying",
            "Federal Reserve signals cautious approach to rate cuts",
            "Dollar weakens boosting gold appeal as safe haven",
            "Geopolitical tensions drive safe-haven demand for gold",
            "Inflation concerns support gold as store of value",
        ],
    },
    {
        "nome": "Petróleo",  "ticker": "CL=F",    "simbolo": "WTI/USD",
        "perfil": "commodity energética reactiva a OPEP, inventários EUA e tensões geopolíticas",
        "noticias_termos": ["crude oil WTI", "OPEC production", "oil inventory"],
        "noticias_fallback": [
            "OPEC+ maintains production cuts amid global demand concerns",
            "US crude inventories show unexpected weekly drawdown",
            "Oil prices firm as Middle East supply risks persist",
            "Energy demand outlook improves on China recovery signals",
            "WTI crude approaches key technical resistance levels",
        ],
    },
    {
        "nome": "Prata",     "ticker": "SI=F",    "simbolo": "XAG/USD",
        "perfil": "metal precioso industrial, mais volátil que o ouro, forte correlação com ouro",
        "noticias_termos": ["silver price", "precious metals", "silver industrial"],
        "noticias_fallback": [
            "Silver demand surges driven by solar panel manufacturing",
            "Industrial metals rally as global manufacturing PMI improves",
            "Silver outperforms gold in risk-on environment",
            "Green energy transition continues driving silver demand outlook",
            "Silver investment flows increase alongside gold buying",
        ],
    },
    {
        "nome": "S&P 500",   "ticker": "ES=F",    "simbolo": "SPX",
        "perfil": "índice das 500 maiores empresas americanas, barómetro do sentimento global",
        "noticias_termos": ["S&P 500 stocks", "US economy earnings", "Federal Reserve equities"],
        "noticias_fallback": [
            "S&P 500 approaches all-time highs on strong earnings season",
            "Fed pause expectations boost equities broadly",
            "Corporate profit margins expand despite macro headwinds",
            "Risk appetite returns as inflation data cools further",
            "Broad market rally driven by tech and financial sectors",
        ],
    },
    {
        "nome": "Nasdaq",    "ticker": "NQ=F",    "simbolo": "NQ100",
        "perfil": "índice tecnológico, sensível a taxas de juro e earnings de Big Tech",
        "noticias_termos": ["Nasdaq tech stocks", "AI earnings technology", "growth stocks rates"],
        "noticias_fallback": [
            "Nasdaq climbs on blowout earnings from major tech companies",
            "AI infrastructure spending cycle continues to drive valuations",
            "Rate cut expectations boost growth and technology stocks",
            "Big Tech outperforms as cloud revenue beats estimates",
            "Technology sector leads market rally on AI optimism",
        ],
    },
]

# Pares correlacionados — nunca abertos em simultâneo
CORRELACOES = [
    frozenset({"S&P 500", "Nasdaq"}),
    frozenset({"Ouro", "Prata"}),
]


# ─── HTTP SERVER ─────────────────────────────────────────────────────────────
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
        "saldo": SALDO_INICIAL,
        "custos_totais": 0.0,
        "posicoes_abertas": [],
        "historico": [],
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
        # Migrar formato antigo
        if "posicao_aberta" in c:
            pos_antiga = c.pop("posicao_aberta")
            if pos_antiga and "posicoes_abertas" not in c:
                c["posicoes_abertas"] = [pos_antiga]
        c.setdefault("posicoes_abertas", [])
        c.setdefault("custos_totais", 0.0)
        c.setdefault("historico", [])
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
        "total_operacoes": total,
        "ganhas":          gan,
        "perdidas":        per,
        "win_rate":        round(gan / total * 100, 1) if total > 0 else 0.0,
        "lucro_liquido_total": round(lucro, 2),
        "custos_totais":   round(carteira.get("custos_totais", 0), 2),
        "rentabilidade":   round(rent, 2),
    }
    return carteira


def activos_correlacionados(a, b):
    return any(a in grupo and b in grupo for grupo in CORRELACOES)


def verificar_posicoes(carteira, precos):
    """Fecha posições que atingiram SL ou TP."""
    posicoes = carteira["posicoes_abertas"]
    manter   = []
    for pos in posicoes:
        nome  = pos["activo"]
        preco = precos.get(nome)
        if preco is None:
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
                lucro_bruto = (pos["stop_loss"]   - pos["preco_entrada"]) * pos["contratos"]
                preco_fecho = pos["stop_loss"]
                resultado   = "PERDEU"
        elif pos["tipo"] == "VENDER":
            if preco <= pos["take_profit"]:
                lucro_bruto = (pos["preco_entrada"] - pos["take_profit"]) * pos["contratos"]
                preco_fecho = pos["take_profit"]
                resultado   = "GANHOU"
            elif preco >= pos["stop_loss"]:
                lucro_bruto = (pos["preco_entrada"] - pos["stop_loss"])   * pos["contratos"]
                preco_fecho = pos["stop_loss"]
                resultado   = "PERDEU"
        if resultado:
            custo_fecho    = preco_fecho * pos["contratos"] * CUSTO_OP
            lucro_liquido  = lucro_bruto - custo_fecho
            carteira["saldo"]        += lucro_liquido
            carteira["custos_totais"] += custo_fecho
            carteira["historico"].append({
                "activo":        nome,
                "tipo":          pos["tipo"],
                "entrada":       pos["preco_entrada"],
                "saida":         preco_fecho,
                "lucro_bruto":   round(lucro_bruto, 2),
                "custos":        round(pos.get("custo_entrada", 0) + custo_fecho, 4),
                "lucro_liquido": round(lucro_liquido, 2),
                "lucro":         round(lucro_liquido, 2),  # compat dashboard
                "resultado":     resultado,
                "hora_abertura": pos["hora_abertura"],
                "hora_fecho":    datetime.now().strftime("%Y-%m-%d %H:%M"),
                "hora":          datetime.now().strftime("%Y-%m-%d %H:%M"),  # compat
            })
            sinal = "+" if lucro_liquido >= 0 else ""
            print(f"  ✓ FECHADA [{nome}] {resultado} | Bruto:${lucro_bruto:.2f} Custos:${custo_fecho:.2f} Líquido:${sinal}{lucro_liquido:.2f}")
        else:
            manter.append(pos)
    carteira["posicoes_abertas"] = manter
    return carteira


def abrir_posicao(carteira, activo, tipo, preco, stop_loss, take_profit, atr):
    if len(carteira["posicoes_abertas"]) >= MAX_POSICOES:
        return carteira, False, "máximo de posições atingido"
    nomes_abertos = {p["activo"] for p in carteira["posicoes_abertas"]}
    for nome_ab in nomes_abertos:
        if activos_correlacionados(activo, nome_ab):
            return carteira, False, f"correlacionado com {nome_ab}"
    # Calcular SL/TP a partir de ATR se não fornecidos
    if not stop_loss:
        stop_loss  = round(preco - SL_MULT * atr if tipo == "COMPRAR" else preco + SL_MULT * atr, 4)
    if not take_profit:
        take_profit = round(preco + TP_MULT * atr if tipo == "COMPRAR" else preco - TP_MULT * atr, 4)
    # Garantir SL não excede 3% do preço
    sl_pct = abs(preco - stop_loss) / preco
    if sl_pct > 0.03:
        sl_dist    = preco * 0.03
        stop_loss  = round(preco - sl_dist if tipo == "COMPRAR" else preco + sl_dist, 4)
        take_profit = round(preco + 2 * sl_dist if tipo == "COMPRAR" else preco - 2 * sl_dist, 4)
    risco    = abs(preco - stop_loss)
    if risco <= 0:
        return carteira, False, "risco calculado a zero"
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
        "hora_abertura": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "custo_entrada": round(custo_entrada, 4),
    })
    print(f"  ★ ABERTA [{activo}] {tipo} @ {preco:.4f} | SL:{stop_loss:.4f} TP:{take_profit:.4f} | Custo:${custo_entrada:.2f}")
    return carteira, True, f"{tipo} @ {preco:.2f}"


# ─── MERCADO ──────────────────────────────────────────────────────────────────
def sessao_mercado():
    h = datetime.now(timezone.utc).hour
    if 8 <= h < 17:  return "LONDRA"
    if 13 <= h < 22: return "NEW YORK"
    return "ASIA"


def obter_indicadores(ticker):
    dados  = yf.download(ticker, period="90d", interval="1h", progress=False)
    close  = dados["Close"].squeeze()
    high   = dados["High"].squeeze()
    low    = dados["Low"].squeeze()
    volume = dados["Volume"].squeeze()

    preco     = close.iloc[-1].item()
    var_24h   = (preco - close.iloc[-24].item()) / close.iloc[-24].item() * 100

    mm20  = close.rolling(20).mean()
    mm50  = close.rolling(50).mean()
    mm200 = close.rolling(200).mean()

    delta      = close.diff()
    ganho      = delta.where(delta > 0, 0).rolling(14).mean()
    perda      = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi_s      = 100 - (100 / (1 + ganho / perda))
    rsi        = rsi_s.iloc[-1].item()
    rsi_prev   = rsi_s.iloc[-2].item()

    ema12      = close.ewm(span=12).mean()
    ema26      = close.ewm(span=26).mean()
    macd_l     = ema12 - ema26
    macd_sig   = macd_l.ewm(span=9).mean()
    hist_s     = macd_l - macd_sig
    macd_hist      = hist_s.iloc[-1].item()
    macd_hist_prev = hist_s.iloc[-2].item()

    std    = close.rolling(20).std()
    bb_mid = mm20
    bb_up  = (bb_mid + 2 * std).iloc[-1].item()
    bb_dn  = (bb_mid - 2 * std).iloc[-1].item()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()
    atr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean().iloc[-1].item()

    l14     = low.rolling(14).min()
    h14     = high.rolling(14).max()
    stoch_s = 100 * (close - l14) / (h14 - l14)
    stoch_k      = stoch_s.iloc[-1].item()
    stoch_k_prev = stoch_s.iloc[-2].item()
    stoch_d      = stoch_s.rolling(3).mean().iloc[-1].item()

    vol_med   = volume.rolling(20).mean().iloc[-1].item()
    vol_ratio = volume.iloc[-1].item() / vol_med if vol_med > 0 else 1.0

    return {
        "preco": preco, "var_24h": var_24h,
        "mm20": mm20.iloc[-1].item(), "mm50": mm50.iloc[-1].item(), "mm200": mm200.iloc[-1].item(),
        "rsi": rsi, "rsi_prev": rsi_prev,
        "macd": macd_l.iloc[-1].item(), "signal": macd_sig.iloc[-1].item(),
        "macd_hist": macd_hist, "macd_hist_prev": macd_hist_prev,
        "bb_up": bb_up, "bb_mid": bb_mid.iloc[-1].item(), "bb_dn": bb_dn,
        "atr": atr, "stoch_k": stoch_k, "stoch_k_prev": stoch_k_prev, "stoch_d": stoch_d,
        "vol_ratio": vol_ratio, "sessao": sessao_mercado(),
        "max7":  close.tail(168).max().item(), "min7":  close.tail(168).min().item(),
        "max30": close.tail(720).max().item(), "min30": close.tail(720).min().item(),
    }


def obter_noticias(activo_cfg):
    api_key  = os.environ.get("NEWSAPI_KEY")
    noticias = []
    if api_key:
        try:
            headers = {"X-Api-Key": api_key}
            for termo in activo_cfg["noticias_termos"]:
                r = requests.get(
                    f"https://newsapi.org/v2/everything?q={termo}&language=en&sortBy=publishedAt&pageSize=3",
                    headers=headers, timeout=5
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
    return noticias[:7]


# ─── ANÁLISE COM CLAUDE ───────────────────────────────────────────────────────
def analisar(d, noticias, activo_cfg):
    nome    = activo_cfg["nome"]
    simbolo = activo_cfg["simbolo"]
    hora    = datetime.now().strftime("%H:%M de %d/%m/%Y")

    rsi_trend   = "a SUBIR" if d["rsi"]       > d["rsi_prev"]       else "a DESCER"
    hist_trend  = "a SUBIR (bullish)" if d["macd_hist"] > d["macd_hist_prev"] else "a DESCER (bearish)"
    stoch_trend = "a SUBIR" if d["stoch_k"]   > d["stoch_k_prev"]   else "a DESCER"

    sl_buy  = round(d["preco"] - SL_MULT * d["atr"], 4)
    tp_buy  = round(d["preco"] + TP_MULT * d["atr"], 4)
    sl_sell = round(d["preco"] + SL_MULT * d["atr"], 4)
    tp_sell = round(d["preco"] - TP_MULT * d["atr"], 4)

    noticias_texto = "\n".join(f"  {i+1}. {n}" for i, n in enumerate(noticias))

    prompt = f"""És um trader profissional com 30 anos de experiência. Analisa {nome} ({simbolo}) AGORA e calcula o score de entrada.

HORA: {hora} | SESSÃO: {d['sessao']}
PERFIL: {activo_cfg['perfil']}

━━ DADOS TÉCNICOS ━━
Preço:    {d['preco']:.4f} | Var 24h: {d['var_24h']:+.2f}%
MM20/50/200: {d['mm20']:.4f} / {d['mm50']:.4f} / {d['mm200']:.4f}
RSI(14):  {d['rsi']:.1f} {rsi_trend} (anterior: {d['rsi_prev']:.1f})
MACD Hist: {d['macd_hist']:.6f} {hist_trend} (anterior: {d['macd_hist_prev']:.6f})
Bollinger: {d['bb_dn']:.4f} / {d['bb_mid']:.4f} / {d['bb_up']:.4f}
Stoch K:  {d['stoch_k']:.1f} {stoch_trend} (anterior: {d['stoch_k_prev']:.1f}) | D: {d['stoch_d']:.1f}
Volume:   {d['vol_ratio']:.2f}x média 20h
ATR(14):  {d['atr']:.4f}

━━ NOTÍCIAS RECENTES ━━
{noticias_texto}

━━ SISTEMA DE SCORE (máx 130 pts → ÷1.3 = 0-100) ━━

COMPRAR — pontua se:
  RSI < 45 → +20 pts | RSI 45-55 a SUBIR → +10 pts
  MACD histograma positivo OU a SUBIR → +20 pts
  Preço > MM20 ({d['mm20']:.4f}) → +15 pts
  Preço ≤ Bollinger inferior ({d['bb_dn']:.4f}) → +20 pts
  Stoch K < 30 e a SUBIR → +15 pts
  Volume ≥ 0.8x média → +10 pts
  Notícias POSITIVAS para {nome} → +20 pts
  Sessão LONDRA ou NEW YORK → +10 pts

VENDER — pontua se:
  RSI > 55 → +20 pts | RSI 45-55 a DESCER → +10 pts
  MACD histograma negativo OU a DESCER → +20 pts
  Preço < MM20 ({d['mm20']:.4f}) → +15 pts
  Preço ≥ Bollinger superior ({d['bb_up']:.4f}) → +20 pts
  Stoch K > 70 e a DESCER → +15 pts
  Volume ≥ 0.8x média → +10 pts
  Notícias NEGATIVAS para {nome} → +20 pts
  Sessão LONDRA ou NEW YORK → +10 pts

DECISÃO: score_compra ≥ 58 → COMPRAR | score_venda ≥ 58 → VENDER | ambos < 58 → AGUARDAR
Se ambos ≥ 58: usa o maior.

Stop Loss COMPRAR:  {sl_buy:.4f} (1.5×ATR abaixo)
Take Profit COMPRAR: {tp_buy:.4f} (3×ATR acima)
Stop Loss VENDER:   {sl_sell:.4f} (1.5×ATR acima)
Take Profit VENDER: {tp_sell:.4f} (3×ATR abaixo)

Responde APENAS com JSON válido sem texto extra, sem markdown, sem backticks:
{{"decisao":"COMPRAR ou VENDER ou AGUARDAR","score":75,"confianca":75,"stop_loss":null,"take_profit":null,"raciocinio":"3 frases directas e precisas","factores_positivos":["f1","f2"],"factores_negativos":["f1","f2"],"sentimento_noticias":"POSITIVO ou NEGATIVO ou NEUTRO"}}"""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    resp   = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = resp.content[0].text.strip()
    if "```" in texto:
        texto = texto.split("```")[1]
        if texto.startswith("json"):
            texto = texto[4:]
    return json.loads(texto.strip())


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
        # Novo formato
        "saldo":              carteira["saldo"],
        "custos_totais":      carteira.get("custos_totais", 0),
        "posicoes_abertas":   posicoes,
        "estatisticas":       stats,
        "historico":          carteira.get("historico", []),
        # Compat com dashboard existente
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
    hora = datetime.now().strftime("%H:%M")
    print(f"\n{SEP}")
    print(f"=== CICLO 30MIN — {hora} ===")
    print(SEP)

    carteira       = carregar_carteira()
    precos_atuais  = {}
    resultados     = []    # [(activo_cfg, d, r)]
    analises_novos = []

    for activo_cfg in ACTIVOS:
        nome   = activo_cfg["nome"]
        ticker = activo_cfg["ticker"]
        print(f"[{nome:<10}] ", end="", flush=True)
        try:
            d = obter_indicadores(ticker)
            precos_atuais[nome] = d["preco"]
        except Exception as e:
            print(f"ERRO indicadores: {e}")
            continue

        noticias = obter_noticias(activo_cfg)

        try:
            r = analisar(d, noticias, activo_cfg)
        except Exception as e:
            print(f"ERRO análise: {e}")
            continue

        score   = r.get("score", 0)
        decisao = r.get("decisao", "AGUARDAR")
        flag    = " ★" if decisao != "AGUARDAR" and score >= THRESHOLD_ENTRADA else \
                  " ○" if score >= THRESHOLD_MONITOR else ""

        print(f"${d['preco']:<12.4f} RSI:{d['rsi']:.0f}  Score:{score:>3}%  {decisao}{flag}")

        resultados.append((activo_cfg, d, r))
        analises_novos.append({
            "hora":                datetime.now().strftime("%Y-%m-%d %H:%M"),
            "activo":              nome,
            "simbolo":             activo_cfg["simbolo"],
            "preco":               round(d["preco"], 4),
            "rsi":                 round(d["rsi"], 1),
            "decisao":             decisao,
            "score":               score,
            "confianca":           r.get("confianca", score),
            "risco":               "ALTO" if score >= 70 else "MEDIO" if score >= 50 else "BAIXO",
            "stop_loss":           r.get("stop_loss"),
            "take_profit":         r.get("take_profit"),
            "raciocinio":          r.get("raciocinio", ""),
            "sentimento_noticias": r.get("sentimento_noticias", "NEUTRO"),
            "factores_positivos":  r.get("factores_positivos", []),
            "factores_negativos":  r.get("factores_negativos", []),
        })

    # Verificar posições abertas
    if precos_atuais:
        carteira = verificar_posicoes(carteira, precos_atuais)

    # Seleccionar melhores oportunidades
    oportunidades = sorted(
        [(cfg, d, r) for cfg, d, r in resultados
         if r.get("score", 0) >= THRESHOLD_ENTRADA and r.get("decisao") in ("COMPRAR", "VENDER")],
        key=lambda x: x[2].get("score", 0), reverse=True
    )

    nomes_posicoes = {p["activo"] for p in carteira["posicoes_abertas"]}
    acoes = []

    for cfg, d, r in oportunidades:
        nome = cfg["nome"]
        if nome in nomes_posicoes:
            continue
        if len(carteira["posicoes_abertas"]) >= MAX_POSICOES:
            break
        corr = next((n for n in nomes_posicoes if activos_correlacionados(nome, n)), None)
        if corr:
            print(f"  ⚠  {nome} correlacionado com {corr} — ignorado")
            continue
        carteira, aberta, motivo = abrir_posicao(
            carteira, nome, r["decisao"], d["preco"],
            r.get("stop_loss"), r.get("take_profit"), d["atr"]
        )
        if aberta:
            nomes_posicoes.add(nome)
            acoes.append(f"ENTROU {nome} {r['decisao']} @ {d['preco']:.4f}  score:{r.get('score')}%")
        else:
            acoes.append(f"REJEITADO {nome}: {motivo}")

    if not acoes:
        max_s = max((r.get("score", 0) for _, _, r in resultados), default=0)
        acoes.append(f"AGUARDOU — score máx {max_s}% < {THRESHOLD_ENTRADA}%" if max_s < THRESHOLD_ENTRADA
                     else f"AGUARDOU — posições ocupadas ou correlação")

    # Log resumo
    print()
    if oportunidades:
        best = oportunidades[0]
        print(f"[MELHOR SETUP] {best[0]['nome']}  score:{best[2].get('score')}%  {best[2].get('decisao')}")
    else:
        print(f"[MELHOR SETUP] Nenhum com score ≥ {THRESHOLD_ENTRADA}%")
    for a in acoes:
        print(f"[ACÇÃO]        {a}")

    atualizar_estatisticas(carteira)
    stats = carteira["estatisticas"]
    pos_str = ", ".join(f"{p['activo']} {p['tipo']}" for p in carteira["posicoes_abertas"]) or "—"
    print(f"[CARTEIRA]     ${carteira['saldo']:.2f} ({stats['rentabilidade']:+.2f}%) | "
          f"Posições: {pos_str} | Lucro líquido: ${stats['lucro_liquido_total']:.2f} | "
          f"Custos: ${stats['custos_totais']:.2f}")
    print(SEP)

    guardar_carteira(carteira)
    atualizar_dashboard(carteira, analises_novos)
    print(f"  → {len(analises_novos)} análises guardadas em dados_dashboard.json\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    print(f"\n{SEP}")
    print(f"  ROBOTRADING AGENTE ASSERTIVO v2")
    print(f"  6 activos | Ciclo 30min | Threshold {THRESHOLD_ENTRADA}% | HTTP porta {PORT}")
    print(f"  Bitcoin primeiro — {' | '.join(a['nome'] for a in ACTIVOS)}")
    print(f"{SEP}\n")
    while True:
        try:
            executar_ciclo()
            print(f"  Próximo ciclo em 30 minutos ({datetime.now().strftime('%H:%M')})\n")
            time.sleep(1800)
        except KeyboardInterrupt:
            print("\nRobo parado.")
            break
        except Exception as e:
            print(f"\n[ERRO CICLO] {e}")
            print("  A tentar novamente em 5 minutos...\n")
            time.sleep(300)


if __name__ == "__main__":
    main()
