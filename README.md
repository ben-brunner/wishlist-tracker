# Comic Hunt

Deux outils pour la chasse aux comics, dans une seule page statique :

- **Suivi des prix** — un relevé par jour sur une wishlist imusic, l'historique
  dans un CSV versionné.
- **Recherche** — une chaîne de recherche, neuf boutiques ouvertes d'un coup.

Pas de serveur, pas de base de données, pas de compte à créer.

```
track.py                      le relevé et tous les calculs
history.csv                   l'historique (une ligne par changement de prix)
docs/index.html               les trois écrans (#suivi, #article/…, #recherche)
docs/data.json                ce que lit l'écran de suivi, régénéré à chaque relevé
tests/                        les pièges de l'export et des calculs, en filet
.github/workflows/releve.yml  le cron quotidien
```

Seul l'écran de suivi dépend de `track.py` ; la recherche est autonome et
fonctionne même sans relevé.

## Mise en place

**1. Créer le dépôt.** Un dépôt GitHub, avec ces fichiers à la racine.

**2. Déclarer l'URL de la wishlist.** Dans le dépôt, *Settings → Secrets and
variables → Actions → New repository secret* :

- nom : `WISHLIST_URL`
- valeur : `https://imusic.fr/page/wishlist/VOTRE_JETON?sort=price&dir=asc&format=csv`

Cette URL est un secret plutôt qu'une constante du code parce que le jeton
qu'elle contient donne accès à la wishlist sans authentification. Pour la même
raison, `data.json` ne la recopie pas : la page publiée serait sinon un moyen
commode de la lire.

**3. Lancer un premier relevé.** Onglet *Actions → Relevé des prix → Run
workflow*. Ça vérifie que tout marche sans attendre le cron.

**4. Publier la page.** Sur un dépôt public, *Settings → Pages*, source
*Deploy from a branch*, branche `master` et répertoire `/docs`. Chaque commit du
robot redéploie la page. C'est le nom `docs` qui rend ce choix possible : Pages
n'accepte que la racine ou ce répertoire-là.

Sur un dépôt privé, Pages demande un plan payant. L'alternative est Cloudflare
Pages (*Create a project → Connect to Git*, aucune commande de build,
répertoire de sortie `docs`), qui accepte les dépôts privés.

## Au quotidien

Le workflow tourne vers 7h17 UTC. Il télécharge l'export, **n'ajoute une ligne
à `history.csv` que si un prix ou une disponibilité a changé**, régénère
`data.json` et commite.

Le commit, lui, a lieu tous les jours : `data.json` porte l'heure du relevé,
donc il change même quand aucun prix n'a bougé. C'est voulu — c'est ce qui
permet à la page d'afficher « dernier relevé le… » sans mentir, et au dépôt de
montrer que le robot tourne encore. Une journée sans mouvement se reconnaît à
son commit qui ne touche pas `history.csv`.

Pour un relevé manuel, en local :

```bash
WISHLIST_URL="https://imusic.fr/page/wishlist/…&format=csv" python3 track.py
```

Le script n'a besoin que de Python 3.10 ou plus récent. Aucune dépendance.

## Les tests

```bash
python3 -m unittest discover tests
```

Bibliothèque standard, aucune installation. Ils couvrent ce qui casse sans
prévenir : les entités HTML qui décalent les colonnes de l'export, les retours
à la ligne de la colonne Prix, la courbe en escalier, la médiane pondérée par
la durée, les seuils du signal « à saisir » et le garde-fou contre les exports
tronqués. `tests/exports/wishlist.csv` est un export figé qui contient chacun
de ces pièges — c'est le fichier à enrichir quand iMusic en invente un nouveau.

## Ce que montre l'écran de suivi

Le prix courant, l'écart avec le dernier prix différent, le plus bas et le plus
haut observés sur douze mois glissants, et une courbe en escalier de
l'historique. Une étoile jaune sur la couverture signale un article au plus bas
prix jamais relevé, et une ligne teintée de rouge un prix jugé assez bas pour
qu'on se dépêche (voir « Le signal “à saisir” »).

Tant qu'un article n'a qu'un seul relevé, la page affiche « suivi depuis le… »
plutôt qu'une variation : l'historique commence le jour de l'installation.
`history.csv` est amorcé avec le relevé du 10 septembre 2026 ; supprimez-le pour
repartir de zéro.

