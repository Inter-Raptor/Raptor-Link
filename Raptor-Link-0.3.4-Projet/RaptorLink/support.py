"""Raptor Link Core 3 - minimal update checker."""
from __future__ import annotations

import json
import re
import urllib.request

APP_VERSION = "0.5.0"
REPOSITORY = "Inter-Raptor/Raptor-Link"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=10"
_VERSION_RE = re.compile(r"^(?:v)?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$", re.I)


def version_tuple(value):
    match = _VERSION_RE.match(str(value or "").strip())
    return tuple(map(int, match.groups())) if match else None


def find_update(current=APP_VERSION, timeout=3):
    """Check GitHub releases. Network errors never escape into Core 3."""
    current_version = version_tuple(current)
    if current_version is None:
        return {"checked": True, "available": False, "error": "Version locale invalide"}
    try:
        request = urllib.request.Request(
            RELEASES_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Raptor-Link/" + str(current),
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
            if parsed is not None:
                candidates.append((parsed, release))

        if not candidates:
            return {"checked": True, "available": False, "error": ""}

        newest_version, newest = max(candidates, key=lambda item: item[0])
        return {
            "checked": True,
            "available": newest_version > current_version,
            "version": ".".join(map(str, newest_version)),
            "name": str(newest.get("name") or newest.get("tag_name") or ""),
            "url": str(newest.get("html_url") or ""),
            "prerelease": bool(newest.get("prerelease")),
            "error": "",
        }
    except Exception as exc:
        return {
            "checked": True,
            "available": False,
            "error": str(exc)[:240],
        }
