#!/usr/bin/env python3
"""
recrutement.py : module RECRUTEMENT - remplit les files de la caserne (soldats) et de l'atelier de siege (engins).

Regles (demande du 13/11) :
  - dans le chateau principal de chaque royaume choisi, le bot remplit tous les emplacements libres de la caserne avec les
    types de soldats choisis (a tour de role) et ceux de l'atelier de siege avec les engins choisis ;
  - le NIVEAU n'est pas demande : le bot prend le plus haut niveau debloque dans ce chateau (liste gpc du jeu ; sinon il essaie
    du plus haut au plus bas) ; un niveau maximum peut etre fixe par type (ex. rester au niveau 10) ;
  - les types sont choisis ROYAUME PAR ROYAUME (ex. seulement des melees aux pics de feu) ; si un type choisi n'est pas
    debloque dans le chateau d'un royaume, c'est une erreur de reglage : RIEN n'est recrute dans ce royaume (message clair) ;
  - la quantite n'est pas demandee : c'est le maximum par emplacement de la caserne du chateau (items.json : stackSize du
    batiment, lu dans gca.BD) ; pour l'atelier le bot cherche le maximum accepte et le retient ;
  - une fois la caserne remplie, il demande toujours l'aide de l'alliance (pas pour l'atelier : l'aide n'existe que pour les
    soldats).
Messages du jeu (capture du 13/11) :
  jca {"CID","KID"} -> jaa : contient spl0 (caserne) et spl1 (atelier de siege) du chateau selectionne
  spl : {"PS": production en cours ({} = rien), "QS": [emplacements ; "P" present = occupe], "LID": 0 caserne / 1 atelier}
  bup {"LID","WID","AMT","PO":-1,"PWR":0,"SK":73,"SID": royaume,"AID": chateau} -> {"spl": ..., "grc": ..., "gcu": ...}
  ahr {"ID": LID, "T": 6} : demande d'aide a l'alliance pour le recrutement du chateau selectionne
  sce -> [["CO8", 3319], ["CO3", 6155]] : stock de composants (pousse apres chaque fabrication) ; items.json donne le cout
         de BASE de chaque engin (costComponent1..8). Le cout REEL depend des bonus du joueur (ex. -25 % : 230 -> 173) et le jeu
         ne l'envoie pas : le bot le mesure a la baisse du stock apres chaque fabrication (cout exact de l'engin + reduction
         appliquee aux autres engins) -> il ne lance que ce que le stock permet
  gpc -> {"A": [{"AID", "KID", "U": {"U": [unites debloquees], "L": [verrouillees]}}]}
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tours   # noqa: E402
from tours import ArretModule, ROYAUMES, duree  # noqa: E402

T = tours.T
RUBRIQUE = "recrutement"
CASERNE, ATELIER = 0, 1
NOMS_LIEUX = {CASERNE: "caserne", ATELIER: "atelier de siege"}
BATIMENT_ITEMS = {CASERNE: "Barracks", ATELIER: "Workshop"}
CLES = {CASERNE: "soldats", ATELIER: "engins"}
PILES = (250, 200, 175, 150, 130, 110, 100, 90, 80, 50, 20, 5, 1)   # tailles d'emplacement du jeu, de la plus grande a la plus petite
HAUT = {CASERNE: 250, ATELIER: 130}        # plus grande taille essayee pour trouver le maximum d'un emplacement
PLANCHER = {CASERNE: 50, ATELIER: 20}      # taille d'essai quand celle du batiment n'est pas connue
BONUS_AIDE = 1.2                           # l'aide de l'alliance ajoute 20 % aux soldats d'un emplacement
CODE_SANS_BATIMENT = 87                    # "Le batiment requis pour ceci n'existe pas" (13/11) : vaut pour CE niveau de soldat
                                           # (ex. niveau 11 pas debloque), pas forcement pour la caserne -> niveau en dessous
MEMOIRE_NIVEAU = 7 * 86400                 # un niveau appris est reessaye plus haut au bout de 7 jours
FICHIER_MEMOIRE = os.path.join(tours.DOSSIER_CAPTURES, "recrutement.json")
PAUSE = 300                     # verification de chaque chateau toutes les 5 min
PAUSE_REFUS = 900               # file refusee (ressources, pieces...) : laissee de cote 15 min
TYPE_AIDE = 6                   # ahr T=6 : aide au recrutement
TYPES_PRINCIPAUX = (1, 12)      # chateau principal (grand empire) / chateau d'un royaume
# noms affiches (interface, journal) ; un type absent garde son nom du jeu
NOMS = {"MeadRanger": "Valkyrie pisteuse", "MeadShieldMaiden": "Vierge au bouclier", "MeadBow": "Valkyrie franc-tireuse",
        "MeadMace": "Protecteur du nord",
        "SceatFameShield": "Tour de gloire", "SceatAttWallMelee": "Canon lance-grappin", "SceatAttMoatAttRange": "Hwacha",
        "SceatAttWallAttRange": "Mortier de siege", "SceatSuppAttWaves": "Chariot de guerre",
        "SceatSuppAttPowerYard": "Lance-flammes d'assaut"}


def nom_type(t):
    return NOMS.get(t, t)


def nom_monde(kid):
    return ROYAUMES.get(kid, "royaume %s" % kid)


AVANT_POSTES = (1, 2, 3)        # avant-postes du grand empire (vert), numerotes par ordre de creation (identifiant)
TYPE_AVANT_POSTE = 4
FICHIER_CHATEAUX = os.path.join(tours.DOSSIER_CAPTURES, "chateaux.json")


def cle_ap(n):
    return "ap%d" % n


def nom_lieu(cle, c=None):
    """Royaume (0..3) ou avant-poste ("ap1".."ap3") -> nom pour le journal."""
    if isinstance(cle, str) and cle.startswith("ap"):
        return "avant-poste %s%s" % (cle[2:], " (%d,%d)" % (c["x"], c["y"]) if c else "")
    return nom_monde(cle)


def cles_config(cfg):
    return list(cfg.get("royaumes") or []) + [cle_ap(n) for n in cfg.get("avant_postes") or []]


def charger_catalogue(fichier=None):
    """items.json -> {lieu: {type: [(niveau, wodID), ...] du plus haut au plus bas niveau}}."""
    with open(fichier or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        unites = json.load(f)["units"]
    cat = {CASERNE: {}, ATELIER: {}}
    for u in unites:
        try:
            wid = int(u["wodID"])
        except (KeyError, TypeError, ValueError):
            continue
        if not u.get("type"):
            continue
        if u.get("name") == BATIMENT_ITEMS[CASERNE] and u.get("role") in ("melee", "ranged"):
            lieu = CASERNE
        elif u.get("name") == BATIMENT_ITEMS[ATELIER]:
            lieu = ATELIER
        else:
            continue
        try:
            niveau = int(u.get("level") or 0)
        except (TypeError, ValueError):
            niveau = 0
        cat[lieu].setdefault(u["type"], []).append((niveau, wid))
    for lieu in cat:
        for t in cat[lieu]:
            cat[lieu][t].sort(reverse=True)
    return cat


def charger_couts(fichier=None):
    """items.json -> {wodID: {"CO3": 1, "CO8": 1}} : composants necessaires pour fabriquer UN engin."""
    with open(fichier or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        unites = json.load(f)["units"]
    res = {}
    for u in unites:
        try:
            c = {"CO" + k[len("costComponent"):]: int(v) for k, v in u.items() if k.startswith("costComponent") and int(v) > 0}
            if c:
                res[int(u["wodID"])] = c
        except (KeyError, TypeError, ValueError):
            continue
    return res


def charger_piles(fichier=None):
    """items.json -> {wodID du batiment caserne: soldats par emplacement}."""
    with open(fichier or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        batiments = json.load(f)["buildings"]
    res = {}
    for b in batiments:
        try:
            if b.get("name") == BATIMENT_ITEMS[CASERNE] and b.get("stackSize"):
                res[int(b["wodID"])] = int(b["stackSize"])
        except (TypeError, ValueError):
            continue
    return res


def charger_batiments(fichier=None):
    """items.json -> ({wodID du batiment: (lieu, niveau)}, {wodID de l'unite: niveau de batiment demande})."""
    with open(fichier or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        d = json.load(f)
    lieux = {v: k for k, v in BATIMENT_ITEMS.items()}
    bat, requis = {}, {}
    for b in d["buildings"]:
        try:
            if b.get("name") in lieux:
                bat[int(b["wodID"])] = (lieux[b["name"]], int(b.get("level") or 0))
        except (KeyError, TypeError, ValueError):
            continue
    for u in d["units"]:
        try:
            if u.get("name") in lieux and u.get("buildingLevel"):
                requis[int(u["wodID"])] = int(u["buildingLevel"])
        except (KeyError, TypeError, ValueError):
            continue
    return bat, requis


def lire_memoire(fichier):
    try:
        with open(fichier, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def reglage(cfg, kid):
    r = (cfg.get("reglages") or {}).get(str(kid))
    return r if isinstance(r, dict) else {}


def _liste_valide(v):
    if not isinstance(v, list):
        return False
    for e in v:
        if not isinstance(e, dict) or not isinstance(e.get("type"), str) or not e["type"]:
            return False
        n = e.get("niveau_max")
        if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 0):
            return False
    return True


def config_valide(cfg):
    try:
        roy, ap = cfg.get("royaumes", []), cfg.get("avant_postes", [])
        if not (isinstance(roy, list) and all(k in ROYAUMES for k in roy) and isinstance(ap, list)
                and all(n in AVANT_POSTES for n in ap) and (roy or ap)):
            return False
        for k in cles_config(cfg):
            r = reglage(cfg, k)
            if not (_liste_valide(r.get("soldats", [])) and _liste_valide(r.get("engins", []))):
                return False
            if not (r.get("soldats") or r.get("engins")):
                return False
        return True
    except AttributeError:
        return False


def _texte_liste(liste):
    return ", ".join(nom_type(e["type"]) + (" (niveau %d au plus)" % e["niveau_max"] if e.get("niveau_max") is not None else "")
                     for e in liste)


def resume_config(cfg):
    lignes = []
    for k in cles_config(cfg):
        r = reglage(cfg, k)
        morceaux = []
        if r.get("soldats"):
            morceaux.append("caserne : " + _texte_liste(r["soldats"]))
        if r.get("engins"):
            morceaux.append("atelier de siege : " + _texte_liste(r["engins"]))
        lignes.append("%s - %s" % (nom_monde(k) + " (chateau principal)" if isinstance(k, int) else nom_lieu(k), " ; ".join(morceaux)))
    lignes.append("plus haut niveau debloque, emplacements remplis au maximum, aide de l'alliance demandee a chaque fois")
    return lignes


def lire_file(spl):
    """Bloc spl -> (emplacements libres, occupes, quelque chose sans aide demandee). Rien en cours = 1 place de plus."""
    if not isinstance(spl, dict) or not isinstance(spl.get("QS"), list):
        return None
    qs = [s for s in spl["QS"] if isinstance(s, dict)]
    occupes = [s["P"] for s in qs if isinstance(s.get("P"), dict) and s["P"]]
    en_cours = spl.get("PS") if isinstance(spl.get("PS"), dict) and spl.get("PS") else None
    libres = len(qs) - len(occupes) + (0 if en_cours else 1)
    sans_aide = any(not p.get("RAH") for p in occupes + ([en_cours] if en_cours else []))
    return libres, len(occupes) + (1 if en_cours else 0), sans_aide


def lire_contenu(spl):
    """Bloc spl -> [(wodID, quantite lancee)] de ce qui est deja dans la file (en cours + en attente). L'aide de l'alliance (RAH)
    ajoute 20 % apres coup (190 -> 228, 80 -> 96 ; captures du 13/11) : on retrouve la quantite lancee en divisant par 1,2."""
    if not isinstance(spl, dict):
        return []
    res = []
    for e in [spl.get("PS")] + [s.get("P") for s in spl.get("QS") or [] if isinstance(s, dict)]:
        if isinstance(e, dict) and isinstance(e.get("WID"), int) and isinstance(e.get("TUA"), int):
            res.append((e["WID"], int(round(e["TUA"] / BONUS_AIDE)) if e.get("RAH") else e["TUA"]))
    return res


def lire_debloques(payload):
    """Reponse gpc -> {chateau: set(unites debloquees)}."""
    d = T.json_ou_none(payload)
    out = {}
    for a in (d or {}).get("A") or [] if isinstance(d, dict) else []:
        if isinstance(a, dict) and isinstance(a.get("AID"), int) and isinstance(a.get("U"), dict):
            out[a["AID"]] = {w for w in a["U"].get("U") or [] if isinstance(w, int)}
    return out


class ModuleRecrutement:
    NOM_MODULE = "RECRUTEMENT"

    def __init__(self, jeu, cfg, chateaux, catalogue, partage=None, sortie=print, horloge=time.time, simulation=False,
                 piles=None, couts=None, batiments=None, memoire=None):
        self.batiments, self.requis = batiments or ({}, {})   # {wodID batiment: (lieu, niveau)}, {wodID unite: niveau demande}
        self.fichier_memoire = memoire
        self.niveau_bat = {}                    # (chateau, lieu) -> niveau du batiment (None = absent)
        self.bons = set()                       # (chateau, wodID) deja acceptes par le jeu
        self.connu = {}                         # (chateau, type) -> wodID du plus haut niveau accepte (memoire)
        self.couts = couts or {}                # {wodID: {composant: quantite par engin}}
        self.composants = None                  # {composant: stock} ; None = pas encore donne par le jeu
        self.couts_reels = {}                   # {wodID: {composant: cout reel mesure}}
        self.reduction = 1.0                    # cout reel / cout de base, mesure sur la derniere fabrication
        self.mesure = None                      # (wodID, quantite, stock avant) de la fabrication en cours de mesure
        self.essai_un = {}                      # wodID -> stock lors du dernier essai "1 engin pour savoir" refuse
        self.noms_wid = {}
        if isinstance(getattr(jeu, "ecouteurs", None), list):
            jeu.ecouteurs.append(self._ecouter)
        self.jeu, self.cfg, self.catalogue = jeu, cfg, catalogue
        self.sortie, self.horloge, self.simulation = sortie, horloge, simulation
        self.partage = partage or tours.Partage()
        self.chateaux = chateaux                # {royaume ou "apN": {"id","x","y","kid"}}
        self.debloques = None                   # {chateau: set(wodID)} ; None = pas encore lu / pas donne par le jeu
        self.tour = {}                          # (chateau, lieu) -> rang du prochain type (a tour de role)
        self.refus = {}                         # (chateau, lieu) -> pas de nouvel essai avant
        self.mauvais = set()                    # (chateau, wodID) refuses par le jeu (niveau pas debloque)
        self.sans_aide_atelier = False
        self.piles = piles or {}                # {wodID caserne: soldats par emplacement}
        self.pile = {}                          # (chateau, lieu) -> quantite par emplacement acceptee par le jeu
        self.dates_niveau = {}
        self.bornes = {}                        # (chateau, lieu) -> (taille acceptee, taille max possible) pendant la recherche
        self.plafond = {}                       # (chateau, lieu) -> plus petite taille refusee par le jeu
        self.base_vue = {}                      # (chateau, lieu) -> plus grosse quantite vue dans la file (deja acceptee)
        self.exclus = {}                        # royaume -> raison (erreur de reglage : rien n'y est recrute)
        self.verifie = False
        self._charger_memoire()
        self.niveau_max_actif = False           # option retiree le 13/11 : toujours le plus haut niveau debloque
        self.sce_demande = False
        self.base = {}                          # (chateau, caserne) -> taille de base d'un emplacement (niveau du batiment)
        self.dits = {}
        self.nb_attaques = 0
        self.nb_recrutements = 0

    # ------------------------------------------------------------------ memoire (evite de redemander ce que le jeu a deja refuse)
    def _charger_memoire(self):
        self.pile_sure = set()
        if not self.fichier_memoire:
            return
        d = lire_memoire(self.fichier_memoire)
        t = self.horloge()
        try:
            for cle, n in (d.get("pile") or {}).items():
                cid, lieu = cle.split(":")
                self.pile[(int(cid), int(lieu))] = int(n)
                self.pile_sure.add((int(cid), int(lieu)))
            for cle, v in (d.get("plafond") or {}).items():
                cid, lieu = cle.split(":")
                self.plafond[(int(cid), int(lieu))] = int(v)
            for cle, (lo, hi) in (d.get("bornes") or {}).items():
                cid, lieu = cle.split(":")
                self.bornes[(int(cid), int(lieu))] = (int(lo), int(hi))
            for cle, (wid, quand) in (d.get("niveaux") or {}).items():
                cid, typ = cle.split(":", 1)
                if t - float(quand) < MEMOIRE_NIVEAU:
                    self.connu[(int(cid), typ)] = int(wid)
                    self.bons.add((int(cid), int(wid)))
                    self.dates_niveau[(int(cid), typ)] = float(quand)
        except (ValueError, TypeError, AttributeError):
            pass

    def _sauver_memoire(self):
        if not self.fichier_memoire:
            return
        d = {"pile": {"%d:%d" % k: n for k, n in self.pile.items() if k in self.pile_sure},
             "niveaux": {"%d:%s" % k: [w, self.dates_niveau.get(k, self.horloge())] for k, w in self.connu.items()},
             "bornes": {"%d:%d" % k: list(v) for k, v in self.bornes.items()},
             "plafond": {"%d:%d" % k: v for k, v in self.plafond.items()}}
        try:
            os.makedirs(os.path.dirname(self.fichier_memoire), exist_ok=True)
            with open(self.fichier_memoire + ".tmp", "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(self.fichier_memoire + ".tmp", self.fichier_memoire)
        except OSError:
            pass

    # ------------------------------------------------------------------ outils
    def _ecouter(self, msg):
        """sce : stock de composants (liste complete ou seulement ceux qui viennent de changer)."""
        try:
            if msg[0] not in ("sce", "gpc") or msg[1] != 0:
                return
        except (TypeError, IndexError):
            return
        if msg[0] == "gpc":                                 # le jeu pousse parfois la liste des unites debloquees
            recu = lire_debloques(msg[2])
            if recu:
                self.debloques = dict(self.debloques or {}, **recu)
            return
        d = T.json_ou_none(msg[2])
        if isinstance(d, list):
            recu = {}
            for e in d:
                if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and isinstance(e[1], (int, float)):
                    self.composants = self.composants or {}
                    self.composants[e[0]] = recu[e[0]] = int(e[1])
            self._mesurer(recu)

    def _mesurer(self, recu):
        """Cout reel = baisse du stock apres la fabrication qu'on vient de lancer (bonus du joueur compris)."""
        if not self.mesure:
            return
        wid, n, avant = self.mesure
        base = self.couts.get(wid) or {}
        if n <= 0 or not base or any(c not in recu or c not in avant for c in base):
            return
        self.mesure = None
        reel = {c: (avant[c] - recu[c]) / n for c in base}
        if any(v <= 0 or v > base[c] * 1.01 for c, v in reel.items()):
            return                                          # autre mouvement de stock en meme temps : mesure ignoree
        self.couts_reels[wid] = reel
        self.reduction = min(1.0, max(reel[c] / base[c] for c in base))
        texte = ", ".join("%s %s" % (("%.1f" % reel[c]).rstrip("0").rstrip("."), c.replace("CO", "composant ")) for c in sorted(reel))
        self._dire_une_fois(("cout", wid), "   cout reel mesure pour %s : %s par engin%s."
                            % (self.noms_wid.get(wid, wid), texte,
                               " (%d %% de reduction)" % round((1 - self.reduction) * 100) if self.reduction < 0.995 else ""))

    def cout(self, wid):
        """Composants par engin : cout mesure, sinon cout de base x reduction deja mesuree sur un autre engin."""
        if wid in self.couts_reels:
            return self.couts_reels[wid]
        base = self.couts.get(wid)
        return {c: q * self.reduction for c, q in base.items()} if base else None

    def possible(self, wid):
        """Nombre d'engins que le stock de composants permet (None = inconnu : on essaie)."""
        cout = self.cout(wid)
        if not cout or self.composants is None or any(c not in self.composants for c in cout):
            return None
        n = min(int(self.composants[c] / q + 1e-9) for c, q in cout.items())
        if n == 0 and self.reduction_inconnue() and all(self.composants[c] > 0 for c in cout) \
                and self.essai_un.get(wid) != tuple(sorted(self.composants.items())):
            return 1                                        # bonus du joueur pas encore mesure : on tente 1 engin pour savoir
        return n

    def reduction_inconnue(self):
        return not self.couts_reels

    def _dire_une_fois(self, cle, texte):
        if self.dits.get(cle) != texte:
            self.dits[cle] = texte
            self.sortie(texte)

    async def _demander(self, cmd, donnees, attendu=None, delai=10):
        await self.jeu.vider()
        await self.jeu.envoyer(cmd, donnees)
        return await self.jeu.attendre(attendu or cmd, delai)

    async def _lire_debloques(self):
        if self.debloques is not None:
            return
        rep = await self._demander("gpc", {}, delai=6)
        self.debloques = lire_debloques(rep[2]) if rep and rep[1] == 0 else {}
        if not self.debloques:
            self.sortie("   liste des unites debloquees pas donnee par le jeu : le bot essaie une fois chaque niveau, du plus haut "
                        "au plus bas, et retient le resultat.")

    def candidats(self, cid, lieu, entree):
        """wodID a essayer pour ce type dans ce chateau, du plus haut niveau au plus bas."""
        niveaux = self.catalogue[lieu].get(entree["type"]) or []
        maxi = entree.get("niveau_max") if self.niveau_max_actif else None
        connus = (self.debloques or {}).get(cid)
        res = []
        for niveau, wid in niveaux:
            if maxi is not None and niveau > maxi:
                continue
            if (cid, wid) in self.mauvais or (connus is not None and wid not in connus):
                continue
            if connus is None and (cid, entree["type"]) in self.connu and niveau > next(
                    (n for n, w in niveaux if w == self.connu[(cid, entree["type"])]), niveau):
                continue                                    # niveau au-dessus de celui deja appris : pas redemande
            res.append((niveau, wid))
        return res

    # ------------------------------------------------------------------ cycle
    def _exclure(self, kid, raison):
        if kid not in self.exclus:
            self.exclus[kid] = raison
            self.sortie("ERREUR DE REGLAGE au %s : %s. RIEN n'est recrute dans ce royaume ; corrige la page Recrutement puis "
                        "relance." % (nom_lieu(kid, self.chateaux.get(kid)), raison))

    def verifier(self):
        """Au lancement : un type choisi pas debloque dans le chateau d'un royaume -> ce royaume est laisse de cote."""
        self.verifie = True
        for kid in cles_config(self.cfg):
            c = self.chateaux.get(kid)
            if c is None:
                continue
            connus = (self.debloques or {}).get(c["id"])
            if connus is None:
                continue                                    # liste pas donnee : verifie au premier recrutement
            for lieu in (CASERNE, ATELIER):
                manquants = [nom_type(e["type"]) + (" (niveau %d au plus)" % e["niveau_max"] if e.get("niveau_max") is not None else "")
                             for e in reglage(self.cfg, kid).get(CLES[lieu]) or [] if not self.candidats(c["id"], lieu, e)]
                if manquants:
                    self._exclure(kid, "%s pas debloque%s dans ce chateau (%s)" % (", ".join(manquants), "s" if len(manquants) > 1 else "",
                                                                             NOMS_LIEUX[lieu]))
                    break

    async def cycle(self):
        await self._lire_debloques()
        if self.composants is None and not self.sce_demande:
            self.sce_demande = True                         # stock de composants (sinon connu a la premiere fabrication)
            rep = await self._demander("sce", {}, delai=5)
            if rep:
                self._ecouter(rep)
        if not self.verifie:
            self.verifier()
        bloques = getattr(self.partage, "royaumes_bloques", None) or ()
        for kid in cles_config(self.cfg):
            c = self.chateaux.get(kid)
            if c is None or c["kid"] in bloques or kid in self.exclus:
                continue
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                return 5
            await self._chateau(kid, c)
        return PAUSE

    async def _chateau(self, kid, c):
        t = self.horloge()
        lieux = [l for l in (CASERNE, ATELIER) if reglage(self.cfg, kid).get(CLES[l]) and self.refus.get((c["id"], l), 0) <= t]
        if not lieux:
            return
        rep = await self._demander("jca", {"CID": c["id"], "KID": c["kid"]}, attendu="jaa", delai=15)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if not isinstance(d, dict) or "gui" not in d:
            self._dire_une_fois(("jaa", kid), "   %s : chateau pas lu (pas de reponse du jeu), nouvel essai au passage suivant."
                                % nom_lieu(kid, self.chateaux.get(kid)))
            return
        self.dits.pop(("jaa", kid), None)
        if (c["id"], CASERNE) not in self.base:               # taille de base d'un emplacement = niveau de la caserne
            try:
                tailles = [self.piles[b[0]] for b in d["gca"]["BD"] if isinstance(b, list) and b and b[0] in self.piles]
            except (KeyError, TypeError):
                tailles = []
            self.base[(c["id"], CASERNE)] = max(tailles) if tailles else 0
        if not self._batiments_ok(kid, c, d):
            return
        for lieu in lieux:
            if kid not in self.exclus:
                await self._remplir(kid, c, lieu, d.get("spl%d" % lieu))

    def _batiments_ok(self, kid, c, d):
        """Avant toute demande : le batiment existe et son niveau suffit pour les types choisis (sinon erreur de reglage)."""
        if not self.batiments:
            return True
        try:
            presents = [self.batiments[b[0]] for b in d["gca"]["BD"] if isinstance(b, list) and b and b[0] in self.batiments]
        except (KeyError, TypeError):
            return True                                     # batiments illisibles : on ne bloque pas
        for lieu in (CASERNE, ATELIER):
            liste = reglage(self.cfg, kid).get(CLES[lieu]) or []
            if not liste:
                continue
            niveaux = [n for l, n in presents if l == lieu]
            self.niveau_bat[(c["id"], lieu)] = max(niveaux) if niveaux else None
            if not niveaux:
                self._exclure(kid, "pas de %s dans ce chateau" % NOMS_LIEUX[lieu])
                return False
            for e in liste:
                demandes = [self.requis[w] for _, w in self.catalogue[lieu].get(e["type"]) or [] if w in self.requis]
                if demandes and min(demandes) > max(niveaux):
                    self._exclure(kid, "%s demande un(e) %s niveau %d (niveau %d dans ce chateau)"
                                  % (nom_type(e["type"]), NOMS_LIEUX[lieu], min(demandes), max(niveaux)))
                    return False
        return True

    async def _remplir(self, kid, c, lieu, spl):
        cid = c["id"]
        etat = lire_file(spl)
        if etat is None:
            self._exclure(kid, "pas de %s dans ce chateau" % NOMS_LIEUX[lieu])
            return
        liste = reglage(self.cfg, kid)[CLES[lieu]]
        # ce qui est deja dans la file (lance par le bot ou a la main) a ete accepte par le jeu : ces niveaux sont debloques, et la
        # plus grosse quantite vue est la taille d'un emplacement (bonus compris) -> rien a chercher, aucun refus
        contenu = lire_contenu(spl)
        appris = False
        for wid, _ in contenu:
            if (cid, wid) not in self.bons:
                self.bons.add((cid, wid))
                for t, niveaux in self.catalogue[lieu].items():
                    if any(w == wid for _, w in niveaux):
                        connu = self.connu.get((cid, t))
                        rang = lambda w: next((n for n, x in niveaux if x == w), -1)
                        if connu is None or rang(wid) > rang(connu):
                            self.connu[(cid, t)] = wid
                            self.dates_niveau[(cid, t)] = self.horloge()
                            appris = True
        plafond = self.plafond.get((cid, lieu))
        contenu = [(w, q) for w, q in contenu if plafond is None or q < plafond]   # tailles deja refusees : bonus termine
        vu = max((q for _, q in contenu), default=0)
        # ce qu'on voit dans la file a ete accepte par le jeu : c'est un MINIMUM (le bot a pu lancer moins que le maximum, ou le
        # stock limiter) ; le maximum se trouve par la recherche, une fois, et reste memorise
        if vu > self.base_vue.get((cid, lieu), 0):
            self.base_vue[(cid, lieu)] = vu
        if (cid, lieu) in self.pile_sure and vu > self.pile[(cid, lieu)]:
            self.pile_sure.discard((cid, lieu))             # le maximum retenu etait trop bas : on recherche au-dessus
            self.bornes[(cid, lieu)] = (vu, min(HAUT[lieu], self.plafond.get((cid, lieu), 10 ** 9) - 1))
            appris = True
        if appris:
            self._sauver_memoire()
        # quantite par emplacement : le plus grand nombre deja accepte ici ; sinon on part du haut et on descend tant que le
        # jeu refuse (au-dessus du maximum du batiment, ou pas assez de ressources)
        quantite = self.pile.get((cid, lieu), PILES[0])
        ajoutes, essais, essais_stock = [], 0, 0
        while etat[0] > 0 and essais < 12:
            essais += 1
            choix, sans_stock = None, []
            for i in range(len(liste)):                       # prochain type recrutable, a tour de role
                rang = (self.tour.get((cid, lieu), 0) + i) % len(liste)
                cands = self.candidats(cid, lieu, liste[rang])
                if cands and self.possible(cands[0][1]) == 0:
                    sans_stock.append(nom_type(liste[rang]["type"]))   # pas assez de composants : type suivant
                    continue
                if cands:
                    choix = (rang, liste[rang], cands[0])
                    break
            if choix is None and sans_stock:
                self._dire_une_fois((cid, lieu, "stock"), "   %s, %s : pas assez de composants pour %s ; nouvel essai des qu'il y "
                                    "en a." % (nom_lieu(kid, self.chateaux.get(kid)), NOMS_LIEUX[lieu], ", ".join(sans_stock)))
                break
            if choix is None:
                self._exclure(kid, "aucun des types choisis n'est debloque dans ce chateau (%s)" % NOMS_LIEUX[lieu])
                break
            rang, entree, (niveau, wid) = choix
            nom = "%s%s" % (nom_type(entree["type"]), " niv %d" % niveau if niveau else "")
            if self.simulation:
                self.sortie("[SIMULATION] %s, %s : %d x %s." % (nom_lieu(kid, self.chateaux.get(kid)), NOMS_LIEUX[lieu], quantite, nom))
                self.tour[(cid, lieu)] = rang + 1
                etat = (etat[0] - 1, etat[1] + 1, etat[2])
                continue
            limite = self.possible(wid)
            self.noms_wid[wid] = nom_type(entree["type"])
            # Le moins de demandes refusees possible (chaque refus ouvre une fenetre d'erreur dans le jeu) :
            #  - niveau pas encore accepte ici : UN essai a une taille sure (taille de base du batiment) ;
            #  - niveau sur, taille maximum pas encore connue : recherche par dichotomie entre la taille sure et le haut
            #    (chaque essai accepte remplit un emplacement ; quelques refus au total, une seule fois par chateau, memorise) ;
            #  - tout est connu : la taille connue, puis la moitie (ressources un peu justes), et c'est tout.
            niveau_sur, pile_sure = (cid, wid) in self.bons, (cid, lieu) in self.pile_sure
            base = max(self.base_vue.get((cid, lieu), 0), self.base.get((cid, lieu)) or min([q for (_, l), q in self.pile.items() if l == lieu and (_, l) in self.pile_sure],
                                                      default=0) or PLANCHER[lieu])   # caserne : son niveau ; sinon un autre chateau
            ancienne = None
            if pile_sure:
                tailles = [self.pile[(cid, lieu)]]
            else:
                tailles = [base]
            recherche = niveau_sur and not pile_sure and limite is None
            if recherche:
                lo, hi = self.bornes.get((cid, lieu), (base, HAUT[lieu]))
                hi = min(hi, self.plafond.get((cid, lieu), 10 ** 9) - 1)
                lo = min(max(lo, base), hi)
                tailles = [lo if lo >= hi else (lo + hi + 1) // 2]
            if limite is not None:
                tailles = list(dict.fromkeys(min(x, limite) for x in tailles))
            tailles = [x for x in tailles if x >= 1]
            n, nouveau, raison, code = None, None, "", None
            while tailles:
                n = tailles.pop(0)
                rep = await self._demander("bup", {"LID": lieu, "WID": wid, "AMT": n, "PO": -1, "PWR": 0, "SK": 73, "SID": c["kid"],
                                                   "AID": cid})
                d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
                if isinstance(d, dict) and lire_file(d.get("spl")) is not None:
                    nouveau = lire_file(d["spl"])
                    if self.composants is not None and self.cout(wid):
                        self.mesure = (wid, n, dict(self.composants))       # le sce qui suit donnera le cout reel
                        for co, q in self.cout(wid).items():                # en attendant ce sce
                            if co in self.composants:
                                self.composants[co] = max(0, int(self.composants[co] - q * n))
                        sce = await self.jeu.attendre("sce", 3)             # le jeu l'envoie juste apres
                        if sce:
                            self._ecouter(sce)
                        self.mesure = None
                    g = d.get("gcu") or {}
                    if isinstance(g.get("C1"), (int, float)):
                        self.partage.pieces = int(g["C1"])
                    break
                code = rep[1] if rep else None
                raison = "code %s" % code if rep else "pas de reponse"
                if not rep or code == CODE_SANS_BATIMENT:
                    break
                if recherche or (pile_sure and niveau_sur and limite is None):
                    if self.plafond.get((cid, lieu), 10 ** 9) > n and code is not None:
                        self.plafond[(cid, lieu)] = n           # cette taille est refusee ici : ne plus la reprendre de la file
                        self._sauver_memoire()
                if ancienne is not None and n < ancienne:   # la moitie aussi refusee : ressources, pas la taille -> stop
                    break
                if recherche and n > lo:                    # trop gros : le maximum est en dessous
                    hi = n - 1
                    self.bornes[(cid, lieu)] = (lo, hi)
                    tailles = [lo if lo >= hi else (lo + hi + 1) // 2]
                elif pile_sure and niveau_sur and limite is None and ancienne is None and n > PLANCHER[lieu]:
                    # la taille connue est refusee (bonus termine ? taille vue dans la file fausse ?) : on la recherche plus bas
                    ancienne = n
                    self.pile_sure.discard((cid, lieu))
                    self.base_vue.pop((cid, lieu), None)
                    recherche, lo, hi = True, PLANCHER[lieu], n - 1
                    self.bornes[(cid, lieu)] = (lo, hi)
                    tailles = [(lo + hi + 1) // 2]
            if nouveau is None and code == CODE_SANS_BATIMENT:
                self.mauvais.add((cid, wid))                # ce niveau n'est pas recrutable ici : le niveau en dessous
                self.bons.discard((cid, wid))
                if self.connu.get((cid, entree["type"])) == wid:
                    del self.connu[(cid, entree["type"])]
                    self._sauver_memoire()
                if self.candidats(cid, lieu, entree):
                    continue
                self._exclure(kid, "%s : aucun niveau recrutable dans ce chateau (le jeu : batiment requis absent, code 87)"
                              % nom_type(entree["type"]))
                break
            if nouveau is None and limite is not None and self.composants is not None:
                self.essai_un[wid] = tuple(sorted(self.composants.items()))   # pas de nouvel essai tant que le stock n'a pas bouge
                if self.reduction_inconnue() or limite <= 1:
                    self.tour[(cid, lieu)] = rang + 1       # pas assez de composants pour celui-ci : au suivant
                    essais_stock += 1
                    if essais_stock <= len(liste):
                        continue
                    break
            if nouveau is None and ancienne is not None:
                self.pile[(cid, lieu)] = ancienne           # refuse meme tout petit : ressources, pas la taille -> on garde
                self.pile_sure.add((cid, lieu))
                self.bornes.pop((cid, lieu), None)
                self.plafond.pop((cid, lieu), None)
            if nouveau is None:
                if not niveau_sur and (self.debloques or {}).get(cid) is None:
                    self.mauvais.add((cid, wid))                # niveau pas debloque ? le niveau en dessous
                    if self.candidats(cid, lieu, entree):
                        continue
                    self.mauvais -= {(cid, w) for _, w in self.catalogue[lieu].get(entree["type"]) or []}
                    raison += ", a tous les niveaux : type pas debloque dans ce chateau, ou"
                self.sortie("   %s, %s : %s refuse par le jeu (%s ressources ou pieces insuffisantes ?) ; nouvel essai dans %s."
                            % (nom_lieu(kid, self.chateaux.get(kid)), NOMS_LIEUX[lieu], nom_type(entree["type"]), raison + " :" if raison[-2:] != "ou" else raison,
                               duree(PAUSE_REFUS)))
                self.refus[(cid, lieu)] = self.horloge() + PAUSE_REFUS
                break
            self.nb_recrutements += 1
            appris = False
            if (cid, wid) not in self.bons:
                self.bons.add((cid, wid))
                if self.connu.get((cid, entree["type"])) != wid:
                    self.connu[(cid, entree["type"])] = wid
                    self.dates_niveau[(cid, entree["type"])] = self.horloge()
                    appris = True
            if recherche:
                lo = max(lo, n)
                self.bornes[(cid, lieu)] = (lo, hi)
                appris = True
                if lo >= hi:                                # taille maximum trouvee
                    self.pile[(cid, lieu)] = lo
                    self.pile_sure.add((cid, lieu))
                    self.bornes.pop((cid, lieu), None)
                    appris = True
            if appris:
                self._sauver_memoire()
            quantite = self.pile.get((cid, lieu), n)
            if recherche and (cid, lieu) not in self.pile_sure:
                self._dire_une_fois((cid, lieu, "taille"), "   %s, %s : recherche du maximum par emplacement (entre %d et %d)."
                                    % (nom_lieu(kid, self.chateaux.get(kid)), NOMS_LIEUX[lieu], lo, hi))
            self.tour[(cid, lieu)] = rang + 1
            ajoutes.append("%d x %s" % (n, nom))
            etat = nouveau
        if ajoutes:
            self.dits.pop((cid, lieu, "stock"), None)
            self.sortie("RECRUTEMENT %s, %s : %s%s." % (nom_lieu(kid, self.chateaux.get(kid)), NOMS_LIEUX[lieu], ", ".join(ajoutes),
                                                        " (file pleine)" if etat[0] <= 0 else ""))
            if etat[2] and lieu == CASERNE:                 # l'aide de l'alliance n'existe que pour les soldats
                await self._aide(kid, lieu)

    async def _aide(self, kid, lieu):
        """Aide de l'alliance pour la file du chateau selectionne (celui qu'on vient de remplir)."""
        rep = await self._demander("ahr", {"ID": lieu, "T": TYPE_AIDE}, attendu="spl", delai=6)
        etat = lire_file(T.json_ou_none(rep[2])) if rep and rep[1] == 0 else None
        if etat is not None:
            self.sortie("   aide de l'alliance demandee (%s, %s)." % (nom_lieu(kid, self.chateaux.get(kid)), NOMS_LIEUX[lieu]))
        elif lieu == ATELIER:
            self.sans_aide_atelier = True                     # pas d'aide pour l'atelier : on ne redemande plus


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "recrutement"
LANCEUR_ETIQUETTE = "RECRUT"
LANCEUR_CLE = ModuleRecrutement.NOM_MODULE
LANCEUR_PRIORITE = 40


def lanceur_config(args, lire=None, sortie=print):
    cfg = tours.lire_config().get(RUBRIQUE)
    if not cfg or not config_valide(cfg):
        raise ArretModule("Module recrutement : reglages absents ou incomplets. Regle-le dans l'interface (page Recrutement), "
                          "puis relance.")
    cfg = dict(cfg)
    cfg["commandants"] = []
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    try:
        return {"catalogue": charger_catalogue(), "piles": charger_piles(), "couts": charger_couts(),
                "batiments": charger_batiments()}
    except (OSError, ValueError, KeyError) as e:
        raise ArretModule("Module recrutement : liste des unites illisible (captures/items.json) : %s" % e)


def chateaux_principaux(chateaux, royaumes):
    """Chateau principal de chaque royaume choisi (a defaut : le premier chateau du royaume)."""
    res = {}
    for kid in royaumes:
        du_royaume = [c for c in chateaux if c["kid"] == kid]
        principaux = [c for c in du_royaume if c.get("type") in TYPES_PRINCIPAUX]
        if principaux or du_royaume:
            res[kid] = (principaux or du_royaume)[0]
    return res


def avant_postes(chateaux):
    """Avant-postes du grand empire, du plus ancien au plus recent (identifiant croissant)."""
    return sorted((c for c in chateaux if c["kid"] == 0 and c.get("type") == TYPE_AVANT_POSTE), key=lambda c: c["id"])


def noter_chateaux(chateaux, fichier=None):
    """Pour l'interface : avant-postes du compte (coordonnees affichees sur la page Recrutement)."""
    try:
        with open(fichier or FICHIER_CHATEAUX, "w", encoding="utf-8") as f:
            json.dump({"avant_postes": [{"n": i + 1, "x": c["x"], "y": c["y"], "id": c["id"]}
                                        for i, c in enumerate(avant_postes(chateaux))]}, f)
    except OSError:
        pass


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    import defense
    chateaux, _ = await defense.lire_compte(jeu)
    if not chateaux:
        raise ArretModule("Module recrutement : liste de tes chateaux illisible.")
    if not args.simulation:
        noter_chateaux(chateaux)
    choisis = chateaux_principaux(chateaux, cfg.get("royaumes") or [])
    aps = avant_postes(chateaux)
    for n in cfg.get("avant_postes") or []:
        if n <= len(aps):
            choisis[cle_ap(n)] = aps[n - 1]
        else:
            sortie("ERREUR DE REGLAGE : avant-poste %d coche mais le compte n'en a que %d. Rien n'y est recrute ; corrige la page "
                   "Recrutement." % (n, len(aps)))
    if not choisis:
        raise ArretModule("Module recrutement : aucun chateau parmi ceux choisis.")
    sortie("")
    sortie("CHATEAUX OU LE BOT RECRUTE :")
    for kid, c in choisis.items():
        sortie("  - %s" % (ROYAUMES.get(kid, kid) + " : chateau (%d,%d)" % (c["x"], c["y"]) if isinstance(kid, int) else nom_lieu(kid, c)))
    return ModuleRecrutement(jeu, cfg, choisis, ressources["catalogue"], partage=partage, sortie=sortie,
                             simulation=args.simulation, piles=ressources.get("piles"), couts=ressources.get("couts"),
                             batiments=ressources.get("batiments"), memoire=None if args.simulation else FICHIER_MEMOIRE)
