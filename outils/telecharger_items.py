#!/usr/bin/env python3
"""
telecharger_items.py : telecharge le fichier de donnees PUBLIC du jeu (items_vXXX.json) que ton navigateur
charge deja a chaque partie : statistiques de toutes les unites, effets, commandants...
Le fichier est range dans captures/items.json. Aucun identifiant n'est utilise.

  py telecharger_items.py                 (version par defaut)
  py telecharger_items.py --version 788.03

Si le telechargement direct echoue, le script le fait faire par le Chrome du bot (deja ouvert et connecte).
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = "https://empire-html5.goodgamestudios.com/default/items/items_v%s.json"


def sauver(texte, chemin):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(texte)
    d = json.loads(texte)
    print("OK : %s (%.1f Mo)" % (chemin, len(texte) / 1e6))
    if isinstance(d, dict):
        print("Contenu (categories principales) :")
        for k in list(d)[:40]:
            v = d[k]
            print("  %-28s %s" % (k, ("%d elements" % len(v)) if isinstance(v, (list, dict)) else repr(v)[:40]))


def direct(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://empire.goodgamestudios.com/"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8")


async def via_chrome(url):
    import websockets
    import test_envoi as T
    import connexion_auto as C
    info = C.chrome_joignable()
    if not info:
        raise RuntimeError("Chrome du bot non ouvert (lance lancer_chrome.bat d'abord)")
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None) as ws:
        nav = T.Navigateur(ws)
        await nav.demarrer()
        for s, cid in await C.contextes(nav):
            origine = await C.ev(nav, s, cid, "location.origin")
            if not origine or "empire-html5" not in origine:
                continue
            p = {"expression": "fetch(%s).then(r=>r.text()).then(t=>{globalThis.__items=t;return t.length})" % json.dumps(url),
                 "awaitPromise": True, "returnByValue": True}
            if cid is not None:
                p["contextId"] = cid
            r = await nav.call("Runtime.evaluate", p, s, timeout=120)
            n = r.get("result", {}).get("value")
            if not n:
                continue
            morceaux = []
            for i in range(0, n, 1000000):
                q = {"expression": "globalThis.__items.slice(%d,%d)" % (i, i + 1000000), "returnByValue": True}
                if cid is not None:
                    q["contextId"] = cid
                morceaux.append((await nav.call("Runtime.evaluate", q, s, timeout=60))["result"]["value"])
            return "".join(morceaux)
    raise RuntimeError("fichier introuvable par le navigateur")


def principal():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="788.03")
    a = p.parse_args()
    url = BASE % a.version
    chemin = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures", "items.json"))
    try:
        sauver(direct(url), chemin)
        return
    except Exception as e:
        print("Telechargement direct impossible (%s) : j'essaie par le Chrome du bot..." % e)
    try:
        sauver(asyncio.run(via_chrome(url)), chemin)
    except Exception as e:
        print("Echec :", e)


if __name__ == "__main__":
    principal()
