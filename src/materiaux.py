#!/usr/bin/env python3
"""
materiaux.py : module MATERIAUX - fabrique les materiaux des engins speciaux.

3 parties (demande du 13/11), chacune avec 6 emplacements (2 gratuits, 4 payants) :
  - raffinerie : bois raffine, pierre raffinee (a partir du bois et de la pierre) ;
  - outilleur : 8 composants (vis, poudre noire, scie, perceuse, pied-de-biche, lanieres de cuir, chaines, plaques) a partir du
    bois/de la pierre raffines et des ressources des royaumes ;
  - butin du dragon : tuiles d'ecailles de dragon, amulettes metamorphes.
Pour chaque partie, le joueur remplit 6 cases (materiau + manuel court 1 h ou long 5 h) ; le bot lance ces fabrications a tour
de role dans les emplacements libres, au plus haut niveau de manuel debloque, seulement si le stock le permet.

Messages du jeu (captures du 08/11 et 13/11) :
  crin {} -> {"CAI": [{"CBI": [{"KID","AID","OID": batiment,"CQID": 1 raffinerie / 2 outilleur / 3 butin du dragon,
              "PS": {"CRID": [en fabrication], "RUT": [secondes restantes de l'emplacement payant]},
              "QS": {"CRID": [en attente], "RUT": [3 emplacements payants]}}],
              "CE": [[616, [recettes debloquees], ...], ...]}]}
  crst {"KID","AID","OID","PWR":0,"CRID": recette} -> meme bloc que crin pour ce batiment ; code 63 = file pleine
  crun {"KID","AID","OID","S":[n],"ST":"production"|"queue"} : achat de l'emplacement payant n (production : 1 ; attente : 1 a 3),
       7 jours (capture du 13/11) -> meme bloc ; un emplacement expire disparait de la liste RUT
  sce -> [["RL", 7070], ["CO3", 6155]] : stocks (bois raffine, composants, tuiles...) apres chaque changement
items.json : craftingRecipes (groupe, niveau, Short/Long, couts), craftingQueues (prix des emplacements payants)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tours   # noqa: E402
from tours import ArretModule, duree  # noqa: E402

T = tours.T
RUBRIQUE = "materiaux"
PARTIES = {"raffinerie": 1, "outilleur": 2, "dragon": 3}           # partie -> craftingQueueId
NOMS_PARTIES = {"raffinerie": "raffinerie", "outilleur": "outilleur", "dragon": "butin du dragon"}
CASES = 6
MANUELS = {"court": "Short", "long": "Long"}
# groupe de recettes -> (partie, cle du stock produit, nom)
MATERIAUX = {1: ("raffinerie", "RL", "Bois raffine"), 2: ("raffinerie", "RS", "Pierre raffinee"),
             3: ("outilleur", "CO1", "Vis"), 4: ("outilleur", "CO2", "Poudre noire"), 5: ("outilleur", "CO3", "Scie"),
             6: ("outilleur", "CO4", "Perceuse"), 7: ("outilleur", "CO5", "Pied-de-biche"),
             8: ("outilleur", "CO6", "Lanieres de cuir"), 9: ("outilleur", "CO7", "Chaines"),
             10: ("outilleur", "CO8", "Plaques de metal"),
             11: ("dragon", "DST", "Tuiles d'ecailles de dragon"), 12: ("dragon", "DC", "Amulettes metamorphes")}
# cout dans items.json -> cle du stock (ressources du chateau, monnaies du compte)
COUTS = {"costWood": "W", "costStone": "S", "costCoal": "C", "costOil": "O", "costGlass": "G", "costIron": "I",
         "costRefinedLumber": "RL", "costRefinedStone": "RS", "costLegendaryToken": "LT", "costLegendaryMaterial": "LM",
         "costSceatToken": "STP", "costC1": "C1", "costSteel": "STEEL", "costDragonGlass": "DG", "costDragonCharm": "DC",
         "costDragonScaleSplinters": "DSS"}
NOMS_STOCK = {"W": "bois", "S": "pierre", "C": "charbon", "O": "huile", "G": "verre", "I": "fer", "RL": "bois raffine",
              "RS": "pierre raffinee", "LT": "jetons legendaires", "LM": "materiaux legendaires", "STP": "jetons",
              "C1": "pieces"}
RESSOURCES_CHATEAU = ("W", "S", "C", "O", "G", "I")
EFFET_RECETTES = 616            # enableCraftingRecipes
CODE_PLEIN = 63
PAUSE = 300
PAUSE_REFUS = 900


def nom_mat(g):
    return MATERIAUX.get(g, (None, None, "materiau %s" % g))[2]


def charger_recettes(fichier=None):
    """items.json -> ({recette: {"groupe","niveau","type","file","duree","couts": {cle: q},"gain"}}, prix des emplacements)."""
    with open(fichier or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        d = json.load(f)
    gains = {}
    for r in d.get("rewards") or []:
        for k, v in r.items():
            if k.startswith("add"):
                try:
                    gains[str(r["rewardID"])] = int(v)
                except (TypeError, ValueError):
                    pass
    recettes = {}
    for r in d["craftingRecipes"]:
        try:
            g = int(r["recipeGroupID"])
            if g not in MATERIAUX or r.get("type") not in MANUELS.values():
                continue
            couts = {COUTS[k]: int(v) for k, v in r.items() if k in COUTS and int(v) > 0}
            if any(k.startswith("cost") and k not in COUTS and k != "costC2" for k in r) or r.get("costC2"):
                continue                                    # cout inconnu ou en rubis : jamais
            recettes[int(r["craftingRecipeId"])] = {
                "groupe": g, "niveau": int(r["level"]), "type": r["type"], "file": int(r["queueTypeId"]),
                "duree": int(r.get("craftingDuration") or 0), "couts": couts,
                "gain": gains.get(str(r.get("rewardIDs", "")).split(",")[0], 0)}
        except (KeyError, TypeError, ValueError):
            continue
    prix = {}
    for q in d.get("craftingQueues") or []:
        try:
            prix[int(q["craftingQueueId"])] = {"production": int(q["productionSlotUnlockCostC1"]),
                                               "attente": [int(x) for x in str(q["queueSlotUnlockCostC1"]).split(",")]}
        except (KeyError, TypeError, ValueError):
            continue
    return recettes, prix


def lire_debloquees(cai):
    """Bloc CAI (crin) -> set des recettes debloquees (effet 616)."""
    res = set()
    for bloc in cai if isinstance(cai, list) else []:
        for e in (bloc or {}).get("CE") or [] if isinstance(bloc, dict) else []:
            if isinstance(e, list) and len(e) >= 2 and e[0] == EFFET_RECETTES and isinstance(e[1], list):
                res.update(x for x in e[1] if isinstance(x, int))
    return res


PAYANTS_PRODUCTION, PAYANTS_ATTENTE = 1, 3


def lire_file(cbi):
    """Bloc d'un batiment -> {"places","occupees","libres","production","attente","payants_actifs","payants"} ; None si illisible.
    production / attente = emplacements payants actifs (un emplacement expire disparait de RUT)."""
    if not isinstance(cbi, dict) or not isinstance(cbi.get("PS"), dict) or not isinstance(cbi.get("QS"), dict):
        return None
    ps, qs = cbi["PS"], cbi["QS"]
    prod = sum(1 for x in ps.get("RUT") or [] if isinstance(x, (int, float)) and x > 0)
    att = sum(1 for x in qs.get("RUT") or [] if isinstance(x, (int, float)) and x > 0)
    places = 2 + prod + att
    occupees = len(ps.get("CRID") or []) + len(qs.get("CRID") or [])
    return {"places": places, "occupees": occupees, "libres": max(0, places - occupees), "production": prod, "attente": att,
            "payants_actifs": prod + att, "payants": PAYANTS_PRODUCTION + PAYANTS_ATTENTE}


def config_valide(cfg):
    try:
        un = False
        for p in PARTIES:
            bloc = cfg.get(p) or {}
            cases = bloc.get("cases", [])
            if not isinstance(cases, list) or len(cases) > CASES or not isinstance(bloc.get("acheter", False), bool):
                return False
            for c in cases:
                if c is None:
                    continue
                if not isinstance(c, dict) or c.get("manuel") not in MANUELS or MATERIAUX.get(c.get("groupe"), (None,))[0] != p:
                    return False
                un = True
        return un
    except AttributeError:
        return False


def resume_config(cfg):
    lignes = []
    for p in PARTIES:
        bloc = cfg.get(p) or {}
        cases = [c for c in bloc.get("cases") or [] if c]
        if cases:
            lignes.append("%s : %s%s" % (NOMS_PARTIES[p], ", ".join("%s (%s)" % (nom_mat(c["groupe"]), c["manuel"]) for c in cases),
                                         " ; emplacements payants achetes automatiquement (s'ils manquent ou ont expire)" if bloc.get("acheter") else ""))
    lignes.append("plus haut manuel debloque ; une fabrication n'est lancee que si le stock le permet")
    return lignes


class ModuleMateriaux:
    NOM_MODULE = "MATERIAUX"

    def __init__(self, jeu, cfg, recettes, prix=None, partage=None, sortie=print, horloge=time.time, simulation=False):
        self.jeu, self.cfg, self.recettes, self.prix = jeu, cfg, recettes, prix or {}
        self.sortie, self.horloge, self.simulation = sortie, horloge, simulation
        self.partage = partage or tours.Partage()
        self.stock = {}                         # monnaies du compte (sce, gcu) : RL, RS, CO1..8, LT, LM, STP, C1...
        self.debloquees = set()
        self.tour = {}                          # partie -> rang de la prochaine case
        self.bloquees = {}                      # (partie, recette) -> stock vu au refus (pas de nouvel essai avant qu'il change)
        self.refus = {}                         # partie -> pas de nouvel essai avant
        self.achat_refuse = {}                  # partie -> pas de nouvel achat avant
        self.dits = {}
        self.nb_attaques = 0
        self.nb_fabrications = 0
        if isinstance(getattr(jeu, "ecouteurs", None), list):
            jeu.ecouteurs.append(self._ecouter)

    # ------------------------------------------------------------------ outils
    def _dire_une_fois(self, cle, texte):
        if self.dits.get(cle) != texte:
            self.dits[cle] = texte
            self.sortie(texte)

    def _ecouter(self, msg):
        try:
            cmd, code, payload = msg[0], msg[1], msg[2]
        except (TypeError, IndexError):
            return
        if code != 0:
            return
        d = T.json_ou_none(payload) if isinstance(payload, str) and (cmd in ("sce", "gcu") or '"gcu"' in payload) else None
        if cmd == "sce" and isinstance(d, list):
            for e in d:
                if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and isinstance(e[1], (int, float)):
                    self.stock[e[0]] = int(e[1])
        elif isinstance(d, dict):
            g = d if cmd == "gcu" else d.get("gcu")
            if isinstance(g, dict) and isinstance(g.get("C1"), (int, float)):
                self.stock["C1"] = int(g["C1"])

    async def _demander(self, cmd, donnees, attendu=None, delai=10):
        await self.jeu.vider()
        await self.jeu.envoyer(cmd, donnees)
        return await self.jeu.attendre(attendu or cmd, delai)

    def recette(self, groupe, manuel):
        """Plus haut niveau debloque pour ce materiau et ce manuel (None si aucun)."""
        typ = MANUELS[manuel]
        ok = [(r["niveau"], rid) for rid, r in self.recettes.items()
              if r["groupe"] == groupe and r["type"] == typ and rid in self.debloquees]
        return max(ok)[1] if ok else None

    def manque(self, rid, ressources):
        """Ce qui manque pour la recette : [] si tout y est ; None dans la liste = stock pas encore connu."""
        res = []
        for k, q in self.recettes[rid]["couts"].items():
            dispo = ressources.get(k) if k in RESSOURCES_CHATEAU else self.stock.get(k)
            if dispo is None:
                res.append(None)
            elif dispo < q:
                res.append("%s %s/%s" % (NOMS_STOCK.get(k, k), fmt(dispo), fmt(q)))
        return res

    # ------------------------------------------------------------------ cycle
    async def cycle(self):
        rep = await self._demander("crin", {}, delai=10)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if not isinstance(d, dict) or not isinstance(d.get("CAI"), list):
            self._dire_une_fois("crin", "   etat des fabrications pas recu : nouvel essai dans 1 min.")
            return 60
        self.dits.pop("crin", None)
        self.debloquees = lire_debloquees(d["CAI"]) or self.debloquees
        batiments = {}
        for bloc in d["CAI"]:
            for b in (bloc or {}).get("CBI") or [] if isinstance(bloc, dict) else []:
                if isinstance(b, dict) and isinstance(b.get("CQID"), int):
                    batiments[b["CQID"]] = b
        ressources = {}
        for p, cq in PARTIES.items():
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                return 5
            cases = [c for c in (self.cfg.get(p) or {}).get("cases") or [] if c]
            if not cases or self.refus.get(p, 0) > self.horloge():
                continue
            b = batiments.get(cq)
            if b is None:
                self._dire_une_fois((p, "abs"), "   %s : batiment absent du compte, rien n'y est fabrique." % NOMS_PARTIES[p])
                continue
            if b.get("KID") in (getattr(self.partage, "royaumes_bloques", None) or ()):
                continue
            if (self.cfg.get(p) or {}).get("acheter"):
                b = await self._acheter(p, b, cq)
            etat = lire_file(b)
            if etat is None or etat["libres"] <= 0:
                continue
            if b.get("AID") not in ressources:
                ressources[b.get("AID")] = await self._ressources(b.get("AID"))
            await self._remplir(p, b, etat, cases, ressources[b.get("AID")])
        return PAUSE

    async def _ressources(self, aid):
        rep = await self._demander("dcl", {"CD": 1}, delai=15)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        for k in (d or {}).get("C") or [] if isinstance(d, dict) else []:
            for a in (k.get("AI") or []) if isinstance(k, dict) else []:
                if isinstance(a, dict) and a.get("AID") == aid:
                    return {r: int(a.get(r) or 0) for r in RESSOURCES_CHATEAU}
        return {}

    async def _acheter(self, p, b, cq):
        """Achete les emplacements payants pas achetes ou expires (production 1, attente 1 a 3) si les pieces suffisent."""
        etat = lire_file(b)
        if etat is None or etat["payants_actifs"] >= etat["payants"] or self.achat_refuse.get(p, 0) > self.horloge():
            return b
        prix = self.prix.get(cq) or {"production": 5000000, "attente": [500000, 3000000, 6500000]}
        a_faire = [("production", i, prix["production"]) for i in range(etat["production"] + 1, PAYANTS_PRODUCTION + 1)]
        a_faire += [("queue", i, prix["attente"][i - 1] if i - 1 < len(prix["attente"]) else prix["attente"][-1])
                    for i in range(etat["attente"] + 1, PAYANTS_ATTENTE + 1)]
        achetes = []
        for st, i, cout in a_faire:
            pieces = self.stock.get("C1", getattr(self.partage, "pieces", None))
            if isinstance(pieces, (int, float)) and pieces < cout:
                self._dire_une_fois((p, "achat"), "   %s : pas assez de pieces pour l'emplacement payant (%s pieces) ; nouvel essai "
                                    "plus tard." % (NOMS_PARTIES[p], fmt(cout)))
                continue                                    # les moins chers d'abord quand meme
            if self.simulation:
                achetes.append("[SIMULATION] %s %d (%s pieces)" % ("production" if st == "production" else "attente", i, fmt(cout)))
                continue
            rep = await self._demander("crun", {"KID": b.get("KID"), "AID": b.get("AID"), "OID": b.get("OID"), "S": [i], "ST": st},
                                       delai=10)
            nouveau = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
            if lire_file(nouveau) is None:
                self.sortie("   %s : achat de l'emplacement payant refuse par le jeu (%s) ; nouvel essai dans %s."
                            % (NOMS_PARTIES[p], "code %s" % rep[1] if rep else "pas de reponse", duree(PAUSE_REFUS)))
                self.achat_refuse[p] = self.horloge() + PAUSE_REFUS
                break
            b = nouveau
            if isinstance(self.stock.get("C1"), int):
                self.stock["C1"] -= cout
            achetes.append("%s %d (%s pieces)" % ("production" if st == "production" else "attente", i, fmt(cout)))
        if achetes:
            self.dits.pop((p, "achat"), None)
            self.sortie("ACHAT %s : emplacement(s) payant(s) %s, pour 7 jours." % (NOMS_PARTIES[p], ", ".join(achetes)))
        return b

    async def _remplir(self, p, b, etat, cases, ressources):
        lances, sans = [], []
        libres = etat["libres"]
        essais = 0
        while libres > 0 and essais < len(cases):
            rang = self.tour.get(p, 0) % len(cases)
            case = cases[rang]
            self.tour[p] = rang + 1
            essais += 1
            rid = self.recette(case["groupe"], case["manuel"])
            nom = "%s (%s)" % (nom_mat(case["groupe"]), case["manuel"])
            if rid is None:
                self._dire_une_fois((p, case["groupe"], case["manuel"]), "   %s : %s pas debloque sur le compte, case ignoree."
                                    % (NOMS_PARTIES[p], nom))
                continue
            manque = self.manque(rid, ressources)
            vu = tuple(sorted(self.stock.items())) + tuple(sorted(ressources.items()))
            if [m for m in manque if m]:
                sans.append("%s : %s" % (nom, ", ".join(m for m in manque if m)))
                continue
            if None in manque and self.bloquees.get((p, rid)) == vu:
                continue                                    # stock inconnu, deja refuse et rien n'a bouge
            if self.simulation:
                self.sortie("[SIMULATION] %s : %s niveau %d." % (NOMS_PARTIES[p], nom, self.recettes[rid]["niveau"]))
                libres -= 1
                essais = 0
                continue
            rep = await self._demander("crst", {"KID": b.get("KID"), "AID": b.get("AID"), "OID": b.get("OID"), "PWR": 0, "CRID": rid},
                                       delai=10)
            nouveau = lire_file(T.json_ou_none(rep[2])) if rep and rep[1] == 0 else None
            if nouveau is None:
                if rep and rep[1] == CODE_PLEIN:
                    break
                self.bloquees[(p, rid)] = vu
                sans.append("%s : refuse par le jeu (%s)" % (nom, "code %s" % rep[1] if rep else "pas de reponse"))
                continue
            for k, q in self.recettes[rid]["couts"].items():          # en attendant les messages du jeu
                if k in RESSOURCES_CHATEAU:
                    ressources[k] = ressources.get(k, 0) - q
                elif k in self.stock:
                    self.stock[k] -= q
            self.nb_fabrications += 1
            lances.append("%s niv %d" % (nom, self.recettes[rid]["niveau"]))
            libres = nouveau["libres"]
            essais = 0
        if lances:
            self.sortie("MATERIAUX %s : %s." % (NOMS_PARTIES[p], ", ".join(lances)))
            self.dits.pop((p, "sans"), None)
        if sans and libres > 0:
            self._dire_une_fois((p, "sans"), "   %s : %d emplacement(s) libre(s) mais rien de fabricable (%s) ; nouvel essai quand le "
                                "stock change." % (NOMS_PARTIES[p], libres, " ; ".join(sans)))


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "materiaux"
LANCEUR_ETIQUETTE = "MATER"
LANCEUR_CLE = ModuleMateriaux.NOM_MODULE
LANCEUR_PRIORITE = 50


def lanceur_config(args, lire=None, sortie=print):
    cfg = tours.lire_config().get(RUBRIQUE)
    if not cfg or not config_valide(cfg):
        raise ArretModule("Module materiaux : reglages absents ou incomplets. Regle-le dans l'interface (page Materiaux), puis "
                          "relance.")
    cfg = dict(cfg)
    cfg["commandants"] = []
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    try:
        recettes, prix = charger_recettes()
    except (OSError, ValueError, KeyError) as e:
        raise ArretModule("Module materiaux : recettes illisibles (captures/items.json) : %s" % e)
    return {"recettes": recettes, "prix": prix}


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    m = ModuleMateriaux(jeu, cfg, ressources["recettes"], ressources.get("prix"), partage=partage, sortie=sortie,
                        simulation=args.simulation)
    try:
        await jeu.envoyer("sce", {})                    # stocks du compte (sinon connus au fil des messages du jeu)
    except Exception:
        pass
    return m
