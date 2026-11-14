#!/usr/bin/env python3
"""
montee.py : module "Tours lev max" : MONTER UNE TOUR au niveau voulu (71 par defaut).

Reglages (interface, page Tours lev max) : rubrique "montee" de config.json
  {"kid": 3, "x": 470, "y": 443, "commandants": [20, 21, 22], "passes": ["MS5", "MS4", "MS3"], "niveau_vise": 71}
Module a part (case "Tours lev max" du tableau de bord) : seul, ou avec le module tours et les autres. L'armee de chaque attaque
est celle reglee dans le module tours pour ce royaume (remplissage automatique ou preselections, cheval, echelles), calculee sur
le VRAI niveau de la tour a chaque attaque ; le module tours n'a pas besoin de tourner.

Fonctionnement (demande du joueur, 14/11) :
  - SALVES : quand la tour est libre et qu'aucune attaque n'est en route, tous les commandants dedies revenus partent l'un apres
    l'autre (4,2 s d'ecart : le jeu ignore une attaque partie moins de 4 s apres la precedente). Les arrivees sont aussi espacees
    d'au moins 4,2 s (le bot connait le trajet de chaque commandant), pour laisser le temps de sauter le blocage entre deux combats.
  - A CHAQUE COMBAT (message cat de l'armee : la tour avec ses victoires et son blocage), le bot saute aussitot le blocage
    (~3 h, moins avec un commandant qui le reduit : 9180 s avec -15 %) avec les passe-temps choisis (1 h, 30 min, 10 min),
    combines pour gacher le moins de temps. Il lit le blocage exact, jamais de rubis.
  - Quand la derniere attaque de la salve est arrivee, nouvelle salve avec les commandants revenus, jusqu'au niveau vise.
  - Le niveau : la carte donne le NOMBRE DE VICTOIRES de la tour, converti en niveau selon le royaume (combat.niveau_reel) :
    feu 112 victoires = niveau 71, sables 112 = 61, glacier 154 = 51 (fin des donnees du jeu, items.json "dungeons").
  - Commandants : le module tours ne les prend jamais ; le module forteresses peut en garder (reservation, comme avec ceux du
    module tours) quand une forteresse devient attaquable bientot ; ils reviennent a la montee ensuite. Le module tours ne
    touche pas non plus a la tour en montee.
  - Arrets : niveau vise atteint (le module s'arrete, ses commandants sont rendus aux autres modules ; ensuite, au demarrage,
    il dit que la tour est deja au niveau vise et ne demarre pas) ;
    plus de passe-temps choisis en stock, armee qui perd, plus de soldats : pause avec un message.
Messages du jeu (capture du 14/11) :
  msd {"X","Y","MID":-1,"NID":-1,"MST":"MS5","KID":"0"} -> msd {"AI":[2,x,y,-1,victoires,blocage restant,royaume]}
  + sce [["MS5", stock restant]] + push gaa {"AI":[[...la tour...]]} ; cat (combat) : A.M.SA = la tour apres le combat.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combat           # noqa: E402
import tours            # noqa: E402
import transferts       # noqa: E402
from tours import ArretModule, PlusDeSoldats, ROYAUMES, T, duree   # noqa: E402

RUBRIQUE = "montee"
PASSES = {"MS5": 3600, "MS4": 1800, "MS3": 600}        # seulement 1 h, 30 min, 10 min (choix du joueur)
NOMS_PASSES = {c: transferts.NOMS_PASSES[c] for c in PASSES}
PASSES_DEFAUT = ["MS5", "MS4", "MS3"]
NIVEAU_VISE = 71
NIVEAU_MAX_SAISIE = 90
ESPACEMENT = 4.2           # s minimum entre deux arrivees (= entre deux departs)
ESPACEMENT_MAX = 10        # attaque arrivee pendant le blocage : l'ecart grandit de 2 s, jusqu'a 10 s
TOLERANCE = 30             # blocage restant accepte sans passe-temps (attendre 30 s plutot que gacher 10 min)
MARGE_ARRIVEE = 1.5        # le blocage doit etre fini 1,5 s avant l'arrivee de la prochaine attaque
ATTENTE_COMBAT = 15        # s apres l'arrivee prevue : sans nouvelles du combat, la tour est relue
SAUTS_MAX = 24             # passe-temps au plus d'un coup (4 h en 10 min)
BLOCAGE_MAX = 4 * 3600     # blocage plus long : temps anormal, pas de passe-temps
PERTES_MAX = 3             # defaites de suite -> pause (armee trop faible ?)
SANS_HAUSSE_MAX = 5        # combats de suite sans victoire comptee alors que la tour s'est bloquee -> arret (niveau max ?)
REFUS_MAX = 5              # attaques refusees de suite -> pause
PAUSE_PROBLEME = 600       # pause apres un probleme (plus de passe-temps, defaites, refus)
FENETRE_RETOUR = 30        # salve en cours : un commandant qui revient dans les 30 s la rejoint (sinon la salve est close)
COMMANDANT_REFUSE = tours.COMMANDANT_REFUSE
CODE_PAS_PRETE = tours.CODE_PAS_PRETE


DU_MONDE = {0: "du vert", 1: "des sables", 2: "du glacier", 3: "du feu"}


def entier(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def config_valide(cfg):
    try:
        return (isinstance(cfg, dict) and entier(cfg.get("kid")) in ROYAUMES
                and entier(cfg.get("x")) is not None and 0 <= cfg["x"] <= 2000 and entier(cfg.get("y")) is not None and 0 <= cfg["y"] <= 2000
                and tours.commandants_valides(cfg.get("commandants"))
                and isinstance(cfg.get("passes"), list) and cfg["passes"] and all(p in PASSES for p in cfg["passes"])
                and len(set(cfg["passes"])) == len(cfg["passes"])
                and entier(cfg.get("niveau_vise", NIVEAU_VISE)) is not None and 1 <= cfg.get("niveau_vise", NIVEAU_VISE) <= NIVEAU_MAX_SAISIE)
    except (AttributeError, TypeError):
        return False


def niveau(kid, victoires):
    return combat.niveau_reel(kid, 2, max(0, int(victoires or 0)))


def victoires_pour(kid, niv, plafond=5000):
    """Moins de victoires qu'il faut a une tour du royaume pour etre au niveau `niv` (None si jamais)."""
    for v in range(plafond + 1):
        if niveau(kid, v) >= niv:
            return v
    return None


