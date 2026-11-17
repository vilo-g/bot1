#!/usr/bin/env python3
"""
interface.py : INTERFACE GRAPHIQUE locale du bot (page web sur TON PC seulement).

Lancement : outils\\lancer_interface.bat   (ou : py src\\interface.py)
La page s'ouvre dans le navigateur : http://127.0.0.1:8770

- Reglages des modules (tours, forteresses) : les memes que l'assistant de la console, enregistres dans config.json.
- Tableau de bord : choisir les modules, demarrer / arreter le bot, voir les attaques.
- Journal en direct : tout ce que le bot affiche (captures/journal_bot.txt) + les journaux des modules.

Securite : le serveur n'ecoute que sur 127.0.0.1 (ton PC), refuse les autres noms d'hote et les envois venant d'autres sites.
Aucun mot de passe n'est jamais demande ni enregistre ici : au demarrage, une fenetre de console s'ouvre pour le bot
(mot de passe seulement si la session a expire ; ensuite les modules attaquent sans rien demander).
Bibliotheque standard seulement (rien a installer).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tours        # noqa: E402
import forteresses  # noqa: E402
import combat       # noqa: E402
import bot          # noqa: E402
import coffre       # noqa: E402
import zipfile      # noqa: E402
import maj          # noqa: E402
import achats       # noqa: E402
import sessions     # noqa: E402
import recrutement  # noqa: E402
import materiaux    # noqa: E402
import defense      # noqa: E402
import transferts   # noqa: E402
import montee       # noqa: E402
import evenements   # noqa: E402
import khan         # noqa: E402,F401
import khan_defense  # noqa: E402

ICI = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(ICI, "interface.html")
PORT = 8770
DOSSIER_IMAGES = os.path.join(ICI, "images")
NOMS_UNITES = {215: "Vierge à bouclier", 2069: "Guerrier à arc court relique", 2029: "Guerrier à hache relique",
               6: "Frondeur vétéran", 35: "Lanceur de kunai renégat",
               10: "Horreur mortelle vétéran", 9: "Horreur démoniaque vétéran"}     # noms avec accents pour la page
VERSION = "2026-11-17-levmax1"   # doit etre egale a VERSION dans interface.html (la page previent si l'interface n'a pas ete relancee)
ETAT_PERIME = 30            # secondes : sans nouvelle du bot depuis plus longtemps, on le considere arrete
JOURNAUX = {"bot": lambda: bot.FICHIER_CONSOLE, "tours": lambda: tours.FICHIER_JOURNAL,
            "forteresses": lambda: forteresses.FICHIER_JOURNAL}
_verrou = threading.Lock()
_processus = {"p": None}
_maj_cache = {"t": 0, "res": None}
MAJ_CACHE = 600              # secondes entre deux verifications automatiques sur GitHub


def etat_maj(forcer=False):
    """Version installee / disponible sur GitHub (verification gardee 10 min, sauf si on la demande)."""
    t = time.time()
    if forcer or _maj_cache["res"] is None or t - _maj_cache["t"] > MAJ_CACHE:
        _maj_cache["res"], _maj_cache["t"] = maj.verifier(tours.RACINE), t
    return dict(_maj_cache["res"], verifie_a=time.strftime("%H:%M", time.localtime(_maj_cache["t"])))


def installer_maj():
    e = etat_bot()
    try:
        res = maj.installer(tours.RACINE, bot_en_marche=e.get("etat") not in ("arrete", None))
    except maj.ErreurMaj as ex:
        raise ErreurSaisie([str(ex)])
    _maj_cache["res"] = None
    return res


class ErreurSaisie(ValueError):
    """Liste d'erreurs lisibles (une par champ)."""
    def __init__(self, erreurs):
        super().__init__("; ".join(erreurs))
        self.erreurs = erreurs


# ============================================================================== verification des reglages envoyes par la page
def _entier(v, nom, mini=None, maxi=None, erreurs=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and v != int(v)):
        if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
            v = int(v.strip())
        else:
            erreurs.append("%s : nombre entier attendu." % nom)
            return None
    v = int(v)
    if (mini is not None and v < mini) or (maxi is not None and v > maxi):
        erreurs.append("%s : entre %s et %s." % (nom, mini, maxi))
        return None
    return v


def _commandants(v, erreurs):
    try:
        return tours.analyser_commandants(v if isinstance(v, str) else ";".join(str(x) for x in v))
    except (ValueError, TypeError) as e:
        erreurs.append("Commandants : %s" % e)
        return None


def _reglages_communs(src, nom, erreurs, b):
    """mode, presets ou cotes, cheval (+ distance des plumes) : communs aux deux modules."""
    mode = src.get("mode")
    if mode not in ("auto", "presets"):
        erreurs.append("%s : mode R (remplissage automatique) ou P (présélections)." % nom)
        return
    b["mode"] = mode
    if mode == "auto":
        cotes = combat.normaliser_cotes(src.get("cotes"))
        if cotes is None:
            erreurs.append("%s : choisis au moins un côté (flanc gauche, centre, flanc droit)." % nom)
        else:
            b["cotes"] = cotes
    else:
        p = src.get("presets")
        if not isinstance(p, list) or len(p) != tours.NB_PRESELECTIONS_DEMANDEES:
            erreurs.append("%s : %d numéros de présélection." % (nom, tours.NB_PRESELECTIONS_DEMANDEES))
        else:
            nums = [_entier(x, "%s, présélection vague %d" % (nom, i + 1), 1, tours.NB_PRESELECTIONS_MAX, erreurs) for i, x in enumerate(p)]
            if None not in nums:
                b["presets"] = nums
    if src.get("cheval") not in ("C", "F", "CF"):
        erreurs.append("%s : cheval C (pièces), F (plumes) ou CF." % nom)
    else:
        b["cheval"] = src["cheval"]
        if src["cheval"] == "CF":
            d = _entier(src.get("distance_plumes"), "%s, distance des plumes" % nom, 1, 1000, erreurs)
            if d is not None:
                b["distance_plumes"] = d


def _royaumes(v, permis, erreurs):
    if not isinstance(v, list) or not v or not all(isinstance(k, int) and not isinstance(k, bool) and k in permis for k in v):
        erreurs.append("Royaumes : coche au moins un royaume (%s)." % ", ".join(tours.ROYAUMES[k] for k in permis))
        return None
    return list(dict.fromkeys(v))


