# Contexte du projet

Deux outils dans une même page statique (`docs/`) :

- **Suivi des prix** des articles d'une wishlist imusic. Un relevé par jour via
  GitHub Actions, historique dans `history.csv`. Deux vues : la liste de tous
  les articles, et la fiche d'un article.
- **Recherche multi-boutiques** : une chaîne de recherche ouvre un onglet par
  boutique. Entièrement côté client, aucun lien avec `track.py`.

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

**L'escalier s'interrompt là où il n'y a pas de prix.** `paliers()` ne produit
rien pour un relevé sans prix, et coupe donc le palier précédent : pendant une
rupture de stock ou une sortie de la wishlist, l'article ne pratiquait aucun
prix. Prolonger le dernier prix connu à travers ces semaines-là gonflerait le
prix habituel et le temps passé moins cher avec des journées que personne n'a
observées — et c'est le prix habituel qui décide du signal « à saisir ». C'est
pourquoi `paliers()` travaille sur `observations()` (tous les relevés, prix à
`None` compris) et non sur `build_series()`, qui ne garde que ce qui se dessine.

Deux durées cohabitent donc, et il faut choisir la bonne : `jours_suivi` est
l'étendue du calendrier depuis le premier relevé, `jours_observes` le temps
pendant lequel l'article avait un prix. Tout ce qui se rapporte aux paliers —
le prix habituel, `part_moins_cher_pct` — se compte sur le second. C'est ce qui
garde vraie la propriété « on ne peut pas avoir été moins cher plus de ~50 % du
temps » : la médiane et la part doivent parler de la même période.

**Un relevé manqué n'est pas un trou.** Si le cron saute trois jours, rien n'est
écrit et la règle de l'escalier s'applique telle quelle — c'est la convention du
projet entre deux relevés quelconques. Ce qui se marque, c'est l'absence
*constatée* : l'article n'était pas dans l'export.

## Un article qui quitte la wishlist

Le jour où un article suivi n'apparaît plus dans l'export, `append_changes()`
écrit une ligne `retire` sans prix. Elle fait trois choses à la fois : elle
coupe le palier (voir plus haut), elle fait que le retour de l'article — même au
même prix — s'inscrit comme un changement de statut, et elle donne à
`roster()` l'état courant de la wishlist sans qu'aucun état n'ait à être
mémorisé ailleurs. Ne pas remplacer ça par un fichier d'état : l'historique se
suffit, et une ligne de journal se relit.

**Mais la ligne ne sort pas de `history.csv`.** Un article qui quitte la
wishlist est presque toujours un article acheté, et **la page n'en garde aucune
trace** : il disparaît de `data.json`, donc de la liste, et son lien en signet
retombe sur « article introuvable ». C'est un choix, pas un oubli — ce suivi
sert à décider d'un achat, pas à tenir le registre de ce qu'on possède. Ne pas
réintroduire de bloc « acheté le… à… », ni de liste des articles sortis : la
proposition a été faite et écartée.

Ce que l'historique garde, lui, n'est pas de la mémoire d'achat : c'est ce qui
rend le calcul juste si l'article revient un jour dans la wishlist.

## Rien ne prédit le prix de demain

La fiche d'un article situe le prix du jour dans son passé — position dans la
fourchette, temps passé moins cher, rythme des mouvements. **Aucun de ces
chiffres n'extrapole, et il ne faut pas en ajouter qui le feraient.**

La raison n'est pas la taille de l'historique, elle est dans la matière : ce qui
fait bouger un prix imusic — rupture de stock, réimpression, taux de change,
promotion de l'éditeur, fin de commercialisation — n'apparaît nulle part dans
`history.csv`. Un modèle nourri d'un point par semaine et par article sortirait
des chiffres d'allure sérieuse et sans contenu. C'est exactement ce que le reste
du script refuse de faire quand il préfère ne rien écrire plutôt que
d'interpréter un export vide.

La saisonnalité (`minimums_mensuels`) est une exception apparente seulement :
elle montre ce qui *a eu lieu* mois par mois, elle n'annonce rien.

**Les indicateurs se taisent tant qu'ils mentiraient.** En dessous de
`MIN_RELEVES_INDICATEURS` relevés ou de `MIN_JOURS_INDICATEURS` jours de suivi,
`indicateurs()` renvoie des `None` et la fiche n'affiche pas le bloc. Même
principe pour `minimums_mensuels` sous `MIN_MOIS_SAISON` mois. C'est track.py
qui tranche : la page ne connaît pas les seuils, elle constate un `None`.

## Le signal « à saisir »

Le seul endroit où la page conseille quelque chose. `affaire()` ne juge que le
prix : au moins 10 % **et** 2 € sous le prix habituel. Le plancher en euros
existe parce que 10 % sur un livre à 6 € ne se saisit pas.

