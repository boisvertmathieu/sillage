"""Rapports Markdown (français)."""

from __future__ import annotations

from sillage.analyze import Analysis
from sillage.insights import CATEGORY_LABELS, Lesson, Opportunity


def _n(x: float | int) -> str:
    if isinstance(x, float) and not x.is_integer():
        return f"{x:,.1f}".replace(",", " ").replace(".", ",")
    return f"{int(x):,}".replace(",", " ")


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x * 100:.0f} %"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def render_opportunities(opps: list[Opportunity], limit: int | None = None) -> str:
    lines = ["# Opportunités d'amélioration", ""]
    if not opps:
        return "\n".join(lines + ["Aucune opportunité au-dessus des seuils. 🎉", ""])
    for i, o in enumerate(opps[:limit] if limit else opps, 1):
        lines += [
            f"## {i}. {o.title}",
            "",
            f"- **Catégorie** : {CATEGORY_LABELS.get(o.category, o.category)} — **où corriger** : {o.where}",
            f"- **Preuves** : {' ; '.join(o.evidence)}",
            f"- **Action proposée** : {o.action}",
        ]
        if o.excerpts:
            lines.append("- **Extraits (caviardés)** : " + " / ".join(f"« {e} »" for e in o.excerpts[:3]))
        lines += [f"- Identifiant : `{o.id}` — priorité {o.priority:.0f}", ""]
    return "\n".join(lines)


def render_lessons(items: list[Lesson]) -> str:
    lines = ["# Leçons apprises", ""]
    if not items:
        return "\n".join(lines + ["Aucune leçon récurrente pour l'instant.", ""])
    for lsn in items:
        lines.append(
            f"- **[{lsn.scope}]** {lsn.text} _(vu {lsn.occurrences} fois, {lsn.sessions} sessions, {lsn.users} pers.)_"
        )
    return "\n".join(lines) + "\n"


