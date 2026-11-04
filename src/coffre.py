#!/usr/bin/env python3
"""
coffre.py : pseudo et mot de passe du compte, enregistres depuis l'interface (page Compte).

Fichier : captures/identifiants.json (PRIVE, comme tout le dossier captures/ : ne jamais l'envoyer).
Le mot de passe y est CHIFFRE avec la protection de Windows (DPAPI, la meme que Chrome pour ses mots de passe) :
seul TON compte Windows, sur CE PC, peut le relire. Si le fichier part sur un autre PC (ex. dossier du bot envoye a un ami),
il est illisible la-bas : le bot redemande simplement le mot de passe.
Le mot de passe n'est jamais renvoye a la page de l'interface ni ecrit dans un journal.
Le bot ne s'en sert que si la session enregistree dans Chrome a expire.
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RACINE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FICHIER = os.path.join(RACINE, "captures", "identifiants.json")
DESCRIPTION = "GGE bot - mot de passe du compte"


class ErreurCoffre(Exception):
    pass


# ============================================================================== chiffrement Windows (DPAPI)
def _dpapi(donnees, chiffrer):
    if os.name != "nt":
        raise ErreurCoffre("Le mot de passe ne peut être enregistré (chiffré) que sous Windows.")
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    tampon = ctypes.create_string_buffer(donnees, len(donnees))
    entree = BLOB(len(donnees), ctypes.cast(tampon, ctypes.POINTER(ctypes.c_char)))
    sortie = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    PAS_DE_FENETRE = 0x1                                  # CRYPTPROTECT_UI_FORBIDDEN
    if chiffrer:
        ok = crypt32.CryptProtectData(ctypes.byref(entree), ctypes.c_wchar_p(DESCRIPTION), None, None, None, PAS_DE_FENETRE,
                                      ctypes.byref(sortie))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(entree), None, None, None, None, PAS_DE_FENETRE, ctypes.byref(sortie))
    if not ok:
        raise ErreurCoffre("Windows refuse de %s le mot de passe (fichier venu d'un autre PC ou d'un autre compte Windows ?)."
                           % ("chiffrer" if chiffrer else "déchiffrer"))
    try:
        return ctypes.string_at(sortie.pbData, sortie.cbData)
    finally:
        kernel32.LocalFree(sortie.pbData)


def chiffrer(texte):
    return base64.b64encode(_dpapi(texte.encode("utf-8"), True)).decode("ascii")


def dechiffrer(b64):
    return _dpapi(base64.b64decode(b64), False).decode("utf-8")


# ============================================================================== fichier
def _lire(fichier=None):
    try:
        with open(fichier or FICHIER, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _ecrire(d, fichier=None):
    chemin = fichier or FICHIER
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, chemin)


def verifier_pseudo(pseudo):
    if not isinstance(pseudo, str) or not pseudo.strip() or len(pseudo) > 60 or any(c in pseudo for c in "\r\n\t"):
        raise ErreurCoffre("Pseudo : écris le nom du compte (60 caractères au plus).")
    return pseudo.strip()


def enregistrer(pseudo, mdp=None, fichier=None):
    """Enregistre le pseudo, et le mot de passe (chiffre) s'il est donne. mdp=None : on garde celui deja enregistre."""
    pseudo = verifier_pseudo(pseudo)
    d = _lire(fichier)
    if d.get("pseudo") and d.get("pseudo") != pseudo and mdp is None:
        d.pop("mdp", None)                                  # autre compte : l'ancien mot de passe ne va pas avec
    d["pseudo"] = pseudo
    if mdp is not None:
        if not isinstance(mdp, str) or not mdp or len(mdp) > 200 or any(c in mdp for c in "\r\n"):
            raise ErreurCoffre("Mot de passe vide ou invalide.")
        d["mdp"] = chiffrer(mdp)
        d["protection"] = "windows-dpapi"
    _ecrire(d, fichier)
    return etat(fichier)


def effacer_mdp(fichier=None):
    d = _lire(fichier)
    d.pop("mdp", None)
    d.pop("protection", None)
    _ecrire(d, fichier)
    return etat(fichier)


def identifiants(fichier=None):
    """(pseudo, mot de passe) ; mot de passe None s'il n'y en a pas ou s'il est illisible sur ce PC."""
    d = _lire(fichier)
    pseudo = d.get("pseudo") if isinstance(d.get("pseudo"), str) else None
    if not d.get("mdp"):
        return pseudo, None
    try:
        return pseudo, dechiffrer(d["mdp"])
    except (ErreurCoffre, ValueError, OSError):
        return pseudo, None


def etat(fichier=None):
    """Ce que la page peut savoir (jamais le mot de passe lui-meme)."""
    d = _lire(fichier)
    pseudo, mdp = identifiants(fichier)
    return {"pseudo": pseudo, "mdp_enregistre": bool(d.get("mdp")), "mdp_lisible": mdp is not None,
            "windows": os.name == "nt"}
