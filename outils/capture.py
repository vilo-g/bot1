#!/usr/bin/env python3
"""
capture.py (bot) : ECOUTE seulement. N'envoie rien au jeu.

Se branche sur le Chrome du bot (outils/lancer_chrome.bat, port 9222) deja connecte au jeu et enregistre dans
bot/captures :
  - capture_ws.jsonl   : messages du jeu envoyes (up) et recus (down)
  - capture_http.jsonl : requetes HTTP (methode, adresse SANS parametres, statut). Jamais le contenu.
Les identifiants, mots de passe et jetons sont masques avant d'ecrire.

Pendant la capture : ecris un mot puis Entree (ex. attaque, achat, acceleration) pour marquer l'etape dans le fichier.
Installation : py -m pip install websockets
Arret        : Ctrl+C
"""
import asyncio
import base64
import json
import re
import threading
import time
import urllib.request
import os
import zlib
from urllib.parse import urlsplit

import websockets

CDP_HTTP = "http://127.0.0.1:9222"
DOSSIER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")
WS_LOG = os.path.join(DOSSIER, "capture_ws.jsonl")
HTTP_LOG = os.path.join(DOSSIER, "capture_http.jsonl")
BRUYANTS = {"pin", "nch", "gaa", "vck", "irc", "ain", "sei", "gpa", "dql"}   # pas affiches dans la console (toujours enregistres)
TYPES_IGNORES = {"Image", "Font", "Stylesheet", "Media", "Ping"}


def sans_params(url):
    """Adresse sans les parametres (ils peuvent contenir des jetons)."""
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}{u.path}"


CHAMPS_SECRETS = ("NOM", "PW", "RCT", "AID", "DID", "LT", "ABT", "TOKEN", "TK", "PWD", "PASSWORD")


def masquer(texte):
    """Masque les secrets en gardant leur longueur :
    - <nick> et <pword> (connexion SmartFox)
    - les champs JSON NOM, PW, RCT, AID... (message lli = vraie connexion au jeu)
    """
    def remplacer(m):
        return f"<{m.group(1)}>XXXX(longueur {len(m.group(2))})</{m.group(1)}>"
    texte = re.sub(r"<(pword|nick)>(.*?)</\1>", remplacer, texte, flags=re.S)

    def remplacer_json(m):
        return f'"{m.group(1)}":"XXXX(longueur {len(m.group(2))})"'
    motif = r'"(' + "|".join(CHAMPS_SECRETS) + r')"\s*:\s*"((?:[^"\\]|\\.)*)"'
    texte = re.sub(motif, remplacer_json, texte)
    # filet de securite : adresses e-mail et jetons JWT ou qu'ils se trouvent
    texte = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "XXXX@XXXX", texte)
    texte = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "XXXX(jeton JWT)", texte)
    return texte


def decoder(octets):
    """Essaie : texte UTF-8 direct, puis zlib, puis deflate brut. None si echec."""
    essais = (
        lambda b: b,
        zlib.decompress,
        lambda b: zlib.decompress(b, -15),
    )
    for essai in essais:
        try:
            return essai(octets).decode("utf-8")
        except Exception:
            continue
    return None


def nom_commande(texte, sens):
    """%xt%Zone%cra%1%{...}% (envoye) ou %xt%cra%1%0%{...}% (recu) -> 'cra' ; None si ce n'est pas un message du jeu."""
    if not texte or not texte.startswith("%xt%"):
        return None
    morceaux = texte.split("%")
    i = 3 if sens == "up" else 2
    return morceaux[i] if len(morceaux) > i and morceaux[i] else None


def ecrire(fichier, donnees):
    fichier.write(json.dumps(donnees, ensure_ascii=False) + "\n")
    fichier.flush()


def entree_http(meth, url, typ, post, statut, redirection=False):
    return {
        "t": round(time.time(), 3),
        "methode": meth,
        "url": url,
        "type": typ,
        "corps": post,
        "statut": statut,
        "redirection": redirection,
    }


