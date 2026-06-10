# -*- coding: utf-8 -*-
"""
ONDE.ASSISTIR — Atualizador v2 (multi-fonte)
=============================================
Cruza DUAS fontes de "onde assistir":
  A) futebolnatv.com.br        (fonte principal, estruturada)
  B) mantosdofutebol.com.br    (fonte secundária, cruzamento)

Jogos encontrados nas duas fontes ganham selo ✓✓ ("v": 2) e a UNIÃO
dos canais informados. Se a fonte B falhar (mudança de layout), o
script segue só com a A — nunca quebra.

Saídas:
  - eventos.json                          (consumido pelo app via fetch)
  - index.html / onde-assistir.html       (dados injetados, se o arquivo existir)

Roda no Colab ou automaticamente via GitHub Actions (atualizar.yml).
"""

import difflib
import json
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OndeAssistir/2.0)"}
BRT = timezone(timedelta(hours=-3))

# ----------------------------------------------------------------- helpers
def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", sem_acento(s).lower()).strip()

CANAIS_FREE = {"GLOBO","SBT","BAND","RECORD","REDETV","TV BRASIL","CAZE TV","CAZETV",
               "XSPORTS","YOUTUBE","CANAL GOAT","GOAT","METROPOLES","TV CULTURA","SPORTYNET"}
CANAIS_STREAM = {"DISNEY+","MAX","HBO MAX","PARAMOUNT+","PRIME VIDEO","AMAZON PRIME VIDEO",
                 "ONEFOOTBALL","FANATIZ","ZAPPING","NSPORTS","UOL PLAY","GLOBOPLAY",
                 "PREMIERE FC","PREMIERE","DAZN","APPLE TV+","NOSSO FUTEBOL+"}
COMPOSTOS = CANAIS_FREE | CANAIS_STREAM | {"SPORTV 2","SPORTV 3","ESPN 2","ESPN 3",
                                           "ESPN 4","ESPN 5","ESPN 6","BAND SPORTS"}

def classificar_canal(nome):
    chave = sem_acento(nome).upper().strip()
    tipo = "free" if chave in CANAIS_FREE else "stream" if chave in CANAIS_STREAM else "tv"
    bonito = nome.title().replace("Fc","FC").replace("Espn","ESPN").replace("Sbt","SBT")
    bonito = bonito.replace("Sportv","SporTV").replace("Cazétv","CazéTV").replace("Caze Tv","CazéTV")
    bonito = bonito.replace("Xsports","XSports").replace("Hbo","HBO").replace("Tnt","TNT")
    return {"n": bonito, "y": tipo}

PAIS_POR_LIGA = [
    (r"brasileir|copa do brasil|paulista|carioca|mineiro|gaucho|copinha", "Brasil"),
    (r"libertadores|sul.?americana|recopa|argentin|chilen|uruguai|colombian", "América do Sul"),
    (r"premier league|championship|ingl|fa cup|carabao", "Inglaterra"),
    (r"la liga|espanhol|copa do rei", "Espanha"),
    (r"italian|coppa", "Itália"),
    (r"alem|bundesliga|dfb", "Alemanha"),
    (r"franc|ligue 1", "França"),
    (r"champions|europa league|conference|uefa|euro|nations", "Europa"),
    (r"mls|nwsl|\beua\b", "EUA"),
    (r"amistos|mundial|copa do mundo|fifa|sele", "Mundial"),
]
def normalizar_liga(liga):
    if re.search(r"copa do mundo|world cup", sem_acento(liga).lower()):
        return "Copa do Mundo FIFA"
    return liga

def pais_da_liga(liga):
    alvo = sem_acento(liga).lower()
    for padrao, pais in PAIS_POR_LIGA:
        if re.search(padrao, alvo): return pais
    return "Outros"

def genero(*textos):
    alvo = sem_acento(" ".join(textos)).lower()
    return "F" if re.search(r"feminin|\bfem\b|\(w\)", alvo) else "M"

RE_HORA = re.compile(r"\b(\d{1,2}[:h]\d{2})\b")

