"""
combat.py : reproduit le « Remplir les vagues » du jeu (code lu dans Game.bundle + ggs.dll).

Formules du jeu (CombatConst) :
  maxAttaquants(niveau)      = 320 si niveau > 69, sinon min(260, 5*niveau + 8)
  soldats_flanc(niv, bonus)  = ceil(0.2 * maxAttaquants * (1 + bonus/100))
  soldats_centre(niv, bonus) = ceil((maxAttaquants - 2*ceil(0.2*maxAttaquants)) * (1 + bonus/100))
  outils_flanc(niv, extra)   = (niv<37:10, <50:20, <69:30, sinon ceil(40+extra))   (flanc gauche/droit)
  vagues_debloquees(niv)     = 1 + (niv>=13) + (niv>=26) + (niv>=51)   (niv = niveau du JOUEUR, pas de la cible)
« niv » = niveau du proprietaire de la cible, « bonus » = effet 28 (soldats en plus sur un flanc) du commandant
+ competences legendaires.

Choix du soldat (pickSoldierStack) : parmi les unites OFFENSIVES de l'inventaire, valeur d'une pile =
attaque * min(cases_libres, quantite_en_stock) ; on garde la meilleure unite de melee et la meilleure a distance,
puis on prend celle dont la valeur est la plus forte (les deux ratios valent 1 quand la defense est inconnue).
"""
import json
import math


def max_attaquants(niveau):
    return 320 if niveau > 69 else min(260, 5 * niveau + 8)


def _flanc_sans_bonus(niveau):
    return math.ceil(0.2 * max_attaquants(niveau))


def soldats_flanc(niveau, bonus=0):
    return math.ceil(0.2 * max_attaquants(niveau) * (1 + bonus / 100))


def soldats_centre(niveau, bonus=0):
    return math.ceil((max_attaquants(niveau) - 2 * _flanc_sans_bonus(niveau)) * (1 + bonus / 100))


def outils_flanc(niveau, extra=0):
    if niveau < 37:
        return 10
    if niveau < 50:
        return 20
    if niveau < 69:
        return 30
    return math.ceil(40 + extra)


def vagues_debloquees(niveau):
    return 1 + (niveau >= 13) + (niveau >= 26) + (niveau >= 51)


def charger_unites(chemin_items):
    """{wodID: fiche} des unites combattantes offensives (fightType 0) ; role melee/ranged."""
    with open(chemin_items, encoding="utf-8") as f:
        items = json.load(f)
    res = {}
    for u in items["units"]:
        if u.get("group") != "Unit":
            continue
        role = u.get("role")
        if role not in ("melee", "ranged"):
            continue
        att = float(u.get("meleeAttack" if role == "melee" else "rangeAttack") or 0)
        if att <= 0:
            continue
        res[int(u["wodID"])] = {"id": int(u["wodID"]), "role": role, "attaque": att,
                                "offensif": str(u.get("fightType")) == "0" or u.get("hybrid") in ("1", 1),
                                "vitesse": float(u.get("speed") or 0), "voyage": _cibles_voyage(u.get("allowedToTravel"))}
    return res


def _cibles_voyage(texte):
    """allowedToTravel ('0+-1#1+-1#10+15') -> royaumes ou l'unite peut aller (liste) ; None = aucune restriction."""
    if not texte:
        return None
    ks = []
    for part in str(texte).split("#"):
        k = part.partition("+")[0].strip()
        if k.lstrip("-").isdigit():
            ks.append(int(k))
    return ks or None


def soldats_par_vitesse(stock, unites, kid=None):
    """Unites offensives presentes en stock et autorisees a voyager dans ce royaume, de la PLUS RAPIDE (champ speed) a la
    plus lente ; a vitesse egale, la plus forte (attaque), puis la plus nombreuse."""
    cands = []
    for wid, qte in stock.items():
        u = unites.get(wid)
        if not u or not u["offensif"] or qte <= 0:
            continue
        if kid is not None and u.get("voyage") and kid not in u["voyage"] and -1 not in u["voyage"]:
            continue
        cands.append((u.get("vitesse", 0), u["attaque"], qte, wid))
    return [c[3] for c in sorted(cands, reverse=True)]


def choisir_soldat_rapide(stock, unites, kid=None):
    """Unite offensive la PLUS RAPIDE presente en stock (voir soldats_par_vitesse). None si aucune."""
    ordre = soldats_par_vitesse(stock, unites, kid)
    return ordre[0] if ordre else None


