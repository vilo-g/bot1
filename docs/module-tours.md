# Module tours (`src/tours.py`)

Lancement : `outils\lancer_bot_tours.bat` (options : `--simulation`, `--max-attaques N`, `--duree 3h`, `--reconfigurer`,
`--apprendre-cheval C|F`, `--avec-bonus`, `--oui`).

## Configuration (assistant du premier lancement)

Enregistrée dans `config.json`, rubrique `"tours"` (jamais de mot de passe). Au lancement suivant : Entrée = on reprend,
`N` = on refait.

L'assistant demande d'abord les réglages **communs**, puis les réglages **de chaque royaume choisi** (un bloc de questions
par royaume, dans l'ordre des royaumes) :

| # | Question | Réponse | Stockage |
|---|----------|---------|----------|
| A | Commandants (communs à tous les royaumes) | `1;3;4;6` (position dans la liste du jeu, le 1er = 1) | `commandants` |
| B | Royaumes | `3` ou `1;3` ou `0;2;3` (0 vert, 1 sables, 2 glacier, 3 feu) | `royaumes` |

Puis, **pour chaque royaume** (stockés dans `reglages["<royaume>"]`) :

| # | Question | Réponse | Clé du royaume |
|---|----------|---------|----------------|
| 1 | Mode | `P` présélections / `R` remplissage automatique | `mode` = `presets` / `auto` |
| 2a | (P) présélection des vagues 1 à 4 | numéros du jeu (1 = la première) ; la 4e sert pour les vagues 5, 6… | `presets` |
| 2b | (R) côtés | `F` flanc gauche, `FF` deux flancs, `A` front, `AF` front + gauche, `AFF` front + deux flancs | `cotes` |
| 3 | Échelles en bois | O/N : 10 sur la vague 1 de chaque côté rempli automatiquement (mode P : seulement pour les vagues de secours) | `echelles` |
| 4 | Soldats | 1 à 4 unités dans l'ordre (`216;215`, Entrée = 216) parmi : 216 Valkyrie pisteuse niv. 10, 215 Vierge à bouclier niv. 10, 6 Frondeur vétéran, 35 Lanceur de kunai renégat, 10 Horreur mortelle vétéran, 9 Horreur démoniaque vétéran | `soldats` (et `soldat` = la 1re) |
| 5 | Cheval | `C` pièces, `F` plumes, `CF` pièces puis plumes à partir d'une distance (en cases, château → tour) | `cheval`, `distance_plumes` |
| 6 | Rayon de recherche | nombre de cases autour du château de CE royaume (Entrée = valeur enregistrée pour ce royaume) | `rayon` |

À partir du 2e royaume, une question O/N (stricte) propose de **reprendre les mêmes réglages que le premier royaume** :
`O` copie le bloc (copie indépendante, modifiable ensuite à la main dans `config.json`), `N` pose toutes les questions du bloc.
Exemple : vert en remplissage automatique avec plumes seulement, feu en présélections avec pièces.

Forme dans `config.json` :

```json
"tours": {
  "commandants": [1, 3], "royaumes": [0, 3],
  "reglages": {
    "0": {"mode": "auto", "cotes": "F", "echelles": false, "soldat": 216, "cheval": "F", "rayon": 13},
    "3": {"mode": "presets", "presets": [4, 5, 1, 2], "echelles": true, "soldat": 216, "cheval": "C", "rayon": 20}
  },
  "chateaux": {}, "niveau_min": 0, "vagues_base": 4, "avec_bonus": false
}
```

Ce qui reste commun à tous les royaumes : les commandants (une seule liste, le premier commandant libre part sur la tour
libre la plus proche de n'importe quel royaume), `niveau_min`, `vagues_base`, `avec_bonus`, les choix de château. Ce qui est
propre à chaque royaume : mode, présélections ou côtés, échelles, soldat, cheval, rayon. Les présélections du compte (`gas`)
ne sont lues que si au moins un royaume est en mode P ; les réglages du cheval (pièces C : `HBW 1007, PTT 0` ; plumes F : `HBW -1, PTT 1`) sont les mêmes pour tous les joueurs et sont
connus d'avance : plus besoin de lancer une attaque à la main la première fois (`--apprendre-cheval C|F` les réapprend si le jeu
les changeait un jour ; ils sont alors gardés dans `captures/chevaux.json`, qui passe avant).

Plus aucune coordonnée à saisir : voir « Châteaux de départ » ci-dessous. Une ancienne configuration à plat (réglages au
niveau de `tours`, sans `reglages`) est jugée incomplète : l'assistant se relance et reprend ses royaumes et son rayon comme
valeurs par défaut.

Réglages non demandés (à modifier à la main dans `config.json`) : `niveau_min` (0), `vagues_base` (4), `avec_bonus` (false).

## Châteaux de départ (lus sur le compte)

À chaque lancement, après la connexion, le bot envoie `jca {"CID":-1,"KID":0}` (la requête que le jeu envoie lui-même à la
connexion : « château courant ») et lit la réponse `jaa` : `gca.O.AP` = `[[royaume, id, x, y, type], ...]`, tous les
châteaux et avant-postes du compte (vérifié sur le journal : 7 entrées sur le compte de l'ancien test, dont 2 au royaume 0 et
2 à Berimond, royaume 4, écarté). La réponse n'est acceptée que si elle contient `gui` (les données du compte) : la même
commande pour le château d'un autre joueur renvoie la liste de CE joueur.
- un seul château dans le royaume : pris automatiquement ;
- plusieurs : question numérotée (stricte), choix mémorisé par identifiant dans `config.json` (`chateaux`), redemandé seulement
  si ce château n'existe plus ;
- royaume choisi sans château : arrêt avec un message clair ;
- liste illisible (le serveur ne répond pas) : secours = coordonnées demandées une fois par royaume (`chateaux_manuels`).
Le bot affiche `CHATEAUX DE DEPART` avec ce qu'il a retenu avant de commencer.

## Plusieurs royaumes

Le bot lit les tours autour du château de chaque royaume choisi (dans le rayon de CE royaume, par couronnes, voir plus bas), fusionne toutes les tours
libres et attaque TOUJOURS la plus proche de son château, tous royaumes confondus (exemple : feu 14 m, vert 15 m, feu 28 m → feu 14, vert 15, feu 28).
Les commandants sont partagés : le premier libre part sur la tour la plus proche, où qu'elle soit. Chaque attaque utilise le
château, le royaume (`KID`), le stock d'armée et les **réglages du royaume de la tour** (mode, soldat, côtés ou présélections,
échelles, cheval ; `adi` et `cra` avec `SX, SY, KID` de ce royaume).
Une tour est repérée par `(royaume, x, y)` (deux royaumes peuvent avoir les mêmes x, y) ; le retour d'un commandant (`cat`)
est rattaché par `M.KID`. Un royaume dont le château n'a plus de soldat utilisable (selon SES réglages) est mis de côté 5 min (plus lu) ; le module
s'arrête quand plus aucun royaume n'en a.