def normaliser_tours(data, ancienne=None):
    """Reglages du module tours envoyes par la page -> configuration complete (rubrique "tours"), ou ErreurSaisie."""
    ancienne = ancienne if isinstance(ancienne, dict) else {}
    erreurs = []
    cfg = {k: v for k, v in ancienne.items() if k not in ("commandants", "royaumes", "reglages")}
    cfg["commandants"] = _commandants(data.get("commandants", ""), erreurs)
    cfg["royaumes"] = _royaumes(data.get("royaumes"), (0, 1, 2, 3), erreurs)
    regl = dict(ancienne.get("reglages") or {}) if isinstance(ancienne.get("reglages"), dict) else {}
    for k in cfg["royaumes"] or []:
        src = (data.get("reglages") or {}).get(str(k)) or {}
        nom = "Royaume %s" % tours.ROYAUMES[k]
        b = {}
        _reglages_communs(src, nom, erreurs, b)
        sol = src.get("soldats")
        if (not isinstance(sol, list) or not 1 <= len(sol) <= tours.NB_SOLDATS_MAX or len(set(map(str, sol))) != len(sol)
                or not all(isinstance(x, int) and not isinstance(x, bool) and x in tours.IDS_UNITES_TOURS for x in sol)):
            erreurs.append("%s : choisis de 1 à %d unités différentes." % (nom, tours.NB_SOLDATS_MAX))
        elif b.get("mode") == "presets" and len(sol) != 1:
            erreurs.append("%s : une seule unité de remplacement en mode présélections." % nom)
        else:
            b["soldats"], b["soldat"] = list(sol), sol[0]
        if not isinstance(src.get("echelles"), bool):
            erreurs.append("%s : échelles oui ou non." % nom)
        else:
            b["echelles"] = src["echelles"]
        r = _entier(src.get("rayon"), "%s, distance maximale" % nom, 10, 500, erreurs)
        if r is not None and r % 10:
            erreurs.append("%s, distance maximale : un multiple de 10." % nom)
        elif r is not None:
            b["rayon"] = r
        regl[str(k)] = b
    cfg["reglages"] = regl
    dep = data.get("deplacement_accelere", ancienne.get("deplacement_accelere", False))
    if not isinstance(dep, bool):
        erreurs.append("Déplacement accéléré : oui ou non.")
    else:
        cfg["deplacement_accelere"] = dep
    achat = data.get("achat_echelles", ancienne.get("achat_echelles", False))
    if not isinstance(achat, bool):
        erreurs.append("Achat automatique d'échelles : oui ou non.")
    else:
        cfg["achat_echelles"] = achat
    cfg.setdefault("chateaux", {})
    cfg.setdefault("niveau_min", 0)
    cfg.setdefault("vagues_base", tours.NB_VAGUES_BASE)
    cfg.setdefault("avec_bonus", False)
    if not erreurs and not tours.config_valide(cfg):
        erreurs.append("Configuration incomplète.")
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


def normaliser_forteresses(data, ancienne=None, autres=None):
    """Reglages du module forteresses -> rubrique "forteresses", ou ErreurSaisie. `autres` : commandants du module tours."""
    ancienne = ancienne if isinstance(ancienne, dict) else {}
    erreurs = []
    cfg = {k: v for k, v in ancienne.items() if k not in ("commandants", "royaumes", "reglages", "reserve")}
    cfg["commandants"] = _commandants(data.get("commandants", ""), erreurs)
    cfg["royaumes"] = _royaumes(data.get("royaumes"), forteresses.ROYAUMES_FORTERESSES, erreurs)
    regl = dict(ancienne.get("reglages") or {}) if isinstance(ancienne.get("reglages"), dict) else {}
    for k in cfg["royaumes"] or []:
        src = (data.get("reglages") or {}).get(str(k)) or {}
        nom = "Royaume %s" % tours.ROYAUMES[k]
        b = {"echelles": False, "soldat": "rapide"}
        _reglages_communs(src, nom, erreurs, b)
        d = _entier(src.get("distance_max"), "%s, distance maximale" % nom, 1, 500, erreurs)
        if d is not None:
            b["distance_max"] = d
        v = _entier(src.get("vagues_remplies"), "%s, vagues à remplir" % nom, 1, 4, erreurs)
        if v is not None:
            b["vagues_remplies"] = v
        regl[str(k)] = b
    cfg["reglages"] = regl
    r = data.get("reserve") or {}
    sec = _entier(r.get("secondes", 0), "Réservation, secondes", 0, forteresses.RESERVE_MAX_SECONDES, erreurs)
    communs = forteresses.commandants_communs(cfg["commandants"] or [], autres if autres is not None else forteresses.autres_commandants())
    if sec:
        mx = _entier(r.get("max"), "Réservation, maximum", 1, max(1, len(cfg["commandants"] or []), len(communs)), erreurs)
        cfg["reserve"] = {"secondes": sec, "max": mx or 1}
    else:
        cfg["reserve"] = {"secondes": 0, "max": 1}
    cfg.setdefault("chateaux", {})
    cfg.setdefault("niveau_min", 0)
    cfg.setdefault("vagues_base", tours.NB_VAGUES_BASE)
    cfg.setdefault("avec_bonus", False)
    if not erreurs and not forteresses.config_valide(cfg):
        erreurs.append("Configuration incomplète.")
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


def normaliser_achats(data, ancienne=None):
    """Reglages du module achats -> rubrique "achats", ou ErreurSaisie."""
    ancienne = ancienne if isinstance(ancienne, dict) else {}
    erreurs = []
    cfg = {k: v for k, v in ancienne.items() if k not in ("seuil", "royaumes", "parts", "engins")}
    seuil = _entier(data.get("seuil"), "Seuil de pièces", achats.SEUIL_MIN, achats.PIECES_MAX, erreurs)
    if seuil is not None:
        cfg["seuil"] = seuil
    cfg["royaumes"] = _royaumes(data.get("royaumes"), (0, 1, 2, 3), erreurs)
    parts, engins = {}, {}
    total = 0
    for k in cfg["royaumes"] or []:
        nom = "Royaume %s" % tours.ROYAUMES[k]
        p = _entier((data.get("parts") or {}).get(str(k)), "%s, part des pièces" % nom, 1, 100, erreurs)
        if p is not None:
            parts[str(k)] = p
            total += p
        src = (data.get("engins") or {}).get(str(k))
        if not isinstance(src, dict) or not src:
            erreurs.append("%s : choisis au moins un engin." % nom)
            continue
        e, t = {}, 0
        for w, v in src.items():
            if not str(w).isdigit() or int(w) not in achats.ENGINS:
                erreurs.append("%s : engin inconnu (%s)." % (nom, w))
                continue
            v = _entier(v, "%s, %s" % (nom, achats.NOMS_ENGINS[int(w)]), 1, 100, erreurs)
            if v is not None:
                e[str(int(w))] = v
                t += v
        if e and t != 100:
            erreurs.append("%s : la répartition des engins doit faire 100 %% (actuellement %d %%)." % (nom, t))
        engins[str(k)] = e
    if cfg["royaumes"] and len(parts) == len(cfg["royaumes"]) and total != 100:
        erreurs.append("La répartition entre royaumes doit faire 100 %% (actuellement %d %%)." % total)
    cfg["parts"], cfg["engins"] = parts, engins
    cfg.setdefault("commandants", [])
    if not erreurs and not achats.config_valide(cfg):
        erreurs.append("Configuration incomplète.")
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


_MAX_MONTEE = {}


def niveaux_max_montee():
    """Dernier niveau d'une tour de chaque royaume (donnees du jeu, lues une seule fois) : {"3": 71, ...}."""
    if not _MAX_MONTEE:
        mv = montee.max_victoires_par_royaume()
        _MAX_MONTEE.update({str(k): montee.niveau(k, v) for k, v in mv.items()} or {"vide": None})
    return {k: v for k, v in _MAX_MONTEE.items() if k != "vide"}


