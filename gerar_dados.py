#!/usr/bin/env python3
"""
Lê robo_log.txt + carteira.json e gera dados_dashboard.json.
Parser linha-a-linha: procura Preco:, RSI:, Decisao:, Confianca:, Raciocinio:
em cada bloco de análise.
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
LOG  = BASE / "robo_log.txt"
CART = BASE / "carteira.json"
OUT  = BASE / "dados_dashboard.json"

SALDO_INICIAL = 10000.0


# ── CARTEIRA ──────────────────────────────────────────────────────────────────
def ler_carteira():
    try:
        raw = json.loads(CART.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[AVISO] carteira.json: {e}")
        raw = {}

    saldo    = raw.get("saldo", SALDO_INICIAL)
    lucro    = raw.get("lucro_total", 0.0)
    total    = raw.get("total_operacoes", 0)
    ganhas   = raw.get("operacoes_ganhas", 0)
    perdidas = raw.get("operacoes_perdidas", 0)
    win_rate = round(ganhas / total * 100, 1) if total > 0 else 0
    rent     = round((saldo - SALDO_INICIAL) / SALDO_INICIAL * 100, 2)

    carteira = {
        "saldo":              saldo,
        "rentabilidade":      rent,
        "total_operacoes":    total,
        "operacoes_ganhas":   ganhas,
        "operacoes_perdidas": perdidas,
        "lucro_total":        lucro,
        "win_rate":           win_rate,
        "historico":          raw.get("historico", []),
    }
    return carteira, raw.get("posicao_aberta", None)


# ── DIVISÃO EM BLOCOS ─────────────────────────────────────────────────────────
# Um bloco começa quando encontramos uma linha de cabeçalho de análise.
# Suportamos dois padrões:
#   Novo:  "ANALISE AGENDADA XAU/USD  |  2026-05-05 21:44"
#   Velho: linha com "HH:MM - DD/MM/YYYY" dentro de bloco ====

RE_NOVO  = re.compile(r"ANALISE AGENDADA(?:\s+XAU/USD)?\s*[|:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", re.IGNORECASE)
RE_VELHO = re.compile(r"(\d{2}:\d{2})\s*-\s*(\d{2}/\d{2}/\d{4})")


def _hora_velho(m):
    hora_str = m.group(1)
    d, mes, ano = m.group(2).split("/")
    return f"{ano}-{mes}-{d} {hora_str}"


def dividir_em_blocos(lines):
    """Devolve lista de (hora, [linhas_do_bloco])."""
    blocos = []
    hora_atual = None
    bloco_atual = []

    for line in lines:
        l = line.strip()

        m = RE_NOVO.search(l)
        if m:
            if hora_atual and bloco_atual:
                blocos.append((hora_atual, bloco_atual))
            hora_atual = m.group(1)
            bloco_atual = []
            continue

        m = RE_VELHO.search(l)
        if m:
            if hora_atual and bloco_atual:
                blocos.append((hora_atual, bloco_atual))
            hora_atual = _hora_velho(m)
            bloco_atual = []
            continue

        if hora_atual is not None:
            bloco_atual.append(line)

    if hora_atual and bloco_atual:
        blocos.append((hora_atual, bloco_atual))

    return blocos


# ── EXTRACÇÃO DE CAMPOS ────────────────────────────────────────────────────────
# Padrões para cada campo — intencionalmente simples e directos
RE_PRECO     = re.compile(r"Prec[oó](?:\s+Ouro)?\s*[:\s]+\$?([\d.]+)", re.IGNORECASE)
RE_RSI       = re.compile(r"RSI(?:\(14\))?\s*[:\s]+([\d.]+)", re.IGNORECASE)
RE_DECISAO   = re.compile(r"Decis[aã]o\s*[:\s]+(COMPRAR|VENDER|AGUARDAR)", re.IGNORECASE)
RE_CONFIANCA = re.compile(r"Confian[cç]a\s*[:\s]+(\d+)\s*%?", re.IGNORECASE)
RE_RISCO     = re.compile(r"^[^\w]*Risco\s*[:\s]+(ALTO|MEDIO|M[EÉ]DIO|BAIXO|MODERADO)", re.IGNORECASE)
RE_RAC_INICIO= re.compile(r"^[^\w]*Raciocinio\s*:", re.IGNORECASE)
RE_SEPARADOR = re.compile(r"^[=─\-]{4,}$")
RE_PARAR_RAC = re.compile(r"(Factores|Zona\s*[a:]|MODO CONSERVADOR|GESTAO|Saldo\s*:|Operacoes|Lucro\s*:|Rentabilidade)", re.IGNORECASE)


def extrair_campos(hora, linhas):
    preco = rsi = decisao = confianca = risco = None
    rac_linhas = []
    in_rac = False
    in_rac_velho = False   # para formato velho com bloco RACIOCINIO DO ESPECIALISTA
    skip_sep = False

    for linha in linhas:
        l = linha.strip()

        # ── Formato velho: detecção do bloco de raciocínio ──────────────────
        if "RACIOCINIO DO ESPECIALISTA" in l:
            in_rac_velho = True
            skip_sep = True
            continue

        if in_rac_velho:
            if RE_SEPARADOR.match(l):
                if skip_sep:
                    skip_sep = False
                    continue
                in_rac_velho = False  # fim do bloco raciocínio velho
                continue
            if re.match(r"DECIS[AÃ]O", l, re.IGNORECASE):
                in_rac_velho = False
                continue
            if l:
                rac_linhas.append(l)
            continue

        # ── Extracção de campos chave:valor ──────────────────────────────────
        if not preco:
            m = RE_PRECO.search(l)
            if m:
                try:
                    preco = float(m.group(1))
                except ValueError:
                    pass

        if rsi is None:
            m = RE_RSI.search(l)
            if m:
                try:
                    rsi = float(m.group(1))
                except ValueError:
                    pass

        if not decisao:
            m = RE_DECISAO.search(l)
            if m:
                decisao = m.group(1).upper()

        if confianca is None:
            m = RE_CONFIANCA.search(l)
            if m:
                try:
                    confianca = int(m.group(1))
                except ValueError:
                    pass

        if not risco:
            m = RE_RISCO.search(l)
            if m:
                risco = m.group(1).upper().replace("MÉDIO", "MEDIO").replace("MEDIO", "MEDIO")

        # ── Raciocínio formato novo (linha "Raciocinio: texto" ou bullets) ───
        if RE_RAC_INICIO.match(l):
            in_rac = True
            # texto na mesma linha após "Raciocinio:"
            resto = RE_RAC_INICIO.sub("", l).strip()
            if resto:
                rac_linhas.append(resto)
            continue

        if in_rac:
            if RE_SEPARADOR.match(l) or RE_PARAR_RAC.search(l):
                in_rac = False
                continue
            texto = l.lstrip("-·•+").strip()
            if texto:
                rac_linhas.append(texto)

    if not preco or not decisao:
        return None

    raciocinio = " ".join(rac_linhas).strip()[:500]

    return {
        "hora":       hora,
        "preco":      preco,
        "rsi":        rsi,
        "decisao":    decisao,
        "confianca":  confianca or 0,
        "risco":      risco or "—",
        "raciocinio": raciocinio,
    }


# ── PARSER PRINCIPAL ──────────────────────────────────────────────────────────
def parse_log(text):
    lines = text.splitlines()
    blocos = dividir_em_blocos(lines)
    analises = []
    for hora, linhas in blocos:
        a = extrair_campos(hora, linhas)
        if a:
            analises.append(a)
    return analises


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    carteira, posicao = ler_carteira()

    try:
        log_text = LOG.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[ERRO] robo_log.txt: {e}")
        log_text = ""

    analises = parse_log(log_text)
    print(f"[OK] {len(analises)} análises encontradas.")

    dados = {
        "carteira":       carteira,
        "analises":       analises,
        "posicao_aberta": posicao,
    }

    OUT.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {OUT.name} gerado.")


if __name__ == "__main__":
    main()
