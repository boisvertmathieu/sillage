"""Modèle normalisé d'une session Copilot CLI.

Ces structures sont la frontière de confidentialité : tout ce qui sort de la machine
(bundle d'export) passe par elles, au niveau de confidentialité choisi.
Aucun prompt, aucun contenu de fichier, aucune sortie d'outil brute n'y est conservé.
Seuls des compteurs, des noms d'outils/skills/agents, des signatures d'erreurs
normalisées et (au niveau ``redacted``) de courts extraits caviardés.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class ToolCall:
    name: str
    category: str  # shell | file | search | mcp | subagent | skill | web | other
    success: bool | None = None
    mcp_server: str | None = None
    target: str | None = None  # commande shell normalisée, extension de fichier, outil MCP
    exit_code: int | None = None
    error_signature: str | None = None
    error_excerpt: str | None = None  # niveau "redacted" seulement
    duration_ms: int | None = None
    output_chars: int | None = None  # taille du résultat renvoyé au modèle (coût en contexte)
    recovered_by: str | None = None  # commande qui a réussi juste après cet échec (contournement observé)
    recovered_by_detail: str | None = None  # idem, commande complète caviardée (niveau "redacted")
    command_excerpt: str | None = None  # commande shell complète caviardée (niveau "redacted")
    subagent: bool = False  # appel effectué par un sous-agent
    user_requested: bool = False


@dataclass
class SkillUse:
    name: str
    trigger: str | None = None  # user-invoked | agent-invoked | context-load
    source: str | None = None
    plugin: str | None = None


@dataclass
class SubagentRun:
    name: str
    success: bool | None = None
    duration_ms: int | None = None
    total_tokens: int | None = None
    total_tool_calls: int | None = None
    error_signature: str | None = None


@dataclass
class PermissionDecision:
    kind: str  # shell | write | read | mcp | url | ...
    result: str  # approved | approved-for-session | denied-interactively-by-user | ...
    target: str | None = None  # identifiant de commande, serveur MCP...
    read_only: bool | None = None  # commande jugée en lecture seule par Copilot


@dataclass
class Correction:
    category: str  # correction | reformulation | interruption
    after_skill: str | None = None
    excerpt: str | None = None  # niveau "redacted" seulement


@dataclass
class SessionRecord:
    session_id: str  # identifiant pseudonymisé
    user: str  # pseudonyme
    start: str | None = None  # ISO 8601
    end: str | None = None
    duration_s: float = 0.0
    active_s: float = 0.0
    copilot_version: str | None = None
    repository: str | None = None
    branch_kind: str | None = None  # main | feature | other (pas le nom réel)
    models: dict[str, int] = field(default_factory=dict)  # modèle -> nb d'appels
    user_messages: int = 0
    assistant_turns: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    skills_invoked: list[SkillUse] = field(default_factory=list)
    skills_available: list[str] = field(default_factory=list)
    custom_agents: list[str] = field(default_factory=list)
    agent_definition_errors: list[str] = field(default_factory=list)
    selected_agent: str | None = None
    subagents: list[SubagentRun] = field(default_factory=list)
    mcp_servers: dict[str, str] = field(default_factory=dict)  # serveur -> dernier statut
    mcp_server_errors: list[str] = field(default_factory=list)  # signatures
    permissions: list[PermissionDecision] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    aborts: int = 0
    session_errors: list[str] = field(default_factory=list)  # signatures
    model_failures: int = 0
    hook_runs: int = 0
    hook_failures: list[str] = field(default_factory=list)  # "hookType: signature"
    compactions: int = 0
    truncations: int = 0
    tool_definitions_tokens: int | None = None
    context_token_limit: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    premium_requests: float | None = None
    nano_aiu: int | None = None  # consommation en nano-unités IA (totalNanoAiu)
    lines_added: int = 0
    lines_removed: int = 0
    files_modified: int = 0
    task_completed: bool | None = None
    plan_rejections: int = 0
    shutdown_type: str | None = None
    abandoned: bool = False
    failure_loops: int = 0
    friction_score: float = 0.0
    friction_label: str = "fluide"
    estimated_minutes_lost: float = 0.0
    unknown_event_types: list[str] = field(default_factory=list)
    replayed_events: int = 0  # événements « checkpoint » ignorés (déjà présents dans l'historique d'origine)
    privacy_level: str = "metrics"

    # ------------------------------------------------------------------ helpers
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        nested = {
            "tool_calls": ToolCall,
            "skills_invoked": SkillUse,
            "subagents": SubagentRun,
            "permissions": PermissionDecision,
            "corrections": Correction,
        }
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue  # tolérance aux versions futures
            if key in nested and isinstance(value, list):
                sub = nested[key]
                sub_known = {f.name for f in fields(sub)}
                value = [sub(**{k: v for k, v in item.items() if k in sub_known}) for item in value]
            kwargs[key] = value
        return cls(**kwargs)

    # Quelques dérivés pratiques
    @property
    def tool_failures(self) -> int:
        return sum(1 for t in self.tool_calls if t.success is False)

    @property
    def user_denials(self) -> int:
        return sum(1 for p in self.permissions if p.result == "denied-interactively-by-user")

    @property
    def skill_names(self) -> list[str]:
        return sorted({s.name for s in self.skills_invoked})
