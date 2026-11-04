"""
session.py : session du bot dans le jeu = connexion automatique + DECONNEXION PROPRE a l'arret.

Utilisation (dans un module du bot) :
    async with SessionBot(pseudo, mdp, sortie=print) as jeu:
        ... jeu.envoyer(...), jeu.attendre(...) ...
        if not await jeu.session_vivante(): ...   # a tester de temps en temps
A la sortie du bloc (fin normale, erreur, Ctrl+C) la session est fermee : le socket du jeu est coupe puis l'onglet
est vide (about:blank). Un joueur qui se reconnecte ensuite ne trouve donc pas le bot encore en ligne.
Filet de securite : si Python est tue (fermeture de la fenetre noire, arret brutal), l'onglet du jeu est ferme par
une requete synchrone vers Chrome.
"""
import asyncio
import atexit
import json
import os
import sys
import time
import urllib.request

RACINE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(RACINE, "outils"))
import test_envoi as T          # noqa: E402
import connexion_auto as C      # noqa: E402

CDP_HTTP = C.CDP_HTTP


def fermer_onglet_sync(id_onglet):
    """Ferme l'onglet par l'interface HTTP de Chrome (synchrone : utilisable dans atexit / fermeture de console)."""
    try:
        urllib.request.urlopen("%s/json/close/%s" % (CDP_HTTP, id_onglet), timeout=3).read()
        return True
    except Exception:
        return False


REPRISE_SANS_LLI = 10      # secondes : ecran du jeu charge sans demande de connexion -> session expiree