def inventaire_depuis_adi(inv):
    """Accepte [[id, qte], ...] ou {id: qte} ; ignore les quantites nulles."""
    if isinstance(inv, dict):
        paires = inv.items()
    else:
        paires = [(p[0], p[1]) for p in inv]
    return {int(i): int(q) for i, q in paires if int(q) > 0}


def choisir_soldat(stock, unites, cases):
    """Meilleure unite offensive pour une pile de `cases` soldats ; None si rien."""
    meilleur = {"melee": (0, None), "ranged": (0, None)}
    for wid, qte in stock.items():
        u = unites.get(wid)
        if not u or not u["offensif"]:
            continue
        valeur = u["attaque"] * min(cases, qte)
        if valeur > meilleur[u["role"]][0]:
            meilleur[u["role"]] = (valeur, wid)
    m, r = meilleur["melee"], meilleur["ranged"]
    if m[0] + r[0] == 0:
        return None
    if m[0] == 0:
        return r[1]
    if r[0] == 0:
        return m[1]
    return m[1] if m[0] >= r[0] else r[1]


def remplir_flanc_gauche(stock, unites, niveau_cible, bonus_flanc, nb_vagues=4, nb_echelles=10, id_echelle=614):
    """Calcule les vagues comme le remplissage auto (flanc gauche seulement) + echelles sur la vague 1.
    Retourne (vagues, stock_restant) ; vagues = liste de dict {"L":{"T":[],"U":[[id,q]]},"R":..,"M":..}."""
    stock = dict(stock)
    cap = soldats_flanc(niveau_cible, bonus_flanc)
    vagues = []
    for n in range(nb_vagues):
        wid = choisir_soldat(stock, unites, cap)
        unites_l = []
        if wid is not None:
            q = min(cap, stock[wid])
            stock[wid] -= q
            unites_l.append([wid, q])
        outils = [[id_echelle, nb_echelles]] if n == 0 else []
        vagues.append({"L": {"T": outils, "U": unites_l}, "R": {"T": [], "U": []}, "M": {"T": [], "U": []}})
    return vagues, stock


# ----------------------------------------------------------------------------- bonus du commandant
EFFET_FLANC = 28   # ATTACK_UNIT_AMOUNT_FLANK
EFFET_FRONT = 34   # ATTACK_UNIT_AMOUNT_FRONT
EFFET_VAGUES = 156  # additionalWaves : vagues supplementaires (equipement 29, relique 22013)
ZONE_TOUR = 2      # type de zone d'une tour (liste areaTypeID des effets PVE : 2,11,13,...)
ZONE_FORTERESSE = 11   # type d'objet de la carte d'une forteresse ([11, x, y, ...]) : meme numero que le type de zone


def effets_par_type(chemin_items, types=(EFFET_FLANC, EFFET_FRONT, EFFET_VAGUES)):
    """Tables de correspondance tirees de items.json.
    Retourne {"direct": {id: (type, zones|None)}, "relique": {id: (type, zones|None)}} :
      direct  : identifiants des effets de l'equipement (equipment_effects -> effects)
      relique : identifiants des effets des reliques/gemmes (relicEffects -> effects), ex. 301 -> 2108 (flanc)
    Les effets reserves aux combats entre joueurs (isPvPFight) sont ignores : une tour est un combat contre le jeu."""
    with open(chemin_items, encoding="utf-8") as f:
        items = json.load(f)
    effets = {}
    for e in items["effects"]:
        t = int(e["effectTypeID"])
        if t in types and str(e.get("isPvPFight")) != "1":
            zones = e.get("areaTypeID")
            effets[int(e["effectID"])] = (t, {int(z) for z in zones.split(",")} if zones else None)
    direct = {}
    for ee in items["equipment_effects"]:
        eid = int(ee["effectID"])
        if eid in effets:
            direct[int(ee["equipmentEffectID"])] = effets[eid]
    for eid, v in effets.items():
        direct.setdefault(eid, v)
    relique = {}
    for r in items["relicEffects"]:
        eid = int(r["effectID"])
        if eid in effets:
            relique[int(r["id"])] = effets[eid]
    return {"direct": direct, "relique": relique}