def normaliser_montee(data, ancienne=None):
    """Page Tours lev max -> rubrique "montee", ou ErreurSaisie."""
    erreurs = []
    cfg = {k: v for k, v in ancienne.items() if k != "active"} if isinstance(ancienne, dict) else {}
    kid = data.get("kid")
    if not (isinstance(kid, int) and not isinstance(kid, bool) and kid in tours.ROYAUMES):
        erreurs.append("Monde : choisis le monde de la tour.")
    x = _entier(data.get("x"), "Coordonnée X de la tour", 0, 2000, erreurs)
    y = _entier(data.get("y"), "Coordonnée Y de la tour", 0, 2000, erreurs)
    cmds = _commandants(data.get("commandants", ""), erreurs)
    p = data.get("passes")
    if not isinstance(p, list) or not p or not all(c in montee.PASSES for c in p):
        erreurs.append("Passe-temps : coche au moins un passe-temps (24 h, 5 h, 1 h, 30 min, 10 min, 5 min ou 1 min).")
    ordre = data.get("ordre_passes", "forts")
    if ordre not in montee.ORDRES_PASSES:
        erreurs.append("Passe-temps : choisis « les plus forts d'abord » ou « les plus faibles d'abord ».")
    mx = montee.NIVEAUX_MAX.get(kid, montee.NIVEAU_MAX_SAISIE) if isinstance(kid, int) else montee.NIVEAU_MAX_SAISIE
    nv = _entier(data.get("niveau_vise", montee.NIVEAU_VISE), "Niveau visé (%d au plus pour une tour %s)"
                 % (mx, montee.DU_MONDE.get(kid, "de ce monde")), 1, mx, erreurs)
    if erreurs:
        raise ErreurSaisie(erreurs)
    cfg.update({"kid": kid, "x": x, "y": y, "commandants": cmds,
                "passes": [c for c in montee.PASSES if c in p], "niveau_vise": nv, "ordre_passes": ordre})
    if not montee.config_valide(cfg):
        raise ErreurSaisie(["Réglages incomplets."])
    return cfg


def normaliser_lanceur(data, ancienne=None):
    erreurs = []
    noms = [m.LANCEUR_NOM for m in bot.a_choisir()]
    mods = data.get("modules")
    if not isinstance(mods, list) or not mods or not all(m in noms for m in mods):
        erreurs.append("Modules : coche au moins un module.")
    else:
        erreurs.extend("Modules : " + x for x in bot.incompatibilites(mods))
    duree = (data.get("duree") or "").strip() if isinstance(data.get("duree", ""), str) else None
    if duree is None:
        erreurs.append("Durée : texte attendu (ex. 3h, 90m, 2h30) ou vide.")
    elif duree:
        try:
            tours.analyser_duree(duree)
        except ValueError as e:
            erreurs.append("Durée : %s" % e)
    ma = data.get("max_attaques")
    if ma in (None, "", 0):
        ma = None
    else:
        ma = _entier(ma, "Nombre d'attaques maximum", 1, 100000, erreurs)
    if not isinstance(data.get("simulation", False), bool):
        erreurs.append("Simulation : oui ou non.")
    if not isinstance(data.get("ecran_allume", True), bool):
        erreurs.append("Écran allumé : oui ou non.")
    if erreurs:
        raise ErreurSaisie(erreurs)
    cfg = dict(ancienne) if isinstance(ancienne, dict) else {}
    cfg.update({"modules": [n for n in noms if n in mods], "duree": duree, "max_attaques": ma,
                "simulation": bool(data.get("simulation", False)), "ecran_allume": bool(data.get("ecran_allume", True))})
    return cfg


# ============================================================================== etat du bot, journaux, demarrage / arret
def etat_bot(maintenant=None):
    maintenant = maintenant or time.time()
    try:
        with open(bot.FICHIER_ETAT, encoding="utf-8") as f:
            e = json.load(f)
    except (OSError, ValueError):
        e = {"etat": "arrete"}
    p = _processus["p"]
    vivant_ici = p is not None and p.poll() is None
    if e.get("etat") in ("en_marche", "connexion") and maintenant - float(e.get("maj", 0)) > ETAT_PERIME and not vivant_ici:
        e = {"etat": "arrete", "remarque": "plus de nouvelles du bot depuis %d s" % (maintenant - float(e.get("maj", 0)))}
    if vivant_ici and (e.get("etat") == "arrete" or float(e.get("maj", 0)) < _processus.get("t", 0)):
        e = {"etat": "demarrage"}                     # lance depuis l'interface, pas encore de nouvelles de ce lancement
    e["arret_demande"] = os.path.exists(bot.FICHIER_ARRET)
    return e


def lire_journal(source, pos=-1, max_lignes=400):
    """Lignes ajoutees au journal depuis l'octet `pos` (-1 = les dernieres lignes). Renvoie (nouvelle position, lignes)."""
    chemin = JOURNAUX[source]()
    try:
        taille = os.path.getsize(chemin)
    except OSError:
        return 0, []
    if pos < 0 or pos > taille:                       # debut, ou fichier recommence
        pos = max(0, taille - 120000)
        debut = True
    else:
        debut = False
    with open(chemin, "rb") as f:
        f.seek(pos)
        brut = f.read(min(taille - pos, 2000000))
    fin = brut.rfind(b"\n") + 1                        # seulement des lignes completes
    texte = brut[:fin].decode("utf-8", errors="replace")
    lignes = texte.splitlines()
    if debut and pos > 0 and lignes:
        lignes = lignes[1:]                            # premiere ligne coupee
    return pos + fin, lignes[-max_lignes:]


def arguments_lancement(lanceur):
    args = [sys.executable, os.path.join(ICI, "bot.py"), "--modules", ";".join(lanceur["modules"]), "--config-ok"]
    if lanceur.get("duree"):
        args += ["--duree", lanceur["duree"]]
    if lanceur.get("max_attaques"):
        args += ["--max-attaques", str(lanceur["max_attaques"])]
    if lanceur.get("simulation"):
        args.append("--simulation")
    if lanceur.get("ecran_allume") is False:
        args.append("--sans-ecran-allume")
    return args


def normaliser_defense(data, ancienne=None):
    """Reglages du module defense -> rubrique "defense", ou ErreurSaisie."""
    ancienne = ancienne if isinstance(ancienne, dict) else {}
    erreurs = []
    cfg = {k: v for k, v in ancienne.items() if k not in ("seuil", "porte", "secondes_porte", "commandants_royaumes", "min_par_type")}
    mp = _entier(data.get("min_par_type", defense.MIN_PAR_TYPE), "Soldats minimum par type", 1, defense.SEUIL_MAX, erreurs)
    if mp is not None:
        cfg["min_par_type"] = mp
    cr = {}
    for k, v in (data.get("commandants_royaumes") or {}).items():
        if str(k) in ("0", "1", "2", "3") and v not in (None, ""):
            n = _entier(v, "Commandant prioritaire %s" % tours.ROYAUMES[int(k)], 1, 99, erreurs)
            if n is not None:
                cr[str(k)] = n
    cfg["commandants_royaumes"] = cr
    seuil = _entier(data.get("seuil"), "Nombre de soldats de l'attaque", 1, defense.SEUIL_MAX, erreurs)
    sp = _entier(data.get("secondes_porte", defense.SECONDES_PORTE), "Secondes avant l'arrivée (porte)", 5, 600, erreurs)
    if seuil is not None:
        cfg["seuil"] = seuil
    if sp is not None:
        cfg["secondes_porte"] = sp
    cfg["porte"] = bool(data.get("porte"))
    cfg["commandants"] = []
    if not erreurs and not defense.config_valide(cfg):
        erreurs.append("Configuration incomplète.")
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


