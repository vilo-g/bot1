# Conception du bot

Inspirée d'un bot existant dont tu as fourni des captures (`docs/exemple-bot/`). On reprend ses réglages,
mais le bot se branche sur le Chrome connecté du joueur (voir `README.md`).

## Modules

| Module | Rôle |
|--------|------|
| **cœur** | branchement au Chrome, envoi / réception des paquets, carte (`gaa`), inventaire et commandants (`adi`/`abi`), planificateur, journal |
| **tours** | choisit les tours attaquables dans le rayon (type 2, minuteur `T` <= 0), lance les attaques, gère la réattaque |
| **forteresses** | même chose pour les forteresses (type 11, minuteur `T` = 0), avec `abi` et ses propres règles |
| **interface** | fenêtre de réglage + démarrer / arrêter |
| **config** | fichier de réglages par royaume (voir plus bas) |

Les modules « tours » et « forteresses » partagent le cœur et ne diffèrent que par le choix des cibles,
la demande préalable (`adi` ou `abi`) et le délai de réattaque.

## Réglages repris du bot d'exemple

Par royaume (Vert 0, Sables 1, Glacier 2, Feu 3), avec une case à cocher :

- **Rayon** autour du château (en cases ; 70 ou 150 dans les captures).
- **Armée** : deux modes.
  - *Waves* : composition fixée vague par vague (modèle enregistré d'une attaque manuelle).
  - *Autofill* : le bot remplit lui-même à partir de l'inventaire. Exemple de la capture : « L · 4w · Ladders only »
    = flanc gauche, 4 vagues, échelles seulement.
- **Filtre de tours** (« All Towers »).
- **Nombre max d'attaques** (50 dans la capture).
- **Châteaux de départ** (le principal, plus d'éventuels avant-postes), avec pour chacun : rayon, outils (on/off),
  type de cheval, priorité de butin, composition propre qui remplace celle du royaume.
- **Cheval** : « Distance » = cheval « pièces » en dessous d'une distance (20 cases dans la capture), cheval « plume »
  au-delà. Le lien avec le champ `HBW` du `cra` est **à tester** (nos `cra` envoient -1 ; le serveur a répondu 1009).
- **Commandants** : liste de numéros séparés par des points-virgules (25 sur 35 dans la capture). Ce sont les
  positions dans la liste du jeu, exactement ce qu'on a confirmé (position -> `LID`).

## Déroulement d'un cycle

1. Lire l'état (carte autour du château pour chaque royaume actif, inventaire, commandants libres).
2. Garder les cibles attaquables (minuteur) dans le rayon, triées par distance.
3. Pour chaque cible : choisir un commandant libre, construire la composition (modèle ou autofill),
   envoyer `adi`/`abi` puis `cra`, lire la réponse (`TT` = trajet).
4. Noter l'heure d'arrivée et de retour (`cat`), lire le rapport (`bls`).
5. S'arrêter au nombre max d'attaques, ou dormir jusqu'à la prochaine cible disponible.

## Prudence (à intégrer dès le départ)

- Délais aléatoires entre deux envois, pas de rafale.
- Arrêt immédiat à la première réponse inattendue du serveur ; tout est écrit dans un journal.
- Premier essai toujours sur un compte de test (`outils/test_envoi.py`).
- Le bot ne manipule jamais le mot de passe.
