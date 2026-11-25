"""Magasin local incrémental et bundles d'export anonymisés.

* Magasin local : ``$SILLAGE_HOME`` ou ``~/.local/share/sillage`` — enregistrements
  normalisés (jamais les journaux bruts), un index (mtime, taille) pour la collecte
  incrémentale et le sel local de pseudonymisation.
* Bundle : fichier ``.jsonl.gz`` partageable (1re ligne = manifeste). Les identifiants
  de session y sont re-pseudonymisés et le niveau de confidentialité y est appliqué.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sillage import RECORD_SCHEMA_VERSION, __version__
from sillage.config import Config
from sillage.model import SessionRecord
from sillage.normalize import normalize
from sillage.reader import candidate_homes, read_sessions
from sillage.redact import pseudonymize

BUNDLE_MAGIC = "sillage-bundle"


def data_dir() -> Path:
    if os.environ.get("SILLAGE_HOME"):
        d = Path(os.environ["SILLAGE_HOME"])
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        d = Path(base) / "sillage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_salt(cfg: Config) -> str:
    shared = os.environ.get(cfg.salt_env)
    if shared:
        return shared
    path = data_dir() / "salt"
    if not path.exists():
        path.write_text(secrets.token_hex(16))
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    return path.read_text().strip()


@dataclass
class CollectResult:
    scanned_homes: list[str]
    new_or_updated: int
    total: int
    bad_lines: int
    unknown_event_types: set[str]


class LocalStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_path = self.root / "sessions.jsonl"
        self.index_path = self.root / "index.json"

    # ------------------------------------------------------------ persistence
    def _load_index(self) -> dict[str, tuple[float, int]]:
        if not self.index_path.exists():
            return {}
        raw = json.loads(self.index_path.read_text())
        return {k: (v[0], v[1]) for k, v in raw.items()}

    def load(self) -> dict[str, SessionRecord]:
        out: dict[str, SessionRecord] = {}
        if self.records_path.exists():
            with self.records_path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        rec = SessionRecord.from_dict(json.loads(line))
                        out[rec.session_id] = rec
        return out

    def _save(self, records: dict[str, SessionRecord], index: dict[str, tuple[float, int]]) -> None:
        tmp = self.records_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for rec in sorted(records.values(), key=lambda r: r.start or ""):
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self.records_path)
        self.index_path.write_text(json.dumps(index))

    # ---------------------------------------------------------------- collect
    def collect(self, cfg: Config, homes: list[str] | None = None, user_identity: str | None = None) -> CollectResult:
        home_paths = candidate_homes(homes or cfg.copilot_homes or None)
        index = self._load_index()
        records = self.load()
        salt = get_salt(cfg)
        updated = bad = 0
        unknown: set[str] = set()
        for raw in read_sessions(home_paths, skip=index):
            if not raw.events:
                continue
            rec = normalize(
                raw,
                salt=salt,
                user_identity=user_identity,
                privacy_level=cfg.local_privacy,
                extra_correction_patterns=cfg.extra_correction_patterns,
                weights=cfg.friction,
            )
            records[rec.session_id] = rec
            index[str(raw.events_path)] = (raw.mtime, raw.size)
            updated += 1
            bad += raw.bad_lines
            unknown.update(rec.unknown_event_types)
        self._save(records, index)
        return CollectResult([str(h) for h in home_paths], updated, len(records), bad, unknown)


# ---------------------------------------------------------------- filtering
def parse_since(value: str | None) -> datetime | None:
    """``30d``, ``12w``, ``48h`` ou une date ISO."""
    if not value:
        return None
    v = value.strip().lower()
    now = datetime.now(UTC)
    try:
        if v.endswith("d"):
            return now - timedelta(days=float(v[:-1]))
        if v.endswith("w"):
            return now - timedelta(weeks=float(v[:-1]))
        if v.endswith("h"):
            return now - timedelta(hours=float(v[:-1]))
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"Période invalide : {value!r} (ex. 30d, 12w, 2026-10-01)") from exc


def filter_records(
    records: Iterable[SessionRecord], since: datetime | None = None, repository: str | None = None
) -> list[SessionRecord]:
    out = []
    for r in records:
        if since and r.start:
            start = datetime.fromisoformat(r.start)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if start < since:
                continue
        if repository and (r.repository or "").lower() != repository.lower():
            continue
        out.append(r)
    return sorted(out, key=lambda r: r.start or "")


# ------------------------------------------------------------------ privacy
def apply_privacy(rec: SessionRecord, level: str, salt: str, hash_repositories: bool = False) -> SessionRecord:
    """Copie de ``rec`` prête à quitter la machine."""
    out = SessionRecord.from_dict(rec.to_dict())
    out.session_id = pseudonymize("session:" + rec.session_id, salt, 16)
    if hash_repositories and out.repository:
        out.repository = "depot-" + pseudonymize("repo:" + out.repository, salt, 8)
    if level == "metrics":
        for t in out.tool_calls:
            t.error_excerpt = None
            t.command_excerpt = None
            t.recovered_by_detail = None
        for c in out.corrections:
            c.excerpt = None
    out.privacy_level = "metrics" if level == "metrics" or rec.privacy_level == "metrics" else "redacted"
    return out


def export_bundle(records: list[SessionRecord], path: Path, cfg: Config, level: str | None = None) -> int:
    level = level or cfg.export_privacy
    salt = get_salt(cfg)
    manifest = {
        "magic": BUNDLE_MAGIC,
        "schema": RECORD_SCHEMA_VERSION,
        "sillage_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "privacy_level": level,
        "count": len(records),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest) + "\n")
        for rec in records:
            out = apply_privacy(rec, level, salt, cfg.hash_repositories)
            fh.write(json.dumps(out.to_dict(), ensure_ascii=False) + "\n")
    return len(records)


def find_bundles(directory: Path) -> list[Path]:
    """Tous les bundles d'un dossier (récursif) — utile quand les jokers ne sont pas interprétés
    (fichiers de configuration, tâches planifiées, instructions d'agents)."""
    if not directory.is_dir():
        raise ValueError(f"Dossier de bundles introuvable : {directory}")
    return sorted(p for p in directory.rglob("*.jsonl*") if p.is_file() and p.name.endswith((".jsonl", ".jsonl.gz")))


def load_bundles(paths: Iterable[Path]) -> list[SessionRecord]:
    """Fusionne des bundles (dédoublonnage par identifiant de session pseudonymisé)."""
    merged: dict[str, SessionRecord] = {}
    for p in paths:
        opener = gzip.open if str(p).endswith(".gz") else open
        with opener(p, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
            first = fh.readline()
            manifest = json.loads(first) if first.strip() else {}
            if manifest.get("magic") != BUNDLE_MAGIC:
                raise ValueError(f"{p} n'est pas un bundle Sillage")
            if int(manifest.get("schema", 0)) > RECORD_SCHEMA_VERSION:
                raise ValueError(f"{p} : schéma {manifest['schema']} plus récent que cette version de Sillage")
            for line in fh:
                if line.strip():
                    rec = SessionRecord.from_dict(json.loads(line))
                    merged[rec.session_id] = rec
    return list(merged.values())