def normaliser_transferts(data, ancienne=None):
    """Reglages du module transferts -> rubrique "transferts", ou ErreurSaisie."""
    erreurs = []
    cfg = {k: v for k, v in (ancienne if isinstance(ancienne, dict) else {}).items() if k not in ("min_envoi", "accelerer", "passes")}
    m = _entier(data.get("min_envoi", transferts.MIN_ENVOI), "Quantité minimum par envoi", 1, 100000000, erreurs)
    if m is not None:
        cfg["min_envoi"] = m
    cfg["accelerer"] = bool(data.get("accelerer"))
    passes = data.get("passes", transferts.PASSES_DEFAUT)
    if not isinstance(passes, list) or not all(isinstance(x, str) and x in transferts.PASSES for x in passes):
        erreurs.append("Passe-temps inconnus.")
    else:
        cfg["passes"] = sorted(set(passes), key=transferts.PASSES.get, reverse=True)
        if cfg["accelerer"] and not cfg["passes"]:
            erreurs.append("Choisis au moins un passe-temps pour accélérer les envois.")
    cfg["commandants"] = []
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


NOMS_RECRUTEMENT = {"MeadRanger": "Valkyrie pisteuse", "MeadShieldMaiden": "Vierge au bouclier", "MeadBow": "Valkyrie franc-tireuse",
                    "MeadMace": "Protecteur du nord", "RelicShortBow": "Guerrier à arc court relique",
                    "RelicAxe": "Guerrier à hache relique", "SceatFameShield": "Tour de gloire", "SceatAttWallMelee": "Canon lance-grappin",
                    "SceatAttMoatAttRange": "Hwacha", "SceatAttWallAttRange": "Mortier de siège",
                    "SceatSuppAttWaves": "Chariot de guerre", "SceatSuppAttPowerYard": "Lance-flammes d'assaut"}
_catalogue = []


def catalogue_recrutement():
    """Types proposes dans la page Recrutement : {"soldats": [{"type","nom","niveaux"}], "engins": [...]}."""
    if not _catalogue:
        try:
            _catalogue.append(recrutement.charger_catalogue())
        except (OSError, ValueError, KeyError):
            return {"soldats": [], "engins": []}
    cat = _catalogue[0]
    res = {}
    for cle, lieu in (("soldats", recrutement.CASERNE), ("engins", recrutement.ATELIER)):
        res[cle] = [{"type": t, "nom": NOMS_RECRUTEMENT[t], "niveaux": sorted({n for n, _ in cat[lieu][t]}),
                     "image": ("recrutement/%s.png" % t) if os.path.isfile(os.path.join(DOSSIER_IMAGES, "recrutement", t + ".png"))
                     else None} for t in NOMS_RECRUTEMENT if t in cat[lieu]]
    return res


def avant_postes_connus():
    """Avant-postes du compte vus par le bot (captures/chateaux.json, ecrit au lancement du module recrutement)."""
    try:
        with open(recrutement.FICHIER_CHATEAUX, encoding="utf-8") as f:
            d = json.load(f)
        return [a for a in d.get("avant_postes") or [] if isinstance(a, dict) and a.get("n") in recrutement.AVANT_POSTES]
    except (OSError, ValueError, AttributeError):
        return None


def normaliser_recrutement(data, ancienne=None):
    """Reglages du module recrutement -> rubrique "recrutement" (types choisis royaume par royaume), ou ErreurSaisie."""
    erreurs = []
    cat = catalogue_recrutement()
    cfg = {k: v for k, v in (ancienne if isinstance(ancienne, dict) else {}).items()
           if k not in ("royaumes", "avant_postes", "reglages", "soldats", "engins", "quantite_soldats", "quantite_engins", "aide")}
    roy = data.get("royaumes", [])
    aps = data.get("avant_postes", [])
    if (not isinstance(roy, list) or not all(isinstance(k, int) and k in tours.ROYAUMES for k in roy)
            or not isinstance(aps, list) or not all(isinstance(n, int) and n in recrutement.AVANT_POSTES for n in aps)):
        raise ErreurSaisie(["Royaumes ou avant-postes invalides."])
    if not roy and not aps:
        raise ErreurSaisie(["Choisis au moins un royaume ou un avant-poste."])
    cfg["royaumes"] = sorted(set(roy))
    cfg["avant_postes"] = sorted(set(aps))
    cfg["reglages"] = {}
    recus = data.get("reglages") if isinstance(data.get("reglages"), dict) else {}
    for k in recrutement.cles_config(cfg):
        nom_k = tours.ROYAUMES[k] if isinstance(k, int) else "avant-poste " + k[2:]
        r = recus.get(str(k)) if isinstance(recus.get(str(k)), dict) else {}
        bloc = {}
        for cle, quoi in (("soldats", "soldat"), ("engins", "engin")):
            connus = {e["type"]: e for e in cat[cle]}
            liste = []
            for e in r.get(cle) if isinstance(r.get(cle), list) else []:
                if not isinstance(e, dict) or e.get("type") not in connus:
                    erreurs.append("%s : type de %s inconnu." % (nom_k, quoi))
                    continue
                if e["type"] not in [x["type"] for x in liste]:
                    liste.append({"type": e["type"], "niveau_max": None})   # toujours le plus haut niveau debloque
            bloc[cle] = liste
        if not bloc["soldats"] and not bloc["engins"]:
            erreurs.append("%s : choisis au moins un type de soldat ou d'engin." % nom_k)
        cfg["reglages"][str(k)] = bloc
    cfg["commandants"] = []
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


NOMS_MATERIAUX = {1: "Bois raffiné", 2: "Pierre raffinée", 3: "Vis", 4: "Poudre noire", 5: "Scie", 6: "Perceuse",
                  7: "Pied-de-biche", 8: "Lanières de cuir", 9: "Chaînes", 10: "Plaques de métal",
                  11: "Tuiles d'écailles de dragon", 12: "Amulettes métamorphes"}
_recettes = []


def catalogue_materiaux():
    """Materiaux proposes dans la page Materiaux : {partie: [{"groupe","nom","image","court","long"}]} + prix des emplacements."""
    if not _recettes:
        try:
            _recettes.append(materiaux.charger_recettes())
        except (OSError, ValueError, KeyError):
            return {"parties": {p: [] for p in materiaux.PARTIES}, "prix": {}}
    recettes, prix = _recettes[0]
    res = {p: [] for p in materiaux.PARTIES}
    for g, (p, cle, _) in sorted(materiaux.MATERIAUX.items()):
        types = {r["type"] for r in recettes.values() if r["groupe"] == g}
        res[p].append({"groupe": g, "nom": NOMS_MATERIAUX[g], "court": "Short" in types, "long": "Long" in types,
                       "image": ("materiaux/%s.png" % cle) if os.path.isfile(os.path.join(DOSSIER_IMAGES, "materiaux", cle + ".png"))
                       else None})
    return {"parties": res, "prix": {p: prix.get(q) for p, q in materiaux.PARTIES.items()}}


