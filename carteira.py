import json
import os
import yfinance as yf
import anthropic
import requests
import warnings
from datetime import datetime
warnings.filterwarnings("ignore")

# ============================================================
#   CARTEIRA VIRTUAL - ROBO DE TRADING - OURO XAU/USD
#   Simula operacoes reais com dinheiro ficticio
# ============================================================

FICHEIRO_CARTEIRA = "carteira.json"
SALDO_INICIAL = 10000.0  # 10.000 euros virtuais

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
        "lucro_total": 0.0
    }

def guardar_carteira(carteira):
    with open(FICHEIRO_CARTEIRA, "w") as f:
        json.dump(carteira, f, ensure_ascii=False, indent=2)

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

def verificar_posicao_aberta(carteira, preco_atual):
    pos = carteira["posicao_aberta"]
    if not pos:
        return carteira

    resultado = None
    lucro = 0.0
    motivo = ""

    if pos["tipo"] == "COMPRAR":
        if preco_atual >= pos["take_profit"]:
            lucro = (pos["take_profit"] - pos["preco_entrada"]) * pos["contratos"]
            resultado = "GANHOU"
            motivo = "Take Profit atingido"
        elif preco_atual <= pos["stop_loss"]:
            lucro = (pos["stop_loss"] - pos["preco_entrada"]) * pos["contratos"]
            resultado = "PERDEU"
            motivo = "Stop Loss atingido"

    elif pos["tipo"] == "VENDER":
        if preco_atual <= pos["take_profit"]:
            lucro = (pos["preco_entrada"] - pos["take_profit"]) * pos["contratos"]
            resultado = "GANHOU"
            motivo = "Take Profit atingido"
        elif preco_atual >= pos["stop_loss"]:
            lucro = (pos["preco_entrada"] - pos["stop_loss"]) * pos["contratos"]
            resultado = "PERDEU"
            motivo = "Stop Loss atingido"

    if resultado:
        carteira["saldo"] += lucro
        carteira["lucro_total"] += lucro
        carteira["total_operacoes"] += 1
        if resultado == "GANHOU":
            carteira["operacoes_ganhas"] += 1
        else:
            carteira["operacoes_perdidas"] += 1

        registo = {
            "hora_fecho": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "tipo": pos["tipo"],
            "preco_entrada": pos["preco_entrada"],
            "preco_saida": preco_atual,
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "lucro": round(lucro, 2),
            "resultado": resultado,
            "motivo": motivo
        }
        carteira["historico"].append(registo)
        carteira["posicao_aberta"] = None

        print(f"\n{'='*58}")
        print(f"   OPERACAO FECHADA - {resultado}!")
        print(f"{'='*58}")
        print(f"Tipo:        {pos['tipo']}")
        print(f"Entrada:     ${pos['preco_entrada']:.2f}")
        print(f"Saida:       ${preco_atual:.2f}")
        print(f"Motivo:      {motivo}")
        print(f"Lucro/Perda: ${lucro:+.2f}")
        print(f"Saldo atual: ${carteira['saldo']:.2f}")
        print(f"{'='*58}\n")

    return carteira

