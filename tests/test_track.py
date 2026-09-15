"""Les pièges de track.py, transformés en filet.

Chaque test correspond à un paragraphe de CLAUDE.md : les entités HTML qui
cassent le découpage, les retours à la ligne de la colonne Prix, la courbe en
escalier, la médiane pondérée par la durée. Ce sont les endroits où une
simplification bien intentionnée casserait quelque chose sans rien afficher
d'anormal.

    python3 -m unittest discover tests

Bibliothèque standard uniquement, comme le reste du projet.
"""

import contextlib
import csv
import io
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import track  # noqa: E402

EXPORTS = Path(__file__).resolve().parent / "exports"

# Une date fixe : les indicateurs comptent le temps écoulé jusqu'à « maintenant »,
# et un test qui dépend de l'heure qu'il est ne prouve rien deux mois plus tard.
MAINTENANT = datetime.fromisoformat("2026-09-14T12:00+02:00")
TODAY = MAINTENANT.date()


def taire_stderr(test):
    """signaler() double ses messages sur stderr : précieux dans le journal
    d'Actions, bruyant au milieu d'une suite de tests."""
    silence = contextlib.redirect_stderr(io.StringIO())
    silence.__enter__()
    test.addCleanup(silence.__exit__, None, None, None)


def journal(*releves, ident="X", titre="Un titre"):
    """Des lignes d'historique à partir de (date, prix, statut).

    Le prix est une chaîne comme dans le CSV : vide quand il n'y en avait pas.
    """
    return [{"date": d, "id": ident, "titre": titre, "prix": p, "statut": s}
            for d, p, s in releves]


# --------------------------------------------------------------------------
# Lecture de l'export
# --------------------------------------------------------------------------

class TestExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = track.parse_export(
            (EXPORTS / "wishlist.csv").read_text(encoding="utf-8"))
        cls.par_titre = {i["titre"]: i for i in cls.items}

    def test_quatre_articles(self):
        self.assertEqual(len(self.items), 4)

    def test_les_entites_ne_decalent_pas_les_colonnes(self):
        """Le « ; » de « &amp; » passerait pour un séparateur : tout glisserait
        d'une colonne et le titre deviendrait un bout d'URL."""
        titres = [i["titre"] for i in self.items]
        self.assertIn("Batman: The Killing Joke Deluxe", titres)
        for item in self.items:
            self.assertNotIn("format=jpg", item["titre"])
            self.assertNotIn("&amp;", item["image"])

    def test_les_faux_amis_restent_intacts(self):
        """html.unescape sur tout le texte traduirait « &not » en « ¬ »."""
        self.assertEqual(
            track.ENTITY_RE.sub(lambda m: __import__("html").unescape(m.group(0)),
                                "Bill &not Ted &amp; Co"),
            "Bill &not Ted & Co",
        )

    def test_prix_eclate_sur_deux_lignes(self):
        """« € 13.49 \\n Acheter » non échappé : read_rows recolle les morceaux."""
        item = self.par_titre["Batman: The Killing Joke Deluxe"]
        self.assertEqual(item["prix"], 13.49)
        self.assertEqual(item["statut"], "disponible")

    def test_prix_multiligne_echappe_et_separateur_de_milliers(self):
        item = self.par_titre["The Many Deaths of Laila Starr Deluxe Edition"]
        self.assertEqual(item["prix"], 1234.50)
        self.assertEqual(item["statut"], "disponible")

    def test_indisponible_na_pas_de_prix(self):
        item = self.par_titre["All Star Superman: The Deluxe Edition"]
        self.assertIsNone(item["prix"])
        self.assertEqual(item["statut"], "indisponible")

    def test_precommande(self):
        item = self.par_titre["Kingdom Come: 30th Anniversary Deluxe Edition"]
        self.assertEqual(item["statut"], "precommande")
        self.assertEqual(item["prix"], 33.49)

    def test_identifiant_lu_dans_le_nom_de_la_pochette(self):
        """L'export ne contient pas d'identifiant produit : c'est l'ISBN du nom
        de fichier qui permet à un article re-ajouté de retrouver son passé."""
        item = self.par_titre["Batman: The Killing Joke Deluxe"]
        self.assertEqual(item["id"], "9781401294052")
        self.assertEqual(
            item["url"],
            "https://imusic.fr/books/9781401294052/batman-the-killing-joke-deluxe",
        )

    def test_section_music_pour_un_media_non_livresque(self):
        item = self.par_titre["Kingdom Come: 30th Anniversary Deluxe Edition"]
        self.assertTrue(item["url"].startswith("https://imusic.fr/music/"))

    def test_colonnes_inattendues(self):
        with self.assertRaises(SystemExit):
            track.parse_export("Titre;Éditeur\nUn livre;Panini\n")