def max_victoires_par_royaume(chemin=None):
    """Victoires de la derniere tour connue de chaque royaume (items.json, rubrique dungeons) : {royaume: victoires}."""
    try:
        with open(chemin or tours.FICHIER_ITEMS, encoding="utf-8") as f:
            d = json.load(f)
        res = {}
        for e in d.get("dungeons") or []:
            k, v = int(e.get("kID")), int(e.get("countVictories"))
            if k in ROYAUMES and v >= 0:
                res[k] = max(res.get(k, 0), v)
        return res
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def objectif(kid, niveau_vise, max_v=None):
    """-> (niveau vise effectif, victoires a atteindre) : le niveau demande, sans depasser le dernier niveau du royaume."""
    if max_v is not None:
        niveau_vise = min(niveau_vise, niveau(kid, max_v))
    v = victoires_pour(kid, niveau_vise)
    if max_v is not None and (v is None or v > max_v):
        v = max_v
    return niveau_vise, v


def texte_passes(liste):
    return ", ".join(NOMS_PASSES[c] for c in sorted(liste, key=PASSES.get, reverse=True))


def texte_utilises(compte):
    return ", ".join("%d x %s" % (n, NOMS_PASSES[c]) for c, n in sorted(compte.items(), key=lambda x: -PASSES[x[0]]) if n) or "aucun"


def resume_config(cfg, max_v=None):
    kid = cfg["kid"]
    niv, v = objectif(kid, cfg.get("niveau_vise", NIVEAU_VISE), max_v)
    return ["tour %s (%d,%d), montee jusqu'au niveau %d (%s victoires)" % (ROYAUMES[kid], cfg["x"], cfg["y"], niv, v if v is not None else "?"),
            "commandants dedies : %s (le module tours ne les prend pas ; les forteresses peuvent en garder avant une forteresse)"
            % ";".join(str(p) for p in cfg["commandants"]),
            "salves : tous les commandants revenus partent a 4,2 s d'ecart ; blocage saute a chaque combat avec %s (jamais de rubis)"
            % texte_passes(cfg["passes"]),
            "armee : reglages du module tours pour ce royaume, calculee sur le vrai niveau de la tour"]


