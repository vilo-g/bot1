#!/usr/bin/env python3
"""
test_envoi.py : TEST D'ENVOI sur un compte de test, depuis le Chrome deja connecte.

Ce que fait ce script, dans l'ordre (chaque etape s'affiche) :
  1. se branche sur le Chrome lance avec lancer_chrome.bat et retrouve le WebSocket du jeu ;
  2. demande la carte autour de ton chateau (gaa, lecture seule) ;
  3. choisit la tour attaquable la plus proche et demande les infos d'attaque (adi) :
     troupes disponibles et commandants (lecture seule) ;
  4. te laisse choisir commandant, unite et quantite ;
  5. AFFICHE le paquet d'attaque (cra), puis ne l'envoie que si tu tapes OUI ;
  6. lit la reponse du serveur (duree du trajet, erreurs) et attend le rapport de combat.

Le script n'ouvre AUCUNE connexion au jeu et ne touche pas a ton mot de passe : il utilise
la connexion deja ouverte par la page. A utiliser UNIQUEMENT sur un compte de test.

Exemples :
  py test_envoi.py --chateau 79 1158 --royaume 0 --lecture-seule
  py test_envoi.py --chateau 79 1158 --royaume 0

Installation : py -m pip install websockets
"""
import argparse
import asyncio
import collections
import json
import math
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import combat  # noqa: E402  (calcul du remplissage automatique)

# ----------------------------------------------------------------------------- reglages
CDP_HTTP = "http://127.0.0.1:9222"
MOTIF_SOCKET = "-game.goodgamestudios.com"   # le WebSocket principal du jeu (tous serveurs)
ZONE_PAR_DEFAUT = "EmpireEx_3"             # utilisee seulement si la zone ne peut pas etre detectee
PAUSE_ENTRE_GAA = 0.4                  # secondes entre deux demandes de carte
RE_MSG = re.compile(r"^%xt%([^%]+)%(-?\d+)%(-?\d+)(?:%(.*))?%$", re.S)   # sans donnees : "%xt%kgt%1%114%" (refus)


class ErreurCDP(Exception):
    pass


# ----------------------------------------------------------------------------- transport CDP
class Navigateur:
    """Parle au Chrome par le protocole de debogage (meme principe qu'observer.py)."""

    def __init__(self, ws):
        self.ws = ws
        self.n = 0
        self.attente = {}
        self.attaches = {}
        self.infos = {}          # session -> (type, adresse sans parametres)
        self.auto = set()
        self.runtime_actif = set()
        self.contextes = {}      # session -> {id du contexte: (origine, nom, par_defaut)}
        self.tache = None

    async def demarrer(self):
        self.tache = asyncio.create_task(self._lire())

    async def _lire(self):
        try:
            async for brut in self.ws:
                m = json.loads(brut)
                if m.get("method") == "Target.attachedToTarget":
                    p = m["params"]
                    ti = p.get("targetInfo", {})
                    self.attaches.setdefault(ti.get("targetId", p["sessionId"]), p["sessionId"])
                    self.infos[p["sessionId"]] = (ti.get("type"), str(ti.get("url", "")).split("?")[0][:90])
                meth = m.get("method")
                if meth == "Runtime.executionContextCreated":
                    c = m["params"]["context"]
                    aux = c.get("auxData") or {}
                    self.contextes.setdefault(m.get("sessionId", ""), {})[c["id"]] = (
                        str(c.get("origin", ""))[:80], str(c.get("name", ""))[:40], aux.get("isDefault", True))
                elif meth == "Runtime.executionContextDestroyed":
                    self.contextes.get(m.get("sessionId", ""), {}).pop(m["params"].get("executionContextId"), None)
                elif meth == "Runtime.executionContextsCleared":
                    self.contextes[m.get("sessionId", "")] = {}
                if "id" in m:
                    f = self.attente.pop(m["id"], None)
                    if f and not f.done():
                        f.set_result(m)
        except Exception:
            pass

    async def call(self, methode, params=None, session=None, timeout=20):
        self.n += 1
        i = self.n
        msg = {"id": i, "method": methode, "params": params or {}}
        if session:
            msg["sessionId"] = session
        f = asyncio.get_running_loop().create_future()
        self.attente[i] = f
        await self.ws.send(json.dumps(msg))
        try:
            m = await asyncio.wait_for(f, timeout)
        except asyncio.TimeoutError:
            self.attente.pop(i, None)            # Chrome n'a pas repondu a temps (page du jeu occupee) : pas de reste en attente
            raise
        if "error" in m:
            raise ErreurCDP(m["error"].get("message", str(m["error"])))
        return m.get("result", {})

    async def sessions(self):
        """Se rattache aux pages, iframes ET workers (le jeu peut vivre dans un worker)."""
        r = await self.call("Target.getTargets")
        for t in r.get("targetInfos", []):
            if t.get("type") in ("page", "iframe", "worker", "shared_worker") and t["targetId"] not in self.attaches:
                try:
                    a = await self.call("Target.attachToTarget",
                                        {"targetId": t["targetId"], "flatten": True})
                    self.attaches[t["targetId"]] = a["sessionId"]
                    self.infos[a["sessionId"]] = (t.get("type"), str(t.get("url", "")).split("?")[0][:90])
                except ErreurCDP:
                    pass
        # workers lances par une page : on demande a chaque page/iframe de nous les rattacher
        for sid in list(self.attaches.values()):
            if sid in self.auto or self.infos.get(sid, ("",))[0] not in ("page", "iframe"):
                continue
            self.auto.add(sid)
            try:
                await self.call("Target.setAutoAttach",
                                {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}, sid)
            except ErreurCDP:
                pass
        await asyncio.sleep(0.6)
        # Runtime.enable : le navigateur nous liste alors tous les contextes (iframes du meme site comprises)
        for sid in list(self.attaches.values()):
            if sid not in self.runtime_actif:
                self.runtime_actif.add(sid)
                try:
                    await self.call("Runtime.enable", {}, sid)
                except ErreurCDP:
                    pass
        return list(dict.fromkeys(self.attaches.values()))


