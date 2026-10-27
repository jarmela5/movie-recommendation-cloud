from fastapi import FastAPI, HTTPException, Query
from prometheus_fastapi_instrumentator import Instrumentator
from movie_loader import get_popular_movies, search_movies, get_movie_details, get_movie_candidates

app = FastAPI(title="Movie Catalog API (Local Dataset)")

# Exposes request counts and latency histograms on /metrics for Prometheus,
# the same role prometheus_flask_exporter plays in the User service.
Instrumentator().instrument(app).expose(app)


@app.get("/api/movies")
async def popular_movies(page: int = Query(1, ge=1)):
    try:
        return get_popular_movies(page)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Unable to load movies from local dataset") from error


@app.get("/api/movies/search")
async def movie_search(query: str = Query(..., min_length=1), page: int = Query(1, ge=1)):
    try:
        return search_movies(query, page)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Unable to search movies") from error

@app.get("/api/movies/candidates")
async def movie_candidates(
    likes: str = Query("", description="Preferred genres, separated by commas"),
    avoids: str = Query("", description="Excluded genres, separated by commas"),
    limit: int = Query(20, ge=1, le=100),
    year_from: int | None = Query(None, ge=1870, le=2100),
    year_to: int | None = Query(None, ge=1870, le=2100),
):
    try:
        return get_movie_candidates(likes=likes, avoids=avoids, limit=limit, year_from=year_from, year_to=year_to)
    except Exception as error:
        raise HTTPException(status_code=500, detail="Unable to select movie candidates") from error


@app.get("/api/movies/{movie_id}")
async def movie_details(movie_id: int):
    movie = get_movie_details(movie_id)
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie
