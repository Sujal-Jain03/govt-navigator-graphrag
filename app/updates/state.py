"""Update state: what has been seen/downloaded, so the daily job
is idempotent — the same URL or the same content is never
downloaded twice.

File: processed_data/updates/state.json
  downloads: {url: {sha256, file, source, kind, discovered_at, status}}
  local:     {filename: {sha256, first_seen}}   (manual drops)
"""
import json
from pathlib import Path


def state_path(processed_dir):
    return Path(processed_dir) / "updates" / "state.json"


def load_state(processed_dir):
    path = state_path(processed_dir)
    if not path.exists():
        return {"downloads": {}, "local": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(processed_dir, state):
    path = state_path(processed_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def seen_url(state, url):
    return url in state.get("downloads", {})


def seen_hash(state, sha256):
    return any(r.get("sha256") == sha256
               for r in state.get("downloads", {}).values())