class TestPrixEtStatut(unittest.TestCase):
    def test_parse_price(self):
        self.assertEqual(track.parse_price("€ 13.49"), 13.49)
        self.assertEqual(track.parse_price("€ 1 234,50"), 1234.50)
        self.assertEqual(track.parse_price("1 234,50 €"), 1234.50)
        self.assertIsNone(track.parse_price("Indisponible"))

    def test_parse_status(self):
        self.assertEqual(track.parse_status("€ 13.49\nAcheter"), "disponible")
        self.assertEqual(track.parse_status("Indisponible"), "indisponible")
        self.assertEqual(track.parse_status("Pré-commande"), "precommande")
        self.assertEqual(track.parse_status(""), "inconnu")


# --------------------------------------------------------------------------
# L'historique : ce qu'on écrit et ce qu'on refuse d'écrire
# --------------------------------------------------------------------------

class TestHistorique(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fichier = Path(self.tmp.name) / "history.csv"
        precedent = track.HISTORY
        track.HISTORY = self.fichier
        self.addCleanup(setattr, track, "HISTORY", precedent)

    def ecrites(self):
        with self.fichier.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_rien_a_ecrire_quand_rien_ne_bouge(self):
        histo = journal(("2026-09-01T08:00+02:00", "13.49", "disponible"))
        _, bouges = track.append_changes(
            histo, [{"id": "X", "titre": "Un titre", "prix": 13.49,
                     "statut": "disponible"}], "2026-09-14T08:00+02:00")
        self.assertEqual(bouges, set())
        self.assertFalse(self.fichier.exists())

    def test_un_article_absent_de_lexport_est_ecrit_comme_retire(self):
        histo = journal(("2026-09-01T08:00+02:00", "13.49", "disponible"))
        track.append_changes(histo, [], "2026-09-14T08:00+02:00")
        lignes = self.ecrites()
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["statut"], track.STATUT_RETIRE)
        self.assertEqual(lignes[0]["prix"], "")
        self.assertEqual(lignes[0]["titre"], "Un titre")

    def test_un_retrait_ne_sannonce_quune_fois(self):
        histo = journal(("2026-09-01T08:00+02:00", "13.49", "disponible"),
                        ("2026-09-02T08:00+02:00", "", track.STATUT_RETIRE))
        track.append_changes(histo, [], "2026-09-14T08:00+02:00")
        self.assertFalse(self.fichier.exists())

    def test_le_retour_dun_article_au_meme_prix_sinscrit(self):
        """Sans la ligne de retour, l'historique prétendrait que le prix a été
        observé pendant toute l'absence."""
        histo = journal(("2026-01-01T08:00+02:00", "13.49", "disponible"),
                        ("2026-02-01T08:00+02:00", "", track.STATUT_RETIRE))
        track.append_changes(
            histo, [{"id": "X", "titre": "Un titre", "prix": 13.49,
                     "statut": "disponible"}], "2026-09-14T08:00+02:00")
        lignes = self.ecrites()
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["prix"], "13.49")

    def test_roster(self):
        histo = journal(("2026-01-01T08:00+02:00", "13.49", "disponible")) \
            + journal(("2026-02-01T08:00+02:00", "", track.STATUT_RETIRE), ident="Y")
        self.assertEqual(track.roster(histo), {"X"})

    def test_export_tronque_refuse(self):
        suivis = {str(n) for n in range(27)}
        self.assertTrue(track.export_suspect([{"id": "1"}] * 5, suivis))
        # Un tiers pile (27 → 18) est encore refusé, la marge est à 33 %.
        self.assertTrue(track.export_suspect([{"id": "1"}] * 18, suivis))
        self.assertFalse(track.export_suspect([{"id": "1"}] * 19, suivis))
        self.assertFalse(track.export_suspect([{"id": "1"}] * 26, suivis))
        # Premier relevé : il n'y a rien à comparer, on laisse passer.
        self.assertFalse(track.export_suspect([], set()))


# --------------------------------------------------------------------------
# La courbe en escalier
# --------------------------------------------------------------------------

