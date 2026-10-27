

import os
import hmac
import re
import sqlite3
from contextlib import contextmanager
from threading import BoundedSemaphore
from http.cookies import SimpleCookie
from typing import Annotated

import httpx
from fastapi import Body, Cookie, Depends, FastAPI, Header, HTTPException, Response

from storage import RecommendationStore
from inference import complete, GENRES, MODEL, explicit_exclusion_genres
from prometheus_fastapi_instrumentator import Instrumentator

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user:8000").rstrip("/")
CATALOG_SERVICE_URL = os.getenv("CATALOG_SERVICE_URL", "http://catalog:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))

app = FastAPI(title="Movie Recommendations (Local AI)")

# Exposes request counts and latency histograms on /metrics for Prometheus,
# the same role prometheus_flask_exporter plays in the User service.
Instrumentator().instrument(app).expose(app)


class GenerateRequest(BaseModel):
    # Identity and preferences always come from User, never from the request body.
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=5, ge=1, le=10, strict=True)
    prompt: str = Field(default="", max_length=500, strict=True)


class UserProfile(BaseModel):
    model_config = ConfigDict(strict=True)
    user_id: int
    revision: int = Field(ge=0)
    likes_genres: str | None
    avoids_genres: str | None
    bio: str | None = None
    tags: str | None = None
    era_from: int | None = None
    era_to: int | None = None


class Movie(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int
    title: str
    genres: list[str]
    rating: float = Field(ge=0, le=5, allow_inf_nan=False)
    summary: str
    release_date: str


class Recommendation(Movie):
    reason: str


class RecommendationsResponse(BaseModel):
    method: str = "local_llm"
    model: str = MODEL
    applied_filters: dict = Field(default_factory=dict)
    recommendations: list[Recommendation]


def get_client():
    # A client per request prevents one user's session from leaking to another.
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False) as client:
        yield client


def fetch_json(client, service, url, **kwargs):
    try:
        result = client.get(url, **kwargs)
    except httpx.TimeoutException as error:
        raise HTTPException(504, f"{service} service timed out") from error
    except httpx.RequestError as error:
        raise HTTPException(503, f"{service} service unavailable") from error

    if service == "User" and result.status_code == 401:
        raise HTTPException(401, "Not logged in or session expired")
    if result.status_code != 200:
        raise HTTPException(502, f"{service} service returned an unexpected response")
    try:
        return result.json()
    except ValueError as error:
        raise HTTPException(502, f"{service} service returned invalid data") from error


def preferred_genres(value):
    return {
        genre.strip().casefold()
        for genre in (value or "").split(",")
        if genre.strip() and genre.strip().casefold() != "(no genres listed)"
    }


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    genres: list[str]
    avoids: list[str]
    year_from: int | None = Field(ge=1870, le=2100)
    year_to: int | None = Field(ge=1870, le=2100)
    unsupported: bool


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    recommendations: list[Pick]


def catalogue_reason(movie, likes):
    """Describe verified catalogue facts, not the model's internal reasoning."""
    matching = sorted({g for g in movie.genres if g.casefold() in preferred_genres(likes)})
    parts = ["Matches your selected genres: " + ", ".join(matching) + "."] if matching else ["Selected by AI from the catalogue."]
    year = re.search(r'\((\d{4})\)\s*$', movie.title)
    if year:
        parts.append(f"Year listed in the catalogue: {year.group(1)}.")
    if movie.rating > 0:
        parts.append(f"Dataset average rating: {movie.rating:.1f}/5.")
    return " ".join(parts)


# Four active generations and no application-level waiting queue. Requests beyond
# the active capacity are rejected immediately instead of waiting or consuming
# unbounded threads.
GENERATION_SLOTS = 4
GENERATION_QUEUE_SIZE = 0
_generation_slot = BoundedSemaphore(GENERATION_SLOTS)


@contextmanager
def generation_capacity():
    if not _generation_slot.acquire(blocking=False):
        raise HTTPException(
            503,
            "AI generation capacity is full; try again shortly",
            headers={"Retry-After": "5"},
        )
    try:
        yield
    finally:
        _generation_slot.release()


def generation_guard():
    """Reject saturated requests before contacting downstream services."""
    with generation_capacity():
        yield


