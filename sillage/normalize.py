"""Transformation d'un journal d'événements Copilot CLI en :class:`SessionRecord`.

Le schéma de référence est ``session-events.schema.json`` livré avec Copilot CLI.
Le code est volontairement tolérant : un champ absent ou un type d'événement inconnu
n'interrompt jamais l'analyse (les types inconnus sont comptés pour détecter une
dérive du format — voir ``sillage doctor``).
"""

from __future__ import annotations

import os
import re
import socket
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import PurePath

from sillage.model import (
    Correction,
    PermissionDecision,
    SessionRecord,
    SkillUse,
    SubagentRun,
    ToolCall,
)
from sillage.reader import RawSession
from sillage.redact import compile_patterns, pseudonymize, redact
from sillage.signatures import command_identifiers, exit_code, primary_command, signature

KNOWN_EVENT_TYPES = {
    "session.start",
    "session.resume",
    "session.remote_steerable_changed",
    "session.error",
    "session.idle",
    "session.title_changed",
    "session.schedule_created",
    "session.schedule_cancelled",
    "session.autopilot_objective_changed",
    "session.info",
    "session.warning",
    "session.model_change",
    "session.mode_changed",
    "session.permissions_changed",
    "session.plan_changed",
    "session.workspace_file_changed",
    "session.handoff",
    "session.truncation",
    "session.snapshot_rewind",
    "session.shutdown",
    "session.context_changed",
    "session.usage_info",
    "session.compaction_start",
    "session.compaction_complete",
    "session.task_complete",
    "user.message",
    "pending_messages.modified",
    "assistant.turn_start",
    "assistant.intent",
    "assistant.reasoning",
    "assistant.reasoning_delta",
    "assistant.streaming_delta",
    "assistant.message",
    "assistant.message_start",
    "assistant.message_delta",
    "assistant.turn_end",
    "assistant.usage",
    "model.call_failure",
    "abort",
    "tool.user_requested",
    "tool.execution_start",
    "tool.execution_partial_result",
    "tool.execution_progress",
    "tool.execution_complete",
    "skill.invoked",
    "subagent.started",
    "subagent.completed",
    "subagent.failed",
    "subagent.selected",
    "subagent.deselected",
    "hook.start",
    "hook.end",
    "hook.progress",
    "system.message",
    "system.notification",
    "permission.requested",
    "permission.completed",
    "user_input.requested",
    "user_input.completed",
    "elicitation.requested",
    "elicitation.completed",
    "sampling.requested",
    "sampling.completed",
    "mcp.oauth_required",
    "mcp.oauth_completed",
    "session.custom_notification",
    "external_tool.requested",
    "external_tool.completed",
    "command.queued",
    "command.execute",
    "command.completed",
    "auto_mode_switch.requested",
    "auto_mode_switch.completed",
    "commands.changed",
    "capabilities.changed",
    "exit_plan_mode.requested",
    "exit_plan_mode.completed",
    "session.tools_updated",
    "session.background_tasks_changed",
    "session.skills_loaded",
    "session.custom_agents_updated",
    "session.mcp_servers_loaded",
    "session.mcp_server_status_changed",
    "session.extensions_loaded",
    "session.canvas.opened",
    "session.canvas.registry_changed",
    "session.canvas.closed",
    "session.extensions.attachments_pushed",
    "mcp_app.tool_call_complete",
    # ajoutés d'après les types générés de @github/copilot-sdk (dist/generated/session-events.d.ts)
    "assistant.fusion_phase_activity",
    "assistant.fusion_phase_completed",
    "assistant.fusion_phase_failed",
    "assistant.fusion_phase_started",
    "assistant.idle",
    "assistant.server_tool_progress",
    "assistant.tool_call_delta",
    "auto_mode_recovery.outcome",
    "auto_mode_recovery_consent.completed",
    "auto_mode_recovery_consent.requested",
    "human_response.recorded",
    "ide.add_file_reference",
    "ide.connection_changed",
    "ide.elicitation_complete",
    "ide.selection_changed",
    "instruction.update",
    "mcp.headers_refresh_completed",
    "mcp.headers_refresh_required",
    "mcp.oauth_recovery_completed",
    "mcp.prompts.list_changed",
    "mcp.resources.list_changed",
    "mcp.tools.list_changed",
    "model.call_finished",
    "permission.assentDetected",
    "permission.carriedForward",
    "permission.contextualAuthorization",
    "permission.messageAuthorization",
    "permission.messageAuthorizationDegraded",
    "permission.messageAuthorizationRead",
    "session.auto_mode_resolved",
    "session.auto_tier_recommendation",
    "session.auto_tier_switch_failed",
    "session.autopilot_stopped",
    "session.binary_asset",
    "session.canvas.recorded",
    "session.canvas.removed",
    "session.canvas.unavailable",
    "session.compaction_summarized",
    "session.completion_receipt",
    "session.context_cleared",
    "session.fusion_completed",
    "session.fusion_resolved",
    "session.fusion_route_failed",
    "session.fusion_route_started",
    "session.indexed_search",
    "session.journal_link",
    "session.managed_plugin_progress",
    "session.managed_settings_enforced",
    "session.managed_settings_resolved",
    "session.mcp_server_needs_reconnect",
    "session.mcp_server_removed",
    "session.mcp_server_tools_loaded",
    "session.mode_notice_delivered",
    "session.model_deselected",
    "session.permission_recovery",
    "session.quota_observation",
    "session.schedule_rearmed",
    "session.session_limits_changed",
    "session.todos_changed",
    "session.usage_checkpoint",
    "session_limits_exhausted.completed",
    "session_limits_exhausted.requested",
    "subagent.configured",
    "tool.shell_output",
    "tool_search.activated",
    "ui.ephemeral_query",
    "workflow.run_settled",
    "workflow.run_started",
    "workflow.run_updated",
}

