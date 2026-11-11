#!/usr/bin/env python3
"""
transferts.py : module TRANSFERTS - repartit les ressources entre les royaumes.

Regles (demande du 08/11) :
  - chaque royaume a sa ressource speciale : vert = fer, glacier = charbon, sables = huile, pics de feu = verre. Elle arrive
    dans son royaume (tours, fiefs) ; le bot l'envoie dans les autres royaumes qui en manquent, dans l'ordre vert, glacier,
    sables, feu ;
  - bois et pierre : seulement depuis les royaumes ou le module tours tourne (il les remplit) et quand ils y sont pleins,
    vers les royaumes ou le module tours ne tourne pas et qui en manquent (meme ordre) ;
  - pas de reserve gardee au depart (les tours et les fiefs remplissent au fur et a mesure) ;
  - un seul envoi a la fois vers un meme royaume (le suivant attend la fin du premier) ;
  - un envoi met 2 h a arriver ; option : l'accelerer avec des passe-temps 1 h (2 par envoi).
Messages du jeu (capture du 08/11) : dcl (ressources et capacites de tous les chateaux, tous royaumes), kpi (royaumes
debloques, envois en cours RT), kgt (envoi : chateau source, royaume source, royaume cible, liste [ressource, quantite]),
msk (passe-temps MS5 = 1 h sur l'envoi en cours vers un royaume).
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tours   # noqa: E402
from tours import ArretModule, ROYAUMES, duree  # noqa: E402

T = tours.T
RUBRIQUE = "transferts"
SPECIALE = {0: "I", 2: "C", 1: "O", 3: "G"}         # vert fer, glacier charbon, sables huile, feu verre
ORDRE = (0, 2, 1, 3)                               # vert, glacier, sables, pics de feu
NOMS_RES = {"W": "bois", "S": "pierre", "I": "fer", "C": "charbon", "O": "huile", "G": "verre"}
PLEIN = 0.98                                       # bois/pierre "pleins" a partir de 98 % de la capacite
MIN_ENVOI = 10000
PAUSE = 300                                        # verification toutes les 5 min
PASSE_ENVOI = "MS5"                                # passe-temps 1 h
MAX_PASSES_PAR_ENVOI = 3


def nom_monde(kid):
    return ROYAUMES.get(kid, "royaume %s" % kid)


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


def config_valide(cfg):
    try:
        m = cfg.get("min_envoi", MIN_ENVOI)
        return (isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 100000000 and isinstance(cfg.get("accelerer", False), bool))
    except AttributeError:
        return False


def resume_config(cfg):
    return ["ressource speciale de chaque royaume (vert fer, glacier charbon, sables huile, feu verre) envoyee dans les royaumes "
            "qui en manquent, dans l'ordre vert, glacier, sables, feu",
            "bois et pierre : depuis les royaumes des tours quand ils y sont pleins, vers les royaumes sans tours",
            "envois de %s au moins par ressource ; %s" % (fmt(cfg.get("min_envoi", MIN_ENVOI)),
            "envois acceleres avec des passe-temps 1 h" if cfg.get("accelerer") else "envois non acceleres (2 h de trajet)")]


def lire_stocks(payload):
    """Reponse dcl -> {royaume: [{"id", "res": {W,S,F,C,O,G,I}, "max": {...}}]}."""
    d = T.json_ou_none(payload)
    out = {}
    for k in (d or {}).get("C") or [] if isinstance(d, dict) else []:
        if not isinstance(k, dict) or not isinstance(k.get("KID"), int):
            continue
        for a in k.get("AI") or []:
            if not isinstance(a, dict) or not isinstance(a.get("AID"), int):
                continue
            g = a.get("gpa") or {}
            res = {r: int(a.get(r) or 0) for r in NOMS_RES}
            mx = {r: int(g.get("MR" + r) or 0) for r in NOMS_RES}
            out.setdefault(k["KID"], []).append({"id": a["AID"], "res": res, "max": mx})
    return out


def lire_kpi(d):
    """Infos des transferts -> (royaumes debloques, {royaume cible: {ressource: quantite en route}}, {royaume: secondes restantes})."""
    d = d if isinstance(d, dict) else {}
    if isinstance(d.get("kpi"), dict):
        d = d["kpi"]
    ouverts = {e["KID"] for e in d.get("UL") or [] if isinstance(e, dict) and e.get("U") == 1 and isinstance(e.get("KID"), int)}
    en_route, restant = {}, {}
    for e in d.get("RT") or []:
        if isinstance(e, dict) and isinstance(e.get("KID"), int):
            for r, q in e.get("G") or []:
                en_route.setdefault(e["KID"], {}).setdefault(r, 0)
                en_route[e["KID"]][r] += int(q)
            restant[e["KID"]] = e.get("RS") or 0
    return ouverts, en_route, restant


def principal(chateaux):
    """Chateau d'un royaume qui recoit les envois et dont on compare le remplissage : celui qui a la plus grande capacite."""
    return max(chateaux, key=lambda c: (sum(c["max"].values()), c["id"]))


