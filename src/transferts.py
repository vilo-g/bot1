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
  - 3 types de ressources (base : bois, pierre, nourriture ; mondes : fer, charbon, huile, verre ; miel/hydromel, pas
    geres) : un envoi ne contient qu'un seul type (plusieurs ressources du meme type ensemble), sinon le jeu refuse (code 2) ;
  - le vert d'abord ; si le module materiaux tourne, ce qu'il lui faut au vert (de quoi remplir 2 fois ses 6 emplacements par
    batiment) n'est jamais envoye ailleurs (demande du 13/11) ;
  - un seul envoi a la fois vers un meme royaume (le suivant attend la fin du premier) ;
  - un envoi met 1 h 36 a 2 h a arriver ; option : l'accelerer avec les passe-temps choisis (1 min a 1 h), combines pour
    gacher le moins de temps possible (1 h 36 -> 1 h + 30 min + 10 min).
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
TYPE_RES = {"W": "base", "S": "base", "F": "base", "I": "monde", "C": "monde", "O": "monde", "G": "monde"}
NOMS_RES = {"W": "bois", "S": "pierre", "I": "fer", "C": "charbon", "O": "huile", "G": "verre"}
PLEIN = 0.98                                       # bois/pierre "pleins" a partir de 98 % de la capacite
MIN_ENVOI = 10000
PAUSE = 300                                        # verification toutes les 5 min
PASSES = {"MS1": 60, "MS2": 300, "MS3": 600, "MS4": 1800, "MS5": 3600}   # capture du 12/11 (msk MST)
NOMS_PASSES = {"MS1": "1 min", "MS2": "5 min", "MS3": "10 min", "MS4": "30 min", "MS5": "1 h"}
PASSES_DEFAUT = ["MS5"]
MAX_PASSES_PAR_ENVOI = 20
REMPLISSAGES_GARDES = 2         # module materiaux : de quoi remplir 2 fois les 6 emplacements de chaque batiment, garde au vert


def reserve_materiaux(cfg_mat, recettes):
    """Ressources du chateau (bois, pierre, charbon, huile, verre, fer) que le module materiaux consomme au vert :
    {ressource: quantite} = pour chaque batiment, le plus gros cout des cases choisies x 6 emplacements x 2."""
    import materiaux
    total = {}
    for partie in materiaux.PARTIES:
        cases = [c for c in (cfg_mat.get(partie) or {}).get("cases") or [] if c]
        pire = {}
        for c in cases:
            typ = materiaux.MANUELS.get(c.get("manuel"))
            for r in recettes.values():
                if r["groupe"] == c.get("groupe") and r["type"] == typ:
                    for k, q in r["couts"].items():
                        if k in materiaux.RESSOURCES_CHATEAU:
                            pire[k] = max(pire.get(k, 0), q)
        for k, q in pire.items():
            total[k] = total.get(k, 0) + q * materiaux.CASES * REMPLISSAGES_GARDES
    return total


def nom_monde(kid):
    return ROYAUMES.get(kid, "royaume %s" % kid)


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


def config_valide(cfg):
    try:
        m = cfg.get("min_envoi", MIN_ENVOI)
        p = cfg.get("passes", PASSES_DEFAUT)
        return (isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 100000000 and isinstance(cfg.get("accelerer", False), bool)
                and isinstance(p, list) and all(x in PASSES for x in p) and (p or not cfg.get("accelerer")))
    except AttributeError:
        return False


def resume_config(cfg):
    return ["ressource speciale de chaque royaume (vert fer, glacier charbon, sables huile, feu verre) envoyee dans les royaumes "
            "qui en manquent, dans l'ordre vert, glacier, sables, feu",
            "bois et pierre : depuis les royaumes des tours quand ils y sont pleins, vers les royaumes sans tours",
            "envois de %s au moins par ressource ; %s" % (fmt(cfg.get("min_envoi", MIN_ENVOI)),
            "envois acceleres avec les passe-temps %s (le moins de temps gache)" % ", ".join(
                NOMS_PASSES[x] for x in sorted(cfg.get("passes", PASSES_DEFAUT), key=PASSES.get, reverse=True))
            if cfg.get("accelerer") else "envois non acceleres (1 h 36 a 2 h de trajet)")]


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


