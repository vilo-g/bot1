#!/usr/bin/env python3
"""
forteresses.py : module d'attaque des FORTERESSES (royaumes 1 sables, 2 glacier, 3 feu).

Lancement : outils\\lancer_bot_forteresses.bat   (ou : py src\\forteresses.py)

Au PREMIER lancement, un assistant pose les questions de configuration (config.json, rubrique "forteresses") :
  [A] commandants PROPRES a ce module (ex. 1;3;4;6)   [B] royaumes (1;2;3)
  puis, POUR CHAQUE royaume : mode P (preselections) ou R (remplissage automatique), preselection de chaque vague ou cotes
  F / FF / A / AF / AFF, cheval C / F / CF, DISTANCE MAXIMALE (cases, du chateau a la forteresse).

Remplissage automatique : seulement le soldat le PLUS RAPIDE du chateau (champ speed des unites), pas d'echelles.
Les forteresses viennent DIRECTEMENT de la carte du jeu (demande gaa, comme pour les tours) : chaque forteresse y est donnee
avec son minuteur personnel (secondes avant qu'elle soit attaquable ; 0 = attaquable maintenant). Les positions des forteresses
sont fixes : la 1re lecture parcourt la carte jusqu'a la distance maximale (zones du CERCLE seulement, les plus proches du
chateau d'abord, par petits morceaux entre deux attaques : les attaques commencent pendant la lecture ; une seule fois, memorisee
dans captures/carte_forteresses.json), ensuite seules les zones qui contiennent une forteresse sont relues (toutes les 5 minutes).
Avant chaque envoi, le bot demande `abi` au jeu (stock, niveau, minuteur) et laisse de cote une forteresse que le jeu dit pas
encore attaquable. La plus proche d'abord, tous royaumes confondus.
Reservation de commandants : si un commandant est aussi dans la liste du module tours, l'assistant propose de le garder pour les
forteresses pendant N secondes avant qu'elles soient attaquables (un par forteresse, au plus M a la fois). Elle ne sert que
lorsque les deux modules tournent dans la meme session : lanceur commun, outils\\lancer_bot.bat (src/bot.py).
Options : --simulation, --max-attaques N, --duree 3h, --reconfigurer, --apprendre-cheval C|F, --avec-bonus, --oui.
"""
import argparse
import asyncio
import copy
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combat  # noqa: E402
import tours   # noqa: E402
from tours import (ArretModule, CibleEnAttente, PlusDeSoldats, ROYAUMES, demander, analyser_commandants,  # noqa: E402
                   analyser_mode, analyser_cotes, analyser_cheval, analyser_entier_positif, analyser_numero,
                   analyser_oui_non, duree)

T = tours.T
RUBRIQUE = "forteresses"
FICHIER_JOURNAL = os.path.join(tours.DOSSIER_CAPTURES, "journal_forteresses.txt")
FICHIER_CARTE = os.path.join(tours.DOSSIER_CAPTURES, "carte_forteresses.json")
ROYAUMES_FORTERESSES = (1, 2, 3)      # le royaume vert (0) n'a pas de forteresse
REFRAICHIR = 300                      # secondes entre deux relectures des minuteurs sur la carte
ZONES_PAR_PASSAGE = 3                 # relecture des minuteurs par petits morceaux (environ 2 s) : les autres modules ne sont pas bloques
ZONES_LECTURE_PAR_PASSAGE = 4         # 1re lecture de la carte par morceaux de 4 zones (environ 3 s) entre deux attaques
LECTURE_PAR_MORCEAUX = True           # False : la 1re lecture est faite en entier au demarrage, avant toute attaque (ancien mode)
PAUSE_LECTURE = 0.3                   # pause entre deux morceaux : les autres modules (tours) passent entre deux
ESSAIS_PAR_COMMANDANT = 8             # forteresses essayees au plus pour un commandant et un passage
CARTE_DEFAUT = object()               # "utiliser FICHIER_CARTE" (None = ne rien memoriser, pour les tests)
ESSAIS_ZONE = 3                       # une zone sans reponse est retentee a 3 lectures de suite, puis a 3 relectures, puis abandonnee
RESERVE_MAX_SECONDES = 3600           # reservation des commandants : au plus 1 h avant qu'une forteresse soit attaquable


# ============================================================================== assistant de configuration
def analyser_royaumes_forteresses(texte):
    res = tours.analyser_royaumes(texte)
    if 0 in res:
        raise ValueError("Le royaume 0 (vert) n'a pas de forteresse : choisis 1 (sables), 2 (glacier) ou 3 (feu).")
    return res


def analyser_secondes_reserve(texte):
    """Secondes avant qu'une forteresse soit attaquable : entier de 1 a 3600 (chiffres seulement)."""
    t = texte.strip()
    if not (t.isascii() and t.isdigit()) or not 1 <= int(t) <= RESERVE_MAX_SECONDES:
        raise ValueError("Ecris un nombre de secondes entier entre 1 et %d (exemple : 900 = 15 minutes)." % RESERVE_MAX_SECONDES)
    return int(t)


def analyseur_reserve_max(n):
    """Nombre de commandants gardes en meme temps : entier de 1 a n (n = commandants communs aux deux modules)."""
    def analyser(texte):
        t = texte.strip()
        if not (t.isascii() and t.isdigit()) or not 1 <= int(t) <= n:
            raise ValueError("Ecris un nombre entier entre 1 et %d." % n)
        return int(t)
    return analyser


def autres_commandants(chemin=None):
    """Commandants choisis dans la configuration du module tours (liste vide s'il n'y en a pas)."""
    tout = tours.lire_config(chemin)
    rubrique = tout.get("tours") if isinstance(tout, dict) else None
    c = rubrique.get("commandants") if isinstance(rubrique, dict) else None
    return [p for p in c if isinstance(p, int) and not isinstance(p, bool) and p >= 1] if isinstance(c, list) else []


def commandants_communs(miens, autres):
    """Commandants de ce module qui sont aussi dans la liste de l'autre module (ordre de ce module)."""
    return [p for p in miens if p in set(autres or [])]


def lignes_reserve(cfg, communs, active=False):
    """Etat de la reservation au demarrage (rien a dire s'il n'y a aucun commandant commun avec un autre module).
    active : True dans le lanceur commun (les autres modules tournent dans la meme session)."""
    if not communs:
        return []
    r = cfg.get("reserve") or {}
    liste = ";".join(str(p) for p in communs)
    l = ["", "RESERVATION DES COMMANDANTS :", "  - commandants communs avec %s : %s" % ("les autres modules lances" if active else "le module tours", liste)]
    if r.get("secondes", 0) > 0:
        l.append("  - reservation : gardes %d s avant qu'une forteresse soit attaquable, %d au maximum" % (r["secondes"], r["max"]))
        if active:
            l.append("  - ACTIVE : les autres modules ne prendront pas un commandant garde.")
        else:
            l.append("  - INACTIVE dans ce lancement : le module tours ne tourne pas ici. Pour les deux ensemble : outils\\lancer_bot.bat")
    else:
        l.append("  - reservation desactivee (--reconfigurer pour l'activer)")
    return l


def reserve_valide(r):
    """Reglage "reserve" : absent (= pas de reservation) ou {"secondes": 0..3600, "max": >= 1}."""
    if r is None:
        return True
    try:
        s, m = r["secondes"], r["max"]
    except (KeyError, TypeError):
        return False
    return all(isinstance(v, int) and not isinstance(v, bool) for v in (s, m)) and 0 <= s <= RESERVE_MAX_SECONDES and m >= 1