#: Événements d'état conservés même s'ils proviennent d'un point de reprise (``checkpoint: true``) :
#: ils décrivent le contexte (dépôt, skills, agents, MCP) sans rien compter deux fois.
CHECKPOINT_STATE_EVENTS = {
    "session.start",
    "session.resume",
    "session.context_changed",
    "session.skills_loaded",
    "session.custom_agents_updated",
    "session.mcp_servers_loaded",
    "session.usage_info",
    "session.usage_checkpoint",
}
#: Raisons d'``abort`` qui traduisent une interruption humaine (les autres : limite de crédits, etc.).
HUMAN_ABORT_REASONS = {None, "", "user_initiated", "user_abort", "remote_command"}

SHELL_TOOLS = {
    "bash",
    "powershell",
    "sh",
    "zsh",
    "shell",
    "read_bash",
    "write_bash",
    "stop_bash",
    "read_powershell",
    "write_powershell",
    "stop_powershell",
}
FILE_TOOLS = {"view", "edit", "create", "str_replace_editor", "apply_patch", "write", "read", "multi_edit"}
SEARCH_TOOLS = {"grep", "glob", "rg", "search", "find"}
WEB_TOOLS = {"web_fetch", "web_search", "fetch"}

#: Une correction n'est attribuée à un skill que dans les N messages utilisateur qui suivent son invocation.
SKILL_ATTRIBUTION_WINDOW = 2
#: Un succès suivant un échec de la même famille de commande, dans cette fenêtre d'appels shell, est un contournement.
RECOVERY_WINDOW = 5