def normaliser_materiaux(data, ancienne=None):
    """Reglages du module materiaux -> rubrique "materiaux", ou ErreurSaisie."""
    erreurs = []
    cfg = {k: v for k, v in (ancienne if isinstance(ancienne, dict) else {}).items() if k not in materiaux.PARTIES}
    for p in materiaux.PARTIES:
        bloc = data.get(p) if isinstance(data.get(p), dict) else {}
        cases = bloc.get("cases") if isinstance(bloc.get("cases"), list) else []
        if len(cases) > materiaux.CASES:
            erreurs.append("Trop de cases (%s)." % materiaux.NOMS_PARTIES[p])
            cases = cases[:materiaux.CASES]
        propres = []
        for c in cases:
            if c is None:
                propres.append(None)
                continue
            if (not isinstance(c, dict) or c.get("manuel") not in materiaux.MANUELS
                    or materiaux.MATERIAUX.get(c.get("groupe"), (None,))[0] != p):
                erreurs.append("Case invalide (%s)." % materiaux.NOMS_PARTIES[p])
                propres.append(None)
                continue
            propres.append({"groupe": c["groupe"], "manuel": c["manuel"]})
        propres += [None] * (materiaux.CASES - len(propres))
        cfg[p] = {"cases": propres, "acheter": bool(bloc.get("acheter"))}
    if not any(c for p in materiaux.PARTIES for c in cfg[p]["cases"]):
        erreurs.append("Choisis au moins un matériau à fabriquer.")
    cfg["commandants"] = []
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


NOMS_ENGINS_NOMADES = {740: "Échelle en tronc", 168: "Échelle en tronc améliorée", 142: "Échelle combinée d'élite",
                       738: "Bélier de feu", 167: "Bélier de feu amélioré", 141: "Bélier combiné d'élite",
                       739: "Boucliers roulants", 169: "Boucliers roulants améliorés", 143: "Bouclier combiné d'élite", 635: "Mur de boucliers"}


def _image(dossier, nom):
    return "images/%s/%s.png" % (dossier, nom) if os.path.exists(os.path.join(DOSSIER_IMAGES, dossier, "%s.png" % nom)) else None


def _engins_nomades(liste, nom, cases, erreurs):
    """[[id, quantite], ...] de la page -> liste validee (cases vides ignorees)."""
    res = []
    for p in liste if isinstance(liste, list) else []:
        if not isinstance(p, list) or len(p) != 2 or p[0] in (None, "", 0):
            continue
        if p[0] not in evenements.ENGINS:
            erreurs.append("%s : engin inconnu." % nom)
            return None
        if cases == 2 and p[0] not in evenements.ENGINS_FLANC:
            erreurs.append("%s : pas de bélier sur un flanc (les béliers vont au centre)." % nom)
            return None
        q = _entier(p[1], "%s, %s" % (nom, NOMS_ENGINS_NOMADES[p[0]]), 1, evenements.QUANTITE_MAX, erreurs)
        if q is None:
            return None
        res.append([p[0], q])
    if len(res) > cases or len({p[0] for p in res}) != len(res):
        erreurs.append("%s : %d engins différents au plus." % (nom, cases))
        return None
    place = evenements.PLACE_ENGINS["flanc" if cases == 2 else "centre"]
    if sum(p[1] for p in res) > place:
        erreurs.append("%s : %d engins au plus en tout (%d choisis)." % (nom, place, sum(p[1] for p in res)))
        return None
    return res


def normaliser_evenements(data, ancienne=None):
    """Page Evenements -> rubrique "evenements" ({"nomades": {...}, "khan": {...}}), ou ErreurSaisie.
    Une partie absente de la page (pas remplie) est retiree des reglages."""
    erreurs = []
    cfg = dict(ancienne) if isinstance(ancienne, dict) else {}
    for genre in ("nomades", "khan"):
        if isinstance(data.get(genre), dict):
            cfg[genre] = _normaliser_camp(data[genre], genre, erreurs)
        else:
            cfg.pop(genre, None)
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


def _normaliser_camp(d, genre, erreurs):
    khan = genre == "khan"
    nb_avant = len(erreurs)
    n = {}
    if not khan:
        n["x"] = _entier(d.get("x"), "Nomades, coordonnée X du camp", 0, 2000, erreurs)
        n["y"] = _entier(d.get("y"), "Nomades, coordonnée Y du camp", 0, 2000, erreurs)
    n["commandants"] = _commandants(d.get("commandants", ""), erreurs)
    _reglages_communs(dict(d, mode="auto"), "Nomades", erreurs, n)     # nomades : remplissage automatique seulement
    if n.get("mode") == "auto":
        s = d.get("soldats")
        if not (isinstance(s, list) and 1 <= len(s) <= tours.NB_SOLDATS_MAX and len(set(s)) == len(s)
                and all(isinstance(u, int) and not isinstance(u, bool) and u > 0 for u in s)):
            erreurs.append("Nomades : choisis de 1 à %d unités de soldats." % tours.NB_SOLDATS_MAX)
        else:
            n["soldats"] = list(s)
        s2 = d.get("soldats_suite", [])
        if not (isinstance(s2, list) and len(s2) <= tours.NB_SOLDATS_MAX and len(set(s2)) == len(s2)
                and all(isinstance(u, int) and not isinstance(u, bool) and u > 0 for u in s2)):
            erreurs.append("Nomades : soldats des vagues suivantes invalides.")
        else:
            n["soldats_suite"] = list(s2)
        e = d.get("engins") if isinstance(d.get("engins"), dict) else {}
        n["engins"] = {"flanc": _engins_nomades(e.get("flanc"), "Engins de la vague 1, flanc", 2, erreurs),
                       "centre": _engins_nomades(e.get("centre"), "Engins de la vague 1, centre", 3, erreurs)}
        k = d.get("coffres") if isinstance(d.get("coffres"), dict) and not khan else {}
        types = [w for w in evenements.COFFRES if w in (k.get("types") or [])]
        if k.get("actif") and not types:
            erreurs.append("Nomades : coche au moins un coffre, ou désactive les coffres.")
        n["coffres"] = {"actif": bool(k.get("actif")), "types": types, "ordre": "faibles" if k.get("ordre") == "faibles" else "forts"}
    p = d.get("passes")
    if not isinstance(p, list) or not p or not all(c in montee.PASSES for c in p):
        erreurs.append("Nomades, passe-temps : coche au moins un passe-temps (24 h, 5 h, 1 h, 30 min ou 10 min).")
    n["remplir_cour"] = bool(d.get("remplir_cour"))
    if khan:
        n.pop("coffres", None)
        n["bannieres"] = [w for w in evenements.BANNIERES if w in (d.get("bannieres") or [])]
        if not n["bannieres"]:
            erreurs.append("Khan : coche au moins un outil de rage.")
        n["declencher"] = bool(d.get("declencher"))
        if n["declencher"]:
            n["en_cours"] = _entier(d.get("en_cours", 3), "Khan, attaques du Khan gardées en route", 1, evenements.EN_COURS_MAX, erreurs)
            n["objectif_declenchements"] = _entier(d.get("objectif_declenchements", d.get("objectif_defenses", 0)), "Khan, objectif en déclenchements", 0, 10 ** 9, erreurs)
            n["objectif_points"] = _entier(d.get("objectif_points", 0), "Khan, objectif en points nomades", 0, 10 ** 12, erreurs)
            n["defense"] = _defense_khan(d.get("defense"), erreurs)
    else:
        n["max_attaques"] = _entier(d.get("max_attaques", 0), "Nomades, arrêt après un nombre d'attaques", 0, 10 ** 6, erreurs)
        n["max_tablettes"] = _entier(d.get("max_tablettes", 0), "Nomades, arrêt après un nombre de tablettes", 0, 10 ** 9, erreurs)
    if len(erreurs) > nb_avant:
        if khan:
            erreurs[nb_avant:] = [e.replace("Nomades", "Khan") for e in erreurs[nb_avant:]]
        return None
    n["passes"] = [c for c in montee.PASSES if c in p]
    if not (evenements.khan_valides(n) if khan else evenements.nomades_valides(n)):
        erreurs.append("%s : réglages incomplets." % ("Khan" if khan else "Nomades"))
        return None
    return n


