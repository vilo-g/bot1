#!/usr/bin/env python3
"""
bot.py : LANCEUR COMMUN. Une seule connexion au compte, et les modules choisis tournent ensemble.

Lancement : outils\\lancer_bot.bat   (ou : py src\\bot.py)
            outils\\lancer_bot.bat --modules tours            (seulement les tours)
            outils\\lancer_bot.bat --modules forteresses      (seulement les forteresses)
            outils\\lancer_bot.bat --modules tours;forteresses

Sans --modules, le lanceur demande quels modules activer (Entree = le dernier choix, garde dans config.json, rubrique
"lanceur"). Chaque module garde sa propre configuration (rubriques "tours", "forteresses"...).

Les modules ne sont PAS relies entre eux : chacun ne connait que l'objet commun `Partage` (tours.py) :
  - retour des commandants (un commandant pris par un module est absent pour les autres),
  - 4 s minimum entre deux attaques, tous modules confondus,
  - reservations (ex. le module forteresses garde des commandants communs avant qu'une forteresse soit attaquable),
  - messages de retour des armees (cat) donnes a tous les modules.
Les modules tournent chacun a leur tour (jamais deux demandes au jeu en meme temps) : le lanceur fait passer le module
dont l'heure est venue, puis attend le suivant.

PRIORITE : chaque module a un LANCEUR_PRIORITE (forteresses 0 = le plus prioritaire, tours 10). Si plusieurs modules sont
prets, le plus prioritaire passe d'abord. Un module qui a une methode urgence() (ex. forteresses : une forteresse attaquable et
un commandant libre) est appele tout de suite, et les modules moins prioritaires s'interrompent entre deux attaques pour lui
laisser la place (via le Partage, sans se connaitre).

AJOUTER UN MODULE : un fichier src/<nom>.py avec LANCEUR_NOM, LANCEUR_ETIQUETTE, LANCEUR_CLE (= NOM_MODULE de sa classe),
LANCEUR_PRIORITE (facultatif), lanceur_config(args, lire, sortie),
lanceur_ressources(cfg, sortie), async lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie, lire) -> objet avec
cycle() (renvoie les secondes a attendre), nb_attaques, cfg["commandants"] ; puis l'ajouter a la liste MODULES ci-dessous.

Options : --modules, --simulation, --max-attaques N (au total), --duree 3h, --reconfigurer, --oui, --avec-bonus, --pseudo,
--sans-ecran-allume (par defaut, l'ecran et le PC restent allumes pendant que le bot travaille).
Arret : Ctrl+C (deconnexion propre du compte).
"""
import argparse
import asyncio
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tours        # noqa: E402
import eveil        # noqa: E402
import forteresses  # noqa: E402
import achats       # noqa: E402
from tours import ArretModule, demander, duree  # noqa: E402

MODULES = [tours, forteresses, achats]  # ordre = numero affiche (1 = tours, 2 = forteresses, 3 = achats...)
RUBRIQUE = "lanceur"
VERIF_SESSION = 30                      # secondes max entre deux verifications que la connexion est vivante
PRIORITE_DEFAUT = 50                    # module sans LANCEUR_PRIORITE (plus petit = plus prioritaire)
URGENCE_SANS_EFFET = 30                 # un module appele en urgence qui n'attaque pas : urgence ignoree pendant 30 s
PAUSE_MAX_ARRET = 2                     # avec l'interface : arret demande vu en 2 s au plus
FICHIER_CONSOLE = os.path.join(tours.DOSSIER_CAPTURES, "journal_bot.txt")    # tout ce que le lanceur affiche (lu par l'interface)
FICHIER_ETAT = os.path.join(tours.DOSSIER_CAPTURES, "bot_etat.json")         # en marche ? modules, attaques (lu par l'interface)
FICHIER_ARRET = os.path.join(tours.DOSSIER_CAPTURES, "arret_demande")        # cree par l'interface : arret propre


# ============================================================================== choix des modules
def par_nom(modules=None):
    return {m.LANCEUR_NOM: m for m in (modules or MODULES)}


