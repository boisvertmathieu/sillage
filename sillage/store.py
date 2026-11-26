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
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sillage import RECORD_SCHEMA_VERSION, __version__
from sillage.classify import strict_signature
from sillage.config import Config
from sillage.model import SessionRecord
from sillage.normalize import normalize
from sillage.reader import candidate_homes, read_sessions
from sillage.redact import compile_patterns, pseudonymize, redact

BUNDLE_MAGIC = "sillage-bundle"


def data_dir() -> Path:
    if os.environ.get("SILLAGE_HOME"):
        d = Path(os.environ["SILLAGE_HOME"])
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA") and not os.environ.get("XDG_DATA_HOME"):
        d = Path(os.environ["LOCALAPPDATA"]) / "sillage"  # Windows natif (hors WSL)
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        d = Path(base) / "sillage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_salt() -> str:
    """Sel aléatoire propre au poste, jamais partagé : sert aux pseudonymes de personne et de session.

    Un sel connu de toute l'équipe permettrait de retrouver une personne en testant
    ``HMAC(prénom@poste)`` pour chaque collègue : il n'est donc jamais utilisé pour l'identité.
    """
    path = data_dir() / "salt"
    if not path.exists():
        path.write_text(secrets.token_hex(16))
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def shared_salt(cfg: Config) -> str | None:
    """Sel commun à l'équipe (variable ``salt_env``) : seulement pour les pseudonymes de dépôts."""
    return os.environ.get(cfg.salt_env) or None


def get_salt(cfg: Config) -> str:  # compatibilité : identité => toujours le sel local
    return local_salt()


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
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
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
        salt = local_salt()
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
                redaction_patterns=cfg.redaction_patterns,
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


def parse_duration(value: str | None) -> timedelta | None:
    """``28d``, ``4w``, ``48h`` ; vide => None (aucune limite)."""
    if not value:
        return None
    v = value.strip().lower()
    units = {"d": "days", "w": "weeks", "h": "hours"}
    try:
        return timedelta(**{units[v[-1]]: float(v[:-1])})
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Durée invalide : {value!r} (ex. 28d, 4w, 48h)") from exc


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
def _prefixed(value: str, fn) -> str:
    """Applique ``fn`` à la partie signature d'une chaîne « préfixe: signature »."""
    head, sep, tail = value.partition(": ")
    return f"{head}{sep}{fn(tail)}" if sep else fn(value)


def apply_privacy(
    rec: SessionRecord,
    level: str,
    salt: str,
    hash_repositories: bool = False,
    redaction_patterns: list[str] | None = None,
    repo_salt: str | None = None,
) -> SessionRecord:
    """Copie de ``rec`` prête à quitter la machine.

    * ``redacted`` : caviardage refait avec les motifs actuels de l'organisation ;
    * ``metrics``  : en plus, aucun extrait ni commande complète ;
    * ``strict``   : en plus, chaque signature d'erreur devient ``type#empreinte`` (aucun texte d'origine).

    ``salt`` (local au poste) re-pseudonymise la personne et la session ; ``repo_salt`` (partagé par
    l'équipe) sert aux pseudonymes de dépôts, qui doivent être identiques d'un poste à l'autre.
    """
    extra = compile_patterns(redaction_patterns)

    def scrub(text: str | None) -> str | None:
        # second passage : couvre les sessions collectées avant l'ajout d'un motif
        return redact(text, 500, extra=extra) if text else text

    if level == "strict":

        def sig(text: str | None) -> str | None:
            return strict_signature(scrub(text))
    else:
        sig = scrub

    out = SessionRecord.from_dict(rec.to_dict())
    out.session_id = pseudonymize("session:" + rec.session_id, salt, 16)
    out.user = pseudonymize("user:" + rec.user, salt)
    if hash_repositories and out.repository:
        if not repo_salt:
            raise ValueError(
                "hash_repositories = true exige un sel d'équipe (variable SILLAGE_SALT) : sans lui, chaque poste "
                "produirait un pseudonyme différent pour le même dépôt."
            )
        out.repository = "depot-" + pseudonymize("repo:" + out.repository, repo_salt, 8)
    for t in out.tool_calls:
        t.error_signature = sig(t.error_signature)
        t.target = scrub(t.target)
        t.recovered_by = scrub(t.recovered_by)
        t.error_excerpt = scrub(t.error_excerpt)
        t.command_excerpt = scrub(t.command_excerpt)
        t.recovered_by_detail = scrub(t.recovered_by_detail)
    for s in out.subagents:
        s.error_signature = sig(s.error_signature)
    for p in out.permissions:
        p.target = scrub(p.target)
    out.session_errors = [_prefixed(e, sig) for e in out.session_errors]
    out.mcp_server_errors = [_prefixed(e, sig) for e in out.mcp_server_errors]
    out.hook_failures = [_prefixed(e, sig) for e in out.hook_failures]
    out.agent_definition_errors = [sig(e) or "" for e in out.agent_definition_errors]
    for c in out.corrections:
        c.excerpt = scrub(c.excerpt)
    if level in ("metrics", "strict"):
        for t in out.tool_calls:
            t.error_excerpt = None
            t.command_excerpt = None
            t.recovered_by_detail = None
        for c in out.corrections:
            c.excerpt = None
    if level == "redacted" and rec.privacy_level == "redacted":
        out.privacy_level = "redacted"
    else:
        out.privacy_level = "metrics" if level == "redacted" else level
    return out


def privacy_copies(
    records: list[SessionRecord], cfg: Config, level: str | None = None, salt: str | None = None
) -> list[SessionRecord]:
    """Copies prêtes à quitter le poste (même traitement pour ``export`` et ``export --preview``)."""
    level = level or cfg.export_privacy
    salt = salt or local_salt()
    repo_salt = shared_salt(cfg)
    return [apply_privacy(r, level, salt, cfg.hash_repositories, cfg.redaction_patterns, repo_salt) for r in records]


def export_bundle(
    records: list[SessionRecord], path: Path, cfg: Config, level: str | None = None, salt: str | None = None
) -> int:
    level = level or cfg.export_privacy
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
        for out in privacy_copies(records, cfg, level, salt):
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