def plan_transferts(stocks, ouverts, en_route, royaumes_tours, bloques=(), min_envoi=MIN_ENVOI):
    """-> [(chateau source, royaume source, royaume cible, [[ressource, quantite], ...])], dans l'ordre d'envoi."""
    dispo = {k: {c["id"]: dict(c["res"]) for c in cs} for k, cs in stocks.items()}
    manque = {}
    for k, cs in stocks.items():
        p = principal(cs)
        manque[k] = {r: max(0, p["max"][r] - p["res"][r] - (en_route.get(k) or {}).get(r, 0)) for r in NOMS_RES}
    actifs = [k for k in ORDRE if k in stocks and k in ouverts and k not in bloques]
    envois = {}
    for src in actifs:
        ressources = [SPECIALE[src]]
        if src in royaumes_tours:
            p = principal(stocks[src])
            ressources += [r for r in ("W", "S") if p["max"][r] and p["res"][r] >= PLEIN * p["max"][r]]
        for r in ressources:
            for dst in actifs:
                if dst == src or (r in ("W", "S") and dst in royaumes_tours):
                    continue
                besoin = manque[dst][r]
                if besoin < min_envoi:
                    continue
                cid = max(dispo[src], key=lambda c: dispo[src][c][r])
                q = min(besoin, dispo[src][cid][r])
                if q < min_envoi:
                    continue
                dispo[src][cid][r] -= q
                manque[dst][r] -= q
                envois.setdefault((cid, src, dst), []).append([r, q])
    return [(cid, src, dst, g) for (cid, src, dst), g in envois.items()]


