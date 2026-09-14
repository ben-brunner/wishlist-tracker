#!/usr/bin/env python3
"""Relève les prix d'une wishlist imusic et tient leur historique.

Une exécution =
  1. télécharge l'export CSV de la wishlist,
  2. ajoute une ligne dans history.csv pour chaque prix qui a bougé,
  3. régénère docs/data.json que lit la page web.

Aucune dépendance : bibliothèque standard uniquement.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history.csv"
OUTPUT = ROOT / "docs" / "data.json"

WISHLIST_URL = os.environ.get("WISHLIST_URL", "").strip()

# Le garde-fou contre les exports tronqués refuse d'écrire, donc l'état auquel
# il compare ne bouge pas : après un vrai grand ménage dans la wishlist, il
# bloquerait tous les relevés suivants. Cette variable dit « oui, je sais » le
# temps d'un relevé.
FORCER = os.environ.get("FORCER_RELEVE", "").strip() not in ("", "0")

HISTORY_FIELDS = ["date", "id", "titre", "prix", "statut"]

# « € 63.99 », « 1 234,50 »… on ne garde que le nombre.
PRICE_RE = re.compile(r"(\d+(?:[ \u00a0]\d{3})*[.,]\d{2})")
# L'ISBN/EAN est dans le nom du fichier de la pochette : .../9781401294052.jpg
ISBN_RE = re.compile(r"/(\d{8,14})\.(?:jpe?g|png|webp)", re.IGNORECASE)

WINDOW_DAYS = 365

# Les indicateurs rétrospectifs de la fiche (position dans la fourchette, temps
# passé moins cher, rythme des mouvements) ne veulent rien dire sur deux ou trois
# relevés. En dessous de ces seuils, track.py renvoie None et la fiche n'affiche
# rien plutôt qu'un chiffre qui a l'air sérieux.
MIN_RELEVES_INDICATEURS = 4
MIN_JOURS_INDICATEURS = 14
MIN_MOIS_SAISON = 4

# Un article peut quitter la wishlist, puis y revenir. Le jour où on constate
# son absence, une ligne « retiré » l'enregistre comme n'importe quel autre
# changement. Elle ne sert pas qu'au journal : sans prix, elle coupe le palier,
# et les mois d'absence cessent d'être comptés comme un prix tenu.
STATUT_RETIRE = "retire"

# Un export tronqué — cinq articles là où il y en a vingt-sept — serait lu comme
# vingt-deux retraits d'un coup. Une wishlist ne se vide pas de cette façon ; un
# incident chez imusic, si. En dessous de ce seuil, on ne touche à rien.
CHUTE_MAX_PCT = 33.0

# Le signal « à saisir » du listing ne juge que le prix : nettement sous ce que
# l'article coûte d'habitude, et d'une somme qui se remarque.
SEUIL_ECART_PCT = 10.0     # au moins 10 % sous le prix habituel
SEUIL_ECART_EUROS = 2.0    # et au moins 2 € : 10 % de 6 €, ça ne se saisit pas

# La rareté ne bloque pas le signal : un prix nettement bas reste un prix
# nettement bas, même si l'article y redescend souvent. Elle est dite à côté,
# quand elle vaut la peine d'être dite — au-dessus de ce seuil, la page ajoute
# « déjà vu moins cher x % du temps ».
SEUIL_NUANCE_PCT = 10.0

# Les URL de pochette contiennent « &amp; ». Le point-virgule de l'entité est
# pris pour un séparateur de colonnes et décale tout l'export : on décode les
# entités bien formées avant de parser. Regex ciblée plutôt que html.unescape
# sur tout le texte, qui traduirait aussi des faux amis comme « &not ».
ENTITY_RE = re.compile(r"&(?:amp|quot|apos|lt|gt|nbsp|#\d+|#x[0-9a-fA-F]+);")


# --------------------------------------------------------------------------
# Téléchargement et lecture de l'export
# --------------------------------------------------------------------------

def download(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "wishlist-tracker (usage personnel)"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


def read_rows(text: str, ncols: int) -> list[list[str]]:
    """Découpe le CSV en lignes de `ncols` colonnes.

    La colonne « Prix » d'imusic contient des retours à la ligne. S'ils sont
    correctement échappés, csv.reader s'en occupe ; sinon une ligne logique se
    retrouve éclatée sur plusieurs lignes physiques. On recolle les morceaux
    tant que le compte de colonnes n'y est pas.
    """
    rows: list[list[str]] = []
    buf: list[str] | None = None
    for raw in csv.reader(io.StringIO(text), delimiter=";"):
        if buf is None:
            if not raw:
                continue
            buf = list(raw)
        else:
            buf[-1] = buf[-1] + "\n" + (raw[0] if raw else "")
            buf.extend(raw[1:])
        if len(buf) >= ncols:
            rows.append(buf)
            buf = None
    if buf:
        rows.append(buf)
    return rows


def cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace("\u00a0", "").replace(" ", "").replace(",", "."))


def parse_status(text: str) -> str:
    low = text.lower()
    if "indisponible" in low:
        return "indisponible"
    if "commande" in low:  # « Pré-commande »
        return "precommande"
    if "acheter" in low or "€" in text:
        return "disponible"
    return "inconnu"


def find_price_cell(row: list[str], preferred: int | None) -> str:
    """La colonne prix, en se rabattant sur le contenu si l'index a glissé."""
    candidate = cell(row, preferred)
    if PRICE_RE.search(candidate) or "disponible" in candidate.lower():
        return candidate
    for value in row:
        if PRICE_RE.search(value) or "indisponible" in value.lower():
            return value.strip()
    return candidate


