# Catalog: persistent movie database

The Catalog service owns a SQLite database at /app/data/catalog.db. Its schema is defined in catalog.sql and applied automatically on startup. On the first startup, the service imports dataset/movies.csv and dataset/ratings.csv into the database. Subsequent restarts read from the database.

# Catalog: genre candidates

`GET /api/movies/candidates` selects films from the local MovieLens dataset.
It uses genre rules, not AI inference.

Query parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `likes` | empty | Comma-separated preferred genres; at least one must match when supplied. |
| `avoids` | empty | Comma-separated excluded genres; any match excludes the film. |
| `year_from`, `year_to` | unset | Inclusive year bounds (1870–2100), extracted from the final `(YYYY)` in the title. |
| `limit` | `20` | Maximum results, from `1` to `100`; invalid values return HTTP 422. |

Genre matching is exact, case-insensitive and ignores surrounding whitespace and
duplicate preferences. `(no genres listed)` is treated as missing information.
Exclusions always win. Results are ordered by the number of matching preferred
genres, then descending mean rating, then ascending movie ID for stable ties.
Without preferred genres, results are ordered by rating while keeping exclusions.
An unknown preferred genre has no matches; an unknown excluded genre excludes
nothing. No compatible films means HTTP 200 with `[]`, without relaxing filters.
Year filtering happens before ranking/limiting; unknown years are excluded when
a year bound is supplied. Reversed bounds yield no matches. The dataset does not
provide runtime or plot metadata.
The response uses the same movie fields as the existing catalog endpoints.

From the repository root, rebuild the catalog:

```sh
docker compose up -d --build catalog
```

With the frontend running, try:

```sh
curl --get 'http://localhost:8080/api/movies/candidates' \
  --data-urlencode 'likes=Comedy,Adventure' \
  --data-urlencode 'avoids=Horror' \
  --data-urlencode 'limit=5'
```

Run the selection tests using the catalog container's installed dependencies:

```sh
docker compose exec -T catalog python -m unittest discover -s tests -v
```

SQLite integration retains literal, case-insensitive title search (including
Unicode and `%`/`_` characters), genre exclusions and year filtering before the
result limit. Tests use temporary SQLite databases rather than a separate
in-memory query path. The dataset is imported only when the database is empty.

The current suites contain 13 Catalog SQLite tests, 37 Recommendations/agent
tests and 16 frontend tests. The latest recorded merge validation also covered
deterministic integration, persistence across
container recreation, five real-model smoke scenarios and real-model agent
integration also passed with the combined implementation. These functional
checks do not replace the final deployment's performance measurements.
