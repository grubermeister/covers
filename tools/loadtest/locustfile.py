"""
Concurrent-user load test for WorldCovers (issue #59).

Scenarios mirror the real React frontend's traffic, not idealized API usage:
- "dashboard mount" replays what hooks/useFilterOptions.ts + postOffices.ts
  actually do on page load: page through colors/shapes/letterings at the
  default page size 10 (the frontend never sends page_size) and pull the
  unpaginated town-options blob.
- "search" and "detail" replay pages/Search.tsx paginated browsing.

Run (server on the volume-test DB, seeded to the tier under test):
    uv run --with locust locust -f tools/loadtest/locustfile.py \
        --host http://127.0.0.1:8001
Then drive the ramp from the web UI (http://localhost:8089):
1 -> 5 -> 10 -> 25 -> 50 users, 5 min per step; plus one 10-user x 60 min
soak watching gunicorn/runserver RSS drift and MySQL Threads_connected.

All scenarios are anonymous GETs (the public catalog); authenticated write
flows are out of scope for #59's read-scaling question.
"""
import random

from locust import HttpUser, between, task

SEARCH_TERMS = ["detroit", "baltimore", "paid", "steam", "ship", "free", "1836"]


def _page_through(client, path, name, max_pages=50):
    # Mirrors frontend services/*.ts: follow `next` at default page size 10.
    url = path
    for _ in range(max_pages):
        with client.get(url, name=name, catch_response=True, timeout=180) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            nxt = resp.json().get("next")
        if not nxt:
            return
        url = nxt.replace(client.base_url, "")


class CatalogVisitor(HttpUser):
    wait_time = between(1, 5)

    def on_start(self):
        # The app sets SECURE_SSL_REDIRECT when DEBUG=False; claiming HTTPS via
        # SECURE_PROXY_SSL_HEADER avoids a redirect to an https:// port that
        # only speaks HTTP (which hangs the TLS handshake indefinitely).
        self.client.headers.update({"X-Forwarded-Proto": "https"})

    @task(3)
    def dashboard_mount(self):
        _page_through(self.client, "/api/v2/colors/", "/colors/ (paged)")
        _page_through(self.client, "/api/v2/shapes/", "/shapes/ (paged)")
        _page_through(self.client, "/api/v2/letterings/", "/letterings/ (paged)")
        self.client.get("/api/v2/regions/?page_size=500", timeout=180, name="/regions/")
        self.client.get("/api/v2/post-offices/town-options/", timeout=180, name="/town-options/")

    @task(5)
    def search(self):
        page = random.randint(1, 5)
        self.client.get(
            f"/api/v2/markings/?page={page}&page_size=10",
            timeout=180, name="/markings/ (browse)",
        )

    @task(2)
    def search_with_year_filter(self):
        self.client.get(
            "/api/v2/markings/?page=1&page_size=10"
            "&earliest_use_year_min=1800&latest_use_year_max=1900",
            timeout=180, name="/markings/ (year filter)",
        )

    @task(2)
    def search_with_text(self):
        term = random.choice(SEARCH_TERMS)
        self.client.get(
            f"/api/v2/markings/?page=1&page_size=10&search={term}",
            timeout=180, name="/markings/ (text search)",
        )

    @task(3)
    def detail(self):
        # Grab a page then view one record, like a user clicking a result.
        resp = self.client.get(
            f"/api/v2/markings/?page={random.randint(1, 20)}&page_size=10",
            timeout=180, name="/markings/ (browse)",
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                mid = random.choice(results)["id"]
                self.client.get(f"/api/v2/markings/{mid}/", timeout=180, name="/markings/<id>/")