Un commandant aussi présent dans la liste du module forteresses peut être **gardé** par la réservation de ce module
(voir docs/module-forteresses.md) : le module tours ne le prend pas tant qu'il est gardé. Ne joue que lorsque les
deux modules tournent dans la même session (lanceur commun `outils\lancer_bot.bat`, voir docs/lanceur.md).

## Numéro de commandant

Ordre d'affichage du jeu = champ `VIS` de `gli.C` de la réponse `adi` (puis `ID`). Pour ce compte `VIS` suit l'ordre des
`ID`. Le paquet `cra` utilise `LID` = `ID` du commandant.

## Vagues (remplissage automatique)

- Nombre de vagues = `vagues_base` (4) + vagues supplémentaires du commandant (effet type 156 : équipement 29, relique 22013 ;
  observé : commandant n°18 = +1 vague, `[20018, 100, [1.0]]` dans une pièce).
- Effets réservés aux combats entre joueurs (`isPvPFight`) ignorés.
- **Royaume vert : niveau d'une tour.** La carte donne le **nombre de victoires** de la tour, pas son niveau. Niveau =
  `floor(1,9 x victoires^0,555) + 1` (conforme aux tranches du jeu, `items.json` : dungeons + lords ; ex. 11 victoires = niveau 8,
  encore 3 pour le 9). Console : `niv 8 (11 victoire(s), encore 3 pour le niveau 9)`. Avant, le bot prenait 11 pour le
  niveau et mettait 13 soldats par flanc au lieu de 10 : attaque ignorée par le serveur.
