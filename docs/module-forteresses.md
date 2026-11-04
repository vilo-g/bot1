# Module forteresses (`src/forteresses.py`)

Lancement : `outils\lancer_bot_forteresses.bat` (options : `--simulation`, `--max-attaques N`, `--duree 3h`, `--reconfigurer`,
`--apprendre-cheval C|F`, `--avec-bonus`, `--oui`). Même boucle que le module tours (même fichier `tours.py` pour les
commandants, le cheval, les présélections, les retours `cat`), avec d'autres cibles, d'autres commandants et d'autres réglages.
**Aucune API externe** : les forteresses sont lues directement sur la carte du jeu, avec ton compte.

## Configuration (assistant du premier lancement)

Enregistrée dans `config.json`, rubrique `"forteresses"` (la rubrique `"tours"` n'est jamais touchée ; pas de mot de passe).
Au lancement suivant : Entrée = on reprend, `N` = on refait. Ordre des questions :

| # | Question | Réponse | Stockage |
|---|----------|---------|----------|
| A | Commandants, **propres à ce module** | `1;3;4;6` (position dans la liste du jeu) | `commandants` |
| A2 | (seulement si un commandant est aussi dans la liste du module tours) réservation : O/N, secondes (1 à 3600), maximum (1 à nombre de communs ; pas demandé s'il n'y a qu'un commun) | `O`, `900`, `1` | `reserve` = `{"secondes": 900, "max": 1}` (`secondes` = 0 : désactivée) |
| B | Royaumes | `1` sables, `2` glacier, `3` feu (le vert n'a pas de forteresse) : `3` ou `1;3` ou `1;2;3` | `royaumes` |

Puis, **pour chaque royaume** (`reglages["<royaume>"]`), avec le raccourci O/N « mêmes réglages que le premier royaume » à
partir du 2e :

| # | Question | Réponse | Clé |
|---|----------|---------|-----|
| 1 | Mode | `P` présélections / `R` remplissage automatique | `mode` = `presets` / `auto` |
| 2a | (P) présélection des vagues 1 à 4 | numéros du jeu ; la 4e sert pour les vagues suivantes | `presets` |
| 2b | (R) côtés | `F`, `FF`, `A`, `AF`, `AFF` (comme les tours) | `cotes` |
| 3 | Cheval | `C` pièces, `F` plumes, `CF` pièces puis plumes à partir d'une distance | `cheval`, `distance_plumes` |
| 4 | Distance maximale | cases, du château de départ à la forteresse (les plus éloignées sont ignorées) | `distance_max` |
| 5 | Nombre de vagues à remplir | `1`, `2`, `3` (les suivantes partent vides) ou `4` (toutes, avec les vagues en plus du commandant) ; absent = toutes | `vagues_remplies` |

Pas de question sur les échelles (jamais utilisées : `echelles` = `false`), ni sur le soldat (`soldat` = `"rapide"`), ni sur une API.

```json
"forteresses": {
  "commandants": [5, 6], "royaumes": [1, 3],
  "reglages": {
    "1": {"mode": "auto", "cotes": "F", "echelles": false, "soldat": "rapide", "cheval": "F", "distance_max": 150},
    "3": {"mode": "presets", "presets": [2, 2, 2, 2], "echelles": false, "soldat": "rapide", "cheval": "CF", "distance_plumes": 100, "distance_max": 200}
  },
  "chateaux": {}, "vagues_base": 4, "avec_bonus": false,
  "reserve": {"secondes": 900, "max": 1}
}
```
(Une rubrique sans `"reserve"` reste valable : pas de réservation.)
(Une ancienne rubrique qui contiendrait encore une clé `"api"` reste valable : la clé est ignorée.)

## Où viennent les forteresses : la carte du jeu (`gaa`)

La demande `gaa {"KID","AX1","AY1","AX2","AY2"}` (zone de 13 x 13 cases, celle que le jeu et le module tours utilisent déjà)
renvoie les objets de la zone. Une forteresse y est `[11, x, y, -1, niveau, minuteur, PID, royaume]` (vérifié dans le journal du
jeu : 19 forteresses vues, minuteurs qui diminuent d'une lecture à l'autre).
**Le minuteur est le nombre de secondes avant que la forteresse soit attaquable pour ton compte : 0 = attaquable maintenant**
(exemples du journal : 0, 7 052, 11 678, 86 017, 331 122 s).

- **Première lecture** : seulement les zones qui touchent le **cercle** de rayon = distance maximale (les coins du carré
  ne peuvent contenir aucune forteresse à portée : environ 20 % de zones en moins, ex. 459 au lieu de 576 pour 150 cases),
  **la plus proche du château d'abord**. Elle se fait **par petits morceaux de 4 zones** (environ 3 s) entre deux attaques :
  le démarrage ne l'attend plus, une forteresse trouvée est attaquée tout de suite, la lecture continue même quand tous les
  commandants sont partis, et les tours partent entre deux morceaux. Plusieurs royaumes avancent ensemble (zone la plus
  proche de son château d'abord). Durée totale : environ 0,75 s par zone (≈ 6 min pour 150 cases), mais sans attente au
  départ ; le bot affiche l'estimation, la progression (25 %, 50 %, 75 %) et « lecture de la carte terminee ». Les
  **positions** des forteresses ne changent jamais : elles sont mémorisées dans `captures/carte_forteresses.json`
  (positions et niveau seulement ; pas de noms de joueurs), avec le château de départ et la distance lue.
- **Ensuite** : seules les zones qui contiennent une forteresse connue sont relues, **par petits morceaux de 3 zones**
  (environ 2 s, pour ne jamais bloquer les tours : un commandant qui revient repart tout de suite), au plus toutes les 5 minutes et seulement
  quand un commandant est libre (de l'ordre de 1 à 20 demandes au lieu de plusieurs centaines).
- Au lancement suivant, le fichier est repris tel quel (quelques secondes). Il est relu en entier si : la distance maximale
  est plus grande que celle déjà lue, le château de départ a changé, le fichier est abîmé, ou plus rien n'est trouvé aux
  positions mémorisées. Une distance plus petite réutilise le fichier.
- **Zone sans réponse du jeu** (délai dépassé ou code d'erreur) : retentée 2 fois (à la fin de la première lecture) ; si elle échoue encore, le bot
  affiche la raison (délai, ou code d'erreur du jeu), mémorise quand même la carte et ne retente que cette zone aux
  3 relectures suivantes (toutes les 5 min), puis l'ignore ("ne repond jamais"). Une forteresse située dans une zone qui ne
  répond plus garde sa dernière valeur connue. Seul cas où toute la carte est relue : plus de 5 % des zones sans réponse
  (connexion en difficulté) ; le message le dit.
- Seules les forteresses sans propriétaire (`-1`) et dont la distance au château (en ligne droite) est au plus la distance
  maximale **du royaume** sont retenues. La plus proche d'abord, tous royaumes confondus.

## Avant l'envoi : `abi`

Pour chaque candidate, le bot envoie `abi {"SX","SY","TX","TY","KID"}` (la demande du jeu pour une forteresse) : la réponse
donne le stock du château, les commandants, et l'entrée de la cible `gaa.AI = [11, x, y, -1, niveau, T, PID, KID]`.
- `T > 0` : le jeu dit qu'elle n'est pas encore attaquable (carte en retard) → laissée de côté `T + 30 s`, on passe à la
  suivante (jusqu'à 8 essais par commandant et par passage) ;
- type différent de 11 : laissée de côté 30 min ;
- le niveau (`niveau`, 55 dans l'exemple du journal) sert aux plafonds de soldats, comme pour les tours.
Le paquet `cra` est le même que pour une tour. Comparé au vrai paquet du journal (forteresse (419,692), commandant 12) :
tous les champs sont identiques, seule la quantité diffère (52 par flanc au niveau 55 sans bonus, 80 envoyés par le jeu avec le
bonus du commandant).

## Soldats

- **Remplissage automatique (R)** : chaque côté choisi reçoit le **maximum** permis par la forteresse (plafond sans bonus :
  47 par flanc au niveau 45, 52 au niveau 55). D'abord l'unité offensive la plus RAPIDE du château (champ `speed` de
  `items.json`, unités autorisées dans le royaume), puis, s'il n'y en a pas assez, la suivante la plus rapide, etc. (au plus
  2 unités différentes par flanc, 6 au front : cases du jeu). L'armée va alors à la vitesse de l'unité la plus lente.
  Sur le compte de test : 277 (vitesse 100), puis 34/35 (50), puis 216/215 (34). Seulement le nombre de vagues choisi
  (question 5), les autres partent vides. Jamais d'échelles. Console : `soldat 277+216`, `2 vague(s) remplie(s) sur 4`.
- **Présélections (P)** : comme pour les tours. Une vague dont la présélection n'a aucun soldat disponible est remplacée par le
  soldat le plus rapide sur les mêmes côtés que la présélection (sans échelles ni outils).
- Plus aucun soldat offensif dans un château : le royaume est mis de côté 5 min ; arrêt si c'est le cas de tous.

## Infos d'attaque (`abi`) sans réponse

Le bot redemande une 2e fois (10 s). Sans réponse aux deux essais, la forteresse est sautée 1 min et le journal note les
messages reçus pendant l'attente (pour comprendre pourquoi le jeu n'a pas répondu).

## Réattaque

Après un envoi, la forteresse est laissée de côté jusqu'à `envoi + trajet aller + 3 h + 30 s` (règle de l'ancien bot du joueur),
puis reprise si la carte lui donne un minuteur à 0 et si `abi` dit `T <= 0`. Le retour des commandants est suivi comme pour
les tours (message `cat` : libre à `arrivée + retour réel`).
Quand rien n'est attaquable, le message donne le temps avant la prochaine forteresse (le plus petit minuteur lu) et le module
se réveille à la seconde où la prochaine forteresse devient attaquable (au plus 60 s d'attente d'un coup), et relit les
minuteurs toutes les 5 minutes, par morceaux de 3 zones.

## Commandants, un seul compte

Les commandants des forteresses sont une liste à part de celle des tours. Un même commandant peut être dans les deux listes
(« commandant commun ») : l'assistant propose alors la **réservation** (question A2).

**Un seul compte, une seule connexion** : pour faire tourner tours et forteresses ensemble, utiliser le lanceur commun
`outils\lancer_bot.bat` (voir `docs/lanceur.md`). Lancer deux `.bat` de modules en même temps couperait la session du premier.

### Réservation des commandants communs

But : que le module tours n'envoie pas un commandant commun juste avant qu'une forteresse devienne attaquable.

- Une forteresse est « bientôt attaquable » si son minuteur est à moins de `secondes` (limite incluse), ou déjà à 0, et
  qu'elle n'est pas déjà attaquée (royaumes sans soldat ignorés).
- Le module forteresses garde **un commandant commun par forteresse bientôt attaquable, au plus `max`**, parmi les communs
  qui sont au château. Un commun en déplacement est gardé **dès son retour** s'il y a encore une forteresse proche.
- Le module tours ne prend pas les commandants gardés (vérifié au début de son passage, puis juste avant chaque envoi, la
  lecture de la carte pouvant prendre du temps). Ses autres commandants ne sont pas touchés.
- Le module forteresses part d'abord avec ses commandants **propres**, puis avec les communs s'il le faut.
- Dès que la forteresse est attaquée (ou n'est plus proche), la réserve est levée. La console affiche
  `reservation : commandant(s) 5 gardes ...` et `reservation levee ...`, seulement quand ça change.

La réservation ne sert que si les modules tournent dans la **même session** : lanceur commun (`outils\lancer_bot.bat`).
Lancé seul (`lancer_bot_forteresses.bat`), le module affiche `RESERVATION DES COMMANDANTS : ... INACTIVE dans ce lancement`.
Dans le lanceur commun, le module forteresses est **prioritaire** (voir `docs/lanceur.md`) : dès qu'une forteresse est
attaquable et qu'un de ses commandants (gardé ou non) est au château, il passe avant les tours. Il publie aussi ses commandants gardés dans l'objet commun (`Partage.reservations`) ;
les autres modules les évitent sans connaître le module forteresses.

## À valider sur le vrai jeu

- Réservation des commandants communs et lanceur commun : testés seulement sur un faux jeu.
- Lecture de la carte pour les forteresses : le format `[11, x, y, -1, niveau, minuteur, PID, royaume]` et la diminution du
  minuteur sont vus dans le journal ; reste à vérifier que ce minuteur est bien celui **de ton compte** (délai de 3 h après
  ton attaque) : `abi` le recontrôle avant chaque envoi.
- Durée réelle de la première lecture de la carte (estimation : environ 0,65 s par zone).
- Première attaque de forteresse par le bot (même format que les tours ; test sur la vraie `abi` du journal seulement).
- Soldat le plus rapide (277) seul sur le flanc gauche : suffisant pour gagner ? (le jeu en a envoyé 80 par flanc avec le bonus).
- Vagues supplémentaires des commandants sur une forteresse (zone 11, mêmes effets que pour les tours sur ce compte).
- Outils d'une présélection sur une forteresse : zone 11 supposée (aucun outil dans le seul exemple vu).
- Délai de 3 h : compté depuis l'arrivée, comme l'ancien bot ; le minuteur de la carte / `abi.T` le confirmera.