NOMS_COTES_DEF = {"L": "flanc gauche", "M": "centre", "R": "flanc droit"}


def _case_def(c, genre, nom, erreurs):
    """[engin, quantite] (ou vide) d'une case de la defense Khan -> case validee, None (vide) ou False (erreur)."""
    if not isinstance(c, list) or len(c) != 2 or c[0] in (None, "", 0, -1):
        return None
    if c[0] not in khan_defense.ENGINS_DEFENSE or khan_defense.ENGINS_DEFENSE[c[0]][1] != genre:
        erreurs.append("Khan, défense, %s : cet engin ne va pas dans cette case." % nom)
        return False
    q = _entier(c[1], "Khan, défense, %s (%s)" % (nom, khan_defense.ENGINS_DEFENSE[c[0]][0]), 1, khan_defense.QUANTITE_CASE_MAX, erreurs)
    return False if q is None else [c[0], q]


def _defense_khan(d, erreurs):
    """Reglages de la defense Khan de la page -> dict valide (khan_defense.defense_valide), ou None (erreurs ajoutees)."""
    d = d if isinstance(d, dict) else {}
    nb = len(erreurs)
    res = {"mur": {}, "douves": {}, "secours": {}}
    mur = d.get("mur") if isinstance(d.get("mur"), dict) else {}
    for c in "LMR":
        s = mur.get(c) if isinstance(mur.get(c), dict) else {}
        cases = list(s.get("cases") or []) if isinstance(s.get("cases"), list) else []
        cases = (cases + [None] * khan_defense.CASES[c])[:khan_defense.CASES[c]]
        r = [None if c != "M" and i == 4 else _case_def(x, khan_defense.genre_case(c, i), "%s, case %d" % (NOMS_COTES_DEF[c], i + 1), erreurs)
             for i, x in enumerate(cases)]                    # 5e case des flancs : pas contre le Khan (seulement contre les seigneurs)
        res["mur"][c] = {"cases": r, "soldats": _entier(s.get("soldats", 0), "Khan, défense, %s : %% des soldats" % NOMS_COTES_DEF[c], 0, 100, erreurs),
                         "melee": _entier(s.get("melee", 50), "Khan, défense, %s : %% de mêlée" % NOMS_COTES_DEF[c], 0, 100, erreurs)}
    if len(erreurs) == nb and sum(res["mur"][c]["soldats"] for c in "LMR") != 100:
        erreurs.append("Khan, défense : la répartition des soldats (flanc gauche + centre + flanc droit) doit faire 100 %%, pas %d %%."
                       % sum(res["mur"][c]["soldats"] for c in "LMR"))
    dv = d.get("douves") if isinstance(d.get("douves"), dict) else {}
    for c in "LMR":
        res["douves"][c] = _case_def(dv.get(c), "douves", "douves (%s)" % NOMS_COTES_DEF[c], erreurs)
    cour = d.get("cour") if isinstance(d.get("cour"), dict) else {}
    malles = (list(cour.get("malles") or []) + [0, 0, 0])[:3] if isinstance(cour.get("malles"), list) else [0, 0, 0]
    res["cour"] = {"malles": [_entier(q if q not in (None, "") else 0, "Khan, défense, cour : malles de la case %d" % (i + 1), 0,
                                      khan_defense.QUANTITE_CASE_MAX, erreurs) for i, q in enumerate(malles)],
                   "melee": _entier(cour.get("melee", 50), "Khan, défense, cour : % de mêlée", 0, 100, erreurs)}
    sec = d.get("secours") if isinstance(d.get("secours"), dict) else {}
    for g in ("mur", "porte", "douves"):
        w = sec.get(g)
        if w in (None, "", 0, -1):
            res["secours"][g] = None
        elif w in khan_defense.ENGINS_DEFENSE and khan_defense.ENGINS_DEFENSE[w][1] == g:
            res["secours"][g] = w
        else:
            erreurs.append("Khan, défense : engin de secours invalide (%s)." % g)
    ds = [x for x in khan_defense.IDS_DEFENSEURS if x in (d.get("defenseurs") or [])]
    if not ds:
        erreurs.append("Khan, défense : choisis au moins un type de soldat qui défend.")
    res["defenseurs"] = ds
    a = d.get("abri") if isinstance(d.get("abri"), dict) else {}
    res["abri"] = {"x": _entier(a.get("x"), "Khan, défense : coordonnée X du joueur protégé", 0, 2000, erreurs),
                   "y": _entier(a.get("y"), "Khan, défense : coordonnée Y du joueur protégé", 0, 2000, erreurs)}
    res["achat_malles"] = _entier(d.get("achat_malles", 0), "Khan, défense : malles à acheter au démarrage", 0, khan_defense.ACHAT_MALLES_MAX, erreurs)
    res["achat_engins"] = bool(d.get("achat_engins", True))
    if len(erreurs) > nb or any(x is False for c in "LMR" for x in res["mur"][c]["cases"]) or any(res["douves"][c] is False for c in "LMR"):
        return None
    return res


def evenements_valides(c):
    return isinstance(c, dict) and evenements.nomades_valides(c.get("nomades"))