def estimation_premiere_lecture(distance_max, x=None, y=None):
    """(nombre de zones de 13x13 cases, secondes) pour lire toute la carte jusqu'a distance_max cases du chateau.
    Avec la position du chateau : zones du cercle seulement (celles que le bot lit vraiment)."""
    if x is None or y is None:
        n = math.ceil((2 * distance_max + 1) / 13) ** 2
    else:
        n = len(zones_cercle(x, y, distance_max))
    return n, n * (T.PAUSE_ENTRE_GAA + 0.25)


def questions_royaume(lire, sortie, kid, ancien=None):
    """Reglages d'UN royaume : mode, presets ou cotes, cheval, distance maximale -> dict."""
    ancien = ancien or {}
    b = {"echelles": False, "soldat": "rapide"}          # pas d'echelles ; soldat le plus rapide (choisi a chaque attaque)
    nom = "%d (%s)" % (kid, ROYAUMES[kid])
    sortie("")
    sortie("-" * 66)
    sortie(" REGLAGES DU ROYAUME %s" % nom.upper())
    sortie("-" * 66)
    sortie("")
    sortie("[1] MODE D'ATTAQUE - royaume %s" % nom)
    sortie("    P = preselections (celles enregistrees dans le jeu)")
    sortie("    R = remplissage automatique (soldat le plus rapide seulement, pas d'echelles)")
    b["mode"] = demander(lire, sortie, "    > ", analyser_mode)
    sortie("")
    if b["mode"] == "presets":
        sortie("[2] PRESELECTION DE CHAQUE VAGUE - royaume %s" % nom)
        sortie("    Quel numero de preselection pour chaque vague ?")
        sortie("    (si un commandant a plus de %d vagues, la preselection de la vague %d sert aussi pour les suivantes)"
               % (tours.NB_PRESELECTIONS_DEMANDEES, tours.NB_PRESELECTIONS_DEMANDEES))
        sortie("    Une vague dont la preselection n'a aucun soldat disponible est remplie avec le soldat le plus rapide,")
        sortie("    sur les memes cotes que la preselection (sans echelles).")
        b["presets"] = [demander(lire, sortie, "    Vague %d : preselection numero " % (i + 1), tours.analyser_preselection)
                        for i in range(tours.NB_PRESELECTIONS_DEMANDEES)]
    else:
        sortie("[2] COTES A REMPLIR - royaume %s" % nom)
        sortie("    F   = un flanc (toujours le GAUCHE)")
        sortie("    FF  = les deux flancs (gauche + droit)")
        sortie("    A   = l'avant (front) seulement")
        sortie("    AF  = l'avant + un flanc (gauche)")
        sortie("    AFF = l'avant + les deux flancs")
        b["cotes"] = demander(lire, sortie, "    > ", analyser_cotes)
    sortie("")
    sortie("[3] CHEVAL - royaume %s" % nom)
    sortie("    C  = pieces")
    sortie("    F  = plumes")
    sortie("    CF = pieces jusqu'a une certaine distance de la forteresse, puis plumes")
    b["cheval"] = demander(lire, sortie, "    > ", analyser_cheval)
    if b["cheval"] == "CF":
        sortie("    A partir de quelle distance (en cases, du chateau a la forteresse) utiliser les plumes ?  Exemple : 100")
        b["distance_plumes"] = demander(lire, sortie, "    Distance > ", analyser_entier_positif)
    sortie("")
    sortie("[4] DISTANCE MAXIMALE - royaume %s" % nom)
    sortie("    Distance maximale (en cases, du chateau a la forteresse) : les forteresses plus loin sont ignorees.")
    sortie("    (il y a environ une forteresse tous les 20 a 40 cases ; plus la distance est grande, plus la 1re lecture de la")
    sortie("    carte est longue : environ 1 min pour 60 cases, 4 min pour 120 cases, 10 min pour 200 cases ; une seule fois)")
    anc = ancien.get("distance_max")
    b["distance_max"] = demander(lire, sortie, "    Distance maximale%s > " % (" (Entree = %s)" % anc if anc else ""),
                                 analyser_entier_positif, anc)
    if b["cheval"] == "CF" and b["distance_plumes"] > b["distance_max"]:
        sortie("    ATTENTION : les plumes commencent a %s cases mais le module n'attaque pas au-dela de %s : les plumes ne"
               % (b["distance_plumes"], b["distance_max"]))
        sortie("    serviront jamais.")
    sortie("")
    sortie("[5] NOMBRE DE VAGUES A REMPLIR - royaume %s" % nom)
    sortie("    1, 2 ou 3 = seulement ces vagues (les suivantes partent vides) ; 4 = toutes (avec les vagues en plus du commandant).")
    sortie("    Chaque vague remplie recoit le MAXIMUM de soldats permis par la forteresse : le soldat le plus rapide d'abord,")
    sortie("    complete par le suivant le plus rapide s'il n'y en a pas assez (l'armee va alors a la vitesse du plus lent).")
    anc = ancien.get("vagues_remplies")
    anc = anc if anc in (1, 2, 3, 4) and not isinstance(anc, bool) else None
    b["vagues_remplies"] = demander(lire, sortie, "    Vagues%s > " % (" (Entree = %s)" % anc if anc else ""), analyser_vagues, anc)
    return b


def analyser_vagues(texte):
    t = texte.strip()
    if t not in ("1", "2", "3", "4"):
        raise ValueError("Reponds 1, 2, 3 ou 4.")
    return int(t)


def question_reserve(lire, sortie, communs, ancienne):
    """Reservation des commandants communs aux deux modules -> {"secondes": n, "max": m} (secondes = 0 : pas de reservation)."""
    anc = ancienne.get("reserve") if reserve_valide(ancienne.get("reserve")) else None
    sortie("")
    sortie("[A2] RESERVATION DES COMMANDANTS COMMUNS AUX DEUX MODULES")
    sortie("    Le(s) commandant(s) %s sont aussi dans la liste du module tours." % ";".join(str(p) for p in communs))
    sortie("    Sans reservation, le module tours peut les envoyer juste avant qu'une forteresse devienne attaquable :")
    sortie("    ils seraient alors absents au moment d'attaquer.")
    sortie("    Avec la reservation, quand une forteresse devient attaquable dans moins de N secondes, le module tours ne")
    sortie("    prend plus ces commandants (un par forteresse attaquable, au plus M a la fois). Un commandant qui revient")
    sortie("    est garde tout de suite. Les autres commandants du module tours ne sont pas touches.")
    sortie("    Activer la reservation ? (O/N)")
    actif = demander(lire, sortie, "    > ", analyser_oui_non, None if anc is None else anc["secondes"] > 0)
    if not actif:
        return {"secondes": 0, "max": 1}
    sortie("    Combien de secondes AVANT qu'une forteresse soit attaquable le module tours ne doit-il plus utiliser ces")
    sortie("    commandants ? (1 a %d ; exemples : 900 = 15 min, 1800 = 30 min)" % RESERVE_MAX_SECONDES)
    ds = anc["secondes"] if anc and anc["secondes"] > 0 else None
    secondes = demander(lire, sortie, "    Secondes%s > " % (" (Entree = %s)" % ds if ds else ""), analyser_secondes_reserve, ds)
    if len(communs) == 1:
        sortie("    Un seul commandant commun : 1 commandant garde au maximum.")
        return {"secondes": secondes, "max": 1}
    sortie("    Combien de commandants garder au maximum en meme temps ? (1 a %d)" % len(communs))
    dm = anc["max"] if anc and 1 <= anc["max"] <= len(communs) else None
    maximum = demander(lire, sortie, "    Maximum%s > " % (" (Entree = %s)" % dm if dm else ""), analyseur_reserve_max(len(communs)), dm)
    return {"secondes": secondes, "max": maximum}