# ----------------------------------------------------------------------------- acces au WebSocket du jeu
JS_TROUVER = """
function(motif) {
  const tous = this.map(s => [String(s.url).split('?')[0], s.readyState]);
  const ok = this.filter(s => s.readyState === 1 && String(s.url).includes(motif));
  ok.sort((a, b) => (String(a.url).includes('world2') ? 1 : 0) - (String(b.url).includes('world2') ? 1 : 0));
  if (!ok.length) return {choisi: null, tous: tous};
  const s = ok[0];
  globalThis.__ggeSock = s;
  if (!globalThis.__ggeInbox) globalThis.__ggeInbox = [];
  if (!s.__ggeHooked) {
    s.__ggeHooked = true;
    s.addEventListener('message', async ev => {
      let d = ev.data;
      try {
        if (typeof d !== 'string') {
          d = (d instanceof Blob) ? await d.text() : new TextDecoder().decode(d);
        }
      } catch (e) { d = ''; }
      const ml = d.match(/^%xt%lli%1%(-?\\d+)%/);
      if (ml) { globalThis.__ggeLli = ml[1]; if (ml[1] === '0') globalThis.__ggeLogin = true; }
      globalThis.__ggeInbox.push(d);
      if (globalThis.__ggeInbox.length > 2000) globalThis.__ggeInbox.splice(0, 1000);
    });
    const orig = s.send.bind(s);
    globalThis.__ggeSendOrig = orig;
    s.send = function(d) {
      try {
        if (typeof d === 'string') {
          const m = d.match(/^%xt%([^%]+)%/);
          if (m && !/^%xt%[^%]+%(lli|vck|core_)/.test(d)) globalThis.__ggeZone = m[1];
          if (/^%xt%[^%]+%(nch|gdi|gbl|gaa|adi|gbd)%/.test(d)) globalThis.__ggeLogin = true;
          if (/^%xt%[^%]+%cra%1%/.test(d)) { (globalThis.__ggeCra = globalThis.__ggeCra || []).push(d); }
          if (/^%xt%[^%]+%lli%/.test(d)) globalThis.__ggeLliEnvoye = Date.now();   // seulement l'heure : jamais le contenu
        }
      } catch (e) {}
      return orig(d);
    };
  }
  return {choisi: String(s.url).split('?')[0], tous: tous};
}
"""


