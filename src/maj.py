#!/usr/bin/env python3
"""
maj.py : MISES A JOUR du bot depuis GitHub (depot public vilo-g/bot1), pour le bouton « Mettre a jour » de l'interface.

- La version installee est dans version.json (a la racine du bot) ; celle du depot est lue sur GitHub.
- Mettre a jour = telecharger le depot (zip), verifier qu'il est complet et plus recent, garder une copie de la version
  actuelle (captures/sauvegardes/), puis remplacer SEULEMENT le programme : src/, outils/, docs/, README.md, version.json et
  les donnees du jeu (captures/items.json). Jamais touches : config.json (reglages, pseudo), captures/ (mot de passe chiffre,
  journaux, cartes), profil-chrome/ (session du jeu).
- Dans src/ et outils/, un fichier qui n'existe plus dans la nouvelle version est supprime (il a ete enleve du bot).
Bibliotheque standard seulement.
"""
import io
import json
import os
import shutil
import time
import urllib.request
import zipfile

DEPOT = "vilo-g/bot1"
BRANCHE = "main"
URL_VERSION = "https://raw.githubusercontent.com/%s/%s/version.json" % (DEPOT, BRANCHE)
URL_ZIP = "https://codeload.github.com/%s/zip/refs/heads/%s" % (DEPOT, BRANCHE)
FICHIER_VERSION = "version.json"
DOSSIERS_PROGRAMME = ("src", "outils")              # remplaces en entier (fichiers enleves = supprimes)
DOSSIERS_AJOUT = ("docs",)                          # remplaces / ajoutes, rien n'est supprime (notes locales gardees)
FICHIERS_RACINE = ("README.md", FICHIER_VERSION)
CAPTURES_PARTAGEES = ("items.json", "LISEZ-MOI.txt")  # donnees du jeu, rien de personnel
OBLIGATOIRES = (FICHIER_VERSION, "src/interface.py", "src/bot.py", "src/tours.py", "outils/test_envoi.py")
TAILLE_MAX = 200 * 1024 * 1024
DELAI_RESEAU = 20
SAUVEGARDES_GARDEES = 3


class ErreurMaj(Exception):
    """Message clair pour l'utilisateur."""


