#!/usr/bin/env python3
"""
khan_defense.py : DEFENSE contre les attaques du Khan (2e partie du module Khan, 16/11).

Ce que fait le bot (reglages : interface, page Khan ; config.json evenements.khan.defense) :
  - au demarrage : lit la defense actuelle du chateau principal (royaume vert) et la GARDE (captures/khan_defense_initiale.json) ;
    achete les malles de pilleur demandees (une seule fois, au demarrage du bot) ; pose la defense Khan choisie (engins du mur,
    portes, douves, cour, % de soldats et % de melee de chaque cote) ; envoie les AUTRES soldats defensifs (ceux qui ne sont
    pas choisis pour defendre) en soutien chez le joueur protege choisi (coordonnees de l'interface), ou ils restent ;
  - pendant le module : la defense est reposee apres chaque attaque du Khan (les engins s'usent) ; un engin qui manque est
    remplace par l'engin de secours de sa categorie ; les soldats defensifs revenus (fin du stationnement de 12 h, retour
    apres une attaque de joueur...) repartent chez le joueur protege ;
  - a la fin (objectif atteint) : les dernieres attaques du Khan en route sont defendues, puis la defense du debut est remise
    et les soldats envoyes chez le joueur protege sont rappeles.

Messages du jeu (captures du 16/11, 07:21 et 11:34 ; code du jeu CastleDefenceDialog) :
  dfc {"CX","CY","AID","KID":-1} -> {"dfw","dfk","dfm","gui":{"I": stock du chateau},...} : toute la defense ;
  dfw {"CX","CY","AID","L":{"S":[[engin,n]...],"UP":% des soldats,"UC":% de melee},"M":{...},"R":{...}} : le mur
      L et R : 5 cases (la 5e est une case de competence, souvent fermee) ; M : 6 cases, les cases 2 et 5 sont les PORTES ;
      UC = part de MELEE (le reste = distance ; fenetre "dialog_defenceUnitcomposition" du jeu : melee = valeur, distance = 100 - valeur) ;
  dfk {"CX","CY","AID","MAUCT","UC":% de melee,"S":[3 cases],"STS":[3 cases de soutien]} : la cour (malles de pilleur) ;
  dfm {"CX","CY","AID","LS":[[engin,n]],"MS":[...],"RS":[...]} : les douves ;
  ibt {"W":108,"A":n,"CX","CY","MID":-1,"KID":0,"PID":739} : achat de malles de pilleur (pieces) ;
  sdi / cds / mcm : infos de soutien, envoi en soutien (WT = heures de stationnement, 12 gratuites), rappel ;
  gam : mouvements ; une attaque du Khan = mouvement de type T 20, attaquant -801 (camp du Khan, SA[0] = 35) ;
  sce [["KM", n]] : medailles du Khan ; [["KT", n]] : tablettes.
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import achats           # noqa: E402
import combat           # noqa: E402
import tours            # noqa: E402
from tours import T, duree   # noqa: E402

FICHIER_INITIALE = os.path.join(tours.DOSSIER_CAPTURES, "khan_defense_initiale.json")
KID = 0
TYPE_KHAN = 20              # champ T d'un mouvement : attaque du Khan
ATTAQUANT_KHAN = -801       # OID / SID de l'attaquant d'une attaque du Khan
ZONE_KHAN = 35
MALLE = 108                 # malle de pilleur (cour) : medailles x 7
PAQUET_MALLE = 739          # paquet de la boutique (capture de l'achat, 16/11)
ACHAT_MALLES_MAX = 1000
QUANTITE_CASE_MAX = 999     # engins au plus par case (jeu)
SEUIL_CASE = 100            # une case sous 100 engins : la defense est remise au plein (toutes les cases en un message)
SEUIL_ACHAT = 3000          # engin achetable en pieces sous 3000 en stock : rachat automatique...
LOT_ACHAT = 3000            # ... de 3000 (paquets de 1000), jamais sous la reserve de pieces (5 millions, comme le module tours)
PAUSE_ACHAT = 600
PRIX_MALLE = 42             # rubis la malle de pilleur (items.json, paquet 739 : packagePriceC2)
HEURES_SOUTIEN = 12         # stationnement gratuit chez un autre joueur (TravelConst.DEFENSE_SUPPORT_DURATION_HOURS_FREE)
TYPES_PAR_COMMANDANT = 10
MIN_PAR_TYPE = 100          # un type de soldat a moins de 100 reste au chateau (pas de commandant gache)
VERIF_SOLDATS = 600         # soldats du chateau reverifies toutes les 10 min (retours)
VERIF_DEFENSE = 60          # defense relue chaque minute (vue en direct dans l'interface ; reposee seulement si besoin)
APRES_ATTAQUE = 8           # secondes apres l'arrivee d'une attaque du Khan avant de reposer la defense
TRAJET_KHAN = 420           # trajet d'une attaque du Khan (s) tant qu'aucun n'a ete vu (400 s le 16/11)
CASES = {"L": 5, "M": 6, "R": 5}
CASES_PORTE = (1, 4)        # au centre : 2e et 5e cases = portes
CASES_COUR = 3

# Engins proposes (noms donnes par le joueur le 16/11). Identifiants deduits des donnees du jeu (items.json) : genre, bonus.
# "mur" = case de mur (flancs et centre), "porte" = case de porte (centre seulement), "douves", "cour".
ENGINS_DEFENSE = {
    110: ("Meurtrière", "mur", "distance +70 %, médailles ×3"),          # icone vue dans l'ecran de defense du joueur (16/11)
    624: ("Archère cruciforme", "mur", "distance +70 %, rubis"),          # armurier : "Arrow slit"
    627: ("Mâchicoulis", "mur", "mur +50 %, rubis"),                     # armurier : "Machicolation"
    637: ("Rochers", "mur", "mur +25 %, pièces"),                        # armurier : "Defense rocks"
    622: ("Herse renforcée", "porte", "porte +75 %, rubis"),              # armurier : "Portcullis"
    111: ("Porte nomade renforcée", "porte", "porte +75 %, médailles ×3"),
    626: ("Porte du château renforcée (porte en bois)", "porte", "porte +35 %, pièces"),   # armurier : "Castle gate reinforcement"
    625: ("Douves enflammées", "douves", "douves +110 %, rubis"),         # armurier : "Fire moat"
    112: ("Douves infernales", "douves", "douves +110 %, médailles ×3"),
    MALLE: ("Malle de pilleur", "cour", "médailles ×7"),
}
# Soldats proposes pour defendre (dans l'ordre du joueur). Les deux premiers existent en plusieurs niveaux : TOUS les niveaux
# comptent ensemble (choisir la Valkyrie franc-tireuse garde les Valkyries de tous niveaux au chateau).
DEFENSEURS = [
    ("valkyrie", "Valkyrie franc-tireuse", "MeadBow", list(range(228, 239)) + [493]),
    ("protecteur", "Protecteur du nord", "MeadMace", list(range(217, 228)) + [489]),
    ("archer_veteran", "Archer composite vétéran", "EliteArrowthrower", [12]),
    ("flamme_veteran", "Porteur de flamme vétéran", "EliteFlamethrower", [11]),
    ("lancier_renegat", "Lancier renégat", "Renegadepikeman", [728]),
    ("javelots_renegat", "Lanceur de javelots renégat", "Renegadespearthrower", [729]),
]
NOMS_DEFENSEURS = {c: n for c, n, _, _ in DEFENSEURS}
IDS_DEFENSEURS = {c: ids for c, _, _, ids in DEFENSEURS}


def _entier(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _case_ok(c, genres):
    return c is None or (isinstance(c, list) and len(c) == 2 and c[0] in ENGINS_DEFENSE and ENGINS_DEFENSE[c[0]][1] in genres
                         and _entier(c[1]) is not None and 1 <= c[1] <= QUANTITE_CASE_MAX)


def genre_case(cote, i):
    return "porte" if cote == "M" and i in CASES_PORTE else "mur"


def defense_valide(d):
    """Reglages de la defense Khan (voir le haut du fichier) -> True / False."""
    try:
        if not isinstance(d, dict):
            return False
        mur = d["mur"]
        for c in "LMR":
            s = mur[c]
            if not (isinstance(s["cases"], list) and len(s["cases"]) == CASES[c]
                    and all(_case_ok(x, (genre_case(c, i),)) for i, x in enumerate(s["cases"]))
                    and _entier(s["soldats"]) is not None and 0 <= s["soldats"] <= 100
                    and _entier(s["melee"]) is not None and 0 <= s["melee"] <= 100):
                return False
        if sum(mur[c]["soldats"] for c in "LMR") != 100:
            return False
        dv = d["douves"]
        if not all(_case_ok(dv[c], ("douves",)) for c in "LMR"):
            return False
        cour = d["cour"]
        if not (isinstance(cour["malles"], list) and len(cour["malles"]) == CASES_COUR
                and all(_entier(q) is not None and 0 <= q <= QUANTITE_CASE_MAX for q in cour["malles"])
                and _entier(cour["melee"]) is not None and 0 <= cour["melee"] <= 100):
            return False
        sec = d["secours"]
        for g in ("mur", "porte", "douves"):
            w = sec.get(g)
            if w is not None and not (w in ENGINS_DEFENSE and ENGINS_DEFENSE[w][1] == g):
                return False
        ds = d["defenseurs"]
        if not (isinstance(ds, list) and ds and all(x in IDS_DEFENSEURS for x in ds) and len(set(ds)) == len(ds)):
            return False
        a = d["abri"]
        if not (_entier(a["x"]) is not None and 0 <= a["x"] <= 2000 and _entier(a["y"]) is not None and 0 <= a["y"] <= 2000):
            return False
        return (_entier(d.get("achat_malles", 0)) is not None and 0 <= d.get("achat_malles", 0) <= ACHAT_MALLES_MAX
                and isinstance(d.get("achat_engins", True), bool))
    except (KeyError, TypeError, AttributeError):
        return False


def _nom(w):
    return ENGINS_DEFENSE.get(w, ("engin %s" % w,))[0]


def resume_defense(d):
    def cases(l):
        r = [("%d x %s" % (c[1], _nom(c[0]))) for c in l if c]
        return " + ".join(r) or "rien"
    m = d["mur"]
    l = ["defense Khan du chateau principal : flanc gauche %s (soldats %d %%, melee %d %%) ; centre %s (soldats %d %%, melee %d %%) ; "
         "flanc droit %s (soldats %d %%, melee %d %%)" % (cases(m["L"]["cases"]), m["L"]["soldats"], m["L"]["melee"],
                                                          cases(m["M"]["cases"]), m["M"]["soldats"], m["M"]["melee"],
                                                          cases(m["R"]["cases"]), m["R"]["soldats"], m["R"]["melee"]),
         "douves : %s ; cour : %d malle(s) de pilleur, melee %d %%" % (
             " / ".join(cases([d["douves"][c]]) for c in "LMR"), sum(d["cour"]["malles"]), d["cour"]["melee"]),
         "engins de secours : %s" % (", ".join("%s -> %s" % (g, _nom(w)) for g, w in d["secours"].items() if w) or "aucun"),
         "soldats qui defendent : %s (tous niveaux) ; les autres soldats defensifs partent chez le joueur protege (%d,%d)" % (
             ", ".join(NOMS_DEFENSEURS[x] for x in d["defenseurs"]), d["abri"]["x"], d["abri"]["y"])]
    if d.get("achat_malles"):
        l.append("achat de %d malle(s) de pilleur au demarrage, une seule fois : %d rubis" % (d["achat_malles"], d["achat_malles"] * PRIX_MALLE))
    if d.get("achat_engins", True):
        l.append("engins achetables en pieces (rochers, porte en bois) : rachetes sous %d en stock, %d d'un coup, jamais sous %d "
                 "pieces ; une case sous %d engins -> defense remise au plein" % (SEUIL_ACHAT, LOT_ACHAT, tours.PIECES_RESERVE, SEUIL_CASE))
    return [tours.sans_accents(x) for x in l]


def soldats_defensifs(chemin_items=None):
    """{identifiant: royaumes autorises ou None} des soldats DEFENSIFS (defense melee + distance > attaque) de items.json."""
    with open(chemin_items or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        items = json.load(f)
    res = {}
    for u in items.get("units", []):
        try:
            if u.get("group") != "Unit" or u.get("role") not in ("melee", "ranged"):
                continue
            att = int(u.get("meleeAttack") or u.get("rangeAttack") or 0)
            if int(u.get("meleeDefence") or 0) + int(u.get("rangeDefence") or 0) > att:
                res[int(u["wodID"])] = combat._cibles_voyage(u.get("allowedToTravel"))
        except (KeyError, TypeError, ValueError):
            continue
    return res


def lire_mouvements_khan(payload, cid):
    """Message gam -> {mid: secondes avant l'arrivee} des attaques du Khan vers le chateau `cid`."""
    d = T.json_ou_none(payload)
    res = {}
    for e in (d.get("M") or []) if isinstance(d, dict) else []:
        m = e.get("M") if isinstance(e, dict) else None
        if not isinstance(m, dict) or _entier(m.get("MID")) is None:
            continue
        ta, sa = m.get("TA") or [], m.get("SA") or []
        khan = m.get("T") == TYPE_KHAN or m.get("OID") == ATTAQUANT_KHAN or (isinstance(sa, list) and sa[:1] == [ZONE_KHAN])
        if khan and isinstance(ta, list) and len(ta) > 3 and ta[3] == cid and m.get("D") != 1:
            tt, pt = m.get("TT") or 0, m.get("PT") or 0
            res[m["MID"]] = max(0, tt - pt)
    return res


def _slots(liste, n):
    r = [list(x) if isinstance(x, list) and len(x) == 2 else [-1, 0] for x in (liste or [])][:n]
    return r + [[-1, 0]] * (n - len(r))


class DefenseKhan:
    """Partie defense du module Khan. `module` = le ModuleKhan (journal, jeu, partage, horloge, simulation)."""

    def __init__(self, module, cfg, chateau, soldats_def):
        self.m = module
        self.cfg = cfg                              # evenements.khan.defense
        self.c = chateau                            # {"id","x","y","kid","type"} : chateau principal du royaume vert
        self.soldats_def = soldats_def              # {id: royaumes} soldats defensifs
        self.gardes = {w for x in cfg["defenseurs"] for w in IDS_DEFENSEURS[x]}
        self.mouvements = {}                        # mid -> instant d'arrivee (attaques du Khan vues dans gam)
        self.attendus = []                          # instants d'arrivee estimes des attaques declenchees pas encore vues
        self.nouveaux = []                          # (instant, mid) : attaques vues dans gam sans declenchement en attente
        self.mouvements_lus = None
        self.vue = None                             # defense vue a la derniere lecture (page "Khan en direct")
        self._envoyes = {}
        self.trajet = TRAJET_KHAN
        self.defenses = 0                           # attaques du Khan arrivees (defendues ou non)
        self.perdues = 0                            # arrivees pendant une attaque de joueur (chateau vide)
        self.reposer_a = None                       # instant ou reposer la defense (apres une attaque du Khan)
        self.defense_posee = 0.0                    # instant de la derniere pose
        self.soldats_lus = float("-inf")
        self.envois = []                            # soutiens partis chez le joueur protege : {"mid","pos"}
        self.initiale = None
        self.manques_notes = set()
        self.achat_pause = {}                       # engin -> pas de nouvel essai d'achat avant
        self.bloque_avant = False

    def _noter(self, texte):
        self.m._noter(tours.sans_accents(texte))

    async def _demander(self, cmd, donnees, delai=15):
        return await self.m._demander(cmd, donnees, delai)

    def _base(self):
        return {"CX": self.c["x"], "CY": self.c["y"], "AID": self.c["id"]}

    # ------------------------------------------------------------------ attaques du Khan en route
    def ecouter(self, cmd, payload):
        if cmd != "gam":
            return
        t = self.m.horloge()
        for mid, reste in lire_mouvements_khan(payload, self.c["id"]).items():
            if mid not in self.mouvements:
                if self.attendus:
                    d = self.attendus.pop(0)
                    self.trajet = max(60, min(3600, reste + (t - (d - self.trajet))))   # trajet observe
                else:
                    self.nouveaux.append((t, mid))     # vue avant la reponse au declenchement (ou deja en route au demarrage)
                self.mouvements[mid] = t + reste
            else:
                self.mouvements[mid] = t + reste
        self._publier()

    def declenche(self):
        """lta accepte : une attaque du Khan part. Le jeu l'annonce (gam) juste avant ou juste apres la reponse au declenchement :
        deja vue -> rien a ajouter ; sinon elle est comptee en route (estimation) jusqu'a ce que gam la montre."""
        t = self.m.horloge()
        self.nouveaux = [(v, mid) for v, mid in self.nouveaux if t - v <= 30 and mid in self.mouvements]
        if self.nouveaux:
            v, mid = self.nouveaux.pop(0)
            self.trajet = max(60, min(3600, self.mouvements[mid] - v))
        else:
            self.attendus.append(t + self.trajet)
        self._publier()

    def _publier(self):
        arr = sorted(list(self.mouvements.values()) + self.attendus)
        ka = getattr(self.m.partage, "khan_arrivees", None)
        if ka is None:
            ka = self.m.partage.khan_arrivees = {}
        ka[self.c["id"]] = arr

    def en_route(self):
        return len(self.mouvements) + len(self.attendus)

    def arrivant_avant(self, limite):
        """Attaques du Khan en route qui arrivent au chateau avant `limite` (instant)."""
        return sorted(a for a in list(self.mouvements.values()) + self.attendus if a <= limite)

    def prochaine(self):
        a = list(self.mouvements.values()) + self.attendus
        return min(a) if a else None

    def _arrivees(self):
        """Attaques du Khan arrivees depuis le dernier passage -> compte des defenses, defense a reposer."""
        t = self.m.horloge()
        vide = self.c["id"] in (getattr(self.m.partage, "chateaux_vides", None) or ())
        arrivees = [mid for mid, a in self.mouvements.items() if a + 3 <= t]
        for mid in arrivees:
            del self.mouvements[mid]
        n_att = len([a for a in self.attendus if a + 60 <= t])          # declenchee mais jamais vue : comptee a l'estimation
        self.attendus = [a for a in self.attendus if a + 60 > t]
        n = len(arrivees) + n_att
        if n:
            if vide:
                self.perdues += n
                self._noter("KHAN : %d attaque(s) du Khan arrivee(s) pendant une attaque de joueur (chateau vide) : perdue(s), "
                            "le bot en relancera." % n)
            else:
                self.defenses += n
                self._noter("KHAN : %d attaque(s) du Khan arrivee(s) sur ton chateau principal (%d defense(s) depuis le lancement)."
                            % (n, self.defenses))
            self.reposer_a = t + APRES_ATTAQUE
            self._publier()
        return n

    # ------------------------------------------------------------------ lecture / pose de la defense
    async def lire(self):
        rep = await self._demander("dfc", dict(self._base(), KID=-1), 20)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if not (isinstance(d, dict) and isinstance(d.get("dfw"), dict) and isinstance(d.get("dfk"), dict) and isinstance(d.get("dfm"), dict)):
            return None
        return d

    def _sauver_initiale(self, d):
        if os.path.exists(FICHIER_INITIALE):
            try:
                with open(FICHIER_INITIALE, encoding="utf-8") as f:
                    vieux = json.load(f)
                if vieux.get("chateau") == self.c["id"]:
                    self.initiale = vieux
                    self._noter("KHAN : defense du debut deja gardee lors d'un lancement precedent (%s) : c'est elle qui sera "
                                "remise a la fin." % vieux.get("date", "?"))
                    return
            except (OSError, ValueError, AttributeError):
                pass
        w, k, m = d["dfw"], d["dfk"], d["dfm"]
        self.initiale = {"chateau": self.c["id"], "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "dfw": {c: {"S": _slots((w.get(c) or {}).get("S"), CASES[c]), "UP": (w.get(c) or {}).get("UP", 33),
                                     "UC": (w.get(c) or {}).get("UC", 50)} for c in "LMR"},
                         "dfk": {"S": _slots(k.get("S"), CASES_COUR), "STS": _slots(k.get("STS"), CASES_COUR),
                                 "UC": k.get("UC", 50), "MAUCT": k.get("MAUCT", 0)},
                         "dfm": {x: _slots(m.get(x), 1) for x in ("LS", "MS", "RS")}}
        try:
            os.makedirs(os.path.dirname(FICHIER_INITIALE), exist_ok=True)
            with open(FICHIER_INITIALE, "w", encoding="utf-8") as f:
                json.dump(self.initiale, f)
        except OSError as e:
            self._noter("   defense du debut pas enregistree sur le disque (%s) : elle est gardee en memoire." % e)
        self._noter("KHAN : defense actuelle du chateau principal gardee (elle sera remise a la fin).")

    @staticmethod
    def _dispo(d):
        """Stock d'engins du chateau : inventaire + engins deja poses (un engin pose n'est plus dans l'inventaire)."""
        stock = combat.inventaire_depuis_adi((d.get("gui") or {}).get("I") or [])
        poses = []
        for c in "LMR":
            poses += (d["dfw"].get(c) or {}).get("S") or []
        poses += (d["dfk"].get("S") or []) + [x for k in ("LS", "MS", "RS") for x in (d["dfm"].get(k) or [])]
        for x in poses:
            if isinstance(x, list) and len(x) == 2 and x[0] not in (-1, None) and isinstance(x[1], int):
                stock[x[0]] = stock.get(x[0], 0) + x[1]
        return stock

    def construire(self, d):
        """Defense voulue -> (dfw, dfk, dfm, manques). Un engin qui manque est complete par l'engin de secours de sa categorie."""
        stock = self._dispo(d)
        manques = []
        sec = self.cfg["secours"]

        def poser(case, genre):
            if not case:
                return [-1, 0]
            w, q = case
            if stock.get(w, 0) >= q:
                stock[w] -= q
                return [w, q]
            s = sec.get(genre)
            if s and stock.get(s, 0) >= q:
                manques.append("%s : plus assez (%d en stock), remplace par %s" % (_nom(w), stock.get(w, 0), _nom(s)))
                stock[s] -= q
                return [s, q]
            reste = stock.get(w, 0)
            if reste > 0:
                manques.append("%s : %d seulement au lieu de %d" % (_nom(w), reste, q))
                stock[w] = 0
                return [w, reste]
            if s and stock.get(s, 0) > 0:
                r = stock[s]
                stock[s] = 0
                manques.append("%s epuise : %d %s a la place" % (_nom(w), r, _nom(s)))
                return [s, r]
            manques.append("%s epuise (et pas d'engin de secours) : case vide" % _nom(w))
            return [-1, 0]

        mur = self.cfg["mur"]
        ouvertes = {c: len((d["dfw"].get(c) or {}).get("S") or []) or CASES[c] for c in "LMR"}
        dfw = dict(self._base())
        for c in "LMR":
            # 5e case des flancs : jamais (16/11 : elle ne sert que contre les seigneurs, le Khan n'en est pas un)
            cases = [poser(x, genre_case(c, i)) if i < ouvertes[c] and not (c != "M" and i == 4) else [-1, 0]
                     for i, x in enumerate(mur[c]["cases"])]
            dfw[c] = {"S": cases, "UP": mur[c]["soldats"], "UC": mur[c]["melee"]}
        malles = []
        for q in self.cfg["cour"]["malles"]:
            if q <= 0:
                malles.append([-1, 0])
                continue
            n = min(q, stock.get(MALLE, 0))
            stock[MALLE] = stock.get(MALLE, 0) - n
            if n < q:
                manques.append("malles de pilleur : %d seulement au lieu de %d" % (n, q))
            malles.append([MALLE, n] if n > 0 else [-1, 0])
        k = d["dfk"]
        dfk = dict(self._base(), MAUCT=k.get("MAUCT", 0), UC=self.cfg["cour"]["melee"], S=malles,
                   STS=_slots(k.get("STS"), CASES_COUR))
        dfm = dict(self._base(), **{x + "S": [poser(self.cfg["douves"][x], "douves")] for x in "LMR"})
        return dfw, dfk, dfm, manques

    async def poser(self, raison=""):
        """Lit la defense et pose la defense Khan (seulement ce qui differe). -> True si la defense est en place."""
        d = await self.lire()
        if d is None:
            self._noter("   KHAN : le jeu ne donne pas la defense du chateau principal : nouvel essai dans 1 min.")
            self.reposer_a = self.m.horloge() + 60
            return False
        if self.initiale is None:
            self._sauver_initiale(d)
        if await self.racheter(d):
            d = await self.lire() or d
        dfw, dfk, dfm, manques = self.construire(d)
        for txt in manques:
            if txt not in self.manques_notes:
                self.manques_notes.add(txt)
                self._noter("   KHAN, defense : %s." % txt)
        ok = await self._envoyer_defense(d, dfw, dfk, dfm)
        self._noter_vue(d, dfw, dfk, dfm)
        self.defense_posee = self.m.horloge()
        self.reposer_a = None
        if ok and raison:
            self._noter("KHAN : defense Khan posee sur le chateau principal (%s)." % raison)
        return ok

    def _noter_vue(self, d, dfw, dfk, dfm):
        """Etat de la defense pour la page "Khan en direct" de l'interface (apres la pose : ce qui a ete envoye remplace ce qui
        etait en place)."""
        try:
            stock = combat.inventaire_depuis_adi((d.get("gui") or {}).get("I") or [])
            envoye = self._envoyes or {}
            mur = {}
            for c in "LMR":
                a = envoye.get("dfw", d["dfw"])[c] if "dfw" in envoye else (d["dfw"].get(c) or {})
                mur[c] = {"S": _slots(a.get("S"), CASES[c])[:4 if c != "M" else 6], "UP": a.get("UP"), "UC": a.get("UC")}
            k = envoye.get("dfk", d["dfk"])
            m = envoye.get("dfm", d["dfm"])
            moins = {}
            if "dfw" in envoye or "dfk" in envoye or "dfm" in envoye:     # engins passes du stock aux cases
                for x in [x for c in "LMR" for x in mur[c]["S"]] + _slots(k.get("S"), CASES_COUR) + [x for c in "LMR" for x in _slots(m.get(c + "S"), 1)]:
                    if x[0] != -1:
                        moins[x[0]] = moins.get(x[0], 0) + x[1]
                for x in [x for c in "LMR" for x in _slots((d["dfw"].get(c) or {}).get("S"), CASES[c])] + _slots(d["dfk"].get("S"), CASES_COUR) \
                        + [x for c in "LMR" for x in _slots(d["dfm"].get(c + "S"), 1)]:
                    if x[0] != -1:
                        moins[x[0]] = moins.get(x[0], 0) - x[1]
            engins = sorted(self._utilises() | {MALLE})
            self.vue = {"t": time.time(), "mur": mur, "cour": {"S": _slots(k.get("S"), CASES_COUR), "UC": k.get("UC")},
                        "douves": {c: _slots(m.get(c + "S"), 1)[0] for c in "LMR"},
                        "stock": [[w, max(0, stock.get(w, 0) - moins.get(w, 0))] for w in engins],
                        "soldats": [[x, NOMS_DEFENSEURS[x], sum(stock.get(w, 0) for w in IDS_DEFENSEURS[x])] for x in self.cfg["defenseurs"]],
                        "autres": sum(n for w, n in stock.items() if w in self.soldats_def and w not in self.gardes),
                        "abri": [self.cfg["abri"]["x"], self.cfg["abri"]["y"]]}
        except (KeyError, TypeError, AttributeError, IndexError):
            pass

    @staticmethod
    def _a_remettre(actuel, voulu):
        """Cases a reposer ? Autre engin, ou case qui descend sous 100 (sous ce qui est voulu si c'est moins). Les engins
        s'usent doucement (999 par case) : on ne renvoie pas la defense pour quelques engins uses."""
        for (wa, qa), (wv, qv) in zip(actuel, voulu):
            if wa != wv or (wv != -1 and qa < min(SEUIL_CASE, qv)):
                return True
        return False

    async def _envoyer_defense(self, d, dfw, dfk, dfm):
        """Une seule demande par partie (mur, cour, douves) : le jeu recoit toujours toutes les cases d'un coup, donc quand une case
        descend sous 100, TOUTES les cases de cette partie sont remises au plein en meme temps."""
        ok = True
        self._envoyes = {}
        actuel_w = {c: (d["dfw"].get(c) or {}) for c in "LMR"}
        if any(self._a_remettre(_slots(actuel_w[c].get("S"), CASES[c]), dfw[c]["S"]) or actuel_w[c].get("UP") != dfw[c]["UP"]
               or actuel_w[c].get("UC") != dfw[c]["UC"] for c in "LMR"):
            if await self._envoyer("dfw", dfw):
                self._envoyes["dfw"] = dfw
            else:
                ok = False
        k = d["dfk"]
        if self._a_remettre(_slots(k.get("S"), CASES_COUR), dfk["S"]) or k.get("UC") != dfk["UC"]:
            if await self._envoyer("dfk", dfk):
                self._envoyes["dfk"] = dfk
            else:
                ok = False
        if any(self._a_remettre(_slots(d["dfm"].get(x + "S"), 1), dfm[x + "S"]) for x in "LMR"):
            if await self._envoyer("dfm", dfm):
                self._envoyes["dfm"] = dfm
            else:
                ok = False
        return ok

    # ------------------------------------------------------------------ rachat des engins achetables en pieces
    def _utilises(self):
        ids = {x[0] for c in "LMR" for x in self.cfg["mur"][c]["cases"] if x}
        ids |= {self.cfg["douves"][c][0] for c in "LMR" if self.cfg["douves"][c]}
        ids |= {w for w in self.cfg["secours"].values() if w}
        return ids

    async def _pieces(self):
        p = getattr(self.m.partage, "pieces", None)
        if p is None:
            rep = await self._demander("gcu", {}, 10)
            d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
            if isinstance(d, dict) and isinstance(d.get("C1"), (int, float)):
                p = self.m.partage.pieces = int(d["C1"])
        return p

    async def racheter(self, d):
        """Engins de la defense achetables en PIECES (armurier : rochers, porte en bois) sous 3000 en stock : rachat de 3000
        (paquets de 1000), seulement si les pieces restent au-dessus de 5 millions. Les engins en rubis ne sont jamais achetes.
        -> True si quelque chose a ete achete."""
        if not self.cfg.get("achat_engins", True) or self.m.simulation:
            return False
        stock = combat.inventaire_depuis_adi((d.get("gui") or {}).get("I") or [])
        prix = getattr(self, "_prix", None) or achats.prix_engins()
        self._prix = prix
        t, achete, selection = self.m.horloge(), False, False
        for w in sorted(self._utilises()):
            if w not in achats.ENGINS or stock.get(w, 0) >= SEUIL_ACHAT or self.achat_pause.get(w, 0) > t:
                continue
            n = 0
            while n < LOT_ACHAT:
                pieces = await self._pieces()
                if pieces is None or pieces - prix[w] * tours.ACHAT_PAR_MESSAGE < tours.PIECES_RESERVE:
                    if not n:
                        self._noter("   KHAN : %s : %d en stock, mais pas assez de pieces pour en racheter (reserve de %d gardee)."
                                    % (_nom(w), stock.get(w, 0), tours.PIECES_RESERVE))
                    self.achat_pause[w] = t + PAUSE_ACHAT
                    break
                if not selection:                                # l'achat arrive dans le chateau selectionne
                    await self._demander("jca", {"CID": self.c["id"], "KID": KID}, 10)
                    selection = True
                rep = await self._demander("sbp", {"PID": achats.ENGINS[w][1], "BT": 0, "TID": 27, "AMT": tours.ACHAT_PAR_MESSAGE,
                                                   "KID": KID, "AID": -1, "PC2": -1, "BA": 0, "PWR": 0, "_PO": -1}, 10)
                r = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
                if not isinstance(r, dict):
                    self._noter("   KHAN : achat de %s refuse (%s) : nouvel essai dans 10 min."
                                % (_nom(w), "code %s" % rep[1] if rep else "pas de reponse"))
                    self.achat_pause[w] = t + PAUSE_ACHAT
                    break
                n += tours.ACHAT_PAR_MESSAGE
                g = r.get("gcu") or {}
                self.m.partage.pieces = int(g["C1"]) if isinstance(g.get("C1"), (int, float)) else pieces - prix[w] * tours.ACHAT_PAR_MESSAGE
            if n:
                achete = True
                self._noter("KHAN : %d %s rachete(s) en pieces (il en restait %d) ; pieces : %d." % (
                    n, tours.sans_accents(_nom(w)), stock.get(w, 0), self.m.partage.pieces or 0))
        return achete

    async def _envoyer(self, cmd, donnees):
        if self.m.simulation:
            self._noter("[SIMULATION] %s %s" % (cmd, json.dumps({k: v for k, v in donnees.items() if k not in ("CX", "CY", "AID")})))
            return True
        rep = await self._demander(cmd, donnees)
        if rep and rep[1] == 0:
            return True
        self._noter("   KHAN : le jeu refuse la defense (%s, %s)." % (cmd, "code %s" % rep[1] if rep else "pas de reponse"))
        return False

    async def acheter_malles(self, n):
        if n <= 0:
            return
        if self.m.simulation:
            self._noter("[SIMULATION] achat de %d malle(s) de pilleur." % n)
            return
        rep = await self._demander("ibt", {"W": MALLE, "A": n, "CX": self.c["x"], "CY": self.c["y"], "MID": -1, "KID": KID,
                                           "PID": PAQUET_MALLE})
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if isinstance(d, dict) and isinstance(d.get("O"), dict):
            self._noter("KHAN : %d malle(s) de pilleur achetee(s) (%d rubis). Le bot n'en rachete plus." % (d["O"].get("A", n), d["O"].get("A", n) * PRIX_MALLE))
        else:
            self._noter("   KHAN : achat des malles refuse par le jeu (%s) : le bot n'essaie plus."
                        % ("code %s" % rep[1] if rep else "pas de reponse"))

    async def restaurer(self):
        """Remet la defense du debut (fichier captures/khan_defense_initiale.json), puis efface le fichier."""
        if not self.initiale:
            return
        b = self._base()
        i = json.loads(json.dumps(self.initiale))
        d = await self.lire()
        if d is not None:                     # engins uses depuis le debut : on remet ce qu'il reste (pas plus que le stock)
            stock = self._dispo(d)
            cases = [x for c in "LMR" for x in i["dfw"][c]["S"]] + i["dfk"]["S"] + [x for k in ("LS", "MS", "RS") for x in i["dfm"][k]]
            for x in cases:
                if x[0] != -1:
                    x[1] = min(x[1], max(0, stock.get(x[0], 0)))
                    stock[x[0]] = stock.get(x[0], 0) - x[1]
                    if x[1] <= 0:
                        x[0], x[1] = -1, 0
        ok = await self._envoyer("dfw", dict(b, **{c: i["dfw"][c] for c in "LMR"}))
        ok &= await self._envoyer("dfk", dict(b, **i["dfk"]))
        ok &= await self._envoyer("dfm", dict(b, **i["dfm"]))
        if ok:
            self._noter("KHAN : defense du debut remise sur le chateau principal.")
            try:
                os.remove(FICHIER_INITIALE)
            except OSError:
                pass
        else:
            self._noter("   KHAN : defense du debut PAS remise entierement (gardee dans captures\\khan_defense_initiale.json, "
                        "elle sera remise au prochain lancement du module Khan).")

    # ------------------------------------------------------------------ soldats envoyes chez le joueur protege
    def _commandants_libres(self, rep_sdi):
        t = self.m.horloge()
        cmds = tours.positions_commandants(rep_sdi)
        part = self.m.partage
        modules = {p for liste in part.commandants.values() for p in liste} | set(self.m.cfg["commandants"])
        pris = {e["pos"] for e in self.envois}
        libres = [(pos, c) for pos, c in enumerate(cmds, 1)
                  if isinstance(c.get("ID"), int) and pos not in pris and pos not in modules and part.libre_a.get(pos, 0) <= t]
        return libres

    async def envoyer_soldats(self):
        """Envoie les soldats defensifs NON choisis chez le joueur protege (coordonnees de l'interface)."""
        self.soldats_lus = self.m.horloge()
        a = self.cfg["abri"]
        rep = await self._demander("sdi", {"TX": a["x"], "TY": a["y"], "SX": self.c["x"], "SY": self.c["y"]}, 20)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if not isinstance(d, dict):
            self._noter("   KHAN : le jeu ne donne pas les infos de soutien vers (%d,%d) (%s) : nouvel essai dans 10 min."
                        % (a["x"], a["y"], "code %s" % rep[1] if rep else "pas de reponse"))
            return
        stock = combat.inventaire_depuis_adi((d.get("gui") or {}).get("I") or [])
        gardes = sum(n for w, n in stock.items() if w in self.gardes)
        a_envoyer = [[w, n] for w, n in stock.items() if w in self.soldats_def and w not in self.gardes and n >= MIN_PAR_TYPE]
        a_envoyer = [wn for wn in a_envoyer if self.soldats_def[wn[0]] is None or KID in self.soldats_def[wn[0]]]
        if not a_envoyer:
            return
        a_envoyer.sort(key=lambda wn: (-wn[1], wn[0]))
        libres = self._commandants_libres(d)
        total = sum(n for _, n in a_envoyer)
        if not libres:
            self._noter("   KHAN : %d soldat(s) defensifs a envoyer chez le joueur protege, mais aucun commandant libre "
                        "(hors commandants des modules) : nouvel essai dans 10 min." % total)
            return
        if self.m.simulation:
            self._noter("[SIMULATION] %d soldat(s) defensifs (%d type(s)) partiraient chez (%d,%d) ; %d soldat(s) choisis restent."
                        % (total, len(a_envoyer), a["x"], a["y"], gardes))
            return
        reste, ptt, envoyes = [list(x) for x in a_envoyer], 1, 0
        for pos, cmd in libres:
            essais = 0
            while reste and essais < 3:
                armee = [list(x) for x in reste[:TYPES_PAR_COMMANDANT]]
                rep = await self._demander("cds", {"SID": self.c["id"], "TX": a["x"], "TY": a["y"], "LID": cmd["ID"], "WT": HEURES_SOUTIEN,
                                                   "HBW": -1, "BPC": 0, "PTT": ptt, "SD": 0, "A": armee})
                r = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
                mv = ((r or {}).get("A") or {}).get("M") if isinstance(r, dict) else None
                if isinstance(mv, dict) and _entier(mv.get("MID")) is not None:
                    self.envois.append({"mid": mv["MID"], "pos": pos})
                    self.m.partage.libre_a[pos] = max(self.m.partage.libre_a.get(pos, 0), self.m.horloge() + HEURES_SOUTIEN * 3600 + 3600)
                    n = sum(q for _, q in armee)
                    envoyes += n
                    reste = reste[TYPES_PAR_COMMANDANT:]
                    self._noter("KHAN : %d soldat(s) defensifs non choisis partis avec le commandant %d chez le joueur protege (%d,%d) "
                                "pour %d h." % (n, pos, a["x"], a["y"], HEURES_SOUTIEN))
                    break
                essais += 1
                if ptt == 1:
                    ptt = 0
                else:
                    break
            if not reste:
                break
        if envoyes and self.vue:
            self.vue["autres"] = max(0, self.vue.get("autres", 0) - envoyes)
        if reste:
            self._noter("   KHAN : %d soldat(s) defensifs pas envoyes (plus de commandant libre ou refus) : nouvel essai dans 10 min."
                        % sum(n for _, n in reste))

    async def rappeler(self):
        for e in self.envois:
            if self.m.simulation:
                continue
            rep = await self._demander("mcm", {"MID": e["mid"]})
            if not rep or rep[1] != 0:
                self._noter("   KHAN : rappel refuse (%s) : ces soldats rentreront seuls a la fin des %d h."
                            % ("code %s" % rep[1] if rep else "pas de reponse", HEURES_SOUTIEN))
            self.m.partage.libre_a[e["pos"]] = self.m.horloge() + 1800
        if self.envois:
            self._noter("KHAN : soldats envoyes chez le joueur protege rappeles.")
        self.envois = []

    # ------------------------------------------------------------------ passage
    async def demarrer(self):
        rep = await self.m._demander("gam", {}, 15)        # mouvements en cours (attaques du Khan deja en route)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if isinstance(d, dict):
            self.ecouter("gam", rep[2])
            self.mouvements_lus = len(d.get("M") or [])
            try:                                           # pour comprendre un oubli (reste sur ce PC, dans captures)
                with open(os.path.join(tours.DOSSIER_CAPTURES, "khan_mouvements.json"), "w", encoding="utf-8") as f:
                    f.write(rep[2])
            except OSError:
                pass
        else:
            self.mouvements_lus = None
            self._noter("   KHAN : le jeu n'a pas donne la liste des mouvements (%s) : attaques du Khan deja en route inconnues."
                        % ("code %s" % rep[1] if rep else "pas de reponse"))
        await self.poser("demarrage")
        await self.acheter_malles(self.cfg.get("achat_malles", 0))
        if self.cfg.get("achat_malles"):
            await self.poser()
        await self.envoyer_soldats()

    async def passage(self):
        """A chaque passage du module : arrivees, defense a reposer, soldats revenus a renvoyer."""
        self._arrivees()
        t = self.m.horloge()
        bloque = KID in (getattr(self.m.partage, "royaumes_bloques", None) or ())
        if bloque:                                         # attaque d'un joueur : le module Defense s'en occupe
            self.bloque_avant = True
            return
        if self.bloque_avant:                              # attaque du joueur passee : on reprend la defense Khan
            self.bloque_avant = False
            self.reposer_a = t
            self.soldats_lus = float("-inf")
            self._noter("KHAN : attaque du joueur passee, la defense Khan reprend.")
        p = self.prochaine()
        proche = p is not None and p - t < 15                # pas de changement juste avant une arrivee
        if not proche and ((self.reposer_a is not None and t >= self.reposer_a) or t - self.defense_posee >= VERIF_DEFENSE):
            await self.poser()
        if not proche and t - self.soldats_lus >= VERIF_SOLDATS:
            await self.envoyer_soldats()

    def progres(self):
        t = self.m.horloge()
        return {"khan_en_route": self.en_route(), "defenses": self.defenses, "perdues": self.perdues,
                "soutiens": len(self.envois), "vue": self.vue,
                "khan_dans": [round(a - t) for a in sorted(list(self.mouvements.values()) + self.attendus)]}
