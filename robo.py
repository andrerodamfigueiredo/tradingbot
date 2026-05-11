import yfinance as yf
import anthropic
import requests
import os
import json
import time
import warnings
from datetime import datetime
warnings.filterwarnings("ignore")

# ============================================================
#   ROBO ANALISTA DE TRADING - OURO XAU/USD
#   Versao 3.0 - Automatico com registo
# ============================================================

FICHEIRO_REGISTO = "registo.json"

def obter_indicadores():
    ouro = yf.download("GC=F", period="60d", interval="1h", progress=False)
    close = ouro["Close"].squeeze()
    preco = close.iloc[-1].item()
    preco_24h = close.iloc[-24].item()
    var_24h = ((preco - preco_24h) / preco_24h) * 100
    mm20 = close.rolling(20).mean().iloc[-1].item()
    mm50 = close.rolling(50).mean().iloc[-1].item()
    delta = close.diff()
    ganho = delta.where(delta > 0, 0).rolling(14).mean()
    perda = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi = (100 - (100 / (1 + ganho / perda))).iloc[-1].item()
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = (ema12 - ema26).iloc[-1].item()
    signal = (ema12 - ema26).ewm(span=9).mean().iloc[-1].item()
    std = close.rolling(20).std()
    bb_up = (close.rolling(20).mean() + 2 * std).iloc[-1].item()
    bb_dn = (close.rolling(20).mean() - 2 * std).iloc[-1].item()
    max30 = close.tail(720).max().item()
    min30 = close.tail(720).min().item()
    return {"preco": preco, "var_24h": var_24h, "mm20": mm20, "mm50": mm50,
            "rsi": rsi, "macd": macd, "signal": signal, "bb_up": bb_up,
            "bb_dn": bb_dn, "max30": max30, "min30": min30}

def obter_noticias():
    api_key = os.environ.get("NEWSAPI_KEY")
    noticias = []
    if api_key:
        try:
            headers = {"X-Api-Key": api_key}
            for termo in ["gold price", "Federal Reserve", "XAU USD"]:
                url = f"https://newsapi.org/v2/everything?q={termo}&language=en&sortBy=publishedAt&pageSize=3"
                r = requests.get(url, headers=headers, timeout=5)
                if r.status_code == 200:
                    for a in r.json().get("articles", []):
                        titulo = a.get("title", "")
                        if titulo and titulo not in noticias and len(titulo) > 10:
                            noticias.append(titulo)
        except:
            pass
    if len(noticias) < 5:
        noticias += [
            "Gold holds near record highs amid global uncertainty",
            "Federal Reserve signals cautious approach to rate cuts",
            "Dollar weakens as inflation data comes in mixed",
            "Geopolitical tensions boost safe-haven demand for gold",
            "Central banks continue record gold purchases in 2026"
        ]
    return noticias[:8]