class Jeu:
    """Envoie et recoit des paquets par le WebSocket deja ouvert dans la page."""

    def __init__(self, nav, zone=None):
        self.nav = nav
        self.zone = zone
        self.session = None
        self.contexte = None     # id du contexte JavaScript qui contient le socket
        self.recus = []          # [(commande, code, payload_texte)]
        self.ecouteurs = []      # fonctions(message) appelees pour chaque message recu (ex. module defense : attaques annoncees)

    async def _eval(self, expression):
        p = {"expression": expression, "returnByValue": True}
        if self.contexte is not None:
            p["contextId"] = self.contexte
        return await self.nav.call("Runtime.evaluate", p, self.session)

    async def connecter(self, motif=MOTIF_SOCKET):
        vus = []
        for s in await self.nav.sessions():
            infos = getattr(self.nav, "infos", {}).get(s, ("?", "?"))
            ctx = getattr(self.nav, "contextes", {}).get(s) or {}
            ids = [cid for cid, (o, n, d) in ctx.items() if d] or [None]
            for cid in ids:
                try:
                    pe = {"expression": "WebSocket.prototype"}
                    if cid is not None:
                        pe["contextId"] = cid
                    proto = await self.nav.call("Runtime.evaluate", pe, s)
                    oid = proto["result"].get("objectId")
                    if not oid:
                        continue
                    inst = await self.nav.call("Runtime.queryObjects", {"prototypeObjectId": oid}, s)
                    rep = await self.nav.call("Runtime.callFunctionOn", {
                        "objectId": inst["objects"]["objectId"],
                        "functionDeclaration": JS_TROUVER,
                        "arguments": [{"value": motif}],
                        "returnByValue": True}, s)
                    valeur = rep.get("result", {}).get("value") or {}
                    origine = ctx.get(cid, ("",))[0] if cid is not None else ""
                    vus.append((infos, origine, valeur.get("tous", [])))
                    if valeur.get("choisi"):
                        self.session, self.contexte = s, cid
                        return valeur["choisi"]
                except (ErreurCDP, asyncio.TimeoutError, KeyError):
                    continue
        raise ErreurCDP("WebSocket du jeu introuvable. Contextes lus (type+adresse de l'onglet, origine du contexte, WebSockets) :\n  "
                        + "\n  ".join(str(v) for v in vus) if vus else "WebSocket du jeu introuvable : aucun contexte lisible")

    async def est_connecte(self):
        """Vrai seulement si le LOGIN est fait (reponse lli code 0, ou le jeu envoie pin/nch)."""
        r = await self._eval("globalThis.__ggeLogin===true")
        return bool(r.get("result", {}).get("value"))

    async def lli_envoye(self):
        """Vrai si le jeu a envoye sa demande de connexion (lli) depuis que le socket est surveille (heure seulement)."""
        r = await self._eval("!!globalThis.__ggeLliEnvoye")
        return bool(r.get("result", {}).get("value"))

    async def nb_champs_saisie(self):
        """Nombre de champs de saisie du jeu (txt_input_N) dans la page du socket : >= 4 quand l'ecran d'accueil est charge."""
        r = await self._eval("document.querySelectorAll('input[id^=txt_input_]').length")
        v = r.get("result", {}).get("value")
        return v if isinstance(v, int) else 0

    async def code_login(self):
        """Code de reponse du serveur a la connexion (lli) : '0' = accepte, autre = refuse ; None = pas encore de reponse."""
        r = await self._eval("globalThis.__ggeLli||null")
        return r.get("result", {}).get("value")

    async def detecter_zone(self, attente=45):
        """La zone (ex. EmpireEx_3) se lit dans les messages que le jeu envoie lui-meme (pin toutes les 60 s)."""
        if self.zone:
            return self.zone
        fin = time.time() + attente
        while time.time() < fin:
            r = await self._eval("globalThis.__ggeZone||null")
            z = r.get("result", {}).get("value")
            if z:
                self.zone = z
                return z
            await asyncio.sleep(1)
        self.zone = ZONE_PAR_DEFAUT
        return None

    async def relever(self):
        r = await self._eval("(()=>{const a=globalThis.__ggeInbox||[];globalThis.__ggeInbox=[];return a;})()")
        for texte in r.get("result", {}).get("value") or []:
            m = RE_MSG.match(texte)
            if m:
                msg = (m.group(1), int(m.group(3)), m.group(4) or "")
                for f in self.ecouteurs:
                    try:
                        f(msg)
                    except Exception:
                        pass
                self.recus.append(msg)

    async def cra_sortants(self):
        r = await self._eval("(()=>{const a=globalThis.__ggeCra||[];globalThis.__ggeCra=[];return a;})()")
        return r.get("result", {}).get("value") or []

    async def attendre_cra_joueur(self, timeout=600):
        """Attend que LE JOUEUR lance une attaque dans le jeu, et renvoie le message envoye par le jeu."""
        fin = time.time() + timeout
        while time.time() < fin:
            await self.relever()          # garde les reponses du serveur (adi/abi...) pendant l'attente
            msgs = await self.cra_sortants()
            if msgs:
                return msgs[-1]
            await asyncio.sleep(0.5)
        return None

    async def vider(self):
        await self.relever()
        self.recus.clear()

    async def envoyer(self, commande, donnees):
        corps = json.dumps(donnees, separators=(",", ":")) if donnees is not None else ""
        msg = "%%xt%%%s%%%s%%1%%%s%%" % (self.zone, commande, corps)
        r = await self._eval("(globalThis.__ggeSendOrig||globalThis.__ggeSock.send.bind(globalThis.__ggeSock))(%s)" % json.dumps(msg))
        if r.get("exceptionDetails"):
            raise ErreurCDP("envoi refuse : %s" % r["exceptionDetails"].get("text"))
        return msg

    async def attendre(self, commandes, timeout=15):
        """Attend le premier message d'une des commandes. Renvoie (commande, code, payload) ou None."""
        if isinstance(commandes, str):
            commandes = {commandes}
        fin = time.time() + timeout
        while time.time() < fin:
            await self.relever()
            for i, (c, code, p) in enumerate(self.recus):
                if c in commandes:
                    return self.recus.pop(i)
            await asyncio.sleep(0.25)
        return None