## Le signal « à saisir »

Certaines lignes de la liste se teintent de rouge et portent la mention
« à saisir ». C'est le seul endroit où la page se permet un conseil. Deux
conditions, sur le prix seul :

- le prix est **au moins 10 % sous le prix habituel** de l'article ;
- l'écart fait **au moins 2 €** — 10 % sur un livre à 6 €, ça ne se saisit pas.

Le *prix habituel* n'est pas une moyenne, c'est le prix qui s'est appliqué la
moitié du temps depuis le début du suivi. Un prix tenu six mois y pèse donc plus
qu'une promotion de trois jours, et une valeur extrême ne le tire pas à elle.
C'est aussi ce qui éteint le signal quand un prix bas dure : au bout d'un moment
il *devient* le prix habituel, et l'écart tombe de lui-même.

Un article indisponible ne se signale jamais — il ne s'achète pas. Une
précommande, si.

**La nuance, quand il y en a une.** Un article peut être 23 % sous son prix
habituel et pourtant y descendre souvent. Le signal s'allume quand même — la
baisse est réelle aujourd'hui —, mais la ligne ajoute en gris « déjà vu moins
cher 45 % du temps », et la fiche l'explique en toutes lettres. La page
vous donne l'affaire et son voisinage ; elle ne décide pas à votre place que ça
ne compte pas.

**On ne peut pas le rater.** La bulle de résumé l'annonce avant tout le reste
— « 2 articles à saisir · 3 baisses cette semaine ! » —, et le tri par défaut
les place en tête de liste, devant les mouvements récents. Les autres tris
(prix, écart, titre) n'y touchent pas : quand vous demandez un ordre précis,
vous l'obtenez. La puce **À saisir** en haut de la liste ne garde que ces
articles, et leur fiche s'ouvre sur une planche qui donne le calcul en toutes
lettres.

Comme le reste, ce signal ne prédit rien : il ne dit pas que le prix va
remonter, il dit qu'on l'a rarement vu descendre aussi bas.

## La fiche d'un article

Le titre ou la pochette d'une ligne ouvre sa fiche, à l'adresse
`…/#article/9781401294052`. Deux boutons s'y trouvent : **Voir sur iMusic**, et
**Chercher ailleurs**, qui bascule sur l'écran de recherche avec l'**ISBN** déjà
dans le champ. L'ISBN plutôt que le titre, parce que neuf boutiques ont neuf
façons d'écrire « Deluxe Edition » mais qu'elles partagent le code-barres. Rien
ne part tant que vous n'avez pas cliqué sur *Tout lancer* : neuf onglets qui
s'ouvrent, ça se décide.

On y lit :

- **la courbe en escalier en grand**, sur trois mois, douze mois ou tout
  l'historique. Le curseur donne la date et le prix qui s'appliquait ce jour-là.
  Les périodes sans prix — rupture de stock, précommande — apparaissent en
  bandes derrière la courbe, et le plus bas jamais relevé en pointillé doré ;
- **où en est ce prix** : sa position dans la fourchette de tout ce qu'on a vu,
  le nombre de jours passés moins cher, le rythme et l'amplitude des mouvements.
  Chaque terme est défini sous le chiffre ;
- **mois par mois**, le prix le plus bas en vigueur pendant chacun des douze
  derniers mois ;
- **le journal des changements**, une ligne par relevé qui a constaté quelque
  chose.

Les deux premiers blocs restent muets tant que l'historique est trop court, et
le troisième n'apparaît qu'après quelques mois : mieux vaut ne rien dire qu'un
chiffre calculé sur trois relevés. Échap, ou « ← Tous les articles », ramène à
la liste, à l'endroit où on l'avait quittée.

## Pourquoi la fiche ne prédit rien

Elle décrit le passé, elle n'annonce pas l'avenir, et c'est délibéré.

Ce qui fait bouger un prix iMusic — rupture de stock, réimpression, taux de
change, promotion de l'éditeur, fin de commercialisation — n'est nulle part dans
`history.csv`. Un modèle nourri d'un point par semaine et par article afficherait
des chiffres d'allure sérieuse et sans contenu : « prix estimé dans 30 jours :
12,80 € ». C'est le contraire de ce que fait le reste du script, qui préfère ne
rien écrire plutôt que d'interpréter un incident réseau comme une wishlist vidée.

