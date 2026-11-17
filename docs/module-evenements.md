# Module Événements

Les événements qui s'enchaînent. Pour l'instant : **les nomades**. Fichier `src/evenements.py`, réglages dans la page
Événements de l'interface (rubrique `evenements` de `config.json`), case « Événements » du tableau de bord.

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

## Pas encore vérifié en jeu

- Le saut du délai du camp : le bot envoie le même message que pour une tour. Si le jeu le refuse, le module se met en pause
  et le dit dans le journal.

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
