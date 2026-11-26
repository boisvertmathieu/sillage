"""Jeu de données synthétique, conforme au schéma des événements Copilot CLI.

Sert à la démonstration (``sillage demo``) et aux tests. Les irritants plantés
correspondent à des situations réalistes d'une DP : script npm mal nommé, proxy
Artifactory, jeton ServiceNow expiré, hook de garde-fou cassé, serveur MCP qui ne
démarre pas, agent d'AQ instable, skill dormant, sorties terraform volumineuses...
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEMO_NOW = datetime(2026, 11, 25, 17, 0, tzinfo=UTC)

USERS = [
    "alex@poste-01",
    "sam@poste-02",
    "charlie@poste-03",
    "dominique@poste-04",
    "frederique@poste-05",
    "maxime@poste-06",
]
REPOS = {
    "dp/portail-citoyen": 0.30,
    "dp/api-dossiers": 0.28,
    "dp/infra-terraform": 0.14,
    "dp/batch-facturation": 0.16,
    "dp/outillage-ia": 0.12,
}
SKILLS = [
    "splunk-investigation",
    "servicenow-incident",
    "jira-recit",
    "dynatrace-problemes",
    "confluence-recherche",
    "revue-securite",
]
AGENTS = ["orchestrateur-dev", "analyste-po", "aq-tests", "architecte"]
MCP_SERVERS = ["splunk", "servicenow", "jira", "dynatrace"]
MODELS = ["claude-sonnet-4.6", "gpt-5.2", "claude-opus-4.6"]


_READ_ONLY = {"git status", "git diff", "git log", "ls", "cat", "grep"}


class _Builder:
    def __init__(self, rng: random.Random, start: datetime) -> None:
        self.rng = rng
        self.t = start
        self.events: list[dict] = []
        self.parent: str | None = None
        self.turn = 0

    def tick(self, lo: float = 1, hi: float = 20) -> None:
        self.t += timedelta(seconds=self.rng.uniform(lo, hi))

    def emit(self, etype: str, data: dict, agent_id: str | None = None) -> None:
        eid = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))
        ev = {
            "id": eid,
            "timestamp": self.t.isoformat().replace("+00:00", "Z"),
            "parentId": self.parent,
            "type": etype,
            "data": data,
        }
        if agent_id:
            ev["agentId"] = agent_id
        self.events.append(ev)
        self.parent = eid

    def call_id(self) -> str:
        return "call_" + "".join(self.rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(16))

    # ------------------------------------------------------------- briques
    def user(self, text: str) -> None:
        self.tick(20, 240)
        self.emit("user.message", {"content": text, "interactionId": str(uuid.uuid4())})

    def assistant(self, model: str, text: str = "", tool_names: list[str] | None = None) -> None:
        self.turn += 1
        tid = str(self.turn)
        self.emit("assistant.turn_start", {"turnId": tid, "interactionId": "i"})
        self.tick(2, 12)
        self.emit(
            "assistant.usage",
            {
                "model": model,
                "inputTokens": self.rng.randint(18000, 60000),
                "outputTokens": self.rng.randint(200, 2500),
                "cacheReadTokens": self.rng.randint(10000, 45000),
                "cacheWriteTokens": 0,
                "copilotUsage": {"totalNanoAiu": self.rng.randint(20, 90) * 10_000_000},
                "duration": 4000,
                "apiCallId": "a",
                "initiator": "agent",
            },
        )
        self.emit(
            "assistant.message",
            {
                "messageId": str(uuid.uuid4()),
                "content": text,
                "model": model,
                "toolRequests": [{"toolCallId": "x", "name": n} for n in (tool_names or [])],
                "turnId": tid,
            },
        )

    def tool(
        self,
        name: str,
        args: dict,
        *,
        ok: bool = True,
        output: str = "ok",
        error: str | None = None,
        mcp: tuple[str, str] | None = None,
        exit_code: int | None = None,
        perm: str | None = None,
        perm_ids: list[str] | None = None,
        agent_id: str | None = None,
    ) -> str:
        cid = self.call_id()
        if perm is not None:
            rid = str(uuid.uuid4())
            req: dict = {
                "kind": "shell" if name == "bash" else ("mcp" if mcp else "write"),
                "toolCallId": cid,
                "intention": "x",
            }
            if name == "bash":
                req["fullCommandText"] = args.get("command", "")
                req["commands"] = [{"identifier": i, "readOnly": i in _READ_ONLY} for i in (perm_ids or [])]
            if mcp:
                req.update({"serverName": mcp[0], "toolName": mcp[1]})
            self.emit("permission.requested", {"requestId": rid, "permissionRequest": req})
            self.tick(1, 25)
            self.emit("permission.completed", {"requestId": rid, "toolCallId": cid, "result": {"kind": perm}})
            if perm.startswith("denied"):
                return cid
        start = {"toolCallId": cid, "toolName": name, "arguments": args, "turnId": str(self.turn)}
        if mcp:
            start.update({"mcpServerName": mcp[0], "mcpToolName": mcp[1]})
        self.emit("tool.execution_start", start, agent_id)
        self.tick(0.5, 30)
        done: dict = {"toolCallId": cid, "success": ok, "turnId": str(self.turn)}
        if ok:
            content = output if exit_code is None else f"{output}\n<exited with exit code {exit_code}>"
            done["result"] = {"content": content}
        else:
            done["error"] = {"message": error or "erreur", "code": "failure"}
        self.emit("tool.execution_complete", done, agent_id)
        return cid


def _session(rng: random.Random, user: str, start: datetime, repo: str) -> list[dict]:
    b = _Builder(rng, start)
    model = rng.choices(MODELS, weights=[0.55, 0.3, 0.15])[0]
    branch = rng.choice(["main", "feature/DP-1432-recherche", "fix/DP-1501-timeout", "copilot/refacto"])
    b.emit(
        "session.start",
        {
            "sessionId": str(uuid.uuid4()),
            "version": 1,
            "producer": "copilot-agent",
            "copilotVersion": "1.0.115",
            "startTime": b.t.isoformat(),
            "selectedModel": model,
            "context": {
                "cwd": f"/home/{user.split('@')[0]}/src/{repo.split('/')[1]}",
                "repository": repo,
                "hostType": "github",
                "branch": branch,
            },
        },
    )
    b.emit("session.skills_loaded", {"skills": [{"name": s, "source": "project", "enabled": True} for s in SKILLS]})
    agent_errors = ["Unknown tool 'jira_search' referenced by agent 'analyste-po'"] if start.day % 9 == 0 else []
    b.emit(
        "session.custom_agents_updated",
        {"agents": [{"id": a, "name": a} for a in AGENTS], "warnings": [], "errors": agent_errors},
    )
    servers = []
    for s in MCP_SERVERS:
        if s == "dynatrace" and rng.random() < 0.35:
            servers.append(
                {
                    "name": s,
                    "status": "failed",
                    "error": "spawn dynatrace-mcp ENOENT (/home/x/.local/bin/dynatrace-mcp)",
                }
            )
        else:
            servers.append({"name": s, "status": "connected"})
    b.emit("session.mcp_servers_loaded", {"servers": servers})
    tool_defs = rng.randint(17000, 26000) if repo != "dp/outillage-ia" else rng.randint(9000, 12000)
    b.emit(
        "session.usage_info",
        {
            "tokenLimit": 128000,
            "currentTokens": tool_defs + 6000,
            "systemTokens": 6000,
            "conversationTokens": 0,
            "toolDefinitionsTokens": tool_defs,
            "messagesLength": 1,
            "isInitial": True,
        },
    )

    hook_broken = start >= DEMO_NOW - timedelta(days=12) and user in USERS[:3]
    current_skill: str | None = None
    turns = rng.randint(2, 7)
    for i in range(turns):
        if i == 0:
            b.user(
                rng.choice(
                    [
                        "Ajoute la pagination à l'endpoint de recherche des dossiers",
                        "Corrige le test qui échoue sur le calcul des frais",
                        "Analyse l'incident INC0042117 et propose un correctif",
                        "Prépare le récit DP-1432 : critères d'acceptation et tâches",
                        "Mets à jour le module terraform du réseau",
                        "Pourquoi le batch de facturation est lent depuis lundi ?",
                    ]
                )
            )
        else:
            b.user(
                rng.choice(
                    [
                        "Continue",
                        "Ok, maintenant les tests",
                        "Parfait, ajoute la doc",
                        "Peux-tu vérifier les logs ?",
                        "Ok pour moi",
                    ]
                )
            )
        if hook_broken:
            b.emit("hook.start", {"hookInvocationId": str(uuid.uuid4()), "hookType": "preToolUse", "input": {}})
            b.emit(
                "hook.end",
                {
                    "hookInvocationId": "h",
                    "hookType": "preToolUse",
                    "success": False,
                    "error": {"message": "/bin/sh: 1: jq: not found\nhook exited with code 127"},
                },
            )
        b.assistant(model, "Je regarde.", ["view", "grep"])
        for _ in range(rng.randint(1, 4)):
            kind = rng.random()
            if kind < 0.45:
                b.tool(
                    "view",
                    {"path": f"src/{rng.choice(['app.ts', 'Service.java', 'main.tf', 'README.md'])}"},
                    output="x" * rng.randint(400, 4000),
                )
            elif kind < 0.7:
                b.tool("grep", {"pattern": "TODO"}, output="src/a.ts:12: TODO")
            else:
                b.tool("edit", {"path": f"src/{rng.choice(['app.ts', 'Service.java', 'main.tf'])}"}, output="edited")

        # ---------------- irritants par dépôt
        if repo == "dp/portail-citoyen" and i == 1 and rng.random() < 0.8:
            n = 3 if rng.random() < 0.35 else 1
            for _ in range(n):
                b.tool(
                    "bash",
                    {"command": "npm test"},
                    perm="approved",
                    perm_ids=["npm test"],
                    output='npm error Missing script: "test"\nnpm error\nnpm error To see a list of scripts, run:\n'
                    "npm error   npm run",
                    exit_code=1,
                )
            b.tool("bash", {"command": "npm run test:unit"}, output="Tests: 142 passed", exit_code=0)
        if repo == "dp/api-dossiers" and i == 1 and rng.random() < 0.7:
            b.tool(
                "bash",
                {"command": "cd services/api && mvn -q test"},
                perm="approved",
                perm_ids=["cd", "mvn test"],
                output="[ERROR] Failed to execute goal on project api-dossiers: Could not resolve dependencies for "
                "project ca.dp:api-dossiers:jar:2.3.1: Failed to collect dependencies at "
                "org.postgresql:postgresql:jar:42.7.4: PKIX path building failed: unable to find valid "
                "certification path to requested target",
                exit_code=1,
            )
            if rng.random() < 0.5:
                b.user("Non, ça ne marche toujours pas, utilise le settings.xml de l'équipe")
                b.assistant(model, "Je réessaie avec le settings.xml.", ["bash"])
                b.tool("bash", {"command": "mvn -s ~/.m2/settings-dp.xml test"}, output="BUILD SUCCESS", exit_code=0)
        if repo == "dp/infra-terraform" and rng.random() < 0.6:
            b.tool(
                "bash",
                {"command": "terraform plan -no-color"},
                perm="approved",
                perm_ids=["terraform plan"],
                output="  # module.reseau.azurerm_subnet.app will be updated in-place\n" * 450,
                exit_code=0,
            )
            if rng.random() < 0.5:
                b.emit("session.compaction_start", {"conversationTokens": 98000})
                b.emit(
                    "session.compaction_complete",
                    {"success": True, "preCompactionTokens": 112000, "postCompactionTokens": 31000},
                )
        # ---------------- skills
        r = rng.random()
        if i == 0 and r < 0.22:
            current_skill = "servicenow-incident"
            b.emit(
                "skill.invoked",
                {"name": current_skill, "path": "x", "content": "...", "trigger": "agent-invoked", "source": "project"},
            )
            failed = rng.random() < 0.4
            b.tool(
                "servicenow-get_incident",
                {"number": "INC0042117"},
                mcp=("servicenow", "get_incident"),
                ok=not failed,
                error="HTTP 401 Unauthorized: OAuth token expired for instance dpprod",
                output='{"number":"INC0042117"}',
            )
            if rng.random() < 0.55:
                b.user(
                    rng.choice(
                        [
                            "Non, c'est le mauvais groupe d'assignation, utilise DP-SOUTIEN-N2",
                            "Ce n'est pas le bon incident, je t'ai demandé le parent",
                            "Non, ne ferme pas l'incident, ajoute seulement une note de travail",
                        ]
                    )
                )
                b.assistant(model, "Je corrige.", ["servicenow-update_incident"])
                b.tool("servicenow-update_incident", {}, mcp=("servicenow", "update_incident"), output="ok")
        elif i == 0 and r < 0.55:
            current_skill = "splunk-investigation"
            b.emit(
                "skill.invoked",
                {"name": current_skill, "path": "x", "content": "...", "trigger": "agent-invoked", "source": "project"},
            )
            for _ in range(rng.randint(1, 3)):
                b.tool(
                    "splunk-search",
                    {"spl": "index=app error"},
                    mcp=("splunk", "search"),
                    output="12 events",
                    ok=rng.random() > 0.05,
                    error="search timed out after 60s",
                )
        elif i == 0 and r < 0.70:
            current_skill = "jira-recit"
            b.emit(
                "skill.invoked",
                {"name": current_skill, "path": "x", "content": "...", "trigger": "user-invoked", "source": "project"},
            )
            b.tool("jira-get_issue", {"key": "DP-1432"}, mcp=("jira", "get_issue"), output="{}")
        elif i == 0 and r < 0.705:
            b.emit(
                "skill.invoked",
                {
                    "name": "confluence-recherche",
                    "path": "x",
                    "content": "...",
                    "trigger": "agent-invoked",
                    "source": "project",
                },
            )
        elif i == 0 and r < 0.75:
            b.emit(
                "skill.invoked",
                {
                    "name": "revue-securite",
                    "path": "x",
                    "content": "...",
                    "trigger": "user-invoked",
                    "source": "project",
                },
            )
        # ---------------- sous-agents
        if rng.random() < 0.18:
            name = rng.choices(["aq-tests", "explore", "analyste-po"], weights=[0.5, 0.35, 0.15])[0]
            cid = b.tool("task", {"agent_type": name, "prompt": "..."}, output="rapport")
            b.emit("subagent.started", {"toolCallId": cid, "agentName": name, "agentDisplayName": name})
            b.tool("bash", {"command": "npm run test:unit"}, output="ok", exit_code=0, agent_id="sub-1")
            b.tick(30, 400)
            if name == "aq-tests" and rng.random() < 0.38:
                b.emit(
                    "subagent.failed",
                    {
                        "toolCallId": cid,
                        "agentName": name,
                        "durationMs": rng.randint(60000, 300000),
                        "totalTokens": rng.randint(80000, 200000),
                        "totalToolCalls": 40,
                        "error": "Exceeded max tool calls (40) without producing the test report",
                    },
                )
            else:
                b.emit(
                    "subagent.completed",
                    {
                        "toolCallId": cid,
                        "agentName": name,
                        "durationMs": rng.randint(20000, 120000),
                        "totalTokens": rng.randint(20000, 90000),
                        "totalToolCalls": 12,
                    },
                )
        # ---------------- permissions
        if rng.random() < 0.35:
            b.tool(
                "bash",
                {"command": "git status"},
                perm="approved",
                perm_ids=["git status"],
                output="On branch main",
                exit_code=0,
            )
        if rng.random() < 0.05:
            b.tool(
                "bash",
                {"command": "git push --force origin main"},
                perm="denied-interactively-by-user",
                perm_ids=["git push"],
            )
            b.user("Non ! Ne pousse jamais directement sur main, ouvre une PR")
        if rng.random() < 0.04:
            b.emit("abort", {"reason": "user_initiated"})
        b.emit("assistant.turn_end", {"turnId": str(b.turn)})
    _ = current_skill
    if rng.random() < 0.7:
        b.emit("session.task_complete", {"success": True, "summary": "ok"})
    b.tick(5, 60)
    b.emit(
        "session.shutdown",
        {
            "shutdownType": "routine",
            "totalPremiumRequests": float(turns),
            "totalApiDurationMs": 60000,
            "sessionStartTime": int(start.timestamp() * 1000),
            "codeChanges": {
                "linesAdded": rng.randint(0, 220),
                "linesRemoved": rng.randint(0, 90),
                "filesModified": [f"f{i}" for i in range(rng.randint(0, 6))],
            },
            "modelMetrics": {},
            "currentModel": model,
        },
    )
    return b.events


def generate(root: Path, sessions: int = 150, days: int = 30, seed: int = 7) -> dict[str, Path]:
    """Écrit un ``.copilot`` synthétique par personne sous ``root``. Retourne {identité: home}."""
    rng = random.Random(seed)
    homes: dict[str, Path] = {}
    for user in USERS:
        home = root / user.split("@")[0] / ".copilot"
        (home / "session-state").mkdir(parents=True, exist_ok=True)
        homes[user] = home
    weights = [1.6, 1.3, 1.0, 1.0, 0.8, 0.5]
    for _ in range(sessions):
        user = rng.choices(USERS, weights=weights)[0]
        day = rng.randint(0, days - 1)
        start = (DEMO_NOW - timedelta(days=day)).replace(hour=rng.randint(13, 21), minute=rng.randint(0, 59))
        if start.weekday() >= 5:
            start -= timedelta(days=2)
        repo = rng.choices(list(REPOS), weights=list(REPOS.values()))[0]
        events = _session(rng, user, start, repo)
        sid = events[0]["data"]["sessionId"]
        sdir = homes[user] / "session-state" / sid
        sdir.mkdir()
        with (sdir / "events.jsonl").open("w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        (sdir / "workspace.yaml").write_text(f"id: {sid}\ncwd: /src/{repo}\nsummary: démo\n", encoding="utf-8")
    return homes
