# -*- coding: utf-8 -*-
# build: 2026-07-02-s (fonte UOL + diagnóstico observável)
"""
ONDE.ASSISTIR — Pipeline de dados v3 (multi-esporte, multi-fonte, autônomo)
===========================================================================
Roda sozinho no GitHub Actions (atualizar.yml). Nenhuma curadoria manual.

FONTES (cada uma protegida por try/except — uma falhar não derruba o resto):
  FUTEBOL .... futebolnatv.com.br (principal) ✕ mantosdofutebol.com.br (cruzamento)
               Jogos confirmados nas duas fontes recebem selo "v":2 (✓✓ no app)
  BASQUETE ... API pública da ESPN (NBA) — canais fixos do contrato Brasil:
               Prime Video + NBA League Pass
  FÓRMULA 1 .. API pública da ESPN (F1) — direitos Brasil 2026-2028: Grupo Globo
               (SporTV/Globoplay todas as etapas; F1 TV Pro alternativa)
  TÊNIS ...... API pública da ESPN (ATP/WTA) — cards por torneio/dia;
               canais Brasil: ESPN + Disney+
  EXTRAS ..... extras.json no repositório — formato idêntico ao eventos.json,
               para esportes ainda sem fonte automática (surf, ciclismo,
               corrida de rua). É código/dado versionado, não chat.

DATAS SÃO ABSOLUTAS ("date": "2026-06-11"). O app só exibe um evento na aba
do dia exato — dado antigo nunca "escorrega" para hoje.
"""

import difflib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OndeAssistir/3.0)"}
BRT = timezone(timedelta(hours=-3))
HOJE = datetime.now(BRT).date()
JANELA = [HOJE + timedelta(days=i) for i in range(7)]  # baseline semanal

# ----------------------------------------------------------------- helpers
def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", sem_acento(s).lower()).strip()

# Palavras que NUNCA fazem parte do nome de um jogo (canais e ruído da fonte)
LIXO_TITULO = re.compile(
    r"\b(fim de jogo|ao vivo|globo ?play|globoplay|globo|sbt|sportv|sporttv|"
    r"ge ?tv|caz[eé] ?tv|caz[eé]|n ?sports|nsports|xsports|premiere|disney\+?|"
    r"espn ?\d?|band ?sports|band|record|youtube|tv aberta|streaming|"
    r"\d+\s*x\s*\d+|\d{1,3}\+?\d?['’]|intervalo|prorroga\w*|p[êe]naltis)\b", re.I)
def limpa_titulo(s):
    s = re.sub(r"^\s*\d{1,3}\+?\d?['’]\s*", "", s)  # cronômetro no início (40')
    s = re.sub(r"\(.*?\)", " ", s)          # remove parênteses
    s = LIXO_TITULO.sub(" ", s)               # remove canais/placar/lixo
    s = re.sub(r"\s+", " ", s).strip(" -–—")
    return s.strip()

CANAIS_FREE = {"GLOBO","SBT","BAND","RECORD","REDETV","TV BRASIL","CAZE TV","CAZETV",
               "XSPORTS","YOUTUBE","CANAL GOAT","GOAT","METROPOLES","TV CULTURA",
               "SPORTYNET","GE TV","GETV","NSPORTS","N SPORTS"}
CANAIS_STREAM = {"DISNEY+","MAX","HBO MAX","PARAMOUNT+","PRIME VIDEO","AMAZON PRIME VIDEO",
                 "ONEFOOTBALL","FANATIZ","ZAPPING","UOL PLAY","GLOBOPLAY","PREMIERE FC",
                 "PREMIERE","DAZN","APPLE TV+","NOSSO FUTEBOL+","NBA LEAGUE PASS",
                 "F1 TV PRO","WSL+"}
COMPOSTOS = CANAIS_FREE | CANAIS_STREAM | {"SPORTV 2","SPORTV 3","ESPN 2","ESPN 3",
                                           "ESPN 4","ESPN 5","ESPN 6","BAND SPORTS"}

def canal(nome):
    chave = sem_acento(nome).upper().strip()
    tipo = "free" if chave in CANAIS_FREE else "stream" if chave in CANAIS_STREAM else "tv"
    b = nome.title()
    for a, dep in [("Fc","FC"),("Espn","ESPN"),("Sbt","SBT"),("Sportv","SporTV"),
                   ("Cazétv","CazéTV"),("Caze Tv","CazéTV"),("Cazé Tv","CazéTV"),("Xsports","XSports"),
                   ("Hbo","HBO"),("Tnt","TNT"),("Nba","NBA"),("Wsl","WSL"),
                   ("Ge Tv","GE TV"),("Getv","GE TV"),("Nsports","N Sports")]:
        b = b.replace(a, dep)
    return {"n": b, "y": tipo}

