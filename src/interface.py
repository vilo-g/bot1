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
import defense      # noqa: E402
import transferts   # noqa: E402

ICI = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(ICI, "interface.html")
PORT = 8770
DOSSIER_IMAGES = os.path.join(ICI, "images")
NOMS_UNITES = {215: "Vierge à bouclier niv. 10", 6: "Frondeur vétéran", 35: "Lanceur de kunai renégat",
               10: "Horreur mortelle vétéran", 9: "Horreur démoniaque vétéran"}     # noms avec accents pour la page
VERSION = "2026-11-08-transferts"   # doit etre egale a VERSION dans interface.html (la page previent si l'interface n'a pas ete relancee)
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
        if src.get("cotes") not in combat.COTES:
            erreurs.append("%s : côtés F, FF, A, AF ou AFF." % nom)
        else:
            b["cotes"] = src["cotes"]
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


def normaliser_lanceur(data, ancienne=None):
    erreurs = []
    noms = [m.LANCEUR_NOM for m in bot.MODULES]
    mods = data.get("modules")
    if not isinstance(mods, list) or not mods or not all(m in noms for m in mods):
        erreurs.append("Modules : coche au moins un module.")
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
    cfg = {k: v for k, v in (ancienne if isinstance(ancienne, dict) else {}).items() if k not in ("min_envoi", "accelerer")}
    m = _entier(data.get("min_envoi", transferts.MIN_ENVOI), "Quantité minimum par envoi", 1, 100000000, erreurs)
    if m is not None:
        cfg["min_envoi"] = m
    cfg["accelerer"] = bool(data.get("accelerer"))
    cfg["commandants"] = []
    if erreurs:
        raise ErreurSaisie(erreurs)
    return cfg


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
                           "transferts": (transferts.RUBRIQUE, transferts.config_valide)}[nom]
            if not valide((tout or {}).get(rub)):
                raise ErreurSaisie(["Module %s : réglages incomplets, enregistre-les d'abord." % m.LANCEUR_NOM])
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
                       "transferts": tout.get(transferts.RUBRIQUE)},
            "prix_porte_max": defense.PRIX_PORTE_MAX,
            "engins": [{"id": w, "nom": achats.NOMS_ENGINS[w], "prix": prix[w],
                        "image": "images/engins/%d.png" % w if os.path.exists(os.path.join(DOSSIER_IMAGES, "engins", "%d.png" % w)) else None}
                       for w in achats.ENGINS],
            "seuil_min": achats.SEUIL_MIN, "pieces_max": achats.PIECES_MAX, "part_budget": achats.PART_BUDGET,
            "pseudo": tout.get("pseudo"), "bot": etat_bot(),
            "modules": [{"nom": m.LANCEUR_NOM, "priorite": getattr(m, "LANCEUR_PRIORITE", bot.PRIORITE_DEFAUT)} for m in bot.MODULES],
            "royaumes": {str(k): v for k, v in tours.ROYAUMES.items()}, "soldat_tours": tours.ID_SOLDAT,
            "reserve_max_secondes": forteresses.RESERVE_MAX_SECONDES, "compte": coffre.etat(), "version": VERSION,
            "unites_tours": [{"id": u[0], "nom": NOMS_UNITES.get(u[0], u[1]), "type": u[2].replace("corps a corps", "corps à corps"),
                              "vitesse": u[3], "attaque": u[4],
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
