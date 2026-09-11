# Contexte du projet

Suivi du prix des articles d'une wishlist imusic. Un relevé par jour via GitHub
Actions, historique dans `history.csv`, page statique dans `docs/`.

Contrainte directrice : **rester simple**. Un seul utilisateur, pas de besoin de
montée en charge. Pas de base de données, pas de framework, pas de dépendance
Python. Avant d'ajouter quelque chose, vérifier que ça ne peut pas se faire avec
la bibliothèque standard et un fichier plat.

## Pièges de l'export imusic

Ces deux points ont été découverts en testant sur le vrai export. Ne pas les
« simplifier » sans relire ce qui suit.

**Les entités HTML cassent le découpage en colonnes.** Les URL de pochette
contiennent `&amp;`, dont le point-virgule est pris pour un séparateur. Sans le
décodage préalable (`ENTITY_RE` dans `parse_export`), tout l'export se décale et
le titre devient `v=1567761787`. Le décodage est volontairement limité aux
entités bien formées : `html.unescape` sur le texte entier traduirait aussi des
faux amis comme `&not`.

**La colonne Prix contient des retours à la ligne** et mélange deux
informations : le montant et la disponibilité (`€ 13.49 … Acheter`,
`Indisponible`, `Pré-commande`). Selon que ces retours sont échappés ou non, une
ligne logique peut être éclatée sur plusieurs lignes physiques : `read_rows`
recolle les morceaux jusqu'à atteindre le bon nombre de colonnes.

**Il n'y a pas d'identifiant produit dans l'export.** La clé utilisée est l'ISBN
extrait du nom de fichier de la pochette. C'est ce qui permet à un article
retiré puis re-ajouté à la wishlist de retrouver son historique.

## Le prix est une courbe en escalier

`history.csv` ne contient **que les changements** : entre deux lignes, le prix
n'a pas bougé. Toute statistique sur une fenêtre temporelle doit donc inclure le
prix *en vigueur* au début de la fenêtre, même s'il a été relevé bien avant.
C'est le rôle de `window()`. L'oublier ferait disparaître de la fenêtre tout
article dont le prix est stable depuis plus d'un an.

## Garde-fous

Le script refuse d'écrire si l'export revient vide, pour ne pas interpréter un
incident réseau comme une wishlist vidée. Il signale sur stderr les lignes dont
le nombre de colonnes est inattendu.

**Angle mort connu :** un export *tronqué* (5 articles sur 27) serait accepté et
interprété comme 22 retraits. L'historique resterait intact, mais la page
mentirait pendant une journée. Un garde-fou envisagé : refuser un relevé où le
nombre d'articles chute de plus d'un tiers.

## Tester sans réseau

`WISHLIST_URL` accepte une URL `file://`, ce qui permet de rejouer des scénarios
sur des exports figés :

```bash
WISHLIST_URL="file:///chemin/vers/export.csv" python3 track.py
```

Pour fabriquer une variante d'un export existant, découper les enregistrements
sur le motif de date en début de ligne — pas sur `\n`, à cause des retours à la
ligne internes au champ Prix :

```python
re.split(r"(?m)^(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2};)", corps)
```

## Interface

`docs/index.html` est autonome : un seul fichier, pas de build, pas de
dépendance hors les polices Google. Il lit `docs/data.json` et ne calcule
rien — minimums, maximums et variations sont produits par `track.py`. Garder
cette séparation.
