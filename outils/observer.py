#!/usr/bin/env python3
"""
observer.py : OBSERVE seulement. N'envoie rien au jeu.

Se branche sur un Chrome lance avec --remote-debugging-port=9222 et enregistre :
  - ws_log.jsonl   : messages WebSocket envoyes (up) et recus (down),
                     binaires decodes en texte quand c'est possible
  - http_log.jsonl : requetes HTTP (methode, adresse SANS parametres, statut,
                     presence d'un corps). Jamais le contenu.

Les champs <nick> et <pword> sont masques avant d'ecrire dans le fichier.

Installation : pip install websockets
Arret        : Ctrl+C
"""
import asyncio
import base64
import json
import re
import time
import urllib.request
import zlib
from urllib.parse import urlsplit

import websockets

CDP_HTTP = "http://127.0.0.1:9222"
WS_LOG = "ws_log.jsonl"
HTTP_LOG = "http_log.jsonl"
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
        print("Impossible de joindre Chrome sur le port 9222.")
        print("Lance d'abord Chrome avec --remote-debugging-port=9222 (voir les instructions).")
        print("Detail :", e)
        return

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
        print("Branche sur Chrome. Recharge (F5) la page du jeu pour tout capturer.")
        print("Ctrl+C pour arreter.")

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
        print("\nArret. Fichiers : ws_log.jsonl et http_log.jsonl")
