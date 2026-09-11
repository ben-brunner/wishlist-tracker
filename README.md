# Suivi des prix d'une wishlist imusic

Un relevé par jour, l'historique dans un CSV versionné, une page statique pour
le consulter. Pas de serveur, pas de base de données, pas de compte à créer.

```
track.py                      le relevé et tous les calculs
history.csv                   l'historique (une ligne par changement de prix)
public/index.html             la page
public/data.json              ce que lit la page, régénéré à chaque relevé
.github/workflows/releve.yml  le cron quotidien
```

## Mise en place

**1. Créer le dépôt.** Un dépôt GitHub privé, avec ces fichiers à la racine.

**2. Déclarer l'URL de la wishlist.** Dans le dépôt, *Settings → Secrets and
variables → Actions → New repository secret* :

- nom : `WISHLIST_URL`
- valeur : `https://imusic.fr/page/wishlist/VOTRE_JETON?sort=price&dir=asc&format=csv`

Cette URL est un secret plutôt qu'une constante du code parce que le jeton
qu'elle contient donne accès à la wishlist sans authentification.

**3. Lancer un premier relevé.** Onglet *Actions → Relevé des prix → Run
workflow*. Ça vérifie que tout marche sans attendre le cron.

**4. Publier la page.** Sur Cloudflare Pages, *Create a project → Connect to
Git*, choisir le dépôt puis :

- commande de build : aucune
- répertoire de sortie : `public`

Chaque commit du robot redéploie la page. Cloudflare accepte les dépôts privés,
contrairement à GitHub Pages qui exigerait de rendre la wishlist publique.

## Au quotidien

Le workflow tourne vers 7h17 UTC. Il télécharge l'export, **n'ajoute une ligne
à `history.csv` que si un prix ou une disponibilité a changé**, régénère
`data.json` et commite. Une journée sans mouvement ne produit aucun commit.

Pour un relevé manuel, en local :

```bash
WISHLIST_URL="https://imusic.fr/page/wishlist/…&format=csv" python3 track.py
```

Le script n'a besoin que de Python 3.10 ou plus récent. Aucune dépendance.

## Ce que montre la page

Le prix courant, l'écart avec le dernier prix différent, le plus bas et le plus
haut observés sur douze mois glissants, et une courbe en escalier de
l'historique. Un signet doré sur la couverture signale un article au plus bas
prix jamais relevé.

Tant qu'un article n'a qu'un seul relevé, la page affiche « suivi depuis le… »
plutôt qu'une variation : l'historique commence le jour de l'installation.
`history.csv` est amorcé avec le relevé du 10 septembre 2026 ; supprimez-le pour
repartir de zéro.

## Points de vigilance

**GitHub désactive les workflows programmés après 60 jours sans activité
humaine sur le dépôt.** Les commits du robot ne comptent pas. GitHub prévient
par mail avant de couper ; un clic suffit à réactiver. Si vous préférez ne pas
y penser, poussez un commit vide de temps en temps.

**Si le format de l'export change**, le script s'en aperçoit et le dit : il
cherche les colonnes par leur nom, signale les lignes au nombre de colonnes
inattendu, et refuse d'écrire si l'export revient vide — pour ne pas effacer
l'historique sur un incident passager.

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