`prix_habituel()` est une **médiane pondérée par la durée**, pas une moyenne :
un prix tenu six mois doit peser plus qu'une promotion de trois jours, et une
moyenne se laisse tirer par une valeur extrême. C'est aussi ce qui éteint le
signal quand un prix bas s'installe et devient le prix normal — l'écart tombe
à zéro tout seul, sans qu'on ait à ajouter de règle.

**La rareté ne bloque pas le signal, et ce point a été tranché.** Un premier jet
éteignait le signal quand l'article avait été moins cher plus de 10 % du temps
suivi. Le motif invoqué — « il redescendra à ce prix-là » — est une prédiction,
exactement ce que le reste du projet refuse ; et une baisse de 23 % reste une
baisse de 23 % le jour où on la voit. La part du temps passé moins cher est donc
calculée, renvoyée dans `part_moins_cher_pct`, et **dite** : au-dessus de
`SEUIL_NUANCE_PCT`, `deja_moins_cher` passe à vrai et la page ajoute « déjà vu
moins cher x % du temps » sous la mention, en gris. Informer plutôt que filtrer.

Une conséquence de la médiane à connaître avant de retoucher ce seuil : un
article ne peut pas avoir été moins cher plus de ~50 % du temps, puisque c'est
la définition de la médiane. Un seuil de nuance au-delà de 45 % ne se
déclencherait donc jamais.

Le test sur le statut est une ceinture par-dessus les bretelles : imusic
n'affiche pas de prix pour un article indisponible, donc `prix` est déjà `None`
et `affaire()` renvoie `None` avant d'y arriver. On le garde pour que la règle
soit lisible dans le code — un indisponible ne s'achète pas, une précommande si.

Comme le reste de la fiche, c'est un constat sur le passé. Le libellé doit le
rester : « on l'a rarement vu aussi bas », jamais « le prix va remonter ».

**Ce signal se voit sans qu'on le cherche.** C'est l'information la plus rare de
la page et la seule qui appelle une décision : elle ne peut pas dépendre du
hasard d'une ligne teintée aperçue en bas de liste. La bulle de résumé l'annonce
en tête (`resumer()`), et le tri par défaut fait remonter ces articles avant les
mouvements de la semaine. Les trois autres tris — prix, écart, titre — n'y
touchent pas : ce sont des demandes explicites, et y glisser un critère de plus
les trahirait.

## Garde-fous

Le script refuse d'écrire si l'export revient vide, pour ne pas interpréter un
incident réseau comme une wishlist vidée. Il signale sur stderr les lignes dont
le nombre de colonnes est inattendu.

Il refuse aussi un export *tronqué* — `export_suspect()`, plus d'un tiers des
articles perdus d'un coup. Ce garde-fou n'était qu'envisagé tant que les
retraits ne s'écrivaient pas ; depuis qu'ils entrent dans `history.csv`, il est
porteur : cinq articles sur vingt-sept produiraient vingt-deux lignes `retire`
et couperaient autant de paliers, durablement.

Comme un refus n'écrit rien, l'état auquel il compare ne bouge pas : après un
vrai grand ménage, tous les relevés suivants seraient refusés. D'où
`FORCER_RELEVE=1`, qui lève le garde-fou le temps d'une exécution. Un garde-fou
qui ne peut pas être levé est un piège, pas une protection.

## Tester

`tests/test_track.py`, `unittest` de la bibliothèque standard, aucune
dépendance :

```bash
python3 -m unittest discover tests
```

Chaque test correspond à un paragraphe de ce fichier — c'est le point : les
pièges décrits ici sont ceux dont la violation ne se voit pas. Un export figé,
`tests/exports/wishlist.csv`, les contient tous (entité `&amp;` dans l'URL de
pochette, colonne Prix éclatée sur deux lignes physiques, prix à séparateur de
milliers, indisponible, précommande). Quand iMusic invente un nouveau piège,
c'est ce fichier qu'on enrichit.

Les dates y sont figées (`MAINTENANT`) : les indicateurs comptent du temps
écoulé, et un test qui dépend de l'heure qu'il est ne prouve plus rien deux mois
plus tard.

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

**Les trois écrans vivent dans ce même fichier**, en trois `<section class="ecran">`
que `router()` montre ou cache selon le fragment d'URL (`#suivi`, `#recherche`,
`#article/<isbn>`, `#recherche/<requête>` ; défaut : `#suivi`). Un fichier par
écran obligerait à dupliquer la feuille de style.

**Le seul fil entre la recherche et le reste passe par ce fragment.** Le bouton
« Chercher ailleurs » de la fiche mène à `#recherche/<isbn>`, et `preremplir()`
pose la chaîne dans le champ. L'ISBN plutôt que le titre : neuf boutiques ont
neuf façons d'écrire « Deluxe Edition », mais un seul code-barres — quand l'id
n'est pas un ISBN, `requete()` retombe sur le titre. Le fil ne va que dans ce
sens et ne transporte qu'une chaîne : l'écran de recherche continue d'ignorer
`data.json` et toutes les fonctions du suivi, et marcherait tel quel si le suivi
disparaissait. Ne pas élargir ce passage en lui faisant traverser un article.