def parse_export(text: str) -> list[dict]:
    text = ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text)
    header_row = next(csv.reader(io.StringIO(text), delimiter=";"), [])
    header = [h.strip().lower() for h in header_row]

    def find(*needles: str) -> int | None:
        for i, name in enumerate(header):
            if any(n in name for n in needles):
                return i
        return None

    idx = {
        "pochette": find("pochette", "cover"),
        "artiste": find("artiste", "artist"),
        "titre": find("titre", "title"),
        "prix": find("prix", "price"),
        "media": find("média", "media"),
        "livraison": find("livraison", "delivery"),
    }
    if idx["titre"] is None or idx["prix"] is None:
        raise SystemExit(
            "Colonnes inattendues dans l'export : " + ", ".join(header_row)
        )

    ncols = len(header_row)
    items: list[dict] = []
    for row in read_rows(text, ncols)[1:]:
        titre = cell(row, idx["titre"])
        if not titre:
            continue
        if len(row) != ncols:
            print(f"  ligne à {len(row)} colonnes au lieu de {ncols} : {titre[:40]}",
                  file=sys.stderr)

        pochette = cell(row, idx["pochette"])
        image = pochette.split("?", 1)[0]
        slug = ""
        if "?" in pochette:
            slug = pochette.split("?", 1)[1].split("&", 1)[0]

        m = ISBN_RE.search(pochette)
        isbn = m.group(1) if m else ""
        media = cell(row, idx["media"])

        if isbn and slug:
            section = "books" if "book" in media.lower() else "music"
            url = f"https://imusic.fr/{section}/{isbn}/{slug}"
        else:
            url = WISHLIST_URL

        prix_cell = find_price_cell(row, idx["prix"])
        items.append(
            {
                "id": isbn or slug or titre,
                "titre": titre,
                "artiste": cell(row, idx["artiste"]),
                "media": media,
                "image": image,
                "url": url,
                "prix": parse_price(prix_cell),
                "statut": parse_status(prix_cell),
                "livraison": cell(row, idx["livraison"]),
            }
        )
    return items


# --------------------------------------------------------------------------
# Historique
# --------------------------------------------------------------------------

def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    with HISTORY.open(newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("id")]