# --------------------------------------------------------------- corrections
_CORRECTION_PATTERNS = [
    # français (texte sans accents, en minuscules)
    r"^\s*non(?![-\w])",
    r"\bpas (ca|comme ca|ce que)\b",
    r"\barrete\b",
    r"\bannule\b",
    r"\bdefais\b",
    r"\bce n'?est pas (ce|ca|correct|bon|la bonne|le bon)\b",
    r"\bc'?est (faux|pas bon|incorrect|mauvais|pas correct)\b",
    r"\btu as (oublie|casse|supprime|efface|ignore)\b",
    r"\b(toujours|encore) (pas|en erreur|le meme|la meme)\b",
    r"\bca (ne )?(marche|fonctionne|compile|passe) (toujours |encore )?pas\b",
    r"\bje t'?ai (dit|demande)\b",
    r"\bpourquoi (as-tu|tu as|t'as)\b",
    r"\bremets?\b.*\b(comme|avant)\b",
    r"\bmauvais(e)? (fichier|groupe|branche|projet|endroit)\b",
    r"\bne (fais|touche|modifie) (pas|jamais)\b",
    # anglais
    r"^\s*no\b[,.! ]",
    r"\bthat'?s (not|wrong)\b",
    r"\bwrong\b",
    r"\bundo\b",
    r"\brevert\b",
    r"\bstill (failing|broken|not working|wrong)\b",
    r"\bdoesn'?t work\b",
    r"\bi (told|asked) you\b",
    r"\bwhy did you\b",
    r"\bdon'?t (do|touch|change)\b",
]
_CORRECTION_RE = re.compile("|".join(f"(?:{p})" for p in _CORRECTION_PATTERNS))


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("’", "'")


def is_correction(text: str | None, extra_patterns: list[str] | None = None) -> bool:
    if not text:
        return False
    folded = _fold(text)[:600]
    if _CORRECTION_RE.search(folded):
        return True
    return any(re.search(p, folded) for p in (extra_patterns or []))


# ------------------------------------------------------------------ friction
@dataclass
class FrictionWeights:
    corrections: float = 15
    denials: float = 15
    aborts: float = 15
    tool_failure_rate: float = 20
    loops: float = 10
    errors: float = 10
    compactions: float = 5
    reformulations: float = 10
    # minutes perdues estimées (indicatif, paramétrable)
    min_per_correction: float = 2.0
    min_per_denial: float = 1.0
    min_per_abort: float = 3.0
    min_per_tool_failure: float = 0.5
    min_per_loop: float = 5.0
    min_per_reformulation: float = 1.5


def compute_friction(rec: SessionRecord, w: FrictionWeights | None = None) -> None:
    w = w or FrictionWeights()
    corrections = sum(1 for c in rec.corrections if c.category == "correction")
    reformulations = sum(1 for c in rec.corrections if c.category == "reformulation")
    calls = len(rec.tool_calls)
    failures = rec.tool_failures
    rate = failures / calls if calls else 0.0
    score = (
        w.corrections * min(1.0, corrections / 2)
        + w.denials * min(1.0, rec.user_denials / 2)
        + w.aborts * min(1.0, rec.aborts / 1)
        + w.tool_failure_rate * min(1.0, rate * 2.5)
        + w.loops * min(1.0, rec.failure_loops / 1)
        + w.errors * min(1.0, (len(rec.session_errors) + rec.model_failures) / 2)
        + w.compactions * min(1.0, (rec.compactions + rec.truncations) / 2)
        + w.reformulations * min(1.0, reformulations / 2)
    )
    rec.friction_score = round(min(100.0, score), 1)
    rec.friction_label = (
        "fluide" if score < 15 else "accrochages" if score < 35 else "difficile" if score < 60 else "pénible"
    )
    rec.estimated_minutes_lost = round(
        corrections * w.min_per_correction
        + rec.user_denials * w.min_per_denial
        + rec.aborts * w.min_per_abort
        + failures * w.min_per_tool_failure
        + rec.failure_loops * w.min_per_loop
        + reformulations * w.min_per_reformulation,
        1,
    )