class ModuleMontee(tours.ModuleTours):
    NOM_MODULE = "MONTEE"
    NOM_CIBLE = "tour"

    def __init__(self, jeu, cfg, sources, effets=None, max_v=None, **kw):
        kw.setdefault("journal", os.path.join(tours.DOSSIER_CAPTURES, "journal_montee.txt"))
        super().__init__(jeu, cfg, sources, effets, **kw)
        self.kid, self.x, self.y = cfg["kid"], cfg["x"], cfg["y"]
        self.cle = (self.kid, self.x, self.y)
        self.max_v = max_v
        self.niveau_vise, self.cible_v = objectif(self.kid, cfg.get("niveau_vise", NIVEAU_VISE), max_v)
        self.etat_tour = None        # (victoires, blocage, instant de lecture) : dernier etat connu de la tour
        self.v_combat = None         # victoires apres le dernier combat traite
        self.vol = []                # attaques en route : {"pos", "lid", "arrivee", "tt"}
        self.combats = []            # combats de nos armees pas encore traites : {"lid", "pos", "v", "T", "t"}
        self.salve = None            # None, ou {"lances": n, "debut": t, "fermee": bool}
        self.derniere_arrivee = float("-inf")
        self.espacement = ESPACEMENT
        self.tt = {}                 # commandant -> trajet aller observe (s)
        self.stock = {}              # passe-temps -> stock (message sce du jeu)
        self.passes_vides = set()
        self.utilises = {c: 0 for c in PASSES}
        self.victoires_gagnees = 0
        self.pertes = self.sans_hausse = self.refus_de_suite = 0
        self.pause_jusqua = float("-inf")
        self.raison_pause = ""
        self.fini = False
        self.cats_vus = set()
        self.nb_combats = 0
        self._ecouteur = isinstance(getattr(jeu, "ecouteurs", None), list)
        if self._ecouteur:
            jeu.ecouteurs.append(self._ecouter)

    # ------------------------------------------------------------------ etat de la tour (messages du jeu)
    def _ecouter(self, msg):
        try:
            cmd, code, payload = msg[0], msg[1], msg[2]
        except (TypeError, IndexError):
            return
        if code != 0 or not isinstance(payload, str):
            return
        if cmd == "sce":
            d = T.json_ou_none(payload)
            for e in d if isinstance(d, list) else []:
                if isinstance(e, list) and len(e) == 2 and e[0] in PASSES and entier(e[1]) is not None:
                    self.stock[e[0]] = e[1]
                    if e[1] > 0:
                        self.passes_vides.discard(e[0])
            return
        marque = "[2,%d,%d," % (self.x, self.y)
        if marque not in payload.replace(" ", ""):
            return
        d = T.json_ou_none(payload)
        if not isinstance(d, dict):
            return
        if cmd == "cat":
            self._combat(d)
        elif cmd in ("gaa", "msd", "adi"):
            if "KID" in d and d["KID"] != self.kid:
                return
            ai = d.get("AI") if cmd != "adi" else (d.get("gaa") or {}).get("AI")
            entrees = [ai] if isinstance(ai, list) and ai and not isinstance(ai[0], list) else ai if isinstance(ai, list) else []
            for e in entrees:
                if self._est_la_tour(e):
                    self._noter_etat(e[4], e[5])

    def _est_la_tour(self, e):
        return (isinstance(e, list) and len(e) >= 6 and e[0] == 2 and e[1] == self.x and e[2] == self.y
                and (len(e) < 7 or e[6] == self.kid) and entier(e[4]) is not None and isinstance(e[5], (int, float)))

    def _noter_etat(self, v, blocage):
        self.etat_tour = (v, blocage, self.horloge())

    def _combat(self, d):
        """Message cat : une de nos armees est arrivee sur la tour ; SA = la tour apres le combat, M.TT = duree du retour."""
        m = (d.get("A") or {}).get("M") or {}
        sa = m.get("SA") or []
        if not self._est_la_tour(sa) or m.get("D") != 1 or m.get("KID", self.kid) != self.kid:
            return
        mid = m.get("MID")
        if mid in self.cats_vus:
            return
        self.cats_vus.add(mid)
        t = self.horloge()
        self._noter_etat(sa[4], sa[5])
        lid = ((((d.get("A") or {}).get("UM") or {}).get("L")) or {}).get("ID")
        if lid is not None:
            a = next((a for a in self.vol if a["lid"] == lid), None)   # None : armee envoyee a la main (pas par la montee)
        else:
            a = min(self.vol, key=lambda x: x["arrivee"]) if self.vol else None   # commandant inconnu : l'attaque la plus tot
        pos, connu = None, True
        if a is not None:
            self.vol.remove(a)
            pos, connu = a["pos"], a.get("tt_connu", True)
            tt = m.get("TT")
            if isinstance(tt, (int, float)):
                self.libre_a[pos] = t + tt + 1
        self.combats.append({"lid": lid, "pos": pos, "v": sa[4], "T": sa[5], "t": t, "tt_connu": connu})

    def _retour(self, payload):
        """Routeur des messages cat (lanceur commun) : deja vus par l'ecouteur du jeu ; sans ecouteur (tests), lus ici."""
        if not self._ecouteur:
            self._ecouter(("cat", 0, payload))

    def _blocage(self):
        """Secondes de blocage restantes de la tour (<= 0 : attaquable), ou None si son etat n'est pas connu."""
        if self.etat_tour is None:
            return None
        v, b, lu = self.etat_tour
        return b - (self.horloge() - lu)

    def _victoires(self):
        return self.etat_tour[0] if self.etat_tour is not None else None

    def texte_niveau(self, v):
        n = niveau(self.kid, v)
        k = 1
        while niveau(self.kid, v + k) == n and k < 5000:
            k += 1
        return "niveau %d (%d victoires, encore %d pour le niveau %d)" % (n, v, k, n + 1)

    def progres(self):
        """Pour l'interface (bot_etat.json)."""
        v = self._victoires()
        return {"royaume": self.kid, "x": self.x, "y": self.y, "niveau": niveau(self.kid, v) if v is not None else None,
                "victoires": v, "niveau_vise": self.niveau_vise, "victoires_visees": self.cible_v, "en_route": len(self.vol),
                "attaques": self.nb_attaques, "passes": {c: n for c, n in self.utilises.items() if n}, "fini": self.fini,
                "pause": self.raison_pause if self.horloge() < self.pause_jusqua else ""}

    # ------------------------------------------------------------------ reservations
    def commandants_gardes(self):
        """Commandants que les AUTRES modules ne prennent pas : tous ceux de la montee, sauf ceux que le module forteresses
        garde pour une forteresse attaquable bientot (option A : les forteresses peuvent les emprunter)."""
        if self.fini:
            return set()
        f = self.partage.reservations.get("FORTERESSES")
        pris = set(f()) if f is not None else set()
        return set(self.cfg["commandants"]) - pris

    def _autre_urgent(self):
        return any(f() for nom, f in list(self.partage.urgences.items()) if nom != self.NOM_MODULE)

    def _prochaine_arrivee(self, t):
        a = [x["arrivee"] for x in self.vol if x["arrivee"] > t]
        return min(a) if a else None

    def _tolerance(self, t):
        p = self._prochaine_arrivee(t)
        return TOLERANCE if p is None else max(0.0, min(TOLERANCE, p - t - MARGE_ARRIVEE))

    def _candidats(self):
        """Salve ouverte : commandants de la montee libres et pas gardes par un autre module (ceux qui reviennent pendant la
        salve la rejoignent)."""
        if self.salve is None or self.salve.get("fermee"):
            return []
        return self._libres()

    def _sauter_utile(self, t):
        b = self._blocage()
        return (b is not None and b > self._tolerance(t) and t >= self.pause_jusqua
                and (bool(self.vol) or bool(self._candidats()) or (self.salve is None and bool(self._libres()))))

    def urgence(self):
        """Combat imminent ou a traiter, blocage a sauter avant la prochaine arrivee : les autres modules (sauf la defense) laissent
        la place quelques secondes. Les DEPARTS de la salve ne sont pas une urgence (14/11) : sinon, salve continue avec beaucoup de
        commandants, le module tours ne repartait jamais avec ses propres commandants ; il passe entre deux departs."""
        if self.fini:
            return False
        t = self.horloge()
        if self.combats or any(a["arrivee"] - 1.0 <= t <= a["arrivee"] + ATTENTE_COMBAT for a in self.vol):
            return True
        return bool(self.vol) and self._sauter_utile(t)

    # ------------------------------------------------------------------ demarrage
    async def _lire_tour(self):
        """Relit la tour sur la carte (zone de 13 x 13 qui commence a la tour) -> entree de la tour ou None."""
        for essai in range(2):
            await self._vider()
            await self.jeu.envoyer("gaa", {"KID": self.kid, "AX1": self.x, "AY1": self.y, "AX2": self.x + 12, "AY2": self.y + 12})
            rep = await self.jeu.attendre("gaa", 10 if essai == 0 else 6)
            d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
            if isinstance(d, dict) and d.get("KID", self.kid) == self.kid and isinstance(d.get("AI"), list):
                for e in d["AI"]:
                    if isinstance(e, list) and len(e) >= 3 and e[1] == self.x and e[2] == self.y:
                        if self._est_la_tour(e):
                            self._noter_etat(e[4], e[5])
                        return e
                return None
        return False

    async def preparer(self):
        """Verifie la tour (c'est bien une tour, sur ce royaume) et affiche ou elle en est."""
        e = await self._lire_tour()
        nom = "%s (%d,%d)" % (ROYAUMES[self.kid], self.x, self.y)
        if e is False:
            raise ArretModule("le jeu ne donne pas la carte autour de la tour %s : relance le bot." % nom)
        if e is None or not self._est_la_tour(e):
            raise ArretModule("ERREUR DE REGLAGE : il n'y a pas de tour en %s (%s). Corrige les coordonnees dans l'interface "
                              "(page Tours lev max)." % (nom, "rien a cet endroit" if e is None else "autre chose : type %s" % e[0]))
        if e[3] != -1:
            raise ArretModule("ERREUR DE REGLAGE : la tour %s appartient a un joueur." % nom)
        v = e[4]
        self.v_combat = v
        self.partage.cibles_gardees = set(getattr(self.partage, "cibles_gardees", set())) | {self.cle}
        self._noter("TOUR %s : %s ; objectif niveau %d (%d victoires, encore %d)." % (
            nom, self.texte_niveau(v), self.niveau_vise, self.cible_v, max(0, self.cible_v - v)))
        if self.max_v is not None and self.niveau_vise < self.cfg.get("niveau_vise", NIVEAU_VISE):
            self._noter("   (niveau %d demande : le dernier niveau d'une tour %s est %d dans les donnees du jeu)"
                        % (self.cfg.get("niveau_vise", NIVEAU_VISE), DU_MONDE[self.kid], self.niveau_vise))
        if v >= self.cible_v:
            self._terminer("la tour est deja au niveau %d" % niveau(self.kid, v))

    def _terminer(self, raison):
        self.fini = True
        getattr(self.partage, "cibles_gardees", set()).discard(self.cle)
        self._noter("TOUR AU NIVEAU VISE : %s. Montee terminee (%d victoire(s) gagnee(s), passe-temps utilises : %s) ; les "
                    "commandants %s sont rendus aux autres modules." % (raison, self.victoires_gagnees, texte_utilises(self.utilises),
                                                                       ";".join(str(p) for p in self.cfg["commandants"])))
        raise ArretModule("TOUR AU NIVEAU VISE (%s) : le module Tours lev max s'arrete (les autres modules continuent). Pour une "
                          "autre tour, change ses coordonnees dans la page Tours lev max." % raison)

    def _pause(self, secondes, raison):
        self.pause_jusqua = self.horloge() + secondes
        self.raison_pause = raison
        self._noter("   PAUSE de la montee pendant %s : %s" % (duree(secondes), raison))

    # ------------------------------------------------------------------ un passage
    async def cycle(self):
        try:
            return await self._passage()
        except BaseException:
            self.fini = True                      # arret du sous-module (ou bug) : les commandants retournent aux autres modules
            getattr(self.partage, "cibles_gardees", set()).discard(self.cle)
            raise

    async def _passage(self):
        await self._absorber()
        self._traiter_combats()
        t = self.horloge()
        if self._sauter_utile(t):
            await self._sauter()
        self._sans_nouvelles()
        attente = await self._lancer()
        return self._reveil(attente)

    def _traiter_combats(self):
        while self.combats:
            c = self.combats.pop(0)
            self.nb_combats += 1
            avant = self.v_combat if self.v_combat is not None else c["v"]
            gagne = c["v"] - avant
            self.v_combat = max(avant, c["v"])
            qui = "commandant %d" % c["pos"] if c["pos"] is not None else "armee"
            if gagne > 0:
                self.victoires_gagnees += gagne
                self.pertes = self.sans_hausse = 0
                self._noter("COMBAT (%s) : victoire -> tour %s ; blocage %s." % (qui, self.texte_niveau(c["v"]), duree(max(0, c["T"]))))
            elif c["T"] <= 0:
                self.pertes += 1
                self._noter("COMBAT (%s) : DEFAITE, la tour n'a pas ete prise (%d de suite)." % (qui, self.pertes))
                if self.pertes >= PERTES_MAX:
                    self.pertes = 0
                    self._pause(PAUSE_PROBLEME, "%d defaites de suite : l'armee est trop faible pour cette tour ? Regarde les "
                                                "reglages de l'armee %s dans le module tours." % (PERTES_MAX, DU_MONDE[self.kid]))
            else:
                self.sans_hausse += 1
                if c.get("tt_connu", True):
                    avant_e = self.espacement
                    self.espacement = min(ESPACEMENT_MAX, self.espacement + 2)
                    suite = "Les arrivees seront espacees de %.1f s%s." % (self.espacement, "" if self.espacement > avant_e else " (deja au maximum)")
                else:
                    suite = "C'etait le 1er depart de ce commandant (trajet pas encore connu) : son vrai trajet est retenu."
                self._noter("COMBAT (%s) : aucune victoire comptee alors que la tour est bloquee (%s) : attaque arrivee pendant le "
                            "blocage. %s" % (qui, duree(c["T"]), suite))
                if self.sans_hausse >= SANS_HAUSSE_MAX:
                    self._terminer("%d combats de suite sans victoire comptee : la tour ne monte plus (niveau maximum ?)" % SANS_HAUSSE_MAX)
            if self.v_combat >= self.cible_v:
                self._terminer("la tour est au %s" % self.texte_niveau(self.v_combat).split(" (")[0])
        self._sauver_etat()

    def _sans_nouvelles(self):
        """Attaque arrivee depuis plus de 15 s sans message de combat : on ne l'attend plus (son commandant revient selon
        l'estimation) ; la tour sera relue au prochain depart."""
        t = self.horloge()
        for a in [a for a in self.vol if a["arrivee"] + ATTENTE_COMBAT < t]:
            self.vol.remove(a)
            self.etat_tour = None
            self._noter("   commandant %d : pas de nouvelles du combat %s apres son arrivee prevue ; la tour sera relue."
                        % (a["pos"], duree(ATTENTE_COMBAT)))

    # ------------------------------------------------------------------ sauter le blocage
    async def _demander_msd(self, code):
        await self._vider()
        await self.jeu.envoyer("msd", {"X": self.x, "Y": self.y, "MID": -1, "NID": -1, "MST": code, "KID": str(self.kid)})
        return await self.jeu.attendre("msd", 5)

    async def _sauter(self):
        """Passe-temps sur le blocage de la tour, combines pour gacher le moins de temps, jusqu'a ce qu'elle soit attaquable a
        temps pour la prochaine arrivee."""
        dispo = {c: PASSES[c] for c in self.cfg["passes"] if c in PASSES and c not in self.passes_vides and self.stock.get(c, 1) > 0}
        utilises, debut = [], self.horloge()
        b0 = self._blocage()
        for _ in range(SAUTS_MAX):
            t = self.horloge()
            b, tol = self._blocage(), self._tolerance(t)
            if b is None or b <= tol:
                break
            if b > BLOCAGE_MAX:
                self._pause(300, "blocage de la tour anormal (%s) : pas de passe-temps." % duree(b))
                break
            if self.simulation:
                self._noter("[SIMULATION] blocage de %s : les passe-temps %s seraient utilises."
                            % (duree(b), ", ".join(NOMS_PASSES[c] for c in transferts.combinaison(b - tol, dispo)) or "aucun"))
                self._noter_etat(self._victoires(), 0)
                return
            dispo = {c: v for c, v in dispo.items() if self.stock.get(c, 1) > 0}     # stock vu a 0 (message sce) : pas d'essai
            plan = transferts.combinaison(b - tol, dispo)
            if not plan:
                self._pause(PAUSE_PROBLEME, "plus aucun des passe-temps choisis (%s) en stock : la tour reste bloquee %s."
                            % (texte_passes(self.cfg["passes"]), duree(b)))
                break
            code = plan[0]
            rep = await self._demander_msd(code)
            d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
            ai = d.get("AI") if isinstance(d, dict) else None
            if not self._est_la_tour(ai):
                self.passes_vides.add(code)
                dispo.pop(code, None)
                self._noter("   passe-temps %s refuse par le jeu (%s) : plus en stock ? Il n'est plus utilise%s."
                            % (NOMS_PASSES[code], "code %s" % rep[1] if rep else "pas de reponse", ", on complete avec les autres" if dispo else ""))
                continue
            self._noter_etat(ai[4], ai[5])
            utilises.append(code)
            self.utilises[code] += 1
        if utilises:
            b = self._blocage()
            stock = ", ".join("%s : %d" % (NOMS_PASSES[c], self.stock[c]) for c in sorted(set(utilises), key=lambda c: -PASSES[c]) if c in self.stock)
            self._noter("   blocage de %s saute : %s en %.1f s -> %s%s." % (
                duree(b0 or 0), texte_utilises({c: utilises.count(c) for c in set(utilises)}), self.horloge() - debut,
                "tour attaquable" if b is not None and b <= 0 else "encore %s (avant la prochaine arrivee)" % duree(b),
                " (stock : %s)" % stock if stock else ""))

    # ------------------------------------------------------------------ salves
    async def _lancer(self):
        """Une attaque de la salve en cours (ou le debut d'une salve) -> secondes avant de reessayer, ou None."""
        t = self.horloge()
        if t < self.pause_jusqua:
            return min(self.pause_jusqua - t, 30)
        if self.kid in (getattr(self.partage, "royaumes_bloques", None) or ()):
            self._attente_msg("defense", "En attente : chateau %s attaque (module defense), pas de nouvelle attaque." % DU_MONDE[self.kid])
            return 5
        if self.salve is not None and not self.salve.get("fermee") and not self._candidats():
            interdits = self._reserves()
            retours = [self.libre_a.get(p, 0) for p in self.cfg["commandants"]
                       if p not in self.absents and p not in interdits and self.libre_a.get(p, 0) > t]
            if retours and min(retours) - t <= FENETRE_RETOUR:
                return min(retours) - t + 0.1     # un commandant revient tout de suite : il rejoint la salve
            self.salve["fermee"] = True
            if self.salve["lances"]:
                self._noter("   salve envoyee (%d attaque(s)) : la suivante partira quand la derniere sera arrivee." % self.salve["lances"])
        if self.salve is not None and self.salve.get("fermee"):
            if not self.vol and not self.combats:
                self.salve = None                 # derniere attaque arrivee : la salve est finie
            else:
                return None                       # on attend les arrivees (les commandants revenus attendent la salve suivante)
        if self.salve is None:
            if self.vol or self.combats:
                return None
            libres = self._libres()
            if not libres:
                gardes = sorted(p for p in self._reserves() if p in self.cfg["commandants"] and self.libre_a.get(p, 0) <= t)
                if gardes:
                    self._attente_msg("gardes", "En attente : commandant(s) %s gardes pour les forteresses." % ";".join(map(str, gardes)))
                    return 5
                retours = [self.libre_a.get(p, 0) for p in self.cfg["commandants"] if p not in self.absents]
                if not retours:
                    raise ArretModule("aucun des commandants de la montee n'existe dans ta liste.")
                return max(1.0, min(min(retours) - t, 30))
            if (self.echelles.get(self.kid) or tours.ECHELLES_SEUIL) < tours.ECHELLES_SEUIL:
                await self._acheter_echelles([self.kid])   # entre deux salves seulement (jamais pendant les combats)
                t = self.horloge()
            self.salve = {"lances": 0, "debut": t, "fermee": False}
            self._noter("SALVE : %d commandant(s) pret(s) (%s) vers la tour %s (%d,%d)%s." % (
                len(libres), ";".join(map(str, libres)), ROYAUMES[self.kid], self.x, self.y,
                ", les autres la rejoignent a leur retour" if len(libres) < len(self.cfg["commandants"]) else ""))
        cands = self._candidats()
        if not cands:
            return None
        if self._autre_urgent():
            return 1                              # forteresse ou defense : elles passent d'abord
        reste = self.dernier_envoi + tours.DELAI_MIN_ATTAQUES - t
        if reste > 0:
            return reste
        # d'abord un commandant jamais parti (creneau prudent, une seule fois par lancement du bot) : sinon les commandants deja
        # connus, toujours revenus a temps, lui prenaient chaque creneau et il ne partait jamais
        pos = min(cands, key=lambda p: (p in self.tt, cands.index(p)))
        # trajet d'un commandant pas encore parti : estimation prudente (le plus court deja vu - 10 %) ; avec le plus long, un
        # commandant plus rapide arrivait 1 s apres le precedent, pendant le saut du blocage (14/11, commandants 19 et 27)
        tt = self.tt.get(pos) or (min(self.tt.values()) * 0.9 if self.tt else None)
        if tt is not None and t + tt < self.derniere_arrivee + self.espacement:
            return self.derniere_arrivee + self.espacement - (t + tt)   # arrivee trop proche de la precedente : on decale
        b = self._blocage()
        if b is not None and b > 0:
            if b > self._tolerance(t):
                return 0.1                        # blocage a sauter d'abord (fait au passage suivant)
            return b + 0.3                        # presque fini : on attend plutot que gacher un passe-temps
        return await self._attaquer(pos)

    async def _attaquer(self, pos):
        kid, cx, cy = self.kid, self.sources[self.kid]["x"], self.sources[self.kid]["y"]
        nom = "tour %s (%d,%d)" % (ROYAUMES[kid], self.x, self.y)
        rep = await self._demander_infos(cx, cy, self.x, self.y, kid, set())
        if rep and rep[1] == CODE_PAS_PRETE:
            return 1                              # pas encore liberee par le jeu (juste apres un saut) : nouvel essai dans 1 s
        if not rep or rep[1] != 0:
            self.refus_de_suite += 1
            self._noter("   %s : le jeu ne donne pas ses infos d'attaque (%s)." % (nom, "code %s" % rep[1] if rep else "pas de reponse"))
            if self.refus_de_suite >= REFUS_MAX:
                self.refus_de_suite = 0
                self._pause(PAUSE_PROBLEME, "le jeu refuse les attaques sur la tour %d fois de suite." % REFUS_MAX)
            return 5
        adi = T.json_ou_none(rep[2]) or {}
        ai = (adi.get("gaa") or {}).get("AI")
        if not self._est_la_tour(ai):
            raise ArretModule("ERREUR DE REGLAGE : %s n'est plus une tour attaquable (%s)." % (nom, str(ai)[:60]))
        if not self._ecouteur:
            self._noter_etat(ai[4], ai[5])
        # l'etat de la tour vu par l'ecouteur est le plus recent : un combat peut avoir eu lieu juste apres la reponse adi (14/11)
        if self.combats or (self._blocage() or 0) > 0 or ai[5] > 0:
            return 0.1                            # combat juste arrive / tour bloquee : le saut passe d'abord, le depart ensuite
        if isinstance((adi.get("gui") or {}).get("I"), list):
            self.echelles[kid] = combat.inventaire_depuis_adi(adi["gui"]["I"]).get(tours.ID_ECHELLE, 0)
        tour = [2, self.x, self.y, -1, ai[4], ai[5], kid]
        try:
            cra, info = self._construire(adi, pos, tour, kid, (cx, cy))
        except PlusDeSoldats as e:
            self._pause(tours.PAUSE_SANS_STOCK, "%s" % e)
            return 5
        except ValueError as e:
            self.absents.add(pos)
            self._noter("   %s : ce numero est ignore." % e)
            return 0.5
        if info["manques"]:
            for m in info["manques"][:2]:
                self._noter("   ATTENTION : " + m)
        if self.simulation:
            self._noter("[SIMULATION] commandant %d (%s) -> %s niv %s : paquet NON envoye" % (pos, info["nom"], nom, info["niveau"]))
            self.libre_a[pos] = self.horloge() + 120
            self.nb_attaques += 1
            return 1
        reste = self.dernier_envoi + tours.DELAI_MIN_ATTAQUES - self.horloge()
        if reste > 0:
            await self.dormir(reste)
        await self._vider()
        if self.combats or (self._blocage() or 0) > 0:
            return 0.1                            # un combat a eu lieu pendant la preparation : saut d'abord (sinon refus du jeu)
        await self.jeu.envoyer("cra", cra)
        self.dernier_envoi = t = self.horloge()
        rep = await self.jeu.attendre("cra", 15)
        d = T.json_ou_none(rep[2]) if rep else None
        if rep and rep[1] == 0 and isinstance(d, dict) and "AAM" in d:
            m = d["AAM"].get("M") or {}
            tt = m.get("TT") if isinstance(m.get("TT"), (int, float)) else 60
            arrivee = t + tt - (m.get("PT") or 0)
            self.vol.append({"pos": pos, "lid": info["lid"], "arrivee": arrivee, "tt": tt, "tt_connu": pos in self.tt})
            self.tt[pos] = tt
            self.derniere_arrivee = max(self.derniere_arrivee, arrivee)
            self.libre_a[pos] = t + 2 * tt + 15          # borne de securite, remplacee par le vrai retour (message cat)
            self.royaume_du_commandant[pos] = kid
            self.nb_attaques += 1
            self.refus_de_suite = 0
            self.salve["lances"] += 1
            self._noter("ATTAQUE %d : commandant %d (%s) -> %s niv %s (%d victoires), %s, arrivee dans %s."
                        % (self.nb_attaques, pos, info["nom"], nom, info["niveau"], ai[4], tours.texte_vagues(info), duree(tt)))
            self._sauver_etat()
            return 0
        if self.combats or (self._blocage() or 0) > 0:
            self._noter("   commandant %d : depart refuse, la tour venait d'etre bloquee par un combat ; il repart apres le saut." % pos)
            return 0.1
        self.refus_de_suite += 1
        self.libre_a[pos] = self.horloge() + COMMANDANT_REFUSE
        self._noter("   REFUS : commandant %d -> %s : %s ; commandant mis de cote %s." % (
            pos, nom, "code %s, reponse %s" % (rep[1], rep[2][:120]) if rep else "attaque ignoree par le serveur (pas de reponse)",
            duree(COMMANDANT_REFUSE)))
        if self.refus_de_suite >= REFUS_MAX:
            self.refus_de_suite = 0
            self._pause(PAUSE_PROBLEME, "%d attaques refusees de suite par le jeu." % REFUS_MAX)
        self._sauver_etat()
        return 1

    def _reveil(self, attente):
        """Secondes avant le prochain passage : pret pile a chaque arrivee, attente courte pendant un combat attendu."""
        t = self.horloge()
        choix = [attente] if attente is not None else []
        for a in self.vol:
            if a["arrivee"] > t:
                choix.append(a["arrivee"] - t - 0.2)
            else:
                choix.append(0.3)                 # arrivee passee, combat pas encore vu : on relit vite
        if self.salve is None and not self.vol:
            retours = [v for p, v in self.libre_a.items() if p in self.cfg["commandants"] and v > t]
            if retours:
                choix.append(min(retours) - t + 0.5)
        if not choix:
            choix.append(5)
        return max(0.1, min(min(choix), 30))


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "montee"          # "Tours lev max" dans l'interface
LANCEUR_ETIQUETTE = "LEVMAX"
LANCEUR_CLE = ModuleMontee.NOM_MODULE
LANCEUR_PRIORITE = -5          # apres la defense ; avant les forteresses seulement pour sauter le blocage au bon moment


