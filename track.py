#!/usr/bin/env python3
"""Relève les prix d'une wishlist imusic et tient leur historique.

Une exécution =
  1. télécharge l'export CSV de la wishlist,
  2. ajoute une ligne dans history.csv pour chaque prix qui a bougé,
  3. régénère public/data.json que lit la page web.

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
OUTPUT = ROOT / "public" / "data.json"

WISHLIST_URL = os.environ.get("WISHLIST_URL", "").strip()

HISTORY_FIELDS = ["date", "id", "titre", "prix", "statut"]

# « € 63.99 », « 1 234,50 »… on ne garde que le nombre.
PRICE_RE = re.compile(r"(\d+(?:[ \u00a0]\d{3})*[.,]\d{2})")
# L'ISBN/EAN est dans le nom du fichier de la pochette : .../9781401294052.jpg
ISBN_RE = re.compile(r"/(\d{8,14})\.(?:jpe?g|png|webp)", re.IGNORECASE)

WINDOW_DAYS = 365

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


def append_changes(history: list[dict], items: list[dict], stamp: str):
    """N'écrit une ligne que si le prix ou la disponibilité a changé."""
    last: dict[str, dict] = {}
    for row in history:
        last[row["id"]] = row

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

def build_series(rows: list[dict]) -> list[list]:
    """[[horodatage, prix], ...] trié. Une entrée = un changement observé."""
    series = []
    for row in sorted(rows, key=lambda r: r["date"]):
        if row["prix"]:
            series.append([row["date"], float(row["prix"])])
    return series


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


def analyse(items: list[dict], history: list[dict], today: date) -> list[dict]:
    par_id: dict[str, list[dict]] = {}
    for row in history:
        par_id.setdefault(row["id"], []).append(row)

    sortie = []
    for item in items:
        series = build_series(par_id.get(item["id"], []))
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
                "plus_bas_12m": bool(fenetre and prix is not None and prix <= min(fenetre) + 0.001),
                "plus_bas_total": bool(tous and prix is not None and prix <= min(tous) + 0.001),
                "suivi_depuis": series[0][0][:10] if series else None,
                "releves": len(series),
                "series": series,
            }
        )
    return sortie


# --------------------------------------------------------------------------

def main() -> int:
    if not WISHLIST_URL:
        print("Variable d'environnement WISHLIST_URL absente.", file=sys.stderr)
        return 1

    today = date.today()
    stamp = datetime.now().astimezone().isoformat(timespec="minutes")

    items = parse_export(download(WISHLIST_URL))
    if not items:
        print("L'export ne contient aucun article : on ne touche à rien.", file=sys.stderr)
        return 1

    history = load_history()
    connus = {row["id"] for row in history}
    history, bouges = append_changes(history, items, stamp)

    analyses = analyse(items, history, today)
    nouveaux = [a for a in analyses if a["id"] not in connus]
    changes = [a for a in analyses
               if a["id"] in bouges and a["id"] in connus and a["variation"] is not None]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "wishlist_url": WISHLIST_URL,
                "articles": sorted(analyses, key=lambda a: (a["prix"] is None, a["prix"] or 0)),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print(f"{len(items)} articles relevés, {len(nouveaux)} nouveaux, "
          f"{len(changes)} changements de prix.")
    for a in changes:
        signe = "+" if a["variation"] > 0 else ""
        print(f"  {signe}{a['variation']:.2f} €  {a['titre'][:60]}  → {a['prix']:.2f} €")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