class TestEscalier(unittest.TestCase):
    def test_window_inclut_le_prix_en_vigueur_avant_la_fenetre(self):
        """Un prix stable depuis deux ans sortirait sinon de la fenêtre douze
        mois, et l'article n'aurait plus ni minimum ni maximum."""
        series = [["2024-01-01T08:00+02:00", 20.0]]
        self.assertEqual(track.window(series, 365, TODAY), [20.0])

    def test_window_garde_le_dernier_prix_anterieur_seulement(self):
        series = [["2020-01-01T08:00+02:00", 30.0],
                  ["2024-01-01T08:00+02:00", 20.0],
                  ["2026-09-01T08:00+02:00", 10.0]]
        self.assertEqual(sorted(track.window(series, 365, TODAY)), [10.0, 20.0])

    def test_un_releve_sans_prix_coupe_le_palier(self):
        """Une rupture de stock n'est pas un palier plat : pendant ces
        semaines-là, l'article ne coûtait rien du tout."""
        obs = track.observations(journal(
            ("2026-01-01T08:00+02:00", "20.00", "disponible"),
            ("2026-02-01T08:00+02:00", "", "indisponible"),
            ("2026-08-01T08:00+02:00", "10.00", "disponible"),
        ))
        segments = track.paliers(obs, MAINTENANT)
        self.assertEqual([p for _, _, p in segments], [20.0, 10.0])
        self.assertEqual(segments[0][1], track.horodatage("2026-02-01T08:00+02:00"))
        self.assertEqual(segments[1][1], MAINTENANT)

    def test_la_serie_dessinee_ignore_les_relevés_sans_prix(self):
        obs = track.observations(journal(
            ("2026-01-01T08:00+02:00", "20.00", "disponible"),
            ("2026-02-01T08:00+02:00", "", "indisponible"),
        ))
        self.assertEqual(track.build_series(obs),
                         [["2026-01-01T08:00+02:00", 20.0]])


# --------------------------------------------------------------------------
# Le prix habituel et le signal « à saisir »
# --------------------------------------------------------------------------

class TestPrixHabituel(unittest.TestCase):
    def paliers(self, *releves):
        return track.paliers(track.observations(journal(*releves)), MAINTENANT)

    def test_mediane_ponderee_et_non_moyenne(self):
        """Une promotion de trois jours ne doit pas tirer le prix habituel."""
        segments = self.paliers(
            ("2026-01-01T08:00+02:00", "20.00", "disponible"),
            ("2026-09-10T08:00+02:00", "2.00", "disponible"),
            ("2026-09-13T08:00+02:00", "20.00", "disponible"),
        )
        self.assertEqual(track.prix_habituel(segments), 20.0)

    def test_un_prix_bas_qui_sinstalle_devient_le_prix_habituel(self):
        """C'est ce qui éteint le signal tout seul, sans règle supplémentaire."""
        segments = self.paliers(
            ("2026-01-01T08:00+02:00", "20.00", "disponible"),
            ("2026-03-01T08:00+02:00", "10.00", "disponible"),
        )
        self.assertEqual(track.prix_habituel(segments), 10.0)

    def test_les_semaines_sans_prix_ne_comptent_pas_dans_la_mediane(self):
        """Sans la coupure, les six mois d'indisponibilité pèseraient au dernier
        prix connu : l'article paraîtrait coûter 20 € d'habitude et son prix du
        jour passerait pour une affaire à moitié prix."""
        segments = self.paliers(
            ("2026-01-01T08:00+02:00", "20.00", "disponible"),
            ("2026-02-01T08:00+02:00", "", "indisponible"),
            ("2026-08-01T08:00+02:00", "10.00", "disponible"),
        )
        self.assertEqual(track.prix_habituel(segments), 10.0)