def config_module(tout):
    """Reglages de la montee + armee du module tours pour ce royaume -> configuration du sous-module (ArretModule sinon)."""
    c = tout.get(RUBRIQUE) if isinstance(tout, dict) else None
    if not config_valide(c):
        raise ArretModule("Tours lev max : reglages invalides. Regle-les dans l'interface (page Tours lev max).")
    ct = tout.get("tours") if isinstance(tout.get("tours"), dict) else {}
    kid = c["kid"]
    regl = (ct.get("reglages") or {}).get(str(kid))
    if not tours.reglages_valides(regl):
        raise ArretModule("Tours lev max : l'armee %s n'est pas reglee dans le module tours (page Tours, royaume %s). "
                          "Regle-la (au besoin coche ce royaume, enregistre, puis decoche-le)." % (DU_MONDE[kid], ROYAUMES[kid]))
    cfg = {"commandants": list(c["commandants"]), "royaumes": [kid], "reglages": {str(kid): dict(regl)},
           "chateaux": dict(ct.get("chateaux") or {}), "vagues_base": ct.get("vagues_base", tours.NB_VAGUES_BASE),
           "avec_bonus": bool(ct.get("avec_bonus", False)), "niveau_min": 0, "achat_echelles": bool(ct.get("achat_echelles", False)),
           "kid": kid, "x": c["x"], "y": c["y"], "passes": list(c["passes"]), "niveau_vise": c.get("niveau_vise", NIVEAU_VISE)}
    if "chateaux_manuels" in ct:
        cfg["chateaux_manuels"] = ct["chateaux_manuels"]
    return cfg


