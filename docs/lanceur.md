# Lanceur commun (`src/bot.py`)

Une seule connexion au compte, et les modules choisis tournent **en même temps**.

Lancement : `outils\lancer_bot.bat`

| Commande | Effet |
|----------|-------|
| `outils\lancer_bot.bat` | demande les modules à lancer (Entrée = le dernier choix) |
| `outils\lancer_bot.bat --modules "1"` | seulement les tours |
| `outils\lancer_bot.bat --modules "2"` | seulement les forteresses |
| `outils\lancer_bot.bat --modules "1;2"` | les deux (on peut aussi écrire `"tours;forteresses"`) |

Mets les guillemets autour de la liste dans la fenêtre de commande. Autres options : `--duree 3h`, `--simulation`,
`--max-attaques N` (au total, tous modules confondus), `--reconfigurer` (refait la configuration des modules choisis),
`--oui`, `--avec-bonus`, `--pseudo`. Arrêt : **Ctrl+C** (déconnexion propre). Sans `--duree`, le bot tourne jusqu'à Ctrl+C.

Les lanceurs d'un seul module (`lancer_bot_tours.bat`, `lancer_bot_forteresses.bat`) marchent toujours. Ne jamais lancer
deux `.bat` en même temps : le deuxième couperait la session du premier.

## Déroulement

1. **Avant la connexion** : choix des modules (gardé dans `config.json`, rubrique `"lanceur"`), puis, pour chaque module
   choisi, sa configuration habituelle (rubrique `"tours"`, `"forteresses"` : Entrée = on reprend) et ses fichiers.
2. **Connexion** au compte (une seule fois).
3. **Démarrage de chaque module** : châteaux, présélections, cheval ; pour les forteresses, lecture de la carte.
   Les lignes de chaque module sont précédées de son nom : `[TOURS] ...`, `[FORT] ...`.
4. **En marche** : le lanceur fait passer le module dont l'heure est venue (chaque module dit dans combien de secondes il
   veut repasser), puis le suivant. Deux modules ne parlent jamais au jeu en même temps.
5. **Arrêt d'un module** (plus de soldat, 3 refus du serveur, commandants absents...) : les autres continuent.
   Si la connexion est coupée (le joueur s'est connecté ailleurs), tout s'arrête.

Plus aucune confirmation : dès que le bot est lancé, les modules attaquent tout seuls (`--confirmer` pour redemander **OUI** avant la première attaque de chaque module, pour une vérification).

## Priorité : les forteresses d'abord

Chaque module a une priorité (`LANCEUR_PRIORITE`, plus petit = plus prioritaire) : **forteresses 0**, tours 10. Les futurs
modules en auront une aussi. L'ordre tapé (`1;2` ou `2;1`) ne change pas la priorité.

1. **Plusieurs modules prêts en même temps** : le plus prioritaire passe d'abord.
2. **Urgence** : dès qu'une forteresse est attaquable **et** qu'un commandant du module forteresses est au château
   (y compris un commandant gardé par la réservation), le module forteresses est appelé tout de suite. Le module tours
   s'interrompt **entre deux attaques** (et n'élargit pas la carte), sans rien envoyer de plus ; s'il attendait les 4 s
   obligatoires avant une attaque de tour, il l'annule. Console : `[TOURS] pause : un module prioritaire ...`.
   Il reprend 1 s après le passage des forteresses.
3. **Réveil à la seconde** : quand aucune forteresse n'est attaquable, le module forteresses se réveille à l'heure exacte
   où la prochaine le devient (au plus 60 s d'attente d'un coup), au lieu d'au moins 15 s.

Retard restant : au plus les 4 s obligatoires entre deux attaques (si une tour vient de partir). Sécurité : si le module
forteresses est appelé en urgence mais n'attaque rien, l'urgence est ignorée 30 s (pas de boucle sans fin).

Avec la **réservation** (module forteresses) : le commandant commun gardé reste au château ; quand la forteresse devient
attaquable, l'urgence le fait partir tout de suite. Sans réservation, la priorité marche aussi, mais seulement si un
commandant du module forteresses est au château à ce moment-là.

## Les modules ne sont pas reliés entre eux

Chaque module ne connaît que l'objet commun `Partage` (`src/tours.py`) :

| Dans `Partage` | Rôle |
|----------------|------|
| `libre_a` | quand chaque commandant revient : un commandant parti avec un module est absent pour les autres |
| `dernier_envoi` | 4 s au moins entre deux attaques, tous modules confondus (sinon le serveur ignore l'attaque) |
| `reservations` | commandants gardés par un module (ex. forteresses avant qu'une forteresse soit attaquable) : les autres ne les prennent pas |
| `commandants` | liste des commandants de chaque module lancé (pour trouver les commandants communs) |
| `routeur` | chaque message de retour d'armée (`cat`) est donné à tous les modules ; seul celui qui a envoyé l'armée le reconnaît |
| `priorites`, `urgences` | priorité de chaque module ; « j'ai une attaque à faire tout de suite » (les moins prioritaires s'interrompent) |

Un commandant peut être dans la liste de plusieurs modules : il ne part qu'avec un seul à la fois. Le lanceur affiche
les commandants communs au démarrage. Pour que le module forteresses les garde avant ses forteresses, voir la
réservation dans `docs/module-forteresses.md`.

## Ajouter un module

Un fichier `src/<nom>.py` qui fournit :

- `LANCEUR_NOM` (ex. `"tours"`), `LANCEUR_ETIQUETTE` (préfixe de la console, ex. `"TOURS"`), `LANCEUR_CLE` (= `NOM_MODULE` de
  sa classe : sa clé dans `Partage`), `LANCEUR_PRIORITE` (facultatif, 50 par défaut) ; une méthode `urgence()` facultative ;
- `lanceur_config(args, lire, sortie)` → configuration (avant la connexion) ;
- `lanceur_ressources(cfg, sortie)` → fichiers nécessaires (avant la connexion) ;
- `async lanceur_demarrer(jeu, cfg, ressources, args, partage, sortie, lire)` → objet avec `cycle()` (renvoie les secondes
  à attendre), `nb_attaques`, `max_attaques`, `cfg["commandants"]`, `_retour(payload)` (le plus simple : une classe dérivée de
  `ModuleTours`, qui reçoit `partage=partage`).

Puis l'ajouter à la liste `MODULES` en haut de `src/bot.py` : il apparaît dans la question « modules à lancer ».

## À valider sur le vrai jeu

- Les deux modules ensemble sur une vraie session (testé seulement sur un faux jeu : commandant commun gardé puis utilisé,
  4 s entre attaques de deux modules, retour `cat` donné au bon module, arrêt d'un module sans arrêter l'autre).
- Première lecture de la carte des forteresses : faite par morceaux de 4 zones (environ 3 s) entre deux attaques ; les tours
  partent entre deux morceaux (testé sur un faux jeu : 7 tours en 27 s pendant la lecture).
- Priorité des forteresses (interruption des tours, réveil à la seconde) : testée sur un faux jeu (forteresse partie 4,2 s
  après être devenue attaquable au milieu d'une série de tours ; la 2e partie 0,4 s après, avec le commandant gardé).
