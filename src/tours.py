#!/usr/bin/env python3
"""
tours.py : module d'attaque des TOURS.

Lancement : outils\\lancer_bot_tours.bat   (ou : py src\\tours.py)

Au PREMIER lancement, un assistant pose les questions de configuration (enregistrees dans config.json, rubrique
"tours") ; ensuite la configuration est reprise (Entree) ou refaite (N, ou option --reconfigurer).

Questions : 1) commandants (ex. 1;3;4;6)   2) preselections (P) ou remplissage automatique (R)
            3) preselection de chaque vague  OU  cotes F / FF / A / AF / AFF   4) echelles en bois (remplissage auto)
            5) soldat (unite 216 pour l'instant)   6) cheval : C pieces / F plumes / CF (pieces puis plumes a partir
            d'une distance)   7) royaumes a attaquer (ex. 3 ou 1;3 ou 0;2;3)   8) rayon de recherche.

Les chateaux (avant-postes) de depart sont lus automatiquement sur le compte pour chaque royaume (le serveur envoie
leur liste) : plus aucune coordonnee a taper. Le bot se connecte (session.py), scanne les tours libres autour du
chateau de chaque royaume choisi, et attaque TOUJOURS la tour libre la plus proche, tous royaumes confondus, avec
le premier commandant libre (les commandants sont partages entre les royaumes). Il se deconnecte proprement a
l'arret (Ctrl+C).
Options utiles : --simulation (ne rien envoyer), --max-attaques N, --duree 3h (s'arrete et se deconnecte apres 3 h), --reconfigurer, --apprendre-cheval C|F.
"""
import argparse
import asyncio
import copy
import json
import math
import os
import re
import sys
import time
import unicodedata

RACINE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "outils"))
import combat           # noqa: E402
import test_envoi as T  # noqa: E402

CONFIG = os.path.join(RACINE, "config.json")
DOSSIER_CAPTURES = os.path.join(RACINE, "captures")
FICHIER_ITEMS = os.path.join(DOSSIER_CAPTURES, "items.json")
FICHIER_CHEVAUX = os.path.join(DOSSIER_CAPTURES, "chevaux.json")
FICHIER_JOURNAL = os.path.join(DOSSIER_CAPTURES, "journal_tours.txt")

ID_SOLDAT = 216                 # unite par defaut (Valkyrie pisteuse niveau 10)
# Unites proposees pour les tours : (numero dans le jeu, nom, type, vitesse, attaque) ; numeros trouves dans items.json et dans le
# stock du chateau (adi). Images : src/images/unites/<numero>.png. Plusieurs unites : la 1re d'abord, completee par la suivante.
UNITES_TOURS = (
    (216, "Valkyrie pisteuse niv. 10", "distance", 34, 310),
    (215, "Vierge a bouclier niv. 10", "corps a corps", 34, 325),
    (6, "Frondeur veteran", "distance", 28, 139),
    (35, "Lanceur de kunai renegat", "distance", 50, 148),
    (10, "Horreur mortelle veteran", "distance", 30, 175),
    (9, "Horreur demoniaque veteran", "corps a corps", 30, 200),
)
IDS_UNITES_TOURS = tuple(u[0] for u in UNITES_TOURS)
NB_SOLDATS_MAX = 4              # unites choisies au plus par royaume (2 au plus par flanc dans une vague, cases du jeu)
ID_ECHELLE = 614                # echelle en bois (valide dans une attaque reelle)
NB_ECHELLES = 10                # sur la 1re vague de chaque cote choisi
NB_VAGUES_BASE = 4              # vagues debloquees pour ce compte (+ vagues supplementaires du commandant)
NB_COMMANDANTS_MAX = 35         # chaque joueur a 35 commandants au plus (numeros 1 a 35 dans la liste du jeu)
NB_PRESELECTIONS_MAX = 30       # preselections du jeu : numeros 1 a 30
NB_PRESELECTIONS_DEMANDEES = 4  # on demande la preselection des vagues 1 a 4 ; la 4e sert pour les suivantes
CHAMPS_CHEVAL = ("HBW", "PTT")  # champs du paquet cra qui dependent du cheval (jeu : HBW = ..., PTT = ...)
ROYAUMES = {0: "vert", 1: "sables", 2: "glacier", 3: "feu"}
DELAI_MIN_ATTAQUES = 4.2         # le serveur ignore une attaque partie moins de 4 s apres la precedente (+ 0,2 s de marge)
ECHECS_MAX = 3                  # refus consecutifs du serveur avant arret du module
ZONES_RATEES_MAX = 3            # relectures de la carte sans reponse de suite avant une pause (et un message)
PAUSE_ZONES_RATEES = 30
RELIRE_TOURS = 60               # secondes : une zone deja lue est relue (si un commandant est libre) apres ce delai -> tour accelere vue
ZONES_RELUES_PAR_PASSAGE = 3    # zones relues au plus par passage (les plus proches du chateau d'abord) : jamais long
VERIFS_MAX_PAR_PASSAGE = 10     # tours verifiees (relues) au plus par passage : au-dela, pause courte et message (jamais de boucle sans fin)
LARGEUR_CRAN = 13              # cases : la carte est lue par couronnes de cette largeur autour du chateau (= une zone gaa)
CIBLE_REFUSEE = (1800, 10800)   # cible ignoree par le serveur : laissee de cote 30 min, puis 3 h si elle est encore refusee
ESSAIS_PAR_PASSAGE = 8          # cibles essayees au plus par passage (une cible refusee ne bloque pas le commandant)
PAUSE_SANS_STOCK = 300          # secondes pendant lesquelles un royaume sans soldat 216 est laisse de cote
TYPES_CHATEAU = {1: "chateau", 12: "avant-poste"}   # types vus dans la liste des chateaux du compte (autres : "type N")


JOURNAL_DEFAUT = object()     # journal = fichier par defaut (None = pas de journal)


class ArretModule(Exception):
    """Arret volontaire du module (message deja pret pour l'utilisateur)."""


class CibleEnAttente(Exception):
    """La cible n'est pas attaquable maintenant (minuteur du jeu, ou ce n'est pas ce qu'on croyait) : on la laisse de cote."""
    def __init__(self, secondes, texte=""):
        super().__init__(texte)
        self.secondes, self.texte = secondes, texte


class PlusDeSoldats(ArretModule):
    """Le chateau de depart n'a plus de soldat de l'unite choisie (les armees sont propres a chaque chateau)."""