def analyseur_modules(modules=None):
    """'1;2' ou 'tours;forteresses' (melange possible) -> liste de noms, sans doublon, ordre conserve. ';' seulement."""
    modules = modules or MODULES
    noms = [m.LANCEUR_NOM for m in modules]

    def analyser(texte):
        parts = [p.strip().lower() for p in texte.strip().split(";")]
        if any(p == "" for p in parts):
            raise ValueError("Ecris des numeros separes par des ';', sans case vide (ex. 1 ou 2 ou 1;2).")
        res = []
        for p in parts:
            if p.isascii() and p.isdigit() and 1 <= int(p) <= len(noms):
                nom = noms[int(p) - 1]
            elif p in noms:
                nom = p
            else:
                raise ValueError("'%s' n'est pas un module : %s." % (p, ", ".join("%d = %s" % (i + 1, n) for i, n in enumerate(noms))))
            if nom not in res:
                res.append(nom)
        return res
    return analyser


def choisir_modules(args, lire=None, sortie=print, modules=None):
    """Modules a lancer : --modules, sinon question (Entree = dernier choix). Le choix est garde dans config.json."""
    modules = modules or MODULES
    analyser = analyseur_modules(modules)
    if getattr(args, "modules", None):
        try:
            choix = analyser(args.modules)
        except ValueError as e:
            raise ArretModule("--modules : %s" % e)
    else:
        lire = lire or input
        anc = (tours.lire_config().get(RUBRIQUE) or {}).get("modules") if isinstance(tours.lire_config(), dict) else None
        noms = [m.LANCEUR_NOM for m in modules]
        anc = [n for n in anc if n in noms] if isinstance(anc, list) and all(isinstance(n, str) for n in anc) else []
        sortie("")
        sortie("=" * 66)
        sortie(" MODULES A LANCER (une seule connexion, ils tournent ensemble)")
        sortie("=" * 66)
        for i, n in enumerate(noms):
            sortie("    %d = %s" % (i + 1, n))
        sortie("    Ecris les numeros separes par des ';'.   Exemples :  1   ou   2   ou   1;2")
        choix = demander(lire, sortie, "    Modules%s > " % (" (Entree = %s)" % ";".join(str(noms.index(n) + 1) for n in anc) if anc else ""),
                         analyser, anc or None)
    tout = tours.lire_config()
    if not isinstance(tout, dict) or (tout.get(RUBRIQUE) or {}).get("modules") != choix:
        rub = dict(tout.get(RUBRIQUE) or {}) if isinstance(tout, dict) and isinstance(tout.get(RUBRIQUE), dict) else {}
        rub["modules"] = choix                       # les autres reglages du lanceur (interface) sont gardes
        tours.memoriser_config(rub, rubrique=RUBRIQUE)
    return choix


# ============================================================================== l'ordonnanceur
class Entree:
    def __init__(self, nom, etiquette, module, priorite=PRIORITE_DEFAUT, rang=0):
        self.nom, self.etiquette, self.module = nom, etiquette, module
        self.priorite, self.rang = priorite, rang       # plus petite priorite = passe en premier ; rang = ordre choisi
        self.prochain = 0.0
        self.arrete = None          # raison de l'arret de ce module
        self.urgence_ignoree_jusqua = float("-inf")
        self.lents = 0              # reponses trop lentes de Chrome de suite (voir ordonnancer)

    def urgent(self, t):
        """Le module a une attaque a faire TOUT DE SUITE (ex. forteresse attaquable + commandant libre) ?"""
        f = getattr(self.module, "urgence", None)
        return f is not None and t >= self.urgence_ignoree_jusqua and bool(f())


def sortie_prefixee(etiquette, sortie=print):
    """Les lignes d'un module sont precedees de son nom : [TOURS] ..., [FORT] ... (lignes vides telles quelles)."""
    def ecrire(texte):
        sortie(("[%s] %s" % (etiquette, texte)) if str(texte).strip() else texte)
    return ecrire


def routeur_cat(entrees):
    """Un message de retour d'armee (cat) est donne a TOUS les modules : seul celui qui a envoye cette armee le reconnait."""
    def router(payload):
        for e in entrees:
            retour = getattr(e.module, "_retour", None)      # un module sans armee (achats) n'a rien a reconnaitre
            if retour is not None:
                retour(payload)
    return router


