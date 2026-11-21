# -*- coding: utf-8 -*-
# build: 2026-11-21-b (placar de todos os jogos pelo endereço da ESPN; tênis; jogo encerrado do dia fica na lista; data conferida com a ESPN)
"""
ONDE.ASSISTIR — Pipeline de dados v5 (auditoria de nov/2026)
============================================================
Roda sozinho no GitHub Actions (.github/workflows/atualizar.yml). Nenhuma
curadoria manual. Escopo: Futebol (BR + Europa, incl. Libertadores,
Sul-Americana, Champions e Copa do Brasil), NBA, NFL e tênis.

FONTES (cada uma protegida por try/except — uma falhar não derruba o resto):
  FUTEBOL .... Dois guias de TV, unidos (juntar_guias):
               - mantosdofutebol.com.br é o principal: jogo, competição, fase
                 e CANAIS por jogo, inclusive jogos pequenos (YouTube).
               - goal.com/br é o segundo: confirma o principal (selo v=2) e
                 acrescenta jogos que ele não lista. Entrou em 11/11/2026, no
                 lugar do futebolnatv.com.br (mudou de formato no fim de
                 outubro e passou a devolver zero jogos).
               Nos dois, a data de cada jogo vem do título do dia na própria
               página (elas listam vários dias).
               ESPN (tabelas oficiais, 7 dias) completa o que os guias ainda
               não publicaram, como "Transmissão a confirmar", e confirma o
               que publicaram (selo v=2 quando o horário confere). Clubes: 11
               ligas. Seleções: Nations League, eliminatórias e os amistosos
               da Seleção Brasileira (nomes já em português, lang=pt).
  BASQUETE ... ESPN (NBA). Canais POR JOGO, a partir dos transmissores que a
               ESPN informa em cada partida: pacote Amazon -> Prime Video;
               ESPN/ABC -> ESPN + Disney+; todos -> NBA League Pass.
  FUT. AMER. . ESPN (NFL). Canais pela janela de horário dos contratos
               2026/27: qui=SporTV, seg=ESPN, dom noite=SporTV, dom tarde=TV
               a confirmar; NFL Game Pass (DAZN) em todos.

  TÊNIS ...... ESPN (ATP e WTA): jogos, horários e fase. Entram as quartas de
               final em diante (Grand Slam: oitavas em diante), a fase de grupos
               dos Finals e TODO jogo com brasileiro; jogo sem horário definido
               ou sem os dois nomes não entra. A ESPN não informa emissora do
               Brasil: o canal vem do contrato de cada circuito (canais_tenis).

PLACAR: o app busca o placar na ESPN pelo endereço que o robô grava em cada
jogo, no campo "pl" = "feed|AAAAMMDD|id do evento|c ou v" (c: o primeiro time
do card é o mandante da ESPN; v: é o visitante). NBA, NFL, tênis e as tabelas
oficiais já vêm da ESPN com o id. Os outros jogos de futebol são procurados na
lista geral do dia (soccer/all, ~230 jogos num sábado): vale o jogo com os dois
times e o horário batendo, na mesma categoria (feminino não casa com
masculino). O que a ESPN não acompanha (estaduais, alguns campeonatos) fica sem
"pl" e o app mostra só "ao vivo" pelo relógio.

ENCERRADOS: os guias tiram da página o jogo que acabou. Jogo de HOJE que já
começou e saiu da página continua na lista (com_memoria), para o app mostrar o
placar final em "Mais cedo hoje".

DATA: se a ESPN não tem o jogo no dia em que a fonte o listou, mas tem no dia ao
lado (mesmos times, mesmo horário), a fonte errou a data: sai a cópia, ou o jogo
vai para a data da ESPN (acerta_datas). Se os dois guias concordam no dia, valem
os guias. Fica tudo registrado no diagnóstico (data_divergente, data_em_duvida).

MEMÓRIA: de madrugada a página "hoje" do guia ainda mostra ontem e a de
"amanhã" já pulou para depois de amanhã — o dia de hoje some por algumas horas.
Por isso cada guia tem memória por dia (memoria_guias.json): se a página deixa
de listar um dia que já listou, vale a última leitura daquele dia. Dia listado
agora sempre substitui a memória. O mesmo vale se o guia sair do ar, inteiro ou
só uma das páginas. A memória nunca derruba a coleta: se estiver estragada, ou
tiver sido gravada por outra versão deste arquivo, é descartada e recomeça.
A ESPN (NBA, NFL e tabelas) tem a mesma proteção, por dia consultado: cada
consulta é tentada 3 vezes; se mesmo assim não responder — ou responder uma
lista vazia para um dia que tinha jogos há menos de 12 horas — vale a última
leitura daquele dia (le_espn). Em 17/11/2026 a ESPN falhou só nas consultas da
NFL e os 13 jogos da semana sumiram do app sem aviso; não acontece mais.

SAÍDAS: eventos.json (dados), index.html (= template onde-assistir.html +
dados embutidos), memoria_guias.json (última leitura de cada dia dos guias) e
diagnostico.json (log das fontes + amostras brutas — é por ele que se depura,
sem precisar do log do Actions).

HORÁRIO: cada fonte que lista um jogo dá o seu horário (guia principal, 2º
guia, tabela oficial da ESPN). Quase sempre são iguais. Quando não são, vale o
horário em que DUAS fontes concordam; sem maioria, vale a tabela oficial, depois
o guia principal (acerta_horarios). Toda divergência vai para o diagnóstico. Em
21/11/2026 o guia principal trouxe Bahia x Coritiba às 17h00; o 2º guia e a
tabela diziam 17h30 (o certo) e o app mostrou 17h00, porque o guia principal
mandava sempre que a diferença fosse de até 30 minutos.

ALARME: se o guia principal ficar 2 horas sem jogos (ou a ESPN inteira fora),
a ESPN ficar 6 horas falhando em alguma consulta, ou o segundo guia ficar um
dia sem jogos, o robô publica o que tem e depois
falha de propósito — o GitHub avisa o dono do repositório por e-mail. Uma falha
isolada não toca. Repete o aviso a cada 24h enquanto durar.

DATAS SÃO ABSOLUTAS ("date": "2026-11-11"). O app só exibe um evento na aba
do dia exato — dado antigo nunca "escorrega" para hoje.
"""

import difflib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BUILD = "2026-11-21-b"       # igual à 2ª linha deste arquivo; carimba o diagnóstico e a memória
MEMORIA_DESDE = "2026-11-21-a"   # a memória gravada desta versão em diante tem o mesmo formato e continua valendo
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OndeAssistir/3.0)"}
DIAG = {"fontes": {}, "amostras": {}}
DIAS_LIDOS = {"mantos": set(), "goal": set()}     # dias (ISO) que cada guia listou nesta coleta
BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT).date()
JANELA = [HOJE + timedelta(days=i) for i in range(7)]  # baseline semanal

# ----------------------------------------------------------------- helpers
def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", sem_acento(s).lower()).strip()

CANAIS_FREE = {"GLOBO","SBT","BAND","RECORD","REDETV","TV BRASIL","CAZE TV","CAZETV",
               "XSPORTS","YOUTUBE","CANAL GOAT","GOAT","METROPOLES","TV CULTURA",
               "SPORTYNET","GE TV","GETV","NSPORTS","N SPORTS","FIFA+","RECORD NEWS"}
CANAIS_STREAM = {"DISNEY+","MAX","HBO MAX","PARAMOUNT+","PRIME VIDEO","AMAZON PRIME VIDEO",
                 "ONEFOOTBALL","FANATIZ","ZAPPING","UOL PLAY","GLOBOPLAY","PREMIERE FC",
                 "PREMIERE","DAZN","APPLE TV+","APPLE TV","APPLETV","NOSSO FUTEBOL+","NBA LEAGUE PASS"}

# Grafia das marcas (aplicada depois do .title(), que escreveria "Espn", "Sbt"...)
MARCAS = [("Fc","FC"),("Espn","ESPN"),("Sbt","SBT"),("Sportv","SporTV"),
          ("Cazétv","CazéTV"),("Caze Tv","CazéTV"),("Cazé Tv","CazéTV"),("Caze TV","CazéTV"),
          ("Xsports","XSports"),("Hbo","HBO"),("Tnt","TNT"),("Nba","NBA"),
          ("Ge Tv","GE TV"),("Getv","GE TV"),("Nsports","N Sports"),
          ("Youtube","YouTube"),("Dazn","DAZN"),("Fifa","FIFA"),("Goat","GOAT"),
          ("Redetv","RedeTV"),("Sportynet","SportyNet"),
          ("Apple Tv","Apple TV"),("Appletv","Apple TV"),("Onefootball","OneFootball")]
def grafia(nome):
    for a, dep in MARCAS: nome = nome.replace(a, dep)
    return nome

# Canais de YouTube cujo endereço vem todo em minúsculas (não dá para separar as
# palavras pelo maiúsculo/minúsculo): nome de exibição igual ao do canal de TV.
NOMES_YT = {"canalgoatbr": "Canal GOAT", "espnbrasil": "ESPN Brasil", "cazetv": "CazéTV",
            "metropolesesportes": "Metrópoles Esportes", "sportynetbrasil": "SportyNet",
            "xsports.brasil": "XSports"}

def canal(nome):
    """Nome cru da fonte -> {"n": nome exibido, "y": tv|stream|free, "u": link exato}.
    "u" só existe quando a própria fonte deu o endereço (canais de YouTube)."""
    nome = " ".join(nome.split())
    if re.search(r"youtube\.com/@", nome, re.I):
        # 'Youtube.com/@TNTSportsBR' -> 'TNT Sports BR (YouTube)', grátis, link do canal.
        # A fonte às vezes repete o começo do endereço ('Youtube.com/@Youtube.com/@concacaf'):
        # o canal é o último @, e 'youtube.com' nunca é nome de canal.
        handles = [h.rstrip(".-") for h in re.findall(r"@([\w.\-]+)", nome)]
        handles = [h for h in handles if h and not re.fullmatch(r"(www\.)?youtube(\.com)?", h, re.I)]
        if not handles: return {"n": "YouTube", "y": "free"}
        handle = handles[-1]
        legivel = NOMES_YT.get(handle.lower())
        if not legivel:
            legivel = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", handle)
            legivel = grafia(legivel[:1].upper() + legivel[1:])
        return {"n": f"{legivel} (YouTube)", "y": "free", "u": f"https://www.youtube.com/@{handle}"}
    chave = sem_acento(nome).upper().strip()
    base = re.sub(r"\s*\([^()]*\)$", "", chave).strip() or chave      # "Globo (SP)" é a Globo
    tipo = "free" if chave in CANAIS_FREE or base in CANAIS_FREE or "YOUTUBE" in chave else \
           "stream" if chave in CANAIS_STREAM or base in CANAIS_STREAM else "tv"
    return {"n": grafia(nome.title()), "y": tipo}