# Saídas alinhadas com os chips fixos do app
PAIS_POR_LIGA = [
    (r"brasileir|copa do brasil|paulista|carioca|mineiro|gaucho|copinha", "Brasil"),
    (r"libertadores|sul.?americana|recopa|argentin|chilen|uruguai|colombian", "América do Sul"),
    (r"premier league|championship|ingl|fa cup|carabao", "Inglaterra"),
    (r"la liga|espanhol|copa do rei", "Espanha"),
    (r"italian|coppa", "Itália"),
    (r"alem|bundesliga|dfb", "Alemanha"),
    (r"mls|nwsl|nba|wnba|nfl|mlb|\beua\b|canad", "EUA/Canadá"),
    (r"franc|ligue 1|portug|holand|eredivisie|champions|europa league|conference|uefa|euro|nations", "Europa (demais)"),
    (r"amistos|mundial|copa do mundo|fifa|sele|atp|wta|wsl", "Mundial"),
]
def pais_da_liga(liga):
    alvo = sem_acento(liga).lower()
    for padrao, pais in PAIS_POR_LIGA:
        if re.search(padrao, alvo): return pais
    return "Mundial"

def normalizar_liga(liga):
    if re.search(r"copa do mundo|world cup", sem_acento(liga).lower()):
        return "Copa do Mundo FIFA"
    return liga

def genero(*textos):
    alvo = sem_acento(" ".join(textos)).lower()
    return "F" if re.search(r"feminin|\bfem\b|\(w\)|wta|wnba", alvo) else "M"

def evento(date, t, sport, league, match, ch, detail="—", g=None, country=None, v=1):
    return {"date": date.isoformat() if hasattr(date, "isoformat") else date,
            "t": t, "sport": sport, "g": g or genero(league, match),
            "country": country or pais_da_liga(league),
            "league": normalizar_liga(league), "detail": detail,
            "match": match, "ch": ch, "v": v}

TRAD = {"Brazil":"Brasil","Germany":"Alemanha","France":"França","Spain":"Espanha",
 "Netherlands":"Holanda","Belgium":"Bélgica","Uruguay":"Uruguai","England":"Inglaterra",
 "Argentina":"Argentina","Portugal":"Portugal","Mexico":"México","South Africa":"África do Sul",
 "South Korea":"Coreia do Sul","Czechia":"República Tcheca","Czech Republic":"República Tcheca",
 "United States":"Estados Unidos","USA":"Estados Unidos","Canada":"Canadá",
 "Bosnia and Herzegovina":"Bósnia e Herzegovina","Scotland":"Escócia","Morocco":"Marrocos",
 "Switzerland":"Suíça","Qatar":"Catar","Japan":"Japão","Croatia":"Croácia","Italy":"Itália",
 "Norway":"Noruega","Sweden":"Suécia","Poland":"Polônia","Austria":"Áustria","Turkey":"Turquia",
 "Australia":"Austrália","Ecuador":"Equador","Colombia":"Colômbia","Paraguay":"Paraguai",
 "Ivory Coast":"Costa do Marfim","Côte d'Ivoire":"Costa do Marfim","Egypt":"Egito",
 "Senegal":"Senegal","Ghana":"Gana","Tunisia":"Tunísia","Algeria":"Argélia","Panama":"Panamá",
 "Costa Rica":"Costa Rica","Haiti":"Haiti","Jordan":"Jordânia","Uzbekistan":"Uzbequistão",
 "Iran":"Irã","Saudi Arabia":"Arábia Saudita","New Zealand":"Nova Zelândia","Cape Verde":"Cabo Verde",
 "Curacao":"Curaçao","Curaçao":"Curaçao","Ukraine":"Ucrânia","Denmark":"Dinamarca",
 "Ireland":"Irlanda","Northern Ireland":"Irlanda do Norte","Wales":"País de Gales",
 "North Macedonia":"Macedônia do Norte","Slovakia":"Eslováquia","Romania":"Romênia","Kosovo":"Kosovo"}
def trad(n): return TRAD.get(n, n)

RE_HORA = re.compile(r"\b(\d{1,2}[:h]\d{2})\b")

