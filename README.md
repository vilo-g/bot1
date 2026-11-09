# Bot Goodgame Empire (FR1) — attaques de forteresses et de tours

Ce dossier contient TOUT le projet. Tout ce qu'on apprend est noté dans `docs/`,
tout le code du bot ira dans `src/`.

## Principe retenu

Le message de connexion (`lli`) contient un jeton reCAPTCHA (`RCT`) fabriqué par la page, qu'un programme
ne peut pas produire. Le bot pilote donc un vrai Chrome (profil dédié `profil-chrome/`) :
1. `src/session.py` ouvre ce Chrome, reprend la session déjà enregistrée ou se connecte avec le mot de passe
   (enregistré dans l'interface, chiffré par Windows, ou demandé à l'écran), et se déconnecte proprement à l'arrêt ;
2. les modules (`src/tours.py`...) utilisent la connexion WebSocket de la page : ils lisent ce que le serveur
   envoie et envoient les ordres (`cra`, `gaa`, `adi`, `pin`...).

Tourne en local sur le PC de chaque joueur. Les identifiants ne quittent jamais la machine.

## Dossiers

| Dossier     | Contenu |
|-------------|---------|
| `docs/`     | Le protocole (`protocole.md`), les questions ouvertes (`a-decouvrir.md`), la conception (`conception.md`) et les captures du bot d'exemple (`exemple-bot/`) |
| `outils/`   | `observer.py` (observe), `test_envoi.py` (fonctions de base du jeu, utilisées par le bot), `connexion_auto.py` (connexion), `telecharger_items.py` (données du jeu), et les lanceurs `.bat` |
| `captures/` | Journaux enregistrés par `observer.py` (PRIVÉ, voir plus bas) |
| `src/`      | Le code du bot : `session.py` (connexion/déconnexion), `combat.py` (formules du jeu), `tours.py` (module tours), `forteresses.py` (module forteresses), `achats.py` (module achats : surplus de pièces dépensé en engins), `defense.py` (module défense : soldats à l'abri quand un château est attaqué), `transferts.py` (module transferts : ressources réparties entre les royaumes), `bot.py` (lanceur commun), `interface.py` + `interface.html` (interface graphique) |

## Lancer le bot

- `outils\lancer_bot_connexion.bat` : connexion seule (test de la session, Ctrl+C = déconnexion).
- **`outils\lancer_interface.bat` : interface graphique** (page dans le navigateur, sur ce PC : réglages des modules,
  démarrer / arrêter, journal en direct ; voir `docs/interface.md`).
- **`outils\lancer_bot.bat` : lanceur commun** (une seule connexion ; tu choisis les modules à faire tourner : tours,
  forteresses, ou les deux en même temps ; voir `docs/lanceur.md`). Exemple : `outils\lancer_bot.bat --modules 1;2`.
- `outils\lancer_bot_tours.bat` : module tours (questions de configuration au premier lancement, voir
  `docs/module-tours.md`). Option `--simulation` pour tout faire sans envoyer d'attaque.
- `outils\lancer_bot_forteresses.bat` : module forteresses (forteresses lues sur la carte du jeu, voir
  `docs/module-forteresses.md`). Mêmes options.

## Lancer une capture

1. Double-clic sur `outils/lancer_chrome.bat` : ouvre le Chrome réservé au bot.
2. Double-clic sur `outils/lancer_observer.bat` : branche l'observateur (laisse la fenêtre ouverte).
3. Connecte-toi au jeu dans ce Chrome, puis joue normalement (voir `docs/a-decouvrir.md`).
4. `Ctrl+C` dans la fenêtre de l'observateur. Les fichiers sont dans `captures/`.

## Règles de confidentialité

- `captures/` et `profil-chrome/` sont privés : ne jamais les envoyer ni les partager (`captures/identifiants.json` contient
  ton mot de passe chiffré). Pour donner le bot à un ami : interface, page Compte, « Créer la copie pour un ami ».
  `profil-chrome/` garde ta session de jeu.
- `observer.py` masque l'identifiant, le mot de passe et les jetons avant d'écrire.
  Avant d'envoyer un extrait quelque part, ouvre-le dans le Bloc-notes et cherche `"PW"` :
  tu dois lire `XXXX(longueur ...)`.

## Avancement

- [x] Format des messages, séquence de connexion, paquet d'attaque `cra` (voir `docs/protocole.md`)
- [x] Liste des commandants et châteaux : fournis par le serveur à la connexion
- [x] Carte : réponses à `gaa` (tours = type 2, forteresses = type 11, avec leur minuteur)
- [x] Troupes disponibles (`adi` / `abi`), durée du trajet et rapport de combat
- [x] Rythme du `pin` (60 s)
- [x] `test_envoi.py` écrit : vérifié sur un faux serveur dans un vrai Chromium, et son `cra` est identique octet pour octet
      à ceux du jeu
- [ ] Test d'envoi sur le VRAI jeu, compte de test (fait)
- [ ] Minuteurs des tours et forteresses, plafond de troupes par flanc, erreurs du serveur
- [x] Connexion automatique robuste + déconnexion propre (`src/session.py`)
- [x] Module tours : assistant de configuration, remplissage automatique, boucle d'attaque (`src/tours.py`)
- [x] Module tours validé sur le vrai jeu : cheval pièces/plumes, échelles, plusieurs commandants, retour réel, 4 s entre attaques
- [x] Module tours : plusieurs royaumes à la fois, tour la plus proche d'abord, châteaux lus sur le compte (à valider sur le vrai jeu)
- [x] Module tours : présélections (mode P, lues avec `gas`) codées, à valider sur le vrai jeu
- [x] Module tours : réglages séparés par royaume (mode, soldat, côtés/présélections, échelles, cheval, rayon), commandants communs
- [x] Module forteresses : assistant (commandants propres, royaumes 1/2/3, réglages par royaume, distance max), forteresses lues sur la carte du jeu avec leur minuteur, soldat le plus rapide, présélections (`src/forteresses.py`), à valider sur le vrai jeu
- [x] Module achats : à partir d'un seuil de pièces, 10 % du seuil dépensés en engins, répartis par royaume puis par engin (page Achats de l'interface)
- [x] Module défense : château attaqué -> ses soldats partent en soutien chez le membre de l'alliance en protection le plus proche, puis sont rappelés ; pause des autres modules sur ce monde ; porte de secours (rubis) en option
- [x] Module transferts : ressource spéciale de chaque royaume et bois/pierre en trop envoyés vers les royaumes qui en manquent
- [x] Module tours : option d'achat automatique d'échelles en bois (moins de 3000 -> 10 000 achetées si plus de 5 M de pièces)

## Attention

Ce genre d'automatisation est contraire aux conditions d'utilisation de Goodgame ;
un compte peut être suspendu. Tester d'abord sur un compte secondaire.
