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
THRESHOLD          = 65          # confiança mínima para abrir posição
SEP                = "=" * 60
PORT               = int(os.environ.get("PORT", 8080))

# ─── ACTIVOS ──────────────────────────────────────────────────────────────────
ACTIVOS = [
    {
        "nome": "Ouro", "ticker": "GC=F", "simbolo": "XAU/USD",
        "perfil": "metal precioso safe-haven com forte correlação a Fed, dólar e geopolítica",
        "noticias_termos": ["gold price", "Federal Reserve", "XAU USD"],
        "noticias_fallback": [
            "Gold holds near record highs amid global uncertainty",
            "Federal Reserve signals cautious approach to rate cuts",
            "Dollar weakens as inflation data comes in mixed",
            "Geopolitical tensions boost safe-haven demand for gold",
            "Central banks continue record gold purchases in 2026",
        ],
    },
    {
        "nome": "Prata", "ticker": "SI=F", "simbolo": "XAG/USD",
        "perfil": "metal precioso com forte componente industrial, mais volátil que o Ouro",
        "noticias_termos": ["silver price", "precious metals", "industrial demand silver"],
        "noticias_fallback": [
            "Silver demand surges on solar panel manufacturing growth",
            "Industrial metals rally as global manufacturing recovers",
            "Silver outperforms gold in risk-on environment",
            "Green energy transition drives silver demand outlook",
            "Silver investment demand increases alongside gold buying",
        ],
    },
    {
        "nome": "Petróleo", "ticker": "CL=F", "simbolo": "WTI/USD",
        "perfil": "commodity energética sensível a OPEP, inventários EUA e geopolítica",
        "noticias_termos": ["crude oil price", "OPEC production", "WTI oil"],
        "noticias_fallback": [
            "OPEC maintains production cuts amid demand concerns",
            "US crude inventories show unexpected drawdown",
            "Oil prices steady as Middle East tensions persist",
            "Energy demand outlook clouded by China slowdown",
            "WTI crude trades near key support levels",
        ],
    },
    {
        "nome": "S&P 500", "ticker": "ES=F", "simbolo": "SPX",
        "perfil": "índice das 500 maiores empresas americanas, barómetro da economia global",
        "noticias_termos": ["S&P 500 stock market", "Federal Reserve rates equities", "US economy earnings"],
        "noticias_fallback": [
            "S&P 500 approaches all-time highs on earnings optimism",
            "Fed signals pause in rate hikes boosting equities",
            "Tech sector leads broader market rally higher",
            "Corporate earnings beat expectations across sectors",
            "Risk appetite returns as inflation pressures ease",
        ],
    },
    {
        "nome": "Bitcoin", "ticker": "BTC-USD", "simbolo": "BTC/USD",
        "perfil": "criptomoeda muito volátil com mercado 24/7, sensível a ETFs e regulação",
        "noticias_termos": ["Bitcoin price crypto", "BTC ETF", "cryptocurrency market"],
        "noticias_fallback": [
            "Bitcoin consolidates above key support after recent rally",
            "Institutional Bitcoin adoption continues to grow strongly",
            "Crypto market shows resilience amid regulatory clarity",
            "Bitcoin ETF inflows remain strong in current cycle",
            "Digital asset market matures as volatility decreases",
        ],
    },
    {
        "nome": "Nasdaq", "ticker": "NQ=F", "simbolo": "NQ100",
        "perfil": "índice tecnológico americano, muito sensível a taxas de juro e earnings tech",
        "noticias_termos": ["Nasdaq technology stocks", "AI earnings tech", "growth stocks rates"],
        "noticias_fallback": [
            "Nasdaq climbs on strong tech sector earnings beat",
            "AI boom continues to drive technology valuations higher",
            "Rate cut expectations boost growth stock outlook",
            "Big Tech leads Nasdaq to new highs on AI demand",
            "Technology sector resilient despite macro headwinds",
        ],
    },
]


# ─── SERVIDOR HTTP ────────────────────────────────────────────────────────────

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
                self.send_error(404, "Não encontrado")
                return
            nome_ficheiro, content_type = self.ROTAS[path]
            ficheiro = os.path.join(BASE_DIR, nome_ficheiro)
            try:
                with open(ficheiro, "rb") as f:
                    corpo = f.read()
            except FileNotFoundError:
                self.send_error(404, f"{nome_ficheiro} não encontrado")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache")
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, fmt, *args):
            pass

    servidor = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[HTTP] Dashboard disponível na porta {PORT}")
    servidor.serve_forever()


