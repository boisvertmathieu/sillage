"""Opportunités d'amélioration et leçons apprises — le carburant de l'auto-amélioration.

Chaque règle est déterministe et explicable : elle cite ses preuves (compteurs,
sessions concernées) et indique **où** corriger (SKILL.md, agent, AGENTS.md du dépôt,
configuration de permissions, serveur MCP, hook...). Un agent « coach » (voir
``copilot/agents/sillage-coach.agent.md``) peut ensuite rédiger le correctif, qui
passe par vos garde-fous et revues habituels avant fusion.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sillage.analyze import Analysis, ErrorPattern
from sillage.classify import classify, display
from sillage.config import InsightThresholds

CATEGORY_LABELS = {
    "skill": "Skill",
    "agent": "Agent / sous-agent",
    "instructions": "Instructions de dépôt",
    "guardrail": "Garde-fou",
    "permissions": "Permissions",
    "mcp": "Serveur MCP",
    "environment": "Environnement",
    "context": "Contexte / tokens",
}


@dataclass
class Opportunity:
    id: str
    category: str
    target: str
    title: str
    evidence: list[str]
    action: str
    where: str
    sessions: int
    users: int
    priority: float
    repositories: list[str] = field(default_factory=list)
    excerpts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Lesson:
    scope: str  # "repo:<nom>" | "skill:<nom>" | "global"
    text: str
    occurrences: int
    sessions: int
    users: int
    last_seen: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _oid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


# ------------------------------------------------- conseils par type d'erreur
def advice_for(signature: str) -> tuple[str, str]:
    """(catégorie, action pour l'humain) — fonctionne aussi sur les signatures strictes."""
    rule = classify(signature)
    return rule.category, rule.advice


def agent_hint(signature: str) -> str:
    return classify(signature).hint


def is_expected_failure(p: ErrorPattern) -> bool:
    """Les tests rouges font partie du cycle normal (TDD) : on ne les traite pas comme irritants."""
    return classify(p.signature).id == "tests"


def _short(signature: str, n: int = 90) -> str:
    text = display(signature)
    return text if len(text) <= n + 4 else text[: n + 1] + "… »"


# ------------------------------------------------------------------- règles
def opportunities(a: Analysis, th: InsightThresholds | None = None) -> list[Opportunity]:
    th = th or InsightThresholds()
    out: list[Opportunity] = []
    ov = a.overview
    if not ov.get("sessions"):
        return out
    total_sessions = ov["sessions"]

    def add(o: Opportunity) -> None:
        out.append(o)

    # R1 — erreurs d'outils récurrentes
    for p in a.errors:
        if len(p.sessions) < th.min_sessions or is_expected_failure(p):
            continue
        category, advice = advice_for(p.signature)
        repos = [r for r, _ in p.repositories.most_common(3)]
        where = f"AGENTS.md de {repos[0]}" if len(p.repositories) == 1 else "instructions communes / skill concerné"
        if p.category == "mcp":
            category, where = "mcp", f"serveur MCP ({p.tool})"
        what = f"`{p.target}`" if p.target else f"l'outil `{p.tool}`"
        rec = p.top_recovery()
        if rec:
            advice = f"Contournement observé {rec[1]} fois : `{rec[0]}`. " + advice
        add(
            Opportunity(
                id=_oid("err", p.signature, p.tool, p.target or ""),
                category=category,
                target=p.target or p.tool,
                title=f"Échec récurrent de {what} : {_short(p.signature)}",
                evidence=[
                    f"{p.occurrences} échecs dans {len(p.sessions)} sessions, {len(p.users)} personne(s)",
                    f"Dépôts : {', '.join(repos) or 'n/d'}",
                ],
                action=advice,
                where=where,
                sessions=len(p.sessions),
                users=len(p.users),
                priority=len(p.sessions) * 2 + p.occurrences * 0.5 + len(p.users) * 2,
                repositories=repos,
                excerpts=p.excerpts,
            )
        )

    # R2 — skills générateurs de friction
    for s in a.skills:
        correction_rate = s["corrections_after"] / s["sessions"] if s["sessions"] else 0.0
        if s["sessions"] >= th.min_sessions and (
            s["friction_lift"] >= th.skill_friction_lift
            or (s["corrections_after"] >= th.min_sessions and correction_rate >= th.skill_correction_rate)
        ):
            add(
                Opportunity(
                    id=_oid("skill-friction", s["skill"]),
                    category="skill",
                    target=s["skill"],
                    title=f"Le skill « {s['skill']} » est souvent corrigé par les utilisateurs",
                    evidence=[
                        f"{s['corrections_after']} corrections/interruptions dans les échanges qui suivent son invocation "
                        f"({correction_rate:.1f} par session)",
                        f"Friction moyenne {s['mean_friction']} vs {ov['mean_friction']} (écart {s['friction_lift']:+})",
                        f"Taux d'échec d'outils dans ces sessions : {_pct(s['tool_failure_rate'])}",
                        f"{s['sessions']} sessions, {s['users']} personne(s)",
                    ],
                    action="Relire les corrections des utilisateurs, préciser les étapes, les valeurs par défaut et les "
                    "cas limites du skill ; ajouter un cas d'évaluation pour chaque correction récurrente.",
                    where=f"SKILL.md de « {s['skill']} »",
                    sessions=s["sessions"],
                    users=s["users"],
                    priority=s["sessions"] * 1.5 + max(0.0, s["friction_lift"]) + s["corrections_after"] * 1.5,
                    excerpts=s["excerpts"],
                )
            )

    # R3 — skills dormants (chargés partout, presque jamais utilisés)
    for s in a.skills:
        rate = s["activation_rate"]
        if (
            s["sessions_available"] >= max(10, th.min_sessions)
            and rate is not None
            and rate <= th.dormant_activation_rate
        ):
            add(
                Opportunity(
                    id=_oid("skill-dormant", s["skill"]),
                    category="skill",
                    target=s["skill"],
                    title=f"Skill dormant : « {s['skill']} » est rarement déclenché",
                    evidence=[
                        f"Disponible dans {s['sessions_available']} sessions, utilisé dans {s['sessions']} "
                        f"({_pct(rate)})"
                    ],
                    action="Réécrire la description (déclencheurs explicites : quand l'utiliser, mots-clés métier) ou "
                    "retirer le skill s'il n'est plus utile — chaque skill chargé coûte du contexte.",
                    where=f"en-tête (description) du SKILL.md de « {s['skill']} »",
                    sessions=s["sessions_available"],
                    users=0,
                    priority=s["sessions_available"] * 0.2,
                )
            )

    # R4 — serveurs MCP peu fiables
    for m in a.mcp:
        if m["startup_errors"] >= th.min_sessions or m["failed_status_sessions"] >= th.min_sessions:
            add(
                Opportunity(
                    id=_oid("mcp-start", m["server"]),
                    category="mcp",
                    target=m["server"],
                    title=f"Le serveur MCP « {m['server']} » échoue souvent au démarrage",
                    evidence=[
                        f"{m['startup_errors']} erreurs de démarrage, {m['failed_status_sessions']} sessions sans le serveur",
                        f"Erreur la plus fréquente : {m['top_startup_error'] or 'n/d'}",
                    ],
                    action="Vérifier l'installation, les variables d'environnement et l'authentification ; ajouter un "
                    "diagnostic au démarrage et une procédure de dépannage.",
                    where="mcp-config.json / paquet du serveur MCP",
                    sessions=max(m["startup_errors"], m["failed_status_sessions"]),
                    users=0,
                    priority=max(m["startup_errors"], m["failed_status_sessions"]) * 1.5,
                )
            )
        if m["calls"] >= 10 and m["failure_rate"] >= th.mcp_failure_rate:
            add(
                Opportunity(
                    id=_oid("mcp-fail", m["server"]),
                    category="mcp",
                    target=m["server"],
                    title=f"Appels au serveur MCP « {m['server']} » souvent en échec",
                    evidence=[f"{m['failures']}/{m['calls']} appels en échec ({_pct(m['failure_rate'])})"],
                    action="Analyser les erreurs par outil MCP, améliorer les messages d'erreur (actionnables pour un "
                    "agent) et la validation des paramètres.",
                    where=f"serveur MCP « {m['server']} »",
                    sessions=m["sessions_used"],
                    users=0,
                    priority=m["failures"] * 0.8,
                )
            )

    # R5 — sous-agents / agents personnalisés en échec
    for s in a.subagents:
        if s["runs"] >= 5 and s["failure_rate"] >= th.subagent_failure_rate:
            add(
                Opportunity(
                    id=_oid("subagent", s["agent"]),
                    category="agent",
                    target=s["agent"],
                    title=f"L'agent « {s['agent']} » échoue dans {_pct(s['failure_rate'])} des cas",
                    evidence=[
                        f"{s['failures']}/{s['runs']} exécutions en échec, durée médiane {s['median_duration_s']} s",
                        f"Erreur la plus fréquente : {s['top_error'] or 'n/d'}",
                    ],
                    action="Restreindre le périmètre et les outils de l'agent, préciser le format de sortie attendu, "
                    "ajouter des critères d'arrêt explicites.",
                    where=f"définition .agent.md de « {s['agent']} »",
                    sessions=s["runs"],
                    users=0,
                    priority=s["failures"] * 2,
                )
            )
    for d in a.agent_definition_errors:
        add(
            Opportunity(
                id=_oid("agentdef", d["error"]),
                category="agent",
                target="définitions d'agents",
                title=f"Définition d'agent invalide : {d['error'][:90]}",
                evidence=[f"Erreur de chargement vue dans {d['sessions']} sessions"],
                action="Corriger le fichier .agent.md (en-tête YAML, outils inexistants) ; ajouter une validation en CI.",
                where="dossier des agents (.github/agents ou ~/.copilot/agents)",
                sessions=d["sessions"],
                users=0,
                priority=d["sessions"] * 1.2,
            )
        )

    # R6 — permissions : demandes systématiquement approuvées (bruit) ou refusées (garde-fou manquant)
    for p in a.permissions:
        if p["kind"] == "shell" and p["approved_once"] >= th.allowlist_min_approvals and p["denied_by_user"] == 0:
            add(
                Opportunity(
                    id=_oid("allow", p["target"]),
                    category="permissions",
                    target=p["target"],
                    title=f"`{p['target']}` est approuvé manuellement à répétition",
                    evidence=[f"{p['approved_once']} approbations ponctuelles, 0 refus, {p['users']} personne(s)"],
                    action="Si la commande est en lecture seule, l'autoriser par défaut dans le profil d'équipe "
                    f"(`copilot --allow-tool='shell({p['target']})'`) : moins d'interruptions.",
                    where="profil de permissions partagé (alias / script de lancement Copilot)",
                    sessions=p["sessions"],
                    users=p["users"],
                    priority=p["approved_once"] * 0.3,
                )
            )
        if p["denied_by_user"] >= th.min_sessions:
            add(
                Opportunity(
                    id=_oid("deny", p["kind"], p["target"]),
                    category="guardrail",
                    target=p["target"],
                    title=f"Les utilisateurs refusent souvent `{p['target']}` ({p['kind']})",
                    evidence=[f"{p['denied_by_user']} refus interactifs sur {p['requests']} demandes"],
                    action="Transformer ce refus humain en garde-fou déterministe (règle `--deny-tool`, hook preToolUse) "
                    "et ajouter l'interdiction dans les instructions pour que l'agent ne la tente plus.",
                    where="hooks de garde-fous / instructions communes",
                    sessions=p["sessions"],
                    users=p["users"],
                    priority=p["denied_by_user"] * 1.5 + p["users"],
                )
            )

    # R7 — hooks (garde-fous) cassés
    for h in a.hooks:
        if h["sessions"] >= th.min_sessions:
            add(
                Opportunity(
                    id=_oid("hook", h["failure"]),
                    category="guardrail",
                    target=h["failure"].split(":")[0],
                    title=f"Un hook échoue de façon répétée : {h['failure'][:90]}",
                    evidence=[
                        f"{h['count']} échecs dans {h['sessions']} sessions, {h['users']} personne(s) "
                        f"({h['hook_runs_total']} exécutions de hooks au total)"
                    ],
                    action="Un garde-fou qui plante ne protège plus : corriger le script (dépendances, chemins) et "
                    "ajouter un test du hook en CI.",
                    where="configuration des hooks (.github/hooks)",
                    sessions=h["sessions"],
                    users=h["users"],
                    priority=h["sessions"] * 3 + h["users"] * 2,
                )
            )

    # R8 — dépôts à friction élevée / boucles d'échec
    for r in a.repositories:
        if r["repository"] == "(hors dépôt)" or r["sessions"] < th.min_sessions:
            continue
        if r["failure_loops"] >= th.min_sessions or r["mean_friction"] >= ov["mean_friction"] + 10:
            add(
                Opportunity(
                    id=_oid("repo", r["repository"]),
                    category="instructions",
                    target=r["repository"],
                    title=f"Le dépôt {r['repository']} génère beaucoup de friction",
                    evidence=[
                        f"Friction moyenne {r['mean_friction']} (moyenne globale {ov['mean_friction']})",
                        f"{r['failure_loops']} boucles d'échec, {r['corrections']} corrections, "
                        f"≈ {r['hours_lost']} h perdues (estimation)",
                    ],
                    action="Créer ou compléter AGENTS.md / copilot-instructions.md : commandes de build/test exactes, "
                    "structure, conventions ; y injecter les leçons Sillage (`sillage lessons --write`).",
                    where=f"AGENTS.md de {r['repository']}",
                    sessions=r["sessions"],
                    users=r["users"],
                    priority=r["sessions"] + r["failure_loops"] * 2 + r["hours_lost"] * 2,
                    repositories=[r["repository"]],
                )
            )

    # R9 — contexte : définitions d'outils trop lourdes / compactions fréquentes
    share = a.context.get("mean_tool_definitions_share")
    if share is not None and share >= th.tool_definitions_share:
        heavy = [m["server"] for m in a.mcp if m["sessions_loaded"] and m["sessions_used"] / m["sessions_loaded"] < 0.2]
        add(
            Opportunity(
                id=_oid("context-tools"),
                category="context",
                target="définitions d'outils",
                title=f"Les définitions d'outils occupent {_pct(share)} de la fenêtre de contexte",
                evidence=[
                    f"≈ {a.context['mean_tool_definitions_tokens']} tokens par session avant tout travail",
                    f"Serveurs MCP chargés mais rarement utilisés : {', '.join(heavy) or 'aucun'}",
                ],
                action="Charger les serveurs MCP par profil de tâche plutôt que globalement, limiter `tools` par serveur, "
                "privilégier les skills (divulgation progressive).",
                where="mcp-config.json / profils de lancement",
                sessions=total_sessions,
                users=ov["users"],
                priority=share * 100,
            )
        )
    big_outputs = [c for c in a.shell_commands if c["calls"] >= 5 and c["mean_output_chars"] >= 8000]
    for c in big_outputs[:5]:
        add(
            Opportunity(
                id=_oid("bigout", c["command"]),
                category="context",
                target=c["command"],
                title=f"`{c['command']}` renvoie en moyenne {c['mean_output_chars']:,} caractères au modèle".replace(
                    ",", " "
                ),
                evidence=[f"{c['calls']} appels, {c['output_chars']:,} caractères cumulés".replace(",", " ")],
                action="Filtrer/compresser la sortie (options silencieuses, `| tail`, outil de compression via hook "
                "preToolUse) : moins de tokens, moins de dilution du contexte.",
                where="instructions communes / hook de compression",
                sessions=c["calls"],
                users=0,
                priority=c["output_chars"] / 20000,
            )
        )

    # tri, k-anonymat sur les extraits
    for o in out:
        if not a.shareable(o.users if o.users else a.k_anonymity):
            o.excerpts = []
    return sorted(out, key=lambda o: -o.priority)


# ------------------------------------------------------------------- leçons
def lessons(
    a: Analysis, th: InsightThresholds | None = None, repository: str | None = None, skill: str | None = None
) -> list[Lesson]:
    """Leçons factuelles, réinjectables dans le contexte des futures sessions."""
    th = th or InsightThresholds()
    min_sessions = max(2, th.min_sessions - 1)
    out: list[Lesson] = []
    for p in a.errors:
        if len(p.sessions) < min_sessions or is_expected_failure(p):
            continue
        what = f"`{p.target}`" if p.target else f"l'outil `{p.tool}`"
        rec = p.top_recovery()
        if rec:
            text = f"{what} échoue souvent ({display(p.signature)}) : utiliser plutôt `{rec[0]}` (solution observée {rec[1]} fois)."
        else:
            text = f"{what} échoue souvent ({display(p.signature)}). {agent_hint(p.signature)}"
        repos = list(p.repositories)
        scopes = [f"repo:{r}" for r in repos] if len(repos) == 1 else ["global"]
        for scope in scopes:
            out.append(Lesson(scope, text, p.occurrences, len(p.sessions), len(p.users), p.last_seen))
    for perm in a.permissions:
        if perm["denied_by_user"] >= min_sessions:
            out.append(
                Lesson(
                    "global",
                    f"Ne pas tenter `{perm['target']}` : refusé {perm['denied_by_user']} fois par les "
                    "utilisateurs. Demander d'abord ou proposer une alternative.",
                    perm["denied_by_user"],
                    perm["denied_by_user"],
                    perm["users"],
                )
            )
    for s in a.skills:
        rate = s["corrections_after"] / s["sessions"] if s["sessions"] else 0.0
        if s["corrections_after"] >= th.min_sessions and rate >= th.skill_correction_rate:
            extra = f" Exemples de corrections : {' / '.join(s['excerpts'][:2])}" if s["excerpts"] else ""
            out.append(
                Lesson(
                    f"skill:{s['skill']}",
                    f"Le skill « {s['skill']} » a été corrigé {s['corrections_after']} fois par les "
                    f"utilisateurs : valider les hypothèses (paramètres, cibles) avant d'agir.{extra}",
                    s["corrections_after"],
                    s["sessions"],
                    s["users"],
                )
            )
    if repository:
        out = [lsn for lsn in out if lsn.scope in (f"repo:{repository}", "global")]
    if skill:
        out = [lsn for lsn in out if lsn.scope in (f"skill:{skill}", "global")]
    return sorted(out, key=lambda lsn: (-lsn.sessions, -lsn.occurrences))


MARK_START = "<!-- sillage:lessons:start -->"
MARK_END = "<!-- sillage:lessons:end -->"


def render_lessons_block(items: list[Lesson], title: str = "Leçons apprises des sessions Copilot") -> str:
    lines = [
        MARK_START,
        f"## {title}",
        "",
        "_Section générée par Sillage à partir des sessions de l'équipe — ne pas éditer à la main._",
        "",
    ]
    if not items:
        lines.append("- Aucune leçon récurrente pour l'instant.")
    for lsn in items:
        lines.append(f"- {lsn.text} _(vu {lsn.occurrences} fois, {lsn.sessions} sessions)_")
    lines.append(MARK_END)
    return "\n".join(lines) + "\n"


def write_lessons_block(path: Path, block: str) -> bool:
    """Insère ou remplace le bloc entre marqueurs (idempotent). Retourne True si le fichier a changé."""
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    if MARK_START in original and MARK_END in original:
        before, _, rest = original.partition(MARK_START)
        _, _, after = rest.partition(MARK_END)
        updated = before + block.rstrip("\n") + after
    else:
        sep = "" if not original else ("\n" if original.endswith("\n") else "\n\n")
        updated = original + sep + block
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False