def _est_effet(x):
    return (isinstance(x, list) and len(x) >= 2 and isinstance(x[0], int) and isinstance(x[-1], list)
            and x[-1] and all(isinstance(v, (int, float)) for v in x[-1]))


def _parcourir(x, rappel, dans_relique=False):
    """Parcourt EQ. Les effets de l'equipement sont dans piece[5] ; ceux de la relique/gemme sont dans la liste
    imbriquee [idRelique, type, niveau, puissance, [effets...], 0] (piece[12][3])."""
    if not isinstance(x, list):
        return
    if _est_effet(x):
        rappel(x[0], x[-1][0], dans_relique)
        return
    for y in x:
        _parcourir(y, rappel, dans_relique)


def bonus_commandant(commandant, effets, zone=ZONE_TOUR, type_effet=EFFET_FLANC, plafond=50):
    """Total (en %, entier) des effets « soldats en plus » (flanc ou front) de l'equipement du commandant pour ce
    type de zone, plafonne (le jeu affiche « max : 50 % »).
    `commandant` = element de adi["gli"]["C"] ; `effets` = effets_par_type(...)."""
    total = 0.0

    def rappel(eid, valeur, relique):
        nonlocal total
        info = effets["relique" if relique else "direct"].get(eid)
        if info is None and not relique:
            # certaines pieces portent directement un effet de relique (ex. [20018, 100, [1.0]] = +1 vague)
            info = effets["relique"].get(eid)
        if info and info[0] == type_effet and (info[1] is None or zone in info[1]):
            total += valeur
    for piece in commandant.get("EQ", []):
        if len(piece) > 5:
            _parcourir(piece[5], rappel, False)
        if len(piece) > 12 and isinstance(piece[12], list) and len(piece[12]) > 3 and isinstance(piece[12][3], list):
            _parcourir(piece[12][3], rappel, True)
    return int(min(total, plafond))


def vagues_supplementaires(commandant, effets, zone=ZONE_TOUR):
    """Nombre de vagues en plus (effet 156 de l'equipement) pour ce commandant."""
    return int(bonus_commandant(commandant, effets, zone, EFFET_VAGUES, plafond=99))


def niveau_cible(adi):
    """Niveau de la cible tel que le serveur le donne dans adi (gaa.AI = [type,x,y,proprio,niveau,minuteur,royaume])."""
    ai = (adi.get("gaa") or {}).get("AI")
    return int(ai[4]) if ai and len(ai) > 4 else None


def niveau_tour_vert(victoires):
    """Royaume VERT : la carte et `adi` donnent le NOMBRE DE VICTOIRES de la tour (chateau de chevalier-brigand), pas son
    niveau. Niveau = floor(1,9 x victoires^0,555) + 1 : verifie sur les donnees du jeu (items.json, rubriques dungeons + lords :
    niveaux 1-9 jusqu'a 16 victoires, 10 a partir de 17, 20 a 64, 30 a 136, 45 a 288, 61 a 504, 71 a 665, 81 a 845) et en jeu
    (11 victoires = niveau 8, 3 attaques pour passer au 9)."""
    v = max(0, int(victoires or 0))
    return int(math.floor(1.9 * v ** 0.555)) + 1


def victoires_avant_niveau_suivant(victoires):
    """Attaques gagnees qu'il faut encore pour que la tour verte passe au niveau suivant."""
    v = max(0, int(victoires or 0))
    n = niveau_tour_vert(v)
    k = 1
    while niveau_tour_vert(v + k) == n:
        k += 1
    return k


def niveau_reel(kid, type_cible, valeur):
    """Niveau a utiliser pour les plafonds : tours (type 2) du royaume vert -> converti depuis les victoires ; sinon tel quel.
    (Royaumes 1 a 3 : le champ est deja de l'ordre du niveau ; au pire le bot met moins que le maximum, jamais plus.)"""
    if kid == 0 and type_cible == 2:
        return niveau_tour_vert(valeur)
    return valeur


def niveau_effectif(niveau_brut):
    """Niveau utilise pour le plafond. Observe : une tour de niveau 0 donne le meme plafond que le niveau 1
    (3 soldats par flanc avec +9 %), donc on prend au moins 1."""
    return max(1, int(niveau_brut or 0))


