"""Configuration (optionnelle) : ``sillage.toml``.

Ordre de recherche : ``--config``, ``$SILLAGE_CONFIG``, ``./sillage.toml``,
``~/.config/sillage/sillage.toml``. Tout a une valeur par défaut raisonnable :
Sillage fonctionne sans aucun fichier de configuration.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from sillage.normalize import FrictionWeights

SAMPLE_CONFIG = """\
# Configuration Sillage — toutes les clés sont optionnelles.

[sillage]
# Niveau de confidentialité du magasin local : "redacted" (extraits caviardés) ou "metrics".
local_privacy = "redacted"
# Niveau par défaut des bundles exportés vers l'équipe : "metrics" recommandé.
export_privacy = "metrics"
# Variable d'environnement contenant un sel partagé par l'équipe (pseudonymes stables
# d'une machine à l'autre). Sans sel partagé, un sel aléatoire local est utilisé.
salt_env = "SILLAGE_SALT"
# Masquer les noms de dépôts dans les exports (pseudonymes).
hash_repositories = false
# Seuil k-anonymat : un extrait ou une signature n'est affiché dans un rapport
# d'équipe que s'il concerne au moins k personnes distinctes.
k_anonymity = 2
# Répertoires Copilot supplémentaires (sinon : $COPILOT_HOME, ~/.copilot, /mnt/c/Users/*/.copilot).
copilot_homes = []

[insights]
# Nombre minimal de sessions concernées pour qu'un irritant devienne une opportunité.
min_sessions = 3
# Écart de friction (points) au-dessus de la moyenne pour signaler un skill.
skill_friction_lift = 10
# Corrections par session (dans les échanges suivant l'invocation) au-delà desquelles un skill est signalé.
skill_correction_rate = 0.5
# Taux d'activation sous lequel un skill chargé est jugé « dormant ».
dormant_activation_rate = 0.05
# Part de la fenêtre de contexte occupée par les définitions d'outils jugée excessive.
tool_definitions_share = 0.15

[corrections]
# Motifs supplémentaires (regex, texte en minuscules sans accents) signalant une correction.
extra_patterns = []

[friction]
# Poids du score de friction (total 100) et minutes perdues estimées par événement.
# corrections = 15
# min_per_correction = 2.0
"""


@dataclass
class InsightThresholds:
    min_sessions: int = 3
    skill_friction_lift: float = 10.0
    skill_correction_rate: float = 0.5
    dormant_activation_rate: float = 0.05
    tool_definitions_share: float = 0.15
    mcp_failure_rate: float = 0.2
    subagent_failure_rate: float = 0.2
    allowlist_min_approvals: int = 5


@dataclass
class Config:
    local_privacy: str = "redacted"
    export_privacy: str = "metrics"
    salt_env: str = "SILLAGE_SALT"
    hash_repositories: bool = False
    k_anonymity: int = 2
    copilot_homes: list[str] = field(default_factory=list)
    extra_correction_patterns: list[str] = field(default_factory=list)
    thresholds: InsightThresholds = field(default_factory=InsightThresholds)
    friction: FrictionWeights = field(default_factory=FrictionWeights)
    source: str | None = None


def _apply(obj: object, values: dict) -> None:
    names = {f.name for f in fields(obj)}  # type: ignore[arg-type]
    for k, v in values.items():
        if k in names:
            setattr(obj, k, v)


def load_config(path: str | None = None) -> Config:
    candidates = [
        path,
        os.environ.get("SILLAGE_CONFIG"),
        "sillage.toml",
        str(Path.home() / ".config" / "sillage" / "sillage.toml"),
    ]
    cfg = Config()
    for cand in candidates:
        if cand and Path(cand).is_file():
            with open(cand, "rb") as fh:
                data = tomllib.load(fh)
            _apply(cfg, data.get("sillage", {}))
            _apply(cfg.thresholds, data.get("insights", {}))
            _apply(cfg.friction, data.get("friction", {}))
            cfg.extra_correction_patterns = list(data.get("corrections", {}).get("extra_patterns", []))
            cfg.source = cand
            break
        if cand == path and path:
            raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")
    for level in (cfg.local_privacy, cfg.export_privacy):
        if level not in ("metrics", "redacted"):
            raise ValueError(f"Niveau de confidentialité invalide : {level!r} (metrics|redacted)")
    return cfg