async def main():
    try:
        info = json.load(urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=5))
    except Exception as e:
        print("Impossible de joindre le Chrome du bot (port 9222).")
        print("Arrete le bot, lance outils\\lancer_chrome.bat, connecte-toi au jeu, puis relance la capture.")
        print("Detail :", e)
        return

    os.makedirs(DOSSIER, exist_ok=True)
    f_ws = open(WS_LOG, "a", encoding="utf-8")
    f_http = open(HTTP_LOG, "a", encoding="utf-8")

    ws_urls = {}    # (session, requestId) -> adresse du WebSocket
    requetes = {}   # (session, requestId) -> (methode, adresse, type, corps)
    cibles = set()  # onglets / iframes deja rattaches
    nb = {"ws": 0, "http": 0}
    compteur_id = 0

    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None) as ws:

        async def envoyer(methode, params=None, session=None):
            nonlocal compteur_id
            compteur_id += 1
            msg = {"id": compteur_id, "method": methode, "params": params or {}}
            if session:
                msg["sessionId"] = session
            await ws.send(json.dumps(msg))

        async def rattacher(cible):
            if cible.get("type") in ("page", "iframe") and cible["targetId"] not in cibles:
                cibles.add(cible["targetId"])
                await envoyer("Target.attachToTarget",
                              {"targetId": cible["targetId"], "flatten": True})

        await envoyer("Target.setDiscoverTargets", {"discover": True})
        print("Branche sur Chrome. Ne recharge pas la page du jeu.")
        print("Avant chaque etape, ecris son NOM puis Entree (il est note dans le fichier), puis fais-la a la main :")
        print("  membres     : ouvrir Alliance > Membres")
        print("  protege     : cliquer sur le chateau d'un membre EN protection (fiche du joueur)")
        print("  nonprotege  : cliquer sur le chateau d'un membre SANS protection")
        print("  soutien     : envoyer quelques soldats en soutien chez le membre en protection")
        print("  rappel      : rappeler ces soldats")
        print("  porte       : ouvrir la porte du chateau (option payee en rubis)")
        print("  attaque     : quand une attaque arrive sur un de tes chateaux (laisse tourner jusqu'a ce qu'elle soit visible)")
        print("Ctrl+C pour arreter.")
        print("")

        boucle = asyncio.get_running_loop()

        def noter(mot):
            ecrire(f_ws, {"t": round(time.time(), 3), "marque": mot})
            print("   --- etape notee : %s ---" % mot)

        def lire_clavier():                      # fil a part (ne bloque ni l'ecoute ni la fermeture)
            while True:
                try:
                    mot = input().strip()
                except Exception:
                    return
                if mot:
                    boucle.call_soon_threadsafe(noter, mot)
        threading.Thread(target=lire_clavier, daemon=True).start()

        async for brut in ws:
            m = json.loads(brut)
            methode = m.get("method")
            if methode is None:
                continue
            p = m.get("params", {})
            s = m.get("sessionId", "")

            if methode == "Target.targetCreated":
                await rattacher(p["targetInfo"])

            elif methode == "Target.attachedToTarget":
                await envoyer("Network.enable", {}, p["sessionId"])

            elif methode == "Network.webSocketCreated":
                ws_urls[(s, p["requestId"])] = sans_params(p["url"])

            elif methode in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
                r = p["response"]
                entree = {
                    "t": round(time.time(), 3),
                    "sens": "up" if methode.endswith("Sent") else "down",
                    "ws": ws_urls.get((s, p["requestId"]), "?"),
                    "opcode": r["opcode"],
                }
                if r["opcode"] == 2:  # binaire (base64 cote Chrome)
                    octets = base64.b64decode(r["payloadData"])
                    entree["octets"] = len(octets)
                    texte = decoder(octets)
                    if texte is None:
                        entree["base64"] = r["payloadData"]
                    else:
                        entree["texte"] = masquer(texte)
                else:
                    entree["texte"] = masquer(r["payloadData"])
                ecrire(f_ws, entree)
                nb["ws"] += 1
                cmd = nom_commande(entree.get("texte"), entree["sens"])
                if cmd and cmd not in BRUYANTS:
                    print("   %s %s  %s" % (time.strftime("%H:%M:%S"), "-> envoye" if entree["sens"] == "up" else "<- recu  ", cmd))
                if nb["ws"] % 200 == 0:
                    print(f"{nb['ws']} messages WebSocket, {nb['http']} requetes HTTP")

            elif methode == "Network.requestWillBeSent":
                req = p["request"]
                cle = (s, p["requestId"])
                redir = p.get("redirectResponse")
                if redir and cle in requetes:
                    meth, url, typ, post = requetes[cle]
                    ecrire(f_http, entree_http(meth, url, typ, post, redir["status"], True))
                    nb["http"] += 1
                if p.get("type") in TYPES_IGNORES:
                    requetes.pop(cle, None)
                else:
                    requetes[cle] = (req["method"], sans_params(req["url"]),
                                     p.get("type"), bool(req.get("hasPostData")))

            elif methode == "Network.responseReceived":
                cle = (s, p["requestId"])
                if cle in requetes:
                    meth, url, typ, post = requetes.pop(cle)
                    ecrire(f_http, entree_http(meth, url, typ, post, p["response"]["status"]))
                    nb["http"] += 1


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArret. Fichiers enregistres dans %s : capture_ws.jsonl et capture_http.jsonl" % DOSSIER)
