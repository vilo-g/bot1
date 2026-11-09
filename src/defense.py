#!/usr/bin/env python3
"""
defense.py : module DEFENSE - quand un de tes chateaux est attaque, ses soldats partent se mettre a l'abri en soutien chez le
membre de l'alliance EN PROTECTION le plus proche (sur le meme monde), puis reviennent une fois l'attaque passee.

Reglages (interface, page Defense ; config.json rubrique "defense") :
  - seuil : a partir de combien de soldats dans l'attaque (estimation du jeu quand l'attaque apparait) le bot reagit ;
  - porte : option de secours payee en rubis - ouvrir la porte du chateau (les soldats ne sont pas tues) si l'attaque arrive
    dans moins de `secondes_porte` secondes (defaut 30) et que les soldats n'ont pas pu partir.
Fonctionnement :
  - le jeu annonce chaque attaque qui part vers un de tes chateaux (message gam : chateau vise, attaquant, duree du trajet,
    nombre de soldats estime). Le bot decide a l'apparition de l'attaque ;
  - seuls les soldats du chateau attaque partent (pas les engins/outils) ; plusieurs mondes attaques = chacun envoie les siens ;
  - pendant l'attaque, les autres modules (tours, forteresses, achats) sont en pause sur ce monde seulement ; ils reprennent
    quand l'attaque est passee et les soldats rentres ;
  - les soldats sont rappeles des que la derniere attaque sur le chateau est arrivee. Securite : la duree de stationnement
    demandee au jeu ne depasse pas l'heure d'arrivee de l'attaque + 10 min (arrondi a l'heure), ils rentrent seuls sinon.
Messages du jeu (capture du 08/11) : gam (attaque annoncee), gdi/ain (joueurs, alliance, temps de protection RPT),
sdi (infos de soutien : soldats du chateau, commandants), cds (envoi du soutien), mcm (rappel), mos (porte, 2 temps : prix
annonce puis confirmation).
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combat  # noqa: E402
import tours   # noqa: E402
from tours import ArretModule, ROYAUMES, duree  # noqa: E402

T = tours.T
RUBRIQUE = "defense"
SEUIL_MAX = 10000000
SECONDES_PORTE = 30
PRIX_PORTE_MAX = 2000               # rubis : au-dela, la porte n'est pas ouverte (securite)
MARGE_RAPPEL = 20                   # secondes apres l'arrivee de la derniere attaque avant de rappeler les soldats
VERIF_SOLDATS = 20                  # pendant une attaque : soldats du chateau reverifies (retours, nouvelles recrues)
ALLIANCE_VALIDE = 600               # liste de l'alliance relue toutes les 10 min au plus
PAUSE_CALME = 3                     # secondes entre deux passages sans attaque
PAUSE_ATTAQUE = 1
TYPE_ATTAQUE = 0                    # champ T d'un mouvement : 0 = attaque
TYPES_PAR_COMMANDANT = 10           # un envoi de soutien prend au plus 10 types de soldats (pas de limite de nombre)
MIN_PAR_TYPE = 100                  # defaut : un type de soldat a moins de 100 n'est pas envoye


def nom_monde(kid):
    return ROYAUMES.get(kid, "royaume %s" % kid)


# ============================================================================== configuration
def config_valide(cfg):
    try:
        s, p, sp = cfg["seuil"], cfg["porte"], cfg.get("secondes_porte", SECONDES_PORTE)
        cr = cfg.get("commandants_royaumes") or {}
        mp = cfg.get("min_par_type", MIN_PAR_TYPE)
        if not (isinstance(mp, int) and not isinstance(mp, bool) and 1 <= mp <= SEUIL_MAX):
            return False
        return (isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= SEUIL_MAX and isinstance(p, bool)
                and isinstance(sp, int) and not isinstance(sp, bool) and 5 <= sp <= 600 and isinstance(cr, dict)
                and all(str(k).isdigit() and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 99 for k, v in cr.items()))
    except (KeyError, TypeError, AttributeError):
        return False


def commandant_prioritaire(cfg, kid):
    """Numero du commandant choisi pour ce royaume (celui qui emmene le plus gros des soldats), ou None."""
    v = (cfg.get("commandants_royaumes") or {}).get(str(kid))
    return v if isinstance(v, int) else None


def resume_config(cfg):
    lignes = ["reaction a partir de %d soldat(s) dans l'attaque (estimation du jeu a son apparition) : tous les soldats du "
              "chateau attaque partent en soutien chez le membre de l'alliance en protection le plus proche" % cfg["seuil"],
              "les modules du monde attaque sont en pause jusqu'au retour des soldats"]
    lignes.append("types de soldats a moins de %d : laisses au chateau ; 10 types au plus par commandant, les plus nombreux "
                  "d'abord" % cfg.get("min_par_type", MIN_PAR_TYPE))
    cr = cfg.get("commandants_royaumes") or {}
    if cr:
        lignes.append("commandant prioritaire (emmene les soldats les plus nombreux, les autres completent) : "
                      + ", ".join("%s %d" % (nom_monde(int(k)), v) for k, v in sorted(cr.items(), key=lambda kv: int(kv[0]))))
    else:
        lignes.append("commandants : ceux qu'aucun module n'utilise d'abord")
    if cfg["porte"]:
        lignes.append("porte ouverte (payee en rubis, %d au plus) si l'attaque arrive dans moins de %d s et que les soldats "
                      "n'ont pas pu partir" % (PRIX_PORTE_MAX, cfg.get("secondes_porte", SECONDES_PORTE)))
    else:
        lignes.append("ouverture de la porte : non")
    return lignes


def charger_soldats(chemin_items=None):
    """{objet: royaumes autorises (liste) ou None} pour les SOLDATS (unites de corps a corps ou a distance), pas les engins."""
    with open(chemin_items or tours.FICHIER_ITEMS, encoding="utf-8") as f:
        items = json.load(f)
    res = {}
    for u in items.get("units", []):
        try:
            if u.get("group") == "Unit" and u.get("role") in ("melee", "ranged"):
                res[int(u["wodID"])] = combat._cibles_voyage(u.get("allowedToTravel"))
        except (KeyError, TypeError, ValueError):
            continue
    return res


def lire_mouvements(payload):
    """Message gam -> (mouvements [{mid, t, d, ta, sa, pt, tt, gs, kid}], noms {joueur: nom})."""
    d = T.json_ou_none(payload)
    if not isinstance(d, dict):
        return [], {}
    noms = {o.get("OID"): o.get("N") for o in d.get("O") or [] if isinstance(o, dict)}
    res = []
    for e in d.get("M") or []:
        m = (e or {}).get("M") if isinstance(e, dict) else None
        if not isinstance(m, dict) or not isinstance(m.get("MID"), int):
            continue
        ta, sa = m.get("TA"), m.get("SA")
        if not (isinstance(ta, list) and len(ta) >= 5):
            continue
        res.append({"mid": m["MID"], "t": m.get("T"), "d": m.get("D"), "ta": ta, "sa": sa if isinstance(sa, list) else [],
                    "pt": m.get("PT") or 0, "tt": m.get("TT") or 0, "gs": e.get("GS"), "kid": m.get("KID")})
    return res, noms


class ModuleDefense:
    NOM_MODULE = "DEFENSE"

    def __init__(self, jeu, cfg, chateaux, moi, soldats, partage=None, sortie=print, horloge=time.time, dormir=None,
                 simulation=False):
        self.jeu, self.cfg, self.sortie, self.horloge, self.simulation = jeu, cfg, sortie, horloge, simulation
        self.chateaux = {c["id"]: c for c in chateaux}         # nos chateaux : id -> {kid, id, x, y, type}
        self.moi = moi                                         # {"OID", "AID"}
        self.soldats = soldats
        self.partage = partage or tours.Partage()
        if not hasattr(self.partage, "royaumes_bloques"):
            self.partage.royaumes_bloques = set()
        self.nb_attaques = 0       # attaques LANCEES par ce module (aucune) : compte pour la limite du lanceur
        self.nb_detectees = 0
        self.nb_abris = 0
        self.attaques = {}         # mid -> {cid, kid, arrivee, gs, defendue, vue, nom}
        self.envois = {}           # cid -> [{mid, depart, tt, pos}] soutiens partis, pas encore rappeles
        self.retours = {}          # cid -> instant ou les soldats rappeles sont rentres
        self.echecs = {}           # cid -> raison du dernier envoi rate (pendant l'attaque en cours)
        self.verif = {}            # cid -> instant de la derniere verification des soldats
        self.porte = {}            # cid -> porte ouverte jusqu'a
        self.alliance, self.alliance_lue = None, float("-inf")
        self.recus = []            # messages gam pas encore lus
        self.bloques_avant = set()
        if isinstance(getattr(jeu, "ecouteurs", None), list):
            jeu.ecouteurs.append(self._ecouter)   # voit les attaques meme pendant le passage d'un autre module

    # ------------------------------------------------------------------ outils
    def _noter(self, texte):
        self.sortie(texte)

    def _ecouter(self, msg):
        if msg[0] == "gam" and msg[1] == 0:
            self.recus.append(msg[2])

    def _nom_chateau(self, cid):
        c = self.chateaux[cid]
        return "%s (%d,%d) au %s" % (tours.TYPES_CHATEAU.get(c["type"], "chateau"), c["x"], c["y"], nom_monde(c["kid"]))

    async def _demander(self, cmd, donnees, attendu=None, delai=10):
        await self.jeu.vider()
        await self.jeu.envoyer(cmd, donnees)
        rep = await self.jeu.attendre(attendu or cmd, delai)
        self._absorber()
        return rep

    def _absorber(self):
        if not isinstance(getattr(self.jeu, "ecouteurs", None), list):     # pas d'ecouteur (tests) : on lit la boite
            reste = []
            for m in self.jeu.recus:
                (self.recus.append(m[2]) if m[0] == "gam" and m[1] == 0 else reste.append(m))
            self.jeu.recus[:] = reste
        t = self.horloge()
        while self.recus:
            mouvements, noms = lire_mouvements(self.recus.pop(0))
            for mv in mouvements:
                self._mouvement(mv, noms, t)

    def _mouvement(self, mv, noms, t):
        cid = mv["ta"][3]
        if mv["t"] != TYPE_ATTAQUE or cid not in self.chateaux:
            return
        arrivee = t + max(0, mv["tt"] - mv["pt"])
        a = self.attaques.get(mv["mid"])
        if a is not None:                      # mise a jour (le bailli precise l'attaque en approchant)
            a["arrivee"] = arrivee
            if isinstance(mv["gs"], int):
                a["gs"] = mv["gs"]
            return
        gs = mv["gs"] if isinstance(mv["gs"], int) else None
        defendue = gs is None or gs >= self.cfg["seuil"]
        sa = mv["sa"]
        attaquant = noms.get(sa[4]) if len(sa) > 4 else None
        self.attaques[mv["mid"]] = {"cid": cid, "kid": self.chateaux[cid]["kid"], "arrivee": arrivee, "gs": gs,
                                    "defendue": defendue, "vue": t, "tardive": arrivee - t <= self.cfg.get("secondes_porte", SECONDES_PORTE)}
        self.nb_detectees += 1
        qui = "%s%s" % (attaquant or "un joueur", " (%s %d,%d)" % (sa[10], sa[1], sa[2]) if len(sa) > 10 else "")
        self._noter("ATTAQUE DETECTEE sur %s par %s : %s soldat(s) estime(s), arrivee dans %s. %s"
                    % (self._nom_chateau(cid), qui, gs if gs is not None else "?", duree(arrivee - t),
                       "Mise a l'abri des soldats." if defendue else
                       "Moins de %d soldats : rien a faire." % self.cfg["seuil"]))

    # ------------------------------------------------------------------ priorite sur les autres modules
    def _actives(self, t):
        return [a for a in self.attaques.values() if a["defendue"] and a["arrivee"] + MARGE_RAPPEL > t]

    def urgence(self):
        """Une attaque a traiter tout de suite (envoi, porte ou rappel) : les autres modules s'interrompent."""
        self._absorber()
        t = self.horloge()
        for a in self._actives(t):
            if a["arrivee"] > t and (a["cid"] not in self.envois or self.verif.get(a["cid"], 0) + VERIF_SOLDATS <= t):
                return True
        return any(self._rappel_du(cid, t) for cid in self.envois)

    def _rappel_du(self, cid, t):
        return cid in self.envois and not any(a["cid"] == cid for a in self._actives(t))

    def _maj_pauses(self, t):
        bloques = {a["kid"] for a in self._actives(t)}
        bloques |= {self.chateaux[c]["kid"] for c in self.envois}
        bloques |= {self.chateaux[c]["kid"] for c, fin in self.retours.items() if fin > t}
        for kid in sorted(bloques - self.bloques_avant):
            self._noter("PAUSE des autres modules au %s (attaque en cours)." % nom_monde(kid))
        for kid in sorted(self.bloques_avant - bloques):
            self._noter("REPRISE des autres modules au %s : attaque passee, soldats rentres." % nom_monde(kid))
        self.bloques_avant = bloques
        self.partage.royaumes_bloques.clear()
        self.partage.royaumes_bloques.update(bloques)

    # ------------------------------------------------------------------ passage
    async def cycle(self):
        self._absorber()
        t = self.horloge()
        par_chateau = {}
        for a in self._actives(t):
            if a["arrivee"] > t:
                par_chateau.setdefault(a["cid"], []).append(a)
        self._maj_pauses(t)
        for cid, liste in par_chateau.items():
            premiere = min(a["arrivee"] for a in liste)
            if cid not in self.envois or self.verif.get(cid, 0) + VERIF_SOLDATS <= self.horloge():
                await self._mettre_a_l_abri(cid, max(a["arrivee"] for a in liste))
            reste = premiere - self.horloge()
            if (self.cfg["porte"] and reste <= self.cfg.get("secondes_porte", SECONDES_PORTE) and self.porte.get(cid, 0) <= self.horloge()
                    and (cid in self.echecs or any(a["tardive"] for a in liste))):
                await self._ouvrir_porte(cid)
        for cid in list(self.envois):
            if self._rappel_du(cid, self.horloge()):
                await self._rappeler(cid)
        t = self.horloge()
        for mid in [m for m, a in self.attaques.items() if a["arrivee"] + MARGE_RAPPEL + 60 < t]:
            del self.attaques[mid]
        for cid in [c for c in self.echecs if c not in par_chateau]:
            del self.echecs[cid]
        self._maj_pauses(t)
        return PAUSE_ATTAQUE if (par_chateau or self.envois) else PAUSE_CALME

    # ------------------------------------------------------------------ alliance : membre en protection le plus proche
    async def _lire_alliance(self):
        if self.alliance is not None and self.horloge() - self.alliance_lue < ALLIANCE_VALIDE:
            return self.alliance
        rep = await self._demander("ain", {"AID": self.moi["AID"]})
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        membres = ((d or {}).get("A") or {}).get("M") if isinstance(d, dict) else None
        if isinstance(membres, list):
            self.alliance, self.alliance_lue = membres, self.horloge()
        return self.alliance or []

    async def protege_le_plus_proche(self, kid, x, y):
        """(distance, {"nom", "x", "y", "chateau"}) du chateau le plus proche d'un membre en protection sur ce monde, ou None."""
        meilleur = None
        for m in await self._lire_alliance():
            if not isinstance(m, dict) or m.get("OID") == self.moi["OID"] or not (isinstance(m.get("RPT"), (int, float)) and m["RPT"] > 0):
                continue
            for e in m.get("AP") or []:
                if isinstance(e, list) and len(e) >= 4 and e[0] == kid and isinstance(e[2], int) and isinstance(e[3], int):
                    dist = math.hypot(e[2] - x, e[3] - y)
                    if meilleur is None or dist < meilleur[0]:
                        meilleur = (dist, {"nom": m.get("N") or "?", "x": e[2], "y": e[3], "oid": m.get("OID")})
        return meilleur

    # ------------------------------------------------------------------ envoi des soldats
    def _commandants_libres(self, rep_sdi, kid):
        """Commandants libres, dans l'ordre d'utilisation : celui choisi pour ce royaume, puis ceux qu'aucun module n'utilise,
        puis les autres. Numeros = ordre d'affichage du jeu (comme dans les autres modules)."""
        t = self.horloge()
        cmds = tours.positions_commandants(rep_sdi)
        pris = {e["pos"] for liste in self.envois.values() for e in liste}
        modules = {p for liste in self.partage.commandants.values() for p in liste}
        prioritaire = commandant_prioritaire(self.cfg, kid)
        libres = [(pos, c) for pos, c in enumerate(cmds, 1)
                  if isinstance(c.get("ID"), int) and pos not in pris and self.partage.libre_a.get(pos, 0) <= t]
        libres.sort(key=lambda pc: (pc[0] != prioritaire, pc[0] in modules, pc[0]))
        return libres

    async def _mettre_a_l_abri(self, cid, fin_attaque):
        c = self.chateaux[cid]
        self.verif[cid] = self.horloge()
        cible = await self.protege_le_plus_proche(c["kid"], c["x"], c["y"])
        if cible is None:
            if self.echecs.get(cid) != "aucun protege":
                self._noter("   IMPOSSIBLE : aucun membre de l'alliance en protection au %s." % nom_monde(c["kid"]))
            self.echecs[cid] = "aucun protege"
            return False
        dist, p = cible
        rep = await self._demander("sdi", {"TX": p["x"], "TY": p["y"], "SX": c["x"], "SY": c["y"]}, delai=10)
        d = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
        if not isinstance(d, dict):
            self.echecs[cid] = "infos"
            self._noter("   le jeu n'a pas donne les infos de soutien (%s) : nouvel essai dans quelques secondes."
                        % ("code %s" % rep[1] if rep else "pas de reponse"))
            self.verif[cid] = self.horloge() - VERIF_SOLDATS + 3
            return False
        stock = combat.inventaire_depuis_adi((d.get("gui") or {}).get("I") or [])
        tous = [[w, n] for w, n in stock.items() if w in self.soldats and n > 0]
        mini = self.cfg.get("min_par_type", MIN_PAR_TYPE)
        miettes = sum(n for _, n in tous if n < mini)
        tous = [[w, n] for w, n in tous if n >= mini]            # les types trop peu nombreux restent (pas de commandant gache)
        if miettes and cid not in self.envois:
            self._noter("   %d soldat(s) de types a moins de %d restent au chateau (reglage)." % (miettes, mini))
        if not tous:
            if cid not in self.envois:
                self._noter("   aucun soldat dans %s : rien a envoyer." % self._nom_chateau(cid))
            self.echecs.pop(cid, None)
            return True
        tous.sort(key=lambda wn: (-wn[1], wn[0]))                 # les soldats les plus nombreux d'abord
        interdits = [w for w, _ in tous if self.soldats[w] is not None and c["kid"] not in self.soldats[w]]
        n_total = sum(n for _, n in tous)
        heures = max(1, min(12, math.ceil((fin_attaque - self.horloge() + 600) / 3600)))
        libres = self._commandants_libres(d, c["kid"])
        if not libres:
            self.echecs[cid] = "commandant"
            self._noter("   IMPOSSIBLE : aucun commandant libre pour emmener les soldats de %s." % self._nom_chateau(cid))
            return False
        if self.simulation:
            self._noter("[SIMULATION] %d soldat(s) de %s partiraient chez %s (%d,%d), a %.1f cases, commandant %d d'abord."
                        % (n_total, self._nom_chateau(cid), p["nom"], p["x"], p["y"], dist, libres[0][0]))
            self.envois.setdefault(cid, [])
            self.echecs.pop(cid, None)
            return True
        # chaque commandant emmene les 10 types de soldats les plus nombreux (pas de limite de nombre) ; s'il reste des types,
        # le commandant suivant complete.
        reste = [list(wn) for wn in tous]
        envoyes, raison, ptt = 0, "", 0
        for pos, cmd in libres:
            essais = 0
            while reste and essais < 4:
                armee = [list(wn) for wn in reste[:TYPES_PAR_COMMANDANT]]   # pas de limite de nombre : 10 types au plus
                rep = await self._demander("cds", {"SID": cid, "TX": p["x"], "TY": p["y"], "LID": cmd["ID"], "WT": heures, "HBW": -1,
                                                   "BPC": 0, "PTT": ptt, "SD": 0, "A": armee}, delai=10)
                r = T.json_ou_none(rep[2]) if rep and rep[1] == 0 else None
                m = ((r or {}).get("A") or {}).get("M") if isinstance(r, dict) else None
                if isinstance(m, dict) and isinstance(m.get("MID"), int):
                    t = self.horloge()
                    tt = m.get("TT") if isinstance(m.get("TT"), (int, float)) else 600
                    self.envois.setdefault(cid, []).append({"mid": m["MID"], "depart": t, "tt": tt, "pos": pos})
                    self.partage.libre_a[pos] = max(self.partage.libre_a.get(pos, 0), t + 12 * 3600)   # absent pour les autres modules
                    n = sum(q for _, q in armee)
                    envoyes += n
                    for w, q in armee:
                        for wn in reste:
                            if wn[0] == w:
                                wn[1] -= q
                    reste = [wn for wn in reste if wn[1] > 0]
                    self.nb_abris += 1
                    self._noter("SOLDATS A L'ABRI : %d soldat(s) de %s partis avec le commandant %d chez %s (%d,%d), a %.1f cases "
                                "(trajet %s)." % (n, self._nom_chateau(cid), pos, p["nom"], p["x"], p["y"], dist, duree(tt)))
                    break                                          # ce commandant est parti : le suivant complete
                raison = "code %s" % rep[1] if rep else "pas de reponse"
                essais += 1
                if interdits and any(w in interdits for w, _ in armee):          # unite qui ne voyage pas sur ce monde
                    n_int = sum(n for w, n in reste if w in interdits)
                    reste = [wn for wn in reste if wn[0] not in interdits]
                    self._noter("   %d soldat(s) ne peuvent pas voyager sur ce monde : ils restent au chateau." % n_int)
                    interdits = []
                elif ptt == 0:
                    ptt = 1                                        # sans cheval refuse : avec plumes
                else:
                    break                                          # ce commandant ne peut pas partir : le suivant
            if not reste:
                break
        if envoyes:
            self.echecs.pop(cid, None)
            if reste:
                self._noter("   %d soldat(s) restent dans %s (plus de commandant libre ou envoi refuse)."
                            % (sum(n for _, n in reste), self._nom_chateau(cid)))
            return True
        self.echecs[cid] = raison or "refuse"
        self._noter("   ECHEC de l'envoi des soldats de %s (%s) : nouvel essai dans %d s." % (self._nom_chateau(cid), raison, VERIF_SOLDATS))
        return False

    async def _rappeler(self, cid):
        t = self.horloge()
        fin = t
        for e in self.envois.pop(cid, []):
            if not self.simulation:
                rep = await self._demander("mcm", {"MID": e["mid"]})
                if not rep or rep[1] != 0:
                    self._noter("   rappel refuse par le jeu (%s) : les soldats rentreront seuls a la fin du stationnement."
                                % ("code %s" % rep[1] if rep else "pas de reponse"))
            retour = min(t - e["depart"], e["tt"]) + 30
            fin = max(fin, t + retour)
            self.partage.libre_a[e["pos"]] = t + retour
        self.retours[cid] = fin
        self._noter("RAPPEL : attaque passee sur %s, soldats rappeles (retour dans %s environ)." % (self._nom_chateau(cid), duree(fin - t)))

    async def _ouvrir_porte(self, cid):
        c = self.chateaux[cid]
        if self.simulation:
            self._noter("[SIMULATION] la porte de %s serait ouverte (rubis)." % self._nom_chateau(cid))
            self.porte[cid] = self.horloge() + 6 * 3600
            return
        rep = await self._demander("mos", {"CID": cid, "KID": c["kid"], "CD": 0})
        d = T.json_ou_none(rep[2]) if rep else None
        if rep and rep[1] != 0 and isinstance(d, dict) and isinstance(d.get("CC2T"), int):
            prix = d["CC2T"]
            if prix > PRIX_PORTE_MAX:
                self._noter("   porte NON ouverte : le jeu demande %d rubis (plus de %d)." % (prix, PRIX_PORTE_MAX))
                self.porte[cid] = self.horloge() + 600
                return
            rep = await self._demander("mos", {"cmdID": "mos", "CID": cid, "KID": c["kid"], "CD": 0, "CC2T": prix})
            d = T.json_ou_none(rep[2]) if rep else None
        else:
            prix = 0
        if rep and rep[1] == 0 and isinstance(d, dict):
            rpt = d.get("RPT") if isinstance(d.get("RPT"), (int, float)) else 6 * 3600
            self.porte[cid] = self.horloge() + rpt
            self._noter("PORTE OUVERTE sur %s (%s rubis) pendant %s : les soldats restes au chateau ne seront pas tues."
                        % (self._nom_chateau(cid), prix or "?", duree(rpt)))
        else:
            self.porte[cid] = self.horloge() + 60
            self._noter("   ouverture de la porte refusee par le jeu (%s)." % ("code %s" % rep[1] if rep else "pas de reponse"))