def render_report(a: Analysis, opps: list[Opportunity], title: str = "Sillage — rapport des sessions Copilot") -> str:
    ov = a.overview
    if not ov.get("sessions"):
        return f"# {title}\n\nAucune session trouvée. Lancez `sillage collect` ou `sillage doctor`.\n"
    labels = ov["friction_labels"]
    lines = [
        f"# {title}",
        "",
        f"Période : {(ov['period_start'] or '')[:10]} → {(ov['period_end'] or '')[:10]} · "
        f"{_n(ov['sessions'])} sessions · {_n(ov['users'])} personne(s) · {_n(ov['repositories'])} dépôt(s)",
        "",
        "> Sillage mesure les **outils** (skills, agents, instructions, environnements), jamais les personnes : "
        "aucun classement individuel n'est produit.",
        "",
        "## En bref",
        "",
        _table(
            ["Indicateur", "Valeur"],
            [
                ["Heures actives avec Copilot", _n(ov["active_hours"])],
                ["Durée médiane d'une session", f"{_n(ov['median_session_min'])} min"],
                ["Appels d'outils (taux d'échec)", f"{_n(ov['tool_calls'])} ({_pct(ov['tool_failure_rate'])})"],
                [
                    "Sessions fluides / accrochages / difficiles / pénibles",
                    f"{labels['fluide']} / {labels['accrochages']} / {labels['difficile']} / {labels['pénible']}",
                ],
                [
                    "Corrections · reformulations · interruptions",
                    f"{_n(ov['corrections'])} · {_n(ov['reformulations'])} · {_n(ov['aborts'])}",
                ],
                ["Refus de permission par l'humain", _n(ov["user_denials"])],
                ["Boucles d'échec (même erreur ≥ 3 fois)", _n(ov["failure_loops"])],
                ["Temps perdu estimé (indicatif)", f"{_n(ov['estimated_hours_lost'])} h"],
                ["Tokens entrée / sortie", f"{_n(ov['input_tokens'])} / {_n(ov['output_tokens'])}"],
                ["Taux de cache (lecture / entrée)", _pct(a.context.get("cache_hit_ratio"))],
                ["Définitions d'outils (part du contexte)", _pct(a.context.get("mean_tool_definitions_share"))],
                ["Requêtes premium", _n(ov["premium_requests"])],
                ["Lignes ajoutées / retirées", f"{_n(ov['lines_added'])} / {_n(ov['lines_removed'])}"],
                ["Invocations de skills · sous-agents", f"{_n(ov['skills_invocations'])} · {_n(ov['subagent_runs'])}"],
            ],
        ),
        "",
        "## Top opportunités d'amélioration",
        "",
    ]
    if opps:
        lines.append(
            _table(
                ["#", "Catégorie", "Opportunité", "Où corriger", "Sessions"],
                [
                    [i, CATEGORY_LABELS.get(o.category, o.category), o.title, o.where, o.sessions]
                    for i, o in enumerate(opps[:10], 1)
                ],
            )
        )
        lines += ["", "Détail et actions : `sillage opportunities`.", ""]
    else:
        lines += ["Aucune opportunité au-dessus des seuils.", ""]

    if a.skills:
        lines += [
            "## Skills",
            "",
            _table(
                ["Skill", "Invocations", "Sessions", "Activation", "Corrections après", "Friction (écart)"],
                [
                    [
                        s["skill"],
                        s["invocations"],
                        s["sessions"],
                        _pct(s["activation_rate"]),
                        s["corrections_after"],
                        f"{s['mean_friction']} ({s['friction_lift']:+})",
                    ]
                    for s in a.skills
                ],
            ),
            "",
        ]
    if a.subagents:
        lines += [
            "## Agents et sous-agents",
            "",
            _table(
                ["Agent", "Exécutions", "Échecs", "Durée médiane", "Tokens moyens"],
                [
                    [
                        s["agent"],
                        s["runs"],
                        _pct(s["failure_rate"]),
                        f"{s['median_duration_s']} s",
                        _n(s["mean_tokens"]),
                    ]
                    for s in a.subagents
                ],
            ),
            "",
        ]
    if a.mcp:
        lines += [
            "## Serveurs MCP",
            "",
            _table(
                ["Serveur", "Chargé (sessions)", "Utilisé (sessions)", "Appels", "Échecs", "Erreurs démarrage"],
                [
                    [
                        m["server"],
                        m["sessions_loaded"],
                        m["sessions_used"],
                        m["calls"],
                        _pct(m["failure_rate"]),
                        m["startup_errors"],
                    ]
                    for m in a.mcp
                ],
            ),
            "",
        ]
    if a.shell_commands:
        lines += [
            "## Commandes shell les plus fréquentes",
            "",
            _table(
                ["Commande", "Appels", "Échecs", "Sortie moyenne (car.)"],
                [
                    [f"`{c['command']}`", c["calls"], _pct(c["failure_rate"]), _n(c["mean_output_chars"])]
                    for c in a.shell_commands[:12]
                ],
            ),
            "",
        ]
    if a.repositories:
        lines += [
            "## Dépôts",
            "",
            _table(
                ["Dépôt", "Sessions", "Friction moy.", "Corrections", "Échecs outils", "Boucles", "Compaction"],
                [
                    [
                        r["repository"],
                        r["sessions"],
                        r["mean_friction"],
                        r["corrections"],
                        _pct(r["tool_failure_rate"]),
                        r["failure_loops"],
                        _pct(r["compaction_rate"]),
                    ]
                    for r in a.repositories
                ],
            ),
            "",
        ]
    if a.models:
        lines += ["## Modèles", "", _table(["Modèle", "Appels"], [[m["model"], _n(m["calls"])] for m in a.models]), ""]
    if a.weekly:
        lines += [
            "## Tendance hebdomadaire",
            "",
            _table(
                ["Semaine", "Sessions", "Personnes", "Friction moy.", "Heures perdues (est.)"],
                [[w["week"], w["sessions"], w["users"], w["mean_friction"], w["hours_lost"]] for w in a.weekly],
            ),
            "",
        ]
    lines += [
        "---",
        "_Temps perdu : estimation indicative (corrections, refus, interruptions, échecs, boucles × minutes "
        "paramétrables dans `sillage.toml`)._",
        "",
    ]
    return "\n".join(lines)
