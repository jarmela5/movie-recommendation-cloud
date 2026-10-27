"""Periodic HTTP worker; owns no database and never uses a user's session."""
import argparse
import logging
import os
import signal
from threading import Event

import httpx

log = logging.getLogger("recommendation-agent")


def run_cycle(client, user_url, recommendations_url, token, stop=None):
    headers = {"X-Internal-Token": token}
    counts = {"generated": 0, "skipped": 0, "failed": 0}
    after = 0
    while stop is None or not stop.is_set():
        response = client.get(user_url + "/internal/profiles",
                              params={"after": after, "limit": 50}, headers=headers, timeout=5)
        response.raise_for_status()
        profiles = response.json()
        if not isinstance(profiles, list) or len(profiles) > 50:
            raise ValueError("Invalid profile page")
        if not profiles:
            return counts
        for profile in profiles:
            if stop is not None and stop.is_set():
                return counts
            user_id = profile.get("user_id") if isinstance(profile, dict) else None
            if type(user_id) is not int or user_id <= after:
                raise ValueError("Invalid profile pagination")
            after = user_id
            try:
                result = client.post(recommendations_url + f"/internal/recommendations/{user_id}/refresh",
                                     headers=headers, timeout=120)
                result.raise_for_status()
                status = result.json()["status"]
                if status not in ("generated", "skipped"):
                    raise ValueError("Invalid refresh status")
                counts[status] += 1
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                # No profile text, credentials or response bodies in logs.
                counts["failed"] += 1
                status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
                log.warning("Refresh failed for user_id=%s (%s); retry next cycle", user_id, status)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.getenv("INTERNAL_API_TOKEN", "")
    if not token:
        parser.error("Configure INTERNAL_API_TOKEN before starting the agent")
    interval = int(os.getenv("AGENT_INTERVAL_SECONDS", "300"))
    if interval < 1:
        parser.error("AGENT_INTERVAL_SECONDS must be positive")
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    user_url = os.getenv("USER_SERVICE_URL", "http://user:8000").rstrip("/")
    rec_url = os.getenv("RECOMMENDATIONS_SERVICE_URL", "http://recommendations:8000").rstrip("/")
    with httpx.Client(trust_env=False) as client:
        while not stop.is_set():
            try:
                counts = run_cycle(client, user_url, rec_url, token, stop)
                log.info("Cycle complete: generated=%s skipped=%s failed=%s", *counts.values())
                success = counts["failed"] == 0
            except (httpx.HTTPError, ValueError, TypeError):
                log.warning("Profile scan failed; retry next cycle")
                success = False
            if args.once:
                return 0 if success else 1
            stop.wait(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