def vers_paquet(vague):
    """Vague calculee -> format du paquet cra (cases vides = [-1, 0], 2 cases par flanc gauche/droit, 3 outils
    et 6 unites au centre)."""
    def cases(liste, n):
        res = [list(x) for x in liste][:n]
        return res + [[-1, 0]] * (n - len(res))
    return {
        "L": {"T": cases(vague["L"]["T"], 2), "U": cases(vague["L"]["U"], 2)},
        "R": {"T": cases(vague["R"]["T"], 2), "U": cases(vague["R"]["U"], 2)},
        "M": {"T": cases(vague["M"]["T"], 3), "U": cases(vague["M"]["U"], 6)},
    }


# ----------------------------------------------------------------------------- remplissage multi-cotes (module tours)
# Lettres de l'utilisateur -> cotes de l'attaque. Le flanc unique est TOUJOURS le gauche.
COTES = {"F": ("L",), "FF": ("L", "R"), "A": ("M",), "AF": ("M", "L"), "AFF": ("M", "L", "R")}
NOMS_COTES = {"L": "flanc gauche", "R": "flanc droit", "M": "front"}


def plafond_cote(cote, niveau_cible, bonus_flanc=0, bonus_front=0):
    """Nombre maximal de soldats d'un cote ('L'/'R' = flanc, 'M' = front) pour une tour de ce niveau."""
    if cote == "M":
        return soldats_centre(niveau_cible, bonus_front)
    return soldats_flanc(niveau_cible, bonus_flanc)


def remplir_cotes(stock, unite, cotes, niveau_cible, nb_vagues, nb_echelles=0, id_echelle=614,
                  bonus_flanc=0, bonus_front=0):
    """Vagues « remplissage automatique » avec UNE seule unite (ex. 216) sur les cotes choisis.
    cotes : lettres 'L','R','M' (voir COTES). Chaque cote recoit le plafond de la tour (ou ce qu'il reste en stock).
    Echelles : `nb_echelles` sur la PREMIERE vague de chaque cote choisi (limitees par le stock d'echelles).
    Retourne (vagues, stock_restant, manques) ; manques = liste de textes (stock insuffisant) pour l'affichage."""
    stock = dict(stock)
    manques = []
    vagues = []
    for n in range(nb_vagues):
        vague = {c: {"T": [], "U": []} for c in ("L", "R", "M")}
        for c in ("M", "L", "R"):                       # ordre de remplissage : front d'abord, comme le jeu
            if c not in cotes:
                continue
            voulu = plafond_cote(c, niveau_cible, bonus_flanc, bonus_front)
            q = min(voulu, stock.get(unite, 0))
            if q > 0:
                stock[unite] -= q
                vague[c]["U"].append([unite, q])
            if q < voulu:
                manques.append("vague %d, %s : %d soldats au lieu de %d (stock de l'unite %d insuffisant)"
                               % (n + 1, NOMS_COTES[c], q, voulu, unite))
            if n == 0 and nb_echelles > 0:
                e = min(nb_echelles, stock.get(id_echelle, 0))
                if e > 0:
                    stock[id_echelle] -= e
                    vague[c]["T"].append([id_echelle, e])
                else:
                    manques.append("vague 1, %s : plus d'echelles en stock" % NOMS_COTES[c])
        vagues.append(vague)
    return vagues, stock, manques


def remplir_cotes_complets(stock, ordre, cotes, niveau_cible, nb_vagues, bonus_flanc=0, bonus_front=0, nb_echelles=0,
                           id_echelle=614):
    """Comme remplir_cotes, mais avec PLUSIEURS unites : chaque cote recoit le plafond de la cible, avec la 1re unite de
    `ordre` (la plus rapide), completee par la suivante si elle manque, etc. Au plus 2 unites differentes par flanc et 6 au
    front (cases du jeu). Retourne (vagues, stock_restant, manques, unites_utilisees)."""
    stock = dict(stock)
    manques, vagues, utilisees = [], [], []
    for n in range(nb_vagues):
        vague = {c: {"T": [], "U": []} for c in ("L", "R", "M")}
        for c in ("M", "L", "R"):                       # ordre de remplissage : front d'abord, comme le jeu
            if c not in cotes:
                continue
            voulu = plafond_cote(c, niveau_cible, bonus_flanc, bonus_front)
            reste, cases = voulu, NB_CASES[(c, "U")]
            for wid in ordre:
                if reste <= 0 or len(vague[c]["U"]) >= cases:
                    break
                q = min(reste, stock.get(wid, 0))
                if q > 0:
                    stock[wid] -= q
                    reste -= q
                    vague[c]["U"].append([wid, q])
                    if wid not in utilisees:
                        utilisees.append(wid)
            if reste > 0:
                manques.append("vague %d, %s : %d soldats au lieu de %d (plus assez de soldats offensifs en stock)"
                               % (n + 1, NOMS_COTES[c], voulu - reste, voulu))
            if n == 0 and nb_echelles > 0:              # echelles sur la 1re vague de chaque cote, comme remplir_cotes
                e = min(nb_echelles, stock.get(id_echelle, 0))
                if e > 0:
                    stock[id_echelle] -= e
                    vague[c]["T"].append([id_echelle, e])
                else:
                    manques.append("vague 1, %s : plus d'echelles en stock" % NOMS_COTES[c])
        vagues.append(vague)
    return vagues, stock, manques, utilisees