def texte_erreur(ex):
    """Erreur inattendue (bug) -> lignes lisibles pour la console et captures/journal_bot.txt (fichier et ligne du probleme)."""
    lignes = [l.rstrip() for l in traceback.format_exception(type(ex), ex, ex.__traceback__) for l in l.splitlines()]
    return [l for l in lignes if l.strip()][-12:]


LENTS_MAX = 5              # Chrome muet 5 passages de suite pour un module : ce module s'arrete
PAUSE_LENT = 10            # secondes avant de reprendre apres une reponse trop lente de Chrome
CHIEN_DE_GARDE = 120                    # secondes : un passage de module plus long est signale (et la connexion verifiee)


async def passage_surveille(e, vivante=None, sortie=print, arret=None, garde=None):
    """Fait passer le module ; si le passage dure plus de CHIEN_DE_GARDE s, le dit dans la console, verifie que la connexion
    est vivante (sinon : SESSION COUPEE) et laisse l'arret demande depuis l'interface interrompre le passage."""
    garde = garde or CHIEN_DE_GARDE
    tache = asyncio.ensure_future(e.module.cycle())
    debut = time.monotonic()
    try:
        while True:
            fait, _ = await asyncio.wait({tache}, timeout=garde)
            if fait:
                return tache.result()
            sortie("[%s] ATTENTION : ce passage dure depuis %d min sans finir (le bot attend le jeu ?) ; je verifie la connexion."
                   % (e.etiquette, round((time.monotonic() - debut) / 60)))
            if vivante is not None and not await vivante():
                raise ArretModule("SESSION COUPEE : la connexion au jeu ne repond plus (passage bloque). Tous les modules s'arretent.")
            if arret is not None and arret():
                return 0                     # l'arret demande est traite tout de suite par la boucle
    finally:
        if not tache.done():
            tache.cancel()
            try:
                await tache
            except (asyncio.CancelledError, Exception):
                pass