# =================================================================
# FUTEBOL — fonte A: futebolnatv.com.br
# =================================================================
def fonte_futebolnatv():
    urls = {HOJE: "https://www.futebolnatv.com.br/jogos-hoje",
            HOJE + timedelta(days=1): "https://www.futebolnatv.com.br/jogos-amanha"}
    evs = []
    for data, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        vistos = set()
        for a in soup.select('a[href*="/aovivo/"]'):
            texto = " ".join(a.get_text(" ", strip=True).split())
            m = RE_HORA.search(texto)
            if not m: continue
            hora = m.group(1).replace("h", ":").zfill(5)
            antes, depois = texto[:m.start()].strip(), texto[m.end():].strip()

            liga, detalhe = antes, ""
            if " - " in antes:
                lf, detalhe = antes.rsplit(" - ", 1)
                meio = len(lf) // 2
                liga = lf[:meio].strip() if lf[:meio].strip() == lf[meio:].strip() else lf.strip()

            tokens = depois.split()
            ctk = []
            while tokens and re.fullmatch(r"[A-Z0-9ÉÊÁ+]{2,}", tokens[-1]):
                ctk.insert(0, tokens.pop())
            resto = " ".join(tokens)
            canais, i = [], 0
            while i < len(ctk):
                par = " ".join(ctk[i:i+2])
                if sem_acento(par).upper() in COMPOSTOS: canais.append(par); i += 2
                else: canais.append(ctk[i]); i += 1
            canais = [c for c in canais
                      if not re.search(r"copa|fifa|rodada|grupo|s[ée]rie|\b20\d\d\b",
                                       sem_acento(c), re.I)]

            # nº de palavras de cada time vem do slug da URL: "time-a-x-time-b-HASH"
            partida = None
            slug = (a.get("href") or "").rsplit("/", 1)[-1].replace(".html", "")
            m_slug = re.match(r"(.+?)-x-(.+?)-[0-9a-f]{6,}$", slug)
            palavras = resto.split()
            if m_slug:
                n1 = len(m_slug.group(1).split("-")); n2 = len(m_slug.group(2).split("-"))
                if len(palavras) == n1 + n2:                # texto sem duplicação
                    partida = " ".join(palavras[:n1]) + " x " + " ".join(palavras[n1:])
                elif len(palavras) == 2 * (n1 + n2):        # texto duplicado (alt da imagem)
                    partida = " ".join(palavras[:n1]) + " x " + " ".join(palavras[2*n1:2*n1+n2])
            if not partida:                                 # último recurso: dedup por regex
                m2 = re.fullmatch(r"(.+?) \1 (.+?) \2", resto)
                partida = f"{m2.group(1)} x {m2.group(3)}" if m2 else resto
            partida = limpa_titulo(partida)
            # validação: precisa sobrar "Time x Time" plausível, senão descarta
            if not re.search(r".+\s+x\s+.+", partida, re.I): continue
            if not partida or (data, hora, norm(partida)) in vistos: continue
            vistos.add((data, hora, norm(partida)))
            evs.append(evento(data, hora, "Futebol", liga, partida,
                              [canal(c) for c in canais] or [{"n":"A confirmar","y":"tv"}],
                              detail=detalhe or "—"))
    return evs

# =================================================================
# FUTEBOL — fonte B (cruzamento): mantosdofutebol.com.br
# =================================================================
def fonte_mantos():
    urls = {HOJE: "https://mantosdofutebol.com.br/guia-de-jogos-tv-hoje-ao-vivo/",
            HOJE + timedelta(days=1): "https://mantosdofutebol.com.br/jogos-de-amanha-tv/"}
    rx = re.compile(r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)\s*[–\-—]\s*([^(\n]+)(?:\(([^)]+)\))?")
    evs = []
    for data, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        corpo = BeautifulSoup(r.text, "html.parser").select_one("article")
        if not corpo: continue
        for linha in corpo.get_text("\n").split("\n"):
            m = rx.search(linha.strip())
            if not m: continue
            h, mi, t1, t2, ctxt, comp = m.groups()
            canais = [c.strip() for c in re.split(r"[,/]| e ", ctxt) if c.strip()]
            liga = (comp or "").strip() or "Futebol"
            evs.append(evento(data, f"{int(h):02d}:{mi}", "Futebol", liga,
                              f"{t1.strip()} x {t2.strip()}", [canal(c) for c in canais]))
    return evs

# =================================================================
# ESPN API (pública, JSON) — NBA, F1, Tênis
# Canais por modalidade = direitos de transmissão vigentes no Brasil.
# Se os direitos mudarem, ajustar SOMENTE o dicionário abaixo.
# =================================================================
CANAIS_MODALIDADE = {
    "nba":    [canal("Prime Video"), canal("NBA League Pass")],
    "f1":     [canal("SporTV"), canal("Globoplay"), canal("F1 TV Pro")],
    "tenis":  [canal("ESPN"), canal("Disney+")],
}

def espn_json(path, data):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={data:%Y%m%d}"
    return requests.get(url, headers=HEADERS, timeout=30).json()