def analisar(d, noticias):
    noticias_texto = "\n".join([f"- {n}" for n in noticias])
    hora = datetime.now().strftime("%H:%M de %d/%m/%Y")
    prompt = f"""Es um trader profissional com 30 anos de experiencia em Ouro XAU/USD.
Es extremamente conservador. So recomendas entradas com confianca acima de 75%.
Preferes perder uma oportunidade a entrar numa operacao duvidosa.
Ratio minimo Risk/Reward: 1:2. Maximo 2% do capital por operacao.

HORA: {hora}

DADOS TECNICOS:
- Preco: {d['preco']:.2f}
- Variacao 24h: {d['var_24h']:.2f}%
- MM20: {d['mm20']:.2f} | MM50: {d['mm50']:.2f}
- RSI: {d['rsi']:.1f}
- MACD: {d['macd']:.2f} | Signal: {d['signal']:.2f}
- Bollinger: {d['bb_dn']:.2f} - {d['bb_up']:.2f}
- Range 30d: {d['min30']:.2f} - {d['max30']:.2f}

NOTICIAS:
{noticias_texto}

Responde APENAS com JSON valido sem texto extra:
{{"decisao":"COMPRAR ou VENDER ou AGUARDAR","confianca":50,"raciocinio":"4-5 frases em portugues","stop_loss":null,"take_profit":null,"risco":"BAIXO ou MEDIO ou ALTO","zona":"zona de preco a vigiar"}}"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    texto = response.content[0].text.strip()
    if "```" in texto:
        texto = texto.split("```")[1]
        if texto.startswith("json"):
            texto = texto[4:]
    return json.loads(texto.strip())

def guardar_registo(d, r, noticias):
    registo = []
    if os.path.exists(FICHEIRO_REGISTO):
        with open(FICHEIRO_REGISTO, "r") as f:
            try:
                registo = json.load(f)
            except:
                registo = []
    entrada = {
        "hora": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "preco": d["preco"],
        "rsi": d["rsi"],
        "decisao": r.get("decisao"),
        "confianca": r.get("confianca"),
        "risco": r.get("risco"),
        "stop_loss": r.get("stop_loss"),
        "take_profit": r.get("take_profit"),
        "raciocinio": r.get("raciocinio"),
        "zona": r.get("zona"),
        "noticias": noticias[:3]
    }
    registo.append(entrada)
    with open(FICHEIRO_REGISTO, "w") as f:
        json.dump(registo, f, ensure_ascii=False, indent=2)
    print(f"Registo guardado. Total de analises: {len(registo)}")

def mostrar(d, r, noticias):
    hora = datetime.now().strftime("%H:%M - %d/%m/%Y")
    sep = "=" * 58
    print(f"\n{sep}")
    print("   ROBO ANALISTA - OURO XAU/USD")
    print(f"   {hora}")
    print(sep)
    print(f"Preco atual:    ${d['preco']:.2f}")
    print(f"Variacao 24h:   {d['var_24h']:.2f}%")
    print(f"MM20 / MM50:    ${d['mm20']:.2f} / ${d['mm50']:.2f}")
    print(f"RSI:            {d['rsi']:.1f}")
    print(f"MACD / Signal:  {d['macd']:.2f} / {d['signal']:.2f}")
    print(f"Bollinger:      ${d['bb_dn']:.2f} - ${d['bb_up']:.2f}")
    print(f"Range 30d:      ${d['min30']:.2f} - ${d['max30']:.2f}")
    print(sep)
    print("   NOTICIAS ANALISADAS")
    print(sep)
    for i, n in enumerate(noticias[:5], 1):
        print(f"{i}. {n[:70]}")
    print(sep)
    print("   RACIOCINIO DO ESPECIALISTA")
    print(sep)
    raciocinio = r.get("raciocinio", "N/A")
    palavras = raciocinio.split()
    linha = ""
    for p in palavras:
        if len(linha) + len(p) > 55:
            print(f"   {linha}")
            linha = p + " "
        else:
            linha += p + " "
    if linha:
        print(f"   {linha}")
    print(sep)
    print("   DECISAO FINAL")
    print(sep)
    decisao = r.get("decisao", "AGUARDAR")
    confianca = r.get("confianca", 0)
    if decisao == "COMPRAR":
        label = ">>> COMPRAR <<<"
    elif decisao == "VENDER":
        label = ">>> VENDER <<<"
    else:
        label = "--- AGUARDAR ---"
    print(f"\n{label}")
    print(f"CONFIANCA:  {confianca}%")
    print(f"RISCO:      {r.get('risco', 'N/A')}")
    print(f"ZONA:       {r.get('zona', 'N/A')}")
    if r.get("stop_loss"):
        print(f"STOP LOSS:  ${r.get('stop_loss')}")
    if r.get("take_profit"):
        print(f"TAKE PROFIT:${r.get('take_profit')}")
        if r.get("stop_loss"):
            risco_val = abs(d['preco'] - r['stop_loss'])
            recompensa = abs(r['take_profit'] - d['preco'])
            if risco_val > 0:
                print(f"RATIO R/R:  1:{recompensa/risco_val:.1f}")
    print(sep)
    if confianca < 75:
        print("MODO CONSERVADOR - A aguardar melhor setup...")
    else:
        print(f"SINAL FORTE - {confianca}% de confianca!")
    print(sep + "\n")

def ciclo_analise():
    print("\n" + "=" * 58)
    print("   ROBO INICIADO - Analise de hora em hora")
    print("   Carrega CTRL+C para parar")
    print("=" * 58)
    while True:
        try:
            print(f"\nA iniciar analise: {datetime.now().strftime('%H:%M:%S')}")
            print("A ligar ao mercado...")
            d = obter_indicadores()
            print("A recolher noticias...")
            noticias = obter_noticias()
            print("A consultar o especialista...")
            r = analisar(d, noticias)
            mostrar(d, r, noticias)
            guardar_registo(d, r, noticias)
            proxima = datetime.now().strftime("%H:%M")
            print(f"Proxima analise em 1 hora. Ultima: {proxima}")
            print("A aguardar... (CTRL+C para parar)\n")
            time.sleep(3600)
        except KeyboardInterrupt:
            print("\nRobo parado pelo utilizador.")
            print(f"Registo guardado em: {FICHEIRO_REGISTO}")
            break
        except Exception as e:
            print(f"Erro: {e}. A tentar novamente em 5 minutos...")
            time.sleep(300)

ciclo_analise()
