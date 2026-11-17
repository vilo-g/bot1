#!/usr/bin/env python3
"""
evenements.py : module "Evenements" (les evenements qui s'enchainent). Pour l'instant : les NOMADES.

Reglages (interface, page Evenements) : rubrique "evenements" de config.json
  {"nomades": {"x": 83, "y": 1160, "commandants": [1, 2, 3], "mode": "auto", "cotes": "LMR", "soldats": [216],
               "engins": {"flanc": [[740, 5], [739, 9]], "centre": [[740, 5], [738, 4], [739, 10]]},
               "coffres": {"actif": true, "types": [405], "ordre": "forts"}, "soldats_cour": 0,
               "presets": [1, 2, 2, 2], "cheval": "F", "passes": ["MS5", "MS4", "MS3"], "max_attaques": 0, "max_tablettes": 0}}

NOMADES (demande du joueur, 15/11) : un camp nomade donne par ses coordonnees (royaume vert), attaque en boucle par les
commandants choisis, par SALVES comme le module Tours lev max (departs a 4,2 s d'ecart, arrivees espacees) :
  - le camp commence sans delai (en mode Maitre+ : niveaux 101 a 109, une victoire = un niveau) ; a son dernier niveau (110) le
    jeu le bloque apres chaque victoire (1 h 30, moins avec le bonus d'alliance) : le bot lit le blocage exact et le saute
    aussitot avec les passe-temps choisis (1 h, 30 min, 10 min), jamais de rubis ;
  - armee : remplissage automatique (soldats choisis sur les cotes choisis a toutes les vagues, engins nomades choisis a la
    vague 1, coffres de tablettes aux vagues suivantes) ou preselections du jeu (vagues 1, 2, 3, 4 et suivantes) ;
  - commandants : le module tours ne les prend jamais tant que ce module tourne ; le module forteresses peut en garder
    (reservation) avant une forteresse, ils reviennent dans la boucle ensuite ;
  - arret : au bout de N attaques et / ou de N tablettes gagnees (0 = pas de limite).

Messages du jeu (capture du 15/11, 3 attaques) :
  carte / adi / cat : le camp = [27, x, y, ?, victoires, blocage restant (<= 0 : attaquable), 0, -601, identifiant du palier, ...]
    identifiant du palier -> items.json "eventAutoScalingCamps" (camplevel, coolDown, addKhanTablet) ; 991 = niveau 101.
  cra : comme une tour, plus "RW" (la cour, ligne etoile) ; places d'une vague = plafond du niveau (64 par flanc, 192 au centre
    au-dessus du niveau 69) x (1 + bonus du commandant + bonus du chateau). Les bonus du chateau sont dans adi "AE" :
    effets de type 28 (flanc) et 34 (centre). Verifie : 190 % / 123 % -> 186 / 429 ; +40 % du commandant -> 212 / 505.
  sce [["KT", n]] : stock de tablettes (arrive quelques secondes apres le combat).
  PAS ENCORE VU : le saut du blocage du camp. Le bot envoie le meme message que pour une tour (msd) ; si le jeu le refuse, le
    module se met en pause et le dit.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combat           # noqa: E402
import montee           # noqa: E402
import tours            # noqa: E402
import khan_defense     # noqa: E402
from tours import ArretModule, PlusDeSoldats, T, duree   # noqa: E402
from montee import PASSES, PASSES_DEFAUT, entier, texte_passes, texte_utilises   # noqa: E402

RUBRIQUE = "evenements"
KID = 0                    # les camps nomades sont au royaume vert
ZONE_CAMP = 27             # type d'objet de la carte d'un camp nomade (= type de zone des effets)
NIVEAU_DEFAUT = 70         # palier inconnu : plafonds des niveaux > 69
# Engins nomades (vague 1) : identifiant -> (nom, bonus de tablettes en %). Donnees du jeu (items.json, units).
# 15/11 : les versions a 7 % (561, 560, 562) retirees a la demande du joueur ; 635 = mur de boucliers (engin de base, sans bonus).
ENGINS = {740: ("echelle en tronc", 3), 142: ("echelle combinee d'elite", 5), 168: ("echelle en tronc amelioree", 6),
          738: ("belier de feu", 3), 141: ("belier combine d'elite", 5), 167: ("belier de feu ameliore", 6),
          739: ("boucliers roulants", 3), 143: ("bouclier combine d'elite", 5), 169: ("boucliers roulants ameliores", 6),
          635: ("mur de boucliers", 0)}
FAMILLES_ENGINS = {"echelles": (740, 142, 168), "beliers": (738, 141, 167), "boucliers": (739, 143, 169, 635)}
ENGINS_FLANC = {w: v for w, v in ENGINS.items() if w not in FAMILLES_ENGINS["beliers"]}   # pas de belier sur un flanc : la porte est au centre
# Coffres de tablettes (vagues 2 et suivantes) : identifiant -> bonus de tablettes en %.
COFFRES = {1: 3, 162: 4, 163: 5, 164: 6, 243: 7, 244: 8, 404: 9, 405: 10, 490: 11, 491: 12, 492: 13}
ENGINS_DEFAUT = {"flanc": [[740, 5], [739, 9]], "centre": [[740, 5], [738, 4], [739, 10]]}
CASES_ENGINS = {"flanc": 2, "centre": 3}
PLACE_ENGINS = {"flanc": 40, "centre": 50}     # engins au plus sur un flanc / au centre d'une vague (tous engins confondus)
QUANTITE_MAX = 50
COUR_MAX = 20000
EFFET_COUR_BONUS = 179     # attackUnitAmountReinforcementBonus : soldats en plus dans la cour (nombre)
EFFET_COUR_BOOST = 180     # attackUnitAmountReinforcementBoost : soldats en plus dans la cour (%)


def places_cour(niveau_joueur, niveau_cible, bonus=0, boost=0):
    """Soldats au plus dans la cour (ligne etoile). Formule du jeu (ggs.dll, CombatConst.getMaxUnitsInReinforcementWave, lue le
    15/11) : arrondi((20 x racine(niveau du joueur) + 50 + 20 x niveau de la cible + bonus) x (1 + boost %)), ou le niveau du
    joueur = niveau + niveau legendaire. Verifie : joueur 70 + 950, camp 101, sans bonus -> 2709 (capture du 15/11)."""
    return int(round((20 * math.sqrt(niveau_joueur) + 50 + 20 * niveau_cible + bonus) * max(0.0, 1 + boost / 100.0)))


def _paires(liste, cases, connus, place=QUANTITE_MAX):
    return (isinstance(liste, list) and len(liste) <= cases and len({p[0] for p in liste if isinstance(p, list) and p}) == len(liste)
            and all(isinstance(p, list) and len(p) == 2 and p[0] in connus and entier(p[1]) is not None and 1 <= p[1] <= QUANTITE_MAX for p in liste)
            and sum(p[1] for p in liste) <= place)


def nomades_valides(c):
    try:
        if not (isinstance(c, dict) and entier(c.get("x")) is not None and 0 <= c["x"] <= 2000 and entier(c.get("y")) is not None
                and 0 <= c["y"] <= 2000 and tours.commandants_valides(c.get("commandants"))):
            return False
        if c.get("mode") == "auto":
            if combat.normaliser_cotes(c.get("cotes")) is None:
                return False
            s = c.get("soldats")
            if not (isinstance(s, list) and 1 <= len(s) <= tours.NB_SOLDATS_MAX and all(entier(u) is not None and u > 0 for u in s) and len(set(s)) == len(s)):
                return False
            s2 = c.get("soldats_suite", [])
            if not (isinstance(s2, list) and len(s2) <= tours.NB_SOLDATS_MAX and all(entier(u) is not None and u > 0 for u in s2) and len(set(s2)) == len(s2)):
                return False
            e = c.get("engins")
            if not (isinstance(e, dict) and _paires(e.get("flanc"), 2, ENGINS_FLANC, PLACE_ENGINS["flanc"]) and _paires(e.get("centre"), 3, ENGINS, PLACE_ENGINS["centre"])):
                return False
            k = c.get("coffres")
            if not (isinstance(k, dict) and isinstance(k.get("actif"), bool) and isinstance(k.get("types"), list)
                    and all(w in COFFRES for w in k["types"]) and len(set(k["types"])) == len(k["types"])
                    and k.get("ordre") in ("forts", "faibles") and (k["types"] or not k["actif"])):
                return False
        else:
            return False                       # 15/11 : plus de mode preselections pour les nomades (demande du joueur)
        if c.get("cheval") not in ("C", "F", "CF") or (c["cheval"] == "CF" and not (entier(c.get("distance_plumes")) or 0) > 0):
            return False
        p = c.get("passes")
        if not (isinstance(p, list) and p and all(x in PASSES for x in p) and len(set(p)) == len(p)):
            return False
        if not isinstance(c.get("remplir_cour", False), bool):
            return False
        return all(entier(c.get(k, 0)) is not None and 0 <= c.get(k, 0) <= m
                   for k, m in (("soldats_cour", COUR_MAX), ("max_attaques", 10 ** 6), ("max_tablettes", 10 ** 9)))
    except (AttributeError, TypeError, KeyError):
        return False


# ============================================================================== donnees du jeu
def charger_camps(chemin=None, zone=None):
    """Paliers des camps nomades (items.json, eventAutoScalingCamps, zone 27) : {identifiant: {"niveau", "blocage", "tablettes"}}."""
    try:
        with open(chemin or tours.FICHIER_ITEMS, encoding="utf-8") as f:
            d = json.load(f)
        res = {}
        for e in d.get("eventAutoScalingCamps") or []:
            if str(e.get("areaType")) == str(zone or ZONE_CAMP):
                res[int(e["eventAutoScalingCampID"])] = {"niveau": int(e.get("camplevel") or 0), "blocage": int(e.get("coolDown") or 0),
                                                         "tablettes": int(e.get("addKhanTablet") or 0)}
                if e.get("generatedRagePerAttack"):          # camp du Khan : rage par attaque, seuil, rage rendue par une defense
                    res[int(e["eventAutoScalingCampID"])].update(rage_attaque=int(e["generatedRagePerAttack"]),
                                                                  rage_max=int(e.get("playerRageCap") or 0),
                                                                  rage_defense=int(e.get("generatedRagePerDefense") or 0))
        return res
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def charger_types_effets(chemin=None):
    """Effets "soldats en plus" du chateau (adi AE) : {identifiant d'effet: (type 28 flanc / 34 centre, zones ou None)}."""
    try:
        with open(chemin or tours.FICHIER_ITEMS, encoding="utf-8") as f:
            d = json.load(f)
        res = {}
        for e in d.get("effects") or []:
            t = int(e["effectTypeID"])
            if t in (combat.EFFET_FLANC, combat.EFFET_FRONT) and str(e.get("isPvPFight")) != "1":
                z = e.get("areaTypeID")
                res[int(e["effectID"])] = (t, {int(x) for x in str(z).split(",")} if z else None)
        return res
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def bonus_chateau(adi, types_effets, type_effet, zone=ZONE_CAMP):
    """Total (en %) des effets du chateau de depart (adi "AE" : objets de construction...) de ce type pour cette zone."""
    total = 0.0
    for a in (adi.get("AE") or []) if isinstance(adi, dict) else []:
        try:
            info = (types_effets or {}).get(a[0])
            if info and info[0] == type_effet and (info[1] is None or zone in info[1]):
                total += float(a[1][0])
        except (TypeError, IndexError, ValueError):
            continue
    return total


# ============================================================================== construction de l'attaque
def _poser_outils(vague, cote, liste, cases, place, stock):
    """Pose des outils [[id, quantite voulue], ...] sur un cote d'une vague, limites par les cases, la place et le stock.
    -> liste des manques [(id, voulu, en stock)]."""
    manques = []
    for wid, voulu in liste:
        if len(vague[cote]["T"]) >= cases or place <= 0:
            break
        q = min(voulu, place)
        if stock.get(wid, 0) < q:
            manques.append((wid, q, stock.get(wid, 0)))
            q = stock.get(wid, 0)
        if q > 0:
            stock[wid] = stock.get(wid, 0) - q
            place -= q
            vague[cote]["T"].append([wid, q])
    return manques


def construire_attaque(cfg, adi, pos, camp, chateau, effets, appris, camps=None, types_effets=None, presets=None, cheval_inconnu_ok=False,
                       niveau_joueur=None, zone=ZONE_CAMP, indice_palier=8, outils_suite=None):
    """Paquet cra du commandant numero `pos` sur le camp nomade `camp` (entree de carte [27,x,y,...]). -> (cra, infos).
    Leve ValueError (commandant absent), PlusDeSoldats (plus assez de soldats ou d'engins), ArretModule."""
    b = tours.cfg_royaume(cfg, KID)
    cmds = tours.positions_commandants(adi)
    if not 1 <= pos <= len(cmds):
        raise ValueError("commandant numero %d absent de la liste (%d commandants)" % (pos, len(cmds)))
    cmd = cmds[pos - 1]
    tx, ty = camp[1], camp[2]
    distance = math.hypot(tx - chateau[0], ty - chateau[1])
    palier = (camps or {}).get(camp[indice_palier]) if len(camp) > indice_palier else None
    niv = (palier or {}).get("niveau") or NIVEAU_DEFAUT
    bc_f = combat.bonus_commandant(cmd, effets, zone, combat.EFFET_FLANC) if effets else 0
    bc_a = combat.bonus_commandant(cmd, effets, zone, combat.EFFET_FRONT) if effets else 0
    bonus_f = bc_f + bonus_chateau(adi, types_effets, combat.EFFET_FLANC, zone)
    bonus_a = bc_a + bonus_chateau(adi, types_effets, combat.EFFET_FRONT, zone)
    extra = combat.vagues_supplementaires(cmd, effets, zone) if effets else 0
    nb_vagues = int(cfg.get("vagues_base", tours.NB_VAGUES_BASE)) + extra
    places = {"L": combat.soldats_flanc(niv, bonus_f), "R": combat.soldats_flanc(niv, bonus_f), "M": combat.soldats_centre(niv, bonus_a)}
    place_outils = {"L": combat.outils_flanc(niv), "R": combat.outils_flanc(niv), "M": combat.outils_front(niv)}
    stock = combat.inventaire_depuis_adi((adi.get("gui") or {}).get("I", []))
    manques = []
    if b.get("mode") == "presets":
        vagues, reste = [], dict(stock)
        for i in range(nb_vagues):
            num = tours.preselection_pour_vague(b, i)
            fiche = (presets or {}).get(num - 1)
            if fiche is None:
                raise ArretModule("Preselection numero %d introuvable (ou pas debloquee) : arret du module." % num)
            vague = {c: {"T": [], "U": []} for c in ("L", "R", "M")}
            for (cote, genre), entrees in zip(combat.GROUPES_PRESELECTION, fiche["groupes"]):
                libre = places[cote] if genre == "U" else place_outils[cote]
                for wid, voulu in entrees:
                    if len(vague[cote][genre]) >= combat.NB_CASES[(cote, genre)] or libre <= 0:
                        break
                    q = min(voulu, libre)
                    if reste.get(wid, 0) < q:
                        manques.append("vague %d (preselection %d), %s : %d x %d voulus, %d en stock"
                                       % (i + 1, num, combat.NOMS_COTES[cote], q, wid, reste.get(wid, 0)))
                        q = reste.get(wid, 0)
                    if q > 0:
                        reste[wid] -= q
                        libre -= q
                        vague[cote][genre].append([wid, q])
            for c in "LRM":
                if not vague[c]["U"]:
                    for wid, q in vague[c]["T"]:      # pas de soldats sur ce cote : ses outils ne partent pas
                        reste[wid] = reste.get(wid, 0) + q
                    vague[c]["T"] = []
            vagues.append(vague)
        if not any(vagues[0][c]["U"] for c in "LRM"):
            raise PlusDeSoldats("Nomades : plus aucun soldat disponible pour la vague 1 (preselection %d)." % tours.preselection_pour_vague(b, 0))
        soldat_affiche, mode = "preselections", "P (preselections)"
    else:
        code = combat.normaliser_cotes(b["cotes"])
        cotes = combat.COTES[code]
        # vague 1 : les soldats choisis pour elle ; vagues suivantes (coffres) : les leurs, s'ils sont choisis (15/11 :
        # en general soldats a distance en vague 1, de melee ensuite), sinon les memes
        # (soldats a niveaux : tous leurs niveaux, le plus haut d'abord, voir tours.deplier)
        suite = tours.deplier(cfg.get("soldats_suite") or b["soldats"])
        vagues, reste, _, utilisees = combat.remplir_cotes_complets(stock, tours.deplier(b["soldats"]), cotes, niv, 1, bonus_f, bonus_a,
                                                                   0, tours.ID_ECHELLE)
        if nb_vagues > 1:
            v2, reste, _, u2 = combat.remplir_cotes_complets(reste, suite, cotes, niv, nb_vagues - 1, bonus_f, bonus_a, 0, tours.ID_ECHELLE)
            vagues += v2
            utilisees = list(utilisees) + [u for u in u2 if u not in utilisees]
        for c in cotes:                               # vague 1 complete, sinon pas d'attaque (une armee incomplete perd)
            n = sum(q for _, q in vagues[0][c]["U"])
            if n < places[c]:
                raise PlusDeSoldats("Nomades : plus assez de soldats pour remplir la vague 1 (%s : %d sur %d, unites %s)."
                                    % (combat.NOMS_COTES[c], n, places[c], ";".join(map(str, b["soldats"]))))
        engins = cfg.get("engins") or {}
        for c in cotes:
            genre = "centre" if c == "M" else "flanc"
            m = _poser_outils(vagues[0], c, engins.get(genre) or [], CASES_ENGINS[genre], place_outils[c], reste)
            if m:
                wid, voulu, a = m[0]
                raise PlusDeSoldats("Nomades : plus assez d'engins pour la vague 1 (%s : %d x %s voulus, %d en stock)."
                                    % (combat.NOMS_COTES[c], voulu, ENGINS.get(wid, ("engin %d" % wid,))[0], a))
        k = cfg.get("coffres") or {}
        bonus_suite = 0
        if outils_suite is not None:
            # Khan : bannieres aux vagues suivantes, juste assez pour atteindre le bonus de rage voulu (suite["pourcent"])
            ordre = sorted(outils_suite["types"], key=lambda w: outils_suite["bonus"].get(w, 0), reverse=True)
            for vague in vagues[1:]:
                for c in cotes:
                    place, cases = place_outils[c], CASES_ENGINS["centre" if c == "M" else "flanc"]
                    for w in ordre:
                        manque = outils_suite["pourcent"] - bonus_suite
                        if manque <= 0 or place <= 0 or len(vague[c]["T"]) >= cases or not vague[c]["U"]:
                            break
                        q = min(place, reste.get(w, 0), -(-manque // outils_suite["bonus"][w]))
                        if q > 0:
                            reste[w] -= q
                            place -= q
                            bonus_suite += q * outils_suite["bonus"][w]
                            vague[c]["T"].append([w, q])
        elif k.get("actif") and k.get("types"):
            ordre = sorted(k["types"], key=lambda w: COFFRES.get(w, 0), reverse=k.get("ordre") != "faibles")
            sans = 0
            for vague in vagues[1:]:
                for c in cotes:
                    if not vague[c]["U"]:
                        continue
                    _poser_outils(vague, c, [[w, place_outils[c]] for w in ordre if reste.get(w, 0) > 0],
                                  CASES_ENGINS["centre" if c == "M" else "flanc"], place_outils[c], reste)
                    if sum(q for _, q in vague[c]["T"]) < place_outils[c]:
                        sans += 1
            if sans:
                manques.append("plus assez de coffres choisis en stock : %d cote(s) de vague sans coffres ou incomplets" % sans)
        soldat_affiche, mode = "+".join(str(u) for u in utilisees), "R %s" % code
    while len(vagues) > 1 and not any(vagues[-1][c]["U"] for c in "LRM"):
        vagues.pop()                                  # vagues finales sans aucun soldat (stock epuise) : inutiles
    cour = [[-1, 0]] * 8
    n_cour = 0
    if cfg.get("remplir_cour"):
        if niveau_joueur:                             # places de la cour : calculees comme le jeu, pour ce commandant
            n_cour = places_cour(niveau_joueur, niv,
                                 combat.bonus_commandant(cmd, effets, zone, EFFET_COUR_BONUS, plafond=10 ** 6) if effets else 0,
                                 combat.bonus_commandant(cmd, effets, zone, EFFET_COUR_BOOST, plafond=10 ** 6) if effets else 0)
        else:
            manques.append("cour pas remplie : le jeu n'a pas encore donne ton niveau (il arrive apres le premier combat)")
    if n_cour > 0:
        candidats = tours.deplier(b.get("soldats") or []) + [w for c in "MLR" for w, _ in vagues[0][c]["U"]]
        u = next((w for w in candidats if reste.get(w, 0) > 0), None)
        if u is not None:
            cour = [[u, min(n_cour, reste[u])]] + [[-1, 0]] * 7
        else:
            manques.append("cour : plus de soldats en stock")
    opt = tours.option_cheval(b, distance)
    champs = tours.champs_pour_cheval(opt, appris)
    cheval_inconnu = champs is None
    if cheval_inconnu:
        if not cheval_inconnu_ok:
            raise ArretModule("Reglages du cheval '%s' inconnus. Lance : outils\\lancer_bot_tours.bat --apprendre-cheval %s" % (opt, opt))
        champs = {}
    cra = T.construire_cra(chateau[0], chateau[1], tx, ty, KID, cmd["ID"], 0, 0, 0)
    cra["A"] = [combat.vers_paquet(v) for v in vagues]
    cra["RW"] = cour
    cra.update({k: champs[k] for k in tours.CHAMPS_CHEVAL if k in champs})
    infos = {"pos": pos, "kid": KID, "lid": cmd["ID"], "nom": cmd.get("N", ""), "niveau": niv if palier else "?", "niveau_brut": camp[4],
             "victoires": camp[4], "distance": distance, "cheval": opt, "cheval_inconnu": cheval_inconnu, "soldat": soldat_affiche,
             "vagues_remplies": len(vagues), "mode": mode, "vagues": len(vagues), "vagues_en_plus": extra, "manques": manques,
             "plafond_flanc": places["L"], "plafond_front": places["M"], "cour": cour[0][1] if cour[0][0] != -1 else 0,
             "coffres": sum(q for v in vagues for c in "LRM" for w, q in v[c]["T"] if w in COFFRES),
             "bonus_suite": bonus_suite if b.get("mode") != "presets" else 0}
    return cra, infos


def resume_config(cfg):
    b = tours.cfg_royaume(cfg, KID)
    l = ["NOMADES : camp (%d,%d), commandants %s (le module tours ne les prend pas ; les forteresses peuvent en garder)"
         % (cfg["x"], cfg["y"], ";".join(str(p) for p in cfg["commandants"]))]
    if b["mode"] == "presets":
        l.append("armee : PRESELECTIONS (vague 1 : %d, vague 2 : %d, vague 3 : %d, vague 4 et suivantes : %d)" % tuple(b["presets"][:4]))
    else:
        e = cfg["engins"]
        l.append("armee : REMPLISSAGE AUTOMATIQUE, cotes : %s, soldats vague 1 : %s, vagues suivantes : %s" % (
            combat.texte_cotes(b["cotes"]), ";".join(map(str, b["soldats"])), ";".join(map(str, cfg.get("soldats_suite") or [])) or "les memes"))
        l.append("vague 1, engins : flanc %s ; centre %s" % tuple(
            " + ".join("%d x %s" % (q, ENGINS[w][0]) for w, q in e[g]) or "aucun" for g in ("flanc", "centre")))
        k = cfg["coffres"]
        l.append("vagues suivantes, coffres : %s" % ("%s, les plus %s d'abord" % (
            ", ".join("+%d %%" % COFFRES[w] for w in sorted(k["types"], key=COFFRES.get)), "forts" if k["ordre"] == "forts" else "faibles")
            if k["actif"] else "aucun"))
    if cfg.get("remplir_cour"):
        l.append("cour : remplie au maximum a chaque attaque")
    l.append("delai du camp saute avec %s (jamais de rubis)" % texte_passes(cfg["passes"]))
    l.append("arret : %s" % (" ou ".join(x for x in ("apres %d attaques" % cfg["max_attaques"] if cfg.get("max_attaques") else "",
                                                     "apres %d tablettes gagnees" % cfg["max_tablettes"] if cfg.get("max_tablettes") else "") if x)
                             or "aucune limite (jusqu'a l'arret du bot)"))
    return l


# ============================================================================== le module
class ModuleNomades(montee.ModuleMontee):
    NOM_MODULE = "NOMADES"
    NOM_CIBLE = "camp"
    TYPE_CIBLE = ZONE_CAMP
    LA_CIBLE = "le camp"
    NOM_PAUSE = "nomades"

    def __init__(self, jeu, cfg, sources, effets=None, camps=None, types_effets=None, **kw):
        kw.setdefault("journal", os.path.join(tours.DOSSIER_CAPTURES, "journal_nomades.txt"))
        super().__init__(jeu, cfg, sources, effets, **kw)
        self.camps = camps or {}
        self.types_effets = types_effets or {}
        self.camp_id = None
        self.tablettes = None            # stock de tablettes (message sce "KT")
        self.tablettes_gagnees = 0
        self.cible_v = 10 ** 9           # pas d'objectif de niveau : les arrets sont le nombre d'attaques / de tablettes
        self.fin_blocage = None          # instant de fin du blocage connu du camp
        self.nouveaux_blocages = 0       # blocages qui ont DEMARRE (= victoires au dernier niveau, ou les victoires ne montent plus)
        self._blocages_vus = 0
        self._blocage_detecte = None     # (instant, fin) du dernier blocage qui a demarre
        self._saut_t = float("-inf")     # instant du dernier passe-temps accepte
        self.niveau_joueur = None        # niveau + niveau legendaire (message gxp) : pour les places de la cour

    # ------------------------------------------------------------------ etat du camp
    def _ecouter(self, msg):
        try:
            cmd, code, payload = msg[0], msg[1], msg[2]
        except (TypeError, IndexError):
            return
        if cmd == "gxp" and code == 0 and isinstance(payload, str):
            d = T.json_ou_none(payload)
            if isinstance(d, dict) and entier(d.get("LVL")) is not None:
                self.niveau_joueur = d["LVL"] + (entier(d.get("LL")) or 0)
        if cmd == "sce" and code == 0 and isinstance(payload, str) and '"KT"' in payload:
            d = T.json_ou_none(payload)
            for e in d if isinstance(d, list) else []:
                if isinstance(e, list) and len(e) == 2 and e[0] == "KT" and entier(e[1]) is not None:
                    if self.tablettes is not None and e[1] > self.tablettes:
                        self.tablettes_gagnees += e[1] - self.tablettes
                    self.tablettes = e[1]
        super()._ecouter(msg)

    def _est_la_tour(self, e):
        return (isinstance(e, list) and len(e) >= 6 and e[0] == ZONE_CAMP and e[1] == self.x and e[2] == self.y
                and entier(e[4]) is not None and isinstance(e[5], (int, float)) and not isinstance(e[5], bool))

    def _noter_entree(self, e):
        if len(e) > 8 and entier(e[8]) is not None:
            self.camp_id = e[8]
        super()._noter_entree(e)

    def _noter_etat(self, v, blocage):
        """Un blocage qui demarre (fin plus tardive que celle connue) = une victoire. Au dernier niveau le nombre de victoires
        ne monte plus : c'est le seul signe. Le jeu annonce le nouvel etat du camp (gaa) juste AVANT le message du combat (cat) :
        regarder si le camp etait bloque a l'arrivee comptait a tort ces victoires comme des arrivees pendant le blocage (15/11)."""
        t = self.horloge()
        if isinstance(blocage, (int, float)):
            fin = t + blocage
            if blocage > 0 and self.fin_blocage is not None and fin > self.fin_blocage + 5:
                self.nouveaux_blocages += 1
                self._blocage_detecte = (t, fin)
            self.fin_blocage = fin
        super()._noter_etat(v, blocage)

    def niveau_camp(self):
        return (self.camps.get(self.camp_id) or {}).get("niveau")

    def texte_niveau(self, v):
        n = self.niveau_camp()
        return "niveau %d (%d victoire(s))" % (n, v) if n else "%d victoire(s)" % v

    def _combat(self, d):
        # Le message du combat peut arriver bien apres le combat (30 s vues le 15/11), donc apres le saut du blocage : il decrit
        # alors le camp au moment du combat (bloque 1 h 30), plus son etat actuel. Dans ce cas l'etat connu est garde.
        m = (d.get("A") or {}).get("M") or {}
        sa = m.get("SA")
        if (self._est_la_tour(sa) and sa[5] > 0 and self._blocage_detecte is not None and self._saut_t >= self._blocage_detecte[0]
                and abs(self.horloge() + sa[5] - self._blocage_detecte[1]) < 120 and self._blocage() is not None):
            sa = list(sa)
            sa[5] = self._blocage()
            d = dict(d, A=dict(d["A"], M=dict(m, SA=sa)))
        n = len(self.combats)
        super()._combat(d)
        if len(self.combats) > n:
            nouveau = self.nouveaux_blocages > self._blocages_vus       # un blocage a demarre : une victoire (une par combat)
            self.combats[-1]["nouveau_blocage"] = nouveau
            if nouveau:
                self._blocages_vus += 1

    async def _demander_msd(self, code):
        rep = await super()._demander_msd(code)
        if rep and rep[1] == 0:
            self._saut_t = self.horloge()
        return rep

    def progres(self):
        """Pour l'interface (bot_etat.json)."""
        return {"evenement": "nomades", "x": self.x, "y": self.y, "niveau": self.niveau_camp(), "victoires": self._victoires(),
                "attaques": self.nb_attaques, "victoires_gagnees": self.victoires_gagnees, "en_route": len(self.vol),
                "tablettes": self.tablettes_gagnees, "stock_tablettes": self.tablettes,
                "max_attaques": self.cfg.get("max_attaques") or 0, "max_tablettes": self.cfg.get("max_tablettes") or 0,
                "passes": {c: n for c, n in self.utilises.items() if n}, "fini": self.fini,
                "pause": self.raison_pause if self.horloge() < self.pause_jusqua else ""}

    # ------------------------------------------------------------------ arrets
    def _attaques_utiles(self):
        """Departs encore permis par les limites choisies (nombre d'attaques, tablettes gagnees)."""
        reste = 10 ** 9
        n = self.cfg.get("max_attaques") or 0
        if n:
            reste = min(reste, n - self.nb_attaques)
        g = self.cfg.get("max_tablettes") or 0
        if g and self.tablettes_gagnees >= g:
            reste = 0
        return reste

    def _raison_fin(self, utiles):
        g = self.cfg.get("max_tablettes") or 0
        if g and self.tablettes_gagnees >= g:
            return "%d tablettes gagnees (limite : %d)" % (self.tablettes_gagnees, g)
        return "%d attaques envoyees (limite : %d)" % (self.nb_attaques, self.cfg.get("max_attaques") or 0)

    def _texte_assez(self):
        return "limite choisie atteinte"

    def _texte_reste(self, utiles):
        return " ; plus que %d attaque(s) avant la limite : %d depart(s) seulement" % (utiles, utiles)

    def _verifier_objectif(self, ai):
        return None

    def _entree_cible(self, ai):
        return list(ai)

    def _nom_cible(self, article=False):
        return "%scamp nomade (%d,%d)" % ("le " if article else "", self.x, self.y)

    def _terminer(self, raison):
        self.fini = True
        self._noter("NOMADES : %s. Termine (%d attaque(s), %d victoire(s), %d tablette(s) gagnee(s), passe-temps utilises : %s) ; les "
                    "commandants %s sont rendus aux autres modules." % (
                        raison, self.nb_attaques, self.victoires_gagnees, self.tablettes_gagnees, texte_utilises(self.utilises),
                        ";".join(str(p) for p in self.cfg["commandants"])))
        raise ArretModule("NOMADES : %s : le module Evenements s'arrete (les autres modules continuent)." % raison)

    # ------------------------------------------------------------------ demarrage
    async def preparer(self):
        e = await self._lire_tour()
        if e is False:
            raise ArretModule("le jeu ne donne pas la carte autour du camp nomade (%d,%d) : relance le bot." % (self.x, self.y))
        if e is None or not self._est_la_tour(e):
            raise ArretModule("NOMADES : il n'y a pas de camp nomade en (%d,%d) (%s). L'evenement des nomades est-il en cours ? "
                              "Verifie les coordonnees dans l'interface (page Evenements)."
                              % (self.x, self.y, "rien a cet endroit" if e is None else "autre chose : type %s" % e[0]))
        self.v_combat = e[4]
        b = self._blocage() or 0
        palier = self.camps.get(self.camp_id) or {}
        self._noter("CAMP NOMADE (%d,%d) : %s, %s%s." % (
            self.x, self.y, self.texte_niveau(e[4]), "attaquable" if b <= 0 else "bloque encore %s" % duree(b),
            " ; delai apres une victoire a ce niveau : %s" % (duree(palier["blocage"]) if palier.get("blocage") else "aucun") if palier else ""))

    # ------------------------------------------------------------------ combats
    def _traiter_combats(self):
        while self.combats:
            c = self.combats.pop(0)
            self.nb_combats += 1
            avant = self.v_combat if self.v_combat is not None else c["v"]
            gagne = c["v"] - avant
            self.v_combat = max(avant, c["v"])
            qui = "commandant %d" % c["pos"] if c["pos"] is not None else "armee"
            if gagne <= 0 and not c.get("nouveau_blocage") and (c["T"] > 2 or (self._blocage() or 0) > 2):
                self.sans_hausse += 1
                if c.get("tt_connu", True):
                    self.espacement = min(montee.ESPACEMENT_MAX, self.espacement + 2)
                self._noter("COMBAT (%s) : arrivee pendant le blocage du camp (encore %s) : pas de combat. Arrivees espacees de %.1f s."
                            % (qui, duree(max(0, c["T"])), self.espacement))
                if self.sans_hausse >= montee.SANS_HAUSSE_MAX:
                    self.sans_hausse = 0
                    self._pause(montee.PAUSE_PROBLEME, "%d attaques de suite arrivees pendant le blocage du camp." % montee.SANS_HAUSSE_MAX)
            elif gagne > 0 or c.get("nouveau_blocage"):
                self.victoires_gagnees += 1
                self.pertes = self.sans_hausse = 0
                self._noter("COMBAT (%s) : victoire -> camp %s ; %s. Tablettes gagnees depuis le lancement : %d%s." % (
                    qui, self.texte_niveau(c["v"]), "blocage %s" % duree(c["T"]) if c["T"] > 0 else "pas de blocage",
                    self.tablettes_gagnees, " (stock %d)" % self.tablettes if self.tablettes is not None else ""))
            else:
                self.pertes += 1
                self._noter("COMBAT (%s) : DEFAITE, le camp n'a pas ete battu (%d de suite)." % (qui, self.pertes))
                if self.pertes >= montee.PERTES_MAX:
                    self.pertes = 0
                    self._pause(montee.PAUSE_PROBLEME, "%d defaites de suite : l'armee est trop faible pour ce camp ? Regarde les reglages "
                                                       "de l'armee (page Evenements)." % montee.PERTES_MAX)
        self._sauver_etat()

    def _construire(self, adi, pos, tour, kid, chateau):
        return construire_attaque(self.cfg, adi, pos, tour, chateau, self.effets, self.appris, self.camps, self.types_effets,
                                  presets=self.presets, cheval_inconnu_ok=self.simulation, niveau_joueur=self.niveau_joueur)


# ============================================================================== le Khan (2e partie de l'evenement des nomades)
ZONE_KHAN = 35             # type d'objet de la carte du camp du Khan de l'alliance
EVENEMENT_NOMADES = 72
BANNIERES = {106: 1, 107: 1}     # 16/11 : la grande banniere (563, +2 %) retiree a la demande du joueur    # outils de rage : identifiant -> bonus de rage en % (items.json, ragePointBonus)


def bannieres_pour(besoin, base):
    """Bonus de rage (en %) qu'une attaque doit porter pour apporter `besoin` de rage : rage = base x (1 + bonus / 100).
    Verifie sur un autre bot : base 365, seuil 3300 -> 805 %."""
    if besoin <= base or base <= 0:
        return 0
    return int(math.ceil((besoin / base - 1) * 100 - 1e-9))


EN_COURS_MAX = 20         # attaques du Khan gardees en route au plus
POINTS_MEDAILLE = 7       # objectif en points nomades : 1 tablette = 1 point, 1 medaille du Khan = 7 points (demande du joueur)


def khan_valides(c):
    try:
        if not isinstance(c, dict):
            return False
        b = c.get("bannieres")
        if not (nomades_valides(dict(c, x=0, y=0, coffres={"actif": False, "types": [], "ordre": "forts"}))
                and isinstance(b, list) and b and all(w in BANNIERES for w in b) and len(set(b)) == len(b)
                and isinstance(c.get("declencher"), bool)):
            return False
        if not c["declencher"]:
            return True
        return (entier(c.get("en_cours")) is not None and 1 <= c["en_cours"] <= EN_COURS_MAX
                and all(entier(c.get(k, 0)) is not None and 0 <= c.get(k, 0) <= 10 ** 12 for k in ("objectif_declenchements", "objectif_points"))
                and khan_defense.defense_valide(c.get("defense")))
    except (AttributeError, TypeError, KeyError):
        return False


class ModuleKhan(ModuleNomades):
    """Attaques du camp du Khan de l'alliance avec des bannieres de rage, et defense contre les attaques du Khan (16/11) :
      fnm {"T":35,"KID":0,"LMIN":-1,"LMAX":-1,"NID":-801} -> {"X","Y","gaa":{"AI":[camp]}} : ou est le camp ;
      camp = [35, x, y, ?, -1, delai restant, delai suivant, prix en rubis, victoires, identifiant du palier, ...] ;
      rpr {"EID":72,"PCRP":rage du joueur} apres chaque attaque / defense ; sei -> E[EID 72].AC.PCRP ;
      lta {"AV":0,"EID":72} : declenche une attaque du Khan quand la barre est pleine (la rage revient a 0) ;
      msd : saute le delai du camp, comme pour une tour. Rage d'une attaque = rage du palier x (1 + % des bannieres).
    Avec "declencher" : le bot garde `en_cours` attaques du Khan en route vers le chateau principal (il declenche des que la
    barre est pleine et qu'il en manque), pose la defense choisie (khan_defense.py) et s'arrete a l'objectif (defenses
    et / ou points nomades), apres avoir defendu les dernieres attaques et remis la defense du debut."""
    NOM_MODULE = "KHAN"
    NOM_CIBLE = "camp du Khan"
    TYPE_CIBLE = ZONE_KHAN
    LA_CIBLE = "le camp du Khan"
    NOM_PAUSE = "Khan"

    def __init__(self, jeu, cfg, sources, effets=None, camps=None, types_effets=None, chateau=None, soldats_def=None, **kw):
        kw.setdefault("journal", os.path.join(tours.DOSSIER_CAPTURES, "journal_khan.txt"))
        super().__init__(jeu, cfg, sources, effets, camps=camps, types_effets=types_effets, **kw)
        self.rage = None                 # rage du joueur (rpr / sei)
        self.rage_prevue = {}            # commandant (lid) -> rage attendue de son attaque en route
        self.declenchements = 0
        self.medailles = None            # stock de medailles du Khan (sce "KM")
        self.medailles_gagnees = 0
        self.fin_demandee = None         # raison de l'arret (objectif atteint) : on finit les defenses en cours
        self.camp_ferme_si_khan = False  # vu : le camp ne se combat pas pendant qu'une attaque du Khan est en route
        self.defense = None
        if cfg.get("declencher") and cfg.get("defense") and chateau is not None:
            self.defense = khan_defense.DefenseKhan(self, cfg["defense"], chateau, soldats_def or {})

    def _ecouter(self, msg):
        try:
            cmd, code, payload = msg[0], msg[1], msg[2]
        except (TypeError, IndexError):
            return
        if code == 0 and isinstance(payload, str):
            if cmd == "rpr":
                d = T.json_ou_none(payload)
                if isinstance(d, dict) and d.get("EID") == EVENEMENT_NOMADES and entier(d.get("PCRP")) is not None:
                    self.rage = d["PCRP"]
            elif cmd == "sce" and '"KM"' in payload:
                d = T.json_ou_none(payload)
                for e in d if isinstance(d, list) else []:
                    if isinstance(e, list) and len(e) == 2 and e[0] == "KM" and entier(e[1]) is not None:
                        if self.medailles is not None and e[1] > self.medailles:
                            self.medailles_gagnees += e[1] - self.medailles
                        self.medailles = e[1]
            elif cmd == "gam" and self.defense is not None:
                self.defense.ecouter(cmd, payload)
        super()._ecouter(msg)

    def _est_la_tour(self, e):
        return (isinstance(e, list) and len(e) >= 10 and e[0] == ZONE_KHAN and e[1] == self.x and e[2] == self.y
                and isinstance(e[5], (int, float)) and not isinstance(e[5], bool) and entier(e[4]) is not None)

    def _noter_entree(self, e):
        if entier(e[9]) is not None:
            self.camp_id = e[9]
        montee.ModuleMontee._noter_entree(self, e)

    def palier(self):
        return self.camps.get(self.camp_id) or {}

    def texte_niveau(self, v):
        n = self.niveau_camp()
        return "niveau %d" % n if n else "niveau inconnu"

    def _nom_cible(self, article=False):
        return "%scamp du Khan (%d,%d)" % ("le " if article else "", self.x, self.y)

    def besoin(self):
        """Rage qui manque pour remplir la barre, attaques en route comptees. None si le palier ou la rage sont inconnus."""
        p = self.palier()
        if self.rage is None or not p.get("rage_max"):
            return None
        en_route = sum(v for lid, v in self.rage_prevue.items() if any(a.get("lid") == lid for a in self.vol))
        return p["rage_max"] - self.rage - en_route - self.credit_khan()[0]

    def trajet_camp(self):
        """Trajet d'une attaque vers le camp (s) : le plus long vu, 90 s tant qu'aucun n'est connu."""
        return max(self.tt.values()) if self.tt else 90

    def credit_khan(self):
        """(rage, arrivees) : rage que rendront les attaques du Khan qui arrivent sur le chateau AVANT qu'une attaque du camp partie
        maintenant n'arrive (16/11, demande du joueur : inutile d'attaquer le camp si une defense remplit la barre plus tot ; on
        attaque ensuite seulement s'il manque encore de la rage)."""
        rd = self.palier().get("rage_defense") or 0
        if self.defense is None or not rd or not self.cfg.get("declencher"):
            return 0, []
        arr = self.defense.arrivant_avant(self.horloge() + self.trajet_camp() + 10)
        return rd * len(arr), arr

    def points(self):
        return self.tablettes_gagnees + POINTS_MEDAILLE * self.medailles_gagnees

    def _objectif_atteint(self):
        """Raison de l'arret si l'objectif choisi (declenchements du Khan, points nomades) est atteint, sinon None.
        16/11 (demande du joueur) : un nombre de DECLENCHEMENTS plutot que de defenses : avec 2 attaques du Khan gardees en route,
        "5 defenses" en faisait 6 (les attaques deja en route arrivent quand meme). Seuls ceux du bot comptent."""
        if not self.cfg.get("declencher"):
            return None
        od, op = self.cfg.get("objectif_declenchements") or 0, self.cfg.get("objectif_points") or 0
        if od and self.declenchements >= od:
            return "Khan declenche %d fois (objectif : %d)" % (self.declenchements, od)
        if op and self.points() >= op:
            return "%d points nomades (objectif : %d ; %d tablette(s), %d medaille(s) x %d)" % (
                self.points(), op, self.tablettes_gagnees, self.medailles_gagnees, POINTS_MEDAILLE)
        return None

    def _attaques_utiles(self):
        if self.fin_demandee:
            return 0
        b = self.besoin()
        if b is not None and b <= 0:
            return 0
        return 10 ** 9

    def _raison_fin(self, utiles):
        return "barre de rage pleine (%s sur %s) : le Khan peut etre declenche" % (self.rage, self.palier().get("rage_max"))

    def _texte_assez(self):
        return "assez de rage en route pour remplir la barre"

    def _texte_reste(self, utiles):
        return ""

    def progres(self):
        p = super().progres()
        p.update(evenement="khan", rage=self.rage, rage_max=self.palier().get("rage_max"), declenchements=self.declenchements,
                 declencher=bool(self.cfg.get("declencher")), en_cours=self.cfg.get("en_cours", 0), points=self.points(),
                 medailles=self.medailles_gagnees, objectif_declenchements=self.cfg.get("objectif_declenchements", 0),
                 objectif_points=self.cfg.get("objectif_points", 0), fin=self.fin_demandee or "")
        if self.defense is not None:
            p.update(self.defense.progres())
        return p

    async def _demander(self, cmd, donnees, delai=10):
        await self._vider()
        await self.jeu.envoyer(cmd, donnees)
        return await self.jeu.attendre(cmd, delai)

    async def _lire_rage(self):
        rep = await self._demander("sei", {})
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        for e in (d.get("E") or []) if isinstance(d, dict) else []:
            if isinstance(e, dict) and e.get("EID") == EVENEMENT_NOMADES and isinstance(e.get("AC"), dict):
                if entier(e["AC"].get("PCRP")) is not None:
                    self.rage = e["AC"]["PCRP"]
                return True
        return False

    async def preparer(self):
        rep = await self._demander("fnm", {"T": ZONE_KHAN, "KID": KID, "LMIN": -1, "LMAX": -1, "NID": -801})
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        ai = ((d.get("gaa") or {}).get("AI") or [None])[0] if isinstance(d, dict) else None
        if not (isinstance(d, dict) and entier(d.get("X")) is not None and entier(d.get("Y")) is not None and isinstance(ai, list)):
            raise ArretModule("KHAN : le jeu ne donne pas de camp du Khan (%s). L'evenement des nomades est-il en cours, et ton "
                              "alliance a-t-elle un camp du Khan ?" % ("code %s" % rep[1] if rep else "pas de reponse"))
        self.x, self.y = d["X"], d["Y"]
        if not self._est_la_tour(ai):
            raise ArretModule("KHAN : camp du Khan illisible (%s)." % str(ai)[:60])
        self._noter_entree(ai)
        self.v_combat = ai[4]
        if not await self._lire_rage() or self.rage is None:
            raise ArretModule("KHAN : le jeu ne donne pas ta rage (evenement des nomades introuvable).")
        p = self.palier()
        if not p.get("rage_attaque"):
            raise ArretModule("KHAN : palier du camp inconnu (%s) : donnees du jeu a mettre a jour (captures\\items.json)." % self.camp_id)
        b = self._blocage() or 0
        self._noter("CAMP DU KHAN (%d,%d) : niveau %d, %s. Rage : %d sur %d ; une attaque sans banniere en donne %d, une defense en "
                    "rend %d." % (self.x, self.y, p["niveau"], "attaquable" if b <= 0 else "delai encore %s" % duree(b), self.rage,
                                  p["rage_max"], p["rage_attaque"], p.get("rage_defense", 0)))
        if self.defense is not None:
            await self.defense.demarrer()
            self._noter("KHAN : %d attaque(s) du Khan deja en route vers ton chateau principal%s ; le bot en garde %d en route et "
                        "n'attaque pas le camp tant qu'il y en a %d en route." % (
                            self.defense.en_route(), " (%d mouvement(s) lus)" % self.defense.mouvements_lus
                            if self.defense.mouvements_lus is not None else "", self.cfg["en_cours"], self.cfg["en_cours"]))

    async def _declencher(self):
        """Barre pleine : declenche une attaque du Khan (lta). Le jeu repond par la rage remise a 0 (rpr)."""
        avant = self.rage
        if self.simulation:
            self._noter("[SIMULATION] le Khan serait declenche (rage %s)." % avant)
            self.rage = 0
            self.declenchements += 1
            if self.defense is not None:
                self.defense.declenche()
            return
        await self._vider()
        await self.jeu.envoyer("lta", {"AV": 0, "EID": EVENEMENT_NOMADES})
        rep = await self.jeu.attendre("rpr", 10)
        if rep and not self._ecouteur:
            self._ecouter(rep)
        if self.rage is not None and avant is not None and self.rage < avant:
            self.declenchements += 1
            if self.defense is not None:
                self.defense.declenche()
            self._noter("KHAN DECLENCHE (%d) : son attaque part vers ton chateau principal (%d en route). Rage : %d -> %d." % (
                self.declenchements, self.defense.en_route() if self.defense else 1, avant, self.rage))
        else:
            self._pause(montee.PAUSE_PROBLEME, "le jeu n'a pas declenche le Khan (rage toujours a %s)." % self.rage)

    async def _passage(self):
        if self.defense is not None:
            await self._absorber()
            await self.defense.passage()
            if self.fin_demandee is None:
                raison = self._objectif_atteint()
                if raison:
                    self.fin_demandee = raison
                    self._noter("KHAN : objectif atteint (%s). Plus de declenchement ni d'attaque ; le bot defend les %d attaque(s) "
                                "du Khan encore en route, puis remet ta defense du debut." % (raison, self.defense.en_route()))
            if self.fin_demandee and not self.vol and not self.combats and self.defense.en_route() == 0:
                await self.defense.restaurer()
                await self.defense.rappeler()
                self._terminer(self.fin_demandee)
        return await super()._passage()

    MARGE_PRIORITE = 30          # s : le Khan a quelque chose a faire dans moins de 30 s -> les autres modules attendent

    def occupe_bientot(self):
        """Le module Khan a-t-il quelque chose a faire dans les 30 s (ordonnanceur : les autres modules attendent) ?"""
        if self.fini:
            return False
        t, marge = self.horloge(), self.MARGE_PRIORITE
        if self.combats or (self.salve is not None and not self.salve.get("fermee")):
            return True
        if any(a["arrivee"] - t < marge for a in self.vol):
            return True
        d = self.defense
        if d is not None:
            p = d.prochaine()
            if (p is not None and p - t < marge) or (d.reposer_a is not None and d.reposer_a - t < marge):
                return True
            pal = self.palier()
            if (self.cfg.get("declencher") and not self.fin_demandee and self.rage is not None and pal.get("rage_max")
                    and self.rage >= pal["rage_max"] and d.en_route() < self.cfg["en_cours"]):
                return True
        return False

    def _sans_nouvelles(self):
        """Attaque du camp sans combat pendant qu'une attaque du Khan est en route (vu le 16/11 : 3 attaques sans combat ni rage
        apres le declenchement) : le camp ne se combat pas tant que le Khan est parti. Le bot n'y envoie plus d'attaque dans ce cas."""
        avant = len(self.vol)
        super()._sans_nouvelles()
        if len(self.vol) < avant and self.defense is not None and self.defense.en_route() and not self.camp_ferme_si_khan:
            self.camp_ferme_si_khan = True
            self._noter("KHAN : attaque du camp sans combat pendant qu'une attaque du Khan est en route : le bot n'attaque plus le camp "
                        "tant qu'une attaque du Khan est en route (bannieres economisees).")

    def _terminer(self, raison):
        self.fini = True
        d = self.defense
        self._noter("KHAN : %s. Termine (%d attaque(s) du camp, Khan declenche %d fois%s, %d tablette(s) et %d medaille(s) gagnees, "
                    "passe-temps utilises : %s) ; les commandants %s sont rendus aux autres modules." % (
                        raison, self.nb_attaques, self.declenchements,
                        ", %d defense(s)" % d.defenses if d is not None else "", self.tablettes_gagnees, self.medailles_gagnees,
                        texte_utilises(self.utilises), ";".join(str(p) for p in self.cfg["commandants"])))
        raise ArretModule("KHAN : %s : le module Khan s'arrete (les autres modules continuent)." % raison)

    async def _lancer(self):
        if not self.cfg.get("declencher"):
            return await super()._lancer()             # remplit la barre puis s'arrete (le joueur declenche lui-meme)
        t = self.horloge()
        if self.fin_demandee:
            return 5                                   # objectif atteint : on attend la fin des defenses (voir _passage)
        p = self.palier()
        bloque = KID in (getattr(self.partage, "royaumes_bloques", None) or ())
        en_route = self.defense.en_route() if self.defense is not None else 0
        if en_route >= self.cfg["en_cours"] or (en_route and self.camp_ferme_si_khan):
            # assez d'attaques du Khan en route (demande du joueur, 16/11) : pas d'attaque du camp, ni de bannieres gachees.
            # La rage qui manque sera completee quand une attaque du Khan sera arrivee (chaque defense rend de la rage).
            if self.salve is not None and not self.salve.get("fermee"):
                self.salve["fermee"] = True
            if not self.vol and not self.combats:
                self.salve = None
            self._attente_msg("khan_plein", "En attente : %d attaque(s) du Khan en route vers ton chateau principal (%s) : pas "
                              "d'attaque du camp avant son arrivee." % (en_route, "maximum choisi : %d" % self.cfg["en_cours"]
                                                                        if en_route >= self.cfg["en_cours"] else "camp ferme pendant ce temps"))
            return 5 if not self.vol else None
        if (self.rage is not None and p.get("rage_max") and self.rage >= p["rage_max"] and t >= self.pause_jusqua
                and not bloque and en_route < self.cfg["en_cours"] and not self.combats):
            await self._declencher()
            return 1
        b = self.besoin()
        if b is not None and b <= 0 and not self.vol and not self.combats:
            if self.salve is not None:
                self.salve = None
            credit, arr = self.credit_khan()
            if credit and self.rage is not None and self.rage < (p.get("rage_max") or 0):
                self._attente_msg("khan_credit", "En attente : une attaque du Khan arrive sur ton chateau dans %s (avant une attaque du "
                                  "camp) ; sa defense rendra %d de rage, assez pour redeclencher : pas d'attaque du camp."
                                  % (duree(max(0, arr[0] - t)), credit))
            return 5                                   # barre pleine (ou remplie par une defense proche) : on attend
        return await super()._lancer()

    def _construire(self, adi, pos, tour, kid, chateau):
        p, besoin = self.palier(), self.besoin()
        if besoin is None or besoin <= 0:
            raise PlusDeSoldats("Khan : la barre de rage est deja pleine, pas d'attaque.")
        pourcent = bannieres_pour(besoin, p["rage_attaque"])
        cra, info = construire_attaque(self.cfg, adi, pos, tour, chateau, self.effets, self.appris, self.camps, self.types_effets,
                                       cheval_inconnu_ok=self.simulation, niveau_joueur=self.niveau_joueur, zone=ZONE_KHAN,
                                       indice_palier=9, outils_suite={"types": self.cfg["bannieres"], "bonus": BANNIERES, "pourcent": pourcent})
        attendue = int(p["rage_attaque"] * (1 + info["bonus_suite"] / 100.0))
        self.rage_prevue[info["lid"]] = attendue
        credit = self.credit_khan()[0]
        info["manques"] = list(info["manques"]) + ["rage : %d sur %d%s, il manque %d ; bannieres +%d %% (voulu +%d %%) -> rage attendue +%d%s" % (
            self.rage, p["rage_max"], " (+%d rendus par une defense avant l'arrivee)" % credit if credit else "", besoin, info["bonus_suite"], pourcent, attendue,
            "" if attendue >= besoin else " : un autre commandant complete")]
        return cra, info


def config_khan(tout):
    """Reglages evenements.khan -> configuration du module Khan (ArretModule sinon)."""
    ev = tout.get(RUBRIQUE) if isinstance(tout, dict) else None
    c = ev.get("khan") if isinstance(ev, dict) else None
    if not khan_valides(c):
        raise ArretModule("Khan : les reglages sont invalides ou absents. Regle-les dans l'interface (page Khan).")
    cfg = config_module({"tours": tout.get("tours"), RUBRIQUE: {"nomades": dict(c, x=0, y=0, coffres={"actif": False, "types": [], "ordre": "forts"})}})
    cfg.update(bannieres=list(c["bannieres"]), declencher=c["declencher"])
    if c["declencher"]:
        cfg.update(en_cours=c["en_cours"], objectif_declenchements=c.get("objectif_declenchements", c.get("objectif_defenses", 0)), objectif_points=c.get("objectif_points", 0),
                   defense=json.loads(json.dumps(c["defense"])))
    return cfg


def resume_khan(cfg):
    l = [x for x in resume_config(cfg) if not x.startswith(("vagues suivantes, coffres", "arret :"))]
    l[0] = "KHAN : camp du Khan de l'alliance, commandants %s (le module tours ne les prend pas)" % ";".join(str(p) for p in cfg["commandants"])
    l.append("vagues suivantes : bannieres de rage (%s), juste ce qu'il faut pour remplir la barre" % ", ".join(
        "+%d %%" % BANNIERES[w] for w in cfg["bannieres"]))
    if cfg["declencher"]:
        l.append("le bot garde %d attaque(s) du Khan en route vers ton chateau principal (il declenche le Khan des que la barre est "
                 "pleine et qu'il en manque une)" % cfg["en_cours"])
        l.extend(khan_defense.resume_defense(cfg["defense"]))
        od, op = cfg.get("objectif_declenchements") or 0, cfg.get("objectif_points") or 0
        l.append("arret : %s ; les attaques du Khan deja en route sont defendues, puis defense du debut remise et soldats rappeles"
                 % (" ou ".join(x for x in (
            "apres %d declenchement(s) du Khan" % od if od else "", "apres %d points nomades (tablette 1, medaille 7)" % op if op else "") if x)
            or "aucun objectif (jusqu'a l'arret du bot ; la defense du debut est gardee dans captures\\khan_defense_initiale.json)"))
    else:
        l.append("declenchement du Khan : non (le module s'arrete quand la barre est pleine)")
    return l


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "evenements"
LANCEUR_ETIQUETTE = "EVENEMENTS"
LANCEUR_CLE = ModuleNomades.NOM_MODULE
LANCEUR_PRIORITE = -5          # comme Tours lev max : apres la defense ; avant les forteresses seulement pour sauter le blocage


def config_module(tout):
    """Reglages de la rubrique evenements -> configuration du module (ArretModule sinon)."""
    ev = tout.get(RUBRIQUE) if isinstance(tout, dict) else None
    c = ev.get("nomades") if isinstance(ev, dict) else None
    if not nomades_valides(c):
        raise ArretModule("Evenements : les reglages des nomades sont invalides ou absents. Regle-les dans l'interface (page Evenements).")
    ct = tout.get("tours") if isinstance(tout.get("tours"), dict) else {}
    bloc = {"mode": c["mode"], "cheval": c["cheval"], "echelles": False, "rayon": 50}
    if c["cheval"] == "CF":
        bloc["distance_plumes"] = c["distance_plumes"]
    if c["mode"] == "auto":
        bloc.update({"cotes": combat.normaliser_cotes(c["cotes"]), "soldats": list(c["soldats"]), "soldat": c["soldats"][0]})
    else:
        bloc.update({"presets": list(c["presets"]), "soldat": tours.ID_SOLDAT})
    cfg = {"commandants": list(c["commandants"]), "royaumes": [KID], "reglages": {str(KID): bloc},
           "chateaux": dict(ct.get("chateaux") or {}), "vagues_base": ct.get("vagues_base", tours.NB_VAGUES_BASE), "avec_bonus": True,
           "niveau_min": 0, "achat_echelles": False, "kid": KID, "x": c["x"], "y": c["y"], "passes": list(c["passes"]),
           "engins": {g: [list(p) for p in (c.get("engins") or ENGINS_DEFAUT)[g]] for g in ("flanc", "centre")},
           "coffres": dict(c.get("coffres") or {"actif": False, "types": [], "ordre": "forts"}),
           "soldats_suite": list(c.get("soldats_suite") or []),
           "remplir_cour": bool(c.get("remplir_cour") or c.get("soldats_cour")), "max_attaques": c.get("max_attaques", 0), "max_tablettes": c.get("max_tablettes", 0)}
    if "chateaux_manuels" in ct:
        cfg["chateaux_manuels"] = ct["chateaux_manuels"]
    return cfg


def lanceur_config(args, lire=None, sortie=print):
    cfg = config_module(tours.lire_config())
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    return {"effets": tours.charger_effets(sortie), "camps": charger_camps(), "types_effets": charger_types_effets()}


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
    module = ModuleNomades(jeu, cfg, sources, ressources["effets"], camps=ressources.get("camps"), types_effets=ressources.get("types_effets"),
                           sortie=sortie, lire=lire, simulation=args.simulation, confirmer=False, presets=presets, partage=partage)
    partage.reservations[ModuleNomades.NOM_MODULE] = module.commandants_gardes
    await module.preparer()
    return module
