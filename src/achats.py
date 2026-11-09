#!/usr/bin/env python3
"""
achats.py : module ACHATS - depense le surplus de pieces en engins (armurier du jeu), pour ne pas depasser la limite du jeu
(2 147 483 647 pieces : tout ce qui depasse est perdu).

Reglages (interface, page Achats ; config.json rubrique "achats") :
  - seuil : a partir de combien de pieces le bot achete (ex. 2 000 000 000) ;
  - royaumes choisis et leur part (en %, total 100) ;
  - pour chaque royaume, les engins choisis et leur part (en %, total 100).
Quand les pieces atteignent le seuil, le bot depense 10 % du seuil (ex. 200 millions pour 2 milliards), reparti selon ces parts :
  ex. vert 70 % / feu 30 % -> 140 M au vert, 60 M au feu ; au vert, echelles 80 % / portes 20 % -> 112 M d'echelles, 28 M de portes.
L'achat se fait dans le chateau de depart du royaume (le meme que pour les tours), par le message sbp de l'armurier.
Priorite la plus basse : une forteresse ou une tour a attaquer passe toujours avant ; un achat en cours s'interrompt et reprend
au passage suivant. Jamais en simulation. Les pieces ne descendent jamais sous seuil - 10 %.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combat  # noqa: E402
import tours   # noqa: E402
from tours import ArretModule, ROYAUMES, duree  # noqa: E402

T = tours.T
RUBRIQUE = "achats"
FICHIER_ETAT = os.path.join(tours.DOSSIER_CAPTURES, "etat_achats.json")
PIECES_MAX = 2147483647            # limite du jeu (au-dela, les pieces gagnees sont perdues)
SEUIL_MIN = 10000000               # seuil le plus bas accepte (10 millions)
PART_BUDGET = 10                   # % du seuil depenses a chaque fois
TAILLES = (100000, 10000, 1000)    # quantite par demande : on essaie la plus grande, on descend si le jeu refuse
PAUSE_ENTRE = 0.5                  # secondes entre deux demandes d'achat
PAUSE_REFUS = 600                  # engin refuse meme par 1000 : laisse de cote 10 min
VERIF = 60                         # secondes entre deux verifications des pieces (elles arrivent toutes seules : message gcu)

# Engins de l'armurier : objet -> (nom pour la console, paquet sbp, prix de secours si items.json manque)
ENGINS = {
    614: ("echelles en bois", 28, 220),
    637: ("rochers", 30, 550),
    611: ("beliers", 32, 430),
    626: ("renforts de porte", 34, 1720),
    620: ("mantelets", 36, 810),
    629: ("fleches enflammees", 38, 3210),
}
NOMS_ENGINS = {614: "Échelles en bois", 637: "Rochers", 611: "Béliers", 626: "Renforts de porte", 620: "Mantelets",
               629: "Flèches enflammées"}     # avec accents, pour l'interface


def prix_engins(fichier=None):
    """{objet: prix en pieces} lu dans items.json (packages de l'armurier) ; prix de secours sinon."""
    prix = {w: p for w, (_, _, p) in ENGINS.items()}
    try:
        with open(fichier or tours.FICHIER_ITEMS, encoding="utf-8") as f:
            d = json.load(f)
        for pk in d.get("packages", []):
            try:
                w, pid = int(pk.get("unitID")), int(pk.get("packageID"))
            except (TypeError, ValueError):
                continue
            if w in ENGINS and pid == ENGINS[w][1] and int(pk.get("unitAmount", 1)) == 1:
                prix[w] = int(pk["packagePriceC1"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return prix


def fmt(n):
    return "{:,}".format(int(n)).replace(",", " ")


# ============================================================================== configuration
def config_valide(cfg):
    """{"seuil", "royaumes", "parts": {royaume: %}, "engins": {royaume: {objet: %}}} : totaux de 100 %, valeurs entieres."""
    try:
        s = cfg["seuil"]
        if not isinstance(s, int) or isinstance(s, bool) or not SEUIL_MIN <= s <= PIECES_MAX:
            return False
        roy = cfg["royaumes"]
        if not roy or len(set(roy)) != len(roy) or not all(k in ROYAUMES for k in roy):
            return False
        parts = [cfg["parts"][str(k)] for k in roy]
        if not all(isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 100 for p in parts) or sum(parts) != 100:
            return False
        for k in roy:
            e = cfg["engins"][str(k)]
            if not e or not all(str(w).isdigit() and int(w) in ENGINS for w in e):
                return False
            v = list(e.values())
            if not all(isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 100 for p in v) or sum(v) != 100:
                return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def plan_achats(cfg, prix):
    """[(royaume, objet, quantite, budget)] : 10 % du seuil, reparti par royaume puis par engin (arrondi a l'unite inferieure)."""
    budget = cfg["seuil"] * PART_BUDGET // 100
    plan = []
    for k in cfg["royaumes"]:
        b_roy = budget * cfg["parts"][str(k)] // 100
        for w, p in cfg["engins"][str(k)].items():
            b = b_roy * p // 100
            q = b // prix[int(w)]
            if q > 0:
                plan.append((k, int(w), q, q * prix[int(w)]))
    return plan


def resume_config(cfg, prix=None):
    prix = prix or prix_engins()
    budget = cfg["seuil"] * PART_BUDGET // 100
    l = ["a partir de %s pieces, le bot depense %s pieces (%d %%) en engins :" % (fmt(cfg["seuil"]), fmt(budget), PART_BUDGET)]
    for k, w, q, b in plan_achats(cfg, prix):
        l.append("    %s : %s %s (%s pieces)" % (ROYAUMES[k], fmt(q), ENGINS[w][0], fmt(b)))
    return l


# ============================================================================== le module
class ModuleAchats:
    NOM_MODULE = "ACHATS"

    def __init__(self, jeu, cfg, sources, prix, partage=None, sortie=print, simulation=False, horloge=None, dormir=None,
                 fichier_etat=FICHIER_ETAT):
        import asyncio
        import time
        self.jeu, self.cfg, self.sources, self.prix = jeu, cfg, sources, prix
        self.partage = partage if partage is not None else tours.Partage()
        self.sortie, self.simulation = sortie, simulation
        self.horloge, self.dormir = horloge or time.time, dormir or asyncio.sleep
        self.fichier_etat = fichier_etat
        self.nb_attaques, self.max_attaques, self.nb_achats = 0, None, 0
        self.plan = []                 # achats restants de la depense en cours : [royaume, objet, quantite]
        self.pause = {}                # objet refuse -> instant avant lequel on ne le retente pas
        self.max_par_demande = TAILLES[0]
        self.plancher = 0              # les pieces ne descendent jamais sous ce montant pendant une depense
        self._pieces_demandees = float("-inf")
        self._dernier_msg = ("", 0)
        self.selectionne = None        # royaume dont le chateau est selectionne dans le jeu
        self._charger_etat()

    # ---------------------------------------------------------------- etat garde d'un lancement a l'autre
    def _charger_etat(self):
        if not self.fichier_etat:
            return
        try:
            with open(self.fichier_etat, encoding="utf-8") as f:
                d = json.load(f)
            m = d.get("max_par_demande")
            if m in TAILLES:
                self.max_par_demande = m
            plan = d.get("plan") or []
            if self.horloge() - float(d.get("sauve", 0)) < 6 * 3600:
                self.plan = [[int(k), int(w), int(q)] for k, w, q in plan if int(q) > 0 and int(k) in self.cfg["royaumes"]]
                self.plancher = int(d.get("plancher", 0)) if self.plan else 0
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _sauver_etat(self):
        if not self.fichier_etat:
            return
        try:
            os.makedirs(os.path.dirname(self.fichier_etat), exist_ok=True)
            with open(self.fichier_etat + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"max_par_demande": self.max_par_demande, "plan": self.plan, "plancher": self.plancher,
                           "sauve": self.horloge()}, f)
            os.replace(self.fichier_etat + ".tmp", self.fichier_etat)
        except OSError:
            pass

    # ---------------------------------------------------------------- messages du jeu
    def _absorber_messages(self):
        restant = []
        for m in list(self.jeu.recus):
            if m[0] == "cat":
                if self.partage.routeur:
                    self.partage.routeur(m[2])                 # retours d'armees : pour les modules d'attaque
                continue
            if m[0] == "gcu" and m[1] == 0:
                d = T.json_ou_none(m[2])
                if isinstance(d, dict) and isinstance(d.get("C1"), (int, float)) and not isinstance(d.get("C1"), bool):
                    self.partage.pieces = int(d["C1"])
            restant.append(m)
        self.jeu.recus[:] = restant

    async def _vider(self):
        self.partage.signe_de_vie()
        await self.jeu.relever()
        self._absorber_messages()
        await self.jeu.vider()

    def _noter(self, texte):
        self.sortie(texte)

    def _attente_msg(self, cle, texte):
        t = self.horloge()
        if cle != self._dernier_msg[0] or t - self._dernier_msg[1] > 1800:
            self.sortie(texte)
            self._dernier_msg = (cle, t)

    # ---------------------------------------------------------------- un passage
    def _retour(self, payload):
        """Retour d'armee (cat) donne a tous les modules : les achats n'envoient pas d'armee, rien a faire."""

    async def cycle(self):
        await self.jeu.relever()
        self._absorber_messages()
        t = self.horloge()
        if self.partage.pieces is None and t - self._pieces_demandees >= VERIF:
            self._pieces_demandees = t
            try:
                await self._vider()
                await self.jeu.envoyer("gcu", {})              # lecture seule : les pieces du compte
                rep = await self.jeu.attendre("gcu", 5)
                if rep and rep[1] == 0:
                    self.jeu.recus.append(rep)
                    self._absorber_messages()
            except T.ErreurCDP:
                pass
        pieces = self.partage.pieces
        if pieces is None:
            self._attente_msg("inconnues", "Pieces du compte pas encore connues : je les verrai au prochain gain.")
            return VERIF
        if not self.plan:
            if pieces < self.cfg["seuil"]:
                self._attente_msg("attente", "Pieces : %s. Achat d'engins a partir de %s (limite du jeu : %s)."
                                  % (fmt(pieces), fmt(self.cfg["seuil"]), fmt(PIECES_MAX)))
                return VERIF
            self.plan = [[k, w, q] for k, w, q, _ in plan_achats(self.cfg, self.prix)]
            self.plancher = self.cfg["seuil"] - self.cfg["seuil"] * PART_BUDGET // 100
            self._noter("SEUIL ATTEINT : %s pieces (seuil %s) : achat de %s pieces d'engins." % (
                fmt(pieces), fmt(self.cfg["seuil"]), fmt(sum(q * self.prix[w] for _, w, q in self.plan))))
            for k, w, q in self.plan:
                self._noter("   prevu : %s %s au %s (%s pieces)" % (fmt(q), ENGINS[w][0], ROYAUMES[k], fmt(q * self.prix[w])))
            self._sauver_etat()
        await self._executer()
        return 1 if self.plan else VERIF

    async def _executer(self):
        """Achete ce qui reste du plan ; s'interrompt (et reprend au passage suivant) si un autre module a une attaque."""
        while self.plan:
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                return
            k, w, q = self.plan[0]
            if k in (getattr(self.partage, "royaumes_bloques", None) or ()):
                return                                   # attaque en cours sur ce monde (module defense) : plus tard
            t = self.horloge()
            if self.pause.get(w, 0) > t:
                self.plan.pop(0)
                continue
            nom = "%s %s au %s" % ("%s", ENGINS[w][0], ROYAUMES[k])
            if self.simulation:
                self._noter("[SIMULATION] achat de %s (rien n'est envoye)" % (nom % fmt(q)))
                self.plan.pop(0)
                continue
            n = min(q, self.max_par_demande)
            cout = n * self.prix[w]
            if self.partage.pieces is not None and self.partage.pieces - cout < self.plancher:
                n = max(0, (self.partage.pieces - self.plancher) // self.prix[w])
                if n <= 0:
                    self._noter("   achats arretes : les pieces (%s) ne descendent pas sous %s." % (fmt(self.partage.pieces), fmt(self.plancher)))
                    self.plan = []
                    self._sauver_etat()
                    return
                cout = n * self.prix[w]
            if self.selectionne != k:
                await self._choisir_chateau(k)
            ok, detail = await self._acheter(k, w, n)
            if ok:
                self.nb_achats += 1
                self.plan[0][2] -= n
                self._noter("ACHAT : %s (%s pieces) ; pieces restantes : %s." % (nom % fmt(n), fmt(cout),
                                                                                 fmt(self.partage.pieces) if self.partage.pieces is not None else "?"))
                if self.plan[0][2] <= 0:
                    self.plan.pop(0)
                self._sauver_etat()
                await self.dormir(PAUSE_ENTRE)
                continue
            if n > TAILLES[-1]:                              # trop d'un coup pour le jeu : on essaie plus petit
                plus_petit = next(x for x in TAILLES if x < n)
                self.max_par_demande = plus_petit
                self._noter("   le jeu refuse %s d'un coup (%s) : je passe a %s par demande." % (fmt(n), detail, fmt(plus_petit)))
                self._sauver_etat()
                continue
            self.pause[w] = self.horloge() + PAUSE_REFUS
            self._noter("   ACHAT REFUSE : %s (%s). Cet engin est laisse de cote %s." % (nom % fmt(n), detail, duree(PAUSE_REFUS)))
            self.plan.pop(0)
            self._sauver_etat()
        self._noter("Achats termines. Pieces : %s." % (fmt(self.partage.pieces) if self.partage.pieces is not None else "?"))
        self.plancher = 0
        self._sauver_etat()

    async def _choisir_chateau(self, k):
        """Comme le jeu : on selectionne le chateau de depart du royaume (l'achat arrive dans le chateau selectionne)."""
        src = self.sources.get(k) or {}
        if src.get("id") is not None:
            await self._vider()
            await self.jeu.envoyer("jca", {"CID": src["id"], "KID": k})
            await self.jeu.attendre("jaa", 10)
        self.selectionne = k

    async def _acheter(self, k, w, n):
        """Une demande sbp. Renvoie (True, "") si le jeu l'a acceptee, sinon (False, raison)."""
        avant = self.partage.pieces
        await self._vider()
        await self.jeu.envoyer("sbp", {"PID": ENGINS[w][1], "BT": 0, "TID": 27, "AMT": n, "KID": k, "AID": -1,
                                       "PC2": -1, "BA": 0, "PWR": 0, "_PO": -1})
        rep = await self.jeu.attendre("sbp", 10)
        await self.jeu.relever()
        self._absorber_messages()
        d = T.json_ou_none(rep[2]) if rep else None
        if rep and rep[1] == 0 and isinstance(d, dict):
            g = d.get("gcu") or {}
            if isinstance(g.get("C1"), (int, float)):
                self.partage.pieces = int(g["C1"])
            return True, ""
        if not rep and avant is not None and self.partage.pieces is not None and avant - self.partage.pieces >= n * self.prix[w] * 0.9:
            return True, ""                                  # pas de reponse sbp, mais les pieces ont bien baisse : achete
        return False, ("code %s" % rep[1]) if rep else "pas de reponse"


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "achats"
LANCEUR_ETIQUETTE = "ACHATS"
LANCEUR_CLE = ModuleAchats.NOM_MODULE
LANCEUR_PRIORITE = 20             # apres les forteresses (0) et les tours (10)


def lanceur_config(args, lire=None, sortie=print):
    cfg = tours.lire_config().get(RUBRIQUE)
    if not cfg or not config_valide(cfg):
        raise ArretModule("Module achats : reglages absents ou incomplets. Regle-le dans l'interface (page Achats), puis relance.")
    cfg = dict(cfg)
    cfg.setdefault("commandants", [])
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    return {"prix": prix_engins()}


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    # chateau de depart de chaque royaume : celui deja choisi pour les tours (sinon il est demande une fois)
    base = dict(cfg)
    base["chateaux"] = dict((tours.lire_config().get("tours") or {}).get("chateaux") or {}, **(cfg.get("chateaux") or {}))
    sources = await tours.determiner_sources(jeu, base, sortie, lire, rubrique=RUBRIQUE)
    sortie("")
    sortie("CHATEAUX OU ARRIVENT LES ACHATS :")
    for ligne in tours.lignes_sources(sources):
        sortie("  - " + ligne)
    return ModuleAchats(jeu, cfg, sources, ressources["prix"], partage=partage, sortie=sortie, simulation=args.simulation)