def combinaison(restant, durees):
    """Passe-temps qui couvrent `restant` secondes en gachant le moins de temps, puis avec le moins de passe-temps.
    durees : {code: secondes}. -> liste de codes, du plus long au plus court ([] si rien a couvrir ou aucun passe-temps)."""
    if restant <= 0 or not durees:
        return []
    m = {c: v // 60 for c, v in durees.items()}
    cible = -(-int(restant) // 60)
    haut = cible + max(m.values())
    meilleur = [None] * (haut + 1)                     # meilleur[s] = liste la plus courte qui fait exactement s minutes
    meilleur[0] = []
    for total in range(1, haut + 1):
        for c, v in m.items():
            if v <= total and meilleur[total - v] is not None:
                essai = meilleur[total - v] + [c]
                if meilleur[total] is None or len(essai) < len(meilleur[total]):
                    meilleur[total] = essai
    for total in range(cible, haut + 1):
        if meilleur[total] is not None:
            return sorted(meilleur[total], key=lambda c: -durees[c])
    return []


def principal(chateaux):
    """Chateau d'un royaume qui recoit les envois et dont on compare le remplissage : celui qui a la plus grande capacite."""
    return max(chateaux, key=lambda c: (sum(c["max"].values()), c["id"]))


def plan_transferts(stocks, ouverts, en_route, royaumes_tours, bloques=(), min_envoi=MIN_ENVOI, reserves=None):
    """-> [(chateau source, royaume source, royaume cible, [[ressource, quantite], ...])], dans l'ordre d'envoi.
    reserves : {royaume: {ressource: quantite}} gardee dans le chateau principal du royaume (jamais envoyee)."""
    dispo = {k: {c["id"]: dict(c["res"]) for c in cs} for k, cs in stocks.items()}
    for k, res in (reserves or {}).items():
        if k in stocks:
            p = principal(stocks[k])
            for r, q in res.items():
                if r in dispo[k][p["id"]]:
                    dispo[k][p["id"]][r] = max(0, dispo[k][p["id"]][r] - q)
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
                envois.setdefault((cid, src, dst, TYPE_RES[r]), []).append([r, q])
    # le jeu refuse un envoi qui melange les types (base / ressources des mondes) : un envoi par type
    plan = [(cid, src, dst, g) for (cid, src, dst, _), g in envois.items()]
    return sorted(plan, key=lambda e: e[2] != 0)        # le vert d'abord (stable : l'ordre des autres ne change pas)


class ModuleTransferts:
    NOM_MODULE = "TRANSFERTS"

    def __init__(self, jeu, cfg, partage=None, sortie=print, horloge=time.time, simulation=False, royaumes_tours=None):
        self.jeu, self.cfg, self.sortie, self.horloge, self.simulation = jeu, cfg, sortie, horloge, simulation
        self.partage = partage or tours.Partage()
        self.royaumes_tours = royaumes_tours          # None = lu dans la config des tours si le module tours est lance
        self.nb_attaques = 0
        self.nb_transferts = 0
        self.plus_de_passes = False
        self.passes_vides = set()   # passe-temps refuses (plus en stock) : plus utilises pendant la session
        self.occupes = set()
        self.reserve_vert = None    # module materiaux : ressources gardees au vert (calcule au premier passage)

    def _tours(self):
        if self.royaumes_tours is not None:
            return set(self.royaumes_tours)
        if tours.ModuleTours.NOM_MODULE not in self.partage.commandants:
            return set()
        return set((tours.lire_config().get("tours") or {}).get("royaumes") or [])

    def _reserve_vert(self):
        if self.reserve_vert is None:
            self.reserve_vert = {}
            if "MATERIAUX" in self.partage.commandants:
                try:
                    import materiaux
                    recettes, _ = materiaux.charger_recettes()
                    self.reserve_vert = reserve_materiaux(tours.lire_config().get("materiaux") or {}, recettes)
                except (OSError, ValueError, KeyError, ImportError):
                    self.reserve_vert = {}
                if self.reserve_vert:
                    self.sortie("   module materiaux actif : gardes au vert (jamais envoyes) : %s."
                                % ", ".join("%s %s" % (fmt(q), NOMS_RES.get(r, r)) for r, q in sorted(self.reserve_vert.items())))
        return self.reserve_vert

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
                               self.cfg.get("min_envoi", MIN_ENVOI), reserves={0: self._reserve_vert()})
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
                if self.cfg.get("accelerer") and await self._accelerer(dst, restant.get(dst)):
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

    async def _accelerer(self, dst, restant=None):
        """Passe-temps sur l'envoi en cours vers dst, combines pour gacher le moins de temps. -> True si l'envoi est arrive."""
        dispo = {c: PASSES[c] for c in self.cfg.get("passes", PASSES_DEFAUT) if c in PASSES and c not in self.passes_vides}
        utilises = []
        if restant is None:
            rep = await self._demander("kpi", {})
            _, _, rs = lire_kpi(T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None)
            restant = rs.get(dst, 0)
        for _ in range(MAX_PASSES_PAR_ENVOI):
            plan = combinaison(restant, dispo)
            if not plan:
                break
            code = plan[0]
            rep = await self._demander("msk", {"MST": code, "KID": str(dst), "TT": "2"})
            if not rep or rep[1] != 0:
                self.passes_vides.add(code)                # plus de ce passe-temps : on complete avec les autres
                dispo.pop(code, None)
                self.sortie("   passe-temps %s refuse (%s) : plus en stock ? Il n'est plus utilise%s."
                            % (NOMS_PASSES[code], "code %s" % rep[1] if rep else "pas de reponse",
                               ", on complete avec les autres" if dispo else ""))
                continue
            utilises.append(code)
            _, _, rs = lire_kpi(T.json_ou_none(rep[2]))
            restant = rs.get(dst, 0)
            if restant <= 0:
                break
        if utilises:
            detail = ", ".join("%d x %s" % (utilises.count(c), NOMS_PASSES[c]) for c in sorted(set(utilises), key=lambda c: -PASSES[c]))
            if restant <= 0:
                self.sortie("   envoi accelere : arrive (%s)." % detail)
            else:
                self.sortie("   envoi accelere (%s) : encore %s de trajet." % (detail, duree(restant)))
        elif not dispo:
            if not self.plus_de_passes:
                self.plus_de_passes = True
                self.sortie("   plus aucun passe-temps choisi en stock : les envois ne sont plus acceleres.")
        return restant <= 0


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "transferts"
LANCEUR_ETIQUETTE = "TRANSF"
LANCEUR_CLE = ModuleTransferts.NOM_MODULE
LANCEUR_PRIORITE = 30


def lanceur_config(args, lire=None, sortie=print):
    cfg = dict(tours.lire_config().get(RUBRIQUE) or {})
    cfg.setdefault("min_envoi", MIN_ENVOI)
    cfg.setdefault("accelerer", False)
    cfg.setdefault("passes", list(PASSES_DEFAUT))
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