def fonte_nba():
    evs = []
    for data in JANELA:
        for ev in espn_json("basketball/nba", data).get("events", []):
            dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
            if dt.date() not in JANELA: continue
            comp = ev.get("competitions", [{}])[0]
            times = [c.get("team", {}).get("displayName", "?") for c in comp.get("competitors", [])]
            if len(times) < 2: continue
            notas = comp.get("notes", [])
            detalhe = notas[0].get("headline", "Temporada NBA") if notas else "Temporada NBA"
            evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Basquete", "NBA",
                              f"{times[1]} x {times[0]}", CANAIS_MODALIDADE["nba"],
                              detail=detalhe, country="EUA/Canadá", v=2))
    return dedup(evs)

def fonte_f1():
    evs = []
    for data in JANELA:
        for ev in espn_json("racing/f1", data).get("events", []):
            gp = ev.get("name", "Fórmula 1")
            for comp in ev.get("competitions", []):
                try:
                    dt = datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(BRT)
                except Exception:
                    continue
                if dt.date() not in JANELA: continue
                sessao = comp.get("type", {}).get("text") or comp.get("type", {}).get("abbreviation", "Sessão")
                evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Fórmula 1", "Fórmula 1",
                                  gp, CANAIS_MODALIDADE["f1"], detail=sessao,
                                  country="Mundial", g="M", v=2))
    return dedup(evs)

def fonte_tenis():
    """Um card por torneio relevante por dia (Grand Slams, Masters, Finals)."""
    RELEVANTES = r"grand slam|masters|finals|open|wimbledon|roland"
    evs = []
    for path, circ in [("tennis/atp", "ATP"), ("tennis/wta", "WTA")]:
        for data in JANELA:
            try:
                j = espn_json(path, data)
            except Exception:
                continue
            for ev in j.get("events", []):
                nome = ev.get("name", "")
                if not re.search(RELEVANTES, nome, re.I): continue
                try:
                    dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
                except Exception:
                    continue
                if dt.date() not in JANELA: continue
                evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Tênis", f"Circuito {circ}",
                                  nome, CANAIS_MODALIDADE["tenis"],
                                  detail="Dia de jogos", country="Mundial",
                                  g="F" if circ == "WTA" else "M", v=1))
    return dedup(evs)

# =================================================================
# EXTRAS — extras.json no repositório (surf, ciclismo, corrida de rua…)
# Mesmo formato do eventos.json. Eventos com data passada são ignorados.
# =================================================================
def fonte_extras():
    p = Path("extras.json")
    if not p.exists(): return []
    dados = json.loads(p.read_text(encoding="utf-8"))
    return [e for e in dados
            if e.get("date", "") >= HOJE.isoformat()
            and "EXEMPLO" not in e.get("detail", "").upper()]

# =================================================================
# TABELAS OFICIAIS (7 dias) — ESPN soccer por liga. Garante que o JOGO
# apareça mesmo sem canal confirmado ("Transmissão a confirmar").
# Quando futebolnatv confirmar os canais (~48h antes), o jogo da fonte
# com canais prevalece e a tabela só preenche o que faltar.
# =================================================================
LIGAS_FIXTURES = [
    ("soccer/fifa.world",             "Copa do Mundo FIFA",   "Mundial"),
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
def fonte_fixtures():
    evs = []
    for slug, liga, pais in LIGAS_FIXTURES:
        for data in JANELA:
            try:
                j = espn_json(slug, data)
            except Exception:
                continue
            for ev in j.get("events", []):
                try:
                    dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(BRT)
                except Exception:
                    continue
                if dt.date() not in JANELA: continue
                comp = ev.get("competitions", [{}])[0]
                cs = comp.get("competitors", [])
                if len(cs) < 2: continue
                home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
                away = next((c for c in cs if c.get("homeAway") == "away"), cs[-1])
                hn = trad(home.get("team", {}).get("displayName", "?"))
                an = trad(away.get("team", {}).get("displayName", "?"))
                notas = comp.get("notes", [])
                detalhe = (notas[0].get("headline", "") if notas else "") or "Tabela oficial"
                evs.append(evento(dt.date(), dt.strftime("%H:%M"), "Futebol", liga,
                                  f"{hn} x {an}", [{"n": "Transmissão a confirmar", "y": "tv"}],
                                  detail=detalhe, country=pais, v=1))
    return dedup(evs)


# ---------------------------------------------------------------------------
# FALLBACK OFICIAL DA COPA — datas/horários FIFA (Brasília). Garante que todo
# jogo do Mundial apareça mesmo se a ESPN bloquear o robô (data centers às vezes
# recebem 403). Canais são preenchidos por aplica_canais_copa(). Atualizar
# conforme a FIFA confirma os classificados das repescagens.
# ---------------------------------------------------------------------------
COPA_FALLBACK = [
 ("2026-06-11","16:00","México x África do Sul"),("2026-06-11","23:00","Coreia do Sul x Rep. Tcheca"),
 ("2026-06-12","16:00","Canadá x Bósnia e Herzegovina"),("2026-06-12","22:00","Estados Unidos x Paraguai"),
 ("2026-06-13","01:00","Austrália x Türkiye"),("2026-06-13","16:00","Catar x Suíça"),
 ("2026-06-13","19:00","Brasil x Marrocos"),("2026-06-13","22:00","Haiti x Escócia"),
 ("2026-06-14","14:00","Alemanha x Curaçao"),("2026-06-14","17:00","Holanda x Japão"),
 ("2026-06-14","20:00","Costa do Marfim x Equador"),("2026-06-14","23:00","Suécia x Tunísia"),
 ("2026-06-15","13:00","Espanha x Cabo Verde"),("2026-06-15","16:00","Bélgica x Egito"),
 ("2026-06-15","19:00","Arábia Saudita x Uruguai"),("2026-06-15","22:00","Irã x Nova Zelândia"),
 ("2026-06-16","16:00","França x Senegal"),("2026-06-16","19:00","Iraque x Noruega"),
 ("2026-06-16","22:00","Argentina x Argélia"),("2026-06-17","01:00","Áustria x Jordânia"),
 ("2026-06-17","14:00","Portugal x RD Congo"),("2026-06-17","17:00","Inglaterra x Croácia"),
 ("2026-06-17","20:00","Gana x Panamá"),("2026-06-17","23:00","Uzbequistão x Colômbia"),
 ("2026-06-18","13:00","Rep. Tcheca x África do Sul"),("2026-06-18","16:00","Suíça x Bósnia e Herzegovina"),
 ("2026-06-18","19:00","Canadá x Catar"),("2026-06-18","22:00","México x Coreia do Sul"),
 ("2026-06-19","16:00","Estados Unidos x Austrália"),("2026-06-19","19:00","Escócia x Marrocos"),
 ("2026-06-19","21:30","Brasil x Haiti"),("2026-06-20","00:00","Türkiye x Paraguai"),
 ("2026-06-20","14:00","Holanda x Suécia"),("2026-06-20","17:00","Alemanha x Costa do Marfim"),
 ("2026-06-20","21:00","Equador x Curaçao"),("2026-06-21","01:00","Tunísia x Japão"),
 ("2026-06-21","13:00","Espanha x Arábia Saudita"),("2026-06-21","16:00","Bélgica x Irã"),
 ("2026-06-21","19:00","Uruguai x Cabo Verde"),("2026-06-21","22:00","Nova Zelândia x Egito"),
 ("2026-06-22","14:00","Argentina x Áustria"),("2026-06-22","18:00","França x Iraque"),
 ("2026-06-22","21:00","Noruega x Senegal"),("2026-06-23","00:00","Jordânia x Argélia"),
 ("2026-06-23","14:00","Portugal x Uzbequistão"),("2026-06-23","17:00","Inglaterra x Gana"),
 ("2026-06-23","20:00","Panamá x Croácia"),("2026-06-23","23:00","Colômbia x RD Congo"),
 ("2026-06-24","16:00","Suíça x Canadá"),("2026-06-24","16:00","Bósnia e Herzegovina x Catar"),
 ("2026-06-24","19:00","Escócia x Brasil"),("2026-06-24","19:00","Marrocos x Haiti"),
 ("2026-06-24","22:00","Rep. Tcheca x México"),("2026-06-24","22:00","África do Sul x Coreia do Sul"),
 ("2026-06-25","17:00","Curaçao x Costa do Marfim"),("2026-06-25","17:00","Equador x Alemanha"),
 ("2026-06-25","20:00","Japão x Suécia"),("2026-06-25","20:00","Tunísia x Holanda"),
 ("2026-06-25","23:00","Türkiye x Estados Unidos"),("2026-06-25","23:00","Paraguai x Austrália"),
 ("2026-06-26","16:00","Noruega x França"),("2026-06-26","16:00","Senegal x Iraque"),
 ("2026-06-26","21:00","Cabo Verde x Arábia Saudita"),("2026-06-26","21:00","Uruguai x Espanha"),
 ("2026-06-27","00:00","Egito x Irã"),("2026-06-27","00:00","Nova Zelândia x Bélgica"),
 ("2026-06-27","18:00","Panamá x Inglaterra"),("2026-06-27","18:00","Croácia x Gana"),
 ("2026-06-27","20:30","Colômbia x Portugal"),("2026-06-27","20:30","RD Congo x Uzbequistão"),
 ("2026-06-27","23:00","Argélia x Áustria"),("2026-06-27","23:00","Jordânia x Argentina"),
 # ===== OITAVAS DE FINAL (verificado SportRadar) =====
 ("2026-06-28","16:00","África do Sul x Canadá"),
 ("2026-06-29","14:00","Brasil x Japão"),
 ("2026-06-29","17:30","Alemanha x Paraguai"),
 ("2026-06-29","22:00","Holanda x Marrocos"),
 ("2026-06-30","14:00","Costa do Marfim x Noruega"),
 ("2026-06-30","18:00","França x Suécia"),
 ("2026-06-30","23:00","México x Equador"),
 ("2026-07-01","13:00","Inglaterra x RD Congo"),
 ("2026-07-01","17:00","Bélgica x Senegal"),
 ("2026-07-01","21:00","Estados Unidos x Bósnia e Herzegovina"),
 ("2026-07-02","16:00","Espanha x Áustria"),
 ("2026-07-02","20:00","Portugal x Croácia"),
 ("2026-07-03","00:00","Suíça x Argélia"),
 ("2026-07-03","15:00","Austrália x Egito"),
 ("2026-07-03","19:00","Argentina x Cabo Verde"),
 ("2026-07-03","22:30","Colômbia x Gana"),
 # ===== QUARTAS DE FINAL (verificado SportRadar) =====
 ("2026-07-04","14:00","Canadá x Marrocos"),
 ("2026-07-04","18:00","Paraguai x França"),
 ("2026-07-05","17:00","Brasil x Noruega"),
 ("2026-07-05","21:00","México x Inglaterra"),
]



# =================================================================
# UOL — página da Copa com jogos e ONDE VAI PASSAR por jogo.
# Parser defensivo: procura blocos "Time x Time" com canais próximos.
# Grava amostra bruta no diagnóstico para calibragem com dados reais.
# =================================================================
DIAG = {"fontes": {}, "amostras": {}}

def fonte_uol_copa():
    """Estrutura real do UOL (capturada via diagnóstico):
         Hoje, 16h00
         Globo SBT Nsports
         ge TV Sportv
         CazéTV
         Espanha
         Áustria
       => horário -> linhas de canais -> Time A -> Time B"""
    url = "https://www.uol.com.br/esporte/futebol/campeonatos/copa-do-mundo/"
    r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    linhas = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]

    # diagnóstico de contexto (mantido para detectar mudanças de layout)
    CANAIS_RX = r"(globo|sportv|caz[eé]|sbt|ge ?tv|globoplay|n ?sports|fifa\+)"
    blocos, achados = [], 0
    for i, l in enumerate(linhas):
        if re.search(CANAIS_RX, l, re.I):
            blocos.append(" || ".join(linhas[max(0,i-6):i+3])[:400]); achados += 1
            if achados >= 3: break
    DIAG["amostras"]["uol"] = blocos

    def linha_de_canais(l):
        toks = sem_acento(l).lower().replace("ge tv","getv").split()
        conhecidos = {"globo","sbt","nsports","getv","sportv","cazetv","globoplay","fifa+"}
        return bool(toks) and all(t in conhecidos for t in toks)

    def extrai_canais(l):
        s = re.sub(r"ge ?tv", "GETV", l, flags=re.I)
        out = []
        for t in s.split():
            k = sem_acento(t).lower()
            nome = {"getv":"GE TV","nsports":"N Sports","cazetv":"CazéTV","sbt":"SBT",
                    "globo":"Globo","sportv":"SporTV","globoplay":"Globoplay"}.get(k)
            if nome: out.append({"n":nome,"y":"free" if nome in ("Globo","SBT","CazéTV","GE TV") else ("stream" if nome=="Globoplay" else "tv")})
        return out

    RX_HORA = re.compile(r"^(Hoje|Amanh[ãa]|\d{2}/\d{2})[ ,]*(\d{1,2})h(\d{2})", re.I)
    evs = []
    i = 0
    while i < len(linhas):
        m = RX_HORA.match(linhas[i])
        if not m:
            i += 1; continue
        quando, hh, mm = m.groups()
        j, canais = i + 1, []
        while j < len(linhas) and linha_de_canais(linhas[j]):
            canais += extrai_canais(linhas[j]); j += 1
        # os 2 próximos itens não-canais são os times
        if canais and j + 1 < len(linhas):
            t1, t2 = linhas[j], linhas[j+1]
            if 2 < len(t1) < 35 and 2 < len(t2) < 35 and not RX_HORA.match(t1):
                # dedup canais
                vistos, cs = set(), []
                for c in canais:
                    if c["n"] in vistos: continue
                    vistos.add(c["n"]); cs.append(c)
                evs.append({"match": f"{t1} x {t2}", "t": f"{int(hh):02d}:{mm}", "ch": cs})
        i = j + 2 if canais else i + 1
    return evs