Les indicateurs répondent donc à la question utile — « ce prix-là, est-ce une
occasion ? » — en la retournant vers ce qui est connu : *il a été moins cher
103 jours sur les 430 où il avait un prix*. Libre à vous de conclure.

## Un article qui quitte la wishlist

Le jour où un article n'est plus dans l'export, une ligne « retiré » entre dans
`history.csv`, comme n'importe quel autre changement. Elle sert deux fois.

D'abord, elle **coupe le palier**. Un article sorti puis remis six mois plus
tard au même prix ne produirait sinon aucune ligne, et l'historique prétendrait
que ce prix a été observé sans interruption : le prix habituel et le temps
passé moins cher se mettraient à compter des semaines que personne n'a
regardées. C'est la même règle pour une rupture de stock — pendant qu'il
n'affiche pas de prix, un article n'en pratique aucun, et ces jours-là ne
comptent ni au numérateur ni au dénominateur.

Ensuite, elle permet à l'article de **revenir sans repartir de zéro**. La clé
est l'ISBN, pas le rang dans l'export : remis dans la wishlist, il retrouve son
passé, et les mois d'absence ne comptent pas comme un prix tenu.

**Sur la page, en revanche, il disparaît.** Un article sorti de la wishlist est
presque toujours un article acheté, et ce suivi sert à décider d'un achat, pas à
tenir le registre de ce qu'on possède : il quitte la liste, et un signet vers sa
fiche affiche « article introuvable ». Aucune date d'achat, aucun prix payé,
aucune liste des articles sortis. L'historique, lui, garde tout — mais dans
`history.csv`, pas sous les yeux.

## Points de vigilance

**GitHub désactive les workflows programmés après 60 jours sans activité
humaine sur le dépôt.** Les commits du robot ne comptent pas. GitHub prévient
par mail avant de couper ; un clic suffit à réactiver. Si vous préférez ne pas
y penser, poussez un commit vide de temps en temps.

**Si le format de l'export change**, le script s'en aperçoit et le dit : il
cherche les colonnes par leur nom, signale les lignes au nombre de colonnes
inattendu, et refuse d'écrire si l'export revient vide — pour ne pas effacer
l'historique sur un incident passager.

**Un export tronqué est refusé, lui aussi.** Cinq articles là où il y en avait
vingt-sept seraient lus comme vingt-deux retraits d'un coup, qui s'écriraient
dans `history.csv` et couperaient autant de paliers. Le script saute donc le
relevé quand la wishlist perd plus d'un tiers de ses articles en une fois : elle
ne se vide pas de cette façon, un incident chez iMusic si. Le seuil est
`CHUTE_MAX_PCT`.

Comme le refus n'écrit rien, il se répéterait indéfiniment après un vrai grand
ménage : l'état auquel le script compare ne bouge pas. Un relevé forcé débloque
la situation, une fois pour toutes :

```bash
FORCER_RELEVE=1 WISHLIST_URL="…" python3 track.py
```

Il ne reste qu'à commiter `history.csv` et `docs/data.json` : le relevé du
lendemain comparera au nouvel état et repassera tout seul.

**Les liens vers les fiches produit** sont reconstruits à partir de l'ISBN et du
slug présents dans l'URL de la pochette, sous la forme
`imusic.fr/books/ISBN/slug`. Si vous ajoutez des disques plutôt que des livres,
le segment devient `music` — c'est la seule heuristique du script, à ajuster
dans `parse_export` si un lien tombe à côté.

## Une subtilité sur le « plus bas sur 1 an »

L'historique ne stocke que les changements, donc le prix est une courbe en
escalier : entre deux lignes, il n'a pas bougé. Pour calculer le minimum sur
douze mois, il faut donc inclure le prix qui était **en vigueur** au début de la
fenêtre, même s'il a été relevé bien avant. C'est ce que fait la fonction
`window()`. Sans ça, un article dont le prix n'a pas bougé depuis quatorze mois
n'aurait aucun point dans la fenêtre et paraîtrait sans historique.
