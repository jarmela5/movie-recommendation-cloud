import unittest
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pandas as pd

import movie_loader


class MovieCandidatesTests(unittest.TestCase):
    def setUp(self):
        # Controlled films make exclusion and ranking independent of MovieLens updates.
        self.movies = pd.DataFrame([
            {"movieId": 1, "title": "Both", "genres": "Comedy|Adventure", "rating": 3.0},
            {"movieId": 2, "title": "Comedy", "genres": "Comedy", "rating": 4.5},
            {"movieId": 3, "title": "Excluded", "genres": "Comedy|Horror", "rating": 5.0},
            {"movieId": 4, "title": "Drama", "genres": "Drama", "rating": 4.0},
            {"movieId": 5, "title": "Unknown", "genres": "(no genres listed)", "rating": 2.0},
            {"movieId": 6, "title": "Adventure", "genres": "Adventure", "rating": 4.5},
            {"movieId": 7, "title": "Missing", "genres": None, "rating": 0.0},
        ])
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(movie_loader, "DB_PATH", Path(self.directory.name) / "catalog.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with movie_loader._connect() as conn:
            conn.executescript((movie_loader.BASE / "catalog.sql").read_text())
            conn.executemany("INSERT INTO movies VALUES (?, ?, ?, ?)",
                self.movies.fillna({"genres": ""}).itertuples(index=False, name=None))

    def ids(self, **kwargs):
        return [movie["id"] for movie in movie_loader.get_movie_candidates(**kwargs)]

    def test_overlap_precedes_rating_and_id_breaks_ties(self):
        self.assertEqual(self.ids(likes="Comedy,Adventure", avoids="Horror"), [1, 2, 6])

    def test_exclusions_win_over_preferences(self):
        self.assertEqual(self.ids(likes="Comedy,Horror", avoids="Horror"), [2, 1])
        self.assertEqual(self.ids(likes="Horror", avoids="Horror"), [])

    def test_case_whitespace_and_duplicate_preferences(self):
        self.assertEqual(
            self.ids(likes=" COMEDY, comedy, AdVeNtUrE, ", avoids=" hORRoR "),
            [1, 2, 6],
        )

    def test_without_preferences_ranks_by_rating_and_keeps_exclusions(self):
        self.assertEqual(self.ids(), [3, 2, 6, 4, 1, 5, 7])
        self.assertEqual(self.ids(likes=" , ", avoids="Horror", limit=3), [2, 6, 4])

    def test_limit_is_applied_after_exclusion_and_ranking(self):
        self.assertEqual(self.ids(likes="Comedy,Adventure", avoids="Horror", limit=1), [1])

    def test_no_matches_returns_empty_without_fallback(self):
        self.assertEqual(self.ids(likes="Western"), [])
        self.assertEqual(self.ids(likes="Com"), [])

    def test_missing_genres_do_not_match_preferences(self):
        self.assertEqual(self.ids(likes="Comedy"), [3, 2, 1])
        self.assertEqual(movie_loader._normalize_genres(["(no genres listed)", ""]), set())

    def test_empty_catalog_returns_empty(self):
        with movie_loader._connect() as conn:
            conn.execute("DELETE FROM movies")
        self.assertEqual(self.ids(likes="Comedy"), [])

    def test_response_preserves_movie_shape_without_mutating_catalog(self):
        before = [dict(row) for row in movie_loader._rows()]
        result = movie_loader.get_movie_candidates(likes="Comedy,Adventure", avoids="Horror")
        self.assertEqual(set(result[0]), {"id", "title", "genres", "rating", "summary", "release_date"})
        self.assertEqual(result[0]["genres"], ["Comedy", "Adventure"])
        self.assertEqual(before, [dict(row) for row in movie_loader._rows()])

    def test_year_filter_precedes_limit_and_excludes_unknown_year(self):
        with movie_loader._connect() as conn:
            conn.execute("UPDATE movies SET title = 'Both (1985)' WHERE movie_id = 1")
            conn.execute("UPDATE movies SET title = 'Comedy (1995)' WHERE movie_id = 2")
        self.assertEqual(self.ids(likes='Comedy', avoids='Horror', year_from=1980, year_to=1989, limit=1), [1])
        self.assertEqual(self.ids(year_from=2020), [])

    def test_literal_unicode_search_and_pagination(self):
        with movie_loader._connect() as conn:
            conn.execute("UPDATE movies SET title = 'Été 100%_fun [1995]' WHERE movie_id = 1")
        for query in ['%', '_', '[', 'été', '100%_']:
            self.assertEqual([m['id'] for m in movie_loader.search_movies(query)], [1])
        self.assertEqual(movie_loader.search_movies("' OR 1=1 --"), [])
        self.assertEqual([m['id'] for m in movie_loader.get_popular_movies(page=2, limit=2)], [3, 4])
        self.assertEqual(movie_loader.get_movie_details(1)['title'], 'Été 100%_fun [1995]')
        self.assertIsNone(movie_loader.get_movie_details(999))

    def test_initialization_keeps_existing_database_without_rereading_dataset(self):
        with patch.object(movie_loader.pd, 'read_csv', side_effect=AssertionError('CSV should not be reread')):
            movie_loader._initialize()
        self.assertEqual(len(movie_loader.get_popular_movies()), 7)

    def test_concurrent_first_import_is_atomic(self):
        directory = Path(self.directory.name)
        movies = directory / 'movies.csv'
        ratings = directory / 'ratings.csv'
        movies.write_text('movieId,title,genres\n1,Example (1995),Comedy\n')
        ratings.write_text('userId,movieId,rating\n1,1,3.0\n2,1,5.0\n')
        with patch.object(movie_loader, 'DB_PATH', directory / 'new.db'), \
             patch.object(movie_loader, 'MOVIES_CSV', movies), \
             patch.object(movie_loader, 'RATINGS_CSV', ratings):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: movie_loader._initialize(), range(2)))
            self.assertEqual(len(movie_loader.get_popular_movies()), 1)
            self.assertEqual(movie_loader.get_movie_details(1)['rating'], 4.0)


if __name__ == "__main__":
    unittest.main()
