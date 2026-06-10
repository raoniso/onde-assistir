# -*- coding: utf-8 -*-
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
                   ("Cazétv","CazéTV"),("Caze Tv","CazéTV"),("Xsports","XSports"),
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

            m2 = re.fullmatch(r"(.+?) \1 (.+?) \2", resto)
            partida = f"{m2.group(1)} x {m2.group(3)}" if m2 else resto
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

def complementar_fixtures(base, fixtures):
    """Tabela só preenche jogos que NENHUMA fonte com canais trouxe."""
    for fx in fixtures:
        existe = any(b["date"] == fx["date"] and b["sport"] == "Futebol" and
                     difflib.SequenceMatcher(None, norm(b["match"]), norm(fx["match"])).ratio() > 0.55
                     for b in base)
        if not existe:
            base.append(fx)
    return base

# ----------------------------------------------------------------- merge
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
            if b["date"] == ev["date"] and b["t"] == ev["t"] and \
               difflib.SequenceMatcher(None, norm(b["match"]), norm(ev["match"])).ratio() > 0.55:
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

    eventos += roda("ESPN NBA", fonte_nba, log)
    eventos += roda("ESPN F1", fonte_f1, log)
    eventos += roda("ESPN Tênis", fonte_tenis, log)
    eventos += roda("extras.json", fonte_extras, log)

    eventos = dedup(eventos)
    eventos = [e for e in eventos if e["date"] >= HOJE.isoformat()]
    for e in eventos:
        if not e["ch"]: e["ch"] = [{"n": "A confirmar", "y": "tv"}]
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
    print("\n".join(log))

if __name__ == "__main__":
    main()