- Soldats par côté = plafond de la tour **sans bonus** (voir `combat.py`) : flanc `ceil(0.2*M)`, front `M - 2*ceil(0.2*M)`,
  `M = 320 si niveau > 69 sinon min(260, 5*niveau + 8)`. Niveau 0 traité comme 1. Le serveur ne plafonne pas : le calcul est côté bot.
- Échelles (id 614) : 10 sur la vague 1 de chaque côté choisi, limitées par le stock.
- Unités choisies dans l'ordre : chaque côté reçoit le plafond de la tour avec la 1re unité, complétée par la suivante s'il
  en manque (au plus 2 unités différentes par flanc, 6 au front). Une seule unité : comme avant. Stock insuffisant : on envoie
  ce qui reste et on le signale ; plus aucune des unités choisies : le royaume est mis de côté. Numéros des unités : trouvés
  dans `items.json` et dans le stock du château (`adi`, `gui.I`) ; images dans `src/images/unites/<numéro>.png` (interface).
  Correspondances nom du jeu → numéro à confirmer en vrai pour 35 (kunai), 10 et 9 (horreurs : unités « EliteDemons »).

## Cheval

CONFIRME sur le vrai jeu (apprentissage + attaque du bot, 01/11) : **pieces = `HBW 1007, PTT 0`** (cheval « Horse » de l'ecurie 3,
paye en pieces) ; **plumes = `HBW -1, PTT 1`** (le serveur repond `HBW 1009`). Le module apprend ces deux champs a partir
d'une attaque lancee a la main avec l'option voulue (`captures/chevaux.json`) et n'envoie rien tant qu'ils sont inconnus.
Trajets observes pour des tours proches : 147 s en plumes, 212 s en pieces (cheval 1007).

## Boucle

