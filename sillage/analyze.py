"""Agrégation des sessions en indicateurs.

Principe éthique (non négociable) : **aucun classement ni tableau par personne**.
Les indicateurs servent à améliorer les outils (skills, agents, instructions,
environnements), pas à évaluer des individus. Les extraits ne sont affichés que
s'ils concernent au moins *k* personnes distinctes (k-anonymat) dans un rapport
d'équipe.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean, median

from sillage.model import SessionRecord

APPROVED = {"approved", "approved-for-session", "approved-for-location"}
DENIED_BY_USER = {"denied-interactively-by-user"}
DENIED_BY_POLICY = {"denied-by-rules", "denied-by-permission-request-hook", "denied-by-content-exclusion-policy"}


def _avg(values: list[float]) -> float:
    return round(mean(values), 1) if values else 0.0


def _med(values: list[float]) -> float:
    return round(median(values), 1) if values else 0.0


@dataclass
class ErrorPattern:
    signature: str
    tool: str
    target: str | None
    category: str
    occurrences: int = 0
    sessions: set[str] = field(default_factory=set)
    users: set[str] = field(default_factory=set)
    repositories: Counter = field(default_factory=Counter)
    excerpts: list[str] = field(default_factory=list)
    last_seen: str | None = None
    recoveries: Counter = field(default_factory=Counter)

    def top_recovery(self, min_count: int = 2) -> tuple[str, int] | None:
        if not self.recoveries:
            return None
        cmd, n = self.recoveries.most_common(1)[0]
        return (cmd, n) if n >= min_count else None


@dataclass
class Analysis:
    records: list[SessionRecord]
    k_anonymity: int = 2
    overview: dict = field(default_factory=dict)
    weekly: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    shell_commands: list[dict] = field(default_factory=list)
    mcp: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    subagents: list[dict] = field(default_factory=list)
    permissions: list[dict] = field(default_factory=list)
    errors: list[ErrorPattern] = field(default_factory=list)
    hooks: list[dict] = field(default_factory=list)
    repositories: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    agent_definition_errors: list[dict] = field(default_factory=list)

    @property
    def team_mode(self) -> bool:
        return self.overview.get("users", 0) > 1

    def shareable(self, users: set[str] | int) -> bool:
        n = users if isinstance(users, int) else len(users)
        return not self.team_mode or n >= self.k_anonymity


def analyze(records: list[SessionRecord], k_anonymity: int = 2) -> Analysis:
    a = Analysis(records=records, k_anonymity=k_anonymity)
    if not records:
        a.overview = {"sessions": 0, "users": 0}
        return a

    frictions = [r.friction_score for r in records]
    mean_friction = mean(frictions)
    calls = [t for r in records for t in r.tool_calls]
    starts = [r.start for r in records if r.start]
    labels = Counter(r.friction_label for r in records)
    a.overview = {
        "sessions": len(records),
        "users": len({r.user for r in records}),
        "repositories": len({r.repository for r in records if r.repository}),
        "period_start": min(starts) if starts else None,
        "period_end": max(starts) if starts else None,
        "active_hours": round(sum(r.active_s for r in records) / 3600, 1),
        "median_session_min": _med([r.duration_s / 60 for r in records]),
        "user_messages": sum(r.user_messages for r in records),
        "tool_calls": len(calls),
        "tool_failure_rate": round(sum(1 for t in calls if t.success is False) / len(calls), 3) if calls else 0.0,
        "corrections": sum(1 for r in records for c in r.corrections if c.category == "correction"),
        "reformulations": sum(1 for r in records for c in r.corrections if c.category == "reformulation"),
        "aborts": sum(r.aborts for r in records),
        "user_denials": sum(r.user_denials for r in records),
        "failure_loops": sum(r.failure_loops for r in records),
        "abandoned_rate": round(sum(1 for r in records if r.abandoned) / len(records), 3),
        "mean_friction": round(mean_friction, 1),
        "median_friction": _med(frictions),
        "friction_labels": {k: labels.get(k, 0) for k in ("fluide", "accrochages", "difficile", "pénible")},
        "estimated_hours_lost": round(sum(r.estimated_minutes_lost for r in records) / 60, 1),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "cache_read_tokens": sum(r.cache_read_tokens for r in records),
        "tool_output_chars": sum(t.output_chars or 0 for t in calls),
        "premium_requests": round(sum(r.premium_requests or 0 for r in records), 1),
        "lines_added": sum(r.lines_added for r in records),
        "lines_removed": sum(r.lines_removed for r in records),
        "skills_invocations": sum(1 for r in records for s in r.skills_invoked if s.trigger != "context-load"),
        "subagent_runs": sum(len(r.subagents) for r in records),
        "sessions_with_skill": sum(1 for r in records if r.skills_invoked),
        "compactions": sum(r.compactions for r in records),
    }

    # ----------------------------------------------------------- semaines
    weeks: dict[str, list[SessionRecord]] = defaultdict(list)
    for r in records:
        if r.start:
            y, w, _ = datetime.fromisoformat(r.start).isocalendar()
            weeks[f"{y}-S{w:02d}"].append(r)
    a.weekly = [
        {
            "week": wk,
            "sessions": len(rs),
            "users": len({r.user for r in rs}),
            "mean_friction": _avg([r.friction_score for r in rs]),
            "hours_lost": round(sum(r.estimated_minutes_lost for r in rs) / 60, 1),
        }
        for wk, rs in sorted(weeks.items())
    ]

    # -------------------------------------------------------------- outils
    by_tool: dict[str, list] = defaultdict(list)
    for t in calls:
        by_tool[t.name].append(t)
    a.tools = sorted(
        (
            {
                "tool": name,
                "category": ts[0].category,
                "calls": len(ts),
                "failures": sum(1 for t in ts if t.success is False),
                "failure_rate": round(sum(1 for t in ts if t.success is False) / len(ts), 3),
                "median_ms": _med([t.duration_ms for t in ts if t.duration_ms is not None]),
                "output_chars": sum(t.output_chars or 0 for t in ts),
            }
            for name, ts in by_tool.items()
        ),
        key=lambda d: -d["calls"],
    )

    by_cmd: dict[str, list] = defaultdict(list)
    for t in calls:
        if t.category == "shell" and t.target:
            by_cmd[t.target].append(t)
    a.shell_commands = sorted(
        (
            {
                "command": cmd,
                "calls": len(ts),
                "failures": sum(1 for t in ts if t.success is False),
                "failure_rate": round(sum(1 for t in ts if t.success is False) / len(ts), 3),
                "output_chars": sum(t.output_chars or 0 for t in ts),
                "mean_output_chars": int(_avg([t.output_chars for t in ts if t.output_chars is not None])),
            }
            for cmd, ts in by_cmd.items()
        ),
        key=lambda d: -d["calls"],
    )

    # ----------------------------------------------------------------- MCP
    mcp_calls: dict[str, list] = defaultdict(list)
    for t in calls:
        if t.mcp_server:
            mcp_calls[t.mcp_server].append(t)
    servers = set(mcp_calls) | {s for r in records for s in r.mcp_servers}
    rows = []
    for s in servers:
        ts = mcp_calls.get(s, [])
        startup_errors = [e for r in records for e in r.mcp_server_errors if e.startswith(s + ":")]
        loaded = [r for r in records if s in r.mcp_servers]
        rows.append(
            {
                "server": s,
                "sessions_loaded": len(loaded),
                "sessions_used": len({id(r) for r in records for t in r.tool_calls if t.mcp_server == s}),
                "calls": len(ts),
                "failures": sum(1 for t in ts if t.success is False),
                "failure_rate": round(sum(1 for t in ts if t.success is False) / len(ts), 3) if ts else 0.0,
                "startup_errors": len(startup_errors),
                "top_startup_error": Counter(startup_errors).most_common(1)[0][0] if startup_errors else None,
                "failed_status_sessions": sum(1 for r in loaded if r.mcp_servers.get(s) in {"failed", "error"}),
            }
        )
    a.mcp = sorted(rows, key=lambda d: (-d["calls"], -d["sessions_loaded"]))

    # -------------------------------------------------------------- skills
    skill_sessions: dict[str, list[SessionRecord]] = defaultdict(list)
    skill_triggers: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        for name in r.skill_names:
            skill_sessions[name].append(r)
        for s in r.skills_invoked:
            skill_triggers[s.name][s.trigger or "?"] += 1
    available: Counter = Counter(name for r in records for name in r.skills_available)
    skill_rows = []
    for name in set(skill_sessions) | set(available):
        rs = skill_sessions.get(name, [])
        avail = available.get(name, 0)
        corrections_after = sum(
            1 for r in records for c in r.corrections if c.after_skill == name and c.category != "reformulation"
        )
        excerpts = [
            c.excerpt
            for r in records
            for c in r.corrections
            if c.after_skill == name and c.excerpt and c.category == "correction"
        ]
        users = {r.user for r in rs}
        skill_rows.append(
            {
                "skill": name,
                "invocations": sum(skill_triggers[name].values()),
                "sessions": len(rs),
                "users": len(users),
                "triggers": dict(skill_triggers[name]),
                "sessions_available": avail,
                "activation_rate": round(len(rs) / avail, 3) if avail else None,
                "mean_friction": _avg([r.friction_score for r in rs]),
                "friction_lift": round(_avg([r.friction_score for r in rs]) - mean_friction, 1) if rs else 0.0,
                "corrections_after": corrections_after,
                "tool_failure_rate": round(
                    sum(r.tool_failures for r in rs) / max(1, sum(len(r.tool_calls) for r in rs)), 3
                ),
                "excerpts": excerpts[:5] if a.shareable(users) or not records else [],
            }
        )
    a.skills = sorted(skill_rows, key=lambda d: (-d["invocations"], -d["sessions_available"]))

    # ----------------------------------------------------------- sous-agents
    sub: dict[str, list] = defaultdict(list)
    for r in records:
        for s in r.subagents:
            sub[s.name].append(s)
    a.subagents = sorted(
        (
            {
                "agent": name,
                "runs": len(rs),
                "failures": sum(1 for s in rs if s.success is False),
                "failure_rate": round(sum(1 for s in rs if s.success is False) / len(rs), 3),
                "median_duration_s": _med([s.duration_ms / 1000 for s in rs if s.duration_ms]),
                "mean_tokens": int(_avg([s.total_tokens for s in rs if s.total_tokens])),
                "top_error": Counter(s.error_signature for s in rs if s.error_signature).most_common(1)[0][0]
                if any(s.error_signature for s in rs)
                else None,
            }
            for name, rs in sub.items()
        ),
        key=lambda d: -d["runs"],
    )
    defs: Counter = Counter(e for r in records for e in r.agent_definition_errors)
    a.agent_definition_errors = [{"error": e, "sessions": n} for e, n in defs.most_common()]

    # ---------------------------------------------------------- permissions
    perms: dict[tuple[str, str], Counter] = defaultdict(Counter)
    perm_users: dict[tuple[str, str], set[str]] = defaultdict(set)
    perm_sessions: Counter = Counter()
    for r in records:
        for key in {(p.kind, p.target or "?") for p in r.permissions}:
            perm_sessions[key] += 1
        for p in r.permissions:
            key = (p.kind, p.target or "?")
            perms[key][p.result] += 1
            perm_users[key].add(r.user)
    a.permissions = sorted(
        (
            {
                "kind": k[0],
                "target": k[1],
                "requests": sum(c.values()),
                "approved": sum(v for res, v in c.items() if res in APPROVED),
                "approved_once": c.get("approved", 0),
                "denied_by_user": sum(v for res, v in c.items() if res in DENIED_BY_USER),
                "denied_by_policy": sum(v for res, v in c.items() if res in DENIED_BY_POLICY),
                "users": len(perm_users[k]),
                "sessions": perm_sessions[k],
            }
            for k, c in perms.items()
        ),
        key=lambda d: -d["requests"],
    )

    # ------------------------------------------------- erreurs récurrentes
    patterns: dict[tuple, ErrorPattern] = {}
    for r in records:
        for t in r.tool_calls:
            if t.success is not False or not t.error_signature:
                continue
            key = (t.error_signature, t.name, t.target)
            p = patterns.get(key)
            if p is None:
                p = patterns[key] = ErrorPattern(t.error_signature, t.name, t.target, t.category)
            p.occurrences += 1
            p.sessions.add(r.session_id)
            p.users.add(r.user)
            if t.recovered_by_detail or t.recovered_by:
                p.recoveries[t.recovered_by_detail or t.recovered_by] += 1
            if r.repository:
                p.repositories[r.repository] += 1
            if t.error_excerpt and len(p.excerpts) < 3 and t.error_excerpt not in p.excerpts:
                p.excerpts.append(t.error_excerpt)
            if r.start and (p.last_seen is None or r.start > p.last_seen):
                p.last_seen = r.start
    for p in patterns.values():
        if not a.shareable(p.users):
            p.excerpts = []
    a.errors = sorted(patterns.values(), key=lambda p: (-len(p.sessions), -p.occurrences))

    # ----------------------------------------------------------------- hooks
    hk: Counter = Counter(h for r in records for h in r.hook_failures)
    hk_sessions: Counter = Counter(h for r in records for h in set(r.hook_failures))
    hk_users: dict[str, set[str]] = defaultdict(set)
    for r in records:
        for h in r.hook_failures:
            hk_users[h].add(r.user)
    total_hook_runs = sum(r.hook_runs for r in records)
    a.hooks = [
        {
            "failure": h,
            "count": n,
            "sessions": hk_sessions[h],
            "users": len(hk_users[h]),
            "hook_runs_total": total_hook_runs,
        }
        for h, n in hk.most_common()
    ]

    # ------------------------------------------------------------- dépôts
    by_repo: dict[str, list[SessionRecord]] = defaultdict(list)
    for r in records:
        by_repo[r.repository or "(hors dépôt)"].append(r)
    a.repositories = sorted(
        (
            {
                "repository": repo,
                "sessions": len(rs),
                "users": len({r.user for r in rs}),
                "mean_friction": _avg([r.friction_score for r in rs]),
                "corrections": sum(1 for r in rs for c in r.corrections if c.category == "correction"),
                "tool_failure_rate": round(
                    sum(r.tool_failures for r in rs) / max(1, sum(len(r.tool_calls) for r in rs)), 3
                ),
                "failure_loops": sum(r.failure_loops for r in rs),
                "compaction_rate": round(sum(1 for r in rs if r.compactions or r.truncations) / len(rs), 3),
                "hours_lost": round(sum(r.estimated_minutes_lost for r in rs) / 60, 1),
            }
            for repo, rs in by_repo.items()
        ),
        key=lambda d: -d["sessions"],
    )

    # ------------------------------------------------------------- modèles
    mc: Counter = Counter()
    for r in records:
        mc.update(r.models)
    a.models = [{"model": m, "calls": n} for m, n in mc.most_common()]

    # ------------------------------------------------------------ contexte
    shares = [
        r.tool_definitions_tokens / r.context_token_limit
        for r in records
        if r.tool_definitions_tokens and r.context_token_limit
    ]
    a.context = {
        "sessions_with_compaction": sum(1 for r in records if r.compactions or r.truncations),
        "mean_tool_definitions_tokens": int(
            _avg([r.tool_definitions_tokens for r in records if r.tool_definitions_tokens])
        ),
        "mean_tool_definitions_share": round(mean(shares), 3) if shares else None,
        "cache_hit_ratio": round(a.overview["cache_read_tokens"] / a.overview["input_tokens"], 3)
        if a.overview["input_tokens"]
        else None,
    }
    return a