def assistant_config(lire=None, sortie=print, ancienne=None, autres=None):
    """Pose les questions et renvoie la configuration (dict). `ancienne` : valeurs deja enregistrees (Entree les conserve).
    `autres` : commandants du module tours (lus dans config.json si absents) pour la question de reservation."""
    lire = lire or input
    ancienne = ancienne or {}
    cfg = {}
    sortie("")
    sortie("=" * 66)
    sortie(" MODULE FORTERESSES - CONFIGURATION")
    sortie(" (une seule fois ; tu pourras la refaire avec --reconfigurer)")
    sortie("=" * 66)
    sortie("")
    sortie("[A] COMMANDANTS (propres a ce module, communs a tous ses royaumes)")
    sortie("    Avec quels commandants attaquer les forteresses ? Numeros dans la liste des commandants du jeu")
    sortie("    (le 1er = 1), separes par des ';'.   Exemple : 1;3;4;6")
    sortie("    Si un commandant est aussi dans la liste du module tours, on te proposera de le reserver avant les forteresses.")
    cfg["commandants"] = demander(lire, sortie, "    > ", analyser_commandants, None)
    communs = commandants_communs(cfg["commandants"], autres_commandants() if autres is None else autres)
    cfg["reserve"] = question_reserve(lire, sortie, communs, ancienne) if communs else {"secondes": 0, "max": 1}
    sortie("")
    sortie("[B] ROYAUMES A ATTAQUER")
    sortie("    1 = sables, 2 = glacier, 3 = feu   (le vert n'a pas de forteresse)")
    sortie("    Ecris les numeros separes par des ';'.   Exemples :  3   ou   1;3   ou   1;2;3")
    sortie("    Tes chateaux / avant-postes sont lus tout seuls sur ton compte. Le bot attaque toujours la forteresse")
    sortie("    attaquable la PLUS PROCHE, tous royaumes confondus, avec le premier commandant libre.")
    sortie("    Les reglages (mode, soldats, cheval, distance maximale) te seront demandes royaume par royaume.")
    anc = [k for k in (ancienne.get("royaumes") or []) if k in ROYAUMES_FORTERESSES] or None
    cfg["royaumes"] = demander(lire, sortie, "    Royaumes%s > " % (" (Entree = %s)" % ";".join(str(k) for k in anc) if anc else ""),
                               analyser_royaumes_forteresses, anc)
    cfg["reglages"] = {}
    premier = None
    for kid in cfg["royaumes"]:
        anciens = (ancienne.get("reglages") or {}).get(str(kid)) or {}
        if premier is not None:
            sortie("")
            sortie("Royaume %d (%s) : utiliser les MEMES reglages que le royaume %d (%s) ? (O/N)"
                   % (kid, ROYAUMES[kid], premier, ROYAUMES[premier]))
            if demander(lire, sortie, "    > ", analyser_oui_non):
                cfg["reglages"][str(kid)] = copy.deepcopy(cfg["reglages"][str(premier)])
                continue
        cfg["reglages"][str(kid)] = questions_royaume(lire, sortie, kid, anciens)
        premier = kid if premier is None else premier
    cfg["chateaux"] = dict(ancienne.get("chateaux") or {})
    if ancienne.get("chateaux_manuels"):
        cfg["chateaux_manuels"] = dict(ancienne["chateaux_manuels"])
    cfg["vagues_base"] = ancienne.get("vagues_base", tours.NB_VAGUES_BASE)
    cfg["avec_bonus"] = ancienne.get("avec_bonus", False)
    cfg["niveau_min"] = 0
    return cfg


def lignes_reglages(b):
    l = []
    if b["mode"] == "presets":
        p = b["presets"]
        l.append("mode : PRESELECTIONS  (vague 1 : %d, vague 2 : %d, vague 3 : %d, vague 4 et suivantes : %d)" % tuple(p[:4]))
        l.append("si une preselection n'a aucun soldat disponible : vague remplie avec le soldat le plus rapide, memes cotes")
    else:
        l.append("mode : REMPLISSAGE AUTOMATIQUE, cotes %s (%s), soldat le plus rapide, sans echelles"
                 % (b["cotes"], " + ".join(combat.NOMS_COTES[c] for c in combat.COTES[b["cotes"]])))
    if b["cheval"] == "CF":
        l.append("cheval : pieces puis plumes a partir de %s cases" % b["distance_plumes"])
    else:
        l.append("cheval : %s" % ("pieces" if b["cheval"] == "C" else "plumes"))
    l.append("distance maximale : %s cases du chateau" % b["distance_max"])
    vr = b.get("vagues_remplies", 4)
    l.append("vagues remplies : %s (maximum de soldats, le plus rapide d'abord puis les suivants)"
             % ("toutes" if vr >= 4 else "%d, les autres vides" % vr))
    return l


def resume_config(cfg):
    l = ["commandants (propres au module forteresses) : " + ";".join(str(c) for c in cfg["commandants"])]
    l.append("royaumes : %s (forteresse la plus proche attaquee en premier)" % ", ".join("%d (%s)" % (k, ROYAUMES[k]) for k in cfg["royaumes"]))
    for kid in cfg["royaumes"]:
        l.append("ROYAUME %d (%s) :" % (kid, ROYAUMES[kid]))
        l += ["    " + x for x in lignes_reglages(cfg["reglages"][str(kid)])]
    l.append("forteresses lues directement sur la carte du jeu (minuteur personnel de chaque forteresse)")
    r = cfg.get("reserve") or {}
    communs = commandants_communs(cfg["commandants"], autres_commandants())
    if r.get("secondes", 0) > 0:
        l.append("reservation des commandants communs avec le module tours (%s) : gardes %d s avant qu'une forteresse soit "
                 "attaquable, %d au maximum" % (";".join(str(p) for p in communs) or "aucun pour l'instant", r["secondes"], r["max"]))
    elif communs:
        l.append("reservation des commandants communs avec le module tours (%s) : NON" % ";".join(str(p) for p in communs))
    return l


def reglages_valides(b):
    try:
        if b["mode"] not in ("auto", "presets") or b["distance_max"] <= 0:
            return False
        if "vagues_remplies" in b and (b["vagues_remplies"] not in (1, 2, 3, 4) or isinstance(b["vagues_remplies"], bool)):
            return False
        if b["mode"] == "auto" and b["cotes"] not in combat.COTES:
            return False
        if b["mode"] == "presets" and not tours.preselections_valides(b["presets"]):
            return False
        return b["cheval"] in ("C", "F", "CF") and not (b["cheval"] == "CF" and not b.get("distance_plumes"))
    except (KeyError, TypeError):
        return False


def config_valide(cfg):
    try:
        if not tours.commandants_valides(cfg["commandants"]) or not cfg["royaumes"] or len(set(cfg["royaumes"])) != len(cfg["royaumes"]):
            return False
        if not reserve_valide(cfg.get("reserve")):
            return False
        if not all(k in ROYAUMES_FORTERESSES for k in cfg["royaumes"]):
            return False
        return all(reglages_valides(cfg["reglages"].get(str(k))) for k in cfg["royaumes"])
    except (KeyError, TypeError, AttributeError):
        return False


# ============================================================================== la carte
def forteresse_libre(e):
    """Entree de la carte [11, x, y, -1, niveau, minuteur, ...] -> (x, y, niveau, minuteur en secondes) si c'est une forteresse
    sans proprietaire, sinon None. Un minuteur illisible compte pour 0 : c'est `abi` qui tranchera avant l'envoi."""
    if not (isinstance(e, list) and len(e) >= 6 and e[0] == 11 and e[3] == -1):
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in e[1:3]):
        return None
    niveau = e[4] if isinstance(e[4], int) and not isinstance(e[4], bool) else 0
    minuteur = e[5] if isinstance(e[5], (int, float)) and not isinstance(e[5], bool) and e[5] > 0 else 0
    return e[1], e[2], niveau, minuteur


def dans_carre(carre, x, y):
    return carre[0] <= x <= carre[2] and carre[1] <= y <= carre[3]