Lecture de la carte par couronnes (`gaa`, voir plus bas) → tours libres (`type 2`, sans propriétaire, minuteur ≤ 0) triées par distance → un commandant libre
par tour (ordre de la config) → `adi` (niveau, stock, équipement) → paquet `cra` → réponse `AAM` (durée du trajet `TT`).
Retour du commandant : à l'arrivée sur la tour le serveur envoie `cat` (`D = 1`) avec la durée RÉELLE du retour `M.TT`
(souvent bien plus courte que l'aller : 63 s au retour pour 221 s à l'aller observé) ; le commandant est libre à
`arrivée prévue + TT du retour`. Avant ce message, borne haute de sécurité `2*TT + 15 s`. Tour laissée de côté `TT + 90 s`
puis relue sur la carte (relecture de sa zone juste avant l'envoi : si le jeu lui donne un minuteur, elle est ignorée).
Première attaque : plus de confirmation par défaut (`--confirmer` pour demander `OUI`), toutes partent seules. Le serveur ignore sans réponse
une attaque partie moins de 4 s après la précédente : le bot attend juste ce qu'il manque pour avoir 4,2 s entre deux envois
(aucune pause en plus si le script est déjà plus lent). Arrêt après 3 refus de suite du serveur, session coupée, ou stock
d'unités vide dans tous les royaumes choisis.
Journal : `captures/journal_tours.txt`.

## Présélections (mode P)

Les présélections sont enregistrées **côté serveur**. Le bot les lit au lancement avec `gas {}` (la requête du jeu à l'ouverture
de l'écran d'attaque) : réponse `{"S":[{"S":index,"A":"<json>","SN":"nom"},...]}`, seules les présélections débloquées sont
listées. Le numéro du joueur = `S + 1` (« présélection 4 » = `S` 3). Code lu dans le jeu (`FightPresetVO`, `FightPresetData`,
`AttackDialogPresets.applyPresetToWave`, `CastleAttackWaveVO`).

`A` = 6 groupes `[id, quantité, id, quantité, ...]`, dans l'ordre des « flanks » d'une vague du jeu :
`outils front, outils gauche, outils droite, soldats front, soldats gauche, soldats droite` (vérifié : `[216, 1060]` au front,
`[216, 361]` à chaque flanc, 3 / 2 / 2 outils ; 6 / 2 / 2 emplacements de soldats). Une 7e liste éventuelle = outils de soutien
(ignorée, `AST` reste `[-1,-1,-1]`).

Application à une tour (copie de `applyPresetToWave`), pour chaque vague avec sa présélection (vague 5 et suivantes = celle de
la vague 4) :
- chaque groupe est parcouru dans l'ordre ; chaque quantité est limitée par la **place restante du groupe** et par le **stock
  du château de départ** ; une entrée qui donne 0 est sautée sans prendre d'emplacement (le suivant prend sa place) ;
- places d'une vague (sans bonus de commandant, comme le jeu) : soldats front `M - 2*ceil(0.2*M)`, flanc `ceil(0.2*M)`
  (`M = 320 si niveau > 69 sinon min(260, 5*niveau + 8)`) ; outils flanc `niveau < 37 : 10, < 50 : 20, < 69 : 30, sinon 40` ;
  outils front `niveau < 11 : 10, < 37 : 20, < 50 : 30, < 69 : 40, sinon 50` (`CombatConst.getTotalAmountTools`) ;
- un outil n'est utilisé que s'il peut attaquer cette cible (`AttackHelper.canUseToolForAttackOnTarget`) : `canBeUsedToAttackNPC`
  différent de 0 et, si la fiche a un `allowedToAttack` (`royaume+zone#royaume+zone`), la paire (royaume, 2 = tour) y figure
  (ex. l'outil 25 `0+29` ou 241 sont refusés sur une tour). Fiches lues dans `captures/items.json` ;
- **soldats manquants (complément)** : sur chaque côté où la présélection met des soldats, ce qui manque (unité absente ou
  stock insuffisant, même en partie, dans la limite de ce que demande la présélection et du plafond de la tour) est complété :
  d'abord avec l'**unité de remplacement** choisie (une seule en mode P), puis avec les unités offensives **à distance** du
  château (la plus forte attaque d'abord), puis celles de **corps à corps** (la plus forte d'abord), seulement celles qui ont le
  droit d'aller dans ce royaume ; au plus 2 unités par flanc et 6 au front (une unité déjà présente est augmentée). Les outils
  de la présélection sont gardés. 10 échelles sur un côté qui n'avait plus aucun soldat, vague 1 seulement, si l'option
  échelles est à O et que ce côté n'a pas d'outil. Console : `vague 1 : preselection 2 completee avec 64 x 216 (flanc gauche)`.
  Une vague sans aucun soldat au final part vide (sans outils).
- le stock est consommé vague après vague ; les vagues finales sans aucun soldat (même en secours) sont retirées ; plus aucun
  soldat → royaume mis de côté (comme en mode R).
Les numéros choisis sont vérifiés avant le premier envoi (message avec la liste des présélections du compte). Le bot n'écrit
jamais de présélection (pas de `sas`).

## À valider sur le vrai jeu

- Réglages séparés par royaume (testés sur faux serveur : côtés, échelles, cheval, rayon, mode P / R différents dans le même
  module) : à confirmer sur deux royaumes réels.
- Mode P : vagues de secours (présélection sans soldat disponible → remplissage automatique, mêmes côtés) : testé sur faux
  serveur seulement.
- Mode P : paquet `cra` construit à partir d'une présélection (testé sur la vraie réponse `gas` du journal et un faux serveur,
  pas encore sur le vrai jeu) : outils multiples par groupe, place des outils / soldats par niveau de tour, vagues vides.
- Une pile de plusieurs dizaines de soldats dans un seul emplacement du front / d'un flanc (validé seulement avec de petits
  nombres : tour niveau 1, 3 soldats, flanc gauche, échelles, cheval pièces : OK le 01/11).
