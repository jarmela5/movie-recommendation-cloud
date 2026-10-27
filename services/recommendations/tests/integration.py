
import time
import uuid

import httpx


def expect(response, status):
    assert response.status_code == status, (response.status_code, response.text)
    return response.json()


def generate(client, **kwargs):
    return expect(client.post("/api/recommendations/generate", **kwargs), 200)


def main():
    suffix = uuid.uuid4().hex[:8]
    first_name = "genre-" + suffix
    second_name = "other-" + suffix
    with httpx.Client(base_url="http://frontend", timeout=15, trust_env=False) as first:
        # Containers start independently; wait for the complete path to become ready.
        for attempt in range(30):
            try:
                readiness = [
                    first.get("/api/movies").status_code == 200,
                    first.get("/api/user/me").status_code == 401,
                    first.post("/api/recommendations/generate").status_code == 401,
                ]
                if all(readiness):
                    break
            except httpx.RequestError:
                pass
            time.sleep(1)
        else:
            raise AssertionError("Test services did not become ready")

        expect(first.post("/api/user/register", json={
            "username": first_name, "email": first_name + "@example.test",
            "password": "integration-only-password",
        }), 201)
        # Newly registered profile: null preferences still produce a general selection.
        general = generate(first, json={"limit": 3})
        assert general["method"] == "local_llm"
        assert len(general["recommendations"]) == 3

        expect(first.put("/api/user/profile", json={
            "likes": ["Comedy", "Adventure"], "avoids": ["Horror"],
        }), 200)
        result = generate(first, json={"limit": 5})
        movies = result["recommendations"]
        assert len(movies) == 5
        ranking = []
        for movie in movies:
            genres = {g.casefold() for g in movie["genres"]}
            assert genres & {"comedy", "adventure"}
            assert "horror" not in genres
            assert movie["reason"]
            ranking.append((-len(genres & {"comedy", "adventure"}), -movie["rating"], movie["id"]))
            details = expect(first.get(f"/api/movies/{movie['id']}"), 200)
            assert details["title"] == movie["title"]
        assert ranking == sorted(ranking)

        # A second cookie jar must use its own profile, not the first user's.
        with httpx.Client(base_url="http://frontend", timeout=15, trust_env=False) as second:
            expect(second.post("/api/user/register", json={
                "username": second_name, "email": second_name + "@example.test",
                "password": "integration-only-password",
            }), 201)
            expect(second.put("/api/user/profile", json={
                "likes": ["Horror"], "avoids": ["Comedy"],
            }), 200)
            other_movies = generate(second)["recommendations"]
            assert other_movies
            assert all("Horror" in m["genres"] and "Comedy" not in m["genres"] for m in other_movies)
            assert generate(first, json={"limit": 5}) == result

        expect(first.put("/api/user/profile", json={"likes": ["InventedGenre"], "avoids": []}), 200)
        assert generate(first)["recommendations"] == []
        expect(first.post("/api/recommendations/generate", json={"limit": 0}), 422)
        expect(first.post("/api/recommendations/generate", json={"username": "other-test"}), 422)
        assert expect(first.get("/api/movies/search", params={"query": "Toy Story"}), 200)
        assert first.get("/recommendations.html").status_code == 200

        old_cookie = first.cookies.get("session")
        expect(first.post("/api/user/logout"), 200)
        expect(first.post("/api/recommendations/generate"), 401)
        expect(first.post("/api/recommendations/generate", headers={"Cookie": f"session={old_cookie}"}), 401)
        expect(first.post("/api/user/login", json={
            "identifier": first_name, "password": "integration-only-password",
        }), 200)
        assert generate(first)["recommendations"] == []

    print("Integration passed: Nginx, real User and Catalog, session isolation, preferences, ranking, empty results, login and logout.")


if __name__ == "__main__":
    main()
