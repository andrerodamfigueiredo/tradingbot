import yfinance as yf
import anthropic
import requests
import os
import json
import time
import subprocess
import warnings
from datetime import datetime, timezone

import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FICHEIRO_CARTEIRA = os.path.join(BASE_DIR, "carteira.json")
SALDO_INICIAL = 10000.0
SEP = "=" * 60


# ─── FUNÇÕES ─────────────────────────────────────────────────────────────────

def sessao_mercado():
    hora_utc = datetime.now(timezone.utc).hour
    if 8 <= hora_utc < 17:
        return "LONDRA"
    elif 13 <= hora_utc < 22:
        return "NEW YORK"
    elif hora_utc >= 23 or hora_utc < 8:
        return "ASIA"
    return "TRANSICAO"


def obter_indicadores():
    ouro = yf.download("GC=F", period="90d", interval="1h", progress=False)

    close = ouro["Close"].squeeze()
    high  = ouro["High"].squeeze()
    low   = ouro["Low"].squeeze()
    volume = ouro["Volume"].squeeze()

    preco    = close.iloc[-1].item()
    preco_24h = close.iloc[-24].item()
    var_24h  = ((preco - preco_24h) / preco_24h) * 100

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

    std   = close.rolling(20).std()
    bb_up = (close.rolling(20).mean() + 2 * std).iloc[-1].item()
    bb_dn = (close.rolling(20).mean() - 2 * std).iloc[-1].item()
    bb_mid = close.rolling(20).mean().iloc[-1].item()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1].item()

    low14   = low.rolling(14).min()
    high14  = high.rolling(14).max()
    stoch_k = (100 * (close - low14) / (high14 - low14)).iloc[-1].item()
    stoch_d = (100 * (close - low14) / (high14 - low14)).rolling(3).mean().iloc[-1].item()

    vol_media_20h = volume.rolling(20).mean().iloc[-1].item()
    vol_atual     = volume.iloc[-1].item()
    vol_ratio     = vol_atual / vol_media_20h if vol_media_20h > 0 else 1.0

    max30 = close.tail(720).max().item()
    min30 = close.tail(720).min().item()
    max7  = close.tail(168).max().item()
    min7  = close.tail(168).min().item()

    return {
        "preco": preco, "var_24h": var_24h,
        "mm20": mm20, "mm50": mm50, "mm200": mm200,
        "rsi": rsi,
        "macd": macd, "signal": signal, "macd_hist": macd_hist,
        "bb_up": bb_up, "bb_mid": bb_mid, "bb_dn": bb_dn,
        "atr": atr, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "vol_ratio": vol_ratio, "sessao": sessao_mercado(),
        "max30": max30, "min30": min30, "max7": max7, "min7": min7,
    }


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
        except Exception:
            pass
    if len(noticias) < 5:
        noticias += [
            "Gold holds near record highs amid global uncertainty",
            "Federal Reserve signals cautious approach to rate cuts",
            "Dollar weakens as inflation data comes in mixed",
            "Geopolitical tensions boost safe-haven demand for gold",
            "Central banks continue record gold purchases in 2026",
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
SESSAO DE MERCADO: {d['sessao']}

DADOS TECNICOS:
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

NOTICIAS:
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


def carregar_carteira():
    if os.path.exists(FICHEIRO_CARTEIRA):
        with open(FICHEIRO_CARTEIRA, "r") as f:
            return json.load(f)
    return {
        "saldo": SALDO_INICIAL,
        "posicao_aberta": None,
        "historico": [],
        "total_operacoes": 0,
        "operacoes_ganhas": 0,
        "operacoes_perdidas": 0,
        "lucro_total": 0.0,
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
        carteira["saldo"] += lucro
        carteira["lucro_total"] += lucro
        carteira["total_operacoes"] += 1
        if resultado == "GANHOU":
            carteira["operacoes_ganhas"] += 1
        else:
            carteira["operacoes_perdidas"] += 1
        carteira["historico"].append({
            "hora": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "tipo": pos["tipo"],
            "entrada": pos["preco_entrada"],
            "saida": preco,
            "lucro": round(lucro, 2),
            "resultado": resultado,
        })
        carteira["posicao_aberta"] = None
        sinal = "+" if lucro >= 0 else ""
        print(f"  OPERACAO FECHADA: {resultado} | Lucro: ${sinal}{lucro:.2f}")
    return carteira


def abrir_posicao(carteira, decisao, preco, stop_loss, take_profit):
    if not stop_loss or not take_profit:
        return carteira
    risco = abs(preco - stop_loss)
    if risco <= 0:
        return carteira
    capital_risco = carteira["saldo"] * 0.02
    contratos = round(capital_risco / risco, 4)
    carteira["posicao_aberta"] = {
        "tipo": decisao,
        "preco_entrada": preco,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "contratos": contratos,
        "hora_abertura": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    print(f"  POSICAO ABERTA: {decisao} @ ${preco:.2f} | SL: ${stop_loss:.2f} | TP: ${take_profit:.2f} | Contratos: {contratos}")
    return carteira


def executar_analise():
    hora = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n{SEP}")
    print(f"  ANALISE AGENDADA XAU/USD  |  {hora}")
    print(SEP)

    print("  A obter indicadores...")
    d = obter_indicadores()
    print("  A obter noticias...")
    noticias = obter_noticias()
    print("  A consultar Claude Haiku...")
    r = analisar(d, noticias)

    carteira = carregar_carteira()
    carteira = verificar_posicao(carteira, d["preco"])

    decisao      = r.get("decisao", "AGUARDAR")
    confianca    = r.get("confianca", 0)
    stop_loss    = r.get("stop_loss")
    take_profit  = r.get("take_profit")
    risco        = r.get("risco", "N/A")
    zona         = r.get("zona", "N/A")
    raciocinio   = r.get("raciocinio", "N/A")
    factores_pos = r.get("factores_positivos", [])
    factores_neg = r.get("factores_negativos", [])

    print(f"\n{'─'*60}")
    print(f"  INDICADORES TECNICOS")
    print(f"{'─'*60}")
    print(f"  Preco:          ${d['preco']:.2f}")
    print(f"  Variacao 24h:   {d['var_24h']:+.2f}%")
    print(f"  Sessao:         {d['sessao']}")
    print(f"  MM20/50/200:    {d['mm20']:.2f} / {d['mm50']:.2f} / {d['mm200']:.2f}")
    print(f"  RSI(14):        {d['rsi']:.1f}")
    print(f"  MACD:           {d['macd']:.2f} | Signal: {d['signal']:.2f} | Hist: {d['macd_hist']:+.2f}")
    print(f"  Bollinger:      {d['bb_dn']:.2f} — {d['bb_mid']:.2f} — {d['bb_up']:.2f}")
    print(f"  ATR(14):        {d['atr']:.2f}")
    print(f"  Stochastic:     K={d['stoch_k']:.1f}  D={d['stoch_d']:.1f}")
    print(f"  Volume ratio:   {d['vol_ratio']:.2f}x media 20h")
    print(f"  Range 7d:       {d['min7']:.2f} — {d['max7']:.2f}")
    print(f"  Range 30d:      {d['min30']:.2f} — {d['max30']:.2f}")

    print(f"\n{'─'*60}")
    print(f"  ANALISE DO TRADER (30 anos experiencia)")
    print(f"{'─'*60}")
    print(f"  Decisao:        {decisao}")
    print(f"  Confianca:      {confianca}%")
    print(f"  Risco:          {risco}")
    print(f"  Zona a vigiar:  {zona}")
    if stop_loss:
        print(f"  Stop Loss:      ${stop_loss:.2f}")
    if take_profit:
        print(f"  Take Profit:    ${take_profit:.2f}")
    if stop_loss and take_profit:
        rr = abs(take_profit - d["preco"]) / abs(d["preco"] - stop_loss)
        print(f"  Risk/Reward:    1:{rr:.1f}")
    print(f"\n  Raciocinio:")
    for linha in raciocinio.split(". "):
        if linha.strip():
            print(f"    - {linha.strip().rstrip('.')}.")

    if factores_pos:
        print(f"\n  Factores Positivos:")
        for f in factores_pos:
            print(f"    + {f}")

    if factores_neg:
        print(f"\n  Factores Negativos:")
        for f in factores_neg:
            print(f"    - {f}")

    print(f"\n{'─'*60}")
    print(f"  GESTAO DE CARTEIRA")
    print(f"{'─'*60}")

    if carteira["posicao_aberta"]:
        pos = carteira["posicao_aberta"]
        print(f"  Posicao aberta: {pos['tipo']} @ ${pos['preco_entrada']:.2f} (desde {pos['hora_abertura']})")
        pnl = (d["preco"] - pos["preco_entrada"]) * pos["contratos"] if pos["tipo"] == "COMPRAR" \
              else (pos["preco_entrada"] - d["preco"]) * pos["contratos"]
        print(f"  PnL flutuante:  ${pnl:+.2f}")

    if confianca >= 75 and decisao in ["COMPRAR", "VENDER"] and not carteira["posicao_aberta"]:
        carteira = abrir_posicao(carteira, decisao, d["preco"], stop_loss, take_profit)
    else:
        if carteira["posicao_aberta"]:
            print(f"  Posicao existente — sem nova entrada.")
        else:
            print(f"  MODO CONSERVADOR — Confianca {confianca}% abaixo de 75%. A aguardar.")

    rentabilidade = ((carteira["saldo"] - SALDO_INICIAL) / SALDO_INICIAL) * 100
    win_rate = (
        (carteira["operacoes_ganhas"] / carteira["total_operacoes"] * 100)
        if carteira["total_operacoes"] > 0 else 0
    )

    print(f"\n  Saldo:          ${carteira['saldo']:.2f}  ({rentabilidade:+.2f}%)")
    print(f"  Lucro total:    ${carteira['lucro_total']:+.2f}")
    print(f"  Operacoes:      {carteira['total_operacoes']}  (W:{carteira['operacoes_ganhas']} / L:{carteira['operacoes_perdidas']} / {win_rate:.0f}% win rate)")
    print(f"\n{SEP}\n")

    guardar_carteira(carteira)

    # Actualiza dados_dashboard.json
    try:
        script_gerar = os.path.join(BASE_DIR, "gerar_dados.py")
        subprocess.run(["python3", script_gerar], check=True, capture_output=True)
        print("[dashboard] dados_dashboard.json actualizado.")
    except Exception as e:
        print(f"[dashboard] AVISO: {e}")


# ─── LOOP PRINCIPAL ───────────────────────────────────────────────────────────

def main():
    print(f"\n{SEP}")
    print(f"  ROBOTRADING XAU/USD — A INICIAR")
    print(f"  Analise de hora em hora | CTRL+C para parar")
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
