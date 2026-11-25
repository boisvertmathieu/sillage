"""Tableau de bord HTML autonome (un seul fichier, aucune ressource externe).

Conçu pour être déposé tel quel sur un partage, un wiki ou un artefact : pas de CDN
(compatible avec les postes et réseaux corporatifs), thème clair/sombre automatique,
graphiques SVG en ligne avec infobulles, tableaux de données en complément de
chaque graphique (accessibilité).
"""

from __future__ import annotations

import html
import json

from sillage import __version__
from sillage.analyze import Analysis
from sillage.insights import CATEGORY_LABELS, Lesson, Opportunity

E = html.escape


def _n(x: float | int | None) -> str:
    if x is None:
        return "n/d"
    if isinstance(x, float) and not x.is_integer():
        return f"{x:,.1f}".replace(",", " ").replace(".", ",")
    return f"{int(x):,}".replace(",", " ")


def _pct(x: float | None) -> str:
    return "n/d" if x is None else f"{x * 100:.0f} %"


# ------------------------------------------------------------------ SVG
def _hbar_path(x0: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Barre horizontale ancrée à gauche, extrémité de donnée arrondie (droite)."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return f"M{x0:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - 2 * r:.1f}a{r},{r} 0 0 1 -{r},{r}h-{w - r:.1f}z"


def _vbar_path(x: float, base: float, w: float, h: float, r: float = 4) -> str:
    """Barre verticale ancrée à la ligne de base, extrémité de donnée arrondie (haut)."""
    if h <= 0:
        return ""
    r = min(r, h, w / 2)
    return f"M{x:.1f},{base:.1f}v-{h - r:.1f}a{r},{r} 0 0 1 {r},-{r}h{w - 2 * r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - r:.1f}z"


def hbar_chart(
    rows: list[tuple[str, float, str]], *, label: str, fills: list[str] | None = None, value_fmt=_n, width: int = 560
) -> str:
    """rows = [(libellé, valeur, infobulle)] — une seule série, étiquettes directes."""
    if not rows:
        return '<p class="muted">Aucune donnée.</p>'
    bar_h, gap, label_w, value_w = 22, 6, 190, 70
    height = len(rows) * (bar_h + gap) + 4
    plot_w = width - label_w - value_w
    vmax = max(v for _, v, _ in rows) or 1
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{E(label)}" class="chart">']
    for i, (name, value, tip) in enumerate(rows):
        y = 2 + i * (bar_h + gap)
        w = max(0.0, plot_w * value / vmax)
        fill = fills[i] if fills else "var(--series-1)"
        short = name if len(name) <= 28 else name[:27] + "…"
        parts.append(
            f'<g class="mark" tabindex="0" data-tip="{E(tip)}">'
            f'<rect x="0" y="{y - gap / 2}" width="{width}" height="{bar_h + gap}" fill="transparent"/>'
            f'<text x="{label_w - 8}" y="{y + bar_h * 0.68:.1f}" text-anchor="end" class="lbl">{E(short)}</text>'
            f'<path d="{_hbar_path(label_w, y, w, bar_h)}" style="fill:{fill}"/>'
            f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h * 0.68:.1f}" class="val">{E(value_fmt(value))}</text>'
            "</g>"
        )
    parts.append(f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{height}" class="axis"/></svg>')
    return "".join(parts)


def vbar_chart(rows: list[tuple[str, float, str]], *, label: str, width: int = 560, height: int = 200) -> str:
    if not rows:
        return '<p class="muted">Aucune donnée.</p>'
    top, bottom, left = 16, 26, 36
    plot_h = height - top - bottom
    vmax = max(v for _, v, _ in rows) or 1
    slot = (width - left) / len(rows)
    bw = max(4.0, min(48.0, slot - 2))
    base = top + plot_h
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{E(label)}" class="chart">']
    for frac in (0.5, 1.0):
        y = base - plot_h * frac
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{_n(round(vmax * frac))}</text>'
        )
    for i, (name, value, tip) in enumerate(rows):
        x = left + i * slot + (slot - bw) / 2
        h = plot_h * value / vmax
        parts.append(
            f'<g class="mark" tabindex="0" data-tip="{E(tip)}">'
            f'<rect x="{left + i * slot:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h + bottom}" fill="transparent"/>'
            f'<path d="{_vbar_path(x, base, bw, h)}" style="fill:var(--series-1)"/>'
            f'<text x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle" class="tick">{E(name)}</text></g>'
        )
    parts.append(f'<line x1="{left}" y1="{base}" x2="{width}" y2="{base}" class="axis"/></svg>')
    return "".join(parts)