# ----------------------------------------------------------------------------- logique du jeu
def json_ou_none(texte):
    try:
        return json.loads(texte)
    except Exception:
        return None


def carres(x, y, rayon):
    """Carres de 13x13 (bornes incluses) couvrant [x-rayon, x+rayon] x [y-rayon, y+rayon]."""
    x0, y0 = max(0, x - rayon), max(0, y - rayon)
    x1, y1 = x + rayon, y + rayon
    out = []
    ax = x0
    while ax <= x1:
        ay = y0
        while ay <= y1:
            out.append((ax, ay, ax + 12, ay + 12))
            ay += 13
        ax += 13
    return out


def tours_attaquables(entrees, x, y, rayon, niveau_min=0, ignorer_minuteur=False):
    """Tours (type 2) de niveau >= niveau_min, sans proprietaire (-1), avec un minuteur <= 0
    (sauf si ignorer_minuteur), triees par distance au chateau."""
    res = []
    for e in entrees.values():
        if e[0] == 2 and len(e) >= 7 and e[3] == -1 and e[4] >= niveau_min and (ignorer_minuteur or e[5] <= 0):
            d = math.hypot(e[1] - x, e[2] - y)
            if d <= rayon * 1.5:
                res.append((d, e))
    res.sort(key=lambda t: t[0])
    return res


def sauver_carte(entrees, cx, cy, kid):
    """Ecrit les tours et forteresses recues dans captures/carte_tours.json (pas de noms de joueurs)."""
    dossier = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures")
    try:
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, "carte_tours.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump({"chateau": [cx, cy], "kid": kid,
                       "types": dict(collections.Counter(e[0] for e in entrees.values())),
                       "tours": [e for e in entrees.values() if e[0] == 2],
                       "forteresses": [e for e in entrees.values() if e[0] == 11]}, f)
        return os.path.normpath(chemin)
    except OSError:
        return None


def vague(i, unite, quantite, outil, outil_q):
    """Une vague : tout sur le flanc gauche (comme dans les captures). Outil dans la 1re vague seulement."""
    outil_l = [outil, outil_q] if (outil is not None and i == 0) else [-1, 0]
    return {
        "L": {"T": [outil_l, [-1, 0]], "U": [[unite, quantite], [-1, 0]]},
        "R": {"T": [[-1, 0], [-1, 0]], "U": [[-1, 0], [-1, 0]]},
        "M": {"T": [[-1, 0]] * 3, "U": [[-1, 0]] * 6},
    }


def construire_cra(sx, sy, tx, ty, kid, lid, nb_vagues, unite, quantite, outil=None, outil_q=0):
    return {
        "SX": sx, "SY": sy, "TX": tx, "TY": ty, "KID": kid, "LID": lid,
        "WT": 0, "HBW": -1, "BPC": 0, "ATT": 0, "AV": 0, "LP": 0, "FC": 0,
        "PTT": 1, "SD": 0, "ICA": 0, "CD": 99,
        "A": [vague(i, unite, quantite, outil, outil_q) for i in range(nb_vagues)],
        "BKS": [], "AST": [-1, -1, -1], "RW": [[-1, 0]] * 8, "ASCT": 0,
    }


def dossier_captures():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "captures"))


def fichier_modele(args):
    return args.modele_fichier or os.path.join(dossier_captures(), "modele_cra.json")


def resume_armee(cra):
    """Texte court : pour chaque vague, unites et outils de chaque flanc."""
    out = []
    for i, w in enumerate(cra.get("A", [])):
        parts = []
        for f, nom in (("L", "gauche"), ("M", "milieu"), ("R", "droit")):
            u = [x for x in w.get(f, {}).get("U", []) if x[0] != -1]
            t = [x for x in w.get(f, {}).get("T", []) if x[0] != -1]
            if u or t:
                parts.append("%s: unites %s outils %s" % (nom, u or "-", t or "-"))
        out.append("vague %d : %s" % (i + 1, " | ".join(parts) or "vide"))
    return out