`preremplir()` **ne lance pas** la recherche : neuf onglets qui s'ouvrent sont
une décision, pas une conséquence de la navigation. Et un `#recherche` nu ne
touche pas au champ, pour que revenir par l'onglet retrouve ce qu'on y tapait.

Le suivi et la fiche, eux, lisent le même `data.json` : la fiche cherche son
article par `id` dans le tableau déjà chargé. Un identifiant absent affiche un
message, pas une page vide — un article retiré de la wishlist disparaît de
`data.json` alors que son lien peut rester dans un signet.

Un site statique n'a pas de routage serveur : c'est ce qui impose le fragment
plutôt qu'une vraie URL par article, et ce qui exclut de générer 27 fichiers
depuis `track.py`.

Le premier appel à `router()` est **à la fin du script**, pas à sa déclaration :
ouvrir la page directement sur `#article/…` demande que la fiche et ses
constantes existent déjà. Et comme `data.json` arrive plus tard, le `fetch`
rappelle `router()` une fois les données là.

L'esthétique est « comics » : trame de points en fond, panneaux blancs bordés de
3 px avec une ombre dure et décalée, Bangers pour les titres et les prix, Space
Grotesk pour le texte, JetBrains Mono pour les libellés et les dates. Toutes les
couleurs passent par les variables de `:root`.

**Le décor est dosé selon la densité de l'écran.** La recherche n'a qu'un champ
et neuf cases à montrer : elle peut se permettre les bordures épaisses, les
ombres dures et les encadrés. Le suivi affiche une trentaine d'articles avec
chacun six chiffres : il les présente **à plat**, en lignes séparées par un
filet (`--rule`), dans **une seule** planche blanche. Ne pas y remettre une
bordure par article — c'est ce qui avait été essayé, et la page devenait
illisible. Ce qui porte l'identité comics sur cet écran : le titre, la bulle de
résumé, les prix en Bangers, l'étoile jaune, le vert et le rouge des variations.

**L'exception, c'est la ligne « à saisir »** : fond teinté et filet rouge à
gauche, toujours pas de cadre. Les marges négatives de `#liste li.saisir`
compensent exactement le filet et le retrait, pour que le contenu ne se décale
pas d'un pixel quand le signal s'allume ; si les gouttières de la planche
changent, ces trois nombres changent ensemble. Et le fond n'est pas le signal à
lui seul : la mention « à saisir · x % sous le prix habituel » le dit en toutes
lettres, avec sa raison.

La fiche est à l'autre bout : un seul article, donc peu dense, donc le décor
complet — une planche par bloc, bordures de 3 px, ombres décalées.

**Sur la fiche, track.py fournit tout ce qui se compte** : `statuts`,
`journal`, `indicateurs`, `min_mensuel`, `max_total`, `affaire`. La page ne fait que deux
choses avec des nombres, toutes deux géométriques : mettre des prix à l'échelle
d'un dessin, et tailler la série à la fenêtre choisie (3 mois / 12 mois / tout).
Cette taille applique la règle de l'escalier comme `window()` : elle **greffe**
au bord gauche le prix qui était en vigueur, sinon un article stable depuis six
mois donnerait un graphique vide sur trois mois. Le point greffé n'est pas un
relevé, il ne reçoit donc pas de pastille.

Les graduations du graphique sont lues sur ce qui est dessiné : ce sont des
étiquettes d'axe, pas des statistiques. Ne pas les confondre avec les chiffres
du bloc « où en est ce prix ? », qui viennent tous de `track.py`.

**`series` ignore les relevés sans prix**, d'où la piste `statuts` : sans elle,
une rupture de stock ressemblerait à un palier plat. Elle est peinte en bandes
derrière la courbe.

Le cadre du dessin (`geometrie()`) est plus haut en dessous de 560 px de large :
au ratio du bureau, un téléphone donnerait une courbe de 130 px et des
étiquettes illisibles. Le cadre voyage dans l'état de la courbe (`courbe.g`)
parce que l'infobulle doit convertir la position du curseur avec l'échelle qui a
servi à peindre.

Deux règles à ne pas défaire :

- Le texte gris posé directement sur la trame de points est illisible. Un
  paragraphe hors planche est donc un encadré de narration (`.accroche`,
  `.footnote`, écran recherche) — fond plein, bord noir, ombre décalée. Sur le
  suivi, le même besoin se règle en mettant le texte *dans* la planche
  (`.note`, `.releve`), sans encadré.
- Le jaune sert de remplissage, jamais de couleur de texte : `--or` existe pour
  ça (le repère « plus bas sur 1 an »).