def abrir_posicao(carteira, decisao, preco, stop_loss, take_profit):
    if not stop_loss or not take_profit:
        return carteira

    risco_por_contrato = abs(preco - stop_loss)
    if risco_por_contrato <= 0:
        return carteira

    capital_em_risco = carteira["saldo"] * 0.02
    contratos = round(capital_em_risco / risco_por_contrato, 4)

    carteira["posicao_aberta"] = {
        "tipo": decisao,
        "preco_entrada": preco,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "contratos": contratos,
        "hora_abertura": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    print(f"\n{'='*58}")
    print(f"   NOVA OPERACAO ABERTA - {decisao}!")
    print(f"{'='*58}")
    print(f"Preco entrada: ${preco:.2f}")
    print(f"Stop Loss:     ${stop_loss:.2f}")
    print(f"Take Profit:   ${take_profit:.2f}")
    print(f"Contratos:     {contratos}")
    print(f"Capital risco: ${capital_em_risco:.2f} (2% do saldo)")
    print(f"{'='*58}\n")

    return carteira

def mostrar_estado_carteira(carteira, d, r, noticias):
    hora = datetime.now().strftime("%H:%M - %d/%m/%Y")
    sep = "=" * 58
    print(f"\n{sep}")
    print("   ROBO ANALISTA + CARTEIRA VIRTUAL")
    print(f"   {hora}")
    print(sep)
    print(f"Preco Ouro:     ${d['preco']:.2f}")
    print(f"Variacao 24h:   {d['var_24h']:.2f}%")
    print(f"RSI:            {d['rsi']:.1f}")
    print(f"MACD / Signal:  {d['macd']:.2f} / {d['signal']:.2f}")
    print(sep)
    print("   NOTICIAS")
    print(sep)
    for i, n in enumerate(noticias[:3], 1):
        print(f"{i}. {n[:65]}")
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
    print("   DECISAO")
    print(sep)
    decisao = r.get("decisao", "AGUARDAR")
    confianca = r.get("confianca", 0)
    print(f"Decisao:    {decisao}")
    print(f"Confianca:  {confianca}%")
    print(f"Risco:      {r.get('risco', 'N/A')}")
    if confianca >= 75:
        print(f"SINAL FORTE - A abrir posicao!")
    else:
        print(f"MODO CONSERVADOR - A aguardar...")
    print(sep)
    print("   CARTEIRA VIRTUAL")
    print(sep)
    lucro_cor = "+" if carteira["lucro_total"] >= 0 else ""
    print(f"Saldo:          ${carteira['saldo']:.2f}")
    print(f"Saldo inicial:  ${SALDO_INICIAL:.2f}")
    print(f"Lucro total:    {lucro_cor}${carteira['lucro_total']:.2f}")
    rentabilidade = ((carteira['saldo'] - SALDO_INICIAL) / SALDO_INICIAL) * 100
    print(f"Rentabilidade:  {rentabilidade:+.2f}%")
    print(f"Operacoes:      {carteira['total_operacoes']} total | {carteira['operacoes_ganhas']} ganhas | {carteira['operacoes_perdidas']} perdidas")
    if carteira["posicao_aberta"]:
        pos = carteira["posicao_aberta"]
        lucro_atual = 0
        if pos["tipo"] == "COMPRAR":
            lucro_atual = (d['preco'] - pos["preco_entrada"]) * pos["contratos"]
        else:
            lucro_atual = (pos["preco_entrada"] - d['preco']) * pos["contratos"]
        print(f"\nPOSICAO ABERTA:")
        print(f"  Tipo:        {pos['tipo']}")
        print(f"  Entrada:     ${pos['preco_entrada']:.2f}")
        print(f"  Atual:       ${d['preco']:.2f}")
        print(f"  Stop Loss:   ${pos['stop_loss']:.2f}")
        print(f"  Take Profit: ${pos['take_profit']:.2f}")
        print(f"  P&L atual:   ${lucro_atual:+.2f}")
    else:
        print(f"\nSem posicao aberta.")
    print(sep + "\n")

def ciclo():
    print("\n" + "=" * 58)
    print("   CARTEIRA VIRTUAL INICIADA")
    print(f"   Saldo inicial: ${SALDO_INICIAL:.2f}")
    print("   Carrega CTRL+C para parar")
    print("=" * 58)

    carteira = carregar_carteira()

    while True:
        try:
            print(f"\nA analisar: {datetime.now().strftime('%H:%M:%S')}")
            print("A ligar ao mercado...")
            d = obter_indicadores()
            print("A recolher noticias...")
            noticias = obter_noticias()
            print("A consultar o especialista...")
            r = analisar(d, noticias)

            # Verificar se posicao aberta foi fechada
            carteira = verificar_posicao_aberta(carteira, d["preco"])

            # Abrir nova posicao se sinal forte e sem posicao aberta
            decisao = r.get("decisao", "AGUARDAR")
            confianca = r.get("confianca", 0)
            if confianca >= 75 and decisao in ["COMPRAR", "VENDER"] and not carteira["posicao_aberta"]:
                carteira = abrir_posicao(carteira, decisao, d["preco"],
                                         r.get("stop_loss"), r.get("take_profit"))

            mostrar_estado_carteira(carteira, d, r, noticias)
            guardar_carteira(carteira)

            print(f"Proxima analise em 1 hora. (CTRL+C para parar)\n")
            import time
            time.sleep(3600)

        except KeyboardInterrupt:
            print("\nRobo parado.")
            guardar_carteira(carteira)
            print(f"Carteira guardada em: {FICHEIRO_CARTEIRA}")
            print(f"Saldo final: ${carteira['saldo']:.2f}")
            break
        except Exception as e:
            print(f"Erro: {e}. A tentar em 5 minutos...")
            import time
            time.sleep(300)

ciclo()