def money(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def derniere_ligne(history: list[dict]) -> dict[str, dict]:
    """Le dernier état connu de chaque article. L'historique est chronologique."""
    last: dict[str, dict] = {}
    for row in history:
        last[row["id"]] = row
    return last


def roster(history: list[dict]) -> set[str]:
    """Les articles que le dernier relevé voyait encore dans la wishlist.

    Un article sorti a une ligne « retiré » en dernier ; s'il revient, une ligne
    de prix repasse par-dessus. Cet ensemble est donc l'état courant de la
    wishlist selon l'historique, sans qu'il faille mémoriser quoi que ce soit
    ailleurs.
    """
    return {i for i, r in derniere_ligne(history).items()
            if r["statut"] != STATUT_RETIRE}


def export_suspect(items: list[dict], suivis: set[str]) -> bool:
    """Un export qui perd plus d'un tiers de la wishlist d'un coup.

    Le script refuse déjà d'écrire quand l'export revient vide. Un export
    *tronqué* passait, lui, et serait maintenant pris pour une salve de retraits
    — écrits dans l'historique, donc durables. Une wishlist ne perd pas un tiers
    de ses articles en une nuit ; un incident chez imusic, si.
    """
    return bool(suivis) and len(items) < len(suivis) * (1 - CHUTE_MAX_PCT / 100)


def append_changes(history: list[dict], items: list[dict], stamp: str):
    """N'écrit une ligne que si le prix ou la disponibilité a changé.

    Un article absent de l'export alors qu'il était suivi la veille compte comme
    un changement, lui aussi : il a quitté la wishlist.
    """
    last = derniere_ligne(history)

    nouvelles = []
    for item in items:
        prev = last.get(item["id"])
        prix = money(item["prix"])
        if prev is not None and prev["prix"] == prix and prev["statut"] == item["statut"]:
            continue
        nouvelles.append(
            {
                "date": stamp,
                "id": item["id"],
                "titre": item["titre"],
                "prix": prix,
                "statut": item["statut"],
            }
        )

    # Le retrait s'écrit sans prix : un article hors wishlist n'en a pas, et
    # c'est cette absence qui empêchera paliers() de prolonger le dernier prix
    # connu jusqu'au jour du retour.
    for ident in sorted(roster(history) - {item["id"] for item in items}):
        nouvelles.append(
            {
                "date": stamp,
                "id": ident,
                "titre": last[ident]["titre"],
                "prix": "",
                "statut": STATUT_RETIRE,
            }
        )

    if nouvelles:
        exists = HISTORY.exists()
        with HISTORY.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerows(nouvelles)

    return history + nouvelles, {r["id"] for r in nouvelles}


# --------------------------------------------------------------------------
# Calculs
# --------------------------------------------------------------------------

def observations(rows: list[dict]) -> list[tuple[str, float | None]]:
    """[(horodatage, prix ou None), ...] trié : tout ce qui a été constaté.

    Le prix vaut None quand l'article n'en affichait pas — rupture de stock, ou
    sortie de la wishlist. Garder ces relevés-là est ce qui permet de distinguer
    « le prix n'a pas bougé » de « il n'y avait pas de prix ».
    """
    return [(r["date"], float(r["prix"]) if r["prix"] else None)
            for r in sorted(rows, key=lambda r: r["date"])]


def build_series(obs: list[tuple]) -> list[list]:
    """[[horodatage, prix], ...] : les relevés qui avaient un prix.

    C'est ce que la page dessine. Les trous sont racontés par la piste des
    statuts, pas par la courbe.
    """
    return [[d, p] for d, p in obs if p is not None]


def window(series: list[list], jours: int, today: date) -> list[float]:
    """Prix observés sur la fenêtre, en tenant compte du prix déjà en vigueur.

    L'historique ne stocke que les changements : le prix est une courbe en
    escalier. Le prix qui s'appliquait au début de la fenêtre compte donc,
    même s'il a été relevé bien avant.
    """
    debut = (today - timedelta(days=jours)).isoformat()
    dedans = [p for d, p in series if d[:10] >= debut]
    avant = [p for d, p in series if d[:10] < debut]
    if avant:
        dedans.append(avant[-1])
    return dedans


def horodatage(iso: str) -> datetime:
    """Un horodatage de history.csv en datetime comparable.

    Les relevés portent leur décalage horaire (« +02:00 ») ; ceux d'un export
    plus ancien pourraient ne pas en avoir. On recolle le fuseau local dans ce
    cas, sinon les comparaisons lèvent une TypeError.
    """
    moment = datetime.fromisoformat(iso)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def paliers(obs: list[tuple], maintenant: datetime) -> list[tuple]:
    """La courbe en escalier découpée en paliers (début, fin, prix).

    Le dernier palier court jusqu'à maintenant : entre deux relevés — et depuis
    le dernier — le prix n'a pas bougé. C'est la même règle que window(), vue
    comme des durées plutôt que comme une fenêtre.

    Un relevé sans prix ne produit pas de palier, et coupe donc celui d'avant :
    pendant une rupture de stock ou une absence de la wishlist, l'article ne
    coûtait rien du tout. Compter ces semaines-là au dernier prix connu
    gonflerait le prix habituel et le temps passé moins cher avec des journées
    que personne n'a observées — et c'est le prix habituel qui décide du signal
    « à saisir ».
    """
    out = []
    for i, (d, prix) in enumerate(obs):
        if prix is None:
            continue
        debut = horodatage(d)
        fin = horodatage(obs[i + 1][0]) if i + 1 < len(obs) else maintenant
        if fin > debut:
            out.append((debut, fin, prix))
    return out


def duree(segments: list[tuple]) -> float:
    """Le temps total couvert par des paliers, en secondes.

    C'est la durée pendant laquelle l'article avait un prix — pas la durée du
    suivi, qui elle inclut les périodes sans prix.
    """
    return sum((fin - debut).total_seconds() for debut, fin, _ in segments)


def indicateurs(obs: list[tuple], prix: float | None,
                maintenant: datetime) -> dict:
    """Où se situe le prix du jour dans sa propre histoire.

    Strictement rétrospectif : aucun de ces chiffres ne prétend dire ce que le
    prix fera demain. Une série de prix ne contient pas les raisons qui les font
    bouger (stock, réimpression, promo éditeur), donc on décrit le passé et on
    laisse conclure.

    Tout est None tant que l'historique est trop court pour que ce soit honnête.
    """
    vide = {
        "jours_suivi": None, "jours_observes": None, "jours_moins_cher": None,
        "position_pct": None, "rythme_jours": None, "amplitude_moyenne": None,
        "mouvements": None,
    }
    tous = [p for _, p in obs if p is not None]
    if prix is None or len(tous) < MIN_RELEVES_INDICATEURS:
        return vide

    segments = paliers(obs, maintenant)
    if not segments:
        return vide

    jours_suivi = (maintenant - segments[0][0]).total_seconds() / 86400
    if jours_suivi < MIN_JOURS_INDICATEURS:
        return vide

    # Temps passé à un prix strictement inférieur à celui d'aujourd'hui, et
    # temps total pendant lequel l'article avait un prix. Les deux se comptent
    # sur les mêmes paliers : une période sans prix n'entre ni dans l'un ni dans
    # l'autre, alors qu'elle compte dans la durée de suivi.
    moins_cher = sum(
        (fin - debut).total_seconds() for debut, fin, p in segments
        if p < prix - 0.001
    ) / 86400
    observes = duree(segments) / 86400

    # Un relevé peut n'enregistrer qu'un changement de disponibilité : seuls les
    # écarts de prix réels comptent comme mouvement.
    ecarts = [
        abs(tous[i] - tous[i - 1])
        for i in range(1, len(tous))
        if abs(tous[i] - tous[i - 1]) > 0.001
    ]

    bas, haut = min(tous), max(tous)

    return {
        "jours_suivi": round(jours_suivi),
        "jours_observes": round(observes),
        "jours_moins_cher": round(moins_cher),
        "position_pct": round((prix - bas) / (haut - bas) * 100) if haut - bas > 0.001 else 0,
        "rythme_jours": round(jours_suivi / len(ecarts)) if ecarts else None,
        "amplitude_moyenne": round(sum(ecarts) / len(ecarts), 2) if ecarts else None,
        "mouvements": len(ecarts),
    }


def prix_habituel(segments: list[tuple]) -> float | None:
    """Le prix pratiqué la plupart du temps : la médiane pondérée par la durée.

    Pas une moyenne : un prix tenu six mois doit peser plus qu'une promotion de
    trois jours, et une moyenne se laisse tirer par une valeur extrême. On range
    les paliers du moins cher au plus cher et on prend celui qui contient la
    moitié du temps écoulé.
    """
    total = duree(segments)
    if total <= 0:
        return None
    cumul = 0.0
    for debut, fin, prix in sorted(segments, key=lambda s: s[2]):
        cumul += (fin - debut).total_seconds()
        if cumul >= total / 2:
            return round(prix, 2)
    return round(segments[-1][2], 2)


def affaire(obs: list[tuple], prix: float | None, statut: str,
            indics: dict, maintenant: datetime) -> dict | None:
    """Ce prix-là est-il une bonne affaire ?

    Le verdict ne regarde que l'écart avec le prix habituel, en pourcentage et
    en euros. La part du temps passé moins cher est calculée aussi, mais elle
    n'éteint rien : un prix nettement bas reste un prix nettement bas, même si
    l'article y redescend souvent. Elle nuance, elle ne décide pas — c'est au
    lecteur de trancher, avec les deux informations sous les yeux.

    Constat, pas prédiction : on ne dit jamais que le prix va remonter.

    None tant que l'historique est trop court : ce sont les mêmes seuils que
    les indicateurs de la fiche, on se repose sur leur verdict.
    """
    if prix is None or indics.get("jours_suivi") is None:
        return None

    segments = paliers(obs, maintenant)
    habituel = prix_habituel(segments)
    if habituel is None:
        return None

    ecart = round(habituel - prix, 2)
    ecart_pct = round(ecart / habituel * 100, 1)

    # Sur la durée où l'article avait un prix, et pas sur la durée du suivi :
    # c'est le total qui sert à la médiane. Rapportée à autre chose, la part
    # pourrait dépasser les ~50 % qu'une médiane interdit, et la nuance de la
    # page ne voudrait plus dire ce qu'elle dit.
    part = round(
        sum((fin - debut).total_seconds() for debut, fin, p in segments
            if p < prix - 0.001) / duree(segments) * 100,
        1,
    )

    return {
        "prix_habituel": habituel,
        "ecart": ecart,
        "ecart_pct": ecart_pct,
        "part_moins_cher_pct": part,
        # La page ne connaît pas le seuil : elle lit ce booléen pour savoir s'il
        # y a une nuance à dire.
        "deja_moins_cher": part >= SEUIL_NUANCE_PCT,
        # Un article indisponible ne se saisit pas, quel que soit son prix
        # affiché. Une précommande, si : elle s'achète.
        "saisir": bool(
            statut != "indisponible"
            and ecart_pct >= SEUIL_ECART_PCT
            and ecart >= SEUIL_ECART_EUROS
        ),
    }


def minimums_mensuels(obs: list[tuple], maintenant: datetime,
                      mois: int = 12) -> list[list] | None:
    """Le prix le plus bas *en vigueur* chaque mois, sur les douze derniers.

    En vigueur, pas relevé : un palier qui traverse trois mois compte dans les
    trois. Sans ça, un mois sans relevé paraîtrait sans prix.

    None tant qu'il n'y a pas assez de mois pour qu'un motif saisonnier soit
    lisible — afficher deux barres ne renseigne sur rien.
    """
    segments = paliers(obs, maintenant)
    if not segments:
        return None

    par_mois: dict[str, float] = {}
    for debut, fin, prix in segments:
        curseur = debut.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while curseur <= fin:
            cle = curseur.strftime("%Y-%m")
            par_mois[cle] = min(par_mois.get(cle, prix), prix)
            # Passer au premier du mois suivant.
            curseur = (curseur.replace(day=28) + timedelta(days=4)).replace(day=1)

    # Les clés des `mois` derniers mois, celui en cours compris. Compté en mois
    # et pas en jours : « aujourd'hui moins 365 » retombe à cheval sur un mois
    # de plus ou de moins selon la longueur des mois traversés.
    rang = maintenant.year * 12 + maintenant.month - 1
    cles = [f"{(rang - k) // 12:04d}-{(rang - k) % 12 + 1:02d}"
            for k in range(mois - 1, -1, -1)]
    retenus = [(c, par_mois[c]) for c in cles if c in par_mois]
    if len(retenus) < MIN_MOIS_SAISON:
        return None
    return [[cle, round(valeur, 2)] for cle, valeur in retenus]


def build_statuts(rows: list[dict]) -> list[list]:
    """[[horodatage, statut], ...] : les changements de disponibilité.

    build_series() laisse tomber les relevés sans prix. Sans cette piste, une
    période d'indisponibilité ressemblerait à un palier plat sur la courbe de la
    fiche, alors qu'il n'y avait tout simplement plus de prix.
    """
    out: list[list] = []
    for row in sorted(rows, key=lambda r: r["date"]):
        statut = row.get("statut") or "inconnu"
        if not out or out[-1][1] != statut:
            out.append([row["date"], statut])
    return out


def build_journal(rows: list[dict]) -> list[dict]:
    """Le détail de chaque mouvement, du plus récent au plus ancien.

    Les écarts sont calculés ici, comme le reste : la page affiche, elle ne
    calcule pas. Chaque ligne de history.csv est un changement — de prix, de
    disponibilité, ou les deux —, donc chaque ligne mérite son entrée.
    """
    ordonne = sorted(rows, key=lambda r: r["date"])
    journal = []
    precedent: float | None = None
    for row in ordonne:
        prix = float(row["prix"]) if row["prix"] else None
        variation = None
        variation_pct = None
        if prix is not None and precedent is not None and abs(prix - precedent) > 0.001:
            variation = round(prix - precedent, 2)
            variation_pct = round(variation / precedent * 100, 1)
        journal.append({
            "date": row["date"],
            "prix": prix,
            "precedent": precedent,
            "variation": variation,
            "variation_pct": variation_pct,
            "statut": row.get("statut") or "inconnu",
        })
        if prix is not None:
            precedent = prix
    journal.reverse()
    return journal


def analyse(items: list[dict], history: list[dict], today: date,
            maintenant: datetime) -> list[dict]:
    par_id: dict[str, list[dict]] = {}
    for row in history:
        par_id.setdefault(row["id"], []).append(row)

    sortie = []
    for item in items:
        rows = par_id.get(item["id"], [])
        obs = observations(rows)
        series = build_series(obs)
        prix = item["prix"]

        variation = None
        variation_pct = None
        depuis = None
        if prix is not None and len(series) >= 2:
            precedent = None
            for d, p in reversed(series[:-1]):
                if abs(p - prix) > 0.001:
                    precedent = (d, p)
                    break
            if precedent:
                variation = round(prix - precedent[1], 2)
                variation_pct = round(variation / precedent[1] * 100, 1)
                depuis = series[-1][0]

        fenetre = window(series, WINDOW_DAYS, today) if series else []
        tous = [p for _, p in series]

        indics = indicateurs(obs, prix, maintenant)
        bonne_affaire = affaire(obs, prix, item["statut"], indics, maintenant)

        sortie.append(
            {
                **{k: item[k] for k in
                   ("id", "titre", "artiste", "media", "image", "url", "statut", "livraison")},
                "prix": prix,
                "variation": variation,
                "variation_pct": variation_pct,
                "variation_depuis": depuis,
                "min_12m": round(min(fenetre), 2) if fenetre else None,
                "max_12m": round(max(fenetre), 2) if fenetre else None,
                "min_total": round(min(tous), 2) if tous else None,
                "max_total": round(max(tous), 2) if tous else None,
                "plus_bas_12m": bool(fenetre and prix is not None and prix <= min(fenetre) + 0.001),
                "plus_bas_total": bool(tous and prix is not None and prix <= min(tous) + 0.001),
                "suivi_depuis": series[0][0][:10] if series else None,
                "releves": len(series),
                "series": series,
                "a_saisir": bool(bonne_affaire and bonne_affaire["saisir"]),
                # À partir d'ici, ce que lit la fiche d'un article et pas le listing.
                "statuts": build_statuts(rows),
                "journal": build_journal(rows),
                "indicateurs": indics,
                "affaire": bonne_affaire,
                "min_mensuel": minimums_mensuels(obs, maintenant) if series else None,
            }
        )
    return sortie


# --------------------------------------------------------------------------

def main() -> int:
    if not WISHLIST_URL:
        print("Variable d'environnement WISHLIST_URL absente.", file=sys.stderr)
        return 1

    maintenant = datetime.now().astimezone()
    today = maintenant.date()
    stamp = maintenant.isoformat(timespec="minutes")

    items = parse_export(download(WISHLIST_URL))
    if not items:
        print("L'export ne contient aucun article : on ne touche à rien.", file=sys.stderr)
        return 1

    history = load_history()

    # Un export amputé serait lu comme une salve de retraits, qui s'écriraient
    # dans l'historique et couperaient les paliers. On préfère sauter un relevé.
    suivis = roster(history)
    if export_suspect(items, suivis) and not FORCER:
        print(f"Export suspect : {len(items)} articles là où le dernier relevé "
              f"en suivait {len(suivis)}. On ne touche à rien.\n"
              f"Si la wishlist a vraiment fondu, relancez avec FORCER_RELEVE=1.",
              file=sys.stderr)
        return 1

    connus = {row["id"] for row in history}
    history, bouges = append_changes(history, items, stamp)
    partis = sorted(suivis - {item["id"] for item in items})

    analyses = analyse(items, history, today, maintenant)
    nouveaux = [a for a in analyses if a["id"] not in connus]
    changes = [a for a in analyses
               if a["id"] in bouges and a["id"] in connus and a["variation"] is not None]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "articles": sorted(analyses, key=lambda a: (a["prix"] is None, a["prix"] or 0)),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print(f"{len(items)} articles relevés, {len(nouveaux)} nouveaux, "
          f"{len(changes)} changements de prix, {len(partis)} retirés.")
    for a in changes:
        signe = "+" if a["variation"] > 0 else ""
        print(f"  {signe}{a['variation']:.2f} €  {a['titre'][:60]}  → {a['prix']:.2f} €")
    for ident in partis:
        print(f"  retiré de la wishlist : {ident}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