# ============================================================================== assistant de configuration
def sans_accents(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")


def _entier(texte):
    """Entier ecrit uniquement avec les chiffres 0-9 (pas de signe, pas d'espace, pas de virgule) ; sinon None."""
    t = texte.strip()
    return int(t) if t.isascii() and t.isdigit() else None


def analyser_commandants(texte):
    """'1;3;4;6' -> [1, 3, 4, 6] (positions 1-based dans la liste du jeu, sans doublon, ordre conserve).
    Seul le ';' est accepte comme separateur ; aucune case vide."""
    parts = [p.strip() for p in texte.strip().split(";")]
    if any(p == "" for p in parts):
        raise ValueError("Ecris des numeros separes par des ';', sans case vide (ex. 1;3;4;6).")
    res = []
    for p in parts:
        n = _entier(p)
        if n is None or not 1 <= n <= NB_COMMANDANTS_MAX:
            raise ValueError("'%s' n'est pas un numero valide. Ecris des numeros de 1 a %d separes par des ';' (ex. 1;3;4;6)."
                             % (p, NB_COMMANDANTS_MAX))
        if n not in res:
            res.append(n)
    return res


def analyser_mode(texte):
    t = sans_accents(texte).strip().upper()
    if t == "P":
        return "presets"
    if t == "R":
        return "auto"
    raise ValueError("Reponds exactement P (preselections) ou R (remplissage automatique).")


def analyser_cotes(texte):
    t = sans_accents(texte).strip().upper()
    if t not in combat.COTES:
        raise ValueError("Reponds exactement F, FF, A, AF ou AFF.")
    return t


def analyser_oui_non(texte):
    t = sans_accents(texte).strip().upper()
    if t in ("O", "OUI"):
        return True
    if t in ("N", "NON"):
        return False
    raise ValueError("Reponds exactement O (oui) ou N (non).")


def analyser_confirmation(texte):
    """Questions avec Entree = oui : Entree / O / OUI -> True ; N / NON -> False ; tout le reste est refuse."""
    t = sans_accents(texte).strip().upper()
    if t in ("", "O", "OUI"):
        return True
    if t in ("N", "NON"):
        return False
    raise ValueError("Reponds Entree (ou O) pour oui, N pour non.")


def analyser_cheval(texte):
    t = sans_accents(texte).strip().upper()
    if t not in ("C", "F", "CF"):
        raise ValueError("Reponds exactement C (pieces), F (plumes) ou CF (pieces puis plumes).")
    return t


def analyser_entier_positif(texte):
    """Nombre > 0 : chiffres, avec une partie decimale facultative (ex. 20 ou 20.5)."""
    t = texte.strip().replace(",", ".")
    ent, _, dec = t.partition(".")
    if not (ent.isascii() and ent.isdigit() and (dec == "" or (dec.isascii() and dec.isdigit()))) or float(t) <= 0:
        raise ValueError("Ecris un nombre superieur a 0 (ex. 20).")
    v = float(t)
    return int(v) if v == int(v) else v


def analyser_distance_dizaines(texte):
    """Distance maximale en cases : un multiple de 10 (10, 20, 30... 500)."""
    t = texte.strip()
    if not (t.isascii() and t.isdigit()) or int(t) % 10 or not 10 <= int(t) <= 500:
        raise ValueError("Ecris un multiple de 10 entre 10 et 500 (exemples : 20, 50, 100).")
    return int(t)


def analyser_soldats(texte):
    """'216' ou '216;215' -> liste d'unites de UNITES_TOURS, sans doublon, ordre conserve (1 a NB_SOLDATS_MAX)."""
    parts = [p.strip() for p in texte.strip().split(";")]
    if any(p == "" for p in parts):
        raise ValueError("Ecris des numeros d'unite separes par des ';' (ex. 216 ou 216;215).")
    res = []
    for p in parts:
        n = _entier(p)
        if n not in IDS_UNITES_TOURS:
            raise ValueError("'%s' n'est pas une unite proposee : %s." % (p, ", ".join(str(i) for i in IDS_UNITES_TOURS)))
        if n not in res:
            res.append(n)
    if len(res) > NB_SOLDATS_MAX:
        raise ValueError("%d unites au plus." % NB_SOLDATS_MAX)
    return res


def analyser_soldat_unique(texte):
    res = analyser_soldats(texte)
    if len(res) != 1:
        raise ValueError("Une seule unite de remplacement (ex. 216).")
    return res


def soldats_de(b):
    """Unites choisies pour un royaume, dans l'ordre (ancienne configuration : la seule unite 'soldat')."""
    s = b.get("soldats")
    if isinstance(s, list) and s and all(isinstance(x, int) and not isinstance(x, bool) for x in s):
        return list(s)
    return [b.get("soldat", ID_SOLDAT)]


def nom_unite(wid):
    return next(("%d %s" % (u[0], u[1]) for u in UNITES_TOURS if u[0] == wid), "unite %s" % wid)


def analyser_preselection(texte):
    """Numero de preselection : 1 a NB_PRESELECTIONS_MAX (les preselections du jeu)."""
    n = _entier(texte)
    if n is None or not 1 <= n <= NB_PRESELECTIONS_MAX:
        raise ValueError("Ecris un numero de preselection de 1 a %d (chiffres seulement)." % NB_PRESELECTIONS_MAX)
    return n


def commandants_valides(liste):
    return (isinstance(liste, list) and bool(liste) and len(set(liste)) == len(liste)
            and all(isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= NB_COMMANDANTS_MAX for c in liste))


def preselections_valides(liste):
    return (isinstance(liste, list) and len(liste) >= NB_PRESELECTIONS_DEMANDEES
            and all(isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= NB_PRESELECTIONS_MAX for n in liste))


def analyser_numero(texte):
    n = _entier(texte)
    if n is None or n < 1:
        raise ValueError("Ecris un numero entier >= 1 (chiffres seulement).")
    return n


def analyser_chateau(texte):
    morceaux = texte.split()
    x, y = (_entier(m) for m in morceaux) if len(morceaux) == 2 else (None, None)
    if x is None or y is None:
        raise ValueError("Ecris deux nombres entiers : X puis Y, separes par un espace (ex. 659 630).")
    return [x, y]


def analyser_royaumes(texte):
    """'3' ou '1;3' ou '0;2;3' -> liste de royaumes (0 vert, 1 sables, 2 glacier, 3 feu), sans doublon, ordre conserve.
    Seul le ';' est accepte comme separateur ; aucune case vide."""
    parts = [p.strip() for p in texte.strip().split(";")]
    if any(p == "" for p in parts):
        raise ValueError("Ecris des numeros separes par des ';', sans case vide (ex. 3 ou 1;3).")
    res = []
    for p in parts:
        if p not in ("0", "1", "2", "3"):
            raise ValueError("'%s' n'est pas un royaume valide : 0 (vert), 1 (sables), 2 (glacier) ou 3 (feu)." % p)
        if int(p) not in res:
            res.append(int(p))
    return res


def demander(lire, sortie, invite, analyseur, defaut=None):
    """Pose la question jusqu'a obtenir une reponse valide. Entree vide = `defaut` s'il y en a un."""
    while True:
        try:
            brut = lire(invite)
        except EOFError:
            raise ArretModule("Saisie interrompue.")
        if brut is None:
            raise ArretModule("Saisie interrompue.")
        if not brut.strip() and defaut is not None:
            return defaut
        try:
            return analyseur(brut)
        except ValueError as e:
            sortie("    -> %s" % e)


async def demander_async(lire, sortie, invite, analyseur, defaut=None):
    """Comme demander(), pour une fonction de lecture asynchrone (celle du module, pendant que le bot est connecte)."""
    while True:
        try:
            brut = await lire(invite)
        except EOFError:
            raise ArretModule("Saisie interrompue.")
        if brut is None:
            raise ArretModule("Saisie interrompue.")
        if not brut.strip() and defaut is not None:
            return defaut
        try:
            return analyseur(brut)
        except ValueError as e:
            sortie("    -> %s" % e)


REGLAGES_GLOBAUX = ("commandants", "royaumes", "chateaux", "chateaux_manuels", "niveau_min", "vagues_base", "avec_bonus")


def questions_royaume(lire, sortie, kid, ancien=None):
    """Reglages propres a UN royaume (mode, preselections ou cotes, echelles, soldat, cheval, rayon) -> dict."""
    ancien = ancien or {}
    b = {}
    nom = "%d (%s)" % (kid, ROYAUMES[kid])
    sortie("")
    sortie("-" * 66)
    sortie(" REGLAGES DU ROYAUME %s" % nom.upper())
    sortie("-" * 66)

    sortie("")
    sortie("[1] MODE D'ATTAQUE - royaume %s" % nom)
    sortie("    P = preselections (celles enregistrees dans le jeu)")
    sortie("    R = remplissage automatique")
    b["mode"] = demander(lire, sortie, "    > ", analyser_mode)

    if b["mode"] == "presets":
        sortie("")
        sortie("[2] PRESELECTION DE CHAQUE VAGUE - royaume %s" % nom)
        sortie("    Quel numero de preselection pour chaque vague ?")
        sortie("    (si un commandant a plus de %d vagues, la preselection de la vague %d sert aussi pour les suivantes)"
               % (NB_PRESELECTIONS_DEMANDEES, NB_PRESELECTIONS_DEMANDEES))
        b["presets"] = [demander(lire, sortie, "    Vague %d : preselection numero " % (i + 1), analyser_preselection)
                        for i in range(NB_PRESELECTIONS_DEMANDEES)]
    else:
        sortie("")
        sortie("[2] COTES A REMPLIR - royaume %s" % nom)
        sortie("    F   = un flanc (toujours le GAUCHE)")
        sortie("    FF  = les deux flancs (gauche + droit)")
        sortie("    A   = l'avant (front) seulement")
        sortie("    AF  = l'avant + un flanc (gauche)")
        sortie("    AFF = l'avant + les deux flancs")
        b["cotes"] = demander(lire, sortie, "    > ", analyser_cotes)

    sortie("")
    sortie("[3] ECHELLES EN BOIS - royaume %s" % nom)
    if b["mode"] == "presets":
        sortie("    Quand une preselection n'a aucun soldat disponible pour sa vague, la vague est remplie automatiquement")
        sortie("    (voir [4]). Utiliser alors %d echelles en bois sur la vague 1 de chaque cote rempli ? (O/N)" % NB_ECHELLES)
    else:
        sortie("    Utiliser %d echelles en bois sur la premiere vague de chaque cote choisi ? (O/N)" % NB_ECHELLES)
    b["echelles"] = demander(lire, sortie, "    > ", analyser_oui_non)

    sortie("")
    sortie("[4] %s - royaume %s" % ("UNITE DE REMPLACEMENT" if b["mode"] == "presets" else "SOLDATS", nom))
    for u in UNITES_TOURS:
        sortie("    %3d = %s (%s, vitesse %d, attaque %d)" % u)
    anc = soldats_de(ancien) if ancien.get("soldats") or ancien.get("soldat") else None
    anc = anc if anc and all(x in IDS_UNITES_TOURS for x in anc) else None
    if b["mode"] == "presets":
        sortie("    Une seule unite : elle remplace les soldats qui manquent dans une preselection (meme en partie). S'il n'y en a plus")
        sortie("    non plus, le bot complete avec les unites a distance les plus fortes du chateau, puis celles de corps a corps.")
        anc = anc[:1] if anc else None
        b["soldats"] = demander(lire, sortie, "    Soldats (Entree = %s) > " % (anc or [ID_SOLDAT])[0], analyser_soldat_unique, anc or [ID_SOLDAT])
    else:
        sortie("    Numeros separes par des ';', dans l'ordre : la 1re unite d'abord, completee par la suivante s'il en manque.")
        b["soldats"] = demander(lire, sortie, "    Soldats (Entree = %s) > " % ";".join(str(x) for x in (anc or [ID_SOLDAT])),
                                analyser_soldats, anc or [ID_SOLDAT])
    b["soldat"] = b["soldats"][0]

    sortie("")
    sortie("[5] CHEVAL - royaume %s" % nom)
    sortie("    C  = pieces")
    sortie("    F  = plumes")
    sortie("    CF = pieces jusqu'a une certaine distance de la tour, puis plumes")
    b["cheval"] = demander(lire, sortie, "    > ", analyser_cheval)
    if b["cheval"] == "CF":
        sortie("    A partir de quelle distance (en cases, du chateau a la tour) utiliser les plumes ?  Exemple : 20")
        b["distance_plumes"] = demander(lire, sortie, "    Distance > ", analyser_entier_positif)

    sortie("")
    sortie("[6] DISTANCE MAXIMALE - royaume %s" % nom)
    sortie("    Les tours plus loin que cette distance (en cases, du chateau a la tour) ne sont jamais attaquees.")
    sortie("    Un multiple de 10 : 10, 20, 30... Le bot lit la carte couronne par couronne et ne s'elargit que s'il manque de tours libres.")
    ray = ancien.get("rayon")
    ray = ray if isinstance(ray, int) and not isinstance(ray, bool) and ray % 10 == 0 and 10 <= ray <= 500 else None
    b["rayon"] = demander(lire, sortie, "    Distance maximale%s > " % (" (Entree = %s)" % ray if ray else ""),
                          analyser_distance_dizaines, ray)
    if b["cheval"] == "CF" and b["distance_plumes"] > b["rayon"]:
        sortie("    ATTENTION : le bot n'attaque pas au-dela de %s cases, donc les plumes (a partir de %s) ne"
               % (b["rayon"], b["distance_plumes"]))
        sortie("    serviront jamais. Augmente le rayon (--reconfigurer) si tu veux attaquer des tours plus lointaines.")
    return b


def assistant_config(lire=None, sortie=print, ancienne=None):
    """Pose toutes les questions et renvoie la configuration (dict). `ancienne` : valeurs deja enregistrees
    (Entree les conserve pour les royaumes et le rayon).
    Globaux : commandants, royaumes. Par royaume (cfg["reglages"][str(kid)]) : mode, presets/cotes, echelles, soldat,
    cheval, rayon."""
    lire = lire or input
    ancienne = ancienne or {}
    cfg = {}
    sortie("")
    sortie("=" * 66)
    sortie(" MODULE TOURS - CONFIGURATION")
    sortie(" (une seule fois ; tu pourras la refaire avec --reconfigurer)")
    sortie("=" * 66)

    sortie("")
    sortie("[A] COMMANDANTS (communs a tous les royaumes)")
    sortie("    Avec quels commandants veux-tu attaquer ? Ecris leurs numeros dans la liste des")
    sortie("    commandants du jeu (le 1er = 1), separes par des ';'.   Exemple : 1;3;4;6")
    cfg["commandants"] = demander(lire, sortie, "    > ", analyser_commandants)

    sortie("")
    sortie("[B] ROYAUMES A ATTAQUER")
    sortie("    Dans quels royaumes attaquer les tours ?   0 = vert, 1 = sables, 2 = glacier, 3 = feu")
    sortie("    Ecris les numeros separes par des ';'.   Exemples :  3   ou   1;3   ou   0;2;3")
    sortie("    Tes chateaux / avant-postes sont lus tout seuls sur ton compte (rien a taper). Le bot attaque")
    sortie("    toujours la tour libre la PLUS PROCHE, tous royaumes confondus, avec le premier commandant libre.")
    sortie("    Les reglages (mode, soldats, cheval, rayon...) te seront demandes royaume par royaume.")
    anc = ancienne.get("royaumes") or ([ancienne["royaume"]] if ancienne.get("royaume") in ROYAUMES else None)
    cfg["royaumes"] = demander(lire, sortie, "    Royaumes%s > " % (
        " (Entree = %s)" % ";".join(str(k) for k in anc) if anc else ""), analyser_royaumes, anc)

    cfg["reglages"] = {}
    premier = None
    for kid in cfg["royaumes"]:
        anciens = (ancienne.get("reglages") or {}).get(str(kid)) or ancienne      # ancienne config a plat : valeurs par defaut
        if premier is not None:
            sortie("")
            sortie("Royaume %d (%s) : utiliser les MEMES reglages que le royaume %d (%s) ? (O/N)"
                   % (kid, ROYAUMES[kid], premier, ROYAUMES[premier]))
            if demander(lire, sortie, "    > ", analyser_oui_non):
                cfg["reglages"][str(kid)] = copy.deepcopy(cfg["reglages"][str(premier)])
                continue
        cfg["reglages"][str(kid)] = questions_royaume(lire, sortie, kid, anciens)
        premier = kid if premier is None else premier

    # choix de chateau deja faits (identifiants) et coordonnees de secours : conserves, ils sont reverifies au lancement
    cfg["chateaux"] = dict(ancienne.get("chateaux") or {})
    if ancienne.get("chateaux_manuels"):
        cfg["chateaux_manuels"] = dict(ancienne["chateaux_manuels"])

    # reglages qui ne sont pas demandes (modifiables a la main dans config.json)
    cfg["niveau_min"] = ancienne.get("niveau_min", 0)
    cfg["vagues_base"] = ancienne.get("vagues_base", NB_VAGUES_BASE)
    cfg["avec_bonus"] = ancienne.get("avec_bonus", False)
    return cfg


def cfg_royaume(cfg, kid):
    """Configuration a utiliser pour un royaume : les reglages communs + ceux de ce royaume."""
    r = {k: v for k, v in cfg.items() if k in REGLAGES_GLOBAUX}
    r.update(cfg["reglages"][str(kid)])
    return r


def lignes_reglages(b):
    l = []
    if b["mode"] == "presets":
        p = b["presets"]
        l.append("mode : PRESELECTIONS  (vague 1 : %d, vague 2 : %d, vague 3 : %d, vague 4 et suivantes : %d)" % tuple(p[:4]))
        l.append("soldats manquants d'une preselection : %s, puis unites a distance les plus fortes, puis corps a corps ; echelles : %s"
                 % (nom_unite(soldats_de(b)[0]), "oui (%d sur la vague 1 d'un cote sans soldat)" % NB_ECHELLES if b.get("echelles") else "non"))
    else:
        cotes = b["cotes"]
        l.append("mode : REMPLISSAGE AUTOMATIQUE, cotes %s (%s)" % (cotes, " + ".join(combat.NOMS_COTES[c] for c in combat.COTES[cotes])))
        l.append("echelles en bois : %s" % ("oui (%d sur la vague 1 de chaque cote)" % NB_ECHELLES if b.get("echelles") else "non"))
        l.append("soldats : %s" % ", puis ".join(nom_unite(x) for x in soldats_de(b)))
    if b["cheval"] == "CF":
        l.append("cheval : pieces puis plumes a partir de %s cases" % b["distance_plumes"])
    else:
        l.append("cheval : %s" % ("pieces" if b["cheval"] == "C" else "plumes"))
    l.append("rayon de recherche : %d cases autour du chateau" % b["rayon"])
    return l


def resume_config(cfg):
    """Lignes d'affichage de la configuration."""
    l = ["commandants (communs a tous les royaumes) : " + ";".join(str(c) for c in cfg["commandants"])]
    l.append("royaumes : %s (chateaux lus sur le compte ; tour la plus proche attaquee en premier)"
             % ", ".join("%d (%s)" % (k, ROYAUMES[k]) for k in cfg["royaumes"]))
    for kid in cfg["royaumes"]:
        l.append("ROYAUME %d (%s) :" % (kid, ROYAUMES[kid]))
        l += ["    " + x for x in lignes_reglages(cfg["reglages"][str(kid)])]
    return l


def lire_config(chemin=None):
    try:
        with open(chemin or CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def memoriser_config(cfg_tours, chemin=None, rubrique="tours"):
    """Ajoute la rubrique ("tours" ou "forteresses") a config.json sans toucher au reste (le pseudo, jamais de mot de passe)."""
    chemin = chemin or CONFIG
    tout = lire_config(chemin)
    tout[rubrique] = cfg_tours
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(tout, f, indent=2, ensure_ascii=False)


def reglages_valides(b):
    try:
        if b["mode"] not in ("auto", "presets") or "echelles" not in b:
            return False
        if b["mode"] == "auto" and b["cotes"] not in combat.COTES:
            return False
        if b["mode"] == "presets" and not preselections_valides(b["presets"]):
            return False
        if b["cheval"] not in ("C", "F", "CF") or (b["cheval"] == "CF" and not b.get("distance_plumes")):
            return False
        if "soldats" in b and (not isinstance(b["soldats"], list) or not 1 <= len(b["soldats"]) <= NB_SOLDATS_MAX
                               or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in b["soldats"])):
            return False
        return b["rayon"] >= 1
    except (KeyError, TypeError):
        return False


def config_valide(cfg):
    """Vrai si la configuration enregistree est complete (sinon on relance l'assistant)."""
    try:
        if not commandants_valides(cfg["commandants"]) or not cfg["royaumes"] or not all(k in ROYAUMES for k in cfg["royaumes"]):
            return False
        if len(set(cfg["royaumes"])) != len(cfg["royaumes"]):
            return False
        return all(reglages_valides(cfg["reglages"].get(str(k))) for k in cfg["royaumes"])
    except (KeyError, TypeError, AttributeError):
        return False


def preselection_pour_vague(cfg, i):
    """Numero de preselection de la vague i (0 = vague 1). Au-dela de la 4e vague : celle de la 4e."""
    p = cfg["presets"]
    return p[min(i, NB_PRESELECTIONS_DEMANDEES - 1)]


# ============================================================================== cheval (pieces / plumes)
def option_cheval(cfg, distance):
    """'C' ou 'F' pour une tour a cette distance (CF : pieces en dessous du seuil, plumes a partir du seuil)."""
    if cfg["cheval"] == "CF":
        return "F" if distance >= cfg["distance_plumes"] else "C"
    return cfg["cheval"]


def options_cheval_necessaires(cfg):
    """Options de cheval ('C', 'F') utilisees par au moins un royaume choisi."""
    res = []
    for kid in cfg["royaumes"]:
        c = cfg["reglages"][str(kid)]["cheval"]
        for o in (["C", "F"] if c == "CF" else [c]):
            if o not in res:
                res.append(o)
    return res


# Reglages du cheval, les memes pour tous les joueurs (lus sur de vraies attaques : C = cheval paye en pieces, F = en plumes).
# Plus besoin de lancer une attaque a la main la premiere fois. captures/chevaux.json (--apprendre-cheval C|F) les remplace
# s'il existe.
CHEVAUX_DEFAUT = {"C": {"HBW": 1007, "PTT": 0}, "F": {"HBW": -1, "PTT": 1}}


def charger_chevaux(chemin=None):
    """{"C": {"HBW":..,"PTT":..}, "F": {...}} : reglages connus d'avance (CHEVAUX_DEFAUT), remplaces par ceux appris pendant une
    attaque faite a la main (captures/chevaux.json) s'il y en a."""
    res = copy.deepcopy(CHEVAUX_DEFAUT)
    try:
        with open(chemin or FICHIER_CHEVAUX, encoding="utf-8") as f:
            lu = json.load(f)
    except (OSError, ValueError):
        return res
    for opt, champs in (lu.items() if isinstance(lu, dict) else ()):
        if opt in res and isinstance(champs, dict) and all(isinstance(champs.get(k), int) for k in CHAMPS_CHEVAL):
            res[opt] = {k: champs[k] for k in CHAMPS_CHEVAL}
    return res


def sauver_cheval(option, champs, chemin=None):
    chemin = chemin or FICHIER_CHEVAUX
    tout = charger_chevaux(chemin)
    tout[option] = {k: champs[k] for k in CHAMPS_CHEVAL if k in champs}
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(tout, f, indent=2)


def champs_non_armee(cra):
    return {k: v for k, v in cra.items() if k not in ("A", "SX", "SY", "TX", "TY", "KID", "LID")}


async def apprendre_cheval(jeu, option, sortie=print, lire=None, delai=600):
    """Enregistre les champs du cheval (HBW, PTT) a partir d'UNE attaque que le joueur lance dans le jeu
    avec l'option de cheval demandee ('C' pieces, 'F' plumes). Rien n'est envoye par le bot."""
    nom = "PIECES" if option == "C" else "PLUMES"
    sortie("")
    sortie("APPRENTISSAGE DU CHEVAL : %s" % nom)
    sortie("  Dans la fenetre Chrome du bot, lance UNE attaque normale sur n'importe quelle tour,")
    sortie("  en choisissant le cheval paye en %s (comme tu le ferais d'habitude)." % nom.lower())
    sortie("  J'attends jusqu'a %d minutes... (je n'envoie rien, je note seulement les reglages du cheval)" % (delai // 60))
    await jeu.vider()
    msg = await jeu.attendre_cra_joueur(delai)
    if not msg:
        sortie("  Aucune attaque detectee : rien enregistre.")
        return None
    m = re.match(r"^%xt%([^%]+)%cra%1%(.*)%$", msg, re.S)
    cra = T.json_ou_none(m.group(2)) if m else None
    if not cra:
        sortie("  Attaque detectee mais illisible : %s" % msg[:200])
        return None
    champs = champs_non_armee(cra)
    sauver_cheval(option, champs)
    sortie("  ENREGISTRE pour %s : %s  (fichier %s)" % (nom, {k: champs.get(k) for k in CHAMPS_CHEVAL}, FICHIER_CHEVAUX))
    modele = T.construire_cra(0, 0, 0, 0, 0, 0, 0, 0, 0)
    autres = {k: v for k, v in champs.items() if k not in CHAMPS_CHEVAL and modele.get(k) != v}
    if autres:
        sortie("  Attention : d'autres champs different de ceux que le bot envoie : %s" % json.dumps(autres, separators=(",", ":")))
        sortie("  (envoie-moi ce message, je verifierai s'ils dependent du cheval)")
    appris = charger_chevaux()
    if "C" in appris and "F" in appris:
        if appris["C"] == appris["F"]:
            sortie("  Attention : pieces et plumes ont les MEMES reglages enregistres. Verifie que l'attaque faite")
            sortie("  a la main utilisait bien deux options de cheval differentes.")
        else:
            sortie("  Pieces : %s | Plumes : %s" % (appris["C"], appris["F"]))
    rep = await jeu.attendre("cra", 15)
    if rep:
        d = T.json_ou_none(rep[2]) or {}
        mv = (d.get("AAM", {}) or {}).get("M", {}) or {}
        sortie("  Reponse du serveur : code %s, cheval retenu %s, trajet %s s" % (rep[1], mv.get("HBW"), mv.get("TT")))
    return champs


async def assurer_chevaux(jeu, cfg, sortie, lire, simulation=False):
    """Verifie que les reglages de cheval necessaires sont connus ; sinon les apprend maintenant (sauf simulation)."""
    for opt in options_cheval_necessaires(cfg):
        if opt in charger_chevaux():
            continue
        nom = "pieces" if opt == "C" else "plumes"
        if simulation:
            sortie("  (simulation) reglage du cheval '%s' pas encore appris : il sera demande avant un vrai envoi." % nom)
            continue
        sortie("")
        sortie("Je ne connais pas encore les reglages du cheval en %s (une seule fois)." % nom)
        while True:
            rep = (await lire("  Entree = je les apprends maintenant (tu lances une attaque a la main), S = stop > ")).strip().upper()
            if rep in ("", "S"):
                break
            sortie("    -> Reponds Entree (apprendre maintenant) ou S (stop).")
        if rep == "S":
            raise ArretModule("Reglages du cheval '%s' manquants. Relance avec : outils\\lancer_bot_tours.bat --apprendre-cheval %s"
                              % (nom, opt))
        if await apprendre_cheval(jeu, opt, sortie, lire) is None:
            raise ArretModule("Reglages du cheval '%s' non appris : arret." % nom)


def champs_pour_cheval(option, appris):
    """Champs a copier dans le paquet cra pour cette option ('C'/'F'), ou None si pas encore appris."""
    return appris.get(option)


# ============================================================================== chateaux du compte
def chateaux_depuis_jaa(payload):
    """Liste des chateaux / avant-postes du compte lue dans la reponse jaa a `jca` : gca.O.AP = [[royaume, id, x, y, type], ...].
    Renvoie [{"kid","id","x","y","type"}, ...] (royaumes 0 a 3 seulement), ou None si la reponse n'est pas celle de NOTRE
    compte (la reponse a ses propres donnees : le champ gui) ou si elle est illisible."""
    d = T.json_ou_none(payload)
    if not isinstance(d, dict) or "gui" not in d:
        return None
    try:
        ap = d["gca"]["O"]["AP"]
    except (KeyError, TypeError):
        return None
    res, vus = [], set()
    for e in ap if isinstance(ap, list) else []:
        if (isinstance(e, list) and len(e) >= 5 and all(isinstance(v, int) and not isinstance(v, bool) for v in e[:5])
                and e[0] in ROYAUMES and e[1] not in vus):
            vus.add(e[1])
            res.append({"kid": e[0], "id": e[1], "x": e[2], "y": e[3], "type": e[4]})
    res.sort(key=lambda c: (c["kid"], c["id"]))
    return res or None


async def lire_chateaux(jeu, sortie=print, essais=2):
    """Demande au serveur la liste des chateaux du compte (requete jca, la meme que celle du jeu a la connexion :
    CID -1 = le chateau courant). Renvoie la liste (voir chateaux_depuis_jaa) ou None si le serveur ne la donne pas."""
    for _ in range(essais):
        try:
            await jeu.vider()
            await jeu.envoyer("jca", {"CID": -1, "KID": 0})
            rep = await jeu.attendre("jaa", 15)
        except T.ErreurCDP as e:
            sortie("  (liste des chateaux : requete impossible : %s)" % e)
            return None
        res = chateaux_depuis_jaa(rep[2]) if rep and rep[1] == 0 else None
        if res:
            return res
    return None


def nom_chateau(c):
    return "%s (%d,%d)" % (TYPES_CHATEAU.get(c["type"], "type %s" % c["type"]), c["x"], c["y"])


async def determiner_sources(jeu, cfg, sortie, lire, memoriser=True, rubrique="tours"):
    """Chateau de depart de chaque royaume choisi : {kid: {"x","y","id"(si lu sur le compte),"type"}}.
    - un seul chateau dans le royaume : pris automatiquement ;
    - plusieurs : le joueur choisit UNE fois (choix memorise dans config.json, rubrique chateaux, par identifiant) ;
    - liste illisible : coordonnees demandees une fois par royaume (secours, memorisees)."""
    chateaux = await lire_chateaux(jeu, sortie)
    sources, change = {}, False
    cfg.setdefault("chateaux", {})
    if chateaux is None:
        sortie("")
        sortie("Je n'ai pas pu lire la liste de tes chateaux sur le compte : j'ai besoin de leurs coordonnees")
        sortie("(une seule fois, elles sont ensuite memorisees).")
    for kid in cfg["royaumes"]:
        nom = "%s (%s)" % (kid, ROYAUMES[kid])
        if chateaux is None:
            man = cfg.setdefault("chateaux_manuels", {})
            if str(kid) not in man:
                man[str(kid)] = await demander_async(
                    lire, sortie, "    Royaume %s : coordonnees de ton chateau X Y (ex. 659 630) > " % nom, analyser_chateau)
                change = True
            sources[kid] = {"x": man[str(kid)][0], "y": man[str(kid)][1]}
            continue
        dispo = [c for c in chateaux if c["kid"] == kid]
        if not dispo:
            raise ArretModule("Tu n'as aucun chateau ni avant-poste dans le royaume %s sur ce compte. "
                              "Relance avec --reconfigurer et retire ce royaume." % nom)
        choisi = next((c for c in dispo if str(c["id"]) == str(cfg["chateaux"].get(str(kid)))), None)
        if choisi is None and len(dispo) == 1:
            choisi = dispo[0]
        if choisi is None:
            sortie("")
            sortie("Royaume %s : tu as %d chateaux. Duquel partent les attaques ? (choix memorise)" % (nom, len(dispo)))
            for i, c in enumerate(dispo, 1):
                sortie("    %d) %s" % (i, nom_chateau(c)))

            def _num(texte, n=len(dispo)):
                v = analyser_numero(texte)
                if v > n:
                    raise ValueError("Ecris un numero entre 1 et %d." % n)
                return v
            choisi = dispo[await demander_async(lire, sortie, "    > ", _num) - 1]
            cfg["chateaux"][str(kid)] = choisi["id"]
            change = True
        sources[kid] = dict(choisi)
    if change and memoriser:       # on ne memorise que les choix de chateau (pas les options de la ligne de commande)
        stocke = dict(lire_config().get(rubrique) or cfg)
        stocke["chateaux"] = cfg["chateaux"]
        if "chateaux_manuels" in cfg:
            stocke["chateaux_manuels"] = cfg["chateaux_manuels"]
        memoriser_config(stocke, rubrique=rubrique)
    return sources


def lignes_sources(sources):
    lignes = []
    for kid, s in sorted(sources.items()):
        lieu = nom_chateau(s) if "id" in s else "(%d,%d) (coordonnees saisies)" % (s["x"], s["y"])
        lignes.append("royaume %d (%s) : %s" % (kid, ROYAUMES[kid], lieu))
    return lignes


# ============================================================================== preselections du compte
async def lire_presets(jeu, sortie=print, essais=2):
    """Preselections enregistrees dans le jeu (commande gas, comme le jeu a l'ouverture de l'ecran d'attaque) :
    {index: {"nom": ..., "groupes": [...]}} (index 0 = preselection numero 1) ; None si le serveur ne repond pas."""
    for _ in range(essais):
        try:
            await jeu.vider()
            await jeu.envoyer("gas", {})
            rep = await jeu.attendre("gas", 15)
        except T.ErreurCDP as e:
            sortie("  (preselections : requete impossible : %s)" % e)
            return None
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if isinstance(d, dict) and isinstance(d.get("S"), list):
            res = {}
            for e in d["S"]:
                try:
                    res[int(e["S"])] = {"nom": str(e.get("SN") or ""), "groupes": combat.decoder_preselection(e["A"])}
                except (KeyError, TypeError, ValueError) as err:
                    sortie("  (preselection %s ignoree : %s)" % (e.get("S") if isinstance(e, dict) else "?", err))
            return res
    return None


def royaumes_en_presets(cfg):
    """Royaumes choisis dont le mode est 'presets' (P)."""
    return [k for k in cfg["royaumes"] if cfg["reglages"][str(k)]["mode"] == "presets"]


def verifier_presets(cfg, presets):
    """Arret clair si une preselection choisie (pour un royaume quelconque) n'existe pas ou n'est pas debloquee sur le compte."""
    if presets is None:
        raise ArretModule("Je n'ai pas pu lire tes preselections (le serveur n'a pas repondu a gas). "
                          "Relance, ou choisis R (remplissage automatique) avec --reconfigurer.")
    dispo = ", ".join("%d (%s)" % (i + 1, presets[i]["nom"]) for i in sorted(presets)) or "aucune"
    for kid in royaumes_en_presets(cfg):
        for i in range(NB_PRESELECTIONS_DEMANDEES):
            num = cfg["reglages"][str(kid)]["presets"][i]
            if num - 1 not in presets:
                raise ArretModule("Royaume %d (%s) : la preselection numero %d (vague %d) n'existe pas ou n'est pas debloquee sur ce "
                                  "compte. Preselections disponibles : %s. Relance avec --reconfigurer."
                                  % (kid, ROYAUMES[kid], num, i + 1, dispo))
    return dispo


def lignes_presets(cfg, presets):
    res = []
    for kid in royaumes_en_presets(cfg):
        p = cfg["reglages"][str(kid)]["presets"]
        res += ["royaume %d (%s), vague %d : preselection %d (%s)" % (kid, ROYAUMES[kid], i + 1, p[i], presets[p[i] - 1]["nom"])
                for i in range(NB_PRESELECTIONS_DEMANDEES)]
    return res


# ============================================================================== construction de l'attaque
def positions_commandants(adi):
    """Commandants dans l'ordre d'affichage du jeu (champ VIS, puis identifiant) : le 1er est le numero 1."""
    return sorted(adi.get("gli", {}).get("C", []), key=lambda c: (c.get("VIS", 10 ** 6), c.get("ID", 0)))


def construire_attaque(cfg, adi, pos, tour, kid, chateau, effets, appris, cheval_inconnu_ok=False, presets=None, fiches_outils=None,
                       zone=combat.ZONE_TOUR, unites=None):
    """Prepare le paquet cra du commandant numero `pos` sur la tour `tour` (entree de carte [2,x,y,-1,niv,minuteur,kid]).
    `cfg` : la configuration complete (les reglages du royaume `kid` sont pris dans cfg["reglages"]) ou deja un bloc fusionne
    (voir cfg_royaume). Retourne (cra, infos) ; leve ArretModule / ValueError si impossible."""
    if "reglages" in cfg:
        cfg = cfg_royaume(cfg, kid)
    cmds = positions_commandants(adi)
    if not 1 <= pos <= len(cmds):
        raise ValueError("commandant numero %d absent de la liste (%d commandants)" % (pos, len(cmds)))
    cmd = cmds[pos - 1]
    tx, ty = tour[1], tour[2]
    distance = math.hypot(tx - chateau[0], ty - chateau[1])
    niv_brut = combat.niveau_cible(adi)
    if niv_brut is None:
        niv_brut = tour[4]
    ai = (adi.get("gaa") or {}).get("AI")
    type_cible = ai[0] if isinstance(ai, list) and ai else tour[0]
    victoires = niv_brut if kid == 0 and type_cible == 2 else None      # royaume vert : le champ = nombre de victoires
    niv = combat.niveau_effectif(combat.niveau_reel(kid, type_cible, niv_brut))
    extra = combat.vagues_supplementaires(cmd, effets, zone) if effets else 0
    nb_vagues = int(cfg.get("vagues_base", NB_VAGUES_BASE)) + extra
    # vagues a remplir (reglage "vagues_remplies", 1 a 4 ; absent ou 4 = toutes) : les suivantes partent vides
    vr = cfg.get("vagues_remplies")
    nb_remplies = min(nb_vagues, vr) if isinstance(vr, int) and not isinstance(vr, bool) and 1 <= vr < NB_VAGUES_BASE else nb_vagues
    vide = lambda: {c: {"T": [], "U": []} for c in ("L", "R", "M")}     # noqa: E731
    soldat_affiche = cfg.get("soldat", ID_SOLDAT)
    stock = combat.inventaire_depuis_adi(adi.get("gui", {}).get("I", []))
    bonus_f = bonus_a = 0
    if cfg.get("avec_bonus") and effets:
        bonus_f = combat.bonus_commandant(cmd, effets, zone, combat.EFFET_FLANC)
        bonus_a = combat.bonus_commandant(cmd, effets, zone, combat.EFFET_FRONT)
    if cfg.get("mode") == "presets":
        vagues, manques = [], []
        outil_ok = lambda wid: combat.outil_utilisable(fiches_outils, wid, kid, zone)   # noqa: E731
        secours = []
        for i in range(nb_vagues):
            if i >= nb_remplies:
                vagues.append(vide())
                continue
            num = preselection_pour_vague(cfg, i)
            fiche = (presets or {}).get(num - 1)
            if fiche is None:
                raise ArretModule("Preselection numero %d introuvable (ou pas debloquee) : arret du module." % num)
            vague, stock_apres, avert = combat.appliquer_preselection(fiche["groupes"], stock, niv, outil_ok)
            # soldats manquants (meme en partie) : unite de remplacement choisie, puis distance la plus forte, puis corps a corps
            ordre = combat.ordre_complement(stock_apres, unites, kid, cfg.get("soldat", ID_SOLDAT))
            vague, stock_apres, ajouts, reste = combat.completer_preselection(
                vague, fiche["groupes"], stock_apres, niv, ordre, NB_ECHELLES if cfg.get("echelles") and i == 0 else 0, ID_ECHELLE)
            if ajouts:
                secours.append("vague %d : preselection %d completee avec %s"
                               % (i + 1, num, ", ".join("%d x %d (%s)" % (q, w, combat.NOMS_COTES[c]) for c, w, q in ajouts)))
            outils_preset = {w for (c, g), entrees in zip(combat.GROUPES_PRESELECTION, fiche["groupes"]) if g == "T" for w, _ in entrees}
            avert = [a for a in avert if any(" x %d voulus" % w in a or "outil %d " % w in a for w in outils_preset)] + reste
            if not any(vague[c]["U"] for c in "LRM"):        # plus aucun soldat nulle part : vague vide, sans outils non plus
                stock_apres, avert = stock, []
                vague = {c: {"T": [], "U": []} for c in ("L", "R", "M")}
            stock = stock_apres
            vagues.append(vague)
            manques += ["vague %d (preselection %d) : %s" % (i + 1, num, a) for a in avert]
        manques = secours + manques
        while len(vagues) > 1 and not any(vagues[-1][c]["U"] for c in "LRM"):
            vagues.pop()                      # vagues finales sans aucun soldat (stock epuise) : inutiles
        nb_vagues = len(vagues)
        manques += ["vague %d : aucun soldat disponible (ni dans la preselection ni en remplacement)" % (i + 1)
                    for i, vg in enumerate(vagues) if not any(vg[c]["U"] for c in "LRM")]
        if not any(vg[c]["U"] for vg in vagues for c in "LRM"):
            raise PlusDeSoldats("Plus aucun soldat disponible (preselections ni remplacement) dans le royaume %s." % kid)
    elif cfg.get("soldats_ordre") or len(soldats_de(cfg)) > 1:
        # plusieurs unites, la 1re d'abord, completee par les suivantes : forteresses (la plus rapide d'abord) ou tours (ordre choisi)
        vagues, _, manques, utilisees = combat.remplir_cotes_complets(
            stock, cfg.get("soldats_ordre") or soldats_de(cfg), combat.COTES[cfg["cotes"]], niv, nb_remplies, bonus_f, bonus_a,
            NB_ECHELLES if cfg.get("echelles") else 0, ID_ECHELLE)
        vagues += [vide() for _ in range(nb_vagues - nb_remplies)]
        if not any(vg[c]["U"] for vg in vagues for c in "LRM"):
            raise PlusDeSoldats("Plus aucun soldat offensif en stock dans le royaume %s." % kid)
        soldat_affiche = "+".join(str(u) for u in utilisees)
    else:
        vagues, _, manques = combat.remplir_cotes(
            stock, cfg.get("soldat", ID_SOLDAT), combat.COTES[cfg["cotes"]], niv, nb_remplies,
            NB_ECHELLES if cfg.get("echelles") else 0, ID_ECHELLE, bonus_f, bonus_a)
        vagues += [vide() for _ in range(nb_vagues - nb_remplies)]
        if not any(vg[c]["U"] for vg in vagues for c in "LRM"):
            raise PlusDeSoldats("Plus aucun soldat de l'unite %d en stock dans le royaume %s." % (cfg.get("soldat", ID_SOLDAT), kid))
    opt = option_cheval(cfg, distance)
    champs = champs_pour_cheval(opt, appris)
    cheval_inconnu = champs is None
    if cheval_inconnu:
        if not cheval_inconnu_ok:
            raise ArretModule("Reglages du cheval '%s' inconnus. Lance : outils\\lancer_bot_tours.bat --apprendre-cheval %s" % (opt, opt))
        champs = {}
    cra = T.construire_cra(chateau[0], chateau[1], tx, ty, kid, cmd["ID"], 0, 0, 0)
    cra["A"] = [combat.vers_paquet(v) for v in vagues]
    cra.update({k: champs[k] for k in CHAMPS_CHEVAL if k in champs})
    infos = {"pos": pos, "kid": kid, "lid": cmd["ID"], "nom": cmd.get("N", ""), "niveau": niv, "niveau_brut": niv_brut, "victoires": victoires,
             "distance": distance, "cheval": opt, "cheval_inconnu": cheval_inconnu, "soldat": soldat_affiche,
             "vagues_remplies": nb_remplies,
             "mode": "P (preselections)" if cfg.get("mode") == "presets" else "R %s" % cfg.get("cotes"), "vagues": nb_vagues, "vagues_en_plus": extra, "manques": manques,
             "plafond_flanc": combat.soldats_flanc(niv, bonus_f), "plafond_front": combat.soldats_centre(niv, bonus_a)}
    return cra, infos


def texte_niveau(info):
    """'8 (11 victoires, encore 3 pour le niveau 9)' pour une tour du royaume vert ; sinon le niveau seul."""
    v = info.get("victoires")
    if v is None:
        return "%s" % info["niveau"]
    k = combat.victoires_avant_niveau_suivant(v)
    return "%s (%d victoire(s), encore %d pour le niveau %d)" % (info["niveau"], v, k, info["niveau"] + 1)


def texte_vagues(info):
    """'4 vague(s)' ou '2 vague(s) remplie(s) sur 4'."""
    r = info.get("vagues_remplies", info["vagues"])
    return "%d vague(s)" % info["vagues"] if r >= info["vagues"] else "%d vague(s) remplie(s) sur %d" % (r, info["vagues"])


def analyser_duree(texte):
    """Duree de fonctionnement demandee : 90m, 45min, 3h, 2h30 -> secondes (de 1 minute a 72 h)."""
    t = texte.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})h(?:(\d{2})(?:m|min)?)?", t) or re.fullmatch(r"()(\d{1,4})(?:m|min)", t)
    if not m:
        raise ValueError("Ecris une duree comme 90m, 3h ou 2h30.")
    heures, minutes = int(m.group(1) or 0), int(m.group(2) or 0)
    if (m.group(1) and minutes > 59) or not 1 <= heures * 60 + minutes <= 72 * 60:
        raise ValueError("Duree entre 1 minute et 72 h (exemples : 90m, 3h, 2h30).")
    return (heures * 60 + minutes) * 60


def duree(secondes):
    s = int(max(0, secondes))
    if s >= 3600:
        return "%d h %02d min" % (s // 3600, (s % 3600) // 60)
    if s >= 60:
        return "%d min %02d s" % (s // 60, s % 60)
    return "%d s" % s


# ============================================================================== le module
class Partage:
    """Etat commun a tous les modules qui tournent dans la MEME session (lanceur commun, src/bot.py).
    Les modules ne se connaissent pas : ils passent seulement par cet objet.
      libre_a        : numero de commandant -> instant ou il revient (un commandant pris par un module est absent pour les autres)
      dernier_envoi  : instant du dernier paquet d'attaque, tous modules confondus (le serveur veut 4 s entre deux attaques)
      reservations   : nom du module -> fonction () -> commandants que CE module garde (les autres modules ne les prennent pas)
      commandants    : nom du module -> liste de ses commandants (pour savoir lesquels sont communs)
      routeur        : None, ou fonction(payload) qui donne chaque message de retour (cat) a tous les modules
      priorites      : nom du module -> priorite (plus petit = plus prioritaire ; forteresses avant tours)
      urgences       : nom du module -> fonction () -> True si ce module a une attaque a faire tout de suite"""
    def __init__(self):
        self.libre_a = {}
        self.dernier_envoi = float("-inf")
        self.reservations = {}
        self.commandants = {}
        self.routeur = None
        self.priorites = {}
        self.urgences = {}
        self.signe_de_vie = lambda: None   # lanceur : met a jour l'etat du bot pendant un long passage (l'interface le voit vivant)

    def urgence_prioritaire(self, nom):
        """True si un module PLUS prioritaire que `nom` a une attaque a faire tout de suite (nom doit alors s'interrompre)."""
        p = self.priorites.get(nom)
        if p is None:
            return False
        return any(self.priorites.get(autre, p) < p and f() for autre, f in list(self.urgences.items()) if autre != nom)

    def gardes_par_les_autres(self, nom):
        """Commandants gardes par les modules autres que `nom`."""
        res = set()
        for autre, f in list(self.reservations.items()):
            if autre != nom:
                res |= set(f())
        return res


class ModuleTours:
    CMD_INFO = "adi"               # demande d'infos avant l'attaque : adi (tour) ; abi (forteresse)
    NOM_CIBLE = "tour"             # mot affiche devant les coordonnees de la cible
    NOM_MODULE = "TOURS"           # nom du module dans le message de demarrage
    ATTENTE_APRES_ATTAQUE = 90     # secondes, apres le trajet aller, avant de relire la cible sur la carte

    def __init__(self, jeu, cfg, sources, effets=None, appris=None, sortie=print, lire=None, vivante=None,
                 simulation=False, max_attaques=None, confirmer=True, horloge=time.time, dormir=asyncio.sleep,
                 journal=JOURNAL_DEFAUT, presets=None, fiches_outils=None, duree_max=None, libre_a=None, partage=None):
        self.jeu, self.cfg, self.effets = jeu, cfg, effets
        self.duree_max, self.fin = duree_max, None          # secondes de fonctionnement demandees (None = jusqu'a Ctrl+C)
        self.presets, self.fiches_outils = presets, fiches_outils   # mode P : preselections du compte, fiches des outils
        self.sources = sources     # {royaume: {"x","y",...}} : chateau de depart de chaque royaume choisi
        self.appris = appris if appris is not None else charger_chevaux()
        self.sortie, self.lire, self.vivante = sortie, lire, vivante
        self.simulation, self.max_attaques = simulation, max_attaques
        self.a_confirmer = confirmer and not simulation
        self.horloge, self.dormir = horloge, dormir
        self.partage = partage if partage is not None else Partage()   # etat commun aux modules de la session
        self.journal = FICHIER_JOURNAL if journal is JOURNAL_DEFAUT else journal
        self.libre_a = libre_a if libre_a is not None else self.partage.libre_a   # numero de commandant -> instant ou il revient
        self.zones_ratees = 0      # relectures de la carte ratees de suite (avant une attaque)
        self.unites = None         # fiches des unites (items.json) : complement des preselections par la plus forte attaque
        self.restriction = None    # None, ou fonction -> commandants que ce module n'a pas le droit de prendre (en plus des reservations du partage)
        self.engagees = {}      # (royaume, x, y) -> instant jusqu'auquel on ne retouche pas la tour
        self.absents = set()       # numeros absents de la liste du jeu
        self.en_route = {}         # (royaume, x, y) de la tour -> (numero de commandant, instant d'arrivee prevu)
        self.sans_stock = {}       # royaume -> instant jusqu'auquel on le laisse de cote (plus de soldat dans son chateau)
        self.refus_cibles = {}     # (royaume, x, y) -> nombre de fois ou le serveur a ignore une attaque sur cette cible
        self.nb_attaques = 0
        self.echecs = 0
        self._dernier_msg = ("", 0)
        self.cache = {}            # royaume -> {zone: (instant de lecture, {(x, y): entree})} : zones deja lues (les tours ne bougent pas)
        self.cran = {}             # royaume -> nombre de couronnes deja lues
        self._crans = {}           # royaume -> [[zones de la couronne 0], [couronne 1], ...]

    @property
    def dernier_envoi(self):
        """Instant du dernier paquet cra envoye (par n'importe quel module de la session)."""
        return self.partage.dernier_envoi

    @dernier_envoi.setter
    def dernier_envoi(self, t):
        self.partage.dernier_envoi = t

    # ---------------------------------------------------------------- affichage / journal
    def _noter(self, texte):
        self.sortie(texte)
        if self.journal:
            try:
                os.makedirs(os.path.dirname(self.journal), exist_ok=True)
                with open(self.journal, "a", encoding="utf-8") as f:
                    f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.horloge())), texte.strip()))
            except OSError:
                pass

    def _attente_msg(self, cle, texte):
        """Affiche un message d'attente seulement quand la raison change (ou toutes les 5 minutes)."""
        t = self.horloge()
        if cle != self._dernier_msg[0] or t - self._dernier_msg[1] > 300:
            self.sortie(texte)
            self._dernier_msg = (cle, t)

    # ---------------------------------------------------------------- retour des commandants
    async def _vider(self):
        """Vide les messages recus, apres avoir lu ceux qui annoncent le retour d'un commandant (cat)."""
        self.partage.signe_de_vie()                 # chaque demande au jeu prouve que le bot travaille
        await self._absorber()
        await self.jeu.vider()

    async def _absorber(self):
        rel = getattr(self.jeu, "relever", None)
        if rel is not None:
            await rel()
        restant = []
        for m in list(self.jeu.recus):
            if m[0] == "cat":
                (self.partage.routeur or self._retour)(m[2])      # lanceur commun : chaque module reconnait ses armees
            else:
                restant.append(m)
        self.jeu.recus[:] = restant

    def _retour(self, payload):
        """Message cat : l'armee est arrivee sur la tour ; M.TT = duree REELLE du retour (D = 1), souvent plus courte
        que l'aller. Le commandant est libre a (arrivee prevue + TT du retour)."""
        d = T.json_ou_none(payload) or {}
        m = (d.get("A") or {}).get("M") or {}
        sa = m.get("SA") or []
        tt = m.get("TT")
        if m.get("D") != 1 or len(sa) < 3 or not isinstance(tt, (int, float)):
            return
        kid = m.get("KID", sa[6] if len(sa) == 7 else sa[7] if len(sa) >= 8 else None)   # royaume du combat (2 royaumes : memes x, y)
        cle = next((c for c in self.en_route if c[1:] == (sa[1], sa[2]) and kid in (None, c[0])), None)
        if cle is None:
            return
        pos, arrivee = self.en_route.pop(cle)
        self.libre_a[pos] = arrivee + tt + 1
        self._noter("   commandant %d : arrive sur la tour %s (%d,%d), retour en %d s -> libre dans %s"
                    % (pos, ROYAUMES.get(cle[0], "?"), sa[1], sa[2], tt, duree(self.libre_a[pos] - self.horloge())))

    # ---------------------------------------------------------------- carte
    def _reglages(self, kid):
        """Reglages propres au royaume `kid` (mode, soldat, cheval, rayon...)."""
        return self.cfg["reglages"][str(kid)]

    def _royaumes_actifs(self):
        """Royaumes choisis qui ont encore des soldats (ceux en pause 'sans stock' sont laisses de cote)."""
        t = self.horloge()
        return [k for k in self.cfg["royaumes"] if self.sans_stock.get(k, 0) <= t]

    def _couronnes(self, kid):
        """Zones de 13x13 autour du chateau du royaume, rangees par couronnes de 13 cases (0 = autour du chateau, 1 = de 13 a 26...)."""
        if kid not in self._crans:
            src, rayon = self.sources[kid], self._reglages(kid)["rayon"]
            niveaux = {}
            for z in T.carres(src["x"], src["y"], rayon):
                dx = max(z[0] - src["x"], 0, src["x"] - z[2])
                dy = max(z[1] - src["y"], 0, src["y"] - z[3])
                d = math.hypot(dx, dy)
                if d <= rayon:
                    niveaux.setdefault(int(d // LARGEUR_CRAN), []).append((d, math.hypot((z[0] + z[2]) / 2 - src["x"], (z[1] + z[3]) / 2 - src["y"]), z))
            self._crans[kid] = [[z for _, _, z in sorted(niveaux.get(k, []))] for k in range(max(niveaux) + 1)] if niveaux else []
        return self._crans[kid]

    async def _lire_zone(self, kid, z):
        """Lit une zone de la carte ; met a jour le cache. Renvoie True si le jeu a repondu (2 essais)."""
        for essai in range(2):
            await self._vider()
            await self.jeu.envoyer("gaa", {"KID": kid, "AX1": z[0], "AY1": z[1], "AX2": z[2], "AY2": z[3]})
            rep = await self.jeu.attendre("gaa", 10 if essai == 0 else 6)
            await self.dormir(T.PAUSE_ENTRE_GAA)
            d = T.json_ou_none(rep[2]) if rep else None
            if rep and rep[1] == 0 and isinstance(d, dict) and d.get("KID", kid) == kid:
                # seulement les objets DE la zone : le jeu renvoie un peu plus large, et une tour gardee dans deux zones
                # garderait une copie perimee (cause du blocage du 03/11 : la copie la disait libre, la verification non)
                self.cache.setdefault(kid, {})[z] = (self.horloge(), {(e[1], e[2]): e for e in d.get("AI", [])
                                                                    if isinstance(e, list) and len(e) >= 3
                                                                    and isinstance(e[1], int) and isinstance(e[2], int)
                                                                    and z[0] <= e[1] <= z[2] and z[1] <= e[2] <= z[3]})
                return True
            self.raison_zone = ("pas de reponse du jeu" if not rep else "le jeu repond par une erreur (code %s) : %s" % (rep[1], str(rep[2])[:60])
                                if rep[1] != 0 else "reponse illisible ou pour un autre royaume")
        return False

    async def _lire_cran(self, kid):
        """Lit la couronne suivante (13 cases de plus autour du chateau) du royaume."""
        k = self.cran.get(kid, 0)
        zones = self._couronnes(kid)[k]
        self.cran[kid] = k + 1
        debut, ratees = self.horloge(), 0
        for z in zones:
            if not await self._lire_zone(kid, z):
                ratees += 1
        if k > 0:
            self.sortie("   carte du royaume %d (%s) elargie jusqu'a %d cases (%d zone(s)%s, %s)."
                        % (kid, ROYAUMES[kid], (k + 1) * LARGEUR_CRAN, len(zones),
                           ", %d sans reponse du jeu" % ratees if ratees else "", duree(self.horloge() - debut)))

    async def _rafraichir_proches(self):
        """Relit quelques zones DEJA lues dont la lecture date de plus de RELIRE_TOURS secondes, les plus proches du chateau
        d'abord (couronne par couronne) : un minuteur de tour raccourci dans le jeu (acceleration) est ainsi vu en 1 a 2 min
        quand un commandant attend, sans relire toute la carte."""
        if self.partage.urgence_prioritaire(self.NOM_MODULE):
            return
        t = self.horloge()
        a_relire = []
        for kid in self._royaumes_actifs():
            lues = self.cache.get(kid, {})
            for rang, zones in enumerate(self._couronnes(kid)[:self.cran.get(kid, 0)]):
                for ordre, z in enumerate(zones):
                    if z in lues and t - lues[z][0] >= RELIRE_TOURS:
                        a_relire.append((rang, ordre, kid, z))
        for _, _, kid, z in sorted(a_relire)[:ZONES_RELUES_PAR_PASSAGE]:
            await self._lire_zone(kid, z)

    def _tours_libres(self):
        """[(distance, royaume, entree)] : tours libres dans les zones deja lues (minuteur ecoule depuis la lecture), pas engagees."""
        t, res = self.horloge(), []
        for kid, (lu, e) in self._tours_connues().items():
            src = self.sources[kid[0]]
            if (e[3] == -1 and combat.niveau_reel(kid[0], 2, e[4]) >= self.cfg.get("niveau_min", 0)
                    and lu + max(0, e[5]) <= t + 1 and kid not in self.engagees):
                res.append((math.hypot(e[1] - src["x"], e[2] - src["y"]), kid[0], e))
        res.sort(key=lambda c: c[0])          # la plus proche d'abord, peu importe le royaume
        return res

    def _tours_connues(self):
        """{(royaume, x, y): (instant de lecture, entree)} : chaque tour UNE seule fois (sa lecture la plus recente), dans le
        rayon de son royaume."""
        res = {}
        for kid in self._royaumes_actifs():
            src, rayon = self.sources[kid], self._reglages(kid)["rayon"]
            for lu, entrees in self.cache.get(kid, {}).values():
                for e in entrees.values():
                    if e[0] == 2 and len(e) >= 7 and math.hypot(e[1] - src["x"], e[2] - src["y"]) <= rayon:
                        cle = (kid, e[1], e[2])
                        if cle not in res or lu > res[cle][0]:
                            res[cle] = (lu, e)
        return res

    def texte_carte(self, kid, libres=None):
        """'vert : 7 libre(s) sur 108 connue(s), carte lue jusqu'a 26 cases sur 100'."""
        rayon = self._reglages(kid)["rayon"]
        lu = min(rayon, self.cran.get(kid, 0) * LARGEUR_CRAN)
        n = sum(1 for k, (_, e) in self._tours_connues().items() if k[0] == kid and e[3] == -1)
        return "%s : %s%d connue(s), carte lue jusqu'a %d cases sur %d" % (
            ROYAUMES[kid], "%d libre(s) sur " % libres if libres is not None else "", n, lu, rayon)

    def _prochaine_tour(self):
        """Secondes avant la prochaine tour qui se libere (zones deja lues), ou None."""
        t = self.horloge()
        d = [lu + e[5] - t for kid in self._royaumes_actifs() for lu, entrees in self.cache.get(kid, {}).values()
             for e in entrees.values() if e[0] == 2 and len(e) >= 7 and e[3] == -1 and e[5] > 0 and (kid, e[1], e[2]) not in self.engagees]
        return min(d) if d else None

    async def candidates(self, besoin):
        """Tours libres, la carte etant lue couronne par couronne autour du chateau : on n'elargit que s'il y a moins de
        tours libres que de commandants prets (`besoin`) et qu'il reste des zones a lire."""
        while True:
            cands = self._tours_libres()
            if len(cands) >= besoin or self.partage.urgence_prioritaire(self.NOM_MODULE):
                return cands                                  # (urgence d'un module prioritaire : on n'elargit pas maintenant)
            suivants = [(self.cran.get(k, 0), k) for k in self._royaumes_actifs() if self.cran.get(k, 0) < len(self._couronnes(k))]
            if not suivants:
                return cands
            await self._lire_cran(min(suivants)[1])           # le royaume dont la prochaine couronne est la plus proche

    async def _verifier_tour(self, cand):
        """Relit la zone de la tour juste avant l'attaque (elle a pu changer) : renvoie l'entree a jour, ou None si plus libre."""
        d, kid, e = cand
        z = next((z for z in self.cache.get(kid, {}) if z[0] <= e[1] <= z[2] and z[1] <= e[2] <= z[3]), None)
        if z is None or not await self._lire_zone(kid, z):
            self.engagees[(kid, e[1], e[2])] = self.horloge() + 60
            self.zones_ratees += 1
            self._noter("   tour %s (%d,%d) : la carte n'a pas pu etre relue (%s), tour sautee 1 min."
                        % (ROYAUMES.get(kid, kid), e[1], e[2], getattr(self, "raison_zone", "zone inconnue")))
            return None
        self.zones_ratees = 0
        frais = self.cache[kid][z][1].get((e[1], e[2]))
        if frais is None or frais[0] != 2 or frais[3] != -1 or frais[5] > 0:
            # pas libre : mise de cote jusqu'a la fin de son delai (au moins 2 min), meme si une autre lecture la disait libre
            delai = frais[5] if frais is not None and len(frais) > 5 and isinstance(frais[5], (int, float)) and frais[5] > 0 else 0
            self.engagees[(kid, e[1], e[2])] = self.horloge() + max(120, delai)
            return None
        return frais

    # ---------------------------------------------------------------- une attaque
    async def attaquer(self, pos, tour, kid):
        cx, cy = self.sources[kid]["x"], self.sources[kid]["y"]
        tx, ty = tour[1], tour[2]
        cle = (kid, tx, ty)
        nom = "%s %s (%d,%d)" % (self.NOM_CIBLE, ROYAUMES[kid], tx, ty)
        vus = set()
        for essai in range(2):                     # pas de reponse : un 2e essai (le jeu en perd parfois une)
            await self._vider()
            await self.jeu.envoyer(self.CMD_INFO, {"SX": cx, "SY": cy, "TX": tx, "TY": ty, "KID": kid})
            rep = await self.jeu.attendre(self.CMD_INFO, 15 if essai == 0 else 10)
            if rep:
                break
            vus |= {m[0] for m in self.jeu.recus}
        if not rep or rep[1] != 0:
            self.engagees[cle] = self.horloge() + 60
            self._noter("   %s : pas de reponse correcte aux infos d'attaque (%s), on la saute 1 min."
                        % (nom, str(rep)[:120] if rep else "aucune reponse en 2 essais ; messages recus pendant l'attente : %s"
                           % (", ".join(sorted(vus)) or "aucun")))
            return False
        adi = T.json_ou_none(rep[2]) or {}
        try:
            cra, info = self._construire(adi, pos, tour, kid, (cx, cy))
        except CibleEnAttente as e:
            self.engagees[cle] = self.horloge() + e.secondes + 30
            self._noter("   %s : %s, on la laisse de cote %s."
                        % (nom, e.texte or "pas attaquable pour le moment", duree(e.secondes + 30)))
            return False
        except PlusDeSoldats as e:
            self.sans_stock[kid] = self.horloge() + PAUSE_SANS_STOCK
            self._noter("   %s Ce royaume est laisse de cote %s." % (e, duree(PAUSE_SANS_STOCK)))
            return False
        except ValueError as e:
            self.absents.add(pos)
            self._noter("   %s : ce numero est ignore." % e)
            return False
        if info["manques"]:
            for m in info["manques"][:3]:
                self._noter("   ATTENTION : " + m)
        if self.a_confirmer:
            await self._confirmer(cra, info, tour)
        if self.simulation:
            self._noter("[SIMULATION] commandant %d (%s) -> %s niv %s, dist %.1f, mode %s, soldat %s, cheval %s%s, %d vague(s) : paquet NON envoye"
                        % (pos, info["nom"], nom, info["niveau"], info["distance"], info["mode"], info["soldat"], info["cheval"],
                           " (reglage non appris)" if info["cheval_inconnu"] else "", info["vagues"]))
            self.libre_a[pos] = self.horloge() + 120
            self.engagees[cle] = self.horloge() + 3600
            self.nb_attaques += 1
            return True
        reste = self.dernier_envoi + DELAI_MIN_ATTAQUES - self.horloge()
        if reste > 0:                      # script trop rapide : on attend juste ce qu'il manque pour avoir 4 s
            await self.dormir(reste)
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                return False               # un module prioritaire est devenu urgent pendant l'attente : il passe d'abord (rien n'est envoye)
        await self._vider()
        await self.jeu.envoyer("cra", cra)
        self.dernier_envoi = self.horloge()
        rep = await self.jeu.attendre("cra", 15)
        d = T.json_ou_none(rep[2]) if rep else None
        if rep and rep[1] == 0 and isinstance(d, dict) and "AAM" in d:
            tt = (d["AAM"].get("M") or {}).get("TT") or 60
            self.echecs = 0
            self.nb_attaques += 1
            # borne haute de securite (aller + retour identiques) ; remplacee par la vraie duree des l'arrivee (message cat)
            self.libre_a[pos] = self.horloge() + 2 * tt + 15
            self.en_route[cle] = (pos, self.horloge() + tt)
            self.engagees[cle] = self.horloge() + tt + self.ATTENTE_APRES_ATTAQUE
            self._noter("ATTAQUE %d : commandant %d (%s) -> %s niv %s, dist %.1f, mode %s, soldat %s, cheval %s, %s, trajet %s s"
                        % (self.nb_attaques, pos, info["nom"], nom, info["niveau"], info["distance"], info["mode"], info["soldat"], info["cheval"],
                           texte_vagues(info), tt))
            return True
        self.echecs += 1
        # le probleme vient de la CIBLE (le meme refus pour tous les commandants) : on la laisse de cote, le commandant reste libre
        n = self.refus_cibles[cle] = self.refus_cibles.get(cle, 0) + 1
        pause = CIBLE_REFUSEE[min(n, len(CIBLE_REFUSEE)) - 1]
        self.engagees[cle] = self.horloge() + pause
        try:
            flancs = ", ".join("%s %d" % (combat.NOMS_COTES[c], sum(q for _, q in cra["A"][0][c]["U"] if q > 0))
                               for c in ("L", "M", "R") if any(q > 0 for _, q in cra["A"][0][c]["U"]))
        except (KeyError, IndexError, TypeError, ValueError):
            flancs = "?"
        self._noter("   REFUS : commandant %d -> %s niv %s : %s. Vague 1 : %s ; %s. Cible laissee de cote %s, le commandant "
                    "reste disponible pour une autre cible."
                    % (pos, nom, texte_niveau(info),
                       "code %s, reponse %s" % (rep[1], rep[2][:200]) if rep else "attaque ignoree par le serveur (pas de reponse)",
                       flancs or "aucun soldat", texte_vagues(info), duree(pause)))
        if self.echecs >= ECHECS_MAX:
            raise ArretModule("%d refus de suite : le module s'arrete pour ne pas insister. Regarde le message du serveur "
                              "ci-dessus (ou captures/journal_tours.txt)." % ECHECS_MAX)
        return False

    def _construire(self, adi, pos, tour, kid, chateau):
        """Paquet cra du commandant `pos` sur la cible (voir construire_attaque) ; les forteresses surchargent cette methode."""
        return construire_attaque(self.cfg, adi, pos, tour, kid, chateau, self.effets, self.appris,
                                  cheval_inconnu_ok=self.simulation, presets=self.presets, fiches_outils=self.fiches_outils,
                                  unites=getattr(self, "unites", None))

    async def _confirmer(self, cra, info, tour):
        self.sortie("")
        self.sortie("PREMIERE ATTAQUE - verification (les suivantes partiront sans redemander) :")
        self.sortie("  commandant %d (%s), %s %s (%d,%d) niveau %s (retenu %s), distance %.1f, cheval %s"
                    % (info["pos"], info["nom"], self.NOM_CIBLE, ROYAUMES[info["kid"]], tour[1], tour[2], info["niveau_brut"], info["niveau"],
                       info["distance"], info["cheval"]))
        self.sortie("  %d vague(s)%s ; plafond par flanc %d, front %d (bonus du commandant %s)"
                    % (info["vagues"], " dont %d en plus grace au commandant" % info["vagues_en_plus"] if info["vagues_en_plus"] else "",
                       info["plafond_flanc"], info["plafond_front"], "applique" if self.cfg.get("avec_bonus") else "ignore"))
        for ligne in T.resume_armee(cra):
            self.sortie("    " + ligne)
        while True:
            rep = sans_accents(await self.lire("  Lancer le module ? Tape OUI ou NON > ")).strip().upper()
            if rep in ("OUI", "NON"):
                break
            self.sortie("    -> Reponds exactement OUI ou NON.")
        if rep != "OUI":
            raise ArretModule("Annule : rien n'a ete envoye.")
        self.a_confirmer = False

    # ---------------------------------------------------------------- cycle et boucle
    def _reserves(self):
        """Commandants que ce module n'a pas le droit de prendre maintenant (gardes par un autre module), sinon ensemble vide."""
        res = self.partage.gardes_par_les_autres(self.NOM_MODULE)
        if self.restriction is not None:
            res |= set(self.restriction())
        return res

    def _libres(self):
        t = self.horloge()
        interdits = self._reserves()
        return [p for p in self.cfg["commandants"] if p not in self.absents and p not in interdits and self.libre_a.get(p, 0) <= t]

    async def cycle(self):
        """Un passage : renvoie le nombre de secondes a attendre avant le suivant."""
        await self._absorber()
        t = self.horloge()
        self.en_route = {k: v for k, v in self.en_route.items() if v[1] > t - 3600}
        self._verifier_stock()
        libres = self._libres()
        if not libres:
            restants = [self.libre_a.get(p, 0) for p in self.cfg["commandants"] if p not in self.absents]
            if not restants:
                raise ArretModule("Aucun des commandants choisis n'existe dans ta liste : arret.")
            gardes = sorted(p for p in self._reserves() if p in self.cfg["commandants"] and p not in self.absents and self.libre_a.get(p, 0) <= t)
            if gardes:
                self._attente_msg("gardes-%s" % gardes, "En attente : commandant(s) %s gardes par un autre module (reservation)."
                                  % ";".join(str(p) for p in gardes))
                return 3
            attente = max(5, min(restants) - t)
            self._attente_msg("commandants", "En attente : un commandant revient au plus tard dans %s." % duree(attente))
            return min(attente, 3)      # on relit souvent : le retour reel (cat) est plus court que l'estimation
        self.engagees = {k: v for k, v in self.engagees.items() if v > t}
        await self._rafraichir_proches()            # une tour dont le delai a ete accelere dans le jeu est vue rapidement
        restants = list(libres)
        partis = 0
        essais_rates = 0
        verifs = 0
        cede = False
        while restants:
            interdits = self._reserves()                   # la reserve a pu changer pendant la lecture de la carte
            restants = [p for p in restants if p not in interdits]
            if not restants:
                break
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                cede = True
                break
            cands = await self.candidates(len(restants))
            cand = next((c for c in cands if self.sans_stock.get(c[1], 0) <= self.horloge()), None)
            if cand is None:
                if not partis:
                    proch = self._prochaine_tour()
                    attente = min(max(proch if proch is not None else 60, 15), 60)   # 60 s au plus : une tour acceleree est vue vite
                    if self.en_route:
                        attente = min(attente, 10)      # des commandants reviennent : ne pas dormir longtemps
                    self._attente_msg("aucune-tour", "Aucune tour libre pour le moment (%d commandant(s) pret(s)) : nouvel essai dans %s."
                                      % (len(libres), duree(attente)))
                    return attente
                break
            verifs += 1
            if verifs > VERIFS_MAX_PAR_PASSAGE:
                self._noter("   %d tours verifiees dans ce passage sans en trouver une libre : pause de 10 s (%s)."
                            % (VERIFS_MAX_PAR_PASSAGE, "; ".join(self.texte_carte(k) for k in self._royaumes_actifs())))
                return 10
            frais = await self._verifier_tour(cand)
            if frais is None:
                if self.zones_ratees >= ZONES_RATEES_MAX:      # le jeu ne repond plus a la carte : on ne tourne pas en rond
                    self._noter("   le jeu ne repond plus aux demandes de carte (%d de suite) : nouvel essai dans %d s."
                                % (self.zones_ratees, PAUSE_ZONES_RATEES))
                    self.zones_ratees = 0
                    return PAUSE_ZONES_RATEES
                continue                                # plus libre : on passe a la suivante
            interdits = self._reserves()                   # la lecture de la carte a pu prendre du temps : on reverifie la reserve
            restants = [p for p in restants if p not in interdits]
            if not restants:
                break
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                cede = True
                break
            if not partis:
                par_royaume = ", ".join("%s %d" % (ROYAUMES[k], sum(1 for c in cands if c[1] == k)) for k in sorted({c[1] for c in cands}))
                self._attente_msg("tours-libres-%s" % par_royaume, "%d tour(s) libre(s) (%s), %d commandant(s) pret(s)."
                                  % (len(cands), " ; ".join(self.texte_carte(k, sum(1 for c in cands if c[1] == k))
                                                           for k in sorted({c[1] for c in cands})), len(libres)))
            partis += 1
            pos = restants.pop(0)
            if not await self.attaquer(pos, frais, cand[1]):
                essais_rates += 1
                # cible refusee / pas attaquable : le commandant n'est pas parti, il essaie la cible suivante
                if (essais_rates < ESSAIS_PAR_PASSAGE and pos not in self.absents and self.libre_a.get(pos, 0) <= self.horloge()
                        and pos not in self._reserves()):
                    restants.insert(0, pos)
            if self.max_attaques and self.nb_attaques >= self.max_attaques:
                return 0
        if cede:
            self._noter("   pause : un module prioritaire (forteresses) a une attaque a faire, il passe d'abord ; reprise juste apres.")
            return 1
        self._verifier_stock()
        return 5

    def _verifier_stock(self):
        if not self._royaumes_actifs():
            blocs = [self._reglages(k) for k in self.cfg["royaumes"]]
            unites = "/".join(str(u) for u in sorted({x for b in blocs for x in soldats_de(b)}))
            modes = {b["mode"] for b in blocs}
            if modes == {"presets"}:
                quoi = "des preselections choisies (ni de l'unite %s du remplissage automatique)" % unites
            elif modes == {"auto"}:
                quoi = "de l'unite %s" % unites
            else:
                quoi = "des preselections choisies ni de l'unite %s du remplissage automatique" % unites
            raise ArretModule("Plus aucun soldat %s dans le chateau de depart d'aucun des royaumes choisis : "
                              "arret du module." % quoi)

    async def lancer(self):
        if royaumes_en_presets(self.cfg) and not self.presets:
            raise ArretModule("Mode preselections : les preselections du compte ne sont pas chargees.")
        self.sortie("")
        self.sortie("MODULE %s EN MARCHE (Ctrl+C = arret + deconnexion du compte).%s"
                    % (self.NOM_MODULE, "  MODE SIMULATION : rien n'est envoye." if self.simulation else ""))
        if self.duree_max:
            self.fin = self.horloge() + self.duree_max
            self.sortie("Duree demandee : %s (arret et deconnexion vers %s)."
                        % (duree(self.duree_max), time.strftime("%H:%M", time.localtime(self.fin))))
        while True:
            if self.vivante is not None and not await self.vivante():
                raise ArretModule("SESSION COUPEE : le serveur a ferme la connexion (le joueur s'est probablement connecte). "
                                  "Le module s'arrete.")
            if self.fin is not None and self.horloge() >= self.fin:
                self.sortie("Duree demandee atteinte (%s) : arret et deconnexion du compte. Les armees deja parties reviennent seules."
                            % duree(self.duree_max))
                return
            attente = await self.cycle()
            if self.max_attaques and self.nb_attaques >= self.max_attaques:
                self.sortie("Nombre d'attaques demande atteint (%d) : arret." % self.max_attaques)
                return
            if attente > 0:
                if self.fin is not None:
                    attente = min(attente, max(0.0, self.fin - self.horloge()))
                await self.dormir(attente)


# ============================================================================== lancement
def charger_effets(sortie=print):
    if not os.path.exists(FICHIER_ITEMS):
        sortie("ATTENTION : %s introuvable : vagues supplementaires des commandants ignorees (lance lancer_items.bat)." % FICHIER_ITEMS)
        return None
    return combat.effets_par_type(FICHIER_ITEMS)


def charger_unites_presets(cfg, sortie=print):
    """Fiches des unites (attaque, distance / corps a corps) : seulement s'il y a un royaume en mode preselections (complement)."""
    if not royaumes_en_presets(cfg):
        return None
    if not os.path.exists(FICHIER_ITEMS):
        sortie("ATTENTION : %s introuvable : une preselection incomplete ne sera completee qu'avec l'unite de remplacement." % FICHIER_ITEMS)
        return None
    return combat.charger_unites(FICHIER_ITEMS)


def charger_fiches_outils(sortie=print):
    """Fiches des outils (quels outils peuvent attaquer une tour) lues dans items.json ; None si le fichier manque."""
    if not os.path.exists(FICHIER_ITEMS):
        sortie("ATTENTION : %s introuvable : je ne peux pas verifier quels outils des preselections sont permis sur une tour "
               "(lance lancer_items.bat)." % FICHIER_ITEMS)
        return None
    return combat.charger_outils(FICHIER_ITEMS)


def preparer_config(args, lire=None, sortie=print, rubrique="tours", assistant=None, valide=None, resume=None):
    """Reprend la configuration enregistree (Entree) ou lance l'assistant. Par defaut celle du module tours ;
    le module forteresses passe sa rubrique et ses propres fonctions (assistant, valide, resume)."""
    lire = lire or input
    assistant, valide, resume = assistant or assistant_config, valide or config_valide, resume or resume_config
    cfg = lire_config().get(rubrique)
    if args.reconfigurer or not cfg or not valide(cfg):
        if cfg and not args.reconfigurer and not valide(cfg):
            sortie("La configuration enregistree est incomplete : on la refait.")
        cfg = assistant(lire, sortie, cfg)
        sortie("")
        sortie("RECAPITULATIF :")
        for ligne in resume(cfg):
            sortie("  - " + ligne)
        if not demander(lire, sortie, "Enregistrer et continuer ? (Entree ou O = oui, N = non) > ", analyser_confirmation):
            raise ArretModule("Configuration annulee.")
        memoriser_config(cfg, rubrique=rubrique)
        sortie("Configuration enregistree dans config.json.")
        return cfg
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume(cfg):
        sortie("  - " + ligne)
    if getattr(args, "config_ok", False):            # lance depuis l'interface : la configuration vient d'y etre verifiee
        return cfg
    if not demander(lire, sortie, "Utiliser cette configuration ? (Entree ou O = oui, N = la refaire) > ", analyser_confirmation):
        return preparer_config(argparse.Namespace(reconfigurer=True), lire, sortie, rubrique, assistant, valide, resume)
    return cfg


async def principal(args):
    from session import SessionBot
    import connexion_bot

    async def lire_async(invite):
        return await asyncio.to_thread(input, invite)

    try:
        cfg = None if args.apprendre_cheval else preparer_config(args)    # l'apprentissage n'a pas besoin de la config
        if cfg and args.avec_bonus:
            cfg["avec_bonus"] = True
        effets = charger_effets() if cfg else None
        fiches_outils = charger_fiches_outils() if cfg and royaumes_en_presets(cfg) else None
        import eveil
        eveil.console_sans_selection()                 # un clic dans la fenetre du bot ne le met plus en pause
        if not getattr(args, "sans_ecran_allume", False):
            eveil.GarderEveille(print).__enter__()     # ecran et PC allumes ; Windows reprend ses reglages a la fin du programme
        async with SessionBot(demander_identifiants=lambda: connexion_bot.demander_identifiants_ecran(args.pseudo)) as jeu:
            async def vivante():
                return await connexion_bot._vivante(jeu)
            try:
                if args.apprendre_cheval:
                    await apprendre_cheval(jeu, args.apprendre_cheval, print, lire_async)
                    return
                sources = await determiner_sources(jeu, cfg, print, lire_async)
                print("")
                print("CHATEAUX DE DEPART :")
                for ligne in lignes_sources(sources):
                    print("  - " + ligne)
                presets = None
                if royaumes_en_presets(cfg):
                    presets = await lire_presets(jeu, print)
                    verifier_presets(cfg, presets)
                    print("")
                    print("PRESELECTIONS UTILISEES (lues sur ton compte) :")
                    for ligne in lignes_presets(cfg, presets):
                        print("  - " + ligne)
                await assurer_chevaux(jeu, cfg, print, lire_async, args.simulation)
                module = ModuleTours(jeu, cfg, sources, effets, sortie=print, lire=lire_async, vivante=vivante,
                                     simulation=args.simulation, max_attaques=args.max_attaques,
                                     confirmer=getattr(args, "confirmer", False), presets=presets, fiches_outils=fiches_outils,
                                     duree_max=args.duree)
                module.unites = charger_unites_presets(cfg)
                await module.lancer()
            except ArretModule as e:
                print("\n" + str(e))
    except ArretModule as e:
        print("\n" + str(e))
    except T.ErreurCDP as e:
        print("ERREUR :", e)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nArret demande.")


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
# Chaque module fournit les memes 3 fonctions ; le lanceur ne connait rien d'autre du module.
LANCEUR_NOM = "tours"          # nom du module dans le lanceur (--modules) et dans config.json ("lanceur")
LANCEUR_ETIQUETTE = "TOURS"    # prefixe des lignes de ce module dans la console du lanceur
LANCEUR_CLE = ModuleTours.NOM_MODULE   # cle du module dans le partage (reservations, commandants)
LANCEUR_PRIORITE = 10          # apres les forteresses (0)


def lanceur_config(args, lire=None, sortie=print):
    """AVANT la connexion : configuration du module (reprise ou assistant). Renvoie la configuration."""
    cfg = preparer_config(args, lire, sortie)
    if getattr(args, "avec_bonus", False):
        cfg["avec_bonus"] = True
    return cfg


def lanceur_ressources(cfg, sortie=print):
    """AVANT la connexion : fichiers dont le module a besoin (leve ArretModule s'il en manque un indispensable)."""
    return {"effets": charger_effets(sortie),
            "fiches_outils": charger_fiches_outils(sortie) if royaumes_en_presets(cfg) else None,
            "unites": charger_unites_presets(cfg, sortie)}


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    """APRES la connexion : chateaux, preselections, cheval ; renvoie le module pret (le lanceur appelle ensuite cycle())."""
    sources = await determiner_sources(jeu, cfg, sortie, lire)
    sortie("")
    sortie("CHATEAUX DE DEPART :")
    for ligne in lignes_sources(sources):
        sortie("  - " + ligne)
    presets = None
    if royaumes_en_presets(cfg):
        presets = await lire_presets(jeu, sortie)
        verifier_presets(cfg, presets)
        sortie("")
        sortie("PRESELECTIONS UTILISEES (lues sur ton compte) :")
        for ligne in lignes_presets(cfg, presets):
            sortie("  - " + ligne)
    await assurer_chevaux(jeu, cfg, sortie, lire, args.simulation)
    module = ModuleTours(jeu, cfg, sources, ressources["effets"], sortie=sortie, lire=lire, simulation=args.simulation,
                         confirmer=getattr(args, "confirmer", False), presets=presets, fiches_outils=ressources["fiches_outils"], partage=partage)
    module.unites = ressources.get("unites")
    if royaumes_en_presets(cfg) and not presets:
        raise ArretModule("Mode preselections : les preselections du compte ne sont pas chargees.")
    return module


def argument_duree(texte):
    try:
        return analyser_duree(texte)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def lire_args(argv=None):
    p = argparse.ArgumentParser(description="Module d'attaque des tours (voir l'en-tete du fichier).")
    p.add_argument("--reconfigurer", action="store_true", help="refaire les questions de configuration")
    p.add_argument("--simulation", action="store_true", help="tout faire sauf envoyer les attaques")
    p.add_argument("--max-attaques", type=int, default=None, help="s'arreter apres N attaques (test)")
    p.add_argument("--duree", type=argument_duree, default=None, help="s'arreter apres cette duree : 90m, 3h, 2h30 (sinon : jusqu'a Ctrl+C)")
    p.add_argument("--oui", action="store_true", help="(sans effet : plus aucune confirmation par defaut)")
    p.add_argument("--confirmer", action="store_true", help="demander OUI avant la premiere attaque (verification)")
    p.add_argument("--avec-bonus", action="store_true", help="applique le bonus de flanc/front du commandant (non verifie)")
    p.add_argument("--apprendre-cheval", choices=("C", "F"), help="enregistrer les reglages du cheval pieces (C) ou plumes (F)")
    p.add_argument("--pseudo")
    p.add_argument("--sans-ecran-allume", action="store_true", help="laisser Windows mettre l'ecran en veille / verrouiller la session")
    return p.parse_args(argv)


if __name__ == "__main__":
    try:
        asyncio.run(principal(lire_args()))
    except KeyboardInterrupt:
        print("\nArret.")
