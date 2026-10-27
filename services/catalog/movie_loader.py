"""Persistent MovieLens catalogue with literal search and candidate selection."""
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CATALOG_DB_PATH", str(BASE / "data" / "catalog.db")))
MOVIES_CSV = BASE / "dataset" / "movies.csv"
RATINGS_CSV = BASE / "dataset" / "ratings.csv"


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _initialize():
    with _connect() as conn:
        conn.executescript((BASE / "catalog.sql").read_text(encoding="utf-8"))
        # Serialize first import across simultaneous service/test processes.
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0:
            movies = pd.read_csv(MOVIES_CSV)
            ratings = pd.read_csv(RATINGS_CSV).groupby("movieId")["rating"].mean()
            movies["rating"] = movies["movieId"].map(ratings).fillna(0.0)
            movies["title"] = movies["title"].fillna("")
            movies["genres"] = movies["genres"].fillna("")
            conn.executemany(
                "INSERT INTO movies (movie_id, title, genres, rating) VALUES (?, ?, ?, ?)",
                movies[["movieId", "title", "genres", "rating"]].itertuples(index=False, name=None),
            )


_initialize()


def _normalize_genres(genres):
    return {g.strip().casefold() for g in genres
            if g.strip() and g.strip().casefold() != "(no genres listed)"}


def _rows():
    with _connect() as conn:
        return conn.execute("SELECT movie_id AS movieId, title, genres, rating FROM movies").fetchall()


def _serialize(row):
    return {
        "id": int(row["movieId"]), "title": row["title"],
        "rating": round(float(row["rating"] or 0), 2),
        "summary": "Filme carregado do catálogo persistente.", "release_date": "",
        "genres": row["genres"].split("|") if row["genres"] else [],
    }


def get_popular_movies(page=1, limit=20):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT movie_id AS movieId, title, genres, rating FROM movies "
            "ORDER BY movie_id LIMIT ? OFFSET ?", (limit, (page - 1) * limit),
        ).fetchall()
    return [_serialize(row) for row in rows]


def search_movies(query, page=1, limit=20):
    # instr treats %, _, brackets and backslashes literally, unlike LIKE/regex.
    # Python casefold also retains case-insensitive matching for Unicode titles.
    with _connect() as conn:
        rows = conn.execute(
            "SELECT movie_id AS movieId, title, genres, rating FROM movies "
            "WHERE instr(casefold(title), ?) > 0 ORDER BY movie_id LIMIT ? OFFSET ?",
            (query.casefold(), limit, (page - 1) * limit),
        ).fetchall()
    return [_serialize(row) for row in rows]


def get_movie_candidates(likes="", avoids="", limit=20, year_from=None, year_to=None):
    preferred = _normalize_genres(likes.split(","))
    excluded = _normalize_genres(avoids.split(","))
    candidates = []
    for row in _rows():
        genres = _normalize_genres((row["genres"] or "").split("|"))
        if genres & excluded:
            continue
        matches = len(genres & preferred)
        if preferred and not matches:
            continue
        # Filter before ranking/limiting; unknown years cannot satisfy a bound.
        if year_from is not None or year_to is not None:
            match = re.search(r"\((\d{4})\)\s*$", row["title"])
            if not match:
                continue
            year = int(match.group(1))
            if year_from is not None and year < year_from:
                continue
            if year_to is not None and year > year_to:
                continue
        candidates.append((matches, float(row["rating"] or 0), int(row["movieId"]), row))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [_serialize(row) for _, _, _, row in candidates[:limit]]


def get_movie_details(movie_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT movie_id AS movieId, title, genres, rating FROM movies WHERE movie_id = ?",
            (movie_id,),
        ).fetchone()
    return _serialize(row) if row is not None else None