# ─── DASHBOARD JSON ───────────────────────────────────────────────────────────

def atualizar_dashboard(carteira, novas_analises=None):
    dados = {"carteira": {}, "analises": [], "posicao_aberta": None}
    if os.path.exists(FICHEIRO_DASHBOARD):
        try:
            with open(FICHEIRO_DASHBOARD, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass

    saldo    = carteira.get("saldo", SALDO_INICIAL)
    total    = carteira.get("total_operacoes", 0)
    ganhas   = carteira.get("operacoes_ganhas", 0)
    perdidas = carteira.get("operacoes_perdidas", 0)

    dados["carteira"] = {
        "saldo":              saldo,
        "rentabilidade":      round((saldo - SALDO_INICIAL) / SALDO_INICIAL * 100, 2),
        "total_operacoes":    total,
        "operacoes_ganhas":   ganhas,
        "operacoes_perdidas": perdidas,
        "lucro_total":        carteira.get("lucro_total", 0.0),
        "win_rate":           round(ganhas / total * 100, 1) if total > 0 else 0,
        "historico":          carteira.get("historico", []),
    }
    dados["posicao_aberta"] = carteira.get("posicao_aberta")

    if novas_analises:
        dados["analises"].extend(novas_analises)
        dados["analises"] = dados["analises"][-500:]

    with open(FICHEIRO_DASHBOARD, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ─── FUNÇÕES DE MERCADO ───────────────────────────────────────────────────────

def sessao_mercado():
    h = datetime.now(timezone.utc).hour
    if 8 <= h < 17:   return "LONDRA"
    if 13 <= h < 22:  return "NEW YORK"
    return "ASIA"


def obter_indicadores(ticker):
    dados = yf.download(ticker, period="90d", interval="1h", progress=False)

    close  = dados["Close"].squeeze()
    high   = dados["High"].squeeze()
    low    = dados["Low"].squeeze()
    volume = dados["Volume"].squeeze()

    preco     = close.iloc[-1].item()
    preco_24h = close.iloc[-24].item()
    var_24h   = ((preco - preco_24h) / preco_24h) * 100

    mm20  = close.rolling(20).mean().iloc[-1].item()
    mm50  = close.rolling(50).mean().iloc[-1].item()
    mm200 = close.rolling(200).mean().iloc[-1].item()

    delta = close.diff()
    ganho = delta.where(delta > 0, 0).rolling(14).mean()
    perda = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi   = (100 - (100 / (1 + ganho / perda))).iloc[-1].item()

    ema12       = close.ewm(span=12).mean()
    ema26       = close.ewm(span=26).mean()
    macd_linha  = ema12 - ema26
    macd_signal = macd_linha.ewm(span=9).mean()
    macd        = macd_linha.iloc[-1].item()
    signal      = macd_signal.iloc[-1].item()
    macd_hist   = macd - signal

    std    = close.rolling(20).std()
    bb_up  = (close.rolling(20).mean() + 2 * std).iloc[-1].item()
    bb_dn  = (close.rolling(20).mean() - 2 * std).iloc[-1].item()
    bb_mid = close.rolling(20).mean().iloc[-1].item()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()
    atr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(14).mean().iloc[-1].item()

    low14   = low.rolling(14).min()
    high14  = high.rolling(14).max()
    stoch_k = (100 * (close - low14) / (high14 - low14)).iloc[-1].item()
    stoch_d = (100 * (close - low14) / (high14 - low14)).rolling(3).mean().iloc[-1].item()

    vol_media = volume.rolling(20).mean().iloc[-1].item()
    vol_ratio = volume.iloc[-1].item() / vol_media if vol_media > 0 else 1.0

    return {
        "preco": preco, "var_24h": var_24h,
        "mm20": mm20, "mm50": mm50, "mm200": mm200,
        "rsi": rsi, "macd": macd, "signal": signal, "macd_hist": macd_hist,
        "bb_up": bb_up, "bb_mid": bb_mid, "bb_dn": bb_dn,
        "atr": atr, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "vol_ratio": vol_ratio, "sessao": sessao_mercado(),
        "max30": close.tail(720).max().item(), "min30": close.tail(720).min().item(),
        "max7":  close.tail(168).max().item(), "min7":  close.tail(168).min().item(),
    }


def obter_noticias(activo_cfg):
    api_key  = os.environ.get("NEWSAPI_KEY")
    noticias = []
    if api_key:
        try:
            headers = {"X-Api-Key": api_key}
            for termo in activo_cfg["noticias_termos"]:
                url = f"https://newsapi.org/v2/everything?q={termo}&language=en&sortBy=publishedAt&pageSize=3"
                r   = requests.get(url, headers=headers, timeout=5)
                if r.status_code == 200:
                    for a in r.json().get("articles", []):
                        t = a.get("title", "")
                        if t and t not in noticias and len(t) > 10:
                            noticias.append(t)
        except Exception:
            pass
    if len(noticias) < 5:
        noticias += activo_cfg["noticias_fallback"]
    return noticias[:8]


def analisar(d, noticias, activo_cfg):
    nome   = activo_cfg["nome"]
    simbolo = activo_cfg["simbolo"]
    perfil  = activo_cfg["perfil"]
    noticias_texto = "\n".join([f"- {n}" for n in noticias])
    hora = datetime.now().strftime("%H:%M de %d/%m/%Y")

    prompt = f"""Es um trader profissional com 30 anos de experiencia em {nome} ({simbolo}).
Perfil do activo: {perfil}.
Es extremamente conservador. So recomendas entradas com confianca acima de {THRESHOLD}%.
Preferes perder uma oportunidade a entrar numa operacao duvidosa.
Ratio minimo Risk/Reward: 1:2. Maximo 2% do capital por operacao.

HORA: {hora}
SESSAO DE MERCADO: {d['sessao']}

DADOS TECNICOS DE {simbolo}:
- Preco atual: {d['preco']:.2f}
- Variacao 24h: {d['var_24h']:.2f}%
- MM20: {d['mm20']:.2f} | MM50: {d['mm50']:.2f} | MM200: {d['mm200']:.2f}
- RSI(14): {d['rsi']:.1f}
- MACD: {d['macd']:.2f} | Signal: {d['signal']:.2f} | Histograma: {d['macd_hist']:.2f}
- Bollinger: {d['bb_dn']:.2f} / {d['bb_mid']:.2f} / {d['bb_up']:.2f}
- ATR(14): {d['atr']:.2f}
- Stochastic K: {d['stoch_k']:.1f} | D: {d['stoch_d']:.1f}
- Volume ratio vs media 20h: {d['vol_ratio']:.2f}x
- Range 7d: {d['min7']:.2f} - {d['max7']:.2f}
- Range 30d: {d['min30']:.2f} - {d['max30']:.2f}

NOTICIAS RELEVANTES:
{noticias_texto}

Responde APENAS com JSON valido sem texto extra, sem markdown, sem backticks:
{{"decisao":"COMPRAR ou VENDER ou AGUARDAR","confianca":50,"raciocinio":"4-5 frases em portugues","stop_loss":null,"take_profit":null,"risco":"BAIXO ou MEDIO ou ALTO","zona":"zona de preco a vigiar","factores_positivos":["fator1","fator2"],"factores_negativos":["fator1","fator2"]}}"""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = response.content[0].text.strip()
    if "```" in texto:
        texto = texto.split("```")[1]
        if texto.startswith("json"):
            texto = texto[4:]
    return json.loads(texto.strip())


# ─── CARTEIRA ─────────────────────────────────────────────────────────────────

def carregar_carteira():
    if os.path.exists(FICHEIRO_CARTEIRA):
        with open(FICHEIRO_CARTEIRA, "r") as f:
            return json.load(f)
    return {
        "saldo": SALDO_INICIAL, "posicao_aberta": None, "historico": [],
        "total_operacoes": 0, "operacoes_ganhas": 0,
        "operacoes_perdidas": 0, "lucro_total": 0.0,
    }


def guardar_carteira(carteira):
    with open(FICHEIRO_CARTEIRA, "w") as f:
        json.dump(carteira, f, ensure_ascii=False, indent=2)


def verificar_posicao(carteira, preco):
    pos = carteira["posicao_aberta"]
    if not pos:
        return carteira
    resultado = None
    lucro = 0.0
    if pos["tipo"] == "COMPRAR":
        if preco >= pos["take_profit"]:
            lucro = (pos["take_profit"] - pos["preco_entrada"]) * pos["contratos"]
            resultado = "GANHOU"
        elif preco <= pos["stop_loss"]:
            lucro = (pos["stop_loss"] - pos["preco_entrada"]) * pos["contratos"]
            resultado = "PERDEU"
    elif pos["tipo"] == "VENDER":
        if preco <= pos["take_profit"]:
            lucro = (pos["preco_entrada"] - pos["take_profit"]) * pos["contratos"]
            resultado = "GANHOU"
        elif preco >= pos["stop_loss"]:
            lucro = (pos["preco_entrada"] - pos["stop_loss"]) * pos["contratos"]
            resultado = "PERDEU"
    if resultado:
        carteira["saldo"]           += lucro
        carteira["lucro_total"]     += lucro
        carteira["total_operacoes"] += 1
        if resultado == "GANHOU":
            carteira["operacoes_ganhas"]  += 1
        else:
            carteira["operacoes_perdidas"] += 1
        carteira["historico"].append({
            "hora":      datetime.now().strftime("%Y-%m-%d %H:%M"),
            "activo":    pos.get("activo", "—"),
            "tipo":      pos["tipo"],
            "entrada":   pos["preco_entrada"],
            "saida":     preco,
            "lucro":     round(lucro, 2),
            "resultado": resultado,
        })
        carteira["posicao_aberta"] = None
        sinal = "+" if lucro >= 0 else ""
        print(f"  OPERACAO FECHADA [{pos.get('activo','?')}]: {resultado} | Lucro: ${sinal}{lucro:.2f}")
    return carteira


def abrir_posicao(carteira, decisao, preco, stop_loss, take_profit, activo="Ouro"):
    if not stop_loss or not take_profit:
        return carteira
    risco = abs(preco - stop_loss)
    if risco <= 0:
        return carteira
    capital_risco = carteira["saldo"] * 0.02
    contratos     = round(capital_risco / risco, 4)
    carteira["posicao_aberta"] = {
        "activo":          activo,
        "tipo":            decisao,
        "preco_entrada":   preco,
        "stop_loss":       stop_loss,
        "take_profit":     take_profit,
        "contratos":       contratos,
        "hora_abertura":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    print(f"  POSICAO ABERTA [{activo}]: {decisao} @ {preco:.2f} | SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
    return carteira


# ─── ANÁLISE MULTI-ACTIVO ─────────────────────────────────────────────────────

def executar_analise():
    hora = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n{SEP}")
    print(f"  ANALISE MULTI-ACTIVO  |  {hora}")
    print(f"  {' | '.join(a['nome'] for a in ACTIVOS)}")
    print(SEP)

    carteira    = carregar_carteira()
    resultados  = []   # [(activo_cfg, d, r)]
    analises_novos = []

    for activo_cfg in ACTIVOS:
        nome   = activo_cfg["nome"]
        ticker = activo_cfg["ticker"]

        print(f"\n[{nome}] A obter indicadores ({ticker})...")
        try:
            d = obter_indicadores(ticker)
        except Exception as e:
            print(f"[{nome}] ERRO indicadores: {e}")
            continue

        # Verificar posição aberta deste activo
        pos = carteira.get("posicao_aberta")
        if pos and pos.get("activo") == nome:
            carteira = verificar_posicao(carteira, d["preco"])

        print(f"[{nome}] A obter noticias...")
        noticias = obter_noticias(activo_cfg)

        print(f"[{nome}] A consultar Claude Haiku...")
        try:
            r = analisar(d, noticias, activo_cfg)
        except Exception as e:
            print(f"[{nome}] ERRO analise: {e}")
            continue

        decisao   = r.get("decisao", "AGUARDAR")
        confianca = r.get("confianca", 0)
        risco     = r.get("risco", "N/A")
        raciocinio = r.get("raciocinio", "N/A")

        print(f"[{nome}] {decisao:8}  Confianca: {confianca}%  Risco: {risco}  Preco: {d['preco']:.2f}")

        resultados.append((activo_cfg, d, r))
        analises_novos.append({
            "hora":       hora,
            "activo":     nome,
            "simbolo":    activo_cfg["simbolo"],
            "preco":      round(d["preco"], 2),
            "rsi":        round(d["rsi"], 1),
            "decisao":    decisao,
            "confianca":  confianca,
            "risco":      risco,
            "raciocinio": raciocinio,
        })

    # ── Seleccionar melhor oportunidade ──────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  RESUMO DA SESSAO")
    print(f"{'─'*60}")
    for activo_cfg, d, r in resultados:
        flag = "★" if r.get("confianca", 0) >= THRESHOLD and r.get("decisao") in ["COMPRAR","VENDER"] else " "
        print(f"  {flag} {activo_cfg['nome']:12}  ${d['preco']:>10.2f}  {r.get('decisao','?'):8}  {r.get('confianca',0)}%")

    oportunidades = [
        (cfg, d, r) for cfg, d, r in resultados
        if r.get("confianca", 0) >= THRESHOLD and r.get("decisao") in ["COMPRAR", "VENDER"]
    ]
    oportunidades.sort(key=lambda x: x[2].get("confianca", 0), reverse=True)

    if oportunidades and not carteira.get("posicao_aberta"):
        melhor_cfg, melhor_d, melhor_r = oportunidades[0]
        print(f"\n  ★ MELHOR OPORTUNIDADE: {melhor_cfg['nome']} | "
              f"{melhor_r.get('decisao')} | {melhor_r.get('confianca')}%")
        carteira = abrir_posicao(
            carteira,
            melhor_r.get("decisao"),
            melhor_d["preco"],
            melhor_r.get("stop_loss"),
            melhor_r.get("take_profit"),
            melhor_cfg["nome"],
        )
    elif not carteira.get("posicao_aberta"):
        max_conf = max((r.get("confianca", 0) for _, _, r in resultados), default=0)
        print(f"\n  MODO CONSERVADOR — Confianca maxima {max_conf}% abaixo de {THRESHOLD}%. A aguardar.")
    else:
        pos = carteira["posicao_aberta"]
        print(f"\n  Posicao existente [{pos.get('activo')}] {pos['tipo']} @ {pos['preco_entrada']:.2f} — sem nova entrada.")

    rentabilidade = ((carteira["saldo"] - SALDO_INICIAL) / SALDO_INICIAL) * 100
    win_rate = (
        (carteira["operacoes_ganhas"] / carteira["total_operacoes"] * 100)
        if carteira["total_operacoes"] > 0 else 0
    )
    print(f"\n  Saldo:     ${carteira['saldo']:.2f}  ({rentabilidade:+.2f}%)")
    print(f"  Lucro:     ${carteira['lucro_total']:+.2f}")
    print(f"  Operacoes: {carteira['total_operacoes']}  (W:{carteira['operacoes_ganhas']} / L:{carteira['operacoes_perdidas']} / {win_rate:.0f}% WR)")
    print(f"\n{SEP}\n")

    guardar_carteira(carteira)
    atualizar_dashboard(carteira, analises_novos)
    print(f"[dashboard] {len(analises_novos)} análises guardadas em dados_dashboard.json.")


# ─── LOOP PRINCIPAL ───────────────────────────────────────────────────────────

def main():
    t = threading.Thread(target=iniciar_servidor_http, daemon=True)
    t.start()

    print(f"\n{SEP}")
    print(f"  ROBOTRADING MULTI-ACTIVO — A INICIAR")
    print(f"  6 mercados | Threshold {THRESHOLD}% | Servidor HTTP porta {PORT}")
    print(f"{SEP}\n")

    while True:
        try:
            executar_analise()
            proxima = datetime.now().strftime("%H:%M")
            print(f"  Proxima analise em 1 hora. Ultima: {proxima}")
            print("  A aguardar...\n")
            time.sleep(3600)
        except KeyboardInterrupt:
            print("\nRobo parado pelo utilizador.")
            break
        except Exception as e:
            print(f"\n[ERRO] {e}")
            print("  A tentar novamente em 5 minutos...\n")
            time.sleep(300)


if __name__ == "__main__":
    main()