def generate_selection(client, profile, options):
    intent = Intent(genres=[], avoids=[], year_from=None, year_to=None, unsupported=False)
    if options.prompt.strip():
        # Obvious duration requests must not rely on an LLM recognising missing data.
        if re.search(r'\b(?:minutes?|minutos?|runtime|duration|dura[çc][ãa]o)\b', options.prompt, re.I):
            raise HTTPException(422, 'Runtime constraints are not supported by this catalogue')
        schema = Intent.model_json_schema()
        for field in ("genres", "avoids"):
            schema["properties"][field]["items"] = {"type": "string", "enum": GENRES}
        explicit_genres = explicit_exclusion_genres(options.prompt)
        if explicit_genres:
            schema['properties']['avoids']['items']['enum'] = explicit_genres
        else:
            schema['properties']['avoids']['maxItems'] = 0
        raw = complete(client,
            "Extract only explicit movie genres, exclusions and release years from the request. "
            "Translate Portuguese genres to the provided English genre names. "
            "For the 80s use 1980 through 1989. Missing years are null; missing genres are []. "
            "Set unsupported=true for requests requiring runtime, cast, director or plot facts, "
            "which the dataset does not contain. General mood is a soft preference. "
            "Never infer exclusions from a positive preference: comedy does NOT exclude drama. "
            'Example request "Quero uma comédia dos anos 90" => '
            '{"genres":["Comedy"],"avoids":[],"year_from":1990,"year_to":1999,"unsupported":false}. '
            'Example request "horror without comedy" => '
            '{"genres":["Horror"],"avoids":["Comedy"],"year_from":null,"year_to":null,"unsupported":false}. '
            'Example request "a film directed by Spielberg" => '
            '{"genres":[],"avoids":[],"year_from":null,"year_to":null,"unsupported":true}. '
            "Return only the requested JSON fields.",
            {"request": options.prompt}, schema, 220)
        try:
            intent = Intent.model_validate(raw)
            if any(g not in GENRES for g in intent.genres + intent.avoids):
                raise ValueError('Unknown genre')
            if any(g not in explicit_genres for g in intent.avoids):
                raise ValueError('Ungrounded exclusion')
            if intent.year_from and intent.year_to and intent.year_from > intent.year_to:
                raise ValueError('Reversed years')
        except (ValidationError, ValueError) as error:
            raise HTTPException(502, 'AI returned invalid request filters') from error
        if intent.unsupported:
            raise HTTPException(422, 'This catalogue supports genres and years, not runtime, cast, director or plot constraints')

    # Explicit requested genres replace preferred genres, never exclusions.
    likes = ','.join(intent.genres) if intent.genres else (profile.likes_genres or '')
    avoids = preferred_genres(profile.avoids_genres) | preferred_genres(','.join(intent.avoids))
    lower = [v for v in (profile.era_from, intent.year_from) if v is not None]
    upper = [v for v in (profile.era_to, intent.year_to) if v is not None]
    year_from = max(lower) if lower else None
    year_to = min(upper) if upper else None
    filters = {'likes': likes, 'avoids': ','.join(sorted(avoids)),
               'year_from': year_from, 'year_to': year_to}
    if year_from is not None and year_to is not None and year_from > year_to:
        return RecommendationsResponse(applied_filters=filters, recommendations=[])
    params = {key: value for key, value in filters.items() if value is not None}
    params['limit'] = max(20, options.limit * 3)
    movie_data = fetch_json(client, "Catalog", CATALOG_SERVICE_URL + "/api/movies/candidates", params=params)
    try:
        movies = TypeAdapter(list[Movie]).validate_python(movie_data)
    except ValidationError as error:
        raise HTTPException(502, "Catalog service returned invalid movie data") from error

    # Defence in depth: never send excluded films to the model even if Catalog regresses.
    eligible = []
    for movie in movies:
        if preferred_genres(','.join(movie.genres)) & avoids:
            continue
        if year_from is not None or year_to is not None:
            match = re.search(r'\((\d{4})\)\s*$', movie.title)
            if not match:
                continue
            year = int(match.group(1))
            if (year_from is not None and year < year_from) or (year_to is not None and year > year_to):
                continue
        eligible.append(movie)
    if not eligible:
        return RecommendationsResponse(applied_filters=filters, recommendations=[])
    count = min(options.limit, len(eligible))
    schema = Selection.model_json_schema()
    schema['properties']['recommendations'].update(minItems=count, maxItems=count)
    schema['$defs']['Pick']['properties']['id']['enum'] = [m.id for m in eligible]
    raw = complete(client,
        f"Choose exactly {count} distinct movies from the candidates for this profile and request. "
        "Return only their IDs, without explanations or additional fields. Use supplied titles, genres "
        "and ratings, the profile and the request to choose. Return JSON only.",
        {'profile': {'likes': likes, 'avoids': sorted(avoids),
                     'bio': (profile.bio or '')[:500], 'tags': (profile.tags or '')[:200]},
         'request': options.prompt,
         'candidates': [dict(id=m.id, title=m.title, genres=m.genres, rating=m.rating) for m in eligible]},
        schema, min(1600, count * 100 + 80))
    try:
        selection = Selection.model_validate(raw)
        by_id = {m.id: m for m in eligible}
        ids = [pick.id for pick in selection.recommendations]
        if len(ids) != count or len(set(ids)) != len(ids) or any(i not in by_id for i in ids):
            raise ValueError('Invalid or duplicate selection')
    except (ValidationError, ValueError) as error:
        raise HTTPException(502, 'AI returned an invalid movie selection') from error
    return RecommendationsResponse(applied_filters=filters, recommendations=[
        Recommendation(**by_id[pick.id].model_dump(), reason=catalogue_reason(by_id[pick.id], likes))
        for pick in selection.recommendations
    ])