async def ordonnancer(entrees, horloge=time.time, dormir=asyncio.sleep, vivante=None, duree_max=None, max_attaques=None,
                      sortie=print, arret=None, battement=None):
    """Fait tourner les modules chacun a son tour jusqu'a Ctrl+C, la duree demandee, le nombre d'attaques demande,
    ou l'arret de tous les modules. Un module qui s'arrete (plus de soldat, refus du serveur...) n'arrete pas les autres."""
    fin = horloge() + duree_max if duree_max else None
    if fin is not None:
        sortie("Duree demandee : %s (arret et deconnexion vers %s)."
               % (duree(duree_max), time.strftime("%H:%M", time.localtime(fin))))
    derniere_verif = float("-inf")
    while True:
        t = horloge()
        if battement is not None:
            battement(entrees)
        if arret is not None and arret():
            sortie("Arret demande depuis l'interface : arret et deconnexion du compte. Les armees deja parties reviennent seules.")
            return
        if vivante is not None and t - derniere_verif >= VERIF_SESSION:
            if not await vivante():
                raise ArretModule("SESSION COUPEE : le serveur a ferme la connexion (le joueur s'est probablement connecte). "
                                  "Tous les modules s'arretent.")
            derniere_verif = t
        if fin is not None and t >= fin:
            sortie("Duree demandee atteinte (%s) : arret et deconnexion du compte. Les armees deja parties reviennent seules."
                   % duree(duree_max))
            return
        total = sum(e.module.nb_attaques for e in entrees)
        if max_attaques and total >= max_attaques:
            sortie("Nombre d'attaques demande atteint (%d, tous modules confondus) : arret." % max_attaques)
            return
        actives = [e for e in entrees if e.arrete is None]
        if not actives:
            sortie("Plus aucun module en marche : arret.")
            return
        # modules a faire passer maintenant : leur heure est venue, ou ils ont une urgence ; le plus prioritaire d'abord
        prets = [x for x in actives if x.prochain <= t or x.urgent(t)]
        if not prets:
            prochain = min(x.prochain for x in actives)
            pause = min(prochain - t, VERIF_SESSION if arret is None else PAUSE_MAX_ARRET)
            if fin is not None:
                pause = min(pause, max(0.0, fin - t))
            await dormir(pause)
            continue
        e = min(prets, key=lambda x: (x.priorite, x.rang))
        en_urgence = e.prochain > t
        avant = e.module.nb_attaques
        if max_attaques:
            e.module.max_attaques = e.module.nb_attaques + (max_attaques - total)      # le module s'arrete pile au total demande
        try:
            attente = await passage_surveille(e, vivante, sortie, arret)
            e.lents = 0
        except asyncio.TimeoutError:
            # Chrome n'a pas repondu a temps (page du jeu occupee, PC charge) : ce n'est pas un bug du module. On verifie que
            # le jeu est toujours connecte, puis le module reprend ; il ne s'arrete que si ca se repete
            e.lents += 1
            if vivante is not None and not await vivante():
                raise ArretModule("SESSION COUPEE : Chrome ne repond plus ou le jeu est deconnecte. Tous les modules s'arretent.")
            if e.lents >= LENTS_MAX:
                texte = ("Chrome n'a pas repondu a temps %d fois de suite (page du jeu bloquee ?). Recharge la page du jeu "
                         "ou relance le bot." % e.lents)
                e.arrete = texte
                sortie("")
                sortie("[%s] ARRET DE CE MODULE : %s" % (e.etiquette, texte))
                continue
            sortie("[%s] Chrome a mis plus de 20 s a repondre (page du jeu occupee ?) ; la connexion est bonne, le module "
                   "reprend dans %d s." % (e.etiquette, PAUSE_LENT))
            e.prochain = horloge() + PAUSE_LENT
            continue
        except tours.T.ErreurCDP:
            raise
        except Exception as ex:                       # bug dans un module : on le note en entier, ce module s'arrete, les autres continuent
            if isinstance(ex, ArretModule):
                if ex.args and str(ex.args[0]).startswith("SESSION COUPEE"):
                    raise
                texte = str(ex)
            else:
                texte = "ERREUR INATTENDUE (%s: %s) - envoie ces lignes pour correction :" % (type(ex).__name__, ex)
            e.arrete = texte
            sortie("")
            sortie("[%s] ARRET DE CE MODULE : %s" % (e.etiquette, texte))
            if not isinstance(ex, ArretModule):
                for ligne in texte_erreur(ex):
                    sortie("[%s]   %s" % (e.etiquette, ligne))
            restants = [x.etiquette for x in entrees if x.arrete is None]
            if restants:
                sortie("Les autres modules continuent : %s." % ", ".join(restants))
            continue
        e.prochain = horloge() + max(0.0, attente or 0.0)
        if en_urgence and e.module.nb_attaques == avant:
            e.urgence_ignoree_jusqua = horloge() + URGENCE_SANS_EFFET      # securite : pas de boucle sans fin sur une fausse urgence


# ============================================================================== lancement
def lire_args(argv=None):
    p = argparse.ArgumentParser(description="Lanceur commun : une connexion, plusieurs modules (voir l'en-tete du fichier).")
    p.add_argument("--modules", help="modules a lancer, separes par des ';' : tours, forteresses (ou 1;2)")
    p.add_argument("--reconfigurer", action="store_true", help="refaire les questions de configuration des modules choisis")
    p.add_argument("--simulation", action="store_true", help="tout faire sauf envoyer les attaques")
    p.add_argument("--max-attaques", type=int, default=None, help="s'arreter apres N attaques au total (test)")
    p.add_argument("--duree", type=tours.argument_duree, default=None, help="s'arreter apres cette duree : 90m, 3h, 2h30 (sinon : jusqu'a Ctrl+C)")
    p.add_argument("--oui", action="store_true", help="(sans effet : plus aucune confirmation par defaut)")
    p.add_argument("--confirmer", action="store_true", help="demander OUI avant la premiere attaque de chaque module (verification)")
    p.add_argument("--avec-bonus", action="store_true", help="applique le bonus de flanc/front du commandant (non verifie)")
    p.add_argument("--pseudo")
    p.add_argument("--sans-ecran-allume", action="store_true", help="laisser Windows mettre l'ecran en veille / verrouiller la session")
    p.add_argument("--config-ok", action="store_true", help="(interface) reprendre les configurations enregistrees sans les redemander")
    return p.parse_args(argv)


