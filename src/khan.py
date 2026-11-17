#!/usr/bin/env python3
"""
khan.py : branchement du module Khan (camp du Khan de l'alliance, evenement des nomades) dans le lanceur commun.
Le module lui-meme est dans evenements.py (ModuleKhan) ; reglages : rubrique evenements.khan de config.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import defense          # noqa: E402
import evenements       # noqa: E402
import khan_defense     # noqa: E402
import tours            # noqa: E402
from tours import ArretModule   # noqa: E402

RUBRIQUE = evenements.RUBRIQUE
LANCEUR_NOM = "khan"
LANCEUR_ETIQUETTE = "KHAN"
LANCEUR_CLE = evenements.ModuleKhan.NOM_MODULE
LANCEUR_PRIORITE = -5
# 16/11 (demande du joueur) : pendant le Khan, pas d'autre module qui attaque (tours, forteresses, tours lev max, nomades).
# Defense, recrutement, materiaux, achats et transferts peuvent tourner, seulement quand le Khan n'a rien a faire.
LANCEUR_INCOMPATIBLES = ("tours", "forteresses", "montee", "evenements")


def lanceur_config(args, lire=None, sortie=print):
    cfg = evenements.config_khan(tours.lire_config())
    sortie("")
    sortie("CONFIGURATION ENREGISTREE :")
    for ligne in evenements.resume_khan(cfg):
        sortie("  - " + ligne)
    return cfg


def lanceur_ressources(cfg, sortie=print):
    res = {"effets": tours.charger_effets(sortie), "camps": evenements.charger_camps(zone=evenements.ZONE_KHAN),
           "types_effets": evenements.charger_types_effets()}
    if cfg.get("declencher"):
        try:
            res["soldats_def"] = khan_defense.soldats_defensifs()
        except (OSError, ValueError) as e:
            raise ArretModule("Module Khan : liste des unites illisible (captures/items.json) : %s" % e)
    return res


async def chateau_principal(jeu):
    """Chateau principal du royaume vert (celui que le Khan attaque) : {"id","x","y","kid","type"}."""
    chateaux, _ = await defense.lire_compte(jeu)
    for c in chateaux or []:
        if c.get("kid") == khan_defense.KID and c.get("type") == 1:
            return c
    raise ArretModule("Module Khan : chateau principal du royaume vert introuvable (liste de tes chateaux illisible).")


async def lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie=print, lire=None):
    sources = await tours.determiner_sources(jeu, cfg, sortie, lire, memoriser=False)
    sortie("")
    sortie("CHATEAU DE DEPART : %s" % tours.lignes_sources(sources)[0])
    await tours.assurer_chevaux(jeu, cfg, sortie, lire, args.simulation)
    chateau = await chateau_principal(jeu) if cfg.get("declencher") else None
    if chateau is not None:
        sortie("CHATEAU DEFENDU CONTRE LE KHAN : chateau principal (%d,%d)" % (chateau["x"], chateau["y"]))
    module = evenements.ModuleKhan(jeu, cfg, sources, ressources["effets"], camps=ressources.get("camps"),
                                   types_effets=ressources.get("types_effets"), chateau=chateau,
                                   soldats_def=ressources.get("soldats_def"), sortie=sortie, lire=lire,
                                   simulation=args.simulation, confirmer=False, partage=partage)
    partage.reservations[evenements.ModuleKhan.NOM_MODULE] = module.commandants_gardes
    await module.preparer()
    return module