# ------------------------------------------------------------------- helpers
def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _category(name: str, mcp_server: str | None) -> str:
    if mcp_server:
        return "mcp"
    if name in SHELL_TOOLS:
        return "shell"
    if name in FILE_TOOLS:
        return "file"
    if name in SEARCH_TOOLS:
        return "search"
    if name == "task":
        return "subagent"
    if name == "skill":
        return "skill"
    if name in WEB_TOOLS:
        return "web"
    return "other"


def _target(category: str, args: object, data: dict) -> str | None:
    if not isinstance(args, dict):
        return data.get("mcpToolName") if category == "mcp" else None
    if category == "shell":
        return primary_command(args.get("command"))
    if category == "file":
        path = args.get("path") or args.get("file_path") or args.get("filePath")
        if isinstance(path, str) and path:
            suffix = PurePath(path).suffix.lower()
            return suffix or PurePath(path).name.lower()[:30]
        return None
    if category == "mcp":
        return data.get("mcpToolName")
    if category == "subagent":
        agent = args.get("agent_type") or args.get("agentType") or args.get("agent")
        return str(agent)[:60] if agent else None
    if category == "skill":
        skill = args.get("skill") or args.get("name")
        return str(skill)[:60] if skill else None
    return None


def _branch_kind(branch: str | None) -> str | None:
    if not branch:
        return None
    b = branch.lower()
    if b in {"main", "master", "develop", "dev", "trunk"}:
        return "main"
    if re.match(r"^(feature|feat|fix|bugfix|hotfix|chore|refactor|release|copilot)/", b):
        return "feature"
    return "other"