async def preparer(args, lire_sync=None, sortie=print, modules=None):
    """AVANT la connexion : modules choisis, configuration et fichiers de chacun -> [(module python, cfg, ressources)]."""
    choix = choisir_modules(args, lire_sync, sortie, modules)
    table = par_nom(modules)
    prets = []
    for nom in choix:
        m = table[nom]
        sortie("")
        sortie("#" * 66)
        sortie(" MODULE %s" % nom.upper())
        sortie("#" * 66)
        try:
            cfg = m.lanceur_config(args, lire_sync, sortie)
            prets.append((m, cfg, m.lanceur_ressources(cfg, sortie)))
        except ArretModule as ex:
            if len(choix) == 1:
                raise
            sortie("[%s] MODULE NON DEMARRE : %s" % (m.LANCEUR_ETIQUETTE, ex))   # les autres modules demarrent quand meme
    if not prets:
        raise ArretModule("Aucun module pret : arret.")
    return prets


async def demarrer(jeu, prets, args, sortie=print, lire=None):
    """APRES la connexion : demarre chaque module (dans l'ordre choisi) -> liste d'Entree pour l'ordonnanceur."""
    partage = tours.Partage()
    for m, cfg, _ in prets:
        partage.commandants[m.LANCEUR_CLE] = list(cfg["commandants"])
        partage.priorites[m.LANCEUR_CLE] = getattr(m, "LANCEUR_PRIORITE", PRIORITE_DEFAUT)
    entrees = []
    for rang, (m, cfg, res) in enumerate(prets):
        sortie("")
        sortie("----- demarrage du module %s -----" % m.LANCEUR_NOM)
        try:
            module = await m.lanceur_demarrer(jeu, cfg, res, args, partage, sortie_prefixee(m.LANCEUR_ETIQUETTE, sortie), lire)
        except ArretModule as ex:                                # ex. preselection pas debloquee : les autres modules demarrent
            sortie("[%s] MODULE NON DEMARRE : %s" % (m.LANCEUR_ETIQUETTE, ex))
            partage.commandants.pop(m.LANCEUR_CLE, None)
            continue
        entrees.append(Entree(m.LANCEUR_NOM, m.LANCEUR_ETIQUETTE, module, partage.priorites[m.LANCEUR_CLE], rang))
        if getattr(module, "urgence", None) is not None:
            partage.urgences[m.LANCEUR_CLE] = module.urgence      # les modules moins prioritaires lui laissent la place
    if not entrees:
        raise ArretModule("Aucun module n'a pu demarrer (voir les messages ci-dessus) : arret.")
    partage.routeur = routeur_cat(entrees)
    communs = {}
    for e in entrees:
        for p in e.module.cfg["commandants"]:
            communs.setdefault(p, []).append(e.nom)
    partages = {p: n for p, n in communs.items() if len(n) > 1}
    sortie("")
    sortie("MODULES EN MARCHE : %s (Ctrl+C = arret + deconnexion du compte).%s"
           % (", ".join(e.nom for e in entrees), "  MODE SIMULATION : rien n'est envoye." if args.simulation else ""))
    if len(entrees) > 1:
        sortie("  - ordre de priorite : %s (un module prioritaire qui a une attaque a faire passe avant les autres)"
               % " > ".join(e.nom for e in sorted(entrees, key=lambda x: (x.priorite, x.rang))))
    for p in sorted(partages):
        sortie("  - commandant %d utilise par : %s (un seul a la fois, jamais deux attaques avec lui)" % (p, " et ".join(partages[p])))
    return entrees


def ecrire_console(texte, fichier=None):
    """Affiche ET garde la ligne dans captures/journal_bot.txt (avec l'heure) : l'interface la montre en direct."""
    print(texte)
    try:
        chemin = fichier or FICHIER_CONSOLE
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "a", encoding="utf-8") as f:
            for ligne in str(texte).split("\n"):
                f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), ligne))
    except OSError:
        pass