def demarrer_bot(lanceur, popen=subprocess.Popen):
    with _verrou:
        if etat_bot()["etat"] != "arrete":
            raise ErreurSaisie(["Le bot tourne déjà : arrête-le d'abord."])
        tout = tours.lire_config()
        for nom in lanceur["modules"]:
            m = bot.par_nom()[nom]
            rub, valide = {"tours": ("tours", tours.config_valide),
                           "forteresses": (forteresses.RUBRIQUE, forteresses.config_valide),
                           "achats": (achats.RUBRIQUE, achats.config_valide),
                           "defense": (defense.RUBRIQUE, defense.config_valide),
                           "transferts": (transferts.RUBRIQUE, transferts.config_valide),
                           "recrutement": (recrutement.RUBRIQUE, recrutement.config_valide),
                           "materiaux": (materiaux.RUBRIQUE, materiaux.config_valide),
                           "montee": (montee.RUBRIQUE, montee.config_valide),
                           "evenements": (evenements.RUBRIQUE, evenements_valides),
                           "khan": (evenements.RUBRIQUE, lambda c: isinstance(c, dict) and evenements.khan_valides(c.get("khan")))}[nom]
            if not valide((tout or {}).get(rub)):
                raise ErreurSaisie(["Module %s : réglages incomplets, enregistre-les d'abord."
                                    % ({"montee": "Tours lev max", "evenements": "Événements", "khan": "Khan"}.get(nom, m.LANCEUR_NOM))])
            if nom == "montee":
                kid = tout[rub]["kid"]
                if not tours.reglages_valides(((tout.get("tours") or {}).get("reglages") or {}).get(str(kid))):
                    raise ErreurSaisie(["Module Tours lev max : l'armée %s n'est pas réglée dans la page Tours (coche ce royaume, "
                                        "règle-le, enregistre ; tu peux le décocher ensuite)." % montee.DU_MONDE[kid]])
        try:
            os.remove(bot.FICHIER_ARRET)
        except OSError:
            pass
        options = {"cwd": tours.RACINE}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_CONSOLE      # fenetre du bot : mot de passe eventuel
        _processus["t"] = time.time()
        _processus["p"] = popen(arguments_lancement(lanceur), **options)
        return {"ok": True}