def distance_zone(carre, x, y):
    """Distance (cases) entre le chateau (x, y) et la case de la zone la plus proche de lui (0 si le chateau est dedans)."""
    return math.hypot(max(carre[0] - x, 0, x - carre[2]), max(carre[1] - y, 0, y - carre[3]))


def zones_cercle(x, y, distance_max):
    """Zones de 13x13 cases qui touchent le CERCLE de rayon distance_max autour du chateau (les coins du carre ne peuvent
    contenir aucune forteresse a portee : environ 20 % de zones en moins), la plus proche du chateau d'abord."""
    zs = [c for c in T.carres(x, y, distance_max) if distance_zone(c, x, y) <= distance_max]
    zs.sort(key=lambda c: (distance_zone(c, x, y), math.hypot((c[0] + c[2]) / 2 - x, (c[1] + c[3]) / 2 - y)))
    return zs


# ============================================================================== le module
class ModuleForteresses(tours.ModuleTours):
    """Meme boucle que le module tours (commandants, retours `cat`, cheval, presets), mais : cibles lues sur la carte (type 11,
    minuteur personnel), demande `abi`, soldat le plus rapide, pas d'echelles, forteresse reattaquable 3 h apres l'arrivee."""
    CMD_INFO = "abi"
    NOM_CIBLE = "forteresse"
    NOM_MODULE = "FORTERESSES"
    ATTENTE_APRES_ATTAQUE = 10800 + 30    # 3 h de delai personnel apres l'arrivee (+ 30 s de marge), comme l'ancien bot

    def __init__(self, jeu, cfg, sources, effets=None, appris=None, unites=None, journal=tours.JOURNAL_DEFAUT,
                 fichier_carte=CARTE_DEFAUT, communs=None, **kw):
        super().__init__(jeu, cfg, sources, effets, appris,
                         journal=FICHIER_JOURNAL if journal is tours.JOURNAL_DEFAUT else journal, **kw)
        self.unites = unites if unites is not None else {}
        self.communs = list(communs or [])      # commandants de ce module qui sont aussi dans la liste d'un autre module
        self._gardes_vus = ()                   # derniers commandants gardes (pour n'afficher que les changements)
        self.fichier_carte = FICHIER_CARTE if fichier_carte is CARTE_DEFAUT else fichier_carte
        self.carte = {}              # royaume -> {(x, y): niveau} : positions connues (elles ne bougent pas)
        self.lu_jusqua = {}          # royaume -> distance (cases) jusqu'a laquelle la carte a ete lue en entier
        self.manquantes = {}         # royaume -> {zone: relectures deja ratees} : zones que le jeu n'a pas voulu donner
        self.abandonnees = {}        # royaume -> {zone} : zones qui ne repondent jamais (plus relues pendant cette session)
        self.raison = ""             # pourquoi la derniere zone n'a pas ete lue
        self.fort = {}               # (royaume, x, y) -> {"niveau": n, "dispo": instant ou elle est attaquable}
        self.releve_t = None         # instant de la derniere lecture des minuteurs
        self._a_relire = []          # [(royaume, zone)] : relecture des minuteurs en cours, quelques zones par passage
        self._lecture = {}           # royaume -> 1re lecture de la carte EN COURS (par morceaux, entre deux attaques)

    # ---------------------------------------------------------------- construction (soldat le plus rapide, zone forteresse)
    def _construire(self, adi, pos, tour, kid, chateau):
        ai = (adi.get("gaa") or {}).get("AI")
        if isinstance(ai, list) and len(ai) >= 6:
            if ai[0] != 11:
                raise CibleEnAttente(1800, "ce n'est pas une forteresse (type %s)" % ai[0])
            if isinstance(ai[5], (int, float)) and ai[5] > 0:
                raise CibleEnAttente(int(ai[5]), "le jeu dit qu'elle n'est pas encore attaquable (encore %s)" % duree(ai[5]))
        stock = combat.inventaire_depuis_adi(adi.get("gui", {}).get("I", []))
        ordre = combat.soldats_par_vitesse(stock, self.unites, kid)
        if not ordre:
            raise PlusDeSoldats("Plus aucun soldat offensif autorise dans le royaume %s." % kid)
        bloc = tours.cfg_royaume(self.cfg, kid)
        # le plus rapide d'abord ; s'il n'y en a pas assez, complete par le suivant le plus rapide (le maximum permis)
        bloc.update(soldat=ordre[0], soldats_ordre=ordre, echelles=False)
        return tours.construire_attaque(bloc, adi, pos, ai if isinstance(ai, list) and len(ai) >= 5 else tour, kid, chateau,
                                        self.effets, self.appris, cheval_inconnu_ok=self.simulation, presets=self.presets,
                                        fiches_outils=self.fiches_outils, zone=combat.ZONE_FORTERESSE, unites=self.unites)

    def _verifier_stock(self):
        if not self._royaumes_actifs():
            raise ArretModule("Plus aucun soldat offensif utilisable dans le chateau de depart d'aucun des royaumes choisis : "
                              "arret du module.")

    # ---------------------------------------------------------------- carte memorisee (positions seulement)
    def _charger_carte(self):
        if not self.fichier_carte:
            return
        try:
            with open(self.fichier_carte, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        for kid in self.cfg["royaumes"]:
            try:
                b, src = d[str(kid)], self.sources[kid]
                if b["chateau"] == [src["x"], src["y"]] and b["rayon"] >= self._reglages(kid)["distance_max"]:
                    self.carte[kid] = {(int(x), int(y)): int(n) for x, y, n in b["forteresses"]}
                    self.lu_jusqua[kid] = b["rayon"]
                    self.manquantes[kid] = {tuple(int(v) for v in z): 0 for z in b.get("manquantes", []) if len(z) == 4}
            except (KeyError, TypeError, ValueError, AttributeError):
                continue

    def _sauver_carte(self):
        if not self.fichier_carte:
            return
        try:
            with open(self.fichier_carte, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except (OSError, ValueError):
            d = {}
        for kid in self.lu_jusqua:
            src = self.sources[kid]
            d[str(kid)] = {"chateau": [src["x"], src["y"]], "rayon": self.lu_jusqua[kid],
                           "forteresses": [[x, y, n] for (x, y), n in sorted(self.carte.get(kid, {}).items())],
                           "manquantes": [list(z) for z in sorted(self.manquantes.get(kid, {}))]}
        try:
            os.makedirs(os.path.dirname(self.fichier_carte), exist_ok=True)
            with open(self.fichier_carte, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except OSError:
            pass

    # ---------------------------------------------------------------- lecture de la carte (gaa)
    async def _lire_carre(self, kid, carre, delai=10):
        """Une zone de 13x13 cases -> forteresses libres [(x, y, niveau, minuteur)] ; None si le jeu n'a pas repondu
        (la raison est dans self.raison)."""
        await self._vider()
        await self.jeu.envoyer("gaa", {"KID": kid, "AX1": carre[0], "AY1": carre[1], "AX2": carre[2], "AY2": carre[3]})
        rep = await self.jeu.attendre("gaa", delai)
        await self.dormir(T.PAUSE_ENTRE_GAA)
        if not rep:
            self.raison = "pas de reponse en %d s" % delai
            return None
        d = T.json_ou_none(rep[2])
        if rep[1] != 0 or not isinstance(d, dict):
            self.raison = "le jeu repond par une erreur (code %s) : %s" % (rep[1], str(rep[2])[:70])
            return None
        if d.get("KID", kid) != kid or not isinstance(d.get("AI", []), list):
            self.raison = "reponse pour un autre royaume ou sans liste d'objets (KID %s)" % d.get("KID")
            return None
        return [f for f in (forteresse_libre(e) for e in d.get("AI", [])) if f]

    async def _lire_royaume(self, kid, complet):
        """Lit la carte du royaume : en entier (toutes les zones jusqu'a la distance maximale) ou seulement les zones qui
        contiennent une forteresse connue (+ les zones deja ratees). Une zone sans reponse est retentee 2 fois de suite.
        Renvoie {(x, y): (niveau, minuteur, instant de lecture)}, [zones toujours sans reponse], nombre de zones lues, {zone: raison}."""
        src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
        carres = zones_cercle(src["x"], src["y"], dmax)
        if not complet:
            connues, rates, laissees = self.carte.get(kid, {}), self.manquantes.get(kid, {}), self.abandonnees.get(kid, ())
            carres = [c for c in carres if c not in laissees and (c in rates or any(dans_carre(c, x, y) for (x, y) in connues))]
        vues, echecs, raisons, prochain = {}, [], {}, 0.25

        def noter(fs):
            t = self.horloge()
            for (x, y, niv, minu) in fs:
                vues[(x, y)] = (niv, minu, t)

        for i, c in enumerate(carres):
            fs = await self._lire_carre(kid, c)
            if fs is None:
                echecs.append(c)
                raisons[c] = self.raison
            else:
                noter(fs)
            if complet and len(carres) >= 40 and (i + 1) / len(carres) >= prochain and (i + 1) < len(carres):
                self._noter("   ... %d %% de la carte du royaume %d (%s) lue, %d forteresse(s) trouvee(s)."
                            % (round(100 * (i + 1) / len(carres)), kid, ROYAUMES[kid], len(vues)))
                prochain += 0.25
        for _ in range(2):                                  # zones sans reponse : on les retente tout de suite, 2 fois
            if not echecs:
                break
            await self.dormir(1)
            encore = []
            for c in echecs:
                fs = await self._lire_carre(kid, c, delai=6)
                if fs is None:
                    encore.append(c)
                    raisons[c] = self.raison
                else:
                    noter(fs)
            echecs = encore
        return vues, echecs, len(carres), raisons

    async def releve(self):
        """Lit (ou relit) les minuteurs des forteresses de tous les royaumes actifs et met a jour self.fort."""
        lus = set()
        nouveau = {}
        gardees = []
        for kid in self._royaumes_actifs():
            src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
            nom = "royaume %d (%s)" % (kid, ROYAUMES[kid])
            if kid in self._lecture:
                continue                                # 1re lecture deja en cours (par morceaux)
            complet = kid not in self.carte or self.lu_jusqua.get(kid, 0) < dmax
            if complet:
                self._demarrer_lecture(kid)             # lue par morceaux entre deux attaques : les attaques commencent tout de suite
                continue
            vues, echecs, nb_zones, raisons = await self._lire_royaume(kid, False)
            rates = self.manquantes.setdefault(kid, {})
            laissees = self.abandonnees.setdefault(kid, set())
            for z in echecs[:3]:
                self._noter("   zone x %d a %d, y %d a %d sans reponse apres 3 essais : %s" % (z[0], z[2], z[1], z[3], raisons.get(z, "?")))
            if echecs:
                self._noter("   %s : %d zone(s) sur %d sans reponse du jeu." % (nom, len(echecs), nb_zones))
            if not vues and self.carte.get(kid) and not echecs:
                self.carte.pop(kid, None)           # plus rien aux positions memorisees : on relit toute la carte (par morceaux)
                self.lu_jusqua.pop(kid, None)
                self._demarrer_lecture(kid)
            else:
                for pos, v in vues.items():
                    self.carte.setdefault(kid, {})[pos] = v[0]
                for z in list(rates):                  # zones ratees : lue = oubliee ; encore ratee = on compte, puis on abandonne
                    if z not in echecs:
                        del rates[z]
                    else:
                        rates[z] += 1
                        if rates[z] >= ESSAIS_ZONE:
                            del rates[z]
                            laissees.add(z)
                            self._noter("   %s : la zone x %d a %d, y %d a %d ne repond jamais : elle est ignoree." % (nom, z[0], z[2], z[1], z[3]))
                for z in echecs:
                    if z not in laissees:
                        rates.setdefault(z, 1)
            lus.add(kid)
            for (x, y), (niv, minu, t) in vues.items():
                if math.hypot(x - src["x"], y - src["y"]) <= dmax:
                    nouveau[(kid, x, y)] = {"niveau": niv, "dispo": t + minu}
            # une forteresse dans une zone qui n'a pas repondu garde sa derniere valeur connue (le minuteur est une heure absolue)
            non_lues = set(echecs) | laissees
            gardees += [k for k in self.fort if k[0] == kid and any(dans_carre(z, k[1], k[2]) for z in non_lues)]
        anciens = self.fort
        self.fort = {k: v for k, v in anciens.items() if k[0] not in lus}
        self.fort.update(nouveau)
        for k in gardees:
            if k not in self.fort:
                self.fort[k] = anciens[k]
        self.releve_t = self.horloge()
        if not LECTURE_PAR_MORCEAUX:
            await self.finir_lecture()               # ancien mode : la 1re lecture d'un coup

    # ---------------------------------------------------------------- reservation des commandants communs
    def _reglage_reserve(self):
        """(secondes, maximum) de la reservation ; (0, 0) si elle est desactivee."""
        r = self.cfg.get("reserve")
        if not reserve_valide(r) or not r or r["secondes"] <= 0:
            return 0, 0
        return r["secondes"], r["max"]

    def reserve_active(self):
        return bool(self.communs) and self._reglage_reserve()[0] > 0

    def forteresses_dues(self):
        """Forteresses (royaumes actifs, pas deja engagees) attaquables maintenant ou dans moins de `secondes` secondes."""
        secondes = self._reglage_reserve()[0]
        t = self.horloge()
        actifs = set(self._royaumes_actifs())
        return sum(1 for k, f in self.fort.items() if k[0] in actifs and self.engagees.get(k, 0) <= t and f["dispo"] - t <= secondes)

    def commandants_reserves(self):
        """Commandants communs que l'AUTRE module n'a pas le droit de prendre maintenant : un par forteresse attaquable bientot,
        au plus `max`, parmi les commandants communs deja revenus (un commandant qui revient est garde des son retour).
        Publie dans le partage de la session (partage.reservations["FORTERESSES"]) par le lanceur commun."""
        if not self.reserve_active():
            return set()
        secondes, maximum = self._reglage_reserve()
        t = self.horloge()
        dues = self.forteresses_dues()
        dispo = [p for p in self.communs if p not in self.absents and self.libre_a.get(p, 0) <= t]
        gardes = set(dispo[:min(maximum, dues)])
        vus = tuple(sorted(gardes))
        if vus != self._gardes_vus:
            if vus:
                self._noter("   reservation : commandant(s) %s gardes pour les forteresses (%d attaquable(s) dans moins de %s)."
                            % (";".join(str(p) for p in vus), dues, duree(secondes)))
            else:
                self._noter("   reservation levee : les autres modules peuvent de nouveau utiliser ces commandants.")
            self._gardes_vus = vus
        return gardes

    def _lecture_complete_a_refaire(self):
        """Royaume dont la carte n'a jamais ete lue en entier jusqu'a la distance maximale (1re lecture ratee), hors lecture en cours."""
        return [k for k in self._royaumes_actifs() if k not in self._lecture
                and (k not in self.carte or self.lu_jusqua.get(k, 0) < self._reglages(k)["distance_max"])]

    # ---------------------------------------------------------------- 1re lecture de la carte, par morceaux
    def _demarrer_lecture(self, kid):
        """Prepare la lecture complete du royaume : zones du cercle, la plus proche du chateau d'abord. La lecture elle-meme se
        fait par morceaux (_lire_morceau), entre deux attaques : les forteresses trouvees sont attaquables tout de suite."""
        src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
        zs = zones_cercle(src["x"], src["y"], dmax)
        n, sec = estimation_premiere_lecture(dmax, src["x"], src["y"])
        self._lecture[kid] = {"zones": [(c, 0) for c in zs], "total": len(zs), "vues": {}, "echecs": [], "raisons": {},
                              "debut": self.horloge(), "faites": 0, "prochain": 0.25}
        self.abandonnees.setdefault(kid, set()).clear()
        self._noter("Lecture de la carte du royaume %d (%s) jusqu'a %d cases : %d zones (cercle), environ %s, une seule fois. "
                    "Les plus proches d'abord, par petits morceaux : les attaques commencent pendant la lecture."
                    % (kid, ROYAUMES[kid], dmax, len(zs), duree(sec)))

    def _lecture_en_cours(self):
        """True s'il reste des zones a lire dans un royaume actif (1re lecture par morceaux)."""
        actifs = set(self._royaumes_actifs())
        return any(k in actifs and st["zones"] for k, st in self._lecture.items())

    def avancement_lecture(self):
        """Pourcentage deja lu de la 1re lecture en cours (tous royaumes), ou None s'il n'y en a pas."""
        if not self._lecture:
            return None
        total = sum(st["total"] for st in self._lecture.values())
        faites = sum(st["faites"] + len(st["echecs"]) for st in self._lecture.values())
        return round(100 * faites / total) if total else 100

    async def _lire_morceau(self, nb=None):
        """Lit au plus `nb` zones de la 1re lecture (tous royaumes : la zone la plus proche de SON chateau d'abord). Une zone sans
        reponse est remise a la fin (ESSAIS_ZONE essais en tout). Royaume termine : carte memorisee (voir _finir_lecture)."""
        for _ in range(nb or ZONES_LECTURE_PAR_PASSAGE):
            actifs = set(self._royaumes_actifs())
            prets = [k for k, st in self._lecture.items() if k in actifs and st["zones"]]
            if not prets:
                break
            kid = min(prets, key=lambda k: distance_zone(self._lecture[k]["zones"][0][0], self.sources[k]["x"], self.sources[k]["y"]))
            st = self._lecture[kid]
            c, essais = st["zones"].pop(0)
            fs = await self._lire_carre(kid, c, delai=10 if essais == 0 else 6)
            if fs is None:
                st["raisons"][c] = self.raison
                if essais + 1 < ESSAIS_ZONE:
                    st["zones"].append((c, essais + 1))      # retentee a la fin de la lecture
                else:
                    st["echecs"].append(c)
            else:
                st["faites"] += 1
                lu = self.horloge()
                src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
                for (x, y, niv, minu) in fs:
                    st["vues"][(x, y)] = niv
                    self.carte.setdefault(kid, {})[(x, y)] = niv
                    if math.hypot(x - src["x"], y - src["y"]) <= dmax:
                        self.fort[(kid, x, y)] = {"niveau": niv, "dispo": lu + minu}
            part = (st["faites"] + len(st["echecs"])) / st["total"] if st["total"] else 1
            if st["total"] >= 40 and part >= st["prochain"] and st["zones"]:
                self._noter("   ... %d %% de la carte du royaume %d (%s) lue, %d forteresse(s) trouvee(s)."
                            % (round(100 * part), kid, ROYAUMES[kid], len(st["vues"])))
                while st["prochain"] <= part:
                    st["prochain"] += 0.25
            if not st["zones"]:
                self._finir_lecture(kid)

    def _finir_lecture(self, kid):
        st = self._lecture.pop(kid)
        src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
        nom = "royaume %d (%s)" % (kid, ROYAUMES[kid])
        echecs, nb, vues = st["echecs"], st["total"], st["vues"]
        for z in echecs[:3]:
            self._noter("   zone x %d a %d, y %d a %d sans reponse apres %d essais : %s"
                        % (z[0], z[2], z[1], z[3], ESSAIS_ZONE, st["raisons"].get(z, "?")))
        if echecs:
            self._noter("   %s : %d zone(s) sur %d sans reponse du jeu." % (nom, len(echecs), nb))
        self.carte[kid] = dict(vues)
        for k in [k for k in self.fort if k[0] == kid and (k[1], k[2]) not in vues
                  and not any(dans_carre(z, k[1], k[2]) for z in echecs)]:
            del self.fort[k]                       # plus rien a cette position (ancienne lecture)
        rates = self.manquantes.setdefault(kid, {})
        if len(echecs) <= max(3, nb // 20):
            self.lu_jusqua[kid] = dmax                # carte (presque) complete : memorisee, les zones ratees seront retentees
            rates.clear()
            rates.update({z: 0 for z in echecs})
            self._sauver_carte()
        else:
            self._noter("   %s : trop de zones sans reponse (%d sur %d) : la carte sera relue en entier dans %s."
                        % (nom, len(echecs), nb, duree(REFRAICHIR)))
        proches = sum(1 for (x, y) in vues if math.hypot(x - src["x"], y - src["y"]) <= dmax)
        self._noter("   %s : lecture de la carte terminee, %d forteresse(s) trouvee(s) (dont %d a moins de %d cases) en %s."
                    % (nom, len(vues), proches, dmax, duree(self.horloge() - st["debut"])))
        self.releve_t = self.horloge()               # prochaine relecture des minuteurs dans REFRAICHIR secondes

    async def finir_lecture(self):
        """Lit d'un coup tout ce qui reste de la 1re lecture (tests, ou module seul sans rien d'autre a faire)."""
        while self._lecture_en_cours():
            await self._lire_morceau()

    async def _rafraichir(self):
        """Relecture des minuteurs PAR MORCEAUX : au plus ZONES_PAR_PASSAGE zones par passage (environ 2 s), pour ne jamais
        bloquer les autres modules du lanceur (un commandant de retour repart tout de suite). Toutes les REFRAICHIR secondes,
        la liste des zones a relire est refaite : zones qui contiennent une forteresse connue + zones deja ratees."""
        t = self.horloge()
        if not self._a_relire:
            if t - self.releve_t < REFRAICHIR:
                return
            for kid in self._royaumes_actifs():
                if kid in self._lecture:
                    continue                             # 1re lecture en cours : ses minuteurs sont tout frais
                src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
                connues, rates = self.carte.get(kid, {}), self.manquantes.get(kid, {})
                laissees = self.abandonnees.get(kid, ())
                self._a_relire += [(kid, c) for c in zones_cercle(src["x"], src["y"], dmax)
                                   if c not in laissees and (c in rates or any(dans_carre(c, x, y) for (x, y) in connues))]
            self.releve_t = t
        for _ in range(ZONES_PAR_PASSAGE):
            if not self._a_relire:
                break
            kid, c = self._a_relire.pop(0)
            if kid not in self._royaumes_actifs():
                continue
            fs = await self._lire_carre(kid, c, delai=6)
            rates = self.manquantes.setdefault(kid, {})
            if fs is None:                               # pas de reponse : on garde les dernieres valeurs connues
                rates[c] = rates.get(c, 0) + 1
                if rates[c] >= ESSAIS_ZONE:
                    del rates[c]
                    self.abandonnees.setdefault(kid, set()).add(c)
                    self._noter("   royaume %d (%s) : la zone x %d a %d, y %d a %d ne repond jamais : elle est ignoree."
                                % (kid, ROYAUMES[kid], c[0], c[2], c[1], c[3]))
                continue
            rates.pop(c, None)
            self._appliquer_zone(kid, c, fs)

    def _appliquer_zone(self, kid, c, fs):
        """Zone relue : minuteurs a jour ; une forteresse connue qui n'y est plus libre (prise par un joueur) est retiree."""
        lu = self.horloge()
        src, dmax = self.sources[kid], self._reglages(kid)["distance_max"]
        libres = set()
        for (x, y, niv, minu) in fs:
            libres.add((x, y))
            self.carte.setdefault(kid, {})[(x, y)] = niv
            if math.hypot(x - src["x"], y - src["y"]) <= dmax:
                self.fort[(kid, x, y)] = {"niveau": niv, "dispo": lu + minu}
        for k in [k for k in self.fort if k[0] == kid and dans_carre(c, k[1], k[2]) and (k[1], k[2]) not in libres]:
            del self.fort[k]                         # plus libre (proprietaire) : on ne la propose plus

    async def _apres_sans_info(self, cle):
        """Le jeu n'a pas donne les infos de la forteresse : on relit TOUT DE SUITE sa zone de la carte. Si elle n'y est plus
        libre (prise par un joueur) ou qu'elle a un delai, on le sait sans attendre la relecture des 5 minutes."""
        kid, x, y = cle
        c = next((z for z in zones_cercle(self.sources[kid]["x"], self.sources[kid]["y"], self._reglages(kid)["distance_max"])
                  if dans_carre(z, x, y)), None)
        if c is None:
            return None
        fs = await self._lire_carre(kid, c, delai=6)
        if fs is None:
            return None
        self._appliquer_zone(kid, c, fs)
        f = self.fort.get(cle)
        if f is None:
            return "la carte relue la montre prise par un autre joueur : retiree de la liste"
        if f["dispo"] > self.horloge() + 1:
            self.engagees[cle] = f["dispo"]
            return "la carte relue lui donne un delai de %s : reprise a ce moment-la" % duree(f["dispo"] - self.horloge())
        return None

    # ---------------------------------------------------------------- cibles
    def _candidats(self):
        """[(distance, royaume, entree)] : forteresses attaquables maintenant, dans la distance maximale de LEUR royaume,
        pas deja engagees, la plus proche d'abord."""
        t = self.horloge()
        actifs = set(self._royaumes_actifs())
        res = []
        for (kid, x, y), f in self.fort.items():
            if kid not in actifs or f["dispo"] > t + 1 or (kid, x, y) in self.engagees:
                continue
            src = self.sources[kid]
            res.append((math.hypot(x - src["x"], y - src["y"]), kid, [11, x, y, -1, f["niveau"], 0, 0, kid]))
        res.sort(key=lambda c: c[0])
        return res

    def urgence(self):
        """True si une forteresse est attaquable MAINTENANT et qu'un commandant de ce module est libre (commandants gardes par la
        reservation compris) : le lanceur fait alors passer ce module tout de suite, les autres s'interrompent."""
        if not self._libres():
            return False
        t = self.horloge()
        actifs = set(self._royaumes_actifs())
        return any(k[0] in actifs and self.engagees.get(k, 0) <= t and f["dispo"] <= t + 1 for k, f in self.fort.items())

    def _prochaine(self):
        """Secondes avant la prochaine forteresse attaquable (d'apres les minuteurs lus), ou None."""
        t = self.horloge()
        actifs = set(self._royaumes_actifs())
        d = [f["dispo"] - t for (kid, x, y), f in self.fort.items() if kid in actifs and (kid, x, y) not in self.engagees]
        return min(d) if d else None

    async def preparer_carte(self):
        """Premiere lecture de la carte (au demarrage, avant la boucle) : positions memorisees ou lecture complete."""
        self._charger_carte()
        await self.releve()
        for kid in self._royaumes_actifs():
            dmax = self._reglages(kid)["distance_max"]
            if kid in self._lecture:
                self.sortie("  - royaume %d (%s) : lecture de la carte jusqu'a %d cases en cours (%d zones) : les forteresses "
                            "trouvees sont attaquees pendant la lecture." % (kid, ROYAUMES[kid], dmax, self._lecture[kid]["total"]))
                continue
            n = sum(1 for k, f in self.fort.items() if k[0] == kid)
            dispo = sum(1 for k, f in self.fort.items() if k[0] == kid and f["dispo"] <= self.horloge() + 1)
            self.sortie("  - royaume %d (%s) : %d forteresse(s) dans la distance maximale (%d cases), %d attaquable(s) maintenant"
                        % (kid, ROYAUMES[kid], n, dmax, dispo))

    async def cycle(self):
        """Un passage : renvoie le nombre de secondes a attendre avant le suivant."""
        await self._absorber()
        t = self.horloge()
        self.en_route = {k: v for k, v in self.en_route.items() if v[1] > t - 3600}
        self.engagees = {k: v for k, v in self.engagees.items() if v > t}
        self._verifier_stock()
        libres = self._libres()
        if self.reserve_active():
            libres.sort(key=lambda p: p in self.communs)     # les commandants propres d'abord : les communs restent libres pour l'autre module
        if not libres:
            restants = [self.libre_a.get(p, 0) for p in self.cfg["commandants"] if p not in self.absents]
            if not restants:
                raise ArretModule("Aucun des commandants choisis n'existe dans ta liste : arret.")
            attente = max(5, min(restants) - t)
            self._attente_msg("commandants", "En attente : un commandant revient au plus tard dans %s." % duree(attente))
            if self._lecture_en_cours():
                await self._lire_morceau()           # la lecture de la carte continue pendant que les commandants sont partis
                return PAUSE_LECTURE
            return min(attente, 3)
        if self.releve_t is None or (self._lecture_complete_a_refaire() and t - self.releve_t >= REFRAICHIR):
            await self.releve()                      # au demarrage, ou si la 1re lecture a rate : (re)lance la lecture par morceaux
        if self._lecture_en_cours():
            await self._lire_morceau()               # 1re lecture : quelques zones, puis on attaque ce qui a ete trouve
        else:
            await self._rafraichir()                 # minuteurs : quelques zones par passage seulement
        t = self.horloge()
        cands = self._candidats()
        if not cands:
            proch = self._prochaine()
            if self._lecture_en_cours():
                self._attente_msg("lecture", "Lecture de la carte en cours (%d %%) : rien d'attaquable pour l'instant "
                                  "(%d commandant(s) pret(s))." % (self.avancement_lecture(), len(libres)))
                return PAUSE_LECTURE
            if proch is None:
                self._attente_msg("aucune-forteresse", "Aucune forteresse dans la distance choisie (%d commandant(s) pret(s)) : "
                                  "nouvelle lecture de la carte dans %s." % (len(libres), duree(REFRAICHIR - (t - self.releve_t))))
            else:
                self._attente_msg("prochaine-%d" % (int(proch) // 600),
                                  "Aucune forteresse attaquable pour le moment (%d commandant(s) pret(s)) : la prochaine dans %s."
                                  % (len(libres), duree(proch)))
            attente = min(max(proch, 0.5), 60) if proch is not None else 60   # reveil a la seconde ou la prochaine devient attaquable
            return min(attente, 2) if self._a_relire else attente               # relecture en cours : on continue bientot
        par_royaume = ", ".join("%s %d" % (ROYAUMES[k], sum(1 for c in cands if c[1] == k)) for k in sorted({c[1] for c in cands}))
        self._attente_msg("forteresses-%s" % par_royaume, "%d forteresse(s) attaquable(s) (%s), %d commandant(s) pret(s)."
                          % (len(cands), par_royaume, len(libres)))
        for pos in libres:
            for _ in range(ESSAIS_PAR_COMMANDANT):
                cand = next((c for c in cands if self.sans_stock.get(c[1], 0) <= self.horloge()), None)
                if cand is None:
                    break
                cands.remove(cand)
                ok = await self.attaquer(pos, cand[2], cand[1])
                if ok or pos in self.absents:
                    break
            if self.max_attaques and self.nb_attaques >= self.max_attaques:
                return 0
        self._verifier_stock()
        return PAUSE_LECTURE if self._lecture_en_cours() else 5


# ============================================================================== lancement
# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "forteresses"
LANCEUR_ETIQUETTE = "FORT"
LANCEUR_CLE = ModuleForteresses.NOM_MODULE   # cle du module dans le partage (reservations, commandants)
LANCEUR_PRIORITE = 0           # le plus prioritaire : passe avant tous les autres modules


def lanceur_config(args, lire=None, sortie=print):
    """AVANT la connexion : configuration du module (reprise ou assistant)."""
    cfg = tours.preparer_config(args, lire, sortie, rubrique=RUBRIQUE, assistant=assistant_config, valide=config_valide,
                                resume=resume_config)
    if getattr(args, "avec_bonus", False):
        cfg["avec_bonus"] = True
    return cfg


def lanceur_ressources(cfg, sortie=print):
    """AVANT la connexion : effets des commandants, unites (soldat le plus rapide), fiches des outils."""
    unites = combat.charger_unites(tours.FICHIER_ITEMS) if os.path.exists(tours.FICHIER_ITEMS) else None
    if not unites:
        raise ArretModule("Le fichier %s est introuvable : je ne peux pas savoir quel soldat est le plus rapide. "
                          "Lance outils\\lancer_items.bat d'abord." % tours.FICHIER_ITEMS)
    return {"effets": tours.charger_effets(sortie), "unites": unites,
            "fiches_outils": tours.charger_fiches_outils(sortie) if tours.royaumes_en_presets(cfg) else None}


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    """APRES la connexion : chateaux, preselections, cheval, carte ; publie la reservation dans le partage ; renvoie le module."""
    sources = await tours.determiner_sources(jeu, cfg, sortie, lire, rubrique=RUBRIQUE)
    sortie("")
    sortie("CHATEAUX DE DEPART :")
    for ligne in tours.lignes_sources(sources):
        sortie("  - " + ligne)
    presets = None
    if tours.royaumes_en_presets(cfg):
        presets = await tours.lire_presets(jeu, sortie)
        tours.verifier_presets(cfg, presets)
        sortie("")
        sortie("PRESELECTIONS UTILISEES (lues sur ton compte) :")
        for ligne in tours.lignes_presets(cfg, presets):
            sortie("  - " + ligne)
        if not presets:
            raise ArretModule("Mode preselections : les preselections du compte ne sont pas chargees.")
    await tours.assurer_chevaux(jeu, cfg, sortie, lire, args.simulation)
    autres = [p for nom, liste in partage.commandants.items() if nom != ModuleForteresses.NOM_MODULE for p in liste]
    communs = commandants_communs(cfg["commandants"], autres)
    module = ModuleForteresses(jeu, cfg, sources, ressources["effets"], unites=ressources["unites"], sortie=sortie, lire=lire,
                               simulation=args.simulation, confirmer=getattr(args, "confirmer", False), presets=presets,
                               fiches_outils=ressources["fiches_outils"], communs=communs, partage=partage)
    if module.reserve_active():
        partage.reservations[ModuleForteresses.NOM_MODULE] = module.commandants_reserves
    for ligne in lignes_reserve(cfg, communs, active=True):
        sortie(ligne)
    sortie("")
    sortie("CARTE DES FORTERESSES (lue sur le jeu) :")
    await module.preparer_carte()
    return module


def lire_args(argv=None):
    p = argparse.ArgumentParser(description="Module d'attaque des forteresses (voir l'en-tete du fichier).")
    p.add_argument("--reconfigurer", action="store_true", help="refaire les questions de configuration")
    p.add_argument("--simulation", action="store_true", help="tout faire sauf envoyer les attaques")
    p.add_argument("--max-attaques", type=int, default=None, help="s'arreter apres N attaques (test)")
    p.add_argument("--duree", type=tours.argument_duree, default=None, help="s'arreter apres cette duree : 90m, 3h, 2h30 (sinon : jusqu'a Ctrl+C)")
    p.add_argument("--oui", action="store_true", help="(sans effet : plus aucune confirmation par defaut)")
    p.add_argument("--confirmer", action="store_true", help="demander OUI avant la premiere attaque (verification)")
    p.add_argument("--avec-bonus", action="store_true", help="applique le bonus de flanc/front du commandant (non verifie)")
    p.add_argument("--apprendre-cheval", choices=("C", "F"), help="enregistrer les reglages du cheval pieces (C) ou plumes (F)")
    p.add_argument("--pseudo")
    p.add_argument("--sans-ecran-allume", action="store_true", help="laisser Windows mettre l'ecran en veille / verrouiller la session")
    return p.parse_args(argv)


async def principal(args):
    from session import SessionBot
    import connexion_bot

    async def lire_async(invite):
        return await asyncio.to_thread(input, invite)

    try:
        cfg = None if args.apprendre_cheval else tours.preparer_config(
            args, rubrique=RUBRIQUE, assistant=assistant_config, valide=config_valide, resume=resume_config)
        if cfg and args.avec_bonus:
            cfg["avec_bonus"] = True
        effets = tours.charger_effets() if cfg else None
        presets_utilises = bool(cfg) and bool(tours.royaumes_en_presets(cfg))
        fiches_outils = tours.charger_fiches_outils() if presets_utilises else None
        unites = combat.charger_unites(tours.FICHIER_ITEMS) if cfg and os.path.exists(tours.FICHIER_ITEMS) else None
        if cfg and not unites:
            raise ArretModule("Le fichier %s est introuvable : je ne peux pas savoir quel soldat est le plus rapide. "
                              "Lance outils\\lancer_items.bat d'abord." % tours.FICHIER_ITEMS)
        import eveil
        eveil.console_sans_selection()                 # un clic dans la fenetre du bot ne le met plus en pause
        if not getattr(args, "sans_ecran_allume", False):
            eveil.GarderEveille(print).__enter__()     # ecran et PC allumes ; Windows reprend ses reglages a la fin du programme
        async with SessionBot(demander_identifiants=lambda: connexion_bot.demander_identifiants_ecran(args.pseudo)) as jeu:
            async def vivante():
                return await connexion_bot._vivante(jeu)
            try:
                if args.apprendre_cheval:
                    await tours.apprendre_cheval(jeu, args.apprendre_cheval, print, lire_async)
                    return
                sources = await tours.determiner_sources(jeu, cfg, print, lire_async, rubrique=RUBRIQUE)
                print("")
                print("CHATEAUX DE DEPART :")
                for ligne in tours.lignes_sources(sources):
                    print("  - " + ligne)
                presets = None
                if presets_utilises:
                    presets = await tours.lire_presets(jeu, print)
                    tours.verifier_presets(cfg, presets)
                    print("")
                    print("PRESELECTIONS UTILISEES (lues sur ton compte) :")
                    for ligne in tours.lignes_presets(cfg, presets):
                        print("  - " + ligne)
                await tours.assurer_chevaux(jeu, cfg, print, lire_async, args.simulation)
                communs = commandants_communs(cfg["commandants"], autres_commandants())
                module = ModuleForteresses(jeu, cfg, sources, effets, unites=unites, sortie=print, lire=lire_async,
                                           vivante=vivante, simulation=args.simulation, max_attaques=args.max_attaques,
                                           confirmer=getattr(args, "confirmer", False), presets=presets, fiches_outils=fiches_outils,
                                           duree_max=args.duree, communs=communs)
                for ligne in lignes_reserve(cfg, communs):
                    print(ligne)
                print("")
                print("CARTE DES FORTERESSES (lue sur le jeu) :")
                await module.preparer_carte()
                await module.lancer()
            except ArretModule as e:
                print("\n" + str(e))
    except ArretModule as e:
        print("\n" + str(e))
    except tours.T.ErreurCDP as e:
        print("ERREUR :", e)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nArret demande.")


if __name__ == "__main__":
    try:
        asyncio.run(principal(lire_args()))
    except KeyboardInterrupt:
        print("\nArret.")