@app.get("/healthz")
def health():
    # Liveness only; dependency availability is checked when generating results.
    return {"ok": True}


def get_store():
    return RecommendationStore(os.getenv("RECOMMENDATIONS_DB_PATH", "/app/data/recommendations.sqlite3"))


def authenticated_profile(
    client: Annotated[httpx.Client, Depends(get_client)],
    session: Annotated[str | None, Cookie()] = None,
):
    if not session:
        raise HTTPException(401, "Not logged in or session expired")
    cookie = SimpleCookie()
    cookie["session"] = session
    profile_data = fetch_json(
        client, "User", USER_SERVICE_URL + "/api/user/me",
        headers={"Cookie": cookie.output(header="").strip()},
    )
    try:
        return UserProfile.model_validate(profile_data)
    except ValidationError as error:
        raise HTTPException(502, "User service returned invalid profile data") from error


class LatestResponse(BaseModel):
    status: str
    current_profile_revision: int
    profile_revision: int | None = None
    generated_at: str | None = None
    prompt: str | None = None
    requested_limit: int | None = None
    result: RecommendationsResponse | None = None


@app.get("/api/recommendations/latest", response_model=LatestResponse)
def latest(
    response: Response,
    profile: Annotated[UserProfile, Depends(authenticated_profile)],
    store: Annotated[RecommendationStore, Depends(get_store)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        saved = store.latest(profile.user_id)
        if saved is None:
            return LatestResponse(status="missing", current_profile_revision=profile.revision)
        return LatestResponse(
            status="fresh" if saved['profile_revision'] == profile.revision else "stale",
            current_profile_revision=profile.revision, **saved,
        )
    except (sqlite3.Error, OSError, ValueError, TypeError) as error:
        raise HTTPException(503, "Recommendation storage unavailable") from error


@app.post("/api/recommendations/generate", response_model=RecommendationsResponse)
def generate(
    response: Response,
    capacity: Annotated[None, Depends(generation_guard)],
    client: Annotated[httpx.Client, Depends(get_client)],
    profile: Annotated[UserProfile, Depends(authenticated_profile)],
    store: Annotated[RecommendationStore, Depends(get_store)],
    body: Annotated[GenerateRequest | None, Body()] = None,
):
    options = body or GenerateRequest()
    response.headers["Cache-Control"] = "no-store"
    return generate_and_save(client, profile, options, store)


def generate_and_save(client, profile, options, store):
    result = generate_selection(client, profile, options)
    try:
        store.save(profile.user_id, profile.revision, options.prompt, options.limit, result.model_dump())
    except (sqlite3.Error, OSError) as error:
        raise HTTPException(503, "Recommendation storage unavailable") from error
    return result


def internal_auth(x_internal_token: Annotated[str, Header()] = ""):
    expected = os.getenv("INTERNAL_API_TOKEN", "")
    if not expected or not hmac.compare_digest(x_internal_token.encode(), expected.encode()):
        raise HTTPException(401, "Unauthorized service")


@app.post("/internal/recommendations/{user_id}/refresh", dependencies=[Depends(internal_auth)])
def refresh(
    user_id: int,
    client: Annotated[httpx.Client, Depends(get_client)],
    store: Annotated[RecommendationStore, Depends(get_store)],
):
    # Same slot as interactive generation: background work cannot overload the
    # model or overwrite a newer manual result while it is being generated.
    with generation_capacity():
        data = fetch_json(client, "User", USER_SERVICE_URL + f"/internal/profiles/{user_id}",
                          headers={"X-Internal-Token": os.environ["INTERNAL_API_TOKEN"]})
        try:
            profile = UserProfile.model_validate(data)
            if profile.user_id != user_id:
                raise ValueError("Profile identity mismatch")
        except (ValidationError, ValueError) as error:
            raise HTTPException(502, "User service returned invalid profile data") from error
        try:
            saved = store.latest(user_id)
        except (sqlite3.Error, OSError, ValueError, TypeError) as error:
            raise HTTPException(503, "Recommendation storage unavailable") from error
        if saved is not None and saved['profile_revision'] >= profile.revision:
            return {"status": "skipped", "user_id": user_id}
        generate_and_save(client, profile, GenerateRequest(), store)
        return {"status": "generated", "user_id": user_id}
