# Module Événements

Les événements qui s'enchaînent : **les nomades**, **les Corbeaux de sang** et **les samouraïs**. Fichier `src/evenements.py`, réglages dans la page
Événements de l'interface (rubrique `evenements` de `config.json`), case « Événements » du tableau de bord.

**Un seul sous-module à la fois** : en haut de la page Événements, on active Nomades, Corbeaux de sang ou Samouraïs (`"actif"` dans
la rubrique) ; la page montre les réglages de celui qui est activé. Si l'événement activé n'est pas en cours (pas de camp nomade ou
samouraï aux coordonnées, aucun camp des Corbeaux de sang autour du château), le bot le voit au lancement et arrête le module Événements.

## Nomades

- Un camp nomade donné par ses coordonnées (royaume vert), attaqué en boucle par les commandants choisis, par salves comme
  le module Tours lev max (départs à 4,2 s d'écart, arrivées espacées).
- Tant que le camp n'a pas de délai (en Maître+ : niveaux 101 à 109), les salves s'enchaînent. À son dernier niveau (110), le
  jeu bloque le camp après chaque victoire (1 h 30, moins avec le bonus d'alliance) : le bot lit le délai exact et le saute
  avec les passe-temps cochés (24 h, 5 h, 1 h, 30 min, 10 min, 5 min, 1 min), les plus longs d'abord. Jamais de rubis.
- Armée : remplissage automatique (soldats choisis sur les côtés choisis à toutes les vagues, engins nomades choisis à la
  vague 1, coffres de tablettes aux vagues suivantes, option : soldats dans la cour) ou présélections du jeu. Soldats à niveaux
  (Valkyrie pisteuse, Vierge à bouclier, Guerriers à arc court et à hache reliques) : le plus haut niveau en stock d'abord,
  complété par le niveau en dessous, etc.
- Places d'une vague : plafond du niveau × (1 + bonus du commandant + bonus du château lus dans le jeu). Vérifié sur la
  capture du 15/11 : 186 / 429 soldats, 212 / 505 avec un commandant à +40 %.
- S'il manque des soldats ou un engin pour la vague 1 : pas d'attaque, pause et message. Sans coffre : l'attaque part.
- Commandants : le module tours ne les prend jamais tant que ce module tourne ; le module forteresses peut en garder avant
  une forteresse, ils reviennent ensuite dans la boucle.
- Arrêt : après N attaques et / ou N tablettes gagnées (0 = pas de limite). Les tablettes sont comptées avec le stock que le
  jeu envoie quelques secondes après chaque combat.

## Samouraïs (20/11)

- Même fonctionnement et mêmes réglages que les nomades (rubrique `samourais` de `evenements`) : un camp samouraï donné par ses
  coordonnées (royaume vert, objet de type 29 sur la carte), attaqué en boucle par salves. Niveaux 101 à 109 sans délai ; au niveau
  110, délai de 1 h 30 après chaque victoire, sauté avec les passe-temps cochés (même message `msd` que les nomades, vérifié sur la
  capture du 20/11 avec chaque passe-temps). Jamais de rubis.
- Engins de la vague 1 (seulement ceux dont le joueur a envoyé l'image ; noms du fichier de langue français) : Échelle pliante (26,
  jetons +3 %), Échelle du shogun (171, +5 %), Briseur de portes (25, +3 %), Bélier du shogun (170, +5 %), Bouclier orientable (27,
  +3 %), Bouclier du shogun (172, +5 %), Mur de boucliers (651, sans bonus). Pas de bélier sur un flanc.
- Coffres de jetons (vagues suivantes, `samuraiTokenBooster` d'items.json) : Boîte de jetons de samouraïs (30, +3 %), Coffre de jetons de
  samouraïs (31, +4 %), Coffret du shogun (165, +5 %), Coffre du shogun (166, +6 %), Grand coffre (406, +7 %), Énorme coffre (407,
  +8 %), Coffre à médaillons de samouraïs géants (510, +9 %), Énorme coffre à jetons du samouraï (511, +10 %), Coffre de samouraïs
  colossaux (512, +11 %).
- Places et cour : même calcul que les nomades. Vérifié sur la capture du 20/11 (camp niveau 108) : 216 / 659 soldats et 9 vagues
  pour le commandant 1, 191 / 582 et 10 vagues pour le commandant 2, cour 2849.
- Arrêt : après N attaques et / ou N jetons gagnés (message `sce` `ST` ; `STO` n'est pas compté). Journal :
  `captures/journal_samourais.txt`, état : `captures/etat_samourais.json`.
- Les camps à médailles de l'alliance (samouraïs) seront un module à part, plus tard.

## Corbeaux de sang (18/11)

- Les camps des Corbeaux de sang (objets de type 21 ou 34 sur la carte du royaume vert) autour du château, dans le rayon choisi
  (30 cases par défaut), attaqués avec les commandants choisis : le plus proche libre d'abord, un seul commandant par camp. Un
  camp vaincu disparaît de la carte et revient quelques minutes plus tard ; la carte est relue avant chaque départ. Pas de délai,
  pas de passe-temps.
- Armée : 1 à 3 premières vagues « d'engins » (boutons 1 / 2 / 3), chacune avec ses propres engins choisis case par case (2
  par flanc, 3 au centre, 50 par côté au plus, pas de bélier sur un flanc) ; une vague ouverte pour la première fois reprend les
  engins de la vague 1. Les vagues suivantes sont remplies des bannières de gloire cochées (les plus fortes ou les plus faibles
  d'abord), 50 par côté. Soldats des vagues d'engins et des vagues de bannières au choix. Dans `config.json`, `"engins"` est la
  liste des grilles, une par vague d'engins (l'ancien format, une seule grille, vaut pour toutes les vagues d'engins).
- Engins proposés (noms du fichier de langue français du jeu, effets d'items.json, lus le 18/11 ; seulement ceux qui ont une
  image) — porte : Bélier royal (154, gloire +6 %), Bélier lourd (648), Bélier (611) ; mur : Échelle royale (156, +6 %),
  Beffroi (649), Échelle de siège (614) ; douves : Pont royal (158, +6 %), Pierres (650) ; tirs : Bouclier royal (160, +6 %),
  Mur de boucliers (651), Mantelet (620) ; Tour de gloire (256) : un seul choix, le bot prend le niveau le plus haut qui a assez
  de stock (niveaux 1 à 11 = 256 à 266 : tirs -20 à -30 %, gloire +1 à +3 %), une case comme les autres engins, 30 au plus par
  vague tous côtés confondus (le jeu limite chaque niveau à 20 à 30 par vague). Un ancien réglage avec un engin retiré est
  repris avec celui de sa famille (envahisseurs / premium → Bélier lourd, Beffroi, Pierres ; Mantelet en fonte → Mur de
  boucliers). Bannières : Bannière (+1 %) … Étendard du roi suprême (+13 %).
- Cour (ligne ★, 19/11) : remplie au maximum à chaque attaque (interrupteur, coché par défaut), avec les soldats qui restent
  après les vagues (ceux des vagues d'engins d'abord, plusieurs types si besoin, 8 cases). Places : formule du jeu
  (`places_cour` : 20 × √(niveau + niveau légendaire) + 50 + 20 × niveau du camp + bonus d'équipement), le niveau du joueur lu au
  lancement (réponse `jaa` : gca.O.L + LL), puis suivi par les messages `gxp`. Si le jeu refuse la cour alors que les vagues sont
  déjà acceptées telles quelles (code 313), ce commandant part sans cour pour le reste du lancement.
- Soldats par vague : le maximum du jeu. Un camp de niveau 70 est un « combat légendaire » : les compétences de légende comptent
  (soldats en plus sur les flancs et au centre, outils en plus sur les flancs, vagues en plus), lues au lancement (demande `skl`).
  Le reste du bonus de chaque commandant (général, équipement d'événement…) n'est pas calculé : le bot l'apprend du jeu, qui
  refuse une armée trop grande (code 313) sans rien envoyer ; il réessaie aussitôt avec moins, et garde le maximum trouvé pour
  chaque commandant dans `captures/corbeaux_bonus.json`. Capture du 18/11 : 299 / 926 et 329 / 1041 soldats par flanc / au centre.
- S'il manque des soldats ou un engin pour la vague 1 : pas d'attaque, nouvel essai au retour d'un commandant ou 5 min plus tard.
- Arrêt : après N attaques (0 = pas de limite), une fois les dernières arrivées.
- Journal : `captures/journal_corbeaux.txt`.

## Pas encore vérifié en jeu

- Le saut du délai d'un camp nomade : même message que pour un camp samouraï (vérifié sur la capture du 20/11). Si le jeu le
  refuse, le module se met en pause et le dit dans le journal.
- Corbeaux de sang : le message de retour d'une armée (`cat`) sur un camp, et la réponse `skl` (compétences de légende) ne sont pas
  encore dans une capture ; sans réponse `skl`, le bonus de légende est appris du jeu comme le reste.

## Khan (attaque du camp et défense)

Module à part (case **Khan** du tableau de bord), réglé dans sa page **Khan** (sous Événements). Il ne tourne pas avec tours, forteresses, tours lev max ni nomades ; défense, recrutement, matériaux, achats et transferts peuvent tourner avec lui, seulement quand il n'a rien à faire dans les 30 s.

- **Attaque** : le bot trouve le camp du Khan de l'alliance, l'attaque avec les commandants choisis (engins nomades en vague 1,
  bannières de rage aux vagues suivantes, juste ce qu'il faut pour remplir la barre). Le délai du camp est sauté avec les passe-temps.
- **Déclencher le Khan** (option) : le bot garde N attaques du Khan en route vers le château principal du royaume vert. Il déclenche
  dès que la barre est pleine et qu'il en manque une ; chaque défense rend de la rage, le reste est complété par des attaques du camp.
- **Défense** (comme l'écran de défense du jeu) : par côté (flanc gauche, centre, flanc droit) un engin et une quantité par case
  (les portes seulement dans les 2 cases « Porte » du centre), la part des soldats (total 100 %), la part distance / mêlée ;
  les douves ; la cour (malles de pilleur, part distance / mêlée). Engins de secours si un engin manque. 999 engins au plus par
  case ; dès qu'une case passe sous 100, toute la défense est remise au plein (un seul message par partie : mur, cour, douves).
- **5e case des flancs** : jamais utilisée (elle ne sert que contre les seigneurs, le Khan n'en est pas un).
- **Page « Khan en direct »** (sous Journal) : rage, déclenchements, défenses, points, attaques du Khan en route avec compte à
  rebours, défense en place (engins par case, % de soldats et distance / mêlée), soldats choisis au château, engins en stock.
  La défense est relue chaque minute.
- **Recharge en pièces** (option) : rochers et porte en bois (seuls engins de la défense payés en pièces) rachetés par 3000 quand
  il en reste moins de 3000 en stock, jamais sous 5 millions de pièces. Les engins en rubis ne sont jamais achetés.
- **Soldats** : les types cochés défendent (Valkyrie franc-tireuse et Protecteur du nord : tous niveaux ensemble). Tous les autres
  soldats défensifs partent en soutien 12 h chez le joueur protégé choisi (commandants qu'aucun module n'utilise), et repartent
  s'ils reviennent.
- **Malles de pilleur** : achat en RUBIS (42 la malle, le coût est affiché) du nombre choisi, une seule fois au démarrage.
- **Attaque d'un joueur pendant la défense Khan** : le module Défense ignore les attaques du Khan ; les attaques du Khan qui arrivent
  avant celle du joueur sont défendues d'abord, puis il met tous les soldats à l'abri et ne rappelle que ceux qu'il a envoyés.
  Les attaques du Khan perdues pendant ce temps sont relancées.
- **Objectif** : nombre de déclenchements du Khan (faits par le bot ; les attaques déjà en route sont défendues ensuite) et / ou points nomades (1 tablette = 1 point, 1 médaille du Khan = 7 points). À l'objectif : plus
  de déclenchement, les attaques encore en route sont défendues, la défense du début est remise
  (gardée dans `captures/khan_defense_initiale.json`) et les soldats du joueur protégé sont rappelés.
- Les médailles et tablettes ne sont comptées qu'à partir du 2e message du jeu après le lancement (le jeu ne donne pas le stock avant).

Messages du jeu (captures du 16/11) : `dfc` (lecture de la défense), `dfw` (mur : `UP` = % des soldats, `UC` = % de mêlée),
`dfk` (cour), `dfm` (douves), `ibt` (achat de malles), `sdi` / `cds` / `mcm` (soutien), `gam` (attaque du Khan : type 20, attaquant -801),
`lta` (déclenchement), `rpr` (rage), `sce` `KM` / `KT` (médailles / tablettes).

Engins (noms de l'armurier dans les données du jeu) : Meurtrière 110 (icône vue dans l'écran de défense), Archère cruciforme 624
(Arrow slit), Mâchicoulis 627 (Machicolation), Rochers 637 (Defense rocks), Herse renforcée 622 (Portcullis), Porte nomade
renforcée 111, Porte du château renforcée 626 (Castle gate reinforcement), Douves enflammées 625 (Fire moat), Douves infernales 112.