def lanceur_config(args, lire=None, sortie=print):
    cfg = config_module(tours.lire_config())
    if getattr(args, "avec_bonus", False):
        cfg["avec_bonus"] = True
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    return {"effets": tours.charger_effets(sortie),
            "fiches_outils": tours.charger_fiches_outils(sortie) if tours.royaumes_en_presets(cfg) else None,
            "unites": tours.charger_unites_presets(cfg, sortie),
            "max_victoires": max_victoires_par_royaume()}


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    sources = await tours.determiner_sources(jeu, cfg, sortie, lire, memoriser=False)
    sortie("")
    sortie("CHATEAU DE DEPART : %s" % tours.lignes_sources(sources)[0])
    presets = None
    if tours.royaumes_en_presets(cfg):
        presets = await tours.lire_presets(jeu, sortie)
        tours.verifier_presets(cfg, presets)
        if not presets:
            raise ArretModule("Mode preselections : les preselections du compte ne sont pas chargees.")
    await tours.assurer_chevaux(jeu, cfg, sortie, lire, args.simulation)
    module = ModuleMontee(jeu, cfg, sources, ressources["effets"], max_v=(ressources.get("max_victoires") or {}).get(cfg["kid"]),
                          sortie=sortie, lire=lire, simulation=args.simulation, confirmer=False, presets=presets,
                          fiches_outils=ressources["fiches_outils"], partage=partage)
    module.unites = ressources.get("unites")
    partage.reservations[ModuleMontee.NOM_MODULE] = module.commandants_gardes
    await module.preparer()
    return module
