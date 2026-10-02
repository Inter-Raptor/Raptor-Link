"""Raptor Link update checks, engagement state and community feedback helpers."""
from __future__ import annotations
import json
import os
import platform
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

APP_VERSION = "0.3.6"
REPOSITORY = "Inter-Raptor/Raptor-Link"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=10"
ISSUES_NEW = f"https://github.com/{REPOSITORY}/issues/new"

_VERSION_RE = re.compile(r"^(?:v)?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$", re.I)

def version_tuple(value):
    match = _VERSION_RE.match(str(value or "").strip())
    return tuple(map(int, match.groups())) if match else None

def find_update(current=APP_VERSION, timeout=3):
    """Return the newest published GitHub release newer than *current*.

    A failed or unavailable network never raises into the application.
    """
    current_v = version_tuple(current)
    if current_v is None:
        return {"checked": True, "available": False, "error": "Version locale invalide"}
    try:
        request = urllib.request.Request(
            RELEASES_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Raptor-Link/{current}",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            releases = json.load(response)
        candidates = []
        for release in releases if isinstance(releases, list) else []:
            if release.get("draft"):
                continue
            parsed = version_tuple(release.get("tag_name"))
            if parsed is None:
                continue
            candidates.append((parsed, release))
        if not candidates:
            return {"checked": True, "available": False, "error": ""}
        newest_v, newest = max(candidates, key=lambda item: item[0])
        available = newest_v > current_v
        return {
            "checked": True,
            "available": available,
            "version": ".".join(map(str, newest_v)),
            "name": str(newest.get("name") or newest.get("tag_name") or ""),
            "url": str(newest.get("html_url") or ""),
            "prerelease": bool(newest.get("prerelease")),
            "error": "",
        }
    except Exception as exc:
        return {"checked": True, "available": False, "error": str(exc)[:240]}

class Engagement:
    """Persist cumulative app usage and one-time rating prompt state."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.last_tick = time.monotonic()
        self.last_save = self.last_tick
        self.data = {
            "seconds_used": 0.0,
            "rating_submitted": False,
            "rating": None,
            "rating_snooze_until": 0,
            "rating_never": False,
        }
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except Exception:
            pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)
        self.last_save = time.monotonic()

    def tick(self):
        now = time.monotonic()
        elapsed = max(0.0, min(10.0, now - self.last_tick))
        self.last_tick = now
        with self.lock:
            self.data["seconds_used"] = float(self.data.get("seconds_used", 0)) + elapsed
            if now - self.last_save >= 30:
                try:
                    self._save()
                except OSError:
                    pass

    def snapshot(self):
        with self.lock:
            due = (
                not self.data.get("rating_submitted")
                and not self.data.get("rating_never")
                and float(self.data.get("seconds_used", 0)) >= 3600
                and time.time() >= float(self.data.get("rating_snooze_until", 0))
            )
            return {
                "seconds_used": int(self.data.get("seconds_used", 0)),
                "rating_due": bool(due),
                "rating_submitted": bool(self.data.get("rating_submitted")),
                "rating": self.data.get("rating"),
            }

    def submit_rating(self, stars):
        stars = int(stars)
        if not 1 <= stars <= 5:
            raise ValueError("La note doit être comprise entre 1 et 5.")
        with self.lock:
            self.data["rating"] = stars
            self.data["rating_submitted"] = True
            self.data["rating_never"] = False
            self._save()
        return stars

    def snooze_rating(self, days=7):
        with self.lock:
            self.data["rating_snooze_until"] = int(time.time() + max(1, int(days)) * 86400)
            self._save()

    def never_rating(self):
        with self.lock:
            self.data["rating_never"] = True
            self._save()

    def close(self):
        with self.lock:
            try:
                self._save()
            except OSError:
                pass

def diagnostics(engine):
    state = engine.snapshot()
    devices = []
    for device in state.get("devices", []):
        model = str(device.get("model", "")).strip()
        if model:
            devices.append(model[:100])
    with engine.lock:
        targets = list(engine.config.get("targets", []))
    return {
        "raptor_link": APP_VERSION,
        "windows": platform.platform(),
        "python": platform.python_version(),
        "wled_devices": len(targets),
        "source_devices": devices[:20],
        "sync_running": bool(state.get("running")),
    }

def issue_url(kind, subject="", message="", rating=None, environment=None, ecosystem=""):
    labels = {
        "bug": "Bug",
        "question": "Question",
        "suggestion": "Suggestion",
        "comment": "Feedback",
        "compatibility": "Compatibility",
        "rating": "Rating",
    }
    prefix = labels.get(kind, "Feedback")
    title = f"[{prefix}] {str(subject).strip() or 'Raptor Link'}"[:180]
    body = str(message or "").strip()
    blocks = []
    if body:
        blocks.append(body)
    if ecosystem:
        blocks.append(f"\n### RGB ecosystem\n{ecosystem}")
    if rating is not None:
        blocks.append(f"\n### Rating\n{int(rating)}/5")
    if environment:
        rows = [f"- **{key.replace('_', ' ').title()}**: {value}" for key, value in environment.items()]
        blocks.append("\n### Diagnostics shared by the user\n" + "\n".join(rows))
    blocks.append("\n---\nSent from Raptor Link. The user can review this text before submitting the GitHub issue.")
    params = urllib.parse.urlencode({"title": title, "body": "\n".join(blocks)})
    return ISSUES_NEW + "?" + params
