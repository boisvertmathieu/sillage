"""Mesure de l'effet d'un correctif : avant / après une date pivot.

Les identifiants d'opportunités sont déterministes (même cause => même identifiant d'une période
à l'autre). On compare donc, pour chaque identifiant, la part de sessions touchées avant et après
la date de fusion du correctif. Les volumes diffèrent d'une période à l'autre : tout est ramené
à 100 sessions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sillage.analyze import Analysis, analyze
from sillage.config import InsightThresholds
from sillage.insights import Opportunity, opportunities
from sillage.model import SessionRecord

#: En dessous, la période « après » est jugée trop courte pour conclure.
MIN_SESSIONS_PER_PERIOD = 20

STATUS_LABELS = {
    "resolue": "résolue (sous le seuil)",
    "en-baisse": "en baisse",
    "stable": "stable",
    "en-hausse": "en hausse",
    "nouvelle": "nouvelle",
}


@dataclass
class OpportunityDelta:
    id: str
    title: str
    category: str
    where: str
    status: str
    before_per_100: float
    after_per_100: float

    @property
    def change_pct(self) -> float | None:
        if not self.before_per_100:
            return None
        return round((self.after_per_100 - self.before_per_100) / self.before_per_100 * 100, 0)


@dataclass
class Comparison:
    pivot: str
    before_period: tuple[str | None, str | None]
    after_period: tuple[str | None, str | None]
    before: dict
    after: dict
    deltas: list[OpportunityDelta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in STATUS_LABELS}
        for d in self.deltas:
            out[d.status] += 1
        return out

    def to_dict(self) -> dict:
        data = asdict(self)
        for d, raw in zip(self.deltas, data["deltas"], strict=True):
            raw["change_pct"] = d.change_pct
        data["counts"] = self.counts()
        return data


def _start(r: SessionRecord) -> datetime | None:
    if not r.start:
        return None
    dt = datetime.fromisoformat(r.start)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _kpis(a: Analysis) -> dict:
    ov = a.overview
    n = ov.get("sessions") or 0
    if not n:
        return {"sessions": 0}
    return {
        "sessions": n,
        "users": ov["users"],
        "smooth_share": round(ov["friction_labels"]["fluide"] / n, 3),
        "mean_friction": ov["mean_friction"],
        "tool_failure_rate": ov["tool_failure_rate"],
        "corrections_per_session": round(ov["corrections"] / n, 2),
        "minutes_lost_per_session": round(ov["estimated_hours_lost"] * 60 / n, 1),
        "failure_loops_per_100": round(ov["failure_loops"] / n * 100, 1),
        "ai_units_per_session": round(ov.get("ai_units", 0) / n, 3),
    }


def _per_100(o: Opportunity | None, n: int) -> float:
    return round(o.sessions / n * 100, 1) if o and n else 0.0


def compare(
    records: list[SessionRecord],
    pivot: datetime,
    window: timedelta | None = None,
    thresholds: InsightThresholds | None = None,
    k_anonymity: int = 2,
    ids: list[str] | None = None,
) -> Comparison:
    th = thresholds or InsightThresholds()
    lo = pivot - window if window else None
    hi = pivot + window if window else None
    before = [r for r in records if (s := _start(r)) and s < pivot and (lo is None or s >= lo)]
    after = [r for r in records if (s := _start(r)) and s >= pivot and (hi is None or s < hi)]
    a_before, a_after = analyze(before, k_anonymity), analyze(after, k_anonymity)
    o_before = {o.id: o for o in opportunities(a_before, th)}
    o_after = {o.id: o for o in opportunities(a_after, th)}
    # seuil assoupli : mesurer une opportunité encore présente mais passée sous le seuil
    relaxed = {o.id: o for o in opportunities(a_after, replace(th, min_sessions=1))}
    nb, na = len(before), len(after)

    deltas: list[OpportunityDelta] = []
    for oid in list(o_before) + [i for i in o_after if i not in o_before]:
        if ids and not any(oid.startswith(i) for i in ids):
            continue
        ob, oa = o_before.get(oid), o_after.get(oid)
        ref = ob or oa
        b100, a100 = _per_100(ob, nb), _per_100(oa or relaxed.get(oid), na)
        if ob and not oa:
            status = "resolue"
        elif oa and not ob:
            status = "nouvelle"
        elif a100 <= b100 * 0.75:
            status = "en-baisse"
        elif a100 >= b100 * 1.25:
            status = "en-hausse"
        else:
            status = "stable"
        deltas.append(OpportunityDelta(oid, ref.title, ref.category, ref.where, status, b100, a100))
    order = list(STATUS_LABELS)
    deltas.sort(key=lambda d: (order.index(d.status), -d.before_per_100))

    warnings = []
    for label, n in (("avant", nb), ("après", na)):
        if n < MIN_SESSIONS_PER_PERIOD:
            warnings.append(
                f"Période « {label} » : {n} sessions seulement (< {MIN_SESSIONS_PER_PERIOD}) ; conclusion fragile."
            )

    def period(rs: list[SessionRecord]) -> tuple[str | None, str | None]:
        starts = sorted(r.start for r in rs if r.start)
        return (starts[0][:10], starts[-1][:10]) if starts else (None, None)

    return Comparison(
        pivot=pivot.date().isoformat(),
        before_period=period(before),
        after_period=period(after),
        before=_kpis(a_before),
        after=_kpis(a_after),
        deltas=deltas,
        warnings=warnings,
    )


KPI_LABELS = [
    ("sessions", "Sessions", "{:.0f}"),
    ("smooth_share", "Part de sessions fluides", "{:.0%}"),
    ("mean_friction", "Friction moyenne (/100)", "{:.1f}"),
    ("tool_failure_rate", "Taux d'échec d'outils", "{:.1%}"),
    ("corrections_per_session", "Corrections par session", "{:.2f}"),
    ("minutes_lost_per_session", "Minutes perdues par session (est.)", "{:.1f}"),
    ("failure_loops_per_100", "Boucles d'échec / 100 sessions", "{:.1f}"),
    ("ai_units_per_session", "Unités IA (AIU) par session", "{:.3f}"),
]


def render_comparison(c: Comparison) -> str:
    def fmt(spec: str, v: object) -> str:
        return spec.format(v) if isinstance(v, (int, float)) else "n/d"

    lines = [
        f"# Avant / après le {c.pivot}",
        "",
        f"Avant : {c.before_period[0] or 'n/d'} → {c.before_period[1] or 'n/d'} · "
        f"après : {c.after_period[0] or 'n/d'} → {c.after_period[1] or 'n/d'}",
        "",
    ]
    lines += [f"> ⚠ {w}" for w in c.warnings]
    if c.warnings:
        lines.append("")
    lines += ["| Indicateur | Avant | Après |", "|---|---|---|"]
    for key, label, spec in KPI_LABELS:
        lines.append(f"| {label} | {fmt(spec, c.before.get(key))} | {fmt(spec, c.after.get(key))} |")
    counts = c.counts()
    lines += [
        "",
        "## Opportunités",
        "",
        " · ".join(f"{STATUS_LABELS[k]} : {v}" for k, v in counts.items()),
        "",
    ]
    if not c.deltas:
        lines.append("Aucune opportunité dans l'une ou l'autre période.")
    else:
        lines += [
            "| Statut | Opportunité | Sessions touchées / 100 (avant → après) | Où | Id |",
            "|---|---|---|---|---|",
        ]
        for d in c.deltas:
            change = f" ({d.change_pct:+.0f} %)" if d.change_pct is not None else ""
            title = d.title.replace("|", "\\|")
            lines.append(
                f"| {STATUS_LABELS[d.status]} | {title} | {d.before_per_100} → {d.after_per_100}{change} "
                f"| {d.where} | `{d.id}` |"
            )
    lines += [
        "",
        "_Une opportunité « résolue » est passée sous le seuil de détection ; sa trace résiduelle reste visible "
        "dans la colonne « après ». Indicateurs d'outils, jamais de personnes._",
    ]
    return "\n".join(lines) + "\n"