def _telecharger(url, delai=DELAI_RESEAU):
    req = urllib.request.Request(url, headers={"User-Agent": "gge-bot-maj", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=delai) as r:
        corps = r.read(TAILLE_MAX + 1)
    if len(corps) > TAILLE_MAX:
        raise ErreurMaj("Fichier de mise a jour trop gros.")
    return corps


def lire_version(texte_ou_dict):
    """{'version': entier >= 1, 'date': ..., 'nouveautes': [...]} ou None si illisible."""
    try:
        d = json.loads(texte_ou_dict) if isinstance(texte_ou_dict, (str, bytes)) else texte_ou_dict
        v = d.get("version")
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            notes = d.get("nouveautes") if isinstance(d.get("nouveautes"), list) else []
            return {"version": v, "date": str(d.get("date") or ""), "nouveautes": [str(n) for n in notes][:30]}
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def version_locale(racine):
    try:
        with open(os.path.join(racine, FICHIER_VERSION), encoding="utf-8") as f:
            return lire_version(f.read())
    except OSError:
        return None


def verifier(racine, telecharger=_telecharger):
    """Compare la version installee a celle du depot. Ne leve jamais : {'locale', 'distante', 'a_faire', 'erreur'}."""
    locale = version_locale(racine)
    res = {"locale": locale, "distante": None, "a_faire": False, "erreur": None, "depot": DEPOT}
    try:
        distante = lire_version(_texte(telecharger(URL_VERSION)))
        if distante is None:
            res["erreur"] = "Le fichier de version du depot est illisible."
        else:
            res["distante"] = distante
            res["a_faire"] = locale is None or distante["version"] > locale["version"]
    except ErreurMaj as e:
        res["erreur"] = str(e)
    except Exception as e:                              # pas d'Internet, GitHub injoignable...
        res["erreur"] = "Impossible de joindre GitHub (%s)." % (getattr(e, "reason", None) or e.__class__.__name__)
    return res


def _texte(b):
    return b.decode("utf-8") if isinstance(b, bytes) else b


def _contenu_zip(donnees):
    """Zip du depot -> {chemin relatif ('src/bot.py'): octets}. Refuse les chemins douteux."""
    try:
        z = zipfile.ZipFile(io.BytesIO(donnees))
    except zipfile.BadZipFile:
        raise ErreurMaj("Le fichier telecharge n'est pas un zip valide.")
    fichiers = {}
    for info in z.infolist():
        if info.is_dir():
            continue
        morceaux = info.filename.replace("\\", "/").split("/")
        rel = "/".join(morceaux[1:])                    # 1er dossier = bot1-main/
        if not rel or rel.startswith("/") or ".." in rel.split("/") or ":" in rel:
            raise ErreurMaj("Le zip contient un chemin interdit : %s" % info.filename)
        fichiers[rel] = z.read(info)
    return fichiers


def _a_installer(rel):
    haut = rel.split("/")[0]
    if haut in DOSSIERS_PROGRAMME + DOSSIERS_AJOUT:
        return "__pycache__" not in rel.split("/") and not rel.endswith(".pyc")
    if rel in FICHIERS_RACINE:
        return True
    return rel.startswith("captures/") and rel.split("/", 1)[1] in CAPTURES_PARTAGEES


def sauvegarder(racine, locale):
    """Copie de la version actuelle (programme seulement) : captures/sauvegardes/version-N-date.zip ; garde les 3 dernieres."""
    dossier = os.path.join(racine, "captures", "sauvegardes")
    os.makedirs(dossier, exist_ok=True)
    horo = time.strftime("%Y%m%d-%H%M%S")
    chemin, n = None, 0
    while chemin is None or os.path.exists(chemin):      # deux mises a jour dans la meme seconde : on numerote
        n += 1
        chemin = os.path.join(dossier, "%s-%02d-version-%s.zip" % (horo, n, locale["version"] if locale else "inconnue"))
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as z:
        for haut in DOSSIERS_PROGRAMME + DOSSIERS_AJOUT:
            for d, sous, noms in os.walk(os.path.join(racine, haut)):
                sous[:] = [x for x in sous if x != "__pycache__"]
                for n in noms:
                    p = os.path.join(d, n)
                    z.write(p, os.path.relpath(p, racine))
        for n in FICHIERS_RACINE:
            if os.path.exists(os.path.join(racine, n)):
                z.write(os.path.join(racine, n), n)
    anciennes = sorted(f for f in os.listdir(dossier) if "-version-" in f and f.endswith(".zip"))
    for f in anciennes[:-SAUVEGARDES_GARDEES]:
        try:
            os.remove(os.path.join(dossier, f))
        except OSError:
            pass
    return chemin


def installer(racine, bot_en_marche=False, telecharger=_telecharger):
    """Installe la derniere version du depot. Renvoie {'ok', 'version', 'avant', 'sauvegarde', 'fichiers', 'supprimes'}."""
    if bot_en_marche:
        raise ErreurMaj("Arrete d'abord le bot (bouton Arreter), puis relance la mise a jour.")
    locale = version_locale(racine)
    try:
        donnees = telecharger(URL_ZIP, 120)
    except ErreurMaj:
        raise
    except Exception as e:
        raise ErreurMaj("Telechargement impossible (%s). Verifie ta connexion Internet." % (getattr(e, "reason", None) or e.__class__.__name__))
    fichiers = _contenu_zip(donnees)
    manquants = [f for f in OBLIGATOIRES if f not in fichiers]
    if manquants:
        raise ErreurMaj("La version du depot est incomplete (manque %s) : rien n'a ete change." % ", ".join(manquants))
    nouvelle = lire_version(_texte(fichiers[FICHIER_VERSION]))
    if nouvelle is None:
        raise ErreurMaj("Le fichier de version du depot est illisible : rien n'a ete change.")
    if locale and nouvelle["version"] <= locale["version"]:
        return {"ok": True, "deja": True, "version": nouvelle["version"], "avant": locale["version"]}
    a_ecrire = {rel: b for rel, b in fichiers.items() if _a_installer(rel)}
    sauvegarde = sauvegarder(racine, locale)
    # 1) ecriture dans des fichiers temporaires, 2) remplacement : un probleme au milieu ne laisse pas de fichier a moitie ecrit
    temporaires = []
    try:
        for rel, b in a_ecrire.items():
            dest = os.path.join(racine, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".maj-tmp"
            with open(tmp, "wb") as f:
                f.write(b)
            temporaires.append((tmp, dest))
        for tmp, dest in temporaires:
            os.replace(tmp, dest)
    except OSError as e:
        for tmp, _ in temporaires:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise ErreurMaj("Ecriture impossible (%s). La copie de l'ancienne version est dans %s." % (e, sauvegarde))
    supprimes = []
    gardes = {rel for rel in a_ecrire}
    for haut in DOSSIERS_PROGRAMME:
        for d, sous, noms in os.walk(os.path.join(racine, haut)):
            sous[:] = [x for x in sous if x != "__pycache__"]
            for n in noms:
                p = os.path.join(d, n)
                rel = os.path.relpath(p, racine).replace(os.sep, "/")
                if rel not in gardes:
                    try:
                        os.remove(p)
                        supprimes.append(rel)
                    except OSError:
                        pass
    return {"ok": True, "deja": False, "version": nouvelle["version"], "avant": locale["version"] if locale else None,
            "nouveautes": nouvelle["nouveautes"], "sauvegarde": sauvegarde, "fichiers": len(a_ecrire), "supprimes": supprimes}