def resume_bls(payload):
    """Resume du rapport de combat (bls) : troupes, pertes, butin de l'attaquant."""
    d = json_ou_none(payload) or {}
    for p in d.get("PBI", []):
        if isinstance(p, list) and len(p) >= 5 and p[0] != -222:
            butin = ", ".join("%s %s" % (r[0], r[1]) for r in p[4] if isinstance(r, list))
            return "troupes %s, pertes %s, butin : %s" % (p[2], p[3], butin or "aucun")
    return "rapport recu (format inattendu)"


async def demander(args_val, invite, sortie, lire):
    if args_val is not None:
        return args_val
    return (await lire(invite)).strip()


async def scenario(jeu, args, sortie=print, lire=None):
    """Deroule le test. Renvoie un petit dict de resultats (utile pour les tests automatiques)."""
    if lire is None:
        async def lire(invite):
            return await asyncio.to_thread(input, invite)
    res = {}
    cx, cy = args.chateau
    kid = args.royaume

    sortie("1/6  Connexion au WebSocket du jeu dans le Chrome...")
    url = await jeu.connecter(args.socket)
    sortie("     OK : %s" % url)
    sortie("     Detection de la zone (jusqu'a 45 s, le jeu l'envoie tout seul)...")
    z = await jeu.detecter_zone()
    sortie("     Zone : %s%s" % (jeu.zone, "" if z else "  (NON detectee : valeur par defaut, utilise --zone si ca echoue)"))

    # ---- mode apprentissage : on n'envoie rien, on enregistre l'attaque du joueur
    if args.apprendre:
        sortie("MODE APPRENTISSAGE : fais maintenant UNE attaque normale dans le jeu sur une tour,")
        sortie("avec le bouton « Remplir les vagues » (comme d'habitude). J'attends jusqu'a 10 minutes...")
        await jeu.vider()
        msg = await jeu.attendre_cra_joueur(600)
        if not msg:
            sortie("Aucune attaque detectee : arret.")
            return res
        m = re.match(r"^%xt%([^%]+)%cra%1%(.*)%$", msg, re.S)
        cra = json_ou_none(m.group(2)) if m else None
        if not cra:
            sortie("Attaque detectee mais illisible : %s" % msg[:200])
            return res
        chemin = fichier_modele(args)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump({"zone": jeu.zone, "cra": cra}, f)
        sortie("ATTAQUE ENREGISTREE (modele) : %s" % chemin)
        sortie("  depart (%s, %s) -> cible (%s, %s), royaume %s, commandant (identifiant) %s"
               % (cra["SX"], cra["SY"], cra["TX"], cra["TY"], cra["KID"], cra["LID"]))
        for ligne in resume_armee(cra):
            sortie("  " + ligne)
        autres = {k: v for k, v in cra.items() if k not in ("A", "SX", "SY", "TX", "TY", "KID", "LID")}
        sortie("  autres champs : %s" % json.dumps(autres, separators=(",", ":")))
        rep = await jeu.attendre("cra", 15)
        if rep:
            d = json_ou_none(rep[2]) or {}
            sortie("  reponse du serveur : code %s, trajet %s s" % (rep[1], (d.get("AAM", {}).get("M", {}) or {}).get("TT")))
        # reponse du jeu a l'ouverture de l'ecran d'attaque (adi pour une tour, abi pour une forteresse) :
        # elle contient peut-etre ce qui fixe le nombre maximal de soldats par flanc
        await jeu.relever()
        info = next(((c, p) for (c, code, p) in reversed(jeu.recus) if c in ("adi", "abi") and code == 0), None)
        if info:
            nom = os.path.join(dossier_captures(), "apprentissage_cmd%s_%s.json" % (cra["LID"], int(time.time())))
            with open(nom, "w", encoding="utf-8") as f:
                json.dump({"cra": cra, "type": info[0], "info": json_ou_none(info[1])}, f)
            sortie("  Donnees de l'ecran d'attaque enregistrees : %s" % nom)
        else:
            sortie("  (pas d'ecran d'attaque vu avant l'envoi : ouvre l'attaque APRES avoir lance ce script)")
        res["modele"] = chemin
        return res

    modele = None
    if args.modele:
        chemin = fichier_modele(args)
        try:
            with open(chemin, encoding="utf-8") as f:
                modele = json.load(f)["cra"]
        except (OSError, KeyError, ValueError):
            sortie("Aucun modele lisible (%s). Lance d'abord --apprendre." % chemin)
            return res
        if modele.get("KID") != kid:
            sortie("Le modele est pour le royaume %s, pas %s : arret." % (modele.get("KID"), kid))
            return res
        sortie("Modele charge : %d vague(s), commandant (identifiant) %s." % (len(modele.get("A", [])), modele.get("LID")))

    # ---- 2. carte
    sortie("2/6  Carte autour du chateau (%d, %d), rayon %d (lecture seule)..." % (cx, cy, args.rayon))
    entrees = {}
    for (ax1, ay1, ax2, ay2) in carres(cx, cy, args.rayon):
        await jeu.vider()
        await jeu.envoyer("gaa", {"KID": kid, "AX1": ax1, "AY1": ay1, "AX2": ax2, "AY2": ay2})
        rep = await jeu.attendre("gaa", 10)
        if not rep:
            sortie("     (pas de reponse pour le carre %d,%d)" % (ax1, ay1))
            continue
        d = json_ou_none(rep[2]) or {}
        for e in d.get("AI", []):
            if isinstance(e, list) and len(e) >= 3 and d.get("KID", kid) == kid:
                entrees[(e[1], e[2])] = e
        await asyncio.sleep(PAUSE_ENTRE_GAA)
    sortie("     %d objets recus" % len(entrees))
    res["objets"] = len(entrees)

    census = collections.Counter(e[0] for e in entrees.values())
    sortie("     Types d'objets recus : " + ", ".join("type %s : %d" % kv for kv in sorted(census.items())))
    toutes = sorted(((math.hypot(e[1] - cx, e[2] - cy), e) for e in entrees.values() if e[0] == 2), key=lambda t: t[0])
    sortie("     %d tours (type 2) au total. Les plus proches, donnees brutes [type, x, y, proprio, niveau, minuteur, royaume] :" % len(toutes))
    for d, e in toutes[:8]:
        sortie("       distance %5.1f : %s" % (d, e))
    f = sauver_carte(entrees, cx, cy, kid)
    if f:
        sortie("     Tours et forteresses enregistrees dans : %s" % f)
    cands = tours_attaquables(entrees, cx, cy, args.rayon, args.niveau_min, args.ignorer_minuteur)
    res["tours_total"] = len(toutes)
    sortie("     %d tours retenues (niveau >= %d%s)" % (len(cands), args.niveau_min,
                                                     ", minuteur ignore" if args.ignorer_minuteur else ", minuteur <= 0"))
    for d, e in cands[:5]:
        sortie("       tour (%d, %d)  niveau %s  minuteur %s  distance %.1f" % (e[1], e[2], e[4], e[5], d))
    if args.cible:
        tx, ty = args.cible
    elif cands:
        tx, ty = cands[0][1][1], cands[0][1][2]
    else:
        sortie("     Aucune tour retenue : arret. (essaie --ignorer-minuteur ou --niveau-min 0, ou --cible X Y)")
        return res
    res["cible"] = (tx, ty)
    sortie("     Cible retenue : (%d, %d)" % (tx, ty))

    # ---- 3. infos d'attaque
    sortie("3/6  Infos d'attaque (adi, lecture seule)...")
    await jeu.vider()
    await jeu.envoyer("adi", {"SX": cx, "SY": cy, "TX": tx, "TY": ty, "KID": kid})
    rep = await jeu.attendre("adi", 15)
    if not rep or rep[1] != 0:
        sortie("     Reponse inattendue : %s" % (str(rep)[:300] if rep else "aucune"))
        return res
    d = json_ou_none(rep[2]) or {}
    try:
        nom_adi = os.path.join(dossier_captures(), "adi_%s.json" % int(time.time()))
        with open(nom_adi, "w", encoding="utf-8") as f:
            f.write(rep[2])
        sortie("     Reponse adi enregistree (commandants, equipements, troupes) : %s" % nom_adi)
    except Exception as e:
        sortie("     (adi non enregistree : %s)" % e)
    inventaire = sorted(((u[0], u[1]) for u in d.get("gui", {}).get("I", [])), key=lambda t: -t[1])
    commandants = sorted(d.get("gli", {}).get("C", []), key=lambda c: c.get("ID", 0))
    res["inventaire"] = inventaire
    res["commandants"] = [(c.get("ID"), c.get("N")) for c in commandants]
    sortie("     Troupes disponibles (id : quantite), les plus nombreuses d'abord :")
    sortie("       " + "  ".join("%s:%s" % t for t in inventaire[:15]))
    sortie("     Commandants (numero du jeu -> identifiant, nom) :")
    for pos, c in enumerate(commandants, 1):
        sortie("       %2d -> %s  %s" % (pos, c.get("ID"), c.get("N")))
    if args.lecture_seule:
        sortie("Mode lecture seule : arret avant toute attaque.")
        return res

    # ---- 4. choix
    sortie("4/6  Choix de l'attaque")
    if args.auto:
        pos = int(await demander(args.commandant, "     Numero du commandant (colonne de gauche) : ", sortie, lire))
        if not 1 <= pos <= len(commandants):
            sortie("     Numero hors liste : arret.")
            return res
        cmd = commandants[pos - 1]
        lid = cmd["ID"]
        chemin_items = os.path.join(dossier_captures(), "items.json")
        unites = combat.charger_unites(chemin_items)
        effets = combat.effets_par_type(chemin_items)
        bonus_lu = combat.bonus_commandant(cmd, effets, combat.ZONE_TOUR)
        bonus = bonus_lu if args.avec_bonus else 0     # par defaut : plafond SANS bonus (jamais de depassement)
        niv_brut = combat.niveau_cible(d)
        niv = combat.niveau_effectif(niv_brut)
        nb = int(args.vagues) if args.vagues else 4
        stock = combat.inventaire_depuis_adi(d.get("gui", {}).get("I", []))
        vagues_calc, _ = combat.remplir_flanc_gauche(stock, unites, niv, bonus, nb, args.outil_quantite or 10,
                                                      args.outil if args.outil is not None else 614)
        sortie("     Remplissage auto : niveau cible %s (retenu %s), plafond %d par flanc "
               "(bonus du commandant lu : %d %%, %s)"
               % (niv_brut, niv, combat.soldats_flanc(niv, bonus), bonus_lu,
                  "APPLIQUE" if args.avec_bonus else "ignore par prudence, option --avec-bonus pour l'appliquer"))
        cra = construire_cra(cx, cy, tx, ty, kid, lid, 0, 0, 0)
        cra["A"] = [combat.vers_paquet(v) for v in vagues_calc]
        unite = quantite = "auto"
    elif modele is not None:
        if args.commandant is not None:
            pos = int(args.commandant)
            if not 1 <= pos <= len(commandants):
                sortie("     Numero hors liste : arret.")
                return res
            lid = commandants[pos - 1]["ID"]
        else:
            lid = modele["LID"]
            pos = next((i for i, c in enumerate(commandants, 1) if c.get("ID") == lid), None)
            if pos is None:
                sortie("     Le commandant du modele (id %s) n'est pas dans ta liste : arret." % lid)
                return res
        cra = json.loads(json.dumps(modele))
        cra.update({"SX": cx, "SY": cy, "TX": tx, "TY": ty, "KID": kid, "LID": lid})
        nb = len(cra.get("A", []))
        unite = quantite = "du modele"
    else:
        pos = int(await demander(args.commandant, "     Numero du commandant (colonne de gauche) : ", sortie, lire))
        if not 1 <= pos <= len(commandants):
            sortie("     Numero hors liste : arret.")
            return res
        lid = commandants[pos - 1]["ID"]
        unite = int(await demander(args.unite, "     Identifiant de l'unite a envoyer (flanc gauche) : ", sortie, lire))
        quantite = int(await demander(args.quantite, "     Quantite par vague : ", sortie, lire))
        nb = int(await demander(args.vagues, "     Nombre de vagues : ", sortie, lire))
        cra = construire_cra(cx, cy, tx, ty, kid, lid, nb, unite, quantite, args.outil, args.outil_quantite)
    texte = "%%xt%%%s%%cra%%1%%%s%%" % (jeu.zone, json.dumps(cra, separators=(",", ":")))
    res["cra"] = texte

    # ---- 5. confirmation
    sortie("5/6  Paquet d'attaque pret : tour (%d, %d), commandant n.%d (id %s), %d vague(s)" % (tx, ty, pos, lid, nb))
    for ligne in resume_armee(cra):
        sortie("     " + ligne)
    if not args.oui:
        rep_ok = (await lire("     Taper OUI pour envoyer l'attaque (autre chose = annuler) : ")).strip()
        if rep_ok != "OUI":
            sortie("     Annule. Rien n'a ete envoye.")
            return res
    await jeu.vider()
    await jeu.envoyer("cra", cra)
    rep = await jeu.attendre("cra", 15)

    # ---- 6. resultat
    sortie("6/6  Reponse du serveur")
    if not rep:
        sortie("     Aucune reponse au cra.")
        return res
    res["code"] = rep[1]
    d = json_ou_none(rep[2]) or {}
    if rep[1] == 0 and "AAM" in d:
        tt = d["AAM"]["M"].get("TT")
        res["tt"] = tt
        sortie("     ATTAQUE PARTIE : trajet %s s (mouvement %s)" % (tt, d["AAM"]["M"].get("MID")))
        sortie("     Contenu de la reponse (armee enregistree par le serveur, a me transmettre) :")
        sortie("     " + json.dumps(d["AAM"], separators=(",", ":"))[:1500])
        if args.sans_attendre:
            return res
        sortie("     J'attends l'arrivee et le rapport de combat (Ctrl+C pour arreter)...")
        fin = time.time() + (tt or 60) + 180
        vus = set()
        while time.time() < fin and vus != {"cat", "bls"}:
            m = await jeu.attendre({"cat", "bls"}, 5)
            if not m or m[0] in vus:
                continue
            vus.add(m[0])
            if m[0] == "cat":
                sortie("     Arrivee detectee (cat) : l'armee repart.")
            else:
                res["bls"] = resume_bls(m[2])
                sortie("     Rapport de combat (bls) : %s" % res["bls"])
    else:
        sortie("     Code %s : attaque REFUSEE ou reponse inattendue. Contenu brut (a me transmettre) :" % rep[1])
        sortie("     " + rep[2][:600])
    return res