- Échelles sur le front (validées sur flanc gauche seulement).
- Nombre de vagues des commandants avec vagues supplémentaires.
- Lecture des châteaux par `jca`/`jaa` sur le compte de test (le format vient du journal de l'ancien compte). Si le serveur
  ne répond pas, le bot demande les coordonnées (secours) : envoie-moi alors la sortie.
- Commandants partagés entre royaumes : un commandant de la liste peut-il partir d'un avant-poste d'un autre royaume ? (le
  journal de l'ancien compte le suggère, pas encore vu avec le bot). Le stock de soldats est celui de chaque château.
- Réglages du cheval pièces/plumes dans les autres royaumes (observés identiques au feu : `HBW 1007` / `1009`).
- Détection des commandants occupés : suivi local + refus du serveur (pas de champ « occupé » trouvé dans `adi`).

## Faire tourner le module pendant un temps donne (`--duree`)

`outils\lancer_bot_tours.bat --duree 3h` : le module tourne 3 h, puis s'arrete et se deconnecte du compte tout seul (les
armees deja parties reviennent seules). Formats : `90m`, `45min`, `3h`, `2h30` (de 1 minute a 72 h). Sans `--duree` : jusqu'a Ctrl+C.
Le message de demarrage donne l'heure d'arret prevue. L'option existe aussi pour le module forteresses.
Pour un long fonctionnement : PC allume et non mis en veille (sinon le bot s'arrete en silence), ne pas se connecter au compte en meme temps
(la session du bot serait coupee, le bot s'arrete avec un message), suivre `captures\journal_tours.txt`.

## Lecture de la carte par couronnes (rapide)

Le bot ne lit plus toute la carte à chaque passage. Il lit d'abord les zones de 13 x 13 cases autour du château de chaque
royaume (couronne 0), attaque les tours libres trouvées, et n'**élargit d'une couronne de 13 cases** (13 à 26 cases, puis 26 à 39...)
que lorsqu'il y a **moins de tours libres que de commandants prêts**, jusqu'au rayon réglé (x 1,5 comme avant pour les coins).
- Une zone déjà lue est gardée en mémoire (les tours ne bougent pas) avec l'heure de lecture et le minuteur de chaque tour :
  une tour redevient candidate quand son minuteur est écoulé. Avant d'envoyer un commandant, la zone de la tour est relue
  (1 demande) : si le jeu lui donne maintenant un minuteur, le bot passe à la suivante.
- Un commandant qui revient part tout de suite sur la tour libre la plus proche ; si les tours proches sont en attente de 3 h,
  la carte est élargie couronne par couronne jusqu'à une tour libre (quelques secondes), sans relire les zones déjà connues.
- Avec plusieurs royaumes, la couronne la plus proche d'un château est lue en premier, quel que soit le royaume.
- Une zone sans réponse du jeu est retentée une fois tout de suite.

- **Carte sans réponse** : si le jeu ne répond pas (ou répond par une erreur) quand le bot relit la zone d'une tour juste
  avant l'attaque, la tour est sautée 1 min avec la raison dans le journal ; après 3 échecs de suite, message « le jeu ne
  répond plus aux demandes de carte » et pause de 30 s (avant : le bot pouvait tourner plusieurs minutes sans rien écrire).
  Une réponse d'erreur n'efface plus les tours connues de la zone.
- **Tour accélérée dans le jeu** (délai de récupération raccourci avec un objet) : quand un commandant attend, le bot relit
  les zones déjà lues dont la lecture date de plus d'1 minute, au plus 3 par passage, les plus proches du château d'abord ; il
  attend au plus 60 s entre deux passages. Une tour proche accélérée est donc vue et attaquée en 1 à 2 minutes. Aucune
  relecture quand tous les commandants sont partis.

## Distance maximale (multiples de 10)

La question « DISTANCE MAXIMALE » de chaque royaume (clé `rayon` dans `config.json`) n'accepte qu'un **multiple de 10 entre 10 et 500**
(20, 50, 100...). C'est la distance en ligne droite entre le château de départ et la tour : une tour plus loin n'est **jamais**
attaquée (avant, le bot acceptait jusqu'à 1,5 fois le rayon, pour couvrir les coins du carré lu ; ce n'est plus le cas).
Le choix de la distance est indépendant de la taille des zones lues : le bot interroge toujours le jeu par zones de 13 x 13 cases
(la taille que le jeu utilise lui-même), les range par couronnes de 13 cases autour du château, ne lit que celles qui touchent
le cercle de la distance choisie, puis garde les tours par distance exacte. Exemple : distance 20 -> environ 16 zones au plus.
Une ancienne valeur qui n'est pas un multiple de 10 (ex. 13) n'est pas reprise par Entrée : l'assistant redemande la distance.