# ============================================================================== branchement dans le lanceur commun (src/bot.py)
LANCEUR_NOM = "defense"
LANCEUR_ETIQUETTE = "DEFENSE"
LANCEUR_CLE = ModuleDefense.NOM_MODULE
LANCEUR_PRIORITE = -10            # avant tout le reste (forteresses 0, tours 10, achats 20)


def lanceur_config(args, lire=None, sortie=print):
    cfg = tours.lire_config().get(RUBRIQUE)
    if not cfg or not config_valide(cfg):
        raise ArretModule("Module defense : reglages absents ou incomplets. Regle-le dans l'interface (page Defense), puis relance.")
    cfg = dict(cfg)
    cfg.setdefault("secondes_porte", SECONDES_PORTE)
    cfg["commandants"] = []
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in resume_config(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    try:
        return {"soldats": charger_soldats()}
    except (OSError, ValueError) as e:
        raise ArretModule("Module defense : liste des unites illisible (captures/items.json) : %s" % e)


async def lire_compte(jeu, essais=2):
    """(chateaux, {"OID", "AID"}) lus dans la reponse jaa (meme requete que le jeu a la connexion)."""
    for _ in range(essais):
        await jeu.vider()
        await jeu.envoyer("jca", {"CID": -1, "KID": 0})
        rep = await jeu.attendre("jaa", 15)
        if rep and rep[1] == 0:
            chateaux = tours.chateaux_depuis_jaa(rep[2])
            d = T.json_ou_none(rep[2]) or {}
            o = (d.get("gca") or {}).get("O") or {}
            if chateaux and isinstance(o.get("OID"), int):
                return chateaux, {"OID": o["OID"], "AID": o.get("AID"), "N": o.get("N")}
    return None, None


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    chateaux, moi = await lire_compte(jeu)
    if not chateaux:
        raise ArretModule("Module defense : liste de tes chateaux illisible.")
    if not isinstance(moi.get("AID"), int) or moi["AID"] < 0:
        raise ArretModule("Module defense : tu n'es dans aucune alliance (il faut un membre en protection pour abriter les soldats).")
    module = ModuleDefense(jeu, cfg, chateaux, moi, ressources["soldats"], partage=partage, sortie=sortie, simulation=args.simulation)
    sortie("")
    sortie("CHATEAUX SURVEILLES ET ABRI LE PLUS PROCHE (membre de l'alliance en protection) :")
    for c in chateaux:
        cible = await module.protege_le_plus_proche(c["kid"], c["x"], c["y"])
        sortie("  - %s : %s" % (module._nom_chateau(c["id"]), "%s (%d,%d), a %.1f cases" % (cible[1]["nom"], cible[1]["x"], cible[1]["y"], cible[0])
                                if cible else "AUCUN membre en protection sur ce monde"))
    return module