# ----------------------------------------------------------------------------- presélections (commande gas)
# Une presélection (champ A de la reponse gas, chaine JSON) = 6 groupes [id, quantite, id, quantite, ...] dans l'ordre des
# "flanks" du jeu (CastleAttackWaveVO : outils puis unites, chacun front / gauche / droite). Verifie sur les presélections
# du compte : groupe 3 = [216, 1060] (front), groupes 4 et 5 = [216, 361] (flancs), groupes 0 a 2 = outils.
GROUPES_PRESELECTION = (("M", "T"), ("L", "T"), ("R", "T"), ("M", "U"), ("L", "U"), ("R", "U"))
NB_CASES = {("M", "T"): 3, ("L", "T"): 2, ("R", "T"): 2, ("M", "U"): 6, ("L", "U"): 2, ("R", "U"): 2}


def outils_front(niveau):
    """CombatConst.getTotalAmountTools(1, niveau) : nombre maximal d'outils sur le front."""
    return 10 if niveau < 11 else 20 if niveau < 37 else 30 if niveau < 50 else 40 if niveau < 69 else 50


def plafond_groupe(cote, genre, niveau):
    """Capacite d'un groupe d'une vague (genre 'U' = soldats, 'T' = outils), SANS bonus de commandant : c'est ce que
    le jeu utilise pour appliquer une presélection (CastleAttackWaveVO)."""
    if genre == "U":
        return plafond_cote(cote, niveau)
    return outils_front(niveau) if cote == "M" else outils_flanc(niveau)


def decoder_preselection(texte):
    """Champ A d'une presélection -> 6 groupes [(id, quantite), ...] (cases vides ignorees). ValueError si illisible."""
    try:
        g = json.loads(texte) if isinstance(texte, str) else texte
        if not isinstance(g, list) or len(g) < 6:
            raise ValueError("moins de 6 groupes")
        return [[(int(grp[i]), int(grp[i + 1])) for i in range(0, len(grp) - 1, 2) if int(grp[i]) != -1] for grp in g[:6]]
    except (TypeError, ValueError, IndexError) as e:
        raise ValueError("presélection illisible (%s)" % e)


def charger_outils(chemin_items):
    """{wodID: {"npc": bool, "cibles": [(royaume, zone), ...]}} pour les outils (fiches avec un champ `typ`).
    `cibles` vide = aucune restriction (champ allowedToAttack, ex. '0+43#0+29' = royaume 0 zones 43 ou 29)."""
    with open(chemin_items, encoding="utf-8") as f:
        items = json.load(f)
    res = {}
    for u in items["units"]:
        if not u.get("typ"):
            continue
        cibles = []
        for part in str(u.get("allowedToAttack") or "").split("#"):
            k, _, z = part.partition("+")
            if k.strip().lstrip("-").isdigit() and z.strip().lstrip("-").isdigit():
                cibles.append((int(k), int(z)))
        res[int(u["wodID"])] = {"npc": str(u.get("canBeUsedToAttackNPC", "1")) != "0", "cibles": cibles}
    return res


def outil_utilisable(fiches, wid, kid, zone=ZONE_TOUR):
    """Meme test que AttackHelper.canUseToolForAttackOnTarget pour une cible sans joueur (tour) : l'outil doit pouvoir
    attaquer les PNJ et etre autorise sur ce royaume / ce type de zone. Outil inconnu ou pas de fiches : autorise."""
    f = (fiches or {}).get(wid)
    if f is None:
        return True
    if not f["npc"]:
        return False
    return not f["cibles"] or any(k in (-1, kid) and z in (-1, zone) for k, z in f["cibles"])