# País/região de cada liga, alinhado com os chips fixos do app. A PRIMEIRA
# regra que casar vence, então o específico vem antes do genérico.
PAIS_POR_LIGA = [
    (r"concacaf|\bafc\b|\bcaf\b|amistos|copa do mundo|mundial|eliminatorias|\bfifa\b|\bsele", "Mundial"),
    (r"brasileir|copa do brasil|supercopa do brasil|copa do nordeste|copa verde|copinha|"
     r"paulist|carioca|mineir|gauch|pernambucan|cearense|baian|paranaense|catarinense|goian|"
     r"paraiban|potiguar|capixaba|sergipan|alagoan|maranhense|piauiense|paraense|amazonense|"
     r"brasiliense|candango|mato.?grossense|tocantinense|fares lopes|"
     # copas estaduais que levam o nome do estado ("Copa Santa Catarina", "Copa Rio")
     r"santa catarina|espirito santo|alagoas|sergipe|paraiba|pernambuco|\bceara\b|\bbahia\b|\bgoias\b|"
     r"\bparana\b|maranhao|piaui|rio grande|mato grosso|tocantins|amazonas|rondonia|roraima|amapa|"
     r"\bacre\b|distrito federal|copa rio\b", "Brasil"),
    (r"libertadores|sul.?americana|recopa|conmebol|argentin|chilen|uruguai|colombian|"
     r"paraguai|peruan|equatorian|bolivian|venezuelan", "América do Sul"),
    (r"premier league|championship|ingl|fa cup|carabao|copa da liga inglesa|\befl\b|league (one|two)\b", "Inglaterra"),
    (r"la ?liga|espanh|copa do rei", "Espanha"),
    (r"\bital|coppa|serie a tim", "Itália"),
    (r"alem|bundesliga|dfb", "Alemanha"),
    (r"mls|nwsl|nba|wnba|nfl|mlb|\beua\b|canad", "EUA/Canadá"),
    (r"franc|ligue 1|portug|holand|eredivisie|champions|europa league|conference|uefa|\beuro|nations|"
     r"\bturc|\bruss|\bbelg|\bgreg|\bescoc|\bnoruegu|\bsuec|\bsuic|\bdinamarqu|\bservi|\bchec|\btchec|"
     r"\baustria|\bpolon|\bcroa|\bucrani", "Europa (demais)"),
]
def pais_da_liga(liga):
    alvo = sem_acento(liga).lower()
    for padrao, pais in PAIS_POR_LIGA:
        if re.search(padrao, alvo): return pais
    return "Mundial"     # liga não reconhecida: aparece só sem filtro de país

def genero(*textos):
    alvo = sem_acento(" ".join(textos)).lower()
    return "F" if re.search(r"feminin|\bfem\b|\(w\)|\bwomen|\bnwsl\b|\bwnba\b", alvo) else "M"

def evento(date, t, sport, league, match, ch, detail="—", g=None, country=None, v=1):
    return {"date": date.isoformat() if hasattr(date, "isoformat") else date,
            "t": t, "sport": sport, "g": g or genero(league, match),
            "country": country or pais_da_liga(league),
            "league": league.strip(), "detail": detail,
            "match": match, "ch": ch, "v": v}

# =================================================================
# FUTEBOL — guia de TV principal: mantosdofutebol.com.br
# =================================================================
def divide_fora_dos_parenteses(texto, separadores):
    """Como str.split, mas sem cortar o que está entre parênteses."""
    if texto.count("(") != texto.count(")"):            # parêntese sem par: corta em tudo, como sempre foi
        return [p.strip() for p in re.split("|".join(map(re.escape, separadores)), texto) if p.strip()]
    partes, atual, nivel, i = [], "", 0, 0
    while i < len(texto):
        c = texto[i]
        if c == "(": nivel += 1
        elif c == ")": nivel -= 1
        sep = next((x for x in separadores if texto.startswith(x, i)), None) if nivel == 0 else None
        if sep:
            partes.append(atual); atual = ""; i += len(sep)
        else:
            atual += c; i += 1
    partes.append(atual)
    return [p.strip() for p in partes if p.strip()]

# "(TV e YouTube)", "(TV/YouTube)": o parêntese só diz ONDE o canal passa
_ONDE = r"(?:tv(?: aberta| fechada)?|youtube|app|site|streaming|online)"
RX_SO_ONDE_PASSA = re.compile(rf"{_ONDE}(?:\s*(?:,|/|\+|&|\be\b)\s*{_ONDE})+", re.I)

def separa_canais_mantos(texto):
    """Divide 'A, B e C' e 'Globo/SporTV' sem quebrar URLs (Youtube.com/@X), que
    seguem inteiras para canal() extrair nome e link, nem o que vem entre
    parênteses: 'SportyNet (TV e YouTube)' é o mesmo canal em dois lugares."""
    out = []
    for parte in divide_fora_dos_parenteses(texto, [",", " e "]):
        for p in ([parte] if ".com" in parte.lower() else divide_fora_dos_parenteses(parte, ["/"])):
            m = re.fullmatch(r"(.+?)\s*\(([^()]*)\)", p)
            if m and RX_SO_ONDE_PASSA.fullmatch(m.group(2).strip()):
                out.append(m.group(1).strip())
                if re.search(r"youtube", m.group(2), re.I): out.append(m.group(1).strip() + " (YouTube)")
            else:
                out.append(p)
    return out

# Cabeçalho de dia nas páginas do mantos: "Segunda – 09/11", "Sexta-feira, 13/11"...
# Precisa COMEÇAR pelo dia da semana: data solta no meio de um texto não é cabeçalho.
RX_DIA_MANTOS = re.compile(
    r"^\W*(segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?\W+(\d{1,2})/(\d{1,2})\b", re.I)

RX_JOGO = re.compile(r"\d{1,2}h\d{2}\b")            # linha que começa por um horário
RX_CANAIS = re.compile(r"Canais?\s*:\s*(.+)", re.I)

def data_do_cabecalho(linha):
    """'Segunda – 09/11' -> date (o ano é o que deixa a data mais perto de hoje)."""
    if len(linha) > 40: return None
    m = RX_DIA_MANTOS.match(sem_acento(linha))
    if not m: return None
    dia, mes = int(m.group(2)), int(m.group(3))
    opcoes = []
    for ano in (HOJE.year - 1, HOJE.year, HOJE.year + 1):
        try:
            opcoes.append(datetime(ano, mes, dia).date())
        except ValueError:
            pass
    return min(opcoes, key=lambda d: abs((d - HOJE).days)) if opcoes else None

def fonte_mantos():
    urls = {HOJE: "https://mantosdofutebol.com.br/guia-de-jogos-tv-hoje-ao-vivo/",
            HOJE + timedelta(days=1): "https://mantosdofutebol.com.br/jogos-de-amanha-tv/"}
    # Estrutura real (capturada via diagnostico.json em 09 e 11/11/2026):
    #   "Segunda – 09/11"                                   <- cabeçalho do DIA
    #   "15h00 – Palmeiras x Santos – Paulistão Sub-20 (Semifinal-ida)"
    #   "Canais: Youtube.com/@TNTSportsBR"
    # Cada página traz VÁRIOS dias em sequência. A data de cada jogo vem do
    # cabeçalho acima dele — nunca da URL ("hoje"/"amanhã"), que só vale como
    # último recurso se a página vier sem cabeçalho nenhum.
    # Entre o 2º time e a competição o separador é um travessão (– ou —) ou um
    # hífen COM espaço dos dois lados; hífen colado faz parte do nome do time
    # ("Atlético-MG", "River Plate-URU", "Al-Nassr").
    rx = re.compile(r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)(?:\s*[–—]\s*|\s+-\s+)([^(\n]+?)\s*(?:\(([^)]+)\))?\s*$")
    # o mesmo jogo publicado SEM a competição (a linha acaba no 2º time)
    rx_sem_liga = re.compile(r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)\s*[–—\-]?\s*$")
    evs = []
    for data_url, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        corpo = BeautifulSoup(r.text, "html.parser").select_one("article")
        if not corpo: continue
        linhas = [l.strip() for l in corpo.get_text("\n").split("\n") if l.strip()]
        diag = {"url": url, "dias": [], "jogos_sem_cabecalho": 0, "jogos_sem_canal": 0,
                "jogos_sem_competicao": 0, "futsal_descartados": 0, "linhas_emendadas": [],
                "madrugada_no_dia_seguinte": [],
                "linhas_com_data_ignoradas": [], "linhas_de_jogo_nao_lidas": []}
        data_atual, secao = None, None
        ant_min, virou, em_ordem = None, False, True     # ordem dos horários dentro do dia
        for i, linha in enumerate(linhas):
            m, extra = rx.search(linha), 0       # extra = linhas seguintes já usadas por este jogo
            if not m and RX_JOGO.match(linha):
                # A fonte às vezes parte o jogo em 2-3 linhas (link ou negrito no meio
                # do texto): "15h00 – Ypiranga x Afogados –" + "Pernambucano". Emenda
                # as seguintes até fechar "hora – A x B – competição".
                emenda = linha
                for k in (1, 2):
                    prox = linhas[i + k] if i + k < len(linhas) else ""
                    if not prox or len(prox) > 90 or RX_JOGO.match(prox) or RX_CANAIS.match(prox) \
                            or data_do_cabecalho(prox):
                        break
                    emenda += " " + prox
                    m = rx.search(emenda)
                    if m:
                        extra = k
                        if len(diag["linhas_emendadas"]) < 5: diag["linhas_emendadas"].append(emenda[:140])
                        break
                # Jogo publicado sem a competição: vale se os canais vêm logo abaixo.
                if not m and i + 1 < len(linhas) and RX_CANAIS.match(linhas[i + 1]):
                    m = rx_sem_liga.search(linha)
                    if m: diag["jogos_sem_competicao"] += 1
            if not m:
                d = data_do_cabecalho(linha)
                if d:
                    data_atual = d
                    secao = {"cabecalho": linha[:40], "data": d.isoformat(), "jogos": 0}
                    diag["dias"].append(secao)
                    DIAS_LIDOS["mantos"].add(d.isoformat())
                    ant_min, virou, em_ordem = None, False, True
                elif re.search(r"\b\d{1,2}/\d{1,2}\b", linha) and len(linha) <= 60 \
                        and len(diag["linhas_com_data_ignoradas"]) < 5:
                    diag["linhas_com_data_ignoradas"].append(linha)
                elif RX_JOGO.match(linha) and len(diag["linhas_de_jogo_nao_lidas"]) < 5:
                    # formato novo? aparece aqui, com as duas linhas seguintes de contexto
                    diag["linhas_de_jogo_nao_lidas"].append(
                        {"linha": linha[:120], "seguintes": [l[:80] for l in linhas[i+1:i+3]]})
                continue
            h, mi, t1, t2 = m.group(1, 2, 3, 4)
            compet, fase = (m.group(5), m.group(6)) if m.re is rx else ("", None)
            if not fase and i + extra + 1 < len(linhas):     # fase numa linha só dela: "(Semifinal-ida)"
                mf = re.fullmatch(r"\(([^)]+)\)", linhas[i + extra + 1])
                if mf: fase, extra = mf.group(1), extra + 1
            canais = []
            for prox in linhas[i+1+extra:i+3+extra]:
                mc = RX_CANAIS.match(prox)
                if mc:
                    canais = separa_canais_mantos(mc.group(1))
                    break
                if rx.search(prox): break  # próximo jogo veio antes da linha de canais
            liga = (compet or "").strip() or "Futebol"
            detalhe = (fase or "—").strip() or "—"
            # segurança: se a "liga" for apenas uma fase, vira detalhe
            if re.search(r"^(semifinal|quartas|oitavas|final|rodada|jogo \d)", sem_acento(liga), re.I):
                detalhe, liga = liga, "Futebol"
            # A lista de cada dia vem em ordem de horário. Se depois de um jogo da
            # tarde/noite aparece um de madrugada (00h00–05h59), ele acontece no dia
            # seguinte — e é nesse dia que o app mostra.
            minuto = int(h) * 60 + int(mi)
            if ant_min is not None and minuto < ant_min:
                if em_ordem and int(h) < 6 and ant_min >= 12 * 60: virou = True
                elif not virou: em_ordem = False             # lista fora de ordem: não conclui nada
            ant_min = minuto
            data_jogo = data_atual or data_url
            if virou and int(h) < 6:
                data_jogo += timedelta(days=1)
                if len(diag["madrugada_no_dia_seguinte"]) < 5:
                    diag["madrugada_no_dia_seguinte"].append(f"{linha[:70]} -> {data_jogo}")
            if re.search(r"futsal|\blnf\b", liga, re.I):          # futsal está fora do escopo do app
                diag["futsal_descartados"] += 1
                continue
            if secao: secao["jogos"] += 1
            else: diag["jogos_sem_cabecalho"] += 1
            if not canais: diag["jogos_sem_canal"] += 1
            evs.append(evento(data_jogo, f"{int(h):02d}:{mi}", "Futebol", liga,
                              f"{t1.strip()} x {t2.strip()}",
                              [canal(c) for c in canais] or [{"n": "Transmissão a confirmar", "y": "tv"}],
                              detail=detalhe))
        DIAG["amostras"].setdefault("mantos", []).append(diag)
    return dedup(evs)

