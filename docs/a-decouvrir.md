# Ce qu'il reste à découvrir

## Déjà résolu (2 captures)

- Connexion : faite par le joueur, le bot ne s'en occupe pas (jeton reCAPTCHA `RCT`).
- Maintien de connexion : `pin` toutes les 60 s, `nch` toutes les 30 s.
- Carte : réponse à `gaa`, avec les tours (type 2) et les forteresses (type 11) et leur minuteur.
- Inventaire de troupes et liste des commandants : dans la réponse à `adi` / `abi`.
- Paquet d'attaque `cra`, durée du trajet (`TT`), rapport de combat (`bls`).
- Commandants : numéro affiché = position dans la liste triée par `ID`.

## Questions encore ouvertes

| Question | Pourquoi | Comment le savoir |
|----------|----------|-------------------|
| **Comment le bot ENVOIE un paquet depuis le Chrome connecté** | c'est le point technique central | `outils/test_envoi.py` : écrit et vérifié sur un faux serveur ; **à valider sur le vrai jeu** (compte de test) |
| Sens exact du minuteur `T` (tour : pourquoi 9180 et pas 10800 ?) | choisir quand réattaquer | redemander le même carré 10 min puis 1 h plus tard et comparer |
| Minuteur `T` des forteresses | idem | même méthode, sur une forteresse attaquée |
| Plafond de troupes par flanc | construire `A` sans erreur | attaques manuelles avec plusieurs commandants |
| Comment passer d'une préselection aux flancs du `cra` | idem | à défaut : enregistrer le `cra` d'une attaque manuelle et le rejouer (« modèle ») |
| Réponse du serveur à un `cra` refusé (tour en attente, pas assez de troupes, commandant occupé) | gérer les erreurs | envoyer volontairement un `cra` qui doit échouer, pendant le test d'envoi |
| `adi` / `abi` / `gas` obligatoires avant `cra` ? | éviter des envois inutiles | test d'envoi |
| `adi` pour les tours et `abi` pour les forteresses : toujours ? | choisir la bonne demande | observer d'autres cibles |
| Rapport de combat d'une forteresse | confirmer le résultat | laisser l'attaque sur la forteresse arriver (600 s de trajet) avant de couper |
| ~~Monde vert (KID 0) : tours ?~~ | résolu | tours `[2,x,y,-1,0,-1,0]` (niveau 0, minuteur -1) attaquables, confirmé par le joueur |
| Remplissage automatique : règles exactes (capacité par flanc, choix unité/outils) | automatiser sans modèle | `test_envoi.py --apprendre` : enregistre le `cra` du jeu ; comparer pour plusieurs commandants |

## Piste technique pour l'envoi

Le bot se branche sur le Chrome du joueur par le port de débogage (comme `observer.py`).
Lecture : événements réseau (déjà fait). Envoi : retrouver le WebSocket déjà ouvert par le jeu dans la page
et lui faire envoyer le message, sans ouvrir de nouvelle connexion. À tester d'abord avec un `pin`, puis un `gaa`,
puis un `adi`, et seulement après un `cra` sur une tour facile.

## Capture suivante (facultative, utile pour les minuteurs)

1. Même procédure que d'habitude, mais en laissant tourner **au moins 30 min**.
2. Toutes les 10 min environ, déplacer la carte sur la tour attaquée (479, 437) et sur la forteresse (419, 692).
3. Laisser arriver l'attaque sur la forteresse (10 min de trajet) pour avoir son rapport.

## Test d'envoi : lecture OK (compte de test)

- Connexion au socket du jeu OK (iframe / worker gérés), carte lue (572 objets).
- `test_envoi.py --apprendre` enregistre l'attaque faite à la main (Remplir les vagues) dans `captures/modele_cra.json`;
  `--modele` la rejoue sur la tour la plus proche (niveau min par défaut : 0).

## Connexion automatique

- `outils/connexion_auto.py` (utilisé par `src/session.py`) : ouvre le Chrome du bot, remplit le formulaire, valide ; le jeton reCAPTCHA invisible (`RCT`) est produit par la page.
- Testé sur un faux formulaire (iframe, bandeau de cookies). **À valider sur le vrai site** ; sinon `--diagnostic` écrit `captures/diagnostic_connexion.json/.png`.
- Mot de passe : variable `GGE_MDP` ou saisie masquée, jamais écrit.

## Connexion automatique : VALIDÉE sur le vrai jeu (31/10)

- Formulaire dessiné dans le jeu (canvas) ; champs cachés `txt_input_1..4` (1/2 = inscription, 3/4 = connexion).
- Page imposée à 1920x880 (Emulation) : mise en page identique partout. Positions : pseudo ~(960,351), mot de passe ~(960,445), lien « Connexion ! » ~(960,735).
- Piège : les liens « Conditions Générales » / « Politique de confidentialité » ouvrent une fenêtre qui bloque les clics : ne jamais balayer toute la page.
- Il faut attendre la fin du chargement (≈ 1 min) avant de cliquer. Preuve de connexion : réponse `lli` code 0 ou envoi de `nch`/`gdi`/`gbl` (le `pin` part AVANT le login).
- Calibrage enregistré dans `captures/calibrage_connexion.json` (`--recalibrer` pour le refaire).

## Module tours : acquis (01/11) et à valider

Acquis, validé sur le vrai jeu : cheval pièces (`HBW 1007, PTT 0`) / plumes (`HBW -1, PTT 1`), échelles, plusieurs commandants,
retour réel des commandants (message `cat`, `M.TT`), 4 s minimum entre deux attaques (le serveur ignore les suivantes).

Nouveau, à valider sur le vrai jeu : liste des châteaux du compte par `jca`/`jaa.AP` (plus de coordonnées à saisir), attaques sur
plusieurs royaumes à la fois (tour la plus proche d'abord). Voir `docs/module-tours.md`, rubrique « À valider ».

Présélections : décodées (01/11, `gas` + code du jeu), mode P codé, à valider sur le vrai jeu.

Réglages séparés par royaume (mode, soldat, côtés ou présélections, échelles, cheval, rayon ; commandants communs) : codés et testés
sur faux serveur, à valider avec deux royaumes réels.

Encore à valider : pile de plusieurs dizaines de soldats dans un seul emplacement, échelles sur le front.

## Module forteresses : à valider

Codé et testé sur faux serveur (voir `docs/module-forteresses.md`, rubrique « À valider ») : minuteur de la carte = délai personnel du
compte, durée réelle de la première lecture de la carte, première attaque de forteresse par le bot, soldat le plus rapide seul.