class TestAffaire(unittest.TestCase):
    def juger(self, releves, prix, statut="disponible"):
        obs = track.observations(journal(*releves))
        indics = track.indicateurs(obs, prix, MAINTENANT)
        return indics, track.affaire(obs, prix, statut, indics, MAINTENANT)

    HABITUEL_20 = [
        ("2026-01-01T08:00+02:00", "20.00", "disponible"),
        ("2026-03-01T08:00+02:00", "21.00", "disponible"),
        ("2026-05-01T08:00+02:00", "20.00", "disponible"),
        ("2026-09-01T08:00+02:00", "17.00", "disponible"),
    ]

    def test_signal_allume(self):
        _, f = self.juger(self.HABITUEL_20, 17.0)
        self.assertEqual(f["prix_habituel"], 20.0)
        self.assertEqual(f["ecart"], 3.0)
        self.assertEqual(f["ecart_pct"], 15.0)
        self.assertTrue(f["saisir"])

    def test_le_plancher_en_euros(self):
        """10 % sur un livre à 6 €, ça ne se saisit pas."""
        releves = [(d, str(round(float(p) / 4, 2)), s) for d, p, s in self.HABITUEL_20]
        _, f = self.juger(releves, 4.25)
        self.assertEqual(f["ecart_pct"], 15.0)
        self.assertLess(f["ecart"], track.SEUIL_ECART_EUROS)
        self.assertFalse(f["saisir"])

    def test_un_indisponible_ne_se_saisit_pas(self):
        _, f = self.juger(self.HABITUEL_20, 17.0, statut="indisponible")
        self.assertFalse(f["saisir"])

    def test_une_precommande_se_saisit(self):
        _, f = self.juger(self.HABITUEL_20, 17.0, statut="precommande")
        self.assertTrue(f["saisir"])

    def test_la_rarete_nuance_mais_ne_bloque_pas(self):
        """Un article qui plonge à 12 € deux fois dans l'année : à 15 €, la
        baisse est réelle et le signal s'allume. Elle est seulement dite."""
        releves = [
            ("2026-01-01T08:00+02:00", "20.00", "disponible"),
            ("2026-03-01T08:00+02:00", "12.00", "disponible"),
            ("2026-04-01T08:00+02:00", "20.00", "disponible"),
            ("2026-08-01T08:00+02:00", "12.00", "disponible"),
            ("2026-09-01T08:00+02:00", "15.00", "disponible"),
        ]
        _, f = self.juger(releves, 15.0)
        self.assertTrue(f["saisir"])
        self.assertTrue(f["deja_moins_cher"])
        self.assertGreaterEqual(f["part_moins_cher_pct"], track.SEUIL_NUANCE_PCT)

    def test_la_part_du_temps_moins_cher_ne_depasse_pas_la_moitie(self):
        """C'est la définition de la médiane, et c'est ce qui rend un seuil de
        nuance au-delà de 45 % inatteignable. Y compris quand une rupture de
        stock retire des semaines du décompte : la part se rapporte au temps
        pendant lequel l'article avait un prix, celui-là même qui sert à la
        médiane."""
        for releves in (self.HABITUEL_20, TestIndicateurs.RELEVES):
            _, f = self.juger(releves, float(releves[-1][1]))
            self.assertLessEqual(f["part_moins_cher_pct"], 50.0)

    def test_pas_de_verdict_sans_historique(self):
        releves = [("2026-09-13T08:00+02:00", "20.00", "disponible")]
        indics, f = self.juger(releves, 20.0)
        self.assertIsNone(f)
        self.assertIsNone(indics["position_pct"])

    def test_pas_de_verdict_sur_quelques_jours(self):
        """Quatre relevés en trois jours : le compte y est, la durée non."""
        releves = [(f"2026-09-1{n}T08:00+02:00", str(20 - n), "disponible")
                   for n in range(4)]
        indics, f = self.juger(releves, 17.0)
        self.assertIsNone(f)
        self.assertIsNone(indics["jours_suivi"])


class TestIndicateurs(unittest.TestCase):
    # 18 € du 1er janvier au 1er février, 20 € jusqu'au 1er mars, plus de prix
    # du tout jusqu'au 1er août, 10 € jusqu'au 1er septembre, 12 € depuis.
    RELEVES = [
        ("2026-01-01T08:00+02:00", "18.00", "disponible"),
        ("2026-02-01T08:00+02:00", "20.00", "disponible"),
        ("2026-03-01T08:00+02:00", "", "indisponible"),
        ("2026-08-01T08:00+02:00", "10.00", "disponible"),
        ("2026-09-01T08:00+02:00", "12.00", "disponible"),
    ]

    def indics(self, prix=12.0):
        return track.indicateurs(
            track.observations(journal(*self.RELEVES)), prix, MAINTENANT)

    def test_la_duree_de_suivi_court_depuis_le_premier_releve(self):
        self.assertEqual(self.indics()["jours_suivi"], 256)

    def test_la_duree_observee_exclut_les_periodes_sans_prix(self):
        """31 + 28 + 31 + 13 jours avec un prix : les cinq mois de rupture n'y
        sont pas, alors qu'ils comptent dans la durée de suivi."""
        self.assertEqual(self.indics()["jours_observes"], 103)

    def test_temps_passe_moins_cher(self):
        self.assertEqual(self.indics()["jours_moins_cher"], 31)

    def test_position_dans_la_fourchette(self):
        self.assertEqual(self.indics()["position_pct"], 20)

    def test_les_mouvements_ne_comptent_que_les_ecarts_de_prix(self):
        """Le passage en indisponible n'est pas un mouvement de prix : c'est le
        retour à 10 € qui en est un, et il ne compte qu'une fois."""
        self.assertEqual(self.indics()["mouvements"], 3)
        self.assertEqual(self.indics()["amplitude_moyenne"], 4.67)