class SessionBot:
    def __init__(self, pseudo=None, mdp=None, sortie=print, url=None, delai_total=420, demander_identifiants=None,
                 delai_reprise=60):
        """pseudo/mdp : fournis d'avance, ou demandes SEULEMENT si la session enregistree dans Chrome a expire,
        via demander_identifiants() -> (pseudo, mdp)."""
        self.pseudo, self._mdp = pseudo, mdp
        self.demander_identifiants = demander_identifiants
        self.delai_reprise = delai_reprise
        self.sortie = sortie
        self.url = url or C.URL_JEU
        self.delai_total = delai_total
        self.ws = None
        self.nav = None
        self.jeu = None
        self.page = None
        self.id_onglet = None
        self.fermee = False
        self._handler_console = None

    # ------------------------------------------------------------------ ouverture
    async def __aenter__(self):
        import websockets
        t0 = time.time()
        self.sortie("[1/4] Ouverture de Chrome...")
        profil = os.path.join(RACINE, "profil-chrome")
        info = C.lancer_chrome(self.url, profil, lambda *_: None)
        self.ws = await websockets.connect(info["webSocketDebuggerUrl"], max_size=None)
        self.nav = T.Navigateur(self.ws)
        await self.nav.demarrer()
        self.page = await C.session_page(self.nav)
        if self.page is None:
            await self.nav.call("Target.createTarget", {"url": self.url})
            await asyncio.sleep(3)
            self.page = await C.session_page(self.nav)
        elif "goodgamestudios" not in self.nav.infos.get(self.page, ("", ""))[1] and self.url == C.URL_JEU:
            await self.nav.call("Page.navigate", {"url": self.url}, self.page)
            await asyncio.sleep(3)
        else:
            await self.nav.call("Page.reload", {"ignoreCache": False}, self.page)   # repart de zero : flags de connexion propres
            await asyncio.sleep(2)
        self.id_onglet = next((tid for tid, sid in self.nav.attaches.items() if sid == self.page), None)
        self._armer_filet()
        self.jeu = T.Jeu(self.nav)
        try:
            self.sortie("[2/4] Reprise de la session deja enregistree dans Chrome (jusqu'a %d s)..." % self.delai_reprise)
            if await self._reprise_session():
                self.sortie("   session reprise, pas de mot de passe necessaire.")
            else:
                self.sortie("[2/4] Session expiree : connexion avec le mot de passe (NE TOUCHE PAS a Chrome, environ 30 s).")
                if self.demander_identifiants and not (self.pseudo and self._mdp):
                    self.pseudo, self._mdp = self.demander_identifiants()
                if not (self.pseudo and self._mdp):
                    raise T.ErreurCDP("Session expiree et aucun identifiant fourni.")
                await asyncio.wait_for(self._connexion(), self.delai_total * 3)
            self.sortie("[3/4] Connecte. Detection du serveur...")
            await self._taille_normale()
            z = await self.jeu.detecter_zone()
            self.sortie("[4/4] PRET : serveur %s, pret en %d s. Ctrl+C = arret + deconnexion du compte."
                        % (z or "EmpireEx_3 (par defaut)", time.time() - t0))
        except asyncio.TimeoutError:
            await self.deconnecter()
            raise T.ErreurCDP("La connexion prend trop de temps (%d s). Relance, ou lance outils\\lancer_chrome.bat "
                              "et verifie la page." % self.delai_total)
        except BaseException:
            await self.deconnecter()
            raise
        finally:
            self._mdp = None            # le mot de passe ne reste pas en memoire une fois connecte
        return self.jeu

    async def _taille_normale(self):
        """Une fois connecte : la page reprend la taille de la fenetre Chrome (la taille imposee 1920x880 ne sert qu'a la
        connexion, pour cliquer au bon endroit). Le jeu redevient visible normalement ; le bot, lui, ne clique plus."""
        try:
            await self.nav.call("Emulation.clearDeviceMetricsOverride", {}, self.page)
            self.sortie("   fenetre du jeu remise a sa taille normale : tu peux regarder le compte dans Chrome.")
        except Exception:
            pass                                      # jamais bloquant

    async def _reprise_session(self):
        """Le jeu se reconnecte tout seul avec la session du profil Chrome : on attend de le voir connecte.
        Si le jeu s'est connecte AVANT que le bot ecoute le socket, la reponse de connexion (lli) est deja passee : on pose
        alors directement la question au jeu (petite demande de carte) au lieu d'attendre un message qui ne viendra pas."""
        fin = time.time() + self.delai_reprise
        prochaine_sonde = time.time() + 5
        charge_depuis = None                          # ecran d'accueil du jeu charge depuis... (champs de saisie presents)
        sondes_ratees = 0
        while time.time() < fin:
            try:
                if self.jeu.session is None:
                    await asyncio.wait_for(self.jeu.connecter(), 15)
                    prochaine_sonde = time.time() + 5
                if await self.jeu.est_connecte():
                    return True
                if (await self.jeu.code_login()) not in (None, "0"):
                    return False                      # le serveur a refuse la reprise : il faut le mot de passe
                if charge_depuis is None and await self.jeu.nb_champs_saisie() >= 4:
                    charge_depuis = time.time()
                    sondes_ratees = 0                 # seules comptent les sondes faites une fois l'ecran charge
                if time.time() >= prochaine_sonde:
                    if await self._sonde_connecte(3 if charge_depuis else 6):
                        return True
                    sondes_ratees += 1
                    prochaine_sonde = time.time() + (3 if charge_depuis else 8)
                # page chargee depuis REPRISE_SANS_LLI s, le jeu n'a pas demande de connexion et la sonde ne repond pas :
                # c'est l'ecran de connexion (session expiree) -> inutile d'attendre la fin du delai
                if (charge_depuis and time.time() - charge_depuis >= REPRISE_SANS_LLI and sondes_ratees >= 1
                        and not await self.jeu.lli_envoye()):
                    return False
            except (asyncio.TimeoutError, T.ErreurCDP):
                self.jeu.session = None
            await asyncio.sleep(1.5)
        try:                                          # derniere verification avant de demander le mot de passe
            if self.jeu.session is None:
                await asyncio.wait_for(self.jeu.connecter(), 15)
            return await self.jeu.est_connecte() or await self._sonde_connecte()
        except (asyncio.TimeoutError, T.ErreurCDP):
            return False

    async def _sonde_connecte(self, delai=6):
        """Demande au jeu une toute petite zone de carte (13x13 cases en 0,0, royaume vert) : il ne repond correctement
        (code 0) que si le compte est connecte. Rien n'est modifie dans le jeu."""
        ancienne = self.jeu.zone
        try:
            r = await self.jeu._eval("globalThis.__ggeZone||null")
            self.jeu.zone = ancienne or r.get("result", {}).get("value") or T.ZONE_PAR_DEFAUT
            await self.jeu.vider()
            await self.jeu.envoyer("gaa", {"KID": 0, "AX1": 0, "AY1": 0, "AX2": 12, "AY2": 12})
            rep = await self.jeu.attendre("gaa", delai)
            ok = bool(rep) and rep[1] == 0
            if ok:
                await self.jeu._eval("globalThis.__ggeLogin=true")
            return ok
        except (asyncio.TimeoutError, T.ErreurCDP):
            return False
        finally:
            self.jeu.zone = ancienne                  # la vraie zone est detectee ensuite (etape 3/4)

    async def _connexion(self, essais=3, delai_reponse=35, delai_essai=200):
        """Connexion avec protection : si le jeu ne reagit pas au clic sur « Se connecter » (bug du jeu), on
        actualise la page et on recommence (jusqu'a `essais` fois). Un refus du serveur (mauvais mot de passe)
        n'est JAMAIS retente : on s'arrete pour ne pas verrouiller le compte."""
        dossier = os.path.join(RACINE, "captures")
        derniere = None
        for n in range(1, essais + 1):
            if n > 1:
                self.sortie("   Le jeu n'a pas reagi : j'actualise la page et je recommence (essai %d/%d)..." % (n, essais))
                self.jeu.session = None
            try:
                await asyncio.wait_for(
                    C.connexion(self.nav, self.jeu, self.pseudo, self._mdp, sortie=self._msg_connexion,
                                delai_jeu=delai_reponse, dossier_diag=dossier, calibrage=C.lire_calibrage(dossier),
                                canevas=True, dossier_calib=dossier, recharger=n > 1,
                                deja_verifie=True), delai_essai)      # la reprise de session vient de verifier : pas connecte
                return
            except asyncio.TimeoutError as e:
                derniere = e
            except C.ErreurMonde:
                raise                                   # mauvais monde : reessayer ne changerait rien, le message dit quoi faire
            except T.ErreurCDP as e:
                if "refuse" in str(e).lower():          # mauvais identifiants : pas de nouvel essai
                    raise
                derniere = e
        raise T.ErreurCDP("La connexion a echoue apres %d essais (%s). Regarde la fenetre Chrome et relance."
                          % (essais, derniere or "pas de reponse"))

    def _msg_connexion(self, texte):
        """Garde seulement les messages utiles pour l'utilisateur."""
        t = texte.strip()
        if t.startswith("CONNECTE") or t.startswith("Deja connecte"):
            return
        if "ATTENTION" in t or "Calibrage" in t or "refuse" in t.lower() or "monde" in t.lower() or "prise en compte" in t:
            self.sortie("   " + t)

    # ------------------------------------------------------------------ surveillance
    async def session_vivante(self):
        """Faux si le socket du jeu est ferme (deconnecte par le serveur, ex. le joueur s'est connecte ailleurs)."""
        try:
            r = await self.jeu._eval("(globalThis.__ggeSock && globalThis.__ggeSock.readyState)===1")
            return bool(r.get("result", {}).get("value"))
        except Exception:
            return False

    # ------------------------------------------------------------------ fermeture
    async def deconnecter(self):
        if self.fermee:
            return
        self.fermee = True
        self.sortie("Deconnexion du compte...")
        try:
            if self.jeu is not None and self.jeu.session is not None:
                try:
                    await asyncio.wait_for(self.jeu._eval(
                        "(()=>{try{globalThis.__ggeSock.close(1000,'bot stop')}catch(e){};return true})()"), 4)
                except Exception:
                    pass
            if self.nav is not None and self.page is not None:
                try:
                    await asyncio.wait_for(self.nav.call("Page.navigate", {"url": "about:blank"}, self.page), 5)
                except Exception:
                    if self.id_onglet:
                        fermer_onglet_sync(self.id_onglet)
        finally:
            try:
                if self.ws is not None:
                    await self.ws.close()
            except Exception:
                pass
            self._desarmer_filet()
        self.sortie("Compte deconnecte : tu peux te connecter normalement.")

    async def __aexit__(self, exc_type, exc, tb):
        await asyncio.shield(self.deconnecter())
        return False

    # ------------------------------------------------------------------ filet de securite (arret brutal)
    def _armer_filet(self):
        self._filet = lambda: (not self.fermee) and self.id_onglet and fermer_onglet_sync(self.id_onglet)
        atexit.register(self._filet)
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

                def gestionnaire(evt):
                    if evt in (2, 5, 6):             # fermeture de la console / deconnexion / arret Windows
                        self._filet()
                    return False
                self._handler_console = HANDLER(gestionnaire)       # garder une reference
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._handler_console, True)
            except Exception:
                pass

    def _desarmer_filet(self):
        try:
            atexit.unregister(self._filet)
        except Exception:
            pass
