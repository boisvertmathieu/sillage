from __future__ import annotations

import json
from pathlib import Path

import pytest

from sillage.analyze import analyze
from sillage.demo import generate
from sillage.normalize import normalize
from sillage.reader import RawSession, read_sessions


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Aucun test ne lit ni n'écrit dans le vrai ~/.copilot ou ~/.local/share/sillage."""
    monkeypatch.setenv("SILLAGE_HOME", str(tmp_path / "sillage-home"))
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "copilot-home-vide"))
    monkeypatch.setenv("SILLAGE_IDE_ROOTS", "")
    monkeypatch.delenv("SILLAGE_PARTAGE", raising=False)
    monkeypatch.delenv("SILLAGE_SALT", raising=False)
    monkeypatch.delenv("SILLAGE_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(scope="session")
def demo_homes(tmp_path_factory) -> dict[str, Path]:
    return generate(tmp_path_factory.mktemp("demo"), sessions=120, seed=11)


@pytest.fixture(scope="session")
def demo_records(demo_homes):
    recs = []
    for ident, home in demo_homes.items():
        for raw in read_sessions([home]):
            recs.append(normalize(raw, salt="test", user_identity=ident, privacy_level="redacted"))
    return recs


@pytest.fixture(scope="session")
def demo_analysis(demo_records):
    return analyze(demo_records)


class Events:
    """Petit constructeur d'événements pour des tests ciblés."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.sec = 0

    def add(self, etype: str, data: dict | None = None, **extra) -> Events:
        self.sec += 10
        ev = {
            "id": f"e{len(self.events)}",
            "type": etype,
            "data": data or {},
            "timestamp": f"2026-11-20T14:{self.sec // 60:02d}:{self.sec % 60:02d}Z",
        }
        ev.update(extra)
        self.events.append(ev)
        return self

    def raw(self, sid: str = "s1") -> RawSession:
        return RawSession(session_id=sid, events_path=Path("/dev/null"), events=self.events)

    def write(self, home: Path, sid: str = "s1") -> Path:
        d = home / "session-state" / sid
        d.mkdir(parents=True, exist_ok=True)
        p = d / "events.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in self.events) + "\n")
        return p


@pytest.fixture
def events() -> Events:
    return Events()