# ----------------------------------------------------------------------------- lancement
def lire_args(argv=None):
    p = argparse.ArgumentParser(description="Test d'envoi sur un compte de test (voir l'en-tete du fichier).")
    p.add_argument("--chateau", nargs=2, type=int, metavar=("X", "Y"), required=True,
                   help="coordonnees de ton chateau dans le royaume choisi")
    p.add_argument("--royaume", type=int, default=0, help="0 vert, 1 sables, 2 glacier, 3 feu (defaut 0)")
    p.add_argument("--rayon", type=int, default=13, help="rayon de recherche en cases (defaut 13)")
    p.add_argument("--cible", nargs=2, type=int, metavar=("X", "Y"), help="forcer la cible")
    p.add_argument("--lecture-seule", action="store_true", help="s'arreter avant d'attaquer")
    p.add_argument("--niveau-min", type=int, default=0, help="niveau minimal des tours (defaut 0 = toutes)")
    p.add_argument("--apprendre", action="store_true",
                   help="enregistre l'attaque que TU lances dans le jeu (avec Remplir les vagues) comme modele")
    p.add_argument("--auto", action="store_true",
                   help="remplit comme le jeu (meilleur soldat, plafond du flanc gauche) + echelles sur la vague 1")
    p.add_argument("--avec-bonus", action="store_true",
                   help="avec --auto : applique le bonus de flanc du commandant (non verifie sur tous les cas)")
    p.add_argument("--modele", action="store_true", help="rejoue le modele enregistre sur la tour la plus proche")
    p.add_argument("--modele-fichier", default=None, help="chemin du modele (defaut captures/modele_cra.json)")
    p.add_argument("--ignorer-minuteur", action="store_true",
                   help="ne pas filtrer sur le minuteur (le serveur refusera si la tour n'est pas attaquable)")
    p.add_argument("--commandant", help="numero du commandant dans la liste du jeu")
    p.add_argument("--unite", help="identifiant de l'unite (flanc gauche)")
    p.add_argument("--quantite", help="quantite par vague")
    p.add_argument("--vagues", help="nombre de vagues")
    p.add_argument("--outil", type=int, default=None, help="identifiant d'un outil (1re vague)")
    p.add_argument("--outil-quantite", type=int, default=0)
    p.add_argument("--oui", action="store_true", help="ne pas demander de confirmation (a eviter)")
    p.add_argument("--sans-attendre", action="store_true", help="ne pas attendre le rapport de combat")
    p.add_argument("--socket", default=MOTIF_SOCKET, help="motif de l'adresse du WebSocket du jeu")
    p.add_argument("--zone", default=None, help="zone du serveur (ex. EmpireEx_3), detectee automatiquement sinon")
    return p.parse_args(argv)


async def principal(args):
    import websockets
    try:
        info = json.load(urllib.request.urlopen("%s/json/version" % CDP_HTTP, timeout=5))
    except Exception as e:
        print("Impossible de joindre Chrome sur le port 9222.")
        print("Lance d'abord outils/lancer_chrome.bat, connecte-toi au jeu, puis relance ce script.")
        print("Detail :", e)
        return
    async with websockets.connect(info["webSocketDebuggerUrl"], max_size=None) as ws:
        nav = Navigateur(ws)
        await nav.demarrer()
        jeu = Jeu(nav, args.zone)
        try:
            await scenario(jeu, args)
        except ErreurCDP as e:
            print("Erreur :", e)
            print("Verifie que tu es bien connecte au jeu dans CE Chrome, puis recommence.")


if __name__ == "__main__":
    try:
        asyncio.run(principal(lire_args()))
    except KeyboardInterrupt:
        print("\nArret.")
