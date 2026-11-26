"""Diagnostic d'onboarding : lever automatiquement les points flous à partir de tout l'historique.

``sillage onboard`` lit **l'historique complet** des sessions Copilot CLI du poste (pas seulement
la dernière période) et répond, chiffres à l'appui, aux questions qu'il faudrait sinon poser à la
personne : Sillage lit-il correctement ce format de journaux ? Où sont les sessions ? Quelle part de
l'usage passe par l'IDE plutôt que par la CLI ? Quels dépôts, quels clones, quels AGENTS.md ? Quels
textes sortiraient d'un export et quels motifs de caviardage ajouter ? Le lexique de corrections
rate-t-il des formulations de l'équipe ? L'installation est-elle prête pour la collecte planifiée ?

Ce qui ne peut pas être déduit des journaux devient une **question** explicite. L'agent
``sillage-onboarding`` (voir ``docs/guide-agent-onboarding.md``) s'appuie sur cette sortie.

Confidentialité : la fiche complète est un document **local** (chemins, noms de dépôts, extraits
caviardés). ``--partageable`` ne garde que des statuts et des constats sans texte de session.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sillage import __version__
from sillage.analyze import MCP_DISABLED, analyze
from sillage.config import Config
from sillage.insights import MARK_START as LESSONS_MARK
from sillage.insights import opportunities
from sillage.model import SessionRecord
from sillage.normalize import KNOWN_EVENT_TYPES, is_correction
from sillage.reader import candidate_homes, iter_session_files, load_events
from sillage.redact import compile_patterns, redact

STATUSES = ("bloquant", "attention", "a-confirmer", "ok")
STATUS_LABELS = {"bloquant": "⛔ bloquant", "attention": "⚠ attention", "a-confirmer": "❓ à confirmer", "ok": "✅ ok"}

#: Champs indispensables à l'analyse, par type d'événement (chemins pointés dans ``data``).
EXPECTED_FIELDS: dict[str, list[str]] = {
    "session.start": ["copilotVersion"],
    "user.message": ["content"],
    "assistant.usage": ["model", "inputTokens"],
    "tool.execution_start": ["toolCallId", "toolName"],
    "tool.execution_complete": ["toolCallId", "success"],
    "skill.invoked": ["name"],
    "permission.requested": ["requestId", "permissionRequest.kind"],
    "permission.completed": ["requestId", "result.kind"],
    "hook.end": ["success"],
    "session.shutdown": ["shutdownType"],
}
COVERAGE_MIN = 0.9

#: Messages courts qui ressemblent à une correction sans être reconnus par le lexique.
_CANDIDATE_RE = re.compile(
    r"\b(pas|plutot|au lieu|encore|toujours|devrait|il faut|faut pas|oublie|instead|actually|should|"
    r"shouldn'?t|not what|still|again)\b"
)

IDE_PRODUCTS = ("Code", "Code - Insiders", "VSCodium")
IDE_SUBPATHS = ("User/workspaceStorage/*/chatSessions/*", "User/globalStorage/emptyWindowChatSessions/*")
SCHEDULED_TASK = "Sillage collecte"
ASSET_AGENTS = ("sillage-coach.agent.md", "sillage-onboarding.agent.md")


@dataclass
class Check:
    id: str
    title: str
    status: str
    findings: list[str] = field(default_factory=list)  # local : peut contenir chemins et noms
    action: str = ""
    question: str | None = None
    shareable: list[str] = field(default_factory=list)  # sans texte issu des sessions
    details: dict = field(default_factory=dict)


@dataclass
class Onboarding:
    generated_at: str
    sillage_version: str
    checks: list[Check]
    depots: list[tuple[str, str]] = field(default_factory=list)
    suggested_patterns: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not any(c.status == "bloquant" for c in self.checks)

    def counts(self) -> dict[str, int]:
        c = Counter(ch.status for ch in self.checks)
        return {s: c.get(s, 0) for s in STATUSES}

    def to_dict(self, shareable: bool = False) -> dict:
        if not shareable:
            data = asdict(self)
            data["ready"], data["counts"] = self.ready, self.counts()
            return data
        return {
            "generated_at": self.generated_at,
            "sillage_version": self.sillage_version,
            "ready": self.ready,
            "counts": self.counts(),
            "checks": [
                {"id": c.id, "title": c.title, "status": c.status, "findings": c.shareable} for c in self.checks
            ],
        }


# ------------------------------------------------------------- historique brut
@dataclass
class HistoryScan:
    homes: dict[str, int] = field(default_factory=dict)
    sessions: int = 0
    first: datetime | None = None
    last: datetime | None = None
    weekly: Counter = field(default_factory=Counter)
    versions: Counter = field(default_factory=Counter)
    types: Counter = field(default_factory=Counter)
    unknown: Counter = field(default_factory=Counter)
    bad_lines: int = 0
    checkpoint_events: int = 0
    checkpoint_sessions: int = 0
    coverage: dict[str, list[int]] = field(default_factory=dict)  # "type:champ" -> [présent, total]
    exit_sources: Counter = field(default_factory=Counter)
    repos: Counter = field(default_factory=Counter)
    repo_paths: dict[str, str] = field(default_factory=dict)
    no_repo: int = 0
    corrections: Counter = field(default_factory=Counter)  # extraits caviardés distincts (bornés)
    candidates: Counter = field(default_factory=Counter)
    n_corrections: int = 0
    n_candidates: int = 0
    user_messages: int = 0


def _get(data: object, path: str) -> object:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def scan_history(homes: list[Path], cfg: Config, keep: int = 300) -> HistoryScan:
    """Parcourt toutes les sessions, une à la fois (mémoire bornée). Aucune donnée n'est écrite."""
    scan = HistoryScan()
    extra = compile_patterns(cfg.redaction_patterns)
    for home in homes:
        files = list(iter_session_files([home]))
        if (home / "session-state").is_dir():
            scan.homes[str(home)] = len(files)
        for _sid, ev_path, _ws, _home in files:
            try:
                events, bad = load_events(ev_path)
            except OSError:
                continue
            scan.sessions += 1
            scan.bad_lines += bad
            first_ts = None
            repo = None
            seen_assistant = False
            had_checkpoint = False
            for ev in events:
                etype = ev.get("type", "")
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                scan.types[etype] += 1
                if etype not in KNOWN_EVENT_TYPES:
                    scan.unknown[etype] += 1
                if ev.get("checkpoint") is True:
                    scan.checkpoint_events += 1
                    had_checkpoint = True
                    continue
                ts = _ts(ev.get("timestamp"))
                if ts and first_ts is None:
                    first_ts = ts
                for path in EXPECTED_FIELDS.get(etype, []):
                    cell = scan.coverage.setdefault(f"{etype}:{path}", [0, 0])
                    cell[1] += 1
                    cell[0] += _get(data, path) is not None
                is_sub = bool(ev.get("agentId")) or bool(data.get("parentToolCallId"))
                if etype in ("session.start", "session.resume", "session.context_changed"):
                    ctx = data if etype == "session.context_changed" else data.get("context")
                    ctx = ctx if isinstance(ctx, dict) else {}
                    if etype == "session.start" and data.get("copilotVersion"):
                        scan.versions[str(data["copilotVersion"])] += 1
                    if isinstance(ctx.get("repository"), str) and ctx["repository"]:
                        repo = ctx["repository"]
                        root = ctx.get("gitRoot") or ctx.get("cwd")
                        if isinstance(root, str) and root:
                            scan.repo_paths[repo] = root
                elif etype == "tool.execution_complete" and not is_sub:
                    shell = data.get("shellExecution")
                    text = _get(data, "result.content")
                    if isinstance(shell, dict) and isinstance(shell.get("exitCode"), int):
                        scan.exit_sources["champ shellExecution.exitCode"] += 1
                    elif isinstance(text, str) and "exit code" in text[-200:]:
                        scan.exit_sources["marqueur texte « exit code »"] += 1
                elif etype == "assistant.message" and not is_sub:
                    seen_assistant = True
                elif etype == "user.message" and not is_sub:
                    scan.user_messages += 1
                    content = data.get("content") if isinstance(data.get("content"), str) else ""
                    if not seen_assistant or not content:
                        continue
                    if is_correction(content, cfg.extra_correction_patterns):
                        scan.n_corrections += 1
                        _bounded_add(scan.corrections, redact(content, 160, extra=extra) or "", keep)
                    elif len(content) <= 160 and _CANDIDATE_RE.search(_fold(content)):
                        scan.n_candidates += 1
                        _bounded_add(scan.candidates, redact(content, 160, extra=extra) or "", keep)
            scan.checkpoint_sessions += had_checkpoint
            if repo:
                scan.repos[repo] += 1
            else:
                scan.no_repo += 1
            if first_ts:
                scan.first = min(scan.first or first_ts, first_ts)
                scan.last = max(scan.last or first_ts, first_ts)
                y, w, _ = first_ts.isocalendar()
                scan.weekly[f"{y}-S{w:02d}"] += 1
    return scan


