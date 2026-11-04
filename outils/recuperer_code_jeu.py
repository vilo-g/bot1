#!/usr/bin/env python3
"""
recuperer_code_jeu.py : recupere, depuis le Chrome du bot, le code PUBLIC du jeu (Game.bundle.js) et ses donnees
(items_vXXX.json), puis en extrait les passages qui parlent de flancs / remplissage des vagues / envoi d'attaque.

But : retrouver comment le jeu calcule le « Remplir les vagues » pour que le bot fasse exactement le meme calcul.
Aucun identifiant n'est lu ni enregistre. Il faut seulement que le Chrome du bot soit ouvert sur la page du jeu
(l'ecran de connexion suffit : lance lancer_chrome.bat avant).

Resultat :
  captures/jeu/Game.bundle.js      le code complet (gros fichier, ne pas l'envoyer)
  captures/items.json              les donnees (unites, effets...)
  captures/extraits_code.txt       les passages utiles (c'est LUI qu'il faut m'envoyer)
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_envoi as T
import connexion_auto as C

RACINE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MOTS = [  # (titre, expression reguliere, nombre maximal de passages, demi-largeur du passage)
    ("Cles du paquet d'attaque (ASCT, HBW, AST)", r"ASCT|HBW\b|\"AST\"|AST:", 12, 900),
    ("Remplissage des vagues", r"[Aa]uto[Ff]ill|[Ff]illWave|[Ff]illAllWave|[Ff]illArmy|[Ff]illAttack|[Ff]illFlank", 25, 900),
    ("Flancs", r"[Ff]lank", 40, 600),
    ("Taille / maximum d'une vague", r"[Mm]axUnits|[Mm]axWaveS|[Uu]nitsPerWave|[Ww]aveCapacity|[Mm]axSoldier|[Mm]axTroops|[Mm]axUnitCount|[Mm]axAttackUnit", 30, 700),
]


async def lire_texte(nav, s, cid, url, nom_var):
    p = {"expression": "fetch(%s).then(r=>r.text()).then(t=>{globalThis.%s=t;return t.length})" % (json.dumps(url), nom_var),
         "awaitPromise": True, "returnByValue": True}
    if cid is not None:
        p["contextId"] = cid
    r = await nav.call("Runtime.evaluate", p, s, timeout=180)
    n = r.get("result", {}).get("value")
    if not n:
        return None
    morceaux = []
    for i in range(0, n, 1000000):
        q = {"expression": "globalThis.%s.slice(%d,%d)" % (nom_var, i, i + 1000000), "returnByValue": True}
        if cid is not None:
            q["contextId"] = cid
        morceaux.append((await nav.call("Runtime.evaluate", q, s, timeout=60))["result"]["value"])
    return "".join(morceaux)


async def telecharger():
    import websockets
    info = C.chrome_joignable()
    if not info:
        raise RuntimeError("Chrome du bot non ouvert : lance lancer_chrome.bat d'abord.")
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None) as ws:
        nav = T.Navigateur(ws)
        await nav.demarrer()
        for s, cid in await C.contextes(nav):
            origine = await C.ev(nav, s, cid, "location.origin")
            if not origine or "empire-html5" not in origine:
                continue
            urls = await C.ev(nav, s, cid, "performance.getEntriesByType('resource').map(e => e.name)") or []
            jeu = next((u for u in urls if re.search(r"Game\.bundle\.[0-9a-f]+\.js", u)), None)
            items = next((u for u in urls if re.search(r"items_v[\d.]+\.json", u)), None)
            if not jeu:
                continue
            print("Code du jeu :", jeu.split("/")[-1])
            code = await lire_texte(nav, s, cid, jeu, "__code")
            donnees = await lire_texte(nav, s, cid, items, "__items") if items else None
            return code, donnees
    raise RuntimeError("page du jeu introuvable dans ce Chrome (ouvre empire.goodgamestudios.com dedans).")


def extraire(code, sortie):
    total = 0
    with open(sortie, "w", encoding="utf-8") as f:
        f.write("Extraits du code du jeu (%d caracteres au total)\n" % len(code))
        for titre, motif, maxi, demi in MOTS:
            trouves = [m.start() for m in re.finditer(motif, code)]
            f.write("\n\n######## %s : %d occurrences ########\n" % (titre, len(trouves)))
            dernier = -10 ** 9
            ecrits = 0
            for pos in trouves:
                if pos - dernier < demi:        # ne pas repeter un passage voisin
                    continue
                dernier = pos
                f.write("\n--- position %d ---\n%s\n" % (pos, code[max(0, pos - demi): pos + demi]))
                ecrits += 1
                total += 2 * demi
                if ecrits >= maxi:
                    break
    return total


def principal():
    try:
        code, donnees = asyncio.run(telecharger())
    except Exception as e:
        print("Echec :", e)
        return
    os.makedirs(os.path.join(RACINE, "captures", "jeu"), exist_ok=True)
    chemin = os.path.join(RACINE, "captures", "jeu", "Game.bundle.js")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(code)
    print("Code du jeu enregistre : %s (%.1f Mo)" % (chemin, len(code) / 1e6))
    if donnees:
        with open(os.path.join(RACINE, "captures", "items.json"), "w", encoding="utf-8") as f:
            f.write(donnees)
        print("Donnees enregistrees : captures\\items.json (%.1f Mo)" % (len(donnees) / 1e6))
    sortie = os.path.join(RACINE, "captures", "extraits_code.txt")
    n = extraire(code, sortie)
    print("Extraits ecrits : %s (%.0f Ko). C'est ce fichier qu'il faut m'envoyer." % (sortie, os.path.getsize(sortie) / 1e3))


if __name__ == "__main__":
    principal()
