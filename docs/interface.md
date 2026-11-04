# Interface graphique (`src/interface.py` + `src/interface.html`)

Lancement : `outils\lancer_interface.bat` → la page s'ouvre dans le navigateur : <http://127.0.0.1:8770>.
Elle tourne **sur ton PC seulement** (rien sur Internet, rien à installer). Fermer la fenêtre noire de l'interface ne
coupe pas le bot.

## Pages

| Page | Contenu |
|------|---------|
| Tableau de bord | état du bot (arrêté / connexion / en marche, depuis quand, attaques par module), choix des modules, durée, nombre d'attaques maximum, simulation, **Démarrer** / **Arrêter proprement** |
| Compte | nom du compte et mot de passe (enregistrés sur ce PC, mot de passe chiffré par Windows), bouton pour effacer le mot de passe, copie du bot pour un ami |
| Tours | commandants, royaumes (vert, sables, glacier, feu) et pour chacun : mode, côtés ou présélections, échelles, cheval, **soldats** (images, fenêtre de choix, ordre modifiable), distance maximale (multiples de 10) |
| Forteresses | commandants, réservation des commandants communs (secondes, maximum), royaumes (sables, glacier, feu) et pour chacun : mode, côtés ou présélections, cheval, vagues à remplir, distance maximale |
| Journal | console du bot, journal tours, journal forteresses ; filtre, suivi automatique |

Le journal (attaques en or, erreurs en rouge, réservation / priorité en bleu ; étiquettes `TOURS` / `FORT`) est dans la
page **Journal**. En haut à droite : l'état du bot (**Arrêté**, **Connexion**, **Travaille**).

## Enregistrer

Les réglages sont les mêmes que ceux de l'assistant de la console et vont dans `config.json` (rubriques `tours`,
`forteresses`, `lanceur`). Chaque champ est vérifié par l'interface (mêmes règles que la console) : un champ faux est refusé
avec une explication, rien n'est enregistré. Les autres réglages (châteaux choisis, pseudo...) ne sont pas touchés.

## Démarrer / arrêter

- **Démarrer** : vérifie que les modules choisis sont réglés, puis ouvre une fenêtre noire pour le bot (même programme que
  `lancer_bot.bat`, avec `--config-ok` : la configuration n'est pas redemandée). Dans cette fenêtre : mot de passe
  seulement si la session a expiré ; ensuite les modules attaquent tout seuls, sans confirmation.
- **Arrêter proprement** : crée `captures/arret_demande` ; le bot le voit en 2 s au plus, s'arrête et déconnecte le compte.
  Ctrl+C dans la fenêtre du bot marche toujours.
- Le bot lancé par `lancer_bot.bat` apparaît aussi dans l'interface (fichier `captures/bot_etat.json`, mis à jour toutes
  les 3 s ; sans nouvelle depuis 30 s, il est considéré arrêté).

## Compte et mot de passe

- Page **Compte** : nom du compte et mot de passe. Le bot reprend d'abord la session enregistrée dans son Chrome ; le mot de
  passe ne sert que si elle a expiré. Enregistré, il n'est plus demandé dans la fenêtre du bot (toutes les façons de lancer :
  interface, `lancer_bot.bat`, modules seuls).
- Fichier `captures/identifiants.json` : le mot de passe y est **chiffré par Windows** (DPAPI, comme Chrome) pour ton compte
  Windows sur ce PC ; ailleurs il est illisible (le bot le redemande alors). Jamais renvoyé à la page ni écrit dans un journal.
- **Effacer le mot de passe** le retire du fichier. Mot de passe laissé vide à l'enregistrement = on garde celui enregistré.
- S'il est refusé par le jeu, le bot s'arrête sans réessayer (pour ne pas bloquer le compte) : corrige-le dans la page Compte.

## Envoyer le bot à un ami

Page Compte, **Créer la copie pour un ami** : `copie-pour-un-ami.zip` dans le dossier du bot, avec `src/`, `outils/`, `docs/`,
`README.md` et les données du jeu (`captures/items.json`). **Sans** `config.json`, `profil-chrome/` (ta session), ni le reste
de `captures/` (mot de passe, journaux, cartes, captures réseau). Ton ami décompresse, lance `outils\lancer_interface.bat`
et enregistre son propre compte. Ne lui envoie jamais ton dossier `bot` tel quel.

## Écran toujours allumé

Pendant que le bot travaille (tous les lanceurs), `src/eveil.py` garde le PC **et l'écran** allumés, comme si quelqu'un
l'utilisait : pas de mise en veille, pas d'écran éteint, pas d'économiseur (fonction Windows `SetThreadExecutionState`, celle
des lecteurs vidéo), et, si personne n'a touché au clavier ni à la souris depuis 4 minutes, une pression sur la touche F15
(qui ne fait rien dans les programmes) pour que Windows ne verrouille pas la session — un verrouillage déconnecte le jeu.
Quand tu utilises le PC, rien n'est envoyé. À l'arrêt du bot, Windows reprend ses réglages normaux. Limite : sur un portable,
fermer le couvercle peut quand même mettre le PC en veille (réglage d'alimentation de Windows).

## Sécurité

Le serveur n'écoute que sur 127.0.0.1, refuse les autres noms d'hôte, et refuse les envois qui ne viennent pas de la
page elle-même. Le mot de passe n'est jamais renvoyé à la page (elle sait seulement s'il y en a un).