def enriquecer_canais_uol(eventos, uol):
    """UOL lista os transmissores POR JOGO — é autoridade de canais da Copa.
    Onde casar, os canais do UOL substituem tudo."""
    aplicados = 0
    for e in eventos:
        if e.get("league") != "Copa do Mundo FIFA": continue
        for u in uol:
            if mesmo_jogo(e["match"], u["match"]) and u["ch"]:
                e["ch"] = u["ch"]; aplicados += 1
                break
    DIAG["fontes"]["uol_aplicados"] = aplicados
    return eventos

def fonte_copa_fallback():
    evs = []
    for date, hora, match in COPA_FALLBACK:
        if date not in [d.isoformat() for d in JANELA]: continue
        fase = ("Fase de grupos" if date <= "2026-06-27" else
                "Oitavas de final" if date <= "2026-07-03" else
                "Quartas de final" if date <= "2026-07-05" else
                "Semifinal" if date <= "2026-07-11" else "Final")
        evs.append(evento(date, hora, "Futebol", "Copa do Mundo FIFA", match,
                          [{"n":"Transmissão a confirmar","y":"tv"}],
                          detail=fase, country="Mundial", g="M", v=1))
    return evs

def mesmo_jogo(m1, m2):
    """Compara TIME A TIME, não a string inteira (evita falsos positivos como
    'Holanda x Japão' ~ 'Alemanha x Curaçao' por letras coincidentes)."""
    p1 = re.split(r"\s+x\s+", m1, flags=re.I)
    p2 = re.split(r"\s+x\s+", m2, flags=re.I)
    if len(p1) != 2 or len(p2) != 2:
        return difflib.SequenceMatcher(None, norm(m1), norm(m2)).ratio() > 0.7
    def casa(a, b):
        a, b = norm(a), norm(b)
        if not a or not b: return False
        if a == b or a in b or b in a: return True
        return difflib.SequenceMatcher(None, a, b).ratio() > 0.8
    # mesmo jogo se ambos os times casam (direto ou invertido)
    return (casa(p1[0], p2[0]) and casa(p1[1], p2[1])) or \
           (casa(p1[0], p2[1]) and casa(p1[1], p2[0]))

