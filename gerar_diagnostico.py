#!/usr/bin/env python3
"""
Camada de diagnóstico — SÓ LEITURA.

Lê o estado da carteira no PostgreSQL (tabela carteira_estado) e gera
diagnostico_dados.json para o /diagnostico.html.

Não escreve no PostgreSQL, não abre/fecha posições, não importa nem altera
analise_agendada.py. Pode ser corrido manualmente sempre que se quiser
actualizar o diagnóstico:

    DATABASE_URL=postgresql://... python3 gerar_diagnostico.py

Usa DATABASE_URL (interno, dentro do Railway) ou DATABASE_PUBLIC_URL (fora).
"""
import os
import json
from datetime import datetime, timezone
from statistics import mean

import psycopg2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, "diagnostico_dados.json")

# Início da "fase estável" — operações fechadas antes desta data são
# ignoradas (fase instável, dados incompletos / estratégia em mudança).
DATA_INICIO = "2026-06-07"

CRIPTO = {"Bitcoin", "Ethereum"}

FAIXAS_SCORE = [(58, 69), (70, 79), (80, 89), (90, 100)]

HIPOTESE = "(hipótese — validar com backtesting antes de mudar)"


# ─── LIGAÇÃO ────────────────────────────────────────────────────────────────
def obter_carteira():
    url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        raise SystemExit("Defina DATABASE_URL ou DATABASE_PUBLIC_URL no ambiente.")
    conn = psycopg2.connect(url, connect_timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT dados FROM carteira_estado LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise SystemExit("carteira_estado está vazia.")
    raw = row[0]
    return raw if isinstance(raw, dict) else json.loads(raw)


# ─── HELPERS ────────────────────────────────────────────────────────────────
def faixa_de_score(score):
    for lo, hi in FAIXAS_SCORE:
        if lo <= score <= hi:
            return "90+" if hi == 100 else f"{lo}-{hi}"
    return "outro"


def sessao_de(activo, hora_abertura):
    """Sessão de mercado em que a posição foi ABERTA (hora_abertura é UTC,
    tal como usado pelo robô para decidir sessao_operacional)."""
    if activo in CRIPTO:
        return "Cripto 24/7"
    try:
        dt = datetime.strptime(hora_abertura, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "Desconhecida"
    t = dt.hour * 60 + dt.minute
    if 6 * 60 + 45 <= t < 12 * 60:
        return "Londres"
    if 13 * 60 <= t < 21 * 60:
        return "New York"
    return "Fora de sessão"


# Heurística textual simples: deteta quando o próprio raciocínio de entrada
# (gerado pelo robô) descreve um cenário desfavorável à direcção escolhida.
# É correspondência de palavras-chave, NÃO análise semântica — usado apenas
# como contagem objectiva e auditável (lista de operações incluída no JSON).
GATILHOS_NEGATIVOS = ["prejudic", "contradi", "contrari", "deprecia", "enfraquece a posiç"]
FRASES_VENDER = ["posição vendid", "posição de venda", "vendedora"]
FRASES_COMPRAR = ["posição compr", "posição de compra", "comprada"]


def raciocinio_aponta_contra(raciocinio, tipo):
    r = (raciocinio or "").lower()
    if not any(g in r for g in GATILHOS_NEGATIVOS):
        return False
    frases = FRASES_VENDER if tipo == "VENDER" else FRASES_COMPRAR
    return any(f in r for f in frases)


def round2(x):
    return round(x, 2)


# ─── CAMADA 1 — FACTOS ────────────────────────────────────────────────────────
def calcular_factos(hist, posicoes_abertas):
    n = len(hist)
    ganhas_l = [h for h in hist if h["lucro_liquido"] >= 0]
    perdidas_l = [h for h in hist if h["lucro_liquido"] < 0]

    lucro_liquido_total = sum(h["lucro_liquido"] for h in hist)
    custos_totais = sum(h.get("custos", 0.0) for h in hist)
    lucro_nao_realizado = sum(p.get("pnl_atual", 0.0) for p in posicoes_abertas)

    lucro_medio_vencedores = mean(h["lucro_liquido"] for h in ganhas_l) if ganhas_l else 0.0
    perda_media_perdedores = mean(h["lucro_liquido"] for h in perdidas_l) if perdidas_l else 0.0
    racio = (abs(lucro_medio_vencedores / perda_media_perdedores)
             if perda_media_perdedores else None)

    performance_geral = {
        "total_fechadas": n,
        "ganhas": len(ganhas_l),
        "perdidas": len(perdidas_l),
        "win_rate_pct": round2(len(ganhas_l) / n * 100) if n else 0,
        "lucro_liquido_realizado": round2(lucro_liquido_total),
        "lucro_nao_realizado_posicoes_abertas": round2(lucro_nao_realizado),
        "n_posicoes_abertas": len(posicoes_abertas),
        "custos_totais_periodo": round2(custos_totais),
        "lucro_medio_vencedores": round2(lucro_medio_vencedores),
        "perda_media_perdedores": round2(perda_media_perdedores),
        "racio_ganho_perda": round(racio, 2) if racio is not None else None,
        "win_rate_equilibrio_pct": (round2(100 / (1 + racio)) if racio else None),
        "nota_unrealized": ("lucro_nao_realizado é o pnl_atual reportado pelo robô — "
                             "não inclui o custo de fecho que será debitado quando a "
                             "posição for encerrada."),
    }

    # ── POR ACTIVO ──
    por_activo = {}
    for h in hist:
        a = por_activo.setdefault(h["activo"], [])
        a.append(h)
    tabela_activo = []
    for activo, ops in por_activo.items():
        ganhas = sum(1 for o in ops if o["lucro_liquido"] >= 0)
        soma = sum(o["lucro_liquido"] for o in ops)
        tabela_activo.append({
            "activo": activo,
            "n": len(ops),
            "ganhas": ganhas,
            "perdidas": len(ops) - ganhas,
            "win_rate_pct": round2(ganhas / len(ops) * 100),
            "lucro_liquido": round2(soma),
            "lucro_medio": round2(soma / len(ops)),
        })
    tabela_activo.sort(key=lambda r: r["lucro_liquido"])
    pior_activo = tabela_activo[0]["activo"] if tabela_activo else None

    # ── POR TIPO (COMPRAR/VENDER) ──
    por_tipo = {}
    for h in hist:
        por_tipo.setdefault(h["tipo"], []).append(h)
    tabela_tipo = []
    for tipo, ops in por_tipo.items():
        ganhas = sum(1 for o in ops if o["lucro_liquido"] >= 0)
        soma = sum(o["lucro_liquido"] for o in ops)
        tabela_tipo.append({
            "tipo": tipo,
            "n": len(ops),
            "ganhas": ganhas,
            "win_rate_pct": round2(ganhas / len(ops) * 100),
            "lucro_liquido": round2(soma),
            "lucro_medio": round2(soma / len(ops)),
        })

    # ── POR FAIXA DE SCORE ──
    por_score = {}
    for h in hist:
        score = h.get("score_entrada")
        faixa = faixa_de_score(score) if score is not None else "sem score"
        por_score.setdefault(faixa, []).append(h)
    ordem_faixas = ["58-69", "70-79", "80-89", "90+", "sem score"]
    tabela_score = []
    for faixa in ordem_faixas:
        ops = por_score.get(faixa)
        if not ops:
            continue
        ganhas = sum(1 for o in ops if o["lucro_liquido"] >= 0)
        soma = sum(o["lucro_liquido"] for o in ops)
        tabela_score.append({
            "faixa": faixa,
            "n": len(ops),
            "ganhas": ganhas,
            "win_rate_pct": round2(ganhas / len(ops) * 100),
            "lucro_liquido": round2(soma),
            "lucro_medio": round2(soma / len(ops)),
        })

    # ── POR SESSÃO DE ENTRADA ──
    por_sessao = {}
    for h in hist:
        s = sessao_de(h["activo"], h.get("hora_abertura", ""))
        h["_sessao"] = s
        por_sessao.setdefault(s, []).append(h)
    tabela_sessao = []
    for sessao, ops in por_sessao.items():
        ganhas = sum(1 for o in ops if o["lucro_liquido"] >= 0)
        soma = sum(o["lucro_liquido"] for o in ops)
        tabela_sessao.append({
            "sessao": sessao,
            "n": len(ops),
            "ganhas": ganhas,
            "win_rate_pct": round2(ganhas / len(ops) * 100),
            "lucro_liquido": round2(soma),
            "lucro_medio": round2(soma / len(ops)),
        })
    tabela_sessao.sort(key=lambda r: r["lucro_liquido"])

    # ── PADRÃO TEMPORAL ──
    duracoes = [h.get("duracao") for h in hist if h.get("duracao") is not None]
    duracoes_ganhas = [h["duracao"] for h in ganhas_l if h.get("duracao") is not None]
    duracoes_perdidas = [h["duracao"] for h in perdidas_l if h.get("duracao") is not None]

    por_motivo = {}
    for h in hist:
        m = h.get("motivo_fecho", "desconhecido")
        por_motivo.setdefault(m, []).append(h)
    tabela_motivo = []
    for motivo, ops in por_motivo.items():
        ganhas = sum(1 for o in ops if o["lucro_liquido"] >= 0)
        soma = sum(o["lucro_liquido"] for o in ops)
        tabela_motivo.append({
            "motivo": motivo,
            "n": len(ops),
            "ganhas": ganhas,
            "win_rate_pct": round2(ganhas / len(ops) * 100),
            "lucro_liquido": round2(soma),
        })

    # Casos onde lucro_bruto e lucro_liquido têm sinais diferentes —
    # i.e. a operação teria fechado em lucro antes de custos, mas em
    # prejuízo depois de custos.
    casos_bruto_vs_liquido = []
    for h in hist:
        bruto = h.get("lucro_bruto")
        liquido = h.get("lucro_liquido")
        if bruto is not None and liquido is not None and bruto >= 0 and liquido < 0:
            casos_bruto_vs_liquido.append({
                "activo": h["activo"], "tipo": h["tipo"],
                "hora_fecho": h["hora_fecho"],
                "lucro_bruto": round2(bruto), "lucro_liquido": round2(liquido),
                "motivo_fecho": h.get("motivo_fecho"),
            })

    padrao_temporal = {
        "duracao_media_min": round2(mean(duracoes)) if duracoes else None,
        "duracao_media_ganhas_min": round2(mean(duracoes_ganhas)) if duracoes_ganhas else None,
        "duracao_media_perdidas_min": round2(mean(duracoes_perdidas)) if duracoes_perdidas else None,
        "por_motivo_fecho": tabela_motivo,
        "casos_bruto_positivo_liquido_negativo": casos_bruto_vs_liquido,
        "dados_insuficientes": [
            "melhor_preco (pico de preço/lucro durante a vida da posição) só é "
            "guardado para posições ABERTAS — não fica registado no histórico "
            "quando a posição fecha. Por isso NÃO é possível calcular, para "
            "operações já fechadas: (a) quanto tempo após a abertura ocorreu o "
            "lucro máximo, (b) quanto desse pico foi devolvido (drawdown) até "
            "ao fecho, (c) % de perdedoras que estiveram em lucro significativo "
            "antes de reverter, (d) lucro médio devolvido pelas que reverteram. "
            "A hipótese 'primeiras ~5h em lucro, depois sangra até ao stop' NÃO "
            "É TESTÁVEL com os dados actuais.",
        ],
    }

    # ── PRECISÃO DAS PREVISÕES (COMPRAR/VENDER x score) ──
    matriz = {}
    for h in hist:
        tipo = h["tipo"]
        faixa = faixa_de_score(h.get("score_entrada")) if h.get("score_entrada") is not None else "sem score"
        chave = (tipo, faixa)
        matriz.setdefault(chave, []).append(h)
    matriz_tabela = []
    for (tipo, faixa), ops in sorted(matriz.items()):
        ganhas = sum(1 for o in ops if o["lucro_liquido"] >= 0)
        soma = sum(o["lucro_liquido"] for o in ops)
        matriz_tabela.append({
            "tipo": tipo, "faixa": faixa, "n": len(ops), "ganhas": ganhas,
            "win_rate_pct": round2(ganhas / len(ops) * 100),
            "lucro_liquido": round2(soma),
        })
    precisao_direcao = {
        "por_tipo": tabela_tipo,
        "matriz_tipo_score": matriz_tabela,
        "nota": "Amostra pequena (N=17) — muitas células da matriz têm N=1 ou 2, "
                "não têm significado estatístico isolado.",
    }

    # ── RACIOCÍNIO vs DIRECÇÃO (heurística textual auditável) ──
    contra = []
    a_favor_ou_neutro = []
    for h in hist:
        item = {
            "activo": h["activo"], "tipo": h["tipo"], "hora_fecho": h["hora_fecho"],
            "lucro_liquido": round2(h["lucro_liquido"]), "score_entrada": h.get("score_entrada"),
        }
        if raciocinio_aponta_contra(h.get("raciocinio_entrada"), h["tipo"]):
            contra.append(item)
        else:
            a_favor_ou_neutro.append(item)
    raciocinio_vs_direcao = {
        "descricao": ("Nº de operações cujo próprio texto 'raciocinio_entrada' "
                       "(gerado pelo robô na abertura) descreve, através de "
                       "palavras-chave, um cenário desfavorável à direcção "
                       "escolhida (ex.: raciocínio fala de força do EUR mas a "
                       "posição é VENDER em EUR/USD)."),
        "gatilhos_negativos": GATILHOS_NEGATIVOS,
        "frases_vender": FRASES_VENDER,
        "frases_comprar": FRASES_COMPRAR,
        "n_total": n,
        "n_raciocinio_contra": len(contra),
        "win_rate_contra_pct": round2(sum(1 for o in contra if o["lucro_liquido"] >= 0) / len(contra) * 100) if contra else None,
        "lucro_liquido_contra": round2(sum(o["lucro_liquido"] for o in contra)),
        "n_raciocinio_outros": len(a_favor_ou_neutro),
        "win_rate_outros_pct": round2(sum(1 for o in a_favor_ou_neutro if o["lucro_liquido"] >= 0) / len(a_favor_ou_neutro) * 100) if a_favor_ou_neutro else None,
        "lucro_liquido_outros": round2(sum(o["lucro_liquido"] for o in a_favor_ou_neutro)),
        "operacoes_raciocinio_contra": contra,
    }

    return {
        "performance_geral": performance_geral,
        "por_activo": tabela_activo,
        "pior_activo": pior_activo,
        "por_tipo": tabela_tipo,
        "por_score": tabela_score,
        "por_sessao": tabela_sessao,
        "padrao_temporal": padrao_temporal,
        "precisao_direcao": precisao_direcao,
        "raciocinio_vs_direcao": raciocinio_vs_direcao,
    }


# ─── CAMADA 2 — LEITURA (hipóteses) ──────────────────────────────────────────
def calcular_leitura(factos):
    leitura = []
    pg = factos["performance_geral"]

    # 1. Equilíbrio ganho/perda vs win rate
    if pg["racio_ganho_perda"] is not None:
        leitura.append({
            "texto": (
                f"O rácio ganho médio / perda média é {pg['racio_ganho_perda']} "
                f"(ganho médio +${pg['lucro_medio_vencedores']} vs perda média "
                f"${pg['perda_media_perdedores']}), o que exigiria um win rate de "
                f"equilíbrio de ~{pg['win_rate_equilibrio_pct']}%. O win rate real "
                f"é {pg['win_rate_pct']}% ({pg['ganhas']}/{pg['total_fechadas']}), "
                f"muito abaixo desse limiar — o problema dominante parece ser a "
                f"taxa de acerto, não o tamanho relativo de stops/profits. {HIPOTESE}"
            ),
            "suporte": "performance_geral",
        })

    # 2. Sessão — agrupar sessões lucrativas vs não-lucrativas
    sessoes = factos["por_sessao"]
    neg = [s for s in sessoes if s["lucro_liquido"] < 0]
    pos = [s for s in sessoes if s["lucro_liquido"] >= 0]
    if neg and pos:
        n_neg = sum(s["n"] for s in neg)
        ganhas_neg = sum(s["ganhas"] for s in neg)
        soma_neg = sum(s["lucro_liquido"] for s in neg)
        nomes_neg = " e ".join(f"'{s['sessao']}'" for s in neg)
        n_pos = sum(s["n"] for s in pos)
        ganhas_pos = sum(s["ganhas"] for s in pos)
        soma_pos = sum(s["lucro_liquido"] for s in pos)
        nomes_pos = " e ".join(f"'{s['sessao']}'" for s in pos)
        leitura.append({
            "texto": (
                f"Agrupando por sessão em que a posição foi ABERTA: "
                f"{nomes_pos} (N={n_pos}) fecharam com lucro líquido total "
                f"${round2(soma_pos)} e win rate {round2(ganhas_pos/n_pos*100)}% "
                f"({ganhas_pos}/{n_pos}), enquanto {nomes_neg} (N={n_neg}) "
                f"fecharam com ${round2(soma_neg)} e win rate "
                f"{round2(ganhas_neg/n_neg*100)}% ({ganhas_neg}/{n_neg}). {HIPOTESE}"
            ),
            "suporte": "por_sessao",
        })

    # 3. Score
    faixas_score = {r["faixa"]: r for r in factos["por_score"]}
    if "90+" in faixas_score and any(f in faixas_score for f in ("58-69", "70-79")):
        alta = faixas_score["90+"]
        baixas_n = sum(faixas_score[f]["n"] for f in ("58-69", "70-79") if f in faixas_score)
        baixas_ganhas = sum(faixas_score[f]["ganhas"] for f in ("58-69", "70-79") if f in faixas_score)
        baixas_wr = round2(baixas_ganhas / baixas_n * 100) if baixas_n else 0
        leitura.append({
            "texto": (
                f"Operações com score_entrada 90+ tiveram {alta['win_rate_pct']}% "
                f"win rate ({alta['ganhas']}/{alta['n']}, lucro líquido "
                f"${alta['lucro_liquido']}), enquanto operações com score 58-79 "
                f"tiveram {baixas_wr}% win rate ({baixas_ganhas}/{baixas_n}). "
                f"Nesta amostra, o score actual não mostra correlação positiva "
                f"clara com o resultado. {HIPOTESE}"
            ),
            "suporte": "por_score",
        })

    # 4. Pior activo + concentração EUR/USD VENDER com raciocínio "contra"
    rvd = factos["raciocinio_vs_direcao"]
    if factos["por_activo"]:
        pior = factos["por_activo"][0]
        if pior["lucro_liquido"] < 0:
            contra = rvd.get("operacoes_raciocinio_contra", [])
            n_pior_vender_contra = sum(
                1 for o in contra if o["activo"] == pior["activo"] and o["tipo"] == "VENDER"
            )
            extra = ""
            if pior["activo"] == "EUR/USD" and n_pior_vender_contra == pior["n"]:
                extra = (
                    f" Nas {pior['n']} operações, todas foram VENDER e em todas "
                    f"o texto 'raciocinio_entrada' gerado pelo robô descreve "
                    f"explicitamente um cenário de força do EUR / fraqueza do "
                    f"USD (favorável a COMPRAR, desfavorável à posição VENDER "
                    f"aberta) — detecção por palavras-chave, ver "
                    f"raciocinio_vs_direcao.operacoes_raciocinio_contra."
                )
            leitura.append({
                "texto": (
                    f"'{pior['activo']}' é o activo com maior prejuízo líquido "
                    f"acumulado no período: ${pior['lucro_liquido']} em {pior['n']} "
                    f"operações ({pior['ganhas']} ganhas, win rate "
                    f"{pior['win_rate_pct']}%).{extra} {HIPOTESE}"
                ),
                "suporte": "por_activo + raciocinio_vs_direcao",
            })

    # 5. Raciocínio-contra geral — reportar mesmo quando NÃO mostra diferença,
    # para deixar claro que o padrão acima (EUR/USD) é específico do activo,
    # não um efeito geral desta heurística textual.
    if rvd["n_raciocinio_contra"] >= 2 and rvd["n_raciocinio_outros"] >= 2:
        leitura.append({
            "texto": (
                f"No total, {rvd['n_raciocinio_contra']} de {rvd['n_total']} "
                f"operações ({round2(rvd['n_raciocinio_contra']/rvd['n_total']*100)}%) "
                f"têm um raciocínio de entrada cujo texto descreve, por "
                f"palavras-chave, um cenário desfavorável à direcção aberta. "
                f"Win rate deste grupo: {rvd['win_rate_contra_pct']}% "
                f"(lucro líquido ${rvd['lucro_liquido_contra']}) vs "
                f"{rvd['win_rate_outros_pct']}% no restante "
                f"(lucro líquido ${rvd['lucro_liquido_outros']}) — "
                f"sem diferença que sugira esta heurística, por si só, como "
                f"preditor geral; a concentração em EUR/USD acima parece "
                f"específica desse activo/direcção, não um efeito geral. {HIPOTESE}"
            ),
            "suporte": "raciocinio_vs_direcao",
        })

    # 7. Padrão temporal — registar a limitação como observação também
    leitura.append({
        "texto": (
            "Não é possível testar a hipótese central 'primeiras ~5h em lucro, "
            "depois sangra até ao stop' com os dados actuais, porque o histórico "
            "de fechos não guarda o preço/lucro de pico (melhor_preco) atingido "
            "durante a vida da posição — esse valor só existe enquanto a posição "
            "está aberta. " + HIPOTESE
        ),
        "suporte": "padrao_temporal.dados_insuficientes",
    })

    return leitura


def calcular_veredito(factos, leitura, periodo):
    pg = factos["performance_geral"]
    resumo = (
        f"Fase estável ({periodo['inicio']} a {periodo['fim_dados']}, "
        f"N={pg['total_fechadas']} operações fechadas): win rate real "
        f"{pg['win_rate_pct']}% ({pg['ganhas']}/{pg['total_fechadas']}), "
        f"lucro líquido realizado ${pg['lucro_liquido_realizado']}, "
        f"lucro não realizado (posições abertas) ${pg['lucro_nao_realizado_posicoes_abertas']}."
    )
    top3 = [l["texto"] for l in leitura[:3]]
    proximos_testes = [
        "Backtest isolado da sessão New York (13-21 UTC) vs Londres vs Cripto "
        "para os 8 activos, no período " + periodo["inicio"] + " em diante "
        "— a validar em backtesting, não aplicar directamente.",
        "Backtest do score_entrada como filtro (ex.: exigir score >= 80 vs "
        "permitir >= 58) sobre uma amostra maior — a validar em backtesting, "
        "não aplicar directamente.",
        "Revisão isolada do prompt/instrução que gera 'raciocinio_entrada' "
        "para confirmar se a direcção (COMPRAR/VENDER) está a ser extraída "
        "correctamente do raciocínio gerado pelo modelo — a validar antes de "
        "qualquer alteração ao código de produção.",
        "Instrumentar o registo de fecho para incluir melhor_preco e o "
        "timestamp em que ocorreu, para permitir no futuro testar a hipótese "
        "de reversão pós-pico — requer alteração futura ao "
        "analise_agendada.py, fora do âmbito desta camada de leitura.",
    ]
    return {"resumo": resumo, "observacoes_top3": top3, "proximos_testes": proximos_testes}


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    carteira = obter_carteira()
    hist_completo = carteira.get("historico", [])
    posicoes_abertas = carteira.get("posicoes_abertas", [])

    hist = [h for h in hist_completo if h.get("hora_fecho", "") >= DATA_INICIO]
    hist.sort(key=lambda h: h.get("hora_fecho", ""))

    periodo = {
        "inicio": DATA_INICIO,
        "fim_dados": hist[-1]["hora_fecho"][:10] if hist else DATA_INICIO,
        "n_operacoes": len(hist),
        "n_ignoradas_fase_instavel": len(hist_completo) - len(hist),
    }

    factos = calcular_factos(hist, posicoes_abertas)
    leitura = calcular_leitura(factos)
    veredito = calcular_veredito(factos, leitura, periodo)

    saida = {
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "periodo": periodo,
        "veredito": veredito,
        "factos": factos,
        "leitura": leitura,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2, default=str)

    print(f"[diagnostico] {OUT_PATH} gerado — {len(hist)} operações no período "
          f"{periodo['inicio']} a {periodo['fim_dados']} "
          f"({periodo['n_ignoradas_fase_instavel']} ignoradas, fase instável).")
    print(f"[diagnostico] Win rate real: {factos['performance_geral']['win_rate_pct']}% "
          f"| Lucro líquido: ${factos['performance_geral']['lucro_liquido_realizado']}")


if __name__ == "__main__":
    main()