def _bounded_add(counter: Counter, value: str, keep: int) -> None:
    if value in counter or len(counter) < keep:
        counter[value] += 1


def _fold(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("’", "'")


# ---------------------------------------------------------------- IDE et poste
def ide_roots() -> list[Path]:
    """Répertoires de VS Code où Copilot Chat conserve ses conversations (tous systèmes, et Windows vu de WSL)."""
    if "SILLAGE_IDE_ROOTS" in os.environ:  # surcharge (tests, emplacements non standard)
        return [Path(p) for p in os.environ["SILLAGE_IDE_ROOTS"].split(os.pathsep) if p]
    home = Path.home()
    bases = [home / ".vscode-server" / "data"]
    for product in IDE_PRODUCTS:
        bases += [home / ".config" / product, home / "Library" / "Application Support" / product]
        if os.environ.get("APPDATA"):
            bases.append(Path(os.environ["APPDATA"]) / product)
        bases += [Path(p) for p in sorted(glob.glob(f"/mnt/c/Users/*/AppData/Roaming/{product}"))]
    return bases


def count_ide_chats(roots: list[Path], days: int = 90) -> dict[str, list[int]]:
    """{racine: [fichiers, fichiers modifiés depuis ``days`` jours]} — compte seulement, ne lit rien."""
    limit = datetime.now().timestamp() - days * 86400
    out: dict[str, list[int]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        total = recent = 0
        for sub in IDE_SUBPATHS:
            for f in glob.glob(str(root / sub)):
                try:
                    mtime = os.stat(f).st_mtime
                except OSError:
                    continue
                total += 1
                recent += mtime >= limit
        if total:
            out[str(root)] = [total, recent]
    return out


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _scheduled_task_exists() -> bool | None:
    exe = shutil.which("schtasks.exe") or (shutil.which("schtasks") if sys.platform == "win32" else None)
    if not exe:
        return None
    try:
        res = subprocess.run([exe, "/Query", "/TN", SCHEDULED_TASK], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.returncode == 0


# ---------------------------------------------------- motifs de confidentialité
_SUGGESTIONS: list[tuple[str, re.Pattern[str]]] = [
    (
        "environnement de production",
        re.compile(r"(?i)\b(?:[a-z0-9]+[-_.])*prod(?:[-_.][a-z0-9]+)+\b|\b[a-z]{2,5}prod\d*\b|\bprod\d+\b"),
    ),
    ("identifiant de dossier, de billet ou de client", re.compile(r"\b[A-Z]{2,6}-\d{3,}\b")),
    ("nom de personne (NOM Prénom)", re.compile(r"\b[A-ZÉÈ]{2,} [A-ZÉ][a-zéèêàç]+\b")),
    ("nom de serveur interne", re.compile(r"(?i)\b(?:srv|db|sql|ora|app|web)[a-z0-9]*-?[a-z0-9-]*\d+\b")),
    ("long numéro (dossier, client, compte)", re.compile(r"\b\d{6,}\b")),
]
_PLACEHOLDERS = re.compile(r"\[[A-Z_]+\]|<[a-z]+>")
#: Mots en majuscules courants dans les messages techniques : pas des noms de personnes.
_UPPER_STOP = {
    "ERROR",
    "ERR",
    "WARN",
    "WARNING",
    "INFO",
    "DEBUG",
    "FATAL",
    "FAIL",
    "FAILED",
    "HTTP",
    "HTTPS",
    "NPM",
    "API",
    "JSON",
    "SQL",
    "AWS",
    "URL",
    "TODO",
    "NOTE",
    "OK",
    "PR",
    "CI",
    "CD",
    "GET",
    "POST",
    "PUT",
}


def _generalize(kind: str, token: str) -> str:
    if kind.startswith("identifiant"):
        prefix, _, digits = token.partition("-")
        return rf"\b{re.escape(prefix)}-\d{{{len(digits)}}}\b"
    if kind.startswith("long numéro"):
        return rf"\b\d{{{len(token)}}}\b"
    if kind.startswith("nom de personne"):
        return r"\b[A-ZÉÈ]{2,} [A-ZÉ][a-zéèêàç]+\b"
    m = re.match(r"(?i)^(prod)[-_]", token)
    if m:
        return rf"\b{m.group(1)}[-_][a-z0-9-]+\b"
    return rf"\b{re.escape(token)}\b"


def suggest_patterns(values: Counter, existing: list[str] | None = None) -> list[dict]:
    """Motifs candidats pour ``[confidentialite]`` à partir du texte qui sortirait d'un export."""
    compiled = compile_patterns(existing)
    found: dict[str, dict] = {}
    for value, n in values.items():
        text = _PLACEHOLDERS.sub(" ", value)
        if any(p.search(text) for p in compiled):
            text = " ".join(p.sub(" ", text) for p in compiled)
        for kind, rx in _SUGGESTIONS:
            for m in rx.finditer(text):
                token = m.group(0)
                if kind.startswith("nom de personne") and token.split(" ")[0] in _UPPER_STOP:
                    continue
                pattern = _generalize(kind, token)
                entry = found.setdefault(pattern, {"pattern": pattern, "kind": kind, "count": 0, "examples": []})
                entry["count"] += n
                if token not in entry["examples"] and len(entry["examples"]) < 3:
                    entry["examples"].append(token)
    return sorted(found.values(), key=lambda d: -d["count"])


def outgoing_text(records: list[SessionRecord]) -> Counter:
    """Toutes les valeurs textuelles d'enregistrements déjà passés par ``apply_privacy``."""
    out: Counter = Counter()
    for r in records:
        if r.repository:
            out[r.repository] += 1
        for t in r.tool_calls:
            for v in (t.target, t.recovered_by, t.error_signature, t.command_excerpt, t.error_excerpt):
                if v:
                    out[v] += 1
        for p in r.permissions:
            if p.target:
                out[p.target] += 1
        for v in r.session_errors + r.mcp_server_errors + r.hook_failures + r.agent_definition_errors:
            out[v] += 1
        for c in r.corrections:
            if c.excerpt:
                out[c.excerpt] += 1
    return out


# ------------------------------------------------------------------ diagnostic
def run_onboarding(
    records: list[SessionRecord],
    cfg: Config,
    homes: list[Path] | None = None,
    ide: list[Path] | None = None,
    copilot_target: Path | None = None,
    now: datetime | None = None,
) -> Onboarding:
    from sillage.store import privacy_copies

    now = now or datetime.now(UTC)
    homes = homes if homes is not None else candidate_homes(cfg.copilot_homes or None)
    scan = scan_history(homes, cfg)
    checks: list[Check] = []
    questions: list[str] = []

    # 1. Sessions et emplacement --------------------------------------------------
    c = Check("sessions", "Sessions trouvées sur le poste", "ok")
    if not scan.homes:
        c.findings.append("Aucun dossier session-state parmi : " + ", ".join(str(h) for h in homes))
    for h, n in scan.homes.items():
        c.findings.append(f"{h} : {n} session(s)")
    windows_side = sum(n for h, n in scan.homes.items() if h.startswith("/mnt/") or ":\\" in h)
    if scan.first and scan.last:
        weeks = max(1.0, (scan.last - scan.first).days / 7)
        c.findings.append(
            f"Historique : {scan.sessions} sessions du {scan.first.date()} au {scan.last.date()} "
            f"(≈ {scan.sessions / weeks:.1f} par semaine)"
        )
        recent = sum(n for wk, n in scan.weekly.items() if wk >= _week(now - timedelta(weeks=8)))
        c.details["sessions_8_weeks"] = recent
        c.findings.append(f"8 dernières semaines : {recent} sessions")
    if windows_side:
        c.findings.append(f"Dont {windows_side} lancées côté Windows (lues depuis WSL)")
    c.shareable = [
        f"{scan.sessions} sessions dans l'historique",
        f"sessions côté Windows : {'oui' if windows_side else 'non'}",
    ]
    if scan.sessions == 0:
        c.status = "bloquant"
        c.action = (
            "Aucune session : lancer au moins une session `copilot` dans un dépôt, ou indiquer le dossier avec "
            "`--copilot-home` (Windows natif : %USERPROFILE%\\.copilot ; WSL : /mnt/c/Users/<nom>/.copilot)."
        )
    elif scan.sessions < 10:
        c.status = "attention"
        c.action = (
            "Historique court : les constats suivants sont indicatifs. Refaire `sillage onboard` après 2 semaines."
        )
    checks.append(c)

    # 2. Format des journaux ------------------------------------------------------
    c = Check("format", "Sillage lit-il correctement ce format de journaux ?", "ok")
    if scan.versions:
        c.findings.append(
            "Versions de Copilot CLI : " + ", ".join(f"{v} ({n})" for v, n in scan.versions.most_common(6))
        )
    weak = {k: v for k, v in scan.coverage.items() if v[1] >= 5 and v[0] / v[1] < COVERAGE_MIN}
    for key, (present, total) in sorted(scan.coverage.items()):
        if key in weak:
            c.findings.append(f"Champ peu présent : {key} dans {present}/{total} événements")
    if scan.unknown:
        c.findings.append(
            "Types d'événements inconnus de Sillage : "
            + ", ".join(f"{t} ({n})" for t, n in scan.unknown.most_common(12))
        )
    if scan.checkpoint_events:
        c.findings.append(
            f"Événements rejoués (« checkpoint ») : {scan.checkpoint_events} dans {scan.checkpoint_sessions} "
            "session(s) — ignorés au comptage (pas de double compte)"
        )
    if scan.exit_sources:
        c.findings.append(
            "Source des codes de sortie shell : " + ", ".join(f"{k} ({n})" for k, n in scan.exit_sources.items())
        )
    if scan.bad_lines:
        c.findings.append(f"Lignes illisibles : {scan.bad_lines} (sessions en cours ou journaux tronqués)")
    c.shareable = [
        "versions : " + ", ".join(v for v, _ in scan.versions.most_common(6)),
        "types inconnus : " + (", ".join(t for t, _ in scan.unknown.most_common(20)) or "aucun"),
        "champs peu présents : " + (", ".join(sorted(weak)) or "aucun"),
        f"événements checkpoint : {'oui' if scan.checkpoint_events else 'non'}",
    ]
    if weak:
        c.status = "bloquant"
        c.action = (
            "Des champs indispensables manquent : le format a changé. Transmettre la sortie `--partageable` au "
            "responsable de Sillage avant le pilote (aucun contenu de session n'y figure)."
        )
    elif scan.unknown:
        c.status = "attention"
        c.action = (
            "Nouveaux types d'événements : sans impact sur les mesures actuelles, mais à ajouter à Sillage "
            "(KNOWN_EVENT_TYPES) s'ils portent un signal utile. Transmettre la liste au responsable de Sillage."
        )
    checks.append(c)

    # 3. Dépôts, clones et AGENTS.md ----------------------------------------------
    c = Check("depots", "Dépôts, clones locaux et AGENTS.md", "ok")
    depots: list[tuple[str, str]] = []
    total = scan.sessions or 1
    c.findings.append(f"Sessions hors dépôt : {scan.no_repo} ({scan.no_repo / total:.0%})")
    missing_snippet = []
    for repo, n in scan.repos.most_common(25):
        path = scan.repo_paths.get(repo)
        state = "clone introuvable sur ce poste"
        if path and Path(path).is_dir():
            depots.append((repo, path))
            agents = Path(path) / "AGENTS.md"
            if not agents.exists():
                state = "pas d'AGENTS.md"
                missing_snippet.append(repo)
            else:
                text = agents.read_text(encoding="utf-8", errors="replace")
                has_block = LESSONS_MARK in text
                has_snippet = "Mémoire collective (Sillage)" in text
                state = f"AGENTS.md {'avec' if has_block else 'sans'} bloc de leçons"
                if not has_snippet:
                    state += ", sans l'extrait d'instructions Sillage"
                    missing_snippet.append(repo)
        c.findings.append(f"{repo} : {n} sessions — {state}")
    c.details["missing_snippet"] = missing_snippet
    c.shareable = [f"{len(scan.repos)} dépôt(s) distinct(s)", f"part hors dépôt : {scan.no_repo / total:.0%}"]
    if scan.repos and scan.no_repo / total > 0.5:
        c.status = "attention"
        c.action = (
            "Plus de la moitié des sessions sont hors dépôt : les leçons par dépôt toucheront peu ces sessions. "
            "Prévoir des instructions personnelles (~/.copilot/copilot-instructions.md) pour les leçons globales."
        )
    elif missing_snippet:
        c.status = "a-confirmer"
        c.action = "Ajouter l'extrait `sillage/assets/copilot/instructions-snippet.md` aux AGENTS.md listés."
        questions.append(
            "Pour quels dépôts voulez-vous publier les leçons (fichier depots.txt) ? Un brouillon est proposé ci-dessous."
        )
    checks.append(c)

    # 4. Usage de Copilot dans l'IDE ----------------------------------------------
    c = Check("ide", "Quelle part de l'usage Sillage ne voit-il pas (Copilot dans l'IDE) ?", "ok")
    chats = count_ide_chats(ide if ide is not None else ide_roots())
    ide_recent = sum(v[1] for v in chats.values())
    cli_recent = checks[0].details.get("sessions_8_weeks", 0) * 90 / 56  # ramené à 90 jours
    for root, (n, recent) in chats.items():
        c.findings.append(f"{root} : {n} conversation(s) Copilot Chat, dont {recent} depuis 90 jours")
    if not chats:
        c.findings.append("Aucune conversation Copilot Chat de VS Code trouvée sur ce poste")
    c.findings.append(f"Sessions CLI estimées sur 90 jours : {cli_recent:.0f}")
    c.shareable = [
        f"conversations IDE récentes : {'beaucoup' if ide_recent > cli_recent else 'peu' if ide_recent else 'aucune'}"
    ]
    if ide_recent and ide_recent >= 0.5 * max(cli_recent, 1):
        c.status = "a-confirmer"
        c.action = (
            "Une part importante de l'usage passe par Copilot dans VS Code, invisible pour Sillage (CLI seulement). "
            "Les indicateurs du pilote sous-estimeront l'usage réel : le dire explicitement à la gestion."
        )
        questions.append(
            "L'usage de Copilot dans l'IDE doit-il entrer dans le périmètre (hors pilote CLI pour l'instant) ?"
        )
    checks.append(c)

    # 5. Outillage : cibles d'amélioration ---------------------------------------
    c = Check("outillage", "Skills, agents, serveurs MCP et hooks en usage", "ok")
    skills_av = Counter(s for r in records for s in r.skills_available)
    skills_used = Counter(s.name for r in records for s in r.skills_invoked if s.trigger != "context-load")
    agents = Counter(a for r in records for a in r.custom_agents)
    mcp_status: dict[str, Counter] = {}
    for r in records:
        for srv, st in r.mcp_servers.items():
            mcp_status.setdefault(srv, Counter())[st] += 1
    hook_runs = sum(r.hook_runs for r in records)
    hook_fail = sum(len(r.hook_failures) for r in records)
    c.findings.append(f"Skills disponibles : {len(skills_av)} · utilisés : {len(skills_used)}")
    if skills_used:
        c.findings.append(
            "Skills les plus utilisés : " + ", ".join(f"{s} ({n})" for s, n in skills_used.most_common(5))
        )
    c.findings.append(f"Agents personnalisés : {', '.join(sorted(agents)) or 'aucun'}")
    disabled = sorted(s for s, st in mcp_status.items() if any(k in MCP_DISABLED for k in st))
    for srv, st in sorted(mcp_status.items()):
        c.findings.append(f"MCP {srv} : " + ", ".join(f"{k} ({n})" for k, n in st.most_common()))
    c.findings.append(f"Hooks : {hook_runs} exécutions, {hook_fail} échecs")
    c.shareable = [
        f"skills disponibles/utilisés : {len(skills_av)}/{len(skills_used)}",
        f"agents personnalisés : {len(agents)}",
        f"serveurs MCP désactivés par la politique : {'oui' if disabled else 'non'}",
    ]
    if disabled:
        c.findings.append(
            f"Serveurs MCP désactivés ou arrêtés (politique probable) : {', '.join(disabled)} — mode sans MCP confirmé"
        )
    checks.append(c)

    # 6. Confidentialité : ce qui sortirait ----------------------------------------
    c = Check("confidentialite", "Ce qui sortirait d'un export, et motifs de caviardage à ajouter", "ok")
    level = cfg.export_privacy
    try:
        copies = privacy_copies(records, cfg, level, salt="onboarding")
    except ValueError as exc:  # p. ex. hash_repositories sans sel d'équipe
        c.status = "bloquant"
        c.action = str(exc)
        copies = privacy_copies(records, replace(cfg, hash_repositories=False), level, salt="onboarding")
    out_text = outgoing_text(copies)
    # motifs cherchés aussi dans le texte du niveau « metrics » : ils protègent le magasin local et
    # resteraient nécessaires si l'équipe relevait le niveau d'export
    wider = (
        out_text
        if level != "strict"
        else outgoing_text(privacy_copies(records, replace(cfg, hash_repositories=False), "metrics", salt="onboarding"))
    )
    suggestions = suggest_patterns(wider, cfg.redaction_patterns)
    leaving = " ".join(out_text)
    for sug in suggestions:
        sug["leaves_now"] = bool(re.search(sug["pattern"], leaving))
    c.findings.append(
        f"Niveau d'export : {level} · {len(out_text)} valeurs textuelles distinctes sortiraient · "
        f"{len(cfg.redaction_patterns)} motif(s) d'organisation configuré(s)"
    )
    for sug in suggestions[:12]:
        where = "sort au niveau actuel" if sug["leaves_now"] else "local / niveau metrics seulement"
        c.findings.append(
            f"{sug['kind']} : `{sug['pattern']}` ({sug['count']} occurrence(s), ex. {', '.join(sug['examples'])}) "
            f"— {where}"
        )
    if os.environ.get(cfg.salt_env):
        c.findings.append(
            f"${cfg.salt_env} est défini : il ne sert qu'aux pseudonymes de dépôts (l'identité utilise le sel local)."
        )
    c.shareable = [f"niveau d'export : {level}", f"motifs candidats trouvés : {len(suggestions)}"]
    if level != "strict" and c.status == "ok":
        c.status = "attention"
        c.action = "Le niveau d'export n'est pas `strict` : le confirmer avec le responsable de la protection des RP."
    if suggestions:
        if c.status == "ok":
            c.status = "a-confirmer" if any(s["leaves_now"] for s in suggestions) else "ok"
        c.action = (c.action + " " if c.action else "") + (
            "Valider les motifs proposés (section « Proposition sillage.toml »), puis relire `sillage export --preview`."
        )
        questions.append("Parmi les motifs de caviardage proposés, lesquels retenir pour le sillage.toml d'équipe ?")
    checks.append(c)

    # 7. Lexique de corrections ---------------------------------------------------
    c = Check("corrections", "Le lexique de corrections reconnaît-il vos formulations ?", "ok")
    rate = scan.n_corrections / scan.user_messages if scan.user_messages else 0.0
    c.findings.append(f"{scan.n_corrections} correction(s) reconnue(s) sur {scan.user_messages} messages ({rate:.1%})")
    for t, n in scan.corrections.most_common(5):
        c.findings.append(f"reconnue ({n}×) : « {t} »")
    for t, n in scan.candidates.most_common(10):
        c.findings.append(f"non reconnue, à vérifier ({n}×) : « {t} »")
    c.details["candidates"] = scan.n_candidates
    c.shareable = [f"taux de corrections reconnues : {rate:.1%}", f"candidats non reconnus : {scan.n_candidates}"]
    if scan.candidates:
        c.status = "a-confirmer"
        c.action = (
            "Si des messages « non reconnus » sont bien des corrections, ajouter leur tournure à "
            "`[corrections] extra_patterns` (regex en minuscules, sans accents)."
        )
        questions.append("Les messages « non reconnus » listés sont-ils des corrections de l'agent ?")
    checks.append(c)

    # 8. Seuils -----------------------------------------------------------------
    c = Check("seuils", "Les seuils de détection sont-ils adaptés au volume ?", "ok")
    a = analyze(records, cfg.k_anonymity)
    opps = opportunities(a, cfg.thresholds)
    recent = [r for r in records if r.start and _ts(r.start) and _ts(r.start) >= now - timedelta(days=28)]
    opps_recent = opportunities(analyze(recent, cfg.k_anonymity), cfg.thresholds)
    c.findings.append(
        f"Avec min_sessions = {cfg.thresholds.min_sessions} : {len(opps)} opportunité(s) sur tout l'historique, "
        f"{len(opps_recent)} sur les 28 derniers jours ({len(recent)} sessions)"
    )
    c.shareable = [f"opportunités (historique / 28 j) : {len(opps)} / {len(opps_recent)}"]
    questions.append(
        "Combien de personnes participeront au pilote ? (sert à calibrer min_sessions : visez 5 à 15 "
        "opportunités par rapport mensuel)"
    )
    if len(recent) >= 20 and not opps_recent:
        c.status = "a-confirmer"
        c.action = "Aucune opportunité sur 28 jours malgré le volume : envisager min_sessions = 2 pour le pilote."
    elif len(opps_recent) > 30:
        c.status = "a-confirmer"
        c.action = "Beaucoup d'opportunités pour une seule personne : relever min_sessions pour la vue d'équipe."
    checks.append(c)

    # 9. Installation et collecte planifiée --------------------------------------
    c = Check("installation", "Poste prêt pour la collecte hebdomadaire ?", "ok")
    target = copilot_target or Path(os.path.expanduser(os.environ.get("COPILOT_HOME", "~/.copilot")))
    installed = [a for a in ASSET_AGENTS if (target / "agents" / a).exists()]
    wsl = _is_wsl()
    c.findings.append(
        f"Python {sys.version.split()[0]} · {platform.system()}{' (WSL)' if wsl else ''} · Sillage {__version__}"
    )
    c.findings.append(f"Copilot CLI sur le PATH : {'oui' if shutil.which('copilot') else 'non'}")
    c.findings.append(f"Agents Sillage installés : {', '.join(installed) or 'aucun'}")
    share = os.environ.get("SILLAGE_PARTAGE")
    share_ok = bool(share) and Path(share).is_dir() and os.access(share, os.W_OK)
    c.findings.append(
        f"Dossier partagé ($SILLAGE_PARTAGE) : {share or 'non défini'}"
        + (" — accessible en écriture" if share_ok else " — inaccessible" if share else "")
    )
    task = _scheduled_task_exists()
    c.findings.append(
        f"Tâche planifiée « {SCHEDULED_TASK} » : "
        + ("présente" if task else "absente" if task is False else "non vérifiable sur ce système")
    )
    c.shareable = [
        f"plateforme : {platform.system()}{' (WSL)' if wsl else ''}",
        f"dossier partagé accessible : {'oui' if share_ok else 'non'}",
        f"tâche planifiée : {'oui' if task else 'non'}",
    ]
    todo = []
    if not installed:
        todo.append("`sillage copilot-install`")
    if not share_ok:
        todo.append("définir SILLAGE_PARTAGE vers le dossier partagé du pilote (accès en écriture)")
        questions.append("Quel est le chemin du dossier partagé du pilote, et qui en est le curateur ?")
    if not task:
        todo.append("créer la tâche planifiée hebdomadaire (docs/flux-equipe.md, étape 2)")
    if todo:
        c.status = "attention"
        c.action = "À faire : " + " ; ".join(todo) + "."
    checks.append(c)

    questions.append(
        "Le dispositif (niveau strict, motifs, dossier partagé en lecture pour le curateur seulement, rétention "
        "90 jours) a-t-il été validé par le responsable de la protection des renseignements personnels ?"
    )
    return Onboarding(
        generated_at=now.isoformat(timespec="seconds"),
        sillage_version=__version__,
        checks=checks,
        depots=depots,
        suggested_patterns=suggestions,
        questions=list(dict.fromkeys(questions)),
    )


def _week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-S{w:02d}"


# ------------------------------------------------------------------- rendu
def render_onboarding(o: Onboarding, shareable: bool = False) -> str:
    counts = o.counts()
    lines = [
        "# Fiche d'onboarding Sillage",
        "",
        f"_Générée le {o.generated_at[:10]} par Sillage {o.sillage_version} à partir de tout l'historique du poste._",
        "",
    ]
    if not shareable:
        lines += [
            "> **Document local** : contient des chemins, des noms de dépôts et des extraits caviardés de vos "
            "sessions. Ne pas le partager ; pour le responsable du pilote, utiliser `sillage onboard --partageable`.",
            "",
        ]
    lines += [
        f"**Prêt pour le pilote : {'oui' if o.ready else 'non'}** — "
        + " · ".join(f"{STATUS_LABELS[s]} : {n}" for s, n in counts.items()),
        "",
        "| Point | Statut |",
        "|---|---|",
    ]
    lines += [f"| {c.title} | {STATUS_LABELS[c.status]} |" for c in o.checks]
    for c in o.checks:
        lines += ["", f"## {c.title}", "", f"Statut : {STATUS_LABELS[c.status]}", ""]
        lines += [f"- {f}" for f in (c.shareable if shareable else c.findings)]
        if c.action:
            lines += ["", f"**Action** : {c.action}"]
    if shareable:
        return "\n".join(lines) + "\n"
    if o.questions:
        lines += ["", "## Questions qui restent à trancher (non déductibles des journaux)", ""]
        lines += [f"{i}. {q}" for i, q in enumerate(o.questions, 1)]
    if o.depots:
        lines += ["", "## Brouillon de depots.txt (examples/publier-lecons.sh)", "", "```text"]
        lines += [f"{repo}  {path}" for repo, path in o.depots]
        lines += ["```"]
    if o.suggested_patterns:
        lines += ["", "## Proposition sillage.toml (à valider ligne par ligne)", "", "```toml", "[confidentialite]"]
        lines += ["motifs = ["]
        lines += [f"  '{s['pattern']}',  # {s['kind']}" for s in o.suggested_patterns[:12]]
        lines += ["]", "```"]
    return "\n".join(lines) + "\n"


def write_depots(path: Path, depots: list[tuple[str, str]]) -> None:
    header = "# owner/nom  /chemin/du/clone — généré par `sillage onboard`, à relire\n"
    path.write_text(header + "".join(f"{repo}  {p}\n" for repo, p in depots), encoding="utf-8")