def cotes_de_preselection(groupes):
    """Cotes ('M', 'L', 'R') ou la presélection place des soldats (groupes de soldats non vides)."""
    return tuple(c for (c, g), entrees in zip(GROUPES_PRESELECTION, groupes) if g == "U" and entrees)


def appliquer_preselection(groupes, stock, niveau, outil_ok=None):
    """Remplit UNE vague comme le jeu (AttackDialogPresets.applyPresetToWave) : chaque groupe est parcouru dans l'ordre ;
    une entree est limitee par la place restante du groupe et par le stock ; une entree qui donne 0 (outil interdit sur
    la cible, plus de stock) est sautee sans prendre de case. Le stock est consomme (vague par vague).
    Retourne (vague, stock_restant, avertissements)."""
    stock = dict(stock)
    vague = {c: {"T": [], "U": []} for c in ("L", "R", "M")}
    avert = []
    for (cote, genre), entrees in zip(GROUPES_PRESELECTION, groupes):
        libre = plafond_groupe(cote, genre, niveau)
        for wid, voulu in entrees:
            if len(vague[cote][genre]) >= NB_CASES[(cote, genre)]:
                break
            if genre == "T" and outil_ok is not None and not outil_ok(wid):
                avert.append("outil %d interdit sur cette cible (%s) : ignore" % (wid, NOMS_COTES[cote]))
                continue
            q = min(voulu, libre)
            if min(q, stock.get(wid, 0)) < q:
                avert.append("%s : %d x %d voulus, %d en stock" % (NOMS_COTES[cote], q, wid, stock.get(wid, 0)))
                q = min(q, stock.get(wid, 0))
            if q > 0:
                stock[wid] -= q
                libre -= q
                vague[cote][genre].append([wid, q])
    return vague, stock, avert


def ordre_complement(stock, unites, kid=None, secours=None):
    """Unites pour completer une presélection : l'unite de remplacement choisie d'abord, puis les unites offensives A DISTANCE
    du chateau (la plus forte attaque d'abord), puis celles de CORPS A CORPS (la plus forte d'abord). Seulement celles en stock
    et autorisees a voyager dans ce royaume. Sans fichier des unites : seulement l'unite de remplacement."""
    ordre = [secours] if secours is not None and stock.get(secours, 0) > 0 else []
    if not unites:
        return ordre

    def permise(w):
        u = unites.get(w)
        return (u is not None and u["offensif"] and stock.get(w, 0) > 0 and w not in ordre
                and (kid is None or not u.get("voyage") or kid in u["voyage"] or -1 in u["voyage"]))
    for role in ("ranged", "melee"):
        ordre += sorted((w for w in stock if permise(w) and unites[w]["role"] == role), key=lambda w: (-unites[w]["attaque"], w))
    return ordre


def completer_preselection(vague, groupes, stock, niveau, ordre, nb_echelles=0, id_echelle=614):
    """Complete les soldats d'une vague remplie par une presélection : sur chaque cote ou la presélection met des soldats, ce qui
    manque (stock insuffisant, meme en partie) est complete avec les unites de `ordre` (voir ordre_complement), dans les cases
    libres du cote (2 par flanc, 6 au front ; une unite deja presente est simplement augmentee). Echelles : sur un cote ou la
    presélection n'avait plus AUCUN soldat (vague 1 seulement, si demande et s'il n'y a pas deja d'outil).
    Retourne (vague, stock_restant, ajouts [(cote, unite, quantite)], manques [textes])."""
    stock = dict(stock)
    ajouts, manques = [], []
    for (cote, genre), entrees in zip(GROUPES_PRESELECTION, groupes):
        if genre != "U" or not entrees:
            continue
        voulu = min(sum(q for _, q in entrees), plafond_groupe(cote, "U", niveau))
        place = sum(q for _, q in vague[cote]["U"])
        manque = voulu - place
        if manque <= 0:
            continue
        for wid in ordre:
            if manque <= 0:
                break
            dispo = stock.get(wid, 0)
            pile = next((pl for pl in vague[cote]["U"] if pl[0] == wid), None)
            if dispo <= 0 or (pile is None and len(vague[cote]["U"]) >= NB_CASES[(cote, "U")]):
                continue
            q = min(manque, dispo)
            stock[wid] -= q
            manque -= q
            if pile is not None:
                pile[1] += q
            else:
                vague[cote]["U"].append([wid, q])
            ajouts.append((cote, wid, q))
        if manque > 0:
            manques.append("%s : %d soldats au lieu de %d (plus assez de soldats offensifs)" % (NOMS_COTES[cote], voulu - manque, voulu))
        if place == 0 and nb_echelles > 0 and vague[cote]["U"] and not vague[cote]["T"]:
            e = min(nb_echelles, stock.get(id_echelle, 0))
            if e > 0:
                stock[id_echelle] -= e
                vague[cote]["T"].append([id_echelle, e])
    return vague, stock, ajouts, manques