# ----------------------------------------------------------- FONTE A
def fonte_futebolnatv():
    urls = {0: "https://www.futebolnatv.com.br/jogos-hoje",
            1: "https://www.futebolnatv.com.br/jogos-amanha"}
    eventos = []
    for dia, url in urls.items():
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
                liga_full, detalhe = antes.rsplit(" - ", 1)
                meio = len(liga_full)//2
                liga = liga_full[:meio].strip() if liga_full[:meio].strip()==liga_full[meio:].strip() else liga_full.strip()

            tokens = depois.split()
            canais_tk = []
            while tokens and re.fullmatch(r"[A-Z0-9ÉÊÁ+]{2,}", tokens[-1]):
                canais_tk.insert(0, tokens.pop())
            resto = " ".join(tokens)
            canais, i = [], 0
            while i < len(canais_tk):
                par = " ".join(canais_tk[i:i+2])
                if sem_acento(par).upper() in COMPOSTOS: canais.append(par); i += 2
                else: canais.append(canais_tk[i]); i += 1

            m2 = re.fullmatch(r"(.+?) \1 (.+?) \2", resto)
            partida = f"{m2.group(1)} x {m2.group(3)}" if m2 else resto
            if not partida or (dia, hora, norm(partida)) in vistos: continue
            vistos.add((dia, hora, norm(partida)))

            eventos.append({"d":dia,"t":hora,"sport":"Futebol","g":genero(liga,partida),
                "country":pais_da_liga(liga),"league":normalizar_liga(liga),"detail":detalhe or "—",
                "match":partida,"ch":[classificar_canal(c) for c in canais],"v":1})
    return eventos

# ----------------------------------------------------------- FONTE B
def fonte_mantos():
    """Formato típico de linha: '16h00 – Time A x Time B – Canal 1, Canal 2 (Competição)'."""
    urls = {0: "https://mantosdofutebol.com.br/guia-de-jogos-tv-hoje-ao-vivo/",
            1: "https://mantosdofutebol.com.br/jogos-de-amanha-tv/"}
    rx = re.compile(
        r"(\d{1,2})[h:](\d{2})\s*[–\-—]\s*(.+?)\s+[xX]\s+(.+?)\s*[–\-—]\s*([^(\n]+)(?:\(([^)]+)\))?")
    eventos = []
    for dia, url in urls.items():
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        corpo = soup.select_one("article") or soup
        for linha in corpo.get_text("\n").split("\n"):
            m = rx.search(linha.strip())
            if not m: continue
            h, mi, t1, t2, canais_txt, comp = m.groups()
            canais = [c.strip() for c in re.split(r"[,/]| e ", canais_txt) if c.strip()]
            liga = (comp or "").strip() or "Futebol"
            eventos.append({"d":dia,"t":f"{int(h):02d}:{mi}","sport":"Futebol",
                "g":genero(liga,t1,t2),"country":pais_da_liga(liga),"league":normalizar_liga(liga),
                "detail":"—","match":f"{t1.strip()} x {t2.strip()}",
                "ch":[classificar_canal(c) for c in canais],"v":1})
    return eventos

# ----------------------------------------------------------- merge
def mesclar(base, extra):
    for ev in extra:
        achou = None
        for b in base:
            if b["d"]==ev["d"] and b["t"]==ev["t"] and \
               difflib.SequenceMatcher(None, norm(b["match"]), norm(ev["match"])).ratio() > 0.55:
                achou = b; break
        if achou:
            nomes = {norm(c["n"]) for c in achou["ch"]}
            achou["ch"] += [c for c in ev["ch"] if norm(c["n"]) not in nomes]
            achou["v"] = min(achou.get("v",1) + 1, 2)
            if achou["league"] in ("Futebol","—") and ev["league"] not in ("Futebol","—"):
                achou["league"], achou["country"] = ev["league"], ev["country"]
        else:
            base.append(ev)
    return base

# ----------------------------------------------------------- main
def main():
    eventos = fonte_futebolnatv()
    print(f"Fonte A (futebolnatv): {len(eventos)} jogos")
    try:
        extra = fonte_mantos()
        print(f"Fonte B (mantos):      {len(extra)} jogos")
        eventos = mesclar(eventos, extra)
    except Exception as e:
        print(f"Fonte B indisponível ({e}) — seguindo só com a fonte A.")

    for ev in eventos:
        if not ev["ch"]: ev["ch"] = [{"n":"A confirmar","y":"tv"}]
    eventos.sort(key=lambda e: (e["d"], e["t"]))

    Path("eventos.json").write_text(json.dumps(eventos, ensure_ascii=False, indent=1), encoding="utf-8")
    duplos = sum(1 for e in eventos if e.get("v",1) >= 2)
    print(f"\neventos.json: {len(eventos)} jogos ({duplos} confirmados em 2 fontes)")

    agora = datetime.now(BRT).strftime("%d/%m %H:%M")
    for nome in ("index.html", "onde-assistir.html"):
        p = Path(nome)
        if p.exists():
            html = p.read_text(encoding="utf-8")
            bloco = "/*DB-START*/\nconst DB = " + json.dumps(eventos, ensure_ascii=False, indent=1) + ";\n/*DB-END*/"
            html = re.sub(r"/\*DB-START\*/.*?/\*DB-END\*/", bloco, html, flags=re.S)
            html = re.sub(r"const GENERATED_AT = .*?;", f"const GENERATED_AT = '{agora}';", html)
            p.write_text(html, encoding="utf-8")
            print(f"{nome} atualizado.")

if __name__ == "__main__":
    main()