def complementar_fixtures(base, fixtures):
    """Tabela/fallback só preenche jogos que NENHUMA fonte já trouxe."""
    for fx in fixtures:
        existe = any(b["date"] == fx["date"] and b["sport"] == "Futebol" and
                     mesmo_jogo(b["match"], fx["match"])
                     for b in base)
        if not existe:
            base.append(fx)
    return base

# ----------------------------------------------------------------- merge
def dedup_por_jogo(evs):
    """Remove o MESMO jogo aparecendo 2x no dia (fontes com horário/grafia
    divergentes). Mantém o de maior prioridade: tem canais reais > tem placar(v) > scraping."""
    def prioridade(e):
        tem_canal = any("confirmar" not in c["n"].lower() for c in e["ch"])
        return (2 if tem_canal else 0) + e.get("v", 1)
    saida = []
    for e in sorted(evs, key=prioridade, reverse=True):
        dup = next((s for s in saida
                    if s["date"] == e["date"] and s["sport"] == e["sport"]
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

def mesclar_futebol(base, extra):
    for ev in extra:
        achou = None
        for b in base:
            if b["date"] == ev["date"] and b["t"] == ev["t"] and mesmo_jogo(b["match"], ev["match"]):
                achou = b; break
        if achou:
            nomes = {norm(c["n"]) for c in achou["ch"]}
            achou["ch"] += [c for c in ev["ch"] if norm(c["n"]) not in nomes]
            achou["v"] = 2
            if achou["league"] in ("Futebol", "—") and ev["league"] not in ("Futebol", "—"):
                achou["league"], achou["country"] = ev["league"], ev["country"]
        # IMPORTANTE: fonte B é apenas CRUZAMENTO. Evento que só existe nela
        # é descartado — evita "ligas" fantasmas geradas por erro de parsing.
    return base

# ----------------------------------------------------------------- main
CANAIS_COPA_BASE = [
    {"n": "Globo", "y": "free"}, {"n": "SporTV", "y": "tv"},
    {"n": "CazéTV", "y": "free"}, {"n": "GE TV", "y": "free"},
    {"n": "Globoplay", "y": "stream"},
]
CANAIS_COPA_SBT = CANAIS_COPA_BASE + [{"n": "SBT", "y": "free"}, {"n": "N Sports", "y": "tv"}]

# Detentores REAIS de direitos da Copa no Brasil (whitelist). O canal de cada
# jogo específico vem do SCRAPING (por jogo); esta lista só remove ruído
# (Prime/ESPN/Disney etc., que NÃO transmitem a Copa aqui).
CANAIS_VALIDOS_COPA = {"globo","sportv","sportv 2","sportv 3","cazetv","caze tv",
                       "ge tv","getv","globoplay","sbt","n sports","nsports","fifa+"}

def aplica_canais_copa(eventos):
    """Canais POR JOGO: mantém o que o scraping trouxe (filtrado à whitelist de
    detentores reais). Sem canal confirmado -> CazéTV (transmite todos os 104
    jogos por contrato público) + selo 'a confirmar' para os demais."""
    for e in eventos:
        if e.get("league") != "Copa do Mundo FIFA": continue
        validos = [c for c in e["ch"]
                   if sem_acento(c["n"]).lower().strip() in CANAIS_VALIDOS_COPA]
        if validos:
            e["ch"] = validos
        else:
            e["ch"] = [{"n": "CazéTV", "y": "free"},
                       {"n": "Demais canais a confirmar", "y": "tv"}]
    return eventos

def main():
    eventos = []

    def roda(nome, fn, log):
        try:
            r = fn(); log.append(f"  ✓ {nome}: {len(r)} evento(s)"); return r
        except Exception as e:
            log.append(f"  ✗ {nome} indisponível ({type(e).__name__}: {e})"); return []

    log = [f"Coleta {datetime.now(BRT):%d/%m %H:%M} BRT — janela {JANELA[0]} a {JANELA[-1]}"]

    fut_a = roda("futebolnatv", fonte_futebolnatv, log)
    fut_b = roda("mantosdofutebol", fonte_mantos, log)
    eventos += mesclar_futebol(fut_a, fut_b) if fut_a else fut_b

    fixtures = roda("ESPN tabelas (futebol, 7 dias)", fonte_fixtures, log)
    eventos = complementar_fixtures(eventos, fixtures)

    # COPA = AUTORIDADE: a tabela oficial manda. Guardamos os canais que as
    # fontes trouxeram, removemos os jogos de Copa das fontes (podem ter confronto
    # errado por tabela desatualizada) e inserimos a tabela oficial, reaplicando
    # os canais por casamento de times.
    canais_fonte = [e for e in eventos if e.get("league") == "Copa do Mundo FIFA"]
    eventos = [e for e in eventos if e.get("league") != "Copa do Mundo FIFA"]
    copa_fb = roda("Tabela oficial da Copa (autoridade)", fonte_copa_fallback, log)
    for jogo in copa_fb:
        for cf in canais_fonte:
            if cf["date"] == jogo["date"] and mesmo_jogo(cf["match"], jogo["match"]):
                reais = [c for c in cf["ch"] if "confirmar" not in c["n"].lower()]
                if reais: jogo["ch"] = reais
                break
    eventos += copa_fb

    eventos += roda("ESPN NBA", fonte_nba, log)
    eventos += roda("ESPN F1", fonte_f1, log)
    eventos += roda("ESPN Tênis", fonte_tenis, log)
    eventos += roda("extras.json", fonte_extras, log)

    uol = roda("UOL (canais por jogo)", fonte_uol_copa, log)
    eventos = aplica_canais_copa(eventos)
    if uol: eventos = enriquecer_canais_uol(eventos, uol)
    eventos = dedup_por_jogo(eventos)
    eventos = dedup(eventos)
    eventos = [e for e in eventos if e["date"] >= HOJE.isoformat()]
    # Sanitização final: nenhum pseudo-canal passa, venha de qual fonte vier
    PSEUDO = re.compile(r"copa|fifa|rodada|grupo|s[ée]rie|amistos|libertadores|\b20\d\d\b", re.I)
    for e in eventos:
        e["ch"] = [c for c in e["ch"] if not PSEUDO.search(sem_acento(c["n"]))]
        if not e["ch"]: e["ch"] = [{"n": "Transmissão a confirmar", "y": "tv"}]
    eventos.sort(key=lambda e: (e["date"], e["t"]))

    Path("eventos.json").write_text(json.dumps(eventos, ensure_ascii=False, indent=1), encoding="utf-8")
    log.append(f"TOTAL: {len(eventos)} eventos → eventos.json")

    agora = datetime.now(BRT).strftime("%d/%m %H:%M")
    for nome in ("index.html", "onde-assistir.html"):
        p = Path(nome)
        if p.exists():
            html = p.read_text(encoding="utf-8")
            bloco = "/*DB-START*/\nconst DB = " + json.dumps(eventos, ensure_ascii=False, indent=1) + ";\n/*DB-END*/"
            html = re.sub(r"/\*DB-START\*/.*?/\*DB-END\*/", bloco, html, flags=re.S)
            html = re.sub(r"const GENERATED_AT = .*?;", f"const GENERATED_AT = '{agora}';", html)
            p.write_text(html, encoding="utf-8")
            log.append(f"{nome} atualizado.")
    DIAG["fontes"]["log"] = log
    Path("diagnostico.json").write_text(json.dumps(DIAG, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log))

if __name__ == "__main__":
    main()