class TestSaison(unittest.TestCase):
    def test_un_palier_compte_dans_tous_les_mois_quil_traverse(self):
        obs = track.observations(journal(
            ("2026-01-15T08:00+02:00", "20.00", "disponible"),
            ("2026-04-10T08:00+02:00", "15.00", "disponible"),
        ))
        mois = dict(track.minimums_mensuels(obs, MAINTENANT))
        self.assertEqual(mois["2026-02"], 20.0)   # aucun relevé ce mois-là
        self.assertEqual(mois["2026-04"], 15.0)   # le mois du changement garde le plus bas
        self.assertEqual(mois["2026-09"], 15.0)

    def test_silence_en_dessous_du_seuil(self):
        obs = track.observations(journal(
            ("2026-08-01T08:00+02:00", "20.00", "disponible"),
        ))
        self.assertIsNone(track.minimums_mensuels(obs, MAINTENANT))


# --------------------------------------------------------------------------
# Ce que lit la page
# --------------------------------------------------------------------------

class TestSortie(unittest.TestCase):
    HISTO = journal(
        ("2026-01-01T08:00+02:00", "20.00", "disponible"),
        ("2026-02-01T08:00+02:00", "", "indisponible"),
        ("2026-08-01T08:00+02:00", "10.00", "disponible"),
    )

    def test_les_statuts_sont_une_piste_a_part(self):
        self.assertEqual(
            track.build_statuts(self.HISTO),
            [["2026-01-01T08:00+02:00", "disponible"],
             ["2026-02-01T08:00+02:00", "indisponible"],
             ["2026-08-01T08:00+02:00", "disponible"]],
        )

    def test_le_journal_est_du_plus_recent_au_plus_ancien(self):
        j = track.build_journal(self.HISTO)
        self.assertEqual([l["date"] for l in j],
                         [r["date"] for r in reversed(self.HISTO)])
        self.assertEqual(j[0]["variation"], -10.0)
        self.assertIsNone(j[1]["prix"])
        self.assertIsNone(j[1]["variation"])

    def test_analyse_dun_export_complet(self):
        items = track.parse_export(
            (EXPORTS / "wishlist.csv").read_text(encoding="utf-8"))
        histo = []
        for item in items:
            histo += journal(("2026-09-01T08:00+02:00",
                              track.money(item["prix"]), item["statut"]),
                             ident=item["id"], titre=item["titre"])
        sortie = track.analyse(items, histo, TODAY, MAINTENANT)
        self.assertEqual(len(sortie), 4)
        for a in sortie:
            self.assertIn("series", a)
            self.assertIn("journal", a)
            self.assertIsNone(a["affaire"])          # un seul relevé : on se tait
            self.assertFalse(a["a_saisir"])


# --------------------------------------------------------------------------
# Les alertes
# --------------------------------------------------------------------------

EN_TETE = "Pochette;Artiste;Titre;Prix;Média;Livraison\n"
LIGNE_OK = "https://img/9781401294052.jpg?v=1;Alan Moore;Killing Joke;€ 13.49 Acheter;Hardcover;24h\n"


