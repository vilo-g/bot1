#!/usr/bin/env python3
"""
sessions.py : historique des sessions du bot (duree, pieces et rubis gagnes), affiche dans l'interface (encadre Session).

Le jeu envoie gcu {"C1": pieces, "C2": rubis} a chaque changement (et le bot le demande au demarrage). Gagne = somme des
HAUSSES entre deux valeurs : un achat (engins, porte, deplacement accelere) ne fait pas baisser les gains.
Les 10 dernieres sessions sont gardees dans captures/sessions.json (la plus ancienne est supprimee a la 11e).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tours   # noqa: E402

FICHIER = os.path.join(tours.DOSSIER_CAPTURES, "sessions.json")
MAX_SESSIONS = 10
SAUVER_TOUS_LES = 15            # secondes entre deux ecritures du fichier pendant la session


def lire(fichier=None):
    try:
        with open(fichier or FICHIER, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [s for s in d if isinstance(s, dict) and isinstance(s.get("id"), str)] if isinstance(d, list) else []


def ecrire(liste, fichier=None):
    chemin = fichier or FICHIER
    try:
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(liste[-MAX_SESSIONS:], f, ensure_ascii=False)
        os.replace(tmp, chemin)
    except OSError:
        pass


def supprimer(id_session, fichier=None):
    liste = lire(fichier)
    reste = [s for s in liste if s["id"] != id_session]
    if len(reste) == len(liste):
        return False
    ecrire(reste, fichier)
    return True


def pour_interface(fichier=None, etat_perime=None):
    """Plus recente d'abord. Une session marquee en cours dont le bot ne donne plus de nouvelles = terminee (bot coupe net)."""
    t = time.time()
    res = []
    for s in reversed(lire(fichier)):
        s = dict(s)
        if s.get("en_cours") and etat_perime is not None and t - float(s.get("fin", 0)) > etat_perime:
            s["en_cours"] = False
        s["duree"] = max(0, int(float(s.get("fin", 0)) - float(s.get("debut", 0))))
        res.append(s)
    return res


class Compteur:
    def __init__(self, debut=None, pseudo="", fichier=None, horloge=time.time):
        self.fichier, self.horloge = fichier, horloge
        debut = horloge() if debut is None else debut
        self.session = {"id": "%d" % int(debut * 1000), "pseudo": pseudo or "", "debut": debut, "fin": debut,
                        "pieces": 0, "rubis": 0, "en_cours": True}
        self.dernier = {"C1": None, "C2": None}
        self.sauve = float("-inf")
        self.supprimee = False
        ecrire(lire(fichier) + [self.session], fichier)              # 11e session : la plus ancienne part

    def ecouter(self, msg):
        """Ecouteur du jeu (Jeu.ecouteurs) : gcu, ou bloc "gcu" dans une autre reponse (achat...)."""
        try:
            cmd, code, payload = msg[0], msg[1], msg[2]
        except (TypeError, IndexError):
            return
        if code != 0 or not isinstance(payload, str) or (cmd != "gcu" and '"gcu"' not in payload):
            return
        d = tours.T.json_ou_none(payload)
        if isinstance(d, dict):
            self.noter(d if cmd == "gcu" else d.get("gcu"))

    def noter(self, d):
        if not isinstance(d, dict):
            return
        for k, cle in (("C1", "pieces"), ("C2", "rubis")):
            v = d.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            avant = self.dernier[k]
            if avant is not None and v > avant:
                self.session[cle] += int(v - avant)
            self.dernier[k] = v

    def sauver(self, fin=False):
        t = self.horloge()
        if not fin and t - self.sauve < SAUVER_TOUS_LES:
            return
        self.sauve = t
        self.session["fin"] = t
        self.session["en_cours"] = not fin
        liste = lire(self.fichier)
        for i, s in enumerate(liste):
            if s["id"] == self.session["id"]:
                liste[i] = dict(self.session)
                ecrire(liste, self.fichier)
                return
        self.supprimee = True                     # supprimee depuis l'interface : on ne la recree pas
