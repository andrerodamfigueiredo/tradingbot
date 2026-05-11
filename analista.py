import yfinance as yf
import anthropic
import os
import json
import warnings
warnings.filterwarnings("ignore")

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

    return {
        "preco": preco,
        "var_24h": var_24h,
        "mm20": mm20,
        "mm50": mm50,
        "rsi": rsi,
        "macd": macd,
        "signal": signal,
        "bb_up": bb_up,
        "bb_dn": bb_dn,
        "max30": max30,
        "min30": min30
    }

def analisar(d):
    prompt = f"""Es um trader profissional com 30 anos de experiencia em Ouro XAU/USD.
Es extremamente conservador. So recomendas entradas com confianca acima de 75%.
Preferes perder uma oportunidade a entrar numa operacao duvidosa.
Ratio minimo Risk/Reward: 1:2.

DADOS DO MERCADO AGORA:
- Preco: {d['preco']:.2f}
- Variacao 24h: {d['var_24h']:.2f}%
- Media Movel 20h: {d['mm20']:.2f}
- Media Movel 50h: {d['mm50']:.2f}
- RSI: {d['rsi']:.1f}
- MACD: {d['macd']:.2f} | Signal: {d['signal']:.2f}
- Bollinger Superior: {d['bb_up']:.2f}
- Bollinger Inferior: {d['bb_dn']:.2f}
- Maximo 30 dias: {d['max30']:.2f}
- Minimo 30 dias: {d['min30']:.2f}

Responde APENAS com JSON valido, sem texto antes ou depois, sem markdown:
{{"decisao":"COMPRAR ou VENDER ou AGUARDAR","confianca":50,"raciocinio":"explica em 4 frases em portugues como um trader experiente","stop_loss":null,"take_profit":null,"risco":"BAIXO ou MEDIO ou ALTO","zona":"descreve a zona de preco a vigiar"}}"""

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

def mostrar(d, r):
    print("\n" + "=" * 55)
    print("   ROBO ANALISTA - OURO XAU/USD")
    print("=" * 55)
    print(f"Preco atual:    ${d['preco']:.2f}")
    print(f"Variacao 24h:   {d['var_24h']:.2f}%")
    print(f"MM20 / MM50:    ${d['mm20']:.2f} / ${d['mm50']:.2f}")
    print(f"RSI:            {d['rsi']:.1f}")
    print(f"MACD / Signal:  {d['macd']:.2f} / {d['signal']:.2f}")
    print(f"Bollinger:      ${d['bb_dn']:.2f} - ${d['bb_up']:.2f}")
    print(f"Range 30d:      ${d['min30']:.2f} - ${d['max30']:.2f}")
    print("=" * 55)
    print("   RACIOCINIO DO ESPECIALISTA")
    print("=" * 55)
    print(r.get("raciocinio", "N/A"))
    print("=" * 55)
    print("   DECISAO FINAL")
    print("=" * 55)
    print(f"DECISAO:    {r.get('decisao')}")
    print(f"CONFIANCA:  {r.get('confianca')}%")
    print(f"RISCO:      {r.get('risco')}")
    print(f"ZONA:       {r.get('zona')}")
    if r.get("stop_loss"):
        print(f"STOP LOSS:  ${r.get('stop_loss')}")
    if r.get("take_profit"):
        print(f"TAKE PROFIT:${r.get('take_profit')}")
    print("=" * 55)
    if r.get("confianca", 0) < 75:
        print("MODO CONSERVADOR - A aguardar melhor setup...")
    else:
        print(f"SINAL FORTE - {r.get('confianca')}% de confianca!")
    print("=" * 55)

print("A ligar ao mercado...")
d = obter_indicadores()
print("A consultar o especialista...")
r = analisar(d)
mostrar(d, r)