class TestAlertes(unittest.TestCase):
    """Ce que le relevé constate doit atterrir sur la page.

    Un incident qui ne se voit que dans les logs d'Actions n'est pas signalé :
    le suivi continue d'afficher ses prix avec l'aplomb de ceux du jour.
    """

    def setUp(self):
        track.ANOMALIES.clear()
        taire_stderr(self)

    def test_un_export_sain_ne_signale_rien(self):
        """La bannière ne doit s'allumer que quand il y a vraiment quelque
        chose : une alerte qu'on apprend à ignorer ne sert plus à rien."""
        track.parse_export((EXPORTS / "wishlist.csv").read_text(encoding="utf-8"))
        self.assertEqual(track.ANOMALIES, [])

    def test_ligne_mal_decoupee(self):
        track.parse_export(EN_TETE + LIGNE_OK
                           + "https://img/9781632159038.jpg?v=2;Vaughan;Saga;"
                             "€ 39.49 Acheter;Hardcover;24h;en trop\n")
        self.assertEqual(len(track.ANOMALIES), 1)
        self.assertIn("mal découpée", track.ANOMALIES[0]["message"])
        self.assertEqual(track.ANOMALIES[0]["niveau"], "avertissement")

    def test_article_sans_isbn(self):
        """Sans ISBN la clé retombe sur le slug ou le titre : l'article perd
        son historique sans que rien ne le dise."""
        track.parse_export(EN_TETE + LIGNE_OK
                           + "https://img/maison.jpg?v=2;X;Sans ISBN;€ 9.99 Acheter;Hardcover;24h\n")
        self.assertEqual(len(track.ANOMALIES), 1)
        self.assertIn("sans ISBN", track.ANOMALIES[0]["message"])
        self.assertIn("Sans ISBN", track.ANOMALIES[0]["message"])

    def test_prix_illisible(self):
        """Ni prix, ni « indisponible », ni « pré-commande » : la colonne n'a pas
        été comprise. Le relevé sans prix couperait le palier à tort."""
        track.parse_export(EN_TETE + LIGNE_OK
                           + "https://img/9781632159038.jpg?v=2;X;Saga;Nous consulter;Hardcover;24h\n")
        self.assertEqual(len(track.ANOMALIES), 1)
        self.assertIn("prix n'a pas pu être lu", track.ANOMALIES[0]["message"])

    def test_les_anomalies_se_comptent_avant_de_se_dire(self):
        """Quatre lignes cassées font une phrase, pas quatre bannières."""
        lignes = "".join(
            f"https://img/978140129405{n}.jpg?v={n};X;Titre {n};Nous consulter;Hardcover;24h\n"
            for n in range(4))
        track.parse_export(EN_TETE + lignes)
        self.assertEqual(len(track.ANOMALIES), 1)
        self.assertTrue(track.ANOMALIES[0]["message"].startswith("4 articles"))
        self.assertTrue(track.ANOMALIES[0]["message"].endswith("…"))


class TestPublierAlerte(unittest.TestCase):
    """Un refus d'écrire ne doit pas être un refus de le dire.

    Les garde-fous (export vide, export tronqué) laissent data.json intact —
    c'est leur raison d'être. Mais la page afficherait alors les prix de la
    veille sans rien en laisser paraître.
    """

    def setUp(self):
        track.ANOMALIES.clear()
        taire_stderr(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sortie = Path(self.tmp.name) / "data.json"
        precedent = track.OUTPUT
        track.OUTPUT = self.sortie
        self.addCleanup(lambda: setattr(track, "OUTPUT", precedent))

    def lire(self):
        import json
        return json.loads(self.sortie.read_text(encoding="utf-8"))

    def test_les_prix_et_leur_date_ne_bougent_pas(self):
        track.ecrire_json("2026-09-14T14:01:03+00:00", [{"id": "X", "prix": 12.0}])
        avant = self.lire()

        track.ANOMALIES.clear()
        track.signaler("Export suspect : 4 articles là où on en suivait 27.", grave=True)
        track.publier_alerte()
        apres = self.lire()

        # `genere_le` date les prix : il ne bouge pas, puisqu'ils n'ont pas bougé.
        self.assertEqual(apres["genere_le"], avant["genere_le"])
        self.assertEqual(apres["articles"], avant["articles"])
        # `verifie_le` date la tentative : elle, elle a eu lieu.
        self.assertGreaterEqual(apres["verifie_le"], avant["verifie_le"])
        self.assertEqual(apres["alertes"][0]["niveau"], "grave")

    def test_un_releve_reussi_repart_sans_alerte(self):
        track.signaler("Quelque chose clochait la fois d'avant.")
        track.ecrire_json("2026-09-15T09:00:00+00:00", [])
        track.ANOMALIES.clear()
        track.ecrire_json("2026-09-15T10:00:00+00:00", [])
        self.assertEqual(self.lire()["alertes"], [])

    def test_sans_data_json_il_ne_se_passe_rien(self):
        """Premier lancement du projet : il n'y a pas encore de fichier à
        annoter, et l'absence d'alerte n'est pas une erreur."""
        track.signaler("Panne.", grave=True)
        track.publier_alerte()
        self.assertFalse(self.sortie.exists())


if __name__ == "__main__":
    unittest.main()