def _result_text(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("content", "detailedContent"):
        if isinstance(result.get(key), str):
            return result[key]
    return None


def _result_exit_code(result: object, data: dict | None = None) -> int | None:
    shell = (data or {}).get("shellExecution")
    if isinstance(shell, dict) and isinstance(shell.get("exitCode"), int):
        return shell["exitCode"]  # champ structuré (Copilot CLI récents)
    if not isinstance(result, dict):
        return None
    for block in result.get("contents") or []:
        if isinstance(block, dict) and isinstance(block.get("exitCode"), int):
            return block["exitCode"]
    return exit_code(_result_text(result))


def default_user_identity() -> str:
    try:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or os.getlogin()
    except OSError:
        user = "inconnu"
    return f"{user}@{socket.gethostname()}"


# --------------------------------------------------------------- normalizer
def normalize(
    raw: RawSession,
    *,
    salt: str,
    user_identity: str | None = None,
    privacy_level: str = "metrics",
    extra_correction_patterns: list[str] | None = None,
    weights: FrictionWeights | None = None,
    redaction_patterns: list[str] | None = None,
) -> SessionRecord:
    """Construit le :class:`SessionRecord` d'une session.

    ``privacy_level`` : ``metrics`` (aucun texte libre) ou ``redacted`` (extraits caviardés).
    """
    keep_text = privacy_level == "redacted"
    extra = compile_patterns(redaction_patterns)

    def sig(text: str | None) -> str | None:
        return signature(text, extra=extra)

    def red(text: str | None, n: int) -> str | None:
        return redact(text, n, extra=extra)

    rec = SessionRecord(
        session_id=raw.session_id,
        user=pseudonymize(user_identity or default_user_identity(), salt),
        privacy_level=privacy_level,
    )
    tools: dict[str, ToolCall] = {}
    tool_start_ts: dict[str, datetime] = {}
    perm_requests: dict[str, PermissionDecision] = {}
    shell_ids_by_call: dict[str, list[str]] = {}
    subagent_runs: dict[str, SubagentRun] = {}
    current_skill: str | None = None
    skill_budget = 0  # nb de messages utilisateur après une invocation de skill encore attribués à ce skill
    pending_denial = False  # une correction juste après un refus de permission vise l'action refusée
    last_user_msg: str | None = None
    seen_assistant = False
    timestamps: list[datetime] = []
    usage_seen = False
    unknown: set[str] = set()
    last_meaningful: tuple[str, bool] | None = None  # (type, failed?)
    fail_streak: dict[tuple, int] = {}
    recent_shell: list[ToolCall] = []  # derniers appels shell (détection des contournements)
    call_aiu = 0  # somme des nano-AIU par appel (repli si ni point de contrôle ni fermeture)

    for ev in raw.events:
        etype = ev.get("type", "")
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        ts = _ts(ev.get("timestamp"))
        if ts:
            timestamps.append(ts)
        is_sub = bool(ev.get("agentId")) or bool(data.get("parentToolCallId"))
        if etype not in KNOWN_EVENT_TYPES:
            unknown.add(etype)
            continue
        if ev.get("checkpoint") is True and etype not in CHECKPOINT_STATE_EVENTS:
            rec.replayed_events += 1  # rejoué depuis un point de reprise : déjà compté dans l'historique d'origine
            continue

        if etype in ("session.start", "session.resume", "session.context_changed"):
            ctx = data.get("context") if etype != "session.context_changed" else data
            ctx = ctx if isinstance(ctx, dict) else {}
            rec.copilot_version = data.get("copilotVersion") or rec.copilot_version
            rec.repository = ctx.get("repository") or rec.repository
            rec.branch_kind = _branch_kind(ctx.get("branch")) or rec.branch_kind

        elif etype == "user.message":
            if is_sub:
                continue
            rec.user_messages += 1
            if skill_budget > 0:
                skill_budget -= 1
            elif current_skill:
                current_skill = None
            content = data.get("content") if isinstance(data.get("content"), str) else ""
            if seen_assistant and is_correction(content, extra_correction_patterns):
                rec.corrections.append(
                    Correction(
                        "correction",
                        after_skill=None if pending_denial else current_skill,
                        excerpt=red(content, 200) if keep_text else None,
                    )
                )
            elif (
                last_user_msg
                and len(content) > 20
                and SequenceMatcher(None, last_user_msg.lower(), content.lower()).ratio() > 0.85
            ):
                rec.corrections.append(
                    Correction(
                        "reformulation", after_skill=current_skill, excerpt=red(content, 200) if keep_text else None
                    )
                )
            last_user_msg = content
            pending_denial = False
            last_meaningful = ("user.message", False)

        elif etype == "assistant.turn_start" and not is_sub:
            rec.assistant_turns += 1

        elif etype == "assistant.message":
            seen_assistant = True
            if not is_sub:
                last_meaningful = ("assistant.message", False)
            model = data.get("model")
            if model and not usage_seen:
                rec.models[model] = rec.models.get(model, 0) + 1

        elif etype == "assistant.usage":
            if not usage_seen:
                rec.models = {}
                usage_seen = True
            model = data.get("model") or "inconnu"
            rec.models[model] = rec.models.get(model, 0) + 1
            rec.input_tokens += int(data.get("inputTokens") or 0)
            rec.output_tokens += int(data.get("outputTokens") or 0)
            rec.cache_read_tokens += int(data.get("cacheReadTokens") or 0)
            usage = data.get("copilotUsage") if isinstance(data.get("copilotUsage"), dict) else {}
            if isinstance(usage.get("totalNanoAiu"), (int, float)):
                call_aiu += int(usage["totalNanoAiu"])

        elif etype == "session.usage_checkpoint":
            if isinstance(data.get("totalNanoAiu"), (int, float)):
                rec.nano_aiu = max(rec.nano_aiu or 0, int(data["totalNanoAiu"]))

        elif etype == "model.call_failure":
            rec.model_failures += 1

        elif etype == "abort":
            if data.get("reason") not in HUMAN_ABORT_REASONS:
                continue  # p. ex. limite de crédits IA atteinte : pas une interruption humaine
            rec.aborts += 1
            rec.corrections.append(Correction("interruption", after_skill=current_skill))
            last_meaningful = ("abort", True)

        elif etype == "session.error":
            err_sig = sig(data.get("message")) or "erreur"
            code = data.get("statusCode")
            rec.session_errors.append(f"{data.get('errorType') or 'error'}{f' {code}' if code else ''}: {err_sig}")
            last_meaningful = ("session.error", True)

        elif etype == "tool.execution_start":
            cid = data.get("toolCallId") or f"_anon{len(tools)}"
            name = data.get("toolName") or "inconnu"
            server = data.get("mcpServerName")
            cat = _category(name, server)
            call = ToolCall(
                name=name,
                category=cat,
                mcp_server=server,
                target=_target(cat, data.get("arguments"), data),
                subagent=is_sub,
            )
            if cat == "shell" and keep_text and isinstance(data.get("arguments"), dict):
                cmd = data["arguments"].get("command")
                call.command_excerpt = red(cmd, 160) if isinstance(cmd, str) else None
            tools[cid] = call
            if ts:
                tool_start_ts[cid] = ts
            rec.tool_calls.append(call)

        elif etype == "tool.execution_complete":
            cid = data.get("toolCallId")
            call = tools.get(cid) if cid else None
            if call is None:  # complétion sans démarrage (journal tronqué)
                call = ToolCall(name="inconnu", category="other", subagent=is_sub)
                rec.tool_calls.append(call)
            call.user_requested = bool(data.get("isUserRequested"))
            out_text = _result_text(data.get("result"))
            if out_text is not None:
                call.output_chars = len(out_text)
            success = data.get("success")
            text = None
            if success is False:
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                text = err.get("message") or _result_text(data.get("result"))
                call.success = False
            else:
                code = _result_exit_code(data.get("result"), data) if call.category == "shell" else None
                call.exit_code = code
                if code not in (None, 0):
                    call.success = False
                    text = _result_text(data.get("result"))
                else:
                    call.success = True if success is not None or code == 0 else None
            if call.success is False:
                call.error_signature = sig(text) or "échec sans message"
                if keep_text:
                    call.error_excerpt = red(text, 300)
            if cid and cid in shell_ids_by_call and call.category == "shell":
                ids = [i for i in shell_ids_by_call[cid] if i.split(" ")[0] not in {"cd", "echo"}]
                call.target = ids[0] if ids else call.target
            if cid and ts and cid in tool_start_ts:
                call.duration_ms = int((ts - tool_start_ts[cid]).total_seconds() * 1000)
            # boucles d'échec : même appel qui échoue de la même façon >= 3 fois d'affilée
            key = (call.name, call.target, call.error_signature)
            if call.success is False:
                fail_streak[key] = fail_streak.get(key, 0) + 1
                if fail_streak[key] == 3:
                    rec.failure_loops += 1
            else:
                fail_streak = {k: v for k, v in fail_streak.items() if (k[0], k[1]) != (call.name, call.target)}
            if call.category == "shell" and call.target and not is_sub:
                if call.success:
                    head = call.target.split(" ")[0]
                    for prev in recent_shell:
                        if prev.success is not False or prev.recovered_by or prev.recovered_by_detail:
                            continue
                        if not prev.target or prev.target.split(" ")[0] != head:
                            continue
                        if prev.target != call.target:
                            prev.recovered_by = call.target
                            prev.recovered_by_detail = call.command_excerpt
                        elif call.command_excerpt and call.command_excerpt != prev.command_excerpt:
                            # même commande, options différentes (ex. -s settings.xml) : utile au niveau "redacted"
                            prev.recovered_by_detail = call.command_excerpt
                recent_shell = (recent_shell + [call])[-RECOVERY_WINDOW:]
            if not is_sub:
                last_meaningful = ("tool.execution_complete", call.success is False)

        elif etype == "skill.invoked":
            name = data.get("name") or "inconnu"
            rec.skills_invoked.append(
                SkillUse(
                    name=name, trigger=data.get("trigger"), source=data.get("source"), plugin=data.get("pluginName")
                )
            )
            if data.get("trigger") != "context-load":
                current_skill = name
                skill_budget = SKILL_ATTRIBUTION_WINDOW

        elif etype == "session.skills_loaded":
            names = []
            for s in data.get("skills") or []:
                if isinstance(s, dict) and s.get("name") and s.get("enabled", True) is not False:
                    names.append(s["name"])
                elif isinstance(s, str):
                    names.append(s)
            rec.skills_available = sorted(set(names))

        elif etype == "session.custom_agents_updated":
            rec.custom_agents = sorted(
                {a.get("name") or a.get("id") for a in data.get("agents") or [] if isinstance(a, dict)} - {None}
            )
            rec.agent_definition_errors = [sig(e) or "erreur" for e in data.get("errors") or [] if isinstance(e, str)]

        elif etype == "subagent.selected":
            rec.selected_agent = data.get("agentName")

        elif etype == "subagent.started":
            run = SubagentRun(name=data.get("agentName") or "inconnu")
            subagent_runs[data.get("toolCallId") or f"_sub{len(subagent_runs)}"] = run
            rec.subagents.append(run)

        elif etype in ("subagent.completed", "subagent.failed"):
            run = subagent_runs.get(data.get("toolCallId") or "")
            if run is None:
                run = SubagentRun(name=data.get("agentName") or "inconnu")
                rec.subagents.append(run)
            run.success = etype == "subagent.completed"
            run.duration_ms = data.get("durationMs")
            run.total_tokens = data.get("totalTokens")
            run.total_tool_calls = data.get("totalToolCalls")
            if etype == "subagent.failed":
                run.error_signature = sig(str(data.get("error") or "")) or "échec"

        elif etype == "session.mcp_servers_loaded":
            for s in data.get("servers") or []:
                if isinstance(s, dict) and s.get("name"):
                    rec.mcp_servers[s["name"]] = s.get("status") or "?"
                    if s.get("error"):
                        rec.mcp_server_errors.append(f"{s['name']}: {sig(s['error']) or 'erreur'}")

        elif etype == "session.mcp_server_status_changed":
            name = data.get("serverName") or "?"
            rec.mcp_servers[name] = data.get("status") or "?"
            if data.get("error"):
                rec.mcp_server_errors.append(f"{name}: {sig(data['error']) or 'erreur'}")

        elif etype == "permission.requested":
            req = data.get("permissionRequest") if isinstance(data.get("permissionRequest"), dict) else {}
            kind = req.get("kind") or "?"
            target = None
            read_only = None
            if kind == "shell":
                flags = [c.get("readOnly") for c in req.get("commands") or [] if isinstance(c, dict)]
                if flags and all(isinstance(f, bool) for f in flags):
                    read_only = all(flags)
                ids = [
                    c.get("identifier")
                    for c in req.get("commands") or []
                    if isinstance(c, dict) and c.get("identifier")
                ]
                if not ids:
                    ids = command_identifiers(req.get("fullCommandText"))
                ids = [i for i in ids if i]
                target = next((i for i in ids if i.split(" ")[0] not in {"cd", "echo"}), ids[0] if ids else None)
                if req.get("toolCallId"):
                    shell_ids_by_call[req["toolCallId"]] = ids
            elif kind == "mcp":
                target = f"{req.get('serverName')}/{req.get('toolName')}"
                read_only = req.get("readOnly") if isinstance(req.get("readOnly"), bool) else None
            elif kind in ("write", "read"):
                path = req.get("fileName") or req.get("path")
                target = PurePath(path).suffix.lower() if isinstance(path, str) else None
            perm_requests[data.get("requestId") or f"_p{len(perm_requests)}"] = PermissionDecision(
                kind=kind, result="en-attente", target=target, read_only=read_only
            )

        elif etype == "permission.completed":
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            decision = perm_requests.get(data.get("requestId") or "")
            if decision is None:
                decision = PermissionDecision(kind="?", result="en-attente")
            decision.result = result.get("kind") or "?"
            rec.permissions.append(decision)
            if decision.result == "denied-interactively-by-user":
                pending_denial = True

        elif etype == "hook.start":
            rec.hook_runs += 1

        elif etype == "hook.end":
            if data.get("success") is False:
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                rec.hook_failures.append(f"{data.get('hookType') or 'hook'}: {sig(err.get('message')) or 'échec'}")

        elif etype == "session.compaction_complete":
            rec.compactions += 1

        elif etype == "session.truncation":
            rec.truncations += 1

        elif etype == "session.usage_info":
            tdt = data.get("toolDefinitionsTokens")
            if isinstance(tdt, int):
                rec.tool_definitions_tokens = max(rec.tool_definitions_tokens or 0, tdt)
            if isinstance(data.get("tokenLimit"), int):
                rec.context_token_limit = data["tokenLimit"]

        elif etype == "session.task_complete":
            rec.task_completed = bool(data.get("success", True))
            last_meaningful = ("session.task_complete", False)

        elif etype == "exit_plan_mode.completed":
            if data.get("approved") is False:
                rec.plan_rejections += 1

        elif etype == "session.shutdown":
            rec.shutdown_type = data.get("shutdownType")
            if isinstance(data.get("totalNanoAiu"), (int, float)):
                rec.nano_aiu = max(rec.nano_aiu or 0, int(data["totalNanoAiu"]))
            if isinstance(data.get("totalPremiumRequests"), (int, float)):
                rec.premium_requests = data["totalPremiumRequests"]
            changes = data.get("codeChanges") if isinstance(data.get("codeChanges"), dict) else {}
            rec.lines_added = int(changes.get("linesAdded") or 0)
            rec.lines_removed = int(changes.get("linesRemoved") or 0)
            rec.files_modified = len(changes.get("filesModified") or [])
            metrics = data.get("modelMetrics") if isinstance(data.get("modelMetrics"), dict) else {}
            if metrics:
                tot_in = sum(int((m.get("usage") or {}).get("inputTokens") or 0) for m in metrics.values())
                tot_out = sum(int((m.get("usage") or {}).get("outputTokens") or 0) for m in metrics.values())
                if tot_in >= rec.input_tokens:
                    rec.input_tokens, rec.output_tokens = tot_in, max(tot_out, rec.output_tokens)
                    rec.models = {k: int((v.get("requests") or {}).get("count") or 0) for k, v in metrics.items()}
            if isinstance(data.get("toolDefinitionsTokens"), int):
                rec.tool_definitions_tokens = max(rec.tool_definitions_tokens or 0, data["toolDefinitionsTokens"])

    # Requêtes de permission jamais complétées (session interrompue) : ignorées.
    if call_aiu and (rec.nano_aiu or 0) < call_aiu:
        rec.nano_aiu = call_aiu
    if timestamps:
        timestamps.sort()
        rec.start, rec.end = timestamps[0].isoformat(), timestamps[-1].isoformat()
        rec.duration_s = round((timestamps[-1] - timestamps[0]).total_seconds(), 1)
        active = 0.0
        for a, b in zip(timestamps, timestamps[1:], strict=False):
            active += min((b - a).total_seconds(), 300.0)  # inactivité > 5 min exclue
        rec.active_s = round(active, 1)
    if not rec.repository and raw.workspace.get("repository"):
        rec.repository = raw.workspace["repository"]
    rec.abandoned = bool(
        rec.shutdown_type == "error"
        or (last_meaningful is not None and last_meaningful[1] and rec.task_completed is not True)
    )
    rec.unknown_event_types = sorted(unknown)
    compute_friction(rec, weights)
    return rec