def ecrire_etat(etat, fichier=None):
    try:
        chemin = fichier or FICHIER_ETAT
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(etat, f, ensure_ascii=False)
        os.replace(tmp, chemin)
    except OSError:
        pass


def battement_etat(modules, debut, horloge=time.time, fichier=None, tous_les=3):
    """Fonction appelee a chaque tour de l'ordonnanceur : met a jour captures/bot_etat.json (au plus toutes les 3 s)."""
    dernier = [float("-inf")]

    def battre(entrees):
        t = horloge()
        if t - dernier[0] < tous_les:
            return
        dernier[0] = t
        pieces = next((getattr(e.module.partage, "pieces", None) for e in entrees if getattr(e.module, "partage", None)), None)
        ecrire_etat({"etat": "en_marche", "pid": os.getpid(), "modules": modules, "debut": debut, "maj": t, "pieces": pieces,
                     "achats": {e.nom: e.module.nb_achats for e in entrees if hasattr(e.module, "nb_achats")},
                     "attaques": {e.nom: e.module.nb_attaques for e in entrees if not hasattr(e.module, "nb_achats")},
                     "arretes": {e.nom: e.arrete for e in entrees if e.arrete}}, fichier)
    return battre


async def principal(args):
    from session import SessionBot
    import connexion_bot

    async def lire_async(invite):
        return await asyncio.to_thread(input, invite)

    sortie = ecrire_console
    try:
        os.remove(FICHIER_ARRET)                     # une ancienne demande d'arret ne compte pas
    except OSError:
        pass
    debut = time.time()
    eveille = None
    try:
        prets = await preparer(args, sortie=sortie)
        modules = [m.LANCEUR_NOM for m, _, _ in prets]
        ecrire_etat({"etat": "connexion", "pid": os.getpid(), "modules": modules, "debut": debut, "maj": time.time()})
        eveil.console_sans_selection()                         # un clic dans la fenetre du bot ne le met plus en pause
        if getattr(args, "sans_ecran_allume", False):
            sortie("Ecran : reglages normaux de Windows (veille et verrouillage possibles : le jeu peut se deconnecter).")
        else:
            eveille = eveil.GarderEveille(sortie).__enter__()  # ecran et PC allumes (sinon le verrouillage coupe le jeu)
        async with SessionBot(sortie=sortie,
                              demander_identifiants=lambda: connexion_bot.demander_identifiants_ecran(args.pseudo)) as jeu:
            async def vivante():
                return await connexion_bot._vivante(jeu)
            try:
                entrees = await demarrer(jeu, prets, args, sortie, lire_async)
                battre = battement_etat(modules, debut)
                if entrees:
                    entrees[0].module.partage.signe_de_vie = lambda: battre(entrees)   # aussi pendant un long passage d'un module
                await ordonnancer(entrees, vivante=vivante, duree_max=args.duree, max_attaques=args.max_attaques, sortie=sortie,
                                  arret=lambda: os.path.exists(FICHIER_ARRET), battement=battre)
            except ArretModule as e:
                sortie("\n" + str(e))
    except ArretModule as e:
        sortie("\n" + str(e))
    except tours.T.ErreurCDP as e:
        sortie("ERREUR : %s" % e)
    except (KeyboardInterrupt, asyncio.CancelledError):
        sortie("\nArret demande.")
    except Exception as e:                                       # bug : jamais d'arret sans explication
        sortie("ERREUR INATTENDUE (%s: %s) : le bot s'arrete. Envoie ces lignes pour correction :" % (type(e).__name__, e))
        for ligne in texte_erreur(e):
            sortie("   " + ligne)
    finally:
        if eveille is not None:
            eveille.__exit__(None, None, None)
        ecrire_etat({"etat": "arrete", "pid": os.getpid(), "maj": time.time()})
        try:
            os.remove(FICHIER_ARRET)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(principal(lire_args()))
    except KeyboardInterrupt:
        print("\nArret.")
