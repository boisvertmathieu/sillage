"""Découverte et lecture des sessions Copilot CLI sur disque.

Disposition observée (Copilot CLI 1.0.x) ::

    ~/.copilot/                      (ou $COPILOT_HOME)
      session-state/
        <sessionId>/events.jsonl     un événement JSON par ligne
        <sessionId>/workspace.yaml   métadonnées (cwd, résumé, dates)
        <sessionId>.jsonl            ancien format « à plat » (toujours lu)

Sous WSL, les sessions lancées depuis Windows vivent dans
``/mnt/c/Users/<nom>/.copilot`` : elles sont découvertes aussi.
"""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RawSession:
    session_id: str
    events_path: Path
    events: list[dict] = field(default_factory=list)
    workspace: dict[str, str] = field(default_factory=dict)
    bad_lines: int = 0
    mtime: float = 0.0
    size: int = 0
    home: Path | None = None


def candidate_homes(explicit: list[str] | None = None) -> list[Path]:
    """Répertoires Copilot à examiner, dans l'ordre, sans doublons."""
    cands: list[Path] = []
    if explicit:
        cands.extend(Path(os.path.expanduser(p)) for p in explicit)
    else:
        if os.environ.get("COPILOT_HOME"):
            cands.append(Path(os.environ["COPILOT_HOME"]))
        cands.append(Path.home() / ".copilot")
        for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME"):
            if os.environ.get(var):
                cands.append(Path(os.environ[var]) / "copilot")
        # WSL : sessions Copilot lancées côté Windows
        cands.extend(Path(p) for p in sorted(glob.glob("/mnt/c/Users/*/.copilot")))
    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        key = str(c.resolve()) if c.exists() else str(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _parse_workspace_yaml(path: Path) -> dict[str, str]:
    """Lecture minimaliste (clé: valeur de premier niveau) — aucune dépendance YAML."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.startswith((" ", "\t", "#", "-")) or ":" not in line:
                continue
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return out


def iter_session_files(homes: list[Path]) -> Iterator[tuple[str, Path, Path | None, Path]]:
    """(session_id, events_path, workspace_path|None, home) pour chaque session trouvée."""
    for home in homes:
        state = home / "session-state"
        if not state.is_dir():
            continue
        for entry in sorted(state.iterdir()):
            if entry.is_dir():
                ev = entry / "events.jsonl"
                if ev.is_file():
                    ws = entry / "workspace.yaml"
                    yield entry.name, ev, (ws if ws.is_file() else None), home
            elif entry.suffix == ".jsonl" and entry.is_file():
                yield entry.stem, entry, None, home


def load_events(path: Path) -> tuple[list[dict], int]:
    events: list[dict] = []
    bad = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1  # ligne tronquée (session en cours d'écriture, crash...)
                continue
            if isinstance(obj, dict) and "type" in obj:
                events.append(obj)
            else:
                bad += 1
    return events, bad


def read_sessions(homes: list[Path], skip: dict[str, tuple[float, int]] | None = None) -> Iterator[RawSession]:
    """Lit chaque session. ``skip`` : {chemin: (mtime, taille)} déjà traités (collecte incrémentale)."""
    skip = skip or {}
    for sid, ev_path, ws_path, home in iter_session_files(homes):
        try:
            st = ev_path.stat()
        except OSError:
            continue
        if skip.get(str(ev_path)) == (st.st_mtime, st.st_size):
            continue
        events, bad = load_events(ev_path)
        yield RawSession(
            session_id=sid,
            events_path=ev_path,
            events=events,
            workspace=_parse_workspace_yaml(ws_path) if ws_path else {},
            bad_lines=bad,
            mtime=st.st_mtime,
            size=st.st_size,
            home=home,
        )