def _table(headers: list[str], rows: list[list], cls: str = "") -> str:
    head = "".join(f"<th>{E(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (
        f'<div class="tablewrap"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _details_table(summary: str, headers: list[str], rows: list[list]) -> str:
    return f"<details><summary>{E(summary)}</summary>{_table(headers, rows)}</details>"


# ------------------------------------------------------------------ page
CSS = """
:root{color-scheme:light;--bg:#f6f6f4;--surface-1:#fcfcfb;--border:#e3e2dd;--text-primary:#0b0b0b;
--text-secondary:#52514e;--text-muted:#6f6e69;--series-1:#2a78d6;--ord-1:#86b6ef;--ord-2:#3987e5;--ord-3:#1c5cab;
--ord-4:#0d366b;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--chip:#eef4fc}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#121211;--surface-1:#1a1a19;
--border:#2e2e2b;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#9a998f;--series-1:#3987e5;
--ord-1:#184f95;--ord-2:#2a78d6;--ord-3:#6da7ec;--ord-4:#b7d3f6;--chip:#1d2a3a}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface-1:#1a1a19;--border:#2e2e2b;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--text-muted:#9a998f;--series-1:#3987e5;--ord-1:#184f95;--ord-2:#2a78d6;--ord-3:#6da7ec;
--ord-4:#b7d3f6;--chip:#1d2a3a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text-primary);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.15rem;margin:0 0 12px}
.sub{color:var(--text-secondary);margin:0 0 4px}.muted{color:var(--text-muted)}
.note{border-left:3px solid var(--series-1);padding:6px 12px;color:var(--text-secondary);margin:16px 0 20px;
background:var(--surface-1)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:0 0 20px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .k{font-size:.8rem;color:var(--text-secondary)}.tile .v{font-size:1.55rem;font-weight:650;margin-top:2px}
.tile .d{font-size:.78rem;color:var(--text-muted)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:16px;margin-bottom:16px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:16px;min-width:0;margin-bottom:16px}
.grid2 .card{margin-bottom:0}
svg.chart{width:100%;height:auto;display:block;overflow:visible}
svg .lbl,svg .val,svg .tick{font-size:12px;fill:var(--text-secondary)}svg .val{fill:var(--text-primary)}
svg .axis{stroke:var(--border);stroke-width:1}svg .grid{stroke:var(--border);stroke-width:1;stroke-dasharray:2 3}
svg .mark:focus{outline:none}svg .mark:hover path,svg .mark:focus path{opacity:.8}
.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--text-secondary);font-weight:600}td.num,th.num{text-align:right}
details{margin-top:10px}summary{cursor:pointer;color:var(--text-secondary);font-size:.85rem}
.opp{border-top:1px solid var(--border);padding:12px 0}.opp:first-of-type{border-top:0}
.opp h3{font-size:1rem;margin:0 0 6px}.chip{display:inline-block;font-size:.75rem;padding:1px 8px;border-radius:99px;
background:var(--chip);color:var(--text-primary);margin-right:6px}
.opp p{margin:4px 0;color:var(--text-secondary)}.opp p b{color:var(--text-primary);font-weight:600}
code{font-size:.9em}
#tip{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);padding:6px 9px;
border-radius:6px;font-size:.8rem;max-width:320px;display:none;z-index:10}
footer{margin-top:24px;color:var(--text-muted);font-size:.8rem}
"""

JS = """
const tip=document.getElementById('tip');
function show(e,el){tip.textContent=el.dataset.tip;tip.style.display='block';
const r=el.getBoundingClientRect();const x=(e&&e.clientX)||r.left+r.width/2;const y=(e&&e.clientY)||r.top;
tip.style.left=Math.min(x+12,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=Math.max(8,y-tip.offsetHeight-10)+'px';}
document.querySelectorAll('[data-tip]').forEach(el=>{
el.addEventListener('mousemove',e=>show(e,el));el.addEventListener('focus',()=>show(null,el));
el.addEventListener('mouseleave',()=>tip.style.display='none');el.addEventListener('blur',()=>tip.style.display='none');});
"""


def render_html(
    a: Analysis, opps: list[Opportunity], lsns: list[Lesson], title: str = "Sillage — sessions Copilot"
) -> str:
    ov = a.overview
    if not ov.get("sessions"):
        body = "<p>Aucune session trouvée. Lancez <code>sillage collect</code> ou <code>sillage doctor</code>.</p>"
        return f"<!doctype html><html lang=fr><meta charset=utf-8><title>{E(title)}</title><style>{CSS}</style><main>{body}</main></html>"

    labels = ov["friction_labels"]
    smooth = labels["fluide"] / ov["sessions"]
    tiles = [
        ("Sessions", _n(ov["sessions"]), f"{_n(ov['users'])} personnes · {_n(ov['repositories'])} dépôts"),
        ("Heures actives", _n(ov["active_hours"]), f"médiane {_n(ov['median_session_min'])} min / session"),
        ("Sessions fluides", _pct(smooth), f"friction moyenne {_n(ov['mean_friction'])}/100"),
        ("Temps perdu estimé", f"{_n(ov['estimated_hours_lost'])} h", "indicatif, paramétrable"),
        ("Échecs d'outils", _pct(ov["tool_failure_rate"]), f"{_n(ov['failure_loops'])} boucles d'échec"),
        (
            "Corrections humaines",
            _n(ov["corrections"]),
            f"{_n(ov['aborts'])} interruptions · {_n(ov['user_denials'])} refus",
        ),
        (
            "Contexte : définitions d'outils",
            _pct(a.context.get("mean_tool_definitions_share")),
            f"≈ {_n(a.context.get('mean_tool_definitions_tokens'))} tokens / session",
        ),
        ("Opportunités", _n(len(opps)), "d'amélioration détectées"),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="k">{E(k)}</div><div class="v">{E(v)}</div><div class="d">{E(d)}</div></div>'
        for k, v, d in tiles
    )

    weekly = vbar_chart(
        [
            (
                w["week"].split("-")[1],
                w["sessions"],
                f"{w['week']} : {w['sessions']} sessions, {w['users']} pers., friction moy. {w['mean_friction']}",
            )
            for w in a.weekly
        ],
        label="Sessions par semaine",
    )
    friction_rows = [
        (k.capitalize(), labels[k], f"{k} : {labels[k]} sessions ({labels[k] / ov['sessions'] * 100:.0f} %)")
        for k in ("fluide", "accrochages", "difficile", "pénible")
    ]
    friction = hbar_chart(
        friction_rows,
        label="Répartition des sessions par niveau de friction",
        fills=["var(--ord-1)", "var(--ord-2)", "var(--ord-3)", "var(--ord-4)"],
    )

    cat_counts: dict[str, int] = {}
    for o in opps:
        cat_counts[o.category] = cat_counts.get(o.category, 0) + 1
    cats = hbar_chart(
        sorted(
            (
                (CATEGORY_LABELS.get(c, c), n, f"{CATEGORY_LABELS.get(c, c)} : {n} opportunité(s)")
                for c, n in cat_counts.items()
            ),
            key=lambda r: -r[1],
        ),
        label="Opportunités par catégorie (où agir)",
    )

    skills_rows = [
        (
            s["skill"],
            s["sessions"],
            f"{s['skill']} : {s['sessions']} sessions, activation {_pct(s['activation_rate'])}, "
            f"{s['corrections_after']} corrections après invocation",
        )
        for s in a.skills
    ]
    skills_chart = hbar_chart(skills_rows, label="Sessions utilisant chaque skill")

    out_rows = [
        (
            f"{c['command']}",
            c["output_chars"],
            f"{c['command']} : {c['calls']} appels, {_n(c['mean_output_chars'])} car. en moyenne",
        )
        for c in sorted(a.shell_commands, key=lambda c: -c["output_chars"])[:8]
    ]
    out_chart = hbar_chart(out_rows, label="Caractères renvoyés au modèle par commande shell")

    opp_html = []
    for i, o in enumerate(opps[:12], 1):
        ex = (
            ("<p><b>Extraits caviardés :</b> " + " / ".join(f"« {E(e)} »" for e in o.excerpts[:3]) + "</p>")
            if o.excerpts
            else ""
        )
        opp_html.append(
            f'<div class="opp"><h3>{i}. {E(o.title)}</h3>'
            f'<span class="chip">{E(CATEGORY_LABELS.get(o.category, o.category))}</span>'
            f'<span class="chip">{_n(o.sessions)} sessions</span>'
            f"<p><b>Preuves :</b> {E(' ; '.join(o.evidence))}</p>"
            f"<p><b>Action :</b> {E(o.action)}</p><p><b>Où :</b> {E(o.where)}</p>{ex}</div>"
        )
    lessons_html = (
        "".join(
            f'<li><span class="chip">{E(lsn.scope)}</span>{E(lsn.text)} '
            f'<span class="muted">(vu {lsn.occurrences}×, {lsn.sessions} sessions)</span></li>'
            for lsn in lsns[:15]
        )
        or "<li class=muted>Aucune leçon récurrente.</li>"
    )

    tables = [
        _details_table(
            "Tableau : sessions par semaine",
            ["Semaine", "Sessions", "Personnes", "Friction moy.", "Heures perdues (est.)"],
            [[w["week"], w["sessions"], w["users"], w["mean_friction"], w["hours_lost"]] for w in a.weekly],
        ),
    ]
    skills_table = _table(
        ["Skill", "Invocations", "Sessions", "Activation", "Corrections après", "Friction (écart)"],
        [
            [
                E(s["skill"]),
                s["invocations"],
                s["sessions"],
                _pct(s["activation_rate"]),
                s["corrections_after"],
                f"{s['mean_friction']} ({s['friction_lift']:+})",
            ]
            for s in a.skills
        ],
    )
    agents_table = _table(
        ["Agent", "Exécutions", "Taux d'échec", "Durée médiane", "Tokens moyens", "Erreur principale"],
        [
            [
                E(s["agent"]),
                s["runs"],
                _pct(s["failure_rate"]),
                f"{s['median_duration_s']} s",
                _n(s["mean_tokens"]),
                E(s["top_error"] or "—"),
            ]
            for s in a.subagents
        ],
    )
    mcp_table = _table(
        ["Serveur", "Chargé", "Utilisé", "Appels", "Échecs", "Erreurs démarrage"],
        [
            [
                E(m["server"]),
                m["sessions_loaded"],
                m["sessions_used"],
                m["calls"],
                _pct(m["failure_rate"]),
                m["startup_errors"],
            ]
            for m in a.mcp
        ],
    )
    repo_table = _table(
        [
            "Dépôt",
            "Sessions",
            "Friction moy.",
            "Corrections",
            "Échecs outils",
            "Boucles",
            "Compaction",
            "h perdues (est.)",
        ],
        [
            [
                E(r["repository"]),
                r["sessions"],
                r["mean_friction"],
                r["corrections"],
                _pct(r["tool_failure_rate"]),
                r["failure_loops"],
                _pct(r["compaction_rate"]),
                r["hours_lost"],
            ]
            for r in a.repositories
        ],
    )
    model_table = _table(["Modèle", "Appels"], [[E(m["model"]), _n(m["calls"])] for m in a.models])
    period = f"{(ov['period_start'] or '')[:10]} → {(ov['period_end'] or '')[:10]}"
    data_json = json.dumps({"overview": ov, "context": a.context}, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><style>{CSS}</style></head>
<body><main>
<h1>{E(title)}</h1>
<p class="sub">{E(period)} · {_n(ov["sessions"])} sessions · {_n(ov["users"])} personnes · {_n(ov["repositories"])} dépôts</p>
<div class="note">Sillage mesure les <b>outils</b> (skills, agents, instructions, environnements), jamais les personnes :
aucun classement individuel, extraits affichés seulement s'ils concernent au moins {a.k_anonymity} personnes.</div>
<section class="tiles">{tiles_html}</section>
<section class="grid2">
<div class="card"><h2>Sessions par semaine</h2>{weekly}{tables[0]}</div>
<div class="card"><h2>Niveau de friction des sessions</h2>{friction}
<p class="muted">Score 0-100 combinant corrections, refus, interruptions, échecs d'outils, boucles, erreurs et compactions.</p></div>
</section>
<section class="card"><h2>Top opportunités d'amélioration</h2>{"".join(opp_html) or "<p class=muted>Aucune.</p>"}
<details><summary>Voir toutes ({len(opps)}) : <code>sillage opportunities</code></summary></details></section>
<section class="grid2">
<div class="card"><h2>Où agir</h2>{cats}</div>
<div class="card"><h2>Leçons apprises (réinjectables)</h2><ul>{lessons_html}</ul>
<p class="muted"><code>sillage lessons --repo &lt;dépôt&gt; --write AGENTS.md</code> · outil MCP <code>sillage_lessons</code></p></div>
</section>
<section class="grid2">
<div class="card"><h2>Utilisation des skills</h2>{skills_chart}<details><summary>Tableau détaillé</summary>{skills_table}</details></div>
<div class="card"><h2>Coût en contexte des commandes shell</h2>{out_chart}
<p class="muted">Caractères de sortie renvoyés au modèle — cibles prioritaires pour la compression.</p></div>
</section>
<section class="card"><h2>Agents et sous-agents</h2>{agents_table}</section>
<section class="card"><h2>Serveurs MCP</h2>{mcp_table}</section>
<section class="card"><h2>Dépôts</h2>{repo_table}</section>
<section class="card"><h2>Modèles</h2>{model_table}</section>
<footer>Généré par Sillage {__version__}. Temps perdu = estimation indicative paramétrable (sillage.toml).</footer>
</main><div id="tip" role="tooltip"></div>
<script type="application/json" id="sillage-data">{data_json}</script>
<script>{JS}</script></body></html>"""