def arreter_bot():
    os.makedirs(os.path.dirname(bot.FICHIER_ARRET), exist_ok=True)
    with open(bot.FICHIER_ARRET, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    return {"ok": True}


def tout_etat():
    tout = tours.lire_config()
    tout = tout if isinstance(tout, dict) else {}
    prix = achats.prix_engins() if os.path.exists(tours.FICHIER_ITEMS) else {w: p for w, (_, _, p) in achats.ENGINS.items()}
    return {"config": {"tours": tout.get("tours"), "forteresses": tout.get(forteresses.RUBRIQUE), "lanceur": tout.get(bot.RUBRIQUE),
                       "achats": tout.get(achats.RUBRIQUE), "defense": tout.get(defense.RUBRIQUE),
                       "transferts": tout.get(transferts.RUBRIQUE), "recrutement": tout.get(recrutement.RUBRIQUE),
                       "materiaux": tout.get(materiaux.RUBRIQUE), "montee": tout.get(montee.RUBRIQUE),
                       "evenements": tout.get(evenements.RUBRIQUE)},
            "evenements": {"engins": [[w, NOMS_ENGINS_NOMADES[w], evenements.ENGINS[w][1], _image("engins", w), famille]
                                      for famille, ids in evenements.FAMILLES_ENGINS.items() for w in ids],
                           "logo_nomades": _image("evenements", "nomades"), "logo_khan": _image("evenements", "khan"),
                           "bannieres": [[w, {106: "Bannière de rage", 107: "Coffre du pilleur"}[w], b, _image("engins", w)]
                                         for w, b in evenements.BANNIERES.items()],
                           "coffres": [[w, b] for w, b in evenements.COFFRES.items()],
                           "defense_engins": [[w, nom, genre, bonus, _image("engins", w)] for w, (nom, genre, bonus) in khan_defense.ENGINS_DEFENSE.items()],
                           "defenseurs": [[c, nom, len(ids), _image("recrutement", t) or _image("unites", ids[-1])]
                                          for c, nom, t, ids in khan_defense.DEFENSEURS],
                           "en_cours_max": evenements.EN_COURS_MAX, "quantite_case_max": khan_defense.QUANTITE_CASE_MAX,
                           "achat_malles_max": khan_defense.ACHAT_MALLES_MAX, "prix_malle": khan_defense.PRIX_MALLE,
                           "seuil_case": khan_defense.SEUIL_CASE, "seuil_achat": khan_defense.SEUIL_ACHAT, "lot_achat": khan_defense.LOT_ACHAT,
                           "reserve_pieces": tours.PIECES_RESERVE, "engins_pieces": sorted(w for w in khan_defense.ENGINS_DEFENSE if w in achats.ENGINS),
                           "engins_defaut": evenements.ENGINS_DEFAUT, "quantite_max": evenements.QUANTITE_MAX,
                           "place_engins": evenements.PLACE_ENGINS},
            "montee": {"niveaux_max": niveaux_max_montee(), "niveau_vise": montee.NIVEAU_VISE, "niveau_max": montee.NIVEAU_MAX_SAISIE,
                       "niveaux_max_mondes": {str(k): v for k, v in montee.NIVEAUX_MAX.items()},
                       "passes": [[c, montee.NOMS_PASSES[c]] for c in montee.PASSES]},
            "materiaux": catalogue_materiaux(),
            "recrutement": dict(catalogue_recrutement(), avant_postes=avant_postes_connus()),
            "prix_porte_max": defense.PRIX_PORTE_MAX,
            "engins": [{"id": w, "nom": achats.NOMS_ENGINS[w], "prix": prix[w],
                        "image": "images/engins/%d.png" % w if os.path.exists(os.path.join(DOSSIER_IMAGES, "engins", "%d.png" % w)) else None}
                       for w in achats.ENGINS],
            "seuil_min": achats.SEUIL_MIN, "pieces_max": achats.PIECES_MAX, "part_budget": achats.PART_BUDGET,
            "pseudo": tout.get("pseudo"), "bot": etat_bot(), "sessions": sessions.pour_interface(etat_perime=ETAT_PERIME),
            "modules": [{"nom": m.LANCEUR_NOM, "priorite": getattr(m, "LANCEUR_PRIORITE", bot.PRIORITE_DEFAUT),
                         "incompatibles": list(getattr(m, "LANCEUR_INCOMPATIBLES", ()))} for m in bot.a_choisir()],
            "royaumes": {str(k): v for k, v in tours.ROYAUMES.items()}, "soldat_tours": tours.ID_SOLDAT,
            "reserve_max_secondes": forteresses.RESERVE_MAX_SECONDES, "compte": coffre.etat(), "version": VERSION,
            "unites_tours": [{"id": u[0], "nom": NOMS_UNITES.get(u[0], u[1]), "type": u[2].replace("corps a corps", "corps à corps"),
                              "vitesse": u[3], "attaque": u[4], "niveaux": len(tours.niveaux_unite(u[0])) - 1 if u[0] in tours.NIVEAUX_UNITES else None,
                              "image": "images/unites/%d.png" % u[0] if os.path.exists(os.path.join(DOSSIER_IMAGES, "unites", "%d.png" % u[0])) else None}
                             for u in tours.UNITES_TOURS], "nb_soldats_max": tours.NB_SOLDATS_MAX}


# ============================================================================== copie du bot pour un ami
FICHIER_COPIE = "copie-pour-un-ami.zip"
CAPTURES_A_GARDER = ("LISEZ-MOI.txt", "items.json")      # donnees du jeu, rien de personnel


def fichiers_a_partager(racine=None):
    """Fichiers du bot SANS rien de personnel : ni config.json (pseudo, reglages), ni profil-chrome/ (session du jeu), ni
    captures/ (mot de passe chiffre, journaux, cartes, captures reseau), sauf les donnees du jeu (items.json)."""
    racine = racine or tours.RACINE
    res = []
    for dossier in ("src", "outils", "docs"):
        for d, sous, noms in os.walk(os.path.join(racine, dossier)):
            sous[:] = [x for x in sous if x != "__pycache__"]
            res += [os.path.join(d, n) for n in noms if not n.endswith((".pyc", ".tmp"))]
    res += [os.path.join(racine, n) for n in ("README.md", maj.FICHIER_VERSION) if os.path.exists(os.path.join(racine, n))]
    res += [os.path.join(racine, "captures", n) for n in CAPTURES_A_GARDER if os.path.exists(os.path.join(racine, "captures", n))]
    return sorted(res)


def creer_copie(racine=None):
    racine = racine or tours.RACINE
    dest = os.path.join(racine, FICHIER_COPIE)
    fichiers = fichiers_a_partager(racine)
    tmp = dest + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in fichiers:
            z.write(f, os.path.join("bot", os.path.relpath(f, racine)))
    os.replace(tmp, dest)
    return {"ok": True, "chemin": dest, "fichiers": len(fichiers), "taille_mo": round(os.path.getsize(dest) / 1e6, 1)}


def enregistrer(rubrique, data):
    with _verrou:
        tout = tours.lire_config()
        tout = tout if isinstance(tout, dict) else {}
        if rubrique == "tours":
            cfg = normaliser_tours(data, tout.get("tours"))
        elif rubrique == "forteresses":
            autres = (tout.get("tours") or {}).get("commandants") if isinstance(tout.get("tours"), dict) else []
            cfg = normaliser_forteresses(data, tout.get(forteresses.RUBRIQUE), autres if isinstance(autres, list) else [])
        elif rubrique == "lanceur":
            cfg = normaliser_lanceur(data, tout.get(bot.RUBRIQUE))
        elif rubrique == "achats":
            cfg = normaliser_achats(data, tout.get(achats.RUBRIQUE))
        elif rubrique == "defense":
            cfg = normaliser_defense(data, tout.get(defense.RUBRIQUE))
        elif rubrique == "transferts":
            cfg = normaliser_transferts(data, tout.get(transferts.RUBRIQUE))
        elif rubrique == "recrutement":
            cfg = normaliser_recrutement(data, tout.get(recrutement.RUBRIQUE))
        elif rubrique == "materiaux":
            cfg = normaliser_materiaux(data, tout.get(materiaux.RUBRIQUE))
        elif rubrique == "montee":
            cfg = normaliser_montee(data, tout.get(montee.RUBRIQUE))
        elif rubrique == "evenements":
            cfg = normaliser_evenements(data, tout.get(evenements.RUBRIQUE))
        else:
            raise ErreurSaisie(["Rubrique inconnue."])
        tours.memoriser_config(cfg, rubrique={"forteresses": forteresses.RUBRIQUE, "lanceur": bot.RUBRIQUE}.get(rubrique, rubrique))
        return {"ok": True, "config": cfg}


# ============================================================================== serveur HTTP
class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "GGEBot"

    def log_message(self, *a):
        pass

    def _hote_ok(self):
        hote = (self.headers.get("Host") or "").split(":")[0]
        return hote in ("127.0.0.1", "localhost")

    def _json(self, code, obj):
        corps = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):
        if not self._hote_ok():
            return self._json(403, {"erreurs": ["Accès refusé."]})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                corps = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            return self.wfile.write(corps)
        if u.path.startswith("/images/"):
            nom = u.path[len("/images/"):]
            chemin = os.path.normpath(os.path.join(DOSSIER_IMAGES, nom))
            if not chemin.startswith(DOSSIER_IMAGES + os.sep) or not chemin.endswith(".png") or not os.path.isfile(chemin):
                return self._json(404, {"erreurs": ["Image inconnue."]})
            with open(chemin, "rb") as f:
                corps = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "max-age=3600")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            return self.wfile.write(corps)
        if u.path == "/api/etat":
            return self._json(200, tout_etat())
        if u.path == "/api/maj":
            return self._json(200, etat_maj(forcer=parse_qs(u.query).get("forcer") == ["1"]))
        if u.path == "/api/journal":
            q = parse_qs(u.query)
            source = (q.get("source") or ["bot"])[0]
            if source not in JOURNAUX:
                return self._json(400, {"erreurs": ["Journal inconnu."]})
            try:
                pos = int((q.get("pos") or ["-1"])[0])
            except ValueError:
                pos = -1
            pos, lignes = lire_journal(source, pos)
            return self._json(200, {"pos": pos, "lignes": lignes, "bot": etat_bot()})
        self._json(404, {"erreurs": ["Page inconnue."]})

    def do_POST(self):
        if not self._hote_ok() or self.headers.get("X-GGE-Bot") != "1":
            return self._json(403, {"erreurs": ["Accès refusé."]})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(n, 1000000)) or b"{}") if n else {}
            if not isinstance(data, dict):
                raise ErreurSaisie(["Données invalides."])
            chemin = urlparse(self.path).path
            if chemin.startswith("/api/config/"):
                return self._json(200, enregistrer(chemin.rsplit("/", 1)[1], data))
            if chemin == "/api/demarrer":
                tout = tours.lire_config()
                lanceur = normaliser_lanceur(data, (tout or {}).get(bot.RUBRIQUE) if isinstance(tout, dict) else None)
                enregistrer("lanceur", data)
                return self._json(200, demarrer_bot(lanceur))
            if chemin == "/api/arreter":
                return self._json(200, arreter_bot())
            if chemin == "/api/compte":
                mdp = data.get("mdp")
                if mdp is not None and not isinstance(mdp, str):
                    raise ErreurSaisie(["Mot de passe invalide."])
                try:
                    e = coffre.enregistrer(data.get("pseudo"), mdp or None)
                except coffre.ErreurCoffre as ex:
                    raise ErreurSaisie([str(ex)])
                import connexion_bot
                connexion_bot.memoriser_pseudo(e["pseudo"])
                return self._json(200, {"ok": True, "compte": e})
            if chemin == "/api/compte/effacer":
                return self._json(200, {"ok": True, "compte": coffre.effacer_mdp()})
            if chemin == "/api/sessions/supprimer":
                if not isinstance(data.get("id"), str) or not sessions.supprimer(data["id"]):
                    raise ErreurSaisie(["Session introuvable."])
                return self._json(200, {"ok": True, "sessions": sessions.pour_interface(etat_perime=ETAT_PERIME)})
            if chemin == "/api/copie-ami":
                return self._json(200, creer_copie())
            if chemin == "/api/maj/installer":
                return self._json(200, installer_maj())
            self._json(404, {"erreurs": ["Action inconnue."]})
        except ErreurSaisie as e:
            self._json(400, {"erreurs": e.erreurs})
        except (ValueError, OSError) as e:
            self._json(400, {"erreurs": [str(e)]})


def lancer_serveur(port=PORT, ouvrir=True):
    serveur = ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)
    url = "http://127.0.0.1:%d/" % port
    print("INTERFACE DU BOT : %s  (Ctrl+C dans cette fenetre = fermer l'interface ; le bot, lui, continue)" % url)
    if ouvrir:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("Interface fermee.")
    finally:
        serveur.server_close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Interface graphique locale du bot.")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--sans-navigateur", action="store_true", help="ne pas ouvrir le navigateur")
    a = p.parse_args()
    lancer_serveur(a.port, not a.sans_navigateur)