# =================================================================
# ESPN API (pública, JSON) — NBA, NFL e tabelas de futebol
# Os canais NÃO vêm de uma lista fixa: NBA usa os transmissores que a ESPN
# informa em cada jogo (canais_nba); NFL usa a janela de horário do contrato
# (canais_nfl); futebol entra como "Transmissão a confirmar".
# =================================================================
class EnderecoMorto(Exception):
    """A ESPN respondeu que o endereço não existe (400/404): tentar de novo não adianta."""

ESPN_TENTATIVAS, ESPN_ESPERA = 3, 2      # tentativas por consulta; segundos de espera entre elas (x1, x2)
ESPN_SUSPEITA_H = 12                     # resposta vazia para um dia que tinha jogos há menos de 12 h: não vale
ESPN_CORTE = 4                           # consultas seguidas sem resposta até desistir da ESPN nesta coleta
ESPN = {"memoria": {}, "agora": None, "faltas": [], "sem_resposta": set(), "seguidas": 0, "cortada": False, "cortou": False}     # o main() zera a cada coleta

def espn_json(path, data, extra=""):
    return espn_pega(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={data:%Y%m%d}{extra}")

def espn_pega(url):
    # ESPN parada: depois de 4 consultas seguidas sem resposta, não insiste nas outras
    # (cada uma poderia levar minutos, e a coleta tem de terminar para publicar o resto)
    if ESPN["cortada"]: raise RuntimeError("ESPN sem resposta nesta coleta")
    erro = None
    for tentativa in range(1, ESPN_TENTATIVAS + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:               # sem resposta: rede, tempo esgotado
            erro = e
        else:
            if r.status_code in (400, 404): raise EnderecoMorto(f"HTTP {r.status_code}")
            try:
                r.raise_for_status()
                dados = r.json()
                if isinstance(dados, dict):
                    ESPN["seguidas"] = 0
                    return dados
                erro = ValueError("resposta da ESPN fora do formato")
            except Exception as e:           # 403, 429, 5xx, ou corpo que não é JSON
                erro = e
        if tentativa < ESPN_TENTATIVAS: time.sleep(ESPN_ESPERA * tentativa)
    ESPN["seguidas"] += 1
    if ESPN["seguidas"] >= ESPN_CORTE: ESPN["cortada"] = ESPN["cortou"] = True
    raise erro

def espn_outra_fonte():
    """Disjuntor meio aberto. O corte (4 consultas seguidas sem resposta) existe para a coleta não
    ficar minutos esperando uma ESPN parada. Mas em 17/11/2026 só as consultas da NFL falhavam: por
    isso cada fonte seguinte (NBA, NFL, tênis, lista geral) ainda tenta UMA consulta; se também
    falhar, o corte volta na hora."""
    if ESPN["cortada"]:
        ESPN["cortada"], ESPN["seguidas"] = False, ESPN_CORTE - 1

def le_espn(chave, path, dias, converte, extra="", vazio_vale=False):
    """Lê o scoreboard de `path` dia a dia e devolve os jogos já convertidos
    (`converte(evento cru)` devolve um evento do app, ou None para descartar).
    A ESPN às vezes falha numa consulta, ou responde uma lista vazia por engano;
    antes, os jogos daquele dia simplesmente sumiam do app. Agora cada dia
    consultado tem memória (memoria_guias.json, em "espn") e, para cada dia, vale:
      - respondeu com jogos ................... a leitura de agora;
      - não respondeu, depois de 3 tentativas . a última leitura daquele dia;
      - respondeu vazio, mas a última leitura, de menos de 12 h, tinha jogos:
        resposta suspeita, vale a última leitura (um dia inteiro não some de uma
        hora para a outra; se sumiu mesmo, a coleta depois das 12 h aceita);
      - respondeu vazio e não havia jogos ..... vazio mesmo.
    O que veio da memória fica anotado em ESPN["faltas"]: vai para o log, para o
    diagnóstico e, se durar, para o alarme. Só levanta erro quando nenhum dia
    respondeu e não há memória nenhuma (endereço morto, ou ESPN fora do ar na
    primeira coleta). `vazio_vale`: lista vazia é resposta normal (tênis: torneio que
    acabou ou semana sem torneio), então nunca é tratada como suspeita."""
    copia = lambda x: json.loads(json.dumps(x))
    respostas, mortos = {}, []
    for data in dias:
        try:
            respostas[data.isoformat()] = espn_json(path, data, extra).get("events") or []
        except EnderecoMorto:
            mortos.append(data.isoformat())
        except Exception:
            pass
    if not respostas: ESPN["sem_resposta"].add(chave)
    agora = ESPN["agora"] or datetime.now(BRT)
    todas = ESPN["memoria"].get("espn")
    if not isinstance(todas, dict): todas = ESPN["memoria"]["espn"] = {}
    antiga = todas.get(chave) if isinstance(todas.get(chave), dict) else {}
    def guardado_de(dia):                    # leitura anterior do dia, se estiver inteira
        g = antiga.get(dia)
        ok = isinstance(g, dict) and isinstance(g.get("jogos"), list) and \
             all(isinstance(j, dict) and all(isinstance(j.get(k), str) for k in ("date", "t", "match")) and isinstance(j.get("ch"), list)
                 for j in g["jogos"])
        return g if ok else None
    def idade_h(g):
        try: return (agora - datetime.fromisoformat(g["em"])).total_seconds() / 3600
        except Exception: return 1e9
    def converte_sem_quebrar(ev):            # um evento malformado não derruba a fonte
        try: return converte(ev)
        except Exception: return None
    nova, saida = {}, []
    for dia in (d.isoformat() for d in dias):
        g = guardado_de(dia)
        if dia in respostas:
            crus = respostas[dia]
            if crus or vazio_vale or not (g and g["jogos"]) or idade_h(g) > ESPN_SUSPEITA_H:
                jogos = []
                for cru in crus:                     # um evento da ESPN pode virar vários jogos (um torneio de tênis)
                    r = converte_sem_quebrar(cru)
                    for j in (r if isinstance(r, list) else [r]):
                        if isinstance(j, dict):
                            j["_q"] = dia.replace("-", "")      # dia consultado: é com ele que o app pede o placar
                            jogos.append(j)
                nova[dia] = {"lido_em": agora.strftime("%d/%m %H:%M"), "em": agora.isoformat(timespec="minutes"), "jogos": copia(jogos)}
                saida += jogos
                continue
            motivo = "respondeu vazio"
        elif dia in mortos and not antiga:
            continue                         # endereço que nunca funcionou: não é falta de um dia, é ERRO da liga
        else:
            motivo = "não respondeu"
        rotulo = f"{chave} {dia[8:]}/{dia[5:7]} {motivo}"
        if g:
            nova[dia] = g
            saida += copia(g["jogos"])
            ESPN["faltas"].append(f'{rotulo}: vale a leitura de {g.get("lido_em", "?")}, com {len(g["jogos"])} jogo(s)')
        else:
            ESPN["faltas"].append(f"{rotulo} e não há leitura anterior desse dia")
    todas[chave] = nova
    if not respostas and not saida:
        raise RuntimeError(f"ESPN {path}: nenhuma das {len(dias)} consultas respondeu")
    return saida

RX_ID_ESPN = re.compile(r"^[\w-]{1,24}$")
def com_placar(jogos, feed, lado="c"):
    """Troca as anotações da ESPN (_id, _q) pelo endereço do placar: "feed|dia consultado|id|lado"."""
    for e in jogos:
        i, q = str(e.pop("_id", "") or ""), str(e.pop("_q", "") or "")
        if RX_ID_ESPN.match(i) and re.fullmatch(r"\d{8}", q): e["pl"] = f"{feed}|{q}|{i}|{lado}"
    return jogos

# A ESPN agrupa os jogos pelo dia dos EUA. Jogo que começa depois da
# meia-noite de Brasília (00h00–02h) pertence à rodada americana da VÉSPERA,
# então NBA/NFL consultam também o dia anterior à janela.
DIAS_EUA = [HOJE - timedelta(days=1)] + JANELA

def broadcasts_espn(comp):
    """Nomes dos transmissores (EUA) que a própria ESPN lista no jogo."""
    nomes = []
    for b in comp.get("broadcasts", []):
        nomes += b.get("names", [])
    for gb in comp.get("geoBroadcasts", []):
        n = (gb.get("media") or {}).get("shortName")
        if n: nomes.append(n)
    return " | ".join(nomes).upper()

def canais_nba(comp):
    """NBA no Brasil 2026/27, POR JOGO (fonte: broadcasts da ESPN por partida):
    - pacote Amazon (global) -> incluído no Prime Video
    - jogos ESPN/ABC (EUA)  -> ESPN/Disney+ no Brasil
    - todos os demais       -> só NBA League Pass (pago à parte, mesmo dentro do Prime)"""
    tx = broadcasts_espn(comp)
    ch = []
    if "PRIME" in tx or "AMAZON" in tx:
        ch.append({"n": "Prime Video", "y": "stream"})
    if "ESPN" in tx or "ABC" in tx:
        ch += [{"n": "ESPN", "y": "tv"}, {"n": "Disney+", "y": "stream"}]
    ch.append({"n": "NBA League Pass", "y": "stream"})
    return ch

def fonte_nba():
    def converte(ev):
        dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
        if dt.date() not in JANELA: return None
        comp = ev.get("competitions", [{}])[0]
        times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
        if len(times) < 2: return None
        # instrumentação: transmissores crus dos primeiros jogos (valida canais_nba)
        if len(DIAG["amostras"].get("nba_broadcasts", [])) < 4:
            DIAG["amostras"].setdefault("nba_broadcasts", []).append(
                f"{times[1]} x {times[0]} :: {broadcasts_espn(comp) or '(vazio)'}")
        notas = comp.get("notes", [])
        detalhe = (notas[0].get("headline") if notas else "") or "—"   # só o que a ESPN destaca (NBA Cup, jogo internacional)
        jogo = evento(dt.date(), dt.strftime("%H:%M"), "Basquete", "NBA",
                      f"{times[1]} x {times[0]}", canais_nba(comp),
                      detail=detalhe, country="EUA/Canadá")
        jogo["_id"] = ev.get("id")
        return jogo
    espn_outra_fonte()
    return dedup(com_placar(le_espn("NBA", "basketball/nba", DIAS_EUA, converte), "basketball/nba", "v"))       # no card, o visitante vem primeiro

# Direitos NFL no Brasil 2026/27 (contratos públicos, por janela de horário):
# quinta = SporTV; segunda à noite = ESPN; domingo à noite = SporTV;
# domingo tarde = ESPN/SporTV/GE TV (varia por jogo); NFL Game Pass (DAZN) = todos.
def canais_nfl(dt):
    base = [{"n": "NFL Game Pass", "y": "stream"}]
    wd = dt.weekday()  # 0=seg ... 6=dom
    if wd == 3:
        return [{"n": "SporTV", "y": "tv"}, {"n": "Globoplay", "y": "stream"}] + base
    if wd == 0:
        return [{"n": "ESPN", "y": "tv"}, {"n": "Disney+", "y": "stream"}] + base
    if wd == 6 and dt.hour >= 21:
        return [{"n": "SporTV", "y": "tv"}, {"n": "Globoplay", "y": "stream"}] + base
    if wd == 6:
        return base + [{"n": "TV a confirmar", "y": "tv"}]
    return base

def fonte_nfl():
    def converte(ev):
        dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
        if dt.date() not in JANELA: return None
        comp = ev.get("competitions", [{}])[0]
        times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
        if len(times) < 2: return None
        notas = comp.get("notes", [])
        detalhe = (notas[0].get("headline") if notas else "") or "—"   # só o que a ESPN destaca (NBA Cup, jogo internacional)
        jogo = evento(dt.date(), dt.strftime("%H:%M"), "Futebol Americano", "NFL",
                      f"{times[1]} x {times[0]}", canais_nfl(dt),
                      detail=detalhe, country="EUA/Canadá")
        jogo["_id"] = ev.get("id")
        return jogo
    espn_outra_fonte()
    return dedup(com_placar(le_espn("NFL", "football/nfl", DIAS_EUA, converte), "football/nfl", "v"))


# =================================================================
# TÊNIS — ESPN (ATP e WTA). Um evento da ESPN é um torneio inteiro; os jogos
# vêm dentro, por chave (simples, duplas), com fase, horário e país de cada um.
# =================================================================
# Canal no Brasil: por contrato de cada circuito, não por jogo (a ESPN não
# informa emissora daqui). Conferido em 21/11/2026:
#   - temporada da ATP (Masters 1000, 500, 250, ATP Finals, Next Gen) e os quatro
#     Grand Slams: ESPN, com tudo no Disney+ (Meio & Mensagem, 1/7 e 27/10/2026);
#   - WTA fora dos Grand Slams: ESPN até o fim de 2026 e CazéTV a partir de 2027
#     (Meio & Mensagem, 1/7/2026). Só que a ESPN lista junto os WTA 125, que não
#     entram nesses contratos, e não diz o nível do torneio: fica "a confirmar";
#   - Copa Davis (CazéTV) não existe nesta fonte.
def canais_tenis(circuito, grand_slam):
    if circuito == "ATP" or grand_slam:
        return [{"n": "ESPN", "y": "tv"}, {"n": "Disney+", "y": "stream"}]
    return [{"n": "Transmissão a confirmar", "y": "tv"}]

TENIS = [("tennis/atp", "ATP", "M"), ("tennis/wta", "WTA", "F")]
FASE_FINAL = {"final", "finals", "semifinal", "semifinals", "quarterfinal", "quarterfinals", "group stage", "round robin"}
FASE_FINAL_SLAM = {"round of 16", "round 4", "4th round", "fourth round"}
RODADA_PT = {"final": "Final", "finals": "Final", "semifinal": "Semifinal", "semifinals": "Semifinal",
             "quarterfinal": "Quartas de final", "quarterfinals": "Quartas de final", "round of 16": "Oitavas de final",
             "group stage": "Fase de grupos", "round robin": "Fase de grupos", "3rd place": "Disputa do 3º lugar"}
def rodada_pt(rodada):
    chave = " ".join(rodada.lower().split())
    if chave in RODADA_PT: return RODADA_PT[chave]
    m = re.fullmatch(r"round (\d)", chave) or re.fullmatch(r"(\d)(?:st|nd|rd|th) round", chave)
    if m: return f"{m.group(1)}ª rodada"
    m = re.fullmatch(r"round of (\d+)", chave)
    if m: return f"Rodada de {m.group(1)}"
    if chave.startswith("qualif"): return "Qualificatório"
    return rodada.strip()

def jogo_de_tenis(comp, torneio, grand_slam, duplas, circuito, sexo):
    """Um jogo do torneio -> evento do app, ou None se não entra (ver regra no cabeçalho)."""
    if comp.get("timeValid") is False: return None                      # a ESPN ainda não tem o horário
    dt = datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(BRT)
    if dt.date() not in JANELA: return None
    lados = sorted(comp.get("competitors") or [], key=lambda c: c.get("order") or 9)
    if len(lados) != 2: return None
    nomes, brasil = [], False
    for c in lados:
        if duplas:
            r = c.get("roster") or {}
            nome, atletas = r.get("shortDisplayName") or r.get("displayName") or "", r.get("athletes") or []
        else:
            a = c.get("athlete") or {}
            nome, atletas = a.get("displayName") or "", [a]
        nome = " ".join(nome.split())
        if not nome or "TBD" in re.split(r"\s*/\s*", nome.upper()): return None       # adversário ainda não definido
        brasil = brasil or any(((a.get("flag") or {}).get("alt") or "").strip().lower() == "brazil" for a in atletas)
        nomes.append(nome)
    rodada = ((comp.get("round") or {}).get("displayName") or "").strip()
    chave = " ".join(rodada.lower().split())
    fase_final = chave in FASE_FINAL or (grand_slam and chave in FASE_FINAL_SLAM)
    entra = brasil or (fase_final and not duplas and (circuito == "ATP" or grand_slam))
    if not entra: return None
    detalhe = ", ".join(x for x in (rodada_pt(rodada), "duplas" if duplas else "") if x) or "—"
    jogo = evento(dt.date(), dt.strftime("%H:%M"), "Tênis", torneio, f"{nomes[0]} x {nomes[1]}",
                  canais_tenis(circuito, grand_slam), detail=detalhe, g=sexo, country="Mundial")
    jogo["_id"] = comp.get("id")
    return jogo

def fonte_tenis():
    evs, por_circuito = [], {}
    for path, circuito, sexo in TENIS:
        def converte(ev, circuito=circuito, sexo=sexo):
            torneio = " ".join((ev.get("name") or "Tênis").split())
            saida = []
            for chave in ev.get("groupings") or []:
                duplas = "doubles" in ((chave.get("grouping") or {}).get("slug") or "")
                for comp in chave.get("competitions") or []:
                    try: jogo = jogo_de_tenis(comp, torneio, bool(ev.get("major")), duplas, circuito, sexo)
                    except Exception: jogo = None                       # um jogo malformado não derruba o torneio
                    if jogo: saida.append(jogo)
            return saida
        espn_outra_fonte()
        try:
            jogos = le_espn("Tênis " + circuito, path, JANELA, converte, vazio_vale=True)
        except Exception as e:
            por_circuito[circuito] = f"ERRO ({type(e).__name__})"
            continue
        vistos, unicos = set(), []                   # cada consulta devolve o torneio inteiro: o mesmo jogo vem em vários dias
        for j in jogos:
            if j.get("_id") in vistos: continue
            vistos.add(j.get("_id")); unicos.append(j)
        evs += com_placar(unicos, path)
        por_circuito[circuito] = len(unicos)
    DIAG["fontes"]["tenis"] = por_circuito
    if por_circuito and all(str(v).startswith("ERRO") for v in por_circuito.values()):
        raise RuntimeError("ESPN tênis: nenhum circuito respondeu")
    return dedup(evs)

# =================================================================
# PLACAR — o mesmo jogo na lista geral da ESPN (soccer/all), para o app pedir
# o placar pelo id do evento em vez de adivinhar pelo nome.
# =================================================================
URL_LIGAS_ESPN = "https://site.api.espn.com/apis/site/v2/leagues/dropdown?sport=soccer&limit=1000"
RX_LIGA_FEM = re.compile(r"\.w\.|\.w$|nwsl|\.wchampions|\.weuro|\.wwc|\.wworld|\.womens|femenina|_reina", re.I)
RX_LIGA_FEM_NOME = re.compile(r"women|femin|femen|frauen|vrouwen|\bliga f\b|\breina\b|f[ée]minine", re.I)
RX_LIGA_BASE = re.compile(r"u(17|19|20|21|23)\b|under.?\d\d|youth|ncaa", re.I)

def categoria_da_liga(slug, nome):
    if RX_LIGA_FEM.search(slug) or RX_LIGA_FEM_NOME.search(nome): return "F"
    if RX_LIGA_BASE.search(slug + " " + nome): return "base"
    return "principal"

def ligas_espn():
    """id da liga -> (slug, nome) de todas as ligas de futebol da ESPN."""
    return {str(l["id"]): (str(l["slug"]), str(l.get("name") or "")) for l in espn_pega(URL_LIGAS_ESPN).get("leagues") or []
            if l.get("id") and re.fullmatch(r"[a-z0-9._-]+", str(l.get("slug") or ""))}

def lista_geral_espn():
    """Todos os jogos de futebol que a ESPN acompanha na janela, por dia de Brasília:
    {dia: [{"t", "match", "id", "q" (dia consultado), "slug", "cat", "ok" (horário firme)}]}, e os dias
    consultados (datas) que não responderam."""
    espn_outra_fonte()
    ligas = ligas_espn()
    por_dia, faltas = {}, []
    for data in DIAS_EUA:
        try: crus = espn_json("soccer/all", data, "&limit=500").get("events") or []
        except Exception: faltas.append(data); continue
        for ev in crus:
            try:
                dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
                cs = ev["competitions"][0]["competitors"]
                home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
                away = next((c for c in cs if c.get("homeAway") == "away"), cs[-1])
                lid = re.search(r"~l:(\d+)", ev.get("uid") or "")
                slug, nome = ligas.get(lid.group(1), ("", "")) if lid else ("", "")
                por_dia.setdefault(dt.date().isoformat(), []).append({
                    "t": dt.strftime("%H:%M"), "id": str(ev["id"]), "q": f"{data:%Y%m%d}", "slug": slug, "cat": categoria_da_liga(slug, nome),
                    "ok": ev["competitions"][0].get("timeValid") is not False,
                    "match": " ".join(home["team"]["displayName"].split()) + " x " + " ".join(away["team"]["displayName"].split())})
            except Exception: continue
    return por_dia, faltas

# palavras que não distinguem um time de outro ("City", "United", "Atlético"...)
GENERICAS = set("""club clube city united real sporting athletic atletico deportivo deportes football futebol futbol team esporte sport racing
union inter nacional national central olimpia america independiente universidad santos town county rovers wanderers""".split())
def palavras(nome): return {w for w in norm(nome).split() if len(w) >= 4 and w not in GENERICAS}

def casar_placar(e, candidatos):
    """O MESMO jogo de `e` em `candidatos` (lista geral da ESPN, mesmo dia), ou None. Só na mesma
    categoria (feminino não casa com masculino) e com o horário a até 1 hora. 1º: os dois times casam;
    2º: mesmo horário, um time casa e o outro tem uma palavra em comum ("NE Revolution" e "New England
    Revolution"). Havendo dúvida entre dois candidatos, não casa: melhor sem placar do que placar errado."""
    cat = categoria(e)
    if cat == "B": cat = "principal"
    longe = lambda c: abs(minutos(c["t"]) - minutos(e["t"]))
    perto = [c for c in candidatos if c["cat"] == cat and longe(c) <= 60]
    nomes = [e["match"]] + ([e["espn"]] if e.get("espn") else [])
    achados = sorted((c for c in perto if any(mesmo_jogo(c["match"], n) for n in nomes)), key=longe)
    if achados:
        return achados[0] if len(achados) == 1 or longe(achados[0]) < longe(achados[1]) else None
    p = times_de(e["match"])
    if not p: return None
    def meio(c):
        q = times_de(c["match"])
        if not q or longe(c) > 5: return False
        return any((casa_time(a, x) and palavras(b) & palavras(y)) or (casa_time(b, y) and palavras(a) & palavras(x))
                   for (a, b), (x, y) in ((p, q), (p, q[::-1])))
    meios = [c for c in perto if meio(c)]
    return meios[0] if len(meios) == 1 else None

def casar_no_dia_ao_lado(e, candidatos, sentido):
    """O mesmo jogo de `e` na lista da ESPN do dia anterior (sentido -1) ou seguinte (+1), ou None. Só
    vale com os dois times casando, na mesma categoria, com horário firme na ESPN e coerente com o da
    fonte: o mesmo horário no outro dia (até 30 min: a fonte pôs o jogo na página do dia errado) ou a até
    1 hora atravessando a meia-noite (23h30 de um dia e 00h15 do seguinte). Dois candidatos = não casa."""
    cat = categoria(e)
    if cat == "B": cat = "principal"
    nomes = [e["match"]] + ([e["espn"]] if e.get("espn") else [])
    de = minutos(e["t"])
    def coerente(c):
        para = minutos(c["t"])
        if abs(de - para) <= 30: return True
        return 0 <= (para + 1440 - de if sentido > 0 else de + 1440 - para) <= 60
    achados = [c for c in candidatos if c["cat"] == cat and c.get("ok", True) and coerente(c) and any(mesmo_jogo(c["match"], n) for n in nomes)]
    return achados[0] if len(achados) == 1 else None

def junta_canais(fica, sai):
    """`sai` é o mesmo jogo de `fica`, listado com a data errada: o que ele tem de melhor passa para o que fica."""
    sem_canal = lambda e: all("confirmar" in c["n"].lower() for c in e["ch"])
    if sem_canal(fica) and not sem_canal(sai): fica["ch"] = sai["ch"]
    elif not sem_canal(sai):
        tenho = {norm(c["n"]) for c in fica["ch"]}
        fica["ch"] += [c for c in sai["ch"] if norm(c["n"]) not in tenho and "confirmar" not in c["n"].lower()]
    if (fica.get("detail") or "—") in ("—", "Tabela oficial") and (sai.get("detail") or "—") not in ("—", "Tabela oficial"): fica["detail"] = sai["detail"]

RX_PL_FUTEBOL = re.compile(r"^soccer/[a-z0-9._-]+\|\d{8}\|[\w-]{1,24}\|[cv]$")
def data_guardada(g, dia):
    """A correção de data guardada na coleta anterior, se estiver inteira e for mesmo de um dia ao lado."""
    try:
        return (isinstance(g, dict) and abs((datetime.fromisoformat(g["date"]).date() - dia).days) == 1 and bool(re.fullmatch(r"\d\d:\d\d", g["t"]))
                and bool(RX_PL_FUTEBOL.match(g["pl"])) and isinstance(g["espn"], str))
    except Exception: return False

def acerta_datas(eventos, por_dia, guardadas=None, incompletos=frozenset()):
    """Jogo de futebol que a ESPN não tem no dia em que a fonte o listou, mas tem no dia ao lado: a fonte
    errou a data. Casos reais de 21/11/2026, os dois na página de domingo do guia principal: Pumas x
    Monterrey (o 2º guia e a ESPN diziam sábado, e o app mostrou o jogo nos dois dias) e Carabobo x
    Portuguesa (ESPN e calendário oficial da liga: sábado). Se o mesmo jogo já está na lista no dia certo,
    sai a cópia; se não está, o jogo vai para a data e o horário da ESPN (a tabela oficial desempata, como
    no horário). Se os DOIS guias listam o jogo no mesmo dia, valem os guias (duas fontes contra uma) e
    fica só o registro. `guardadas`: correções da coleta anterior, que valem enquanto a ESPN não responder
    sobre o dia (`incompletos`: dias sem resposta; None = todos).
    Devolve (mudanças, dúvidas, correções para guardar)."""
    dm = lambda iso: f"{iso[8:]}/{iso[5:7]}"
    chave = lambda e: f'{e["date"]}|{norm(e["match"])}|{categoria(e)}'
    guardadas = guardadas if isinstance(guardadas, dict) else {}
    por_id = {e["pl"].split("|")[2]: e for e in eventos if e["sport"] == "Futebol" and e.get("pl")}
    mudancas, duvidas, novas = [], [], {}
    for e in list(eventos):
        if e["sport"] != "Futebol" or e.get("pl"): continue
        k, dia, alvo = chave(e), datetime.fromisoformat(e["date"]).date(), None
        for sentido in (-1, 1):
            vizinho = (dia + timedelta(days=sentido)).isoformat()
            try: c = casar_no_dia_ao_lado(e, por_dia.get(vizinho, []), sentido)
            except Exception: c = None
            if c:
                alvo = {"date": vizinho, "t": c["t"], "espn": c["match"],
                        "pl": f'soccer/{c["slug"] or "all"}|{c["q"]}|{c["id"]}|' + ("v" if invertido(e["match"], c["match"]) else "c")}
                break
        if not alvo:
            g = guardadas.get(k)
            if data_guardada(g, dia) and (incompletos is None or g["date"] in incompletos): alvo = {x: g[x] for x in ("date", "t", "espn", "pl")}
        if not alvo: continue
        if {"guia", "guia2"} <= set(e.get("_t") or {}):
            duvidas.append(f'{e["match"]}: os dois guias listam em {dm(e["date"])}; a ESPN, em {dm(alvo["date"])} → valem os guias')
            continue
        novas[k] = alvo
        certo = por_id.get(alvo["pl"].split("|")[2])
        if certo is not None:
            junta_canais(certo, e)
            eventos.remove(e)
            mudancas.append(f'{e["match"]}: listado em {dm(e["date"])} e em {dm(certo["date"])}; a ESPN confirma {dm(certo["date"])} → sai o de {dm(e["date"])}')
        else:
            antes = f'{dm(e["date"])} {e["t"]}'
            e["date"], e["t"], e["pl"] = alvo["date"], alvo["t"], alvo["pl"]
            if not e.get("espn"): e["espn"] = alvo["espn"]
            por_id[alvo["pl"].split("|")[2]] = e
            mudancas.append(f'{e["match"]}: a fonte lista em {antes}; a ESPN, em {dm(e["date"])} {e["t"]} → vale a ESPN')
    return mudancas, duvidas, novas

def enderecos_de_placar(eventos, memoria):
    """Grava "pl" nos jogos de futebol que ainda não têm (os que não vieram das tabelas) e acerta a data
    dos que a ESPN tem no dia ao lado (acerta_datas). Se a lista geral da ESPN falhar, valem o endereço e
    a correção de data guardados na coleta anterior para o mesmo jogo."""
    chave = lambda e: f'{e["date"]}|{norm(e["match"])}|{categoria(e)}'
    guardados = memoria.get("pl") if isinstance(memoria.get("pl"), dict) else {}
    info = DIAG["amostras"]["placar"] = {}
    try:
        por_dia, faltas = lista_geral_espn()
        info["lista_geral"] = {d: len(v) for d, v in sorted(por_dia.items())}
        if faltas: info["dias_sem_resposta"] = [f"{d:%d/%m}" for d in faltas]
        # o dia consultado é o dos EUA: cobre a noite desse dia e a madrugada do seguinte em Brasília
        incompletos = {(d + timedelta(days=i)).isoformat() for d in faltas for i in (0, 1)}
    except Exception as ex:
        por_dia, incompletos = {}, None
        info["erro"] = f"{type(ex).__name__}: {ex}"[:160]
    falta = lambda dia: incompletos is None or dia in incompletos
    da_memoria = 0
    for e in eventos:
        if e["sport"] != "Futebol" or e.get("pl"): continue
        try: c = casar_placar(e, por_dia.get(e["date"], []))
        except Exception: c = None
        if c:
            e["pl"] = f'soccer/{c["slug"] or "all"}|{c["q"]}|{c["id"]}|' + ("v" if invertido(e["match"], c["match"]) else "c")
            if not e.get("espn"): e["espn"] = c["match"]
        elif falta(e["date"]) and isinstance(guardados.get(chave(e)), str) and RX_PL_FUTEBOL.match(guardados[chave(e)]):
            e["pl"] = guardados[chave(e)]; da_memoria += 1
    info["data_divergente"], duvidas, memoria["datas"] = acerta_datas(eventos, por_dia, memoria.get("datas"), incompletos)
    if duvidas: info["data_em_duvida"] = duvidas[:30]
    memoria["pl"] = {chave(e): e["pl"] for e in eventos if e["sport"] == "Futebol" and e.get("pl")}
    sem = [e for e in eventos if not e.get("pl")]
    info.update({"com": len(eventos) - len(sem), "sem": len(sem), "da_memoria": da_memoria,
                 "sem_placar": [f'{e["date"][8:]}/{e["date"][5:7]} {e["t"]} {e["match"]} [{e["league"]}]' for e in sem][:60]})
    return info

# =================================================================
LIGAS_FIXTURES = [
    ("soccer/uefa.champions",         "Champions League",     "Europa (demais)"),
    ("soccer/bra.copa_do_brazil",     "Copa do Brasil",       "Brasil"),
    ("soccer/bra.1",                  "Brasileirão Série A",  "Brasil"),
    ("soccer/bra.2",                  "Brasileirão Série B",  "Brasil"),
    ("soccer/conmebol.libertadores",  "Copa Libertadores",    "América do Sul"),
    ("soccer/conmebol.sudamericana",  "Copa Sul-Americana",   "América do Sul"),
    ("soccer/eng.1",                  "Premier League",       "Inglaterra"),
    ("soccer/esp.1",                  "La Liga",              "Espanha"),
    ("soccer/ger.1",                  "Bundesliga",           "Alemanha"),
    ("soccer/ita.1",                  "Campeonato Italiano",  "Itália"),
    ("soccer/fra.1",                  "Campeonato Francês",   "Europa (demais)"),
]
# Seleções. Com lang=pt a ESPN entrega os nomes em português ("Holanda",
# "República Tcheca"), então casam com os guias sem tabela de tradução.
# Nos amistosos só entram os jogos da Seleção Brasileira: a maioria dos outros
# não tem transmissão no Brasil e viraria card "a confirmar" para sempre — os
# que têm chegam pelos guias, já com canal.
# Para ligar depois, quando voltarem a ter jogos: soccer/uefa.euro (Eurocopa),
# soccer/conmebol.america (Copa América), soccer/fifa.world (Copa do Mundo).
ESPN_PT = "&lang=pt&region=br"
LIGAS_SELECOES = [
    ("soccer/fifa.friendly",        "Amistoso",                     "Mundial",         "Brasil"),
    ("soccer/uefa.nations",         "UEFA Nations League",          "Europa (demais)", None),
    ("soccer/fifa.worldq.conmebol", "Eliminatórias Sul-Americanas", "América do Sul",  None),
    ("soccer/uefa.euroq",           "Eliminatórias da Euro",        "Europa (demais)", None),
]
def fonte_fixtures():
    evs, por_liga = [], {}
    ligas = [(s, l, p, "", None, False) for s, l, p in LIGAS_FIXTURES] + \
            [(s, l, p, ESPN_PT, so, True) for s, l, p, so in LIGAS_SELECOES]
    for slug, liga, pais, extra, so_time, selecoes in ligas:
        def converte(ev, liga=liga, pais=pais, so_time=so_time, selecoes=selecoes):
            dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
            if dt.date() not in JANELA: return None
            comp = ev.get("competitions", [{}])[0]
            cs = comp.get("competitors", [])
            if len(cs) < 2: return None
            home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
            away = next((c for c in cs if c.get("homeAway") == "away"), cs[-1])
            hn = home.get("team", {}).get("displayName", "?")
            an = away.get("team", {}).get("displayName", "?")
            if so_time and so_time not in (hn, an): return None
            notas = comp.get("notes", [])
            detalhe = (notas[0].get("headline", "") if notas else "") or "Tabela oficial"
            jogo = evento(dt.date(), dt.strftime("%H:%M"), "Futebol", liga,
                          f"{hn} x {an}", [{"n": "Transmissão a confirmar", "y": "tv"}],
                          detail=detalhe, country=pais, v=1)
            if selecoes: jogo["sel"] = 1
            if comp.get("timeValid") is False: jogo["_tv"] = 0      # a ESPN ainda não tem o horário: não vale como fonte de horário
            jogo["_id"] = ev.get("id")
            return jogo
        try:
            jogos = com_placar(le_espn(liga, slug, JANELA, converte, extra), slug)
        except Exception as e:
            por_liga[liga] = f"ERRO ({type(e).__name__})"   # slug inválido ou ESPN fora do ar
            continue
        evs += jogos
        por_liga[liga] = len(jogos)
    DIAG["fontes"]["espn_tabelas_por_liga"] = por_liga   # mostra slug morto na hora
    return dedup(evs)


# =================================================================
# Cruzamento entre fontes
# =================================================================
# "Sub-20" e "U-21" valem em qualquer campo; "júnior", "juvenil" etc. só no nome do
# campeonato: há clubes chamados Boca Juniors, Argentinos Juniors e Junior Barranquilla.
RE_BASE = re.compile(r"\bsub.?\d{1,2}\b|\bu-?\d{2}\b", re.I)
RE_BASE_LIGA = re.compile(r"juvenil|\bjunior(es)?\b|aspirantes|\bbase\b|\bcopinha\b", re.I)
RE_TIME_B = re.compile(r"\s(B|II)$")

def categoria(e):
    """Elenco que joga: feminino, base (Sub-20...), time B ou principal.
    O mesmo confronto no mesmo dia em categorias diferentes são jogos DIFERENTES."""
    if e.get("g") == "F": return "F"
    if RE_BASE.search(sem_acento(f'{e["match"]} {e["league"]} {e.get("detail") or ""}')) \
            or RE_BASE_LIGA.search(sem_acento(e["league"])): return "base"
    if any(RE_TIME_B.search(t.strip()) for t in re.split(r"\s+x\s+", e["match"], flags=re.I)): return "B"
    return "principal"

def casa_time(a, b):
    a, b = norm(a), norm(b)
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    return difflib.SequenceMatcher(None, a, b).ratio() > 0.8

def times_de(match):
    p = re.split(r"\s+x\s+", match, flags=re.I)
    return p if len(p) == 2 else None

def mesmo_jogo(m1, m2):
    """Os DOIS times casam (direto ou invertido). Compara time a time, não a
    string inteira (evita falsos positivos como 'Holanda x Japão' ~
    'Alemanha x Curaçao' por letras coincidentes)."""
    p1, p2 = times_de(m1), times_de(m2)
    if not p1 or not p2:
        return difflib.SequenceMatcher(None, norm(m1), norm(m2)).ratio() > 0.7
    return (casa_time(p1[0], p2[0]) and casa_time(p1[1], p2[1])) or \
           (casa_time(p1[0], p2[1]) and casa_time(p1[1], p2[0]))

def invertido(meu, da_espn):
    """O primeiro time do card é o SEGUNDO da ESPN? (o placar da ESPN vem mandante x visitante)"""
    p, q = times_de(meu), times_de(da_espn)
    if not p or not q: return False
    return not (casa_time(p[0], q[0]) or casa_time(p[1], q[1])) and (casa_time(p[0], q[1]) or casa_time(p[1], q[0]))

def um_time_em_comum(m1, m2):
    p1, p2 = times_de(m1), times_de(m2)
    return bool(p1 and p2) and any(casa_time(a, b) for a in p1 for b in p2)

def paises_compativeis(a, b):
    pa, pb = a.get("country"), b.get("country")
    return pa == pb or "Mundial" in (pa, pb)      # "Mundial" = país não identificado

def casar_fontes(e, candidatos):
    """Acha em `candidatos` o registro do MESMO jogo que `e` (mesmo dia, mesmo
    esporte, mesma categoria). 1º: os dois times casam. 2º: mesmo horário + um
    time em comum + país compatível — cobre nomes que cada fonte escreve de um
    jeito ('PSG' x 'Paris Saint-Germain', 'Rennes' x 'Stade Rennais'): um time
    não joga duas partidas na mesma hora."""
    mesmos = [c for c in candidatos
              if c["date"] == e["date"] and c["sport"] == e["sport"] and categoria(c) == categoria(e)]
    for c in mesmos:
        if mesmo_jogo(c["match"], e["match"]): return c
    for c in mesmos:
        if c["t"] == e["t"] and paises_compativeis(c, e) and um_time_em_comum(c["match"], e["match"]):
            return c
    return None

def minutos(t):
    h, m = t.split(":"); return int(h) * 60 + int(m)

def complementar_fixtures(base, fixtures):
    """Cruza o guia de TV com a tabela oficial da ESPN.
    - jogo só na tabela  -> entra como card "Transmissão a confirmar";
    - jogo nos dois      -> fica o do guia (tem canais); se o horário confere,
      ganha selo v=2 e o nome oficial da liga; guarda os nomes da ESPN em
      "espn" (é com eles que o app casa o placar ao vivo)."""
    divergencias = []
    for fx in fixtures:
        achou = casar_fontes(fx, base)
        if achou is None:
            if fx.get("_tv", 1): fx["_t"] = {"espn": fx["t"]}
            base.append(fx)
            continue
        achou["espn"] = fx["match"]
        if fx.get("pl"): achou["pl"] = fx["pl"][:-1] + ("v" if invertido(achou["match"], fx["match"]) else "c")
        if fx.get("_tv", 1): achou.setdefault("_t", {})["espn"] = fx["t"]      # o horário da tabela entra na conta (acerta_horarios)
        if fx.get("sel"): achou["sel"] = 1
        confere = abs(minutos(achou["t"]) - minutos(fx["t"])) <= 30
        if confere:
            achou["v"] = 2
        elif len(divergencias) < 5:
            divergencias.append(f'{fx["date"]} {achou["match"]}: guia {achou["t"]} x ESPN {fx["t"]}')
        if confere or achou.get("league") in ("Futebol", "", None):
            # uma liga, um nome: "Brasileirão", "LaLiga", "Ligue 1" do guia viram
            # o nome da tabela oficial — é com ele que os chips do app filtram
            achou["league"], achou["country"] = fx["league"], fx["country"]
            if (achou.get("detail") or "—") == "—" and fx.get("detail") != "Tabela oficial":
                achou["detail"] = fx["detail"]
    if divergencias:
        DIAG["amostras"]["horario_guia_x_espn"] = divergencias
    return base

# =================================================================
# Horário: o que vale quando as fontes discordam
# =================================================================
ORDEM_HORARIO = ("espn", "guia", "guia2")           # sem maioria: tabela oficial, guia principal, 2º guia
NOME_FONTE = {"guia": "guia", "guia2": "2º guia", "espn": "tabela"}

def acerta_horarios(eventos):
    """Cada jogo traz em "_t" o horário que cada fonte deu ({"guia": "17:00",
    "guia2": "17:30", "espn": "17:30"}). Vale o horário com mais fontes; no
    empate, o da tabela oficial, depois o do guia principal. O selo v=2 passa a
    dizer exatamente isso: duas fontes deram ESTE horário. Devolve a lista das
    divergências (texto), que vai para o log e para o diagnóstico; "_t" e "_tv"
    saem do evento."""
    divergencias = []
    for e in eventos:
        ts = e.pop("_t", None)
        e.pop("_tv", None)
        if not isinstance(ts, dict) or not ts: continue
        votos = {}
        for t in ts.values(): votos[t] = votos.get(t, 0) + 1
        maior = max(votos.values())
        empatados = [t for t, n in votos.items() if n == maior]
        escolhido = empatados[0] if len(empatados) == 1 else next(ts[f] for f in ORDEM_HORARIO if ts.get(f) in empatados)
        if len(votos) > 1:
            divergencias.append(f'{e["date"][8:]}/{e["date"][5:7]} {e["match"]}: '
                                + ", ".join(f"{NOME_FONTE[f]} {ts[f]}" for f in ("guia", "guia2", "espn") if f in ts) + f" → vale {escolhido}")
        e["t"] = escolhido
        e["v"] = 2 if votos[escolhido] >= 2 else 1
    return divergencias

# =================================================================
# Seleções (filtro "Seleções" do app: datas FIFA, Euro, Copa América...)
# =================================================================
# Países como os guias escrevem, com as variações comuns. Mônaco fica de fora
# de propósito: não tem seleção na FIFA e é nome de clube.
PAISES = {norm(p) for p in """
Argentina; Bolívia; Brasil; Chile; Colômbia; Equador; Paraguai; Peru; Uruguai; Venezuela;
Albânia; Alemanha; Andorra; Armênia; Áustria; Azerbaijão; Belarus; Bielorrússia; Bélgica; Bósnia;
Bósnia e Herzegovina; Bósnia-Herzegovina; Bulgária; Cazaquistão; Chipre; Croácia; Dinamarca; Escócia;
Eslováquia; Eslovênia; Espanha; Estônia; Finlândia; França; Geórgia; Gibraltar; Grécia; Holanda;
Países Baixos; Hungria; Ilhas Faroé; Ilhas Faroe; Ilhas Féroe; Inglaterra; Irlanda; República da Irlanda;
Irlanda do Norte; Islândia; Israel; Itália; Kosovo; Letônia; Liechtenstein; Lituânia; Luxemburgo;
Macedônia do Norte; Malta; Moldávia; Moldova; Montenegro; Noruega; País de Gales; Gales; Polônia; Portugal;
República Tcheca; Rep. Tcheca; Tchéquia; Chéquia; Romênia; Rússia; San Marino; Sérvia; Suécia; Suíça;
Turquia; Türkiye; Ucrânia;
Anguilla; Antígua e Barbuda; Aruba; Bahamas; Barbados; Belize; Bermudas; Bermuda; Ilhas Virgens Britânicas;
Canadá; Ilhas Cayman; Ilhas Caimã; Costa Rica; Cuba; Curaçao; Dominica; República Dominicana; El Salvador;
Granada; Guatemala; Guiana; Haiti; Honduras; Jamaica; México; Montserrat; Nicarágua; Panamá; Porto Rico;
São Cristóvão e Névis; Saint Kitts e Nevis; Santa Lúcia; São Vicente e Granadinas; Suriname;
Trinidad e Tobago; Ilhas Turcas e Caicos; Turks e Caicos; Estados Unidos; EUA; Ilhas Virgens Americanas;
Bonaire; Guiana Francesa; Guadalupe; Martinica; Saint Martin; São Martinho; Sint Maarten;
Afeganistão; Arábia Saudita; Austrália; Bahrein; Barein; Bangladesh; Butão; Brunei; Camboja; Catar; Qatar;
China; Coreia do Norte; Coreia do Sul; Emirados Árabes Unidos; Emirados Árabes; Filipinas; Guam; Hong Kong;
Iêmen; Índia; Indonésia; Irã; Iraque; Japão; Jordânia; Kuwait; Kuait; Laos; Líbano; Macau; Malásia; Maldivas;
Mongólia; Mianmar; Myanmar; Nepal; Omã; Paquistão; Palestina; Quirguistão; Singapura; Cingapura; Síria;
Sri Lanka; Tadjiquistão; Tajiquistão; Tailândia; Taiwan; Taipé Chinesa; China Taipé; Timor-Leste;
Turcomenistão; Uzbequistão; Vietnã;
África do Sul; Angola; Argélia; Benin; Botsuana; Burkina Faso; Burquina Faso; Burundi; Cabo Verde; Camarões;
Chade; Comores; Congo; República do Congo; RD Congo; República Democrática do Congo; Congo DR;
Costa do Marfim; Djibuti; Egito; Eritreia; Essuatíni; Suazilândia; Etiópia; Gabão; Gâmbia; Gana; Guiné;
Guiné-Bissau; Guiné Equatorial; Lesoto; Libéria; Líbia; Madagascar; Madagáscar; Malawi; Malaui; Mali;
Marrocos; Maurício; Ilhas Maurício; Mauritânia; Moçambique; Namíbia; Níger; Nigéria; Quênia;
República Centro-Africana; Ruanda; São Tomé e Príncipe; Senegal; Seicheles; Seychelles; Serra Leoa;
Somália; Sudão; Sudão do Sul; Tanzânia; Togo; Tunísia; Uganda; Zâmbia; Zimbábue; Zimbabwe;
Fiji; Ilhas Cook; Ilhas Salomão; Nova Caledônia; Nova Zelândia; Papua-Nova Guiné; Papua Nova Guiné; Samoa;
Samoa Americana; Taiti; Tonga; Vanuatu
""".replace("\n", " ").split(";") if p.strip()}

# Competições que só seleções jogam (olha só o NOME da competição: "eliminatória"
# também é fase de copa de clubes, e fase fica no detalhe).
RX_LIGA_SELECOES = re.compile(
    r"nations league|(das|de) nacoes|eliminatoria|qualificatoria|copa do mundo|mundial (sub|feminin)|"
    r"eurocopa|\beuro\b|copa america|copa ouro|gold cup|copa da asia|copa arabe|copa do golfo|finalissima|"
    r"olimpi|(sul.?americano|europeu|africano|asiatico) sub|fifa series|\bselec", re.I)
# Fora delas, só vale como jogo de seleções o confronto ENTRE DOIS PAÍSES em
# amistoso/torneio ("Amistoso: Lituânia x Suriname"). Assim "Copa do Rei: Andorra
# x Granada" e "Amistoso: Colo-Colo x Racing" continuam sendo jogos de clubes.
RX_AMISTOSO = re.compile(r"amistos|friendl|torneio|\bcup\b", re.I)
RX_SUFIXO_SELECAO = re.compile(r"\(f\)|\bfeminin[oa]\b|\bolimpic[oa]\b|\b(sub|u)[- ]?\d{2}\b", re.I)

def eh_selecao(e):
    if e["sport"] != "Futebol": return False
    liga = sem_acento(e["league"]).lower()
    if re.search(r"\bclubes\b", liga): return False           # Mundial / Copa do Mundo de Clubes
    if RX_LIGA_SELECOES.search(liga): return True
    if not RX_AMISTOSO.search(liga) and liga != "futebol": return False     # "futebol" = guia sem competição
    times = times_de(e["match"])
    return bool(times) and all(norm(RX_SUFIXO_SELECAO.sub(" ", sem_acento(t))) in PAISES for t in times)

# ----------------------------------------------------------------- duplicatas
def dedup_por_jogo(evs):
    """Rede de segurança: o MESMO jogo 2x no dia (mesmo esporte e categoria, os
    dois times casam). Fica o de maior prioridade: canais reais > selo v."""
    def prioridade(e):
        tem_canal = any("confirmar" not in c["n"].lower() for c in e["ch"])
        return (2 if tem_canal else 0) + e.get("v", 1)
    saida = []
    for e in sorted(evs, key=prioridade, reverse=True):
        dup = next((s for s in saida
                    if s["date"] == e["date"] and s["sport"] == e["sport"]
                    and categoria(s) == categoria(e)
                    and mesmo_jogo(s["match"], e["match"])), None)
        if dup is None:
            saida.append(e)
    return saida

def dedup(evs):
    saida, vistos = [], set()
    for e in evs:
        k = (e["date"], e["t"], norm(e["match"]))
        if k in vistos: continue
        vistos.add(k); saida.append(e)
    return saida

# =================================================================
# Alarme: fonte sem dados em coletas seguidas
# =================================================================
def avaliar_saude(anterior, problemas, agora):
    """problemas = [(chave, texto, horas)]: toca quando o problema já dura `horas`
    E foi visto em pelo menos duas coletas (uma falha isolada não toca: o guia
    já teve soluços de minutos). Conta em horas, não em coletas, porque o GitHub
    não cumpre o agendamento à risca. anterior = estado gravado pela coleta
    passada (diagnostico.json -> "saude"). Devolve (estado novo, texto do alarme
    ou None). Enquanto o problema durar, repete o aviso a cada 24h."""
    def quando(txt):
        try: return datetime.fromisoformat(txt)
        except Exception: return None
    antes, abertos, textos = anterior.get("abertos") or {}, {}, []
    for chave, texto, horas in problemas:
        p = dict(antes.get(chave) or {})
        desde = quando(p.get("desde")) or agora
        p.update(desde=desde.isoformat(timespec="minutes"), vezes=int(p.get("vezes") or 0) + 1, texto=texto)
        if p["vezes"] >= 2 and agora - desde >= timedelta(hours=horas):
            ultimo = quando(p.get("avisado"))
            if not ultimo or agora - ultimo >= timedelta(hours=24):
                textos.append(f"{texto} — desde {desde:%d/%m %H:%M}, {p['vezes']} coletas seguidas")
                p["avisado"] = agora.isoformat(timespec="minutes")
        abertos[chave] = p
    return {"abertos": abertos}, (" | ".join(textos) or None)

# =================================================================
# Memória por dia dos guias de TV
# =================================================================
MEMORIA = "memoria_guias.json"

def minuto_de_agora():
    n = datetime.now(BRT); return n.hour * 60 + n.minute

def com_memoria(nome, lidos, memoria, agora):
    """Junta o que o guia `nome` listou agora com a memória dos dias que ele
    deixou de listar. `lidos` = jogos desta coleta; DIAS_LIDOS[nome] = dias cujo
    título a página trouxe. Regra, para cada dia da janela:
      - a página trouxe o título do dia -> vale a leitura de agora (substitui a memória).
        Só no dia de HOJE há um acréscimo: jogo que já começou e saiu da página continua
        (os guias tiram o que acabou; o app precisa dele para mostrar o placar final);
      - não trouxe -> vale a última leitura daquele dia, mais algum jogo que tenha
        caído nele agora (o da madrugada, listado no fim do dia anterior).
    Atualiza `memoria[nome]` e devolve (jogos, dias que vieram da memória)."""
    copia = lambda x: json.loads(json.dumps(x))
    antiga, nova, da_memoria, por_dia = memoria.get(nome) or {}, {}, [], {}
    for e in lidos: por_dia.setdefault(e["date"], []).append(e)
    for dia in (d.isoformat() for d in JANELA):
        if dia in DIAS_LIDOS[nome]:
            de_agora, ficam = copia(por_dia.get(dia, [])), []
            if dia == HOJE.isoformat():
                minuto = minuto_de_agora()
                for e in (antiga.get(dia) or {}).get("jogos") or []:
                    try:
                        if minutos(e["t"]) <= minuto and not any(categoria(x) == categoria(e) and mesmo_jogo(x["match"], e["match"]) for x in de_agora):
                            ficam.append(copia(e))
                    except Exception: pass                    # registro estragado na memória: não fica
            if ficam: DIAG["amostras"].setdefault("encerrados_mantidos", {})[nome] = [f'{e["t"]} {e["match"]}' for e in ficam][:40]
            nova[dia] = {"lido_em": agora, "jogos": de_agora + ficam}
            continue
        guardado = antiga.get(dia) or {}
        de_agora = copia(por_dia.get(dia, []))
        ja = {(e["t"], norm(e["match"])) for e in de_agora}
        so_memoria = [e for e in (guardado.get("jogos") or []) if (e["t"], norm(e["match"])) not in ja]
        if so_memoria: da_memoria.append(f'{dia} (lido em {guardado.get("lido_em", "?")})')
        if de_agora or so_memoria:
            nova[dia] = {"lido_em": guardado.get("lido_em", agora) if so_memoria else agora, "jogos": de_agora + so_memoria}
    memoria[nome] = nova
    # cópias: o cruzamento com as outras fontes altera os jogos, e a memória guarda o guia puro
    return [copia(e) for dia in sorted(nova) for e in nova[dia]["jogos"]], da_memoria

# =================================================================
# FUTEBOL — segundo guia de TV: goal.com/br ("Futebol na TV", página fixa,
# atualizada todo dia, com uma tabela por dia)
# =================================================================
URL_GOAL = "https://www.goal.com/br/listas/futebol-programacao-jogos-tv-aberta-fechada-onde-assistir-online-app/bltc0a7361374657315"
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
# Só vale como título de dia o texto que COMEÇA por "Jogos de <dia da semana>":
# data citada em outro lugar da página (título, "atualizado em...") não muda o dia.
RX_DIA_GOAL = re.compile(r"^\W*jogos de (?:hoje\W+)?(segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?"
                         r"\W+(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")", re.I)

def fonte_goal():
    """Tabelas por dia: JOGO | CAMPEONATO | HORÁRIO | ONDE PASSA?  (título do dia
    acima de cada tabela: "Jogos de quarta, 11 de novembro de 2026")."""
    r = requests.get(URL_GOAL, headers=HEADERS, timeout=30)
    info = DIAG["amostras"]["goal"] = {"http": r.status_code, "bytes": len(r.text)}
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    info["tabelas"] = len(soup.find_all("table"))
    evs, data_atual, dias, nao_lidas = [], None, [], []
    for no in soup.descendants:
        if isinstance(no, str):
            m = RX_DIA_GOAL.search(sem_acento(str(no))) if len(no) <= 120 else None
            if m:
                opcoes = []
                for ano in (HOJE.year - 1, HOJE.year, HOJE.year + 1):
                    try: opcoes.append(datetime(ano, MESES[m.group(3).lower()], int(m.group(2))).date())
                    except ValueError: pass
                if opcoes:
                    data_atual = min(opcoes, key=lambda d: abs((d - HOJE).days))
                    DIAS_LIDOS["goal"].add(data_atual.isoformat())
                    if not dias or dias[-1]["data"] != data_atual.isoformat():
                        dias.append({"titulo": " ".join(str(no).split())[:60], "data": data_atual.isoformat(), "jogos": 0})
            continue
        if getattr(no, "name", None) != "table" or not data_atual: continue
        col = {"jogo": 0, "liga": 1, "hora": 2, "onde": 3}
        for tr in no.find_all("tr"):
            cel = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all(["td", "th"])]
            maius = [sem_acento(c).upper() for c in cel]
            if "JOGO" in maius:                       # cabeçalho da tabela: confere a ordem das colunas
                for i, c in enumerate(maius):
                    if c == "JOGO": col["jogo"] = i
                    elif c.startswith("CAMPEONATO"): col["liga"] = i
                    elif c.startswith("HOR"): col["hora"] = i
                    elif c.startswith("ONDE"): col["onde"] = i
                continue
            if len(cel) <= max(col.values()): continue
            times = re.split(r"\s+x\s+", cel[col["jogo"]], flags=re.I)
            mh = re.search(r"(\d{1,2})\s*[h:]\s*(\d{2})?", cel[col["hora"]])
            if len(times) != 2 or not mh:
                if len(nao_lidas) < 5: nao_lidas.append(" | ".join(cel)[:140])
                continue
            fem = any("(f)" in t.lower() for t in times)
            t1, t2 = [re.sub(r"\s*\(f\)", "", t, flags=re.I).strip() for t in times]
            ml = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", cel[col["liga"]])
            liga, detalhe = (ml.group(1), ml.group(2)) if ml else (cel[col["liga"]], "—")
            if re.search(r"divis", sem_acento(detalhe), re.I):      # "(segunda divisão)" é parte do nome da liga
                liga, detalhe = cel[col["liga"]], "—"
            canais = [canal(c) for c in separa_canais_mantos(cel[col["onde"]])]
            evs.append(evento(data_atual, f"{int(mh.group(1)):02d}:{mh.group(2) or '00'}", "Futebol", liga or "Futebol",
                              f"{t1} x {t2}", canais or [{"n": "Transmissão a confirmar", "y": "tv"}],
                              detail=detalhe, g="F" if fem else None))
            dias[-1]["jogos"] += 1
    info.update({"dias": dias, "linhas_nao_lidas": nao_lidas})
    return dedup(evs)

def juntar_guias(principal, segundo):
    """Une os dois guias de TV; o principal manda. Jogo que está nos dois fica
    com os dados do principal (se ele veio sem canal, usa os do segundo) e, com
    o horário conferindo, ganha selo v=2. Jogo que só o segundo traz entra na
    lista. Nada é removido."""
    sem_canal = lambda e: all("confirmar" in c["n"].lower() for c in e["ch"])
    extras, divergencias = [], []
    for ev in segundo:
        achou = casar_fontes(ev, principal)
        if achou is None:
            extras.append(ev)
            continue
        achou.setdefault("_t", {}).update(ev.get("_t") or {})       # o horário do 2º guia entra na conta (acerta_horarios)
        if abs(minutos(achou["t"]) - minutos(ev["t"])) <= 30:
            achou["v"] = 2
        elif len(divergencias) < 5:
            divergencias.append(f'{ev["date"]} {achou["match"]}: principal {achou["t"]} x segundo {ev["t"]}')
        if sem_canal(achou) and not sem_canal(ev):
            achou["ch"] = ev["ch"]
    DIAG["amostras"]["guias"] = {"nos_dois": len(segundo) - len(extras),
                                 "so_no_segundo": [f'{e["date"][5:]} {e["t"]} {e["match"]} [{e["league"]}]' for e in extras][:15],
                                 "horario_diferente": divergencias}
    return principal + extras

TEMPLATE, PAGINA = "onde-assistir.html", "index.html"

def publicar_pagina(eventos, agora):
    """index.html = template (onde-assistir.html) + dados. O template nunca é
    alterado pelo robô: editar o app é editar só o template."""
    html = Path(TEMPLATE).read_text(encoding="utf-8")
    # "</" escapado: texto raspado de terceiros nunca fecha o <script> da página
    dados = json.dumps(eventos, ensure_ascii=False, indent=1).replace("</", "<\\/")
    bloco = "/*DB-START*/\nconst DB = " + dados + ";\n/*DB-END*/"
    # função no lugar de string: o re.sub não reinterpreta barras do JSON
    html, n1 = re.subn(r"/\*DB-START\*/.*?/\*DB-END\*/", lambda _: bloco, html, count=1, flags=re.S)
    html, n2 = re.subn(r"const GENERATED_AT = .*?;", lambda _: f"const GENERATED_AT = '{agora}';", html, count=1)
    if n1 != 1 or n2 != 1:
        raise RuntimeError("template sem os marcadores DB-START/DB-END ou GENERATED_AT")
    Path(PAGINA).write_text(html, encoding="utf-8")

def main():
    falhas = {}
    def roda(nome, fn, log):
        try:
            r = fn(); log.append(f"  ✓ {nome}: {len(r)} evento(s)"); return r
        except Exception as e:
            falhas[nome] = f"{type(e).__name__}: {e}"[:160]
            log.append(f"  ✗ {nome} indisponível ({type(e).__name__}: {e})"); return []

    agora_dt = datetime.now(BRT)
    log = [f"Coleta {agora_dt:%d/%m %H:%M} BRT — janela {JANELA[0]} a {JANELA[-1]}"]
    try:        # estado do alarme deixado pela coleta anterior
        saude_anterior = json.loads(Path("diagnostico.json").read_text(encoding="utf-8")).get("saude") or {}
    except Exception:
        saude_anterior = {}
    try:        # última leitura de cada dia dos guias — só vale a gravada por esta mesma versão
        memoria = json.loads(Path(MEMORIA).read_text(encoding="utf-8"))
        assert isinstance(memoria, dict) and MEMORIA_DESDE <= str(memoria.get("build") or "") <= BUILD
    except Exception:
        memoria = {}
    memoria["build"] = DIAG["build"] = BUILD
    for dias in DIAS_LIDOS.values(): dias.clear()
    ESPN.update(memoria=memoria, agora=agora_dt, faltas=[], sem_resposta=set(), seguidas=0, cortada=False, cortou=False)
    agora_txt = agora_dt.strftime("%d/%m %H:%M")

    def le_guia(rotulo, nome, fn):
        """Lê um guia e completa com a memória. Devolve (jogos lidos agora, jogos
        que valem, dias que vieram da memória)."""
        lidos = roda(rotulo, fn, log)
        if rotulo in falhas: DIAS_LIDOS[nome].clear()    # leitura interrompida não apaga a memória de dia nenhum
        try:
            jogos, da_memoria = com_memoria(nome, lidos, memoria, agora_txt)
        except Exception as e:      # memória estragada: joga fora e recomeça da leitura de agora
            log.append(f"  ✗ memória de {rotulo} descartada ({type(e).__name__}: {e})")
            memoria[nome] = {}
            try: jogos, da_memoria = com_memoria(nome, lidos, memoria, agora_txt)
            except Exception: jogos, da_memoria = lidos, []
        return lidos, jogos, da_memoria

    lidos1, guia, mem1 = le_guia("mantosdofutebol", "mantos", fonte_mantos)
    lidos2, segundo, mem2 = le_guia("goal (2º guia)", "goal", fonte_goal)
    Path(MEMORIA).write_text(json.dumps(memoria, ensure_ascii=False, indent=1), encoding="utf-8")
    for nome, dias in (("mantosdofutebol", mem1), ("goal (2º guia)", mem2)):
        if dias: log.append(f"  ↺ {nome}: a página não listou {', '.join(dias)} — vale a última leitura")
    for nome, jogos in (DIAG["amostras"].get("encerrados_mantidos") or {}).items():
        log.append(f"  ↺ {nome}: {len(jogos)} jogo(s) de hoje já saíram da página e seguem na lista (encerrados)")
    DIAG["amostras"]["memoria"] = {"mantos": mem1, "goal": mem2}
    for e in guia: e["_t"] = {"guia": e["t"]}              # o horário que cada fonte deu (ver acerta_horarios)
    for e in segundo: e["_t"] = {"guia2": e["t"]}
    eventos = juntar_guias(guia, segundo)

    fixtures = roda("ESPN tabelas (futebol, 7 dias)", fonte_fixtures, log)
    eventos = complementar_fixtures(eventos, fixtures)
    divergentes = acerta_horarios(eventos)
    DIAG["amostras"]["horario_divergente"] = divergentes[:30]
    if divergentes:
        log.append(f"  ! horário: as fontes discordam em {len(divergentes)} jogo(s) — " + "; ".join(divergentes[:4]))

    eventos += roda("ESPN NBA", fonte_nba, log)
    eventos += roda("ESPN NFL", fonte_nfl, log)
    eventos += roda("ESPN tênis", fonte_tenis, log)
    # memória de novo, agora com a leitura da ESPN; e o que a ESPN deixou de responder nesta coleta
    Path(MEMORIA).write_text(json.dumps(memoria, ensure_ascii=False, indent=1), encoding="utf-8")
    ESPN["faltas"].sort(key=lambda f: "com 0 jogo(s)" in f)          # primeiro as que envolvem jogos
    if ESPN["cortou"]: log.append("  ✗ ESPN parou de responder numa das fontes: as consultas seguintes dela nesta coleta não foram feitas")
    for falta in ESPN["faltas"][:12]: log.append(f"  ↺ ESPN {falta}")
    if len(ESPN["faltas"]) > 12: log.append(f'  ↺ ESPN: mais {len(ESPN["faltas"]) - 12} consulta(s) na mesma situação')
    DIAG["amostras"]["espn_faltas"] = ESPN["faltas"][:60]
    eventos = dedup_por_jogo(eventos)
    eventos = dedup(eventos)
    eventos = [e for e in eventos if JANELA[0].isoformat() <= e["date"] <= JANELA[-1].isoformat()]
    # Sanitização final: competição/fase que escape para o lugar do canal não passa.
    # Canais vindos de URL explícita (YouTube) e o FIFA+ são sempre legítimos.
    PSEUDO = re.compile(r"copa|fifa|rodada|grupo|s[ée]rie|amistos|libertadores|\b20\d\d\b", re.I)
    for e in eventos:
        e["ch"] = [c for c in e["ch"]
                   if c.get("u") or c["n"].strip().upper() == "FIFA+" or not PSEUDO.search(sem_acento(c["n"]))]
        unicos = {}                               # o mesmo canal duas vezes no jogo: fica um, de preferência o que tem link
        for c in e["ch"]:
            k = norm(c["n"]) or c["n"]
            if k not in unicos or (c.get("u") and not unicos[k].get("u")): unicos[k] = c
        e["ch"] = list(unicos.values())
        if not e["ch"]: e["ch"] = [{"n": "Transmissão a confirmar", "y": "tv"}]
        if eh_selecao(e): e["sel"] = 1            # filtro "Seleções" do app
    eventos.sort(key=lambda e: (e["date"], e["t"]))
    try:        # endereço do placar de cada jogo; se falhar, os jogos saem do mesmo jeito, só sem "pl"
        placar = enderecos_de_placar(eventos, memoria)
        log.append(f'  ✓ placar: {placar["com"]} jogo(s) com endereço na ESPN, {placar["sem"]} sem'
                   + (f' — lista geral sem resposta em {", ".join(placar["dias_sem_resposta"])}' if placar.get("dias_sem_resposta") else "")
                   + (f' — lista geral indisponível ({placar["erro"]})' if placar.get("erro") else ""))
        if placar.get("data_divergente"):
            log.append(f'  ! data: {len(placar["data_divergente"])} jogo(s) com a data divergente — ' + "; ".join(placar["data_divergente"][:3]))
        if placar.get("data_em_duvida"):
            log.append(f'  ! data: {len(placar["data_em_duvida"])} jogo(s) em que os dois guias e a ESPN discordam do dia — ' + "; ".join(placar["data_em_duvida"][:3]))
    except Exception as e:
        log.append(f"  ✗ placar: endereços não gravados ({type(e).__name__}: {e})")
    eventos[:] = [e for e in eventos if JANELA[0].isoformat() <= e["date"] <= JANELA[-1].isoformat()]       # a correção de data pode ter tirado um jogo da janela
    eventos.sort(key=lambda e: (e["date"], e["t"]))
    for e in eventos:
        for k in [k for k in e if k.startswith("_")]: del e[k]      # anotações internas nunca vão para o app
    Path(MEMORIA).write_text(json.dumps(memoria, ensure_ascii=False, indent=1), encoding="utf-8")

    Path("eventos.json").write_text(json.dumps(eventos, ensure_ascii=False, indent=1), encoding="utf-8")
    log.append(f"TOTAL: {len(eventos)} eventos → eventos.json")

    try:
        publicar_pagina(eventos, agora_txt)
        log.append(f"{PAGINA} gerado a partir de {TEMPLATE}.")
    except Exception as e:
        log.append(f"  ✗ {PAGINA} NÃO atualizado ({type(e).__name__}: {e})")

    # ---- alarme: os dados acima já foram gravados; aqui só se decide se avisa
    problemas = []          # (chave, texto, horas com o problema até tocar)
    if "mantosdofutebol" in falhas:
        problemas.append(("guia", f"guia de TV fora do ar ({falhas['mantosdofutebol']})", 2))
    elif not lidos1:
        problemas.append(("guia", "guia de TV abriu, mas nenhum jogo foi lido (o formato da página pode ter mudado)", 2))
    if "goal (2º guia)" in falhas or not lidos2:            # menos urgente: espera um dia inteiro
        problemas.append(("guia2", "2º guia de TV (Goal) sem jogos: " + falhas.get("goal (2º guia)", "nenhum jogo lido"), 24))
    por_liga = DIAG["fontes"].get("espn_tabelas_por_liga") or {}
    fora = ESPN["sem_resposta"]             # fontes da ESPN em que nenhuma consulta respondeu
    if {"NBA", "NFL"} <= fora and por_liga and all(liga in fora for liga in por_liga):
        problemas.append(("espn", "ESPN fora do ar (sem NBA, NFL e tabelas)", 2))
    elif ESPN["faltas"]:                    # os jogos seguem no ar pela memória; avisa se continuar
        problemas.append(("espn_dias", f'ESPN falhou em {len(ESPN["faltas"])} consulta(s): ' + "; ".join(ESPN["faltas"][:2]), 6))
    DIAG["saude"], alerta = avaliar_saude(saude_anterior, problemas, agora_dt)
    if alerta:
        log.append(f"ALARME: {alerta}")
        saida = os.environ.get("GITHUB_OUTPUT")      # lido pelo último passo do workflow
        if saida:
            with open(saida, "a", encoding="utf-8") as f:
                f.write("alerta=" + " ".join(alerta.split())[:400] + "\n")
    elif problemas:
        log.append("  ! " + " | ".join(t for _, t, _ in problemas) +
                   (" — alarme já enviado" if any(p.get("avisado") for p in DIAG["saude"]["abertos"].values())
                    else " — o alarme toca se continuar assim"))

    DIAG["fontes"]["log"] = log
    Path("diagnostico.json").write_text(json.dumps(DIAG, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log))

if __name__ == "__main__":
    main()