if __name__ == "__main__":
    # verifs : tour de niveau 1 -> 3 / 7 / 3 (observe avec l'autofill du jeu)
    assert (soldats_flanc(1), soldats_centre(1), soldats_flanc(1)) == (3, 7, 3), (soldats_flanc(1), soldats_centre(1))
    assert soldats_flanc(1, 25) == 4
    assert soldats_flanc(70) == 64 and soldats_centre(70) == 192
    v, reste, m = remplir_cotes({216: 100, 614: 25}, 216, COTES["AFF"], 1, 2, 10)
    assert v[0]["M"]["U"] == [[216, 7]] and v[0]["L"]["U"] == [[216, 3]] and v[0]["R"]["U"] == [[216, 3]]
    assert v[0]["M"]["T"] == [[614, 10]] and v[0]["L"]["T"] == [[614, 10]] and v[0]["R"]["T"] == [[614, 5]]
    assert v[1]["L"]["T"] == [] and not m[1:], m
    # presélections : 'FORTERESSE' / '6 9 12 15' du compte, appliquees a une tour de niveau 71 (front 192, flancs 64, 40 outils / 50 au front)
    g = decoder_preselection("[[650,20,648,10,276,20],[298,20,266,30],[649,40,287,10],[216,1060],[216,361],[216,361]]")
    assert g[3] == [(216, 1060)] and g[0] == [(650, 20), (648, 10), (276, 20)] and len(g) == 6
    v, reste, av = appliquer_preselection(g, {216: 5000, 650: 99, 648: 99, 276: 99, 298: 99, 266: 99, 649: 99, 287: 99}, 71)
    assert v["M"]["U"] == [[216, 192]] and v["L"]["U"] == [[216, 64]] and v["R"]["U"] == [[216, 64]] and not av
    assert v["M"]["T"] == [[650, 20], [648, 10], [276, 20]] and v["L"]["T"] == [[298, 20], [266, 20]] and v["R"]["T"] == [[649, 40]], v
    assert reste[216] == 5000 - 320 and reste[266] == 99 - 20     # flanc : 40 outils maxi -> 20 + 20 (30 demandes)
    assert outil_utilisable({25: {"npc": True, "cibles": [(0, 29)]}}, 25, 0, 2) is False
    assert outil_utilisable({25: {"npc": True, "cibles": [(0, 29)]}}, 25, 0, 29) is True
    assert outil_utilisable({5: {"npc": False, "cibles": []}}, 5, 3) is False and outil_utilisable({}, 99, 3) is True
    # soldat le plus rapide : 277 (vitesse 100) devant 216 (34) ; a vitesse egale la plus forte ; unites interdites dans le royaume ignorees
    un = {277: {"offensif": True, "attaque": 97, "vitesse": 100, "voyage": None}, 216: {"offensif": True, "attaque": 310, "vitesse": 34, "voyage": None},
          35: {"offensif": True, "attaque": 148, "vitesse": 50, "voyage": [0]}, 34: {"offensif": True, "attaque": 160, "vitesse": 50, "voyage": None}}
    assert choisir_soldat_rapide({277: 5, 216: 999, 34: 9}, un, 3) == 277
    assert choisir_soldat_rapide({216: 999, 35: 9, 34: 9}, un, 3) == 34 and choisir_soldat_rapide({216: 999, 35: 9}, un, 3) == 216
    assert choisir_soldat_rapide({35: 9, 216: 0}, un, 0) == 35 and choisir_soldat_rapide({}, un, 3) is None
    assert _cibles_voyage("0+-1#1+-1#10+15") == [0, 1, 10] and _cibles_voyage(None) is None
    print("combat.py : formules coherentes avec les observations")
