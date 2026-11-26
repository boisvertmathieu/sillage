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
from sillage.redact import compile_patterns

#: Niveaux d'export, du plus protecteur au plus détaillé.
EXPORT_LEVELS = ("strict", "metrics", "redacted")

SAMPLE_CONFIG = """\
# Configuration Sillage — toutes les clés sont optionnelles.

[sillage]
# Niveau de confidentialité du magasin local : "redacted" (extraits caviardés) ou "metrics".
local_privacy = "redacted"
# Niveau par défaut des bundles exportés vers l'équipe :
#   "strict"   (défaut) : aucun texte issu des messages d'erreur ; seulement un type d'erreur et une empreinte ;
#   "metrics"  : signatures d'erreurs normalisées et caviardées (plus parlantes, risque résiduel) ;
#   "redacted" : en plus, courts extraits caviardés (déconseillé si les sessions touchent la production).
export_privacy = "strict"
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

[confidentialite]
# Motifs (regex Python) propres à l'organisation, remplacés par [CONFIDENTIEL] à la collecte ET à
# l'export : noms d'instances, de namespaces, formats de numéros de dossier ou de client, etc.
# Exemples :
#   motifs = ['\\bdpprod\\b', '\\bprod-[a-z0-9-]+\\b', '\\bDOS-\\d{6}\\b', '\\b[A-Z]{2,} [A-Z][a-z]+\\b']
motifs = []

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
    export_privacy: str = "strict"
    salt_env: str = "SILLAGE_SALT"
    hash_repositories: bool = False
    k_anonymity: int = 2
    copilot_homes: list[str] = field(default_factory=list)
    extra_correction_patterns: list[str] = field(default_factory=list)
    redaction_patterns: list[str] = field(default_factory=list)
    thresholds: InsightThresholds = field(default_factory=InsightThresholds)
    friction: FrictionWeights = field(default_factory=FrictionWeights)
    source: str | None = None


def _apply(obj: object, values: dict) -> None:
    names = {f.name for f in fields(obj)}  # type: ignore[arg-type]
    for k, v in values.items():
        if k in names:
            setattr(obj, k, v)


def load_config(path: str | None = None) -> Config:
    try:
        user_cfg = str(Path.home() / ".config" / "sillage" / "sillage.toml")
    except RuntimeError:  # répertoire personnel indéterminable (environnement minimal)
        user_cfg = None
    candidates = [path, os.environ.get("SILLAGE_CONFIG"), "sillage.toml", user_cfg]
    cfg = Config()
    for cand in candidates:
        if cand and Path(cand).is_file():
            with open(cand, "rb") as fh:
                data = tomllib.load(fh)
            _apply(cfg, data.get("sillage", {}))
            _apply(cfg.thresholds, data.get("insights", {}))
            _apply(cfg.friction, data.get("friction", {}))
            cfg.extra_correction_patterns = list(data.get("corrections", {}).get("extra_patterns", []))
            cfg.redaction_patterns = list(data.get("confidentialite", {}).get("motifs", []))
            cfg.source = cand
            break
        if cand == path and path:
            raise FileNotFoundError(f"Fichier de configuration introuvable : {path}")
    if cfg.local_privacy not in ("metrics", "redacted"):
        raise ValueError(f"Niveau de confidentialité local invalide : {cfg.local_privacy!r} (metrics|redacted)")
    if cfg.export_privacy not in EXPORT_LEVELS:
        raise ValueError(f"Niveau d'export invalide : {cfg.export_privacy!r} ({'|'.join(EXPORT_LEVELS)})")
    compile_patterns(cfg.redaction_patterns)  # erreur claire si un motif est invalide
    return cfg