class ModuleTransferts:
    NOM_MODULE = "TRANSFERTS"

    def __init__(self, jeu, cfg, partage=None, sortie=print, horloge=time.time, simulation=False, royaumes_tours=None):
        self.jeu, self.cfg, self.sortie, self.horloge, self.simulation = jeu, cfg, sortie, horloge, simulation
        self.partage = partage or tours.Partage()
        self.royaumes_tours = royaumes_tours          # None = lu dans la config des tours si le module tours est lance
        self.nb_attaques = 0
        self.nb_transferts = 0
        self.plus_de_passes = False
        self.occupes = set()

    def _tours(self):
        if self.royaumes_tours is not None:
            return set(self.royaumes_tours)
        if tours.ModuleTours.NOM_MODULE not in self.partage.commandants:
            return set()
        return set((tours.lire_config().get("tours") or {}).get("royaumes") or [])

    async def _demander(self, cmd, donnees, delai=10):
        await self.jeu.vider()
        await self.jeu.envoyer(cmd, donnees)
        return await self.jeu.attendre(cmd, delai)

    async def cycle(self):
        rep = await self._demander("dcl", {"CD": 1}, 15)
        stocks = lire_stocks(rep[2]) if rep and rep[1] == 0 else {}
        if not stocks:
            self.sortie("ressources des chateaux pas recues : nouvel essai dans 1 min.")
            return 60
        rep = await self._demander("kpi", {})
        ouverts, en_route, restant = lire_kpi(T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None)
        self.occupes = {k for k, rs in restant.items() if rs > 0}     # un seul envoi a la fois par royaume cible
        if not ouverts:
            ouverts = set(stocks)
        plan = plan_transferts(stocks, ouverts, en_route, self._tours(), getattr(self.partage, "royaumes_bloques", ()) or (),
                               self.cfg.get("min_envoi", MIN_ENVOI))
        for cid, src, dst, g in plan:
            if self.partage.urgence_prioritaire(self.NOM_MODULE):
                return 5                                   # attaque ou forteresse d'abord : la suite au passage suivant
            if dst in self.occupes:
                continue                                   # envoi deja en route vers ce royaume : au passage suivant
            await self._envoyer(cid, src, dst, g)
        return PAUSE

    async def _envoyer(self, cid, src, dst, g):
        texte = ", ".join("%s %s" % (fmt(q), NOMS_RES[r]) for r, q in g)
        if self.simulation:
            self.sortie("[SIMULATION] envoi %s -> %s : %s." % (nom_monde(src), nom_monde(dst), texte))
            return
        for essai in range(3):
            rep = await self._demander("kgt", {"SCID": cid, "SKID": src, "TKID": dst, "G": g})
            if rep and rep[1] == 0:
                self.nb_transferts += 1
                _, _, restant = lire_kpi(T.json_ou_none(rep[2]))
                self.sortie("TRANSFERT %s -> %s : %s (arrivee dans %s)." % (nom_monde(src), nom_monde(dst), texte,
                                                                           duree(restant.get(dst, 7200))))
                self.occupes.add(dst)
                if self.cfg.get("accelerer") and await self._accelerer(dst):
                    self.occupes.discard(dst)              # arrive tout de suite : le royaume peut en recevoir un autre
                    await asyncio.sleep(2)
                return
            # refus : un envoi vers ce royaume encore en route (le jeu n'en accepte qu'un a la fois par royaume) -> on attend
            rep_kpi = await self._demander("kpi", {})
            _, _, restant = lire_kpi(T.json_ou_none(rep_kpi[2]) if rep_kpi and rep_kpi[1] == 0 else None)
            if dst in restant:
                if essai == 0 and restant[dst] <= 15:            # envoi accelere en train d'arriver : quelques secondes
                    await asyncio.sleep(restant[dst] + 3)
                    continue
                self.sortie("   transfert %s -> %s en attente : un envoi vers le %s est deja en route (%s) ; au passage suivant."
                            % (nom_monde(src), nom_monde(dst), nom_monde(dst), duree(restant[dst])))
                return
            if essai < 2 and all(q >= 2 * MIN_ENVOI for _, q in g):
                g = [[r, q // 2] for r, q in g]            # refuse : peut-etre trop d'un coup -> moitie
                texte = ", ".join("%s %s" % (fmt(q), NOMS_RES[r]) for r, q in g)
                continue
            break
        self.sortie("   transfert %s -> %s refuse par le jeu (%s : %s)." % (nom_monde(src), nom_monde(dst),
                                                                       "code %s" % rep[1] if rep else "pas de reponse", texte))

    async def _accelerer(self, dst):
        """-> True si l'envoi est arrive (le royaume cible est libre pour un autre envoi)."""
        if self.plus_de_passes:
            return False
        for n in range(MAX_PASSES_PAR_ENVOI):
            rep = await self._demander("msk", {"MST": PASSE_ENVOI, "KID": str(dst), "TT": "2"})
            if not rep or rep[1] != 0:
                self.plus_de_passes = True
                self.sortie("   acceleration impossible (%s) : plus de passe-temps 1 h ? Les envois suivants ne seront plus "
                            "acceleres." % ("code %s" % rep[1] if rep else "pas de reponse"))
                return False
            _, _, restant = lire_kpi(T.json_ou_none(rep[2]))
            if restant.get(dst, 0) <= 0:
                self.sortie("   envoi accelere : arrive (%d passe-temps 1 h)." % (n + 1))
                return True
        return False


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "transferts"
LANCEUR_ETIQUETTE = "TRANSF"
LANCEUR_CLE = ModuleTransferts.NOM_MODULE
LANCEUR_PRIORITE = 30


def lanceur_config(args, lire=None, sortie=print):
    cfg = dict(tours.lire_config().get(RUBRIQUE) or {})
    cfg.setdefault("min_envoi", MIN_ENVOI)
    cfg.setdefault("accelerer", False)
    if not config_valide(cfg):
        raise ArretModule("Module transferts : reglages invalides. Regle-le dans l'interface (page Transferts), puis relance.")
    cfg["commandants"] = []
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    return {}


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    return ModuleTransferts(jeu, cfg, partage=partage, sortie=sortie, simulation=args.simulation)
