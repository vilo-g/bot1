#!/usr/bin/env python3
"""
connexion_bot.py : connecte le bot au compte, reste connecte, et se deconnecte proprement quand tu l'arretes.

Lancement : outils\\lancer_bot_connexion.bat   (ou : py src\\connexion_bot.py)
Pseudo : option --pseudo, variable GGE_PSEUDO, sinon memorise dans config.json (jamais le mot de passe).
Mot de passe : enregistre dans l'interface (page Compte, chiffre par Windows), sinon variable GGE_MDP, sinon demande
a l'ecran (saisie masquee).
Arret : Ctrl+C. Le compte est deconnecte, un joueur peut alors se connecter sans conflit.
"""
import argparse
import asyncio
import getpass
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session import SessionBot, RACINE, T  # noqa: E402

CONFIG = os.path.join(RACINE, "config.json")


def lire_pseudo(arg):
    if arg:
        return arg
    if os.environ.get("GGE_PSEUDO"):
        return os.environ["GGE_PSEUDO"]
    try:
        with open(CONFIG, encoding="utf-8") as f:
            p = json.load(f).get("pseudo")
            if p:
                print("Pseudo : %s (memorise dans config.json)" % p)
                return p
    except (OSError, ValueError):
        pass
    return input("Pseudo du compte : ").strip()


def memoriser_pseudo(pseudo):
    try:
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        cfg["pseudo"] = pseudo
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def demander_identifiants_ecran(arg_pseudo):
    """Appelee SEULEMENT si la session enregistree dans Chrome a expire. Compte enregistre dans l'interface (page Compte,
    mot de passe chiffre par Windows) : utilise directement ; sinon pseudo et mot de passe demandes a l'ecran."""
    print("\n>>> Connexion necessaire : la session enregistree a expire.")
    try:
        import coffre
        p, m = coffre.identifiants()
    except Exception:                                      # le coffre ne doit jamais empecher la saisie a l'ecran
        p, m = None, None
    if p and m and (not arg_pseudo or arg_pseudo == p):
        print(">>> Compte enregistre dans l'interface : %s (mot de passe enregistre). S'il est refuse, corrige-le dans la page Compte." % p)
        memoriser_pseudo(p)
        return p, m
    pseudo = lire_pseudo(arg_pseudo)
    mdp = os.environ.get("GGE_MDP") or getpass.getpass(">>> Mot de passe (tape-le puis Entree, rien ne s'affiche) : ")
    if pseudo:
        memoriser_pseudo(pseudo)
    return pseudo, mdp


async def principal(args):
    try:
        async with SessionBot(demander_identifiants=lambda: demander_identifiants_ecran(args.pseudo)) as jeu:
            while True:
                await asyncio.sleep(5)
                if not await _vivante(jeu):
                    print("SESSION COUPEE : le serveur a ferme la connexion (le joueur s'est probablement connecte). "
                          "Le bot s'arrete.")
                    return
    except T.ErreurCDP as e:
        print("ERREUR :", e)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nArret demande.")


async def _vivante(jeu, essais=3, pause=5):
    """True si le WebSocket du jeu est ouvert. Chrome qui tarde a repondre (page du jeu occupee quelques secondes) n'est pas
    une connexion perdue : on reessaie ; False seulement si le jeu dit que le socket est ferme ou si Chrome reste muet."""
    for n in range(essais):
        try:
            r = await jeu._eval("(globalThis.__ggeSock && globalThis.__ggeSock.readyState)===1")
            return bool(r.get("result", {}).get("value"))
        except asyncio.TimeoutError:
            if n + 1 < essais:
                await asyncio.sleep(pause)
        except Exception:
            return False
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Connexion du bot au jeu, avec deconnexion propre a l'arret.")
    p.add_argument("--pseudo")
    a = p.parse_args()
    try:
        asyncio.run(principal(a))
    except KeyboardInterrupt:
        print("\nArret.")
