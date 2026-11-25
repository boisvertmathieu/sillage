"""Serveur MCP (stdio) minimal et sans dépendance — « mémoire collective » pour les agents.

Permet à Copilot CLI (ou tout client MCP) d'interroger, *pendant* une session, les
leçons apprises et la santé des skills/agents. Exemple d'usage dans les instructions :
« au début d'une tâche, appelle ``sillage_lessons`` avec le dépôt courant ».

Protocole : JSON-RPC 2.0, un message JSON par ligne (transport stdio MCP).
Implémentation volontairement autonome pour éviter les ruptures de version du SDK.
Tous les outils sont en **lecture seule**.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from sillage import __version__
from sillage.analyze import Analysis, analyze
from sillage.config import Config
from sillage.insights import lessons, opportunities
from sillage.model import SessionRecord
from sillage.report_md import render_lessons, render_opportunities

SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "sillage_lessons",
        "description": "Leçons apprises des sessions Copilot passées de l'équipe (erreurs récurrentes, commandes à "
        "éviter, pièges d'un skill). À appeler au début d'une tâche avec le dépôt courant "
        "(format owner/nom) et/ou le skill utilisé.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "description": "Dépôt, ex. « dp/portail-citoyen »"},
                "skill": {"type": "string", "description": "Nom du skill"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    },
    {
        "name": "sillage_opportunities",
        "description": "Opportunités d'amélioration priorisées (skills, agents, instructions, garde-fous, MCP, "
        "contexte) avec preuves et action proposée. Utile pour un agent « coach » qui prépare des "
        "correctifs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "skill",
                        "agent",
                        "instructions",
                        "guardrail",
                        "permissions",
                        "mcp",
                        "environment",
                        "context",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    },
    {
        "name": "sillage_overview",
        "description": "Indicateurs clés agrégés des sessions Copilot (volumes, friction, échecs, tokens, contexte).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sillage_skill_health",
        "description": "Santé d'un skill : utilisation, taux d'activation, corrections après invocation, friction, "
        "opportunités associées.",
        "inputSchema": {
            "type": "object",
            "properties": {"skill": {"type": "string"}},
            "required": ["skill"],
        },
    },
]


class SillageMCP:
    def __init__(self, loader: Callable[[], list[SessionRecord]], cfg: Config) -> None:
        self._loader = loader
        self.cfg = cfg
        self._analysis: Analysis | None = None

    @property
    def analysis(self) -> Analysis:
        if self._analysis is None:
            self._analysis = analyze(self._loader(), self.cfg.k_anonymity)
        return self._analysis

    # ------------------------------------------------------------- outils
    def call(self, name: str, args: dict[str, Any]) -> tuple[str, Any]:
        a = self.analysis
        th = self.cfg.thresholds
        limit = int(args.get("limit") or 10)
        if name == "sillage_lessons":
            items = lessons(a, th, repository=args.get("repository"), skill=args.get("skill"))[:limit]
            return render_lessons(items), [lsn.to_dict() for lsn in items]
        if name == "sillage_opportunities":
            items = opportunities(a, th)
            if args.get("category"):
                items = [o for o in items if o.category == args["category"]]
            items = items[:limit]
            return render_opportunities(items), [o.to_dict() for o in items]
        if name == "sillage_overview":
            data = {"overview": a.overview, "context": a.context}
            return json.dumps(data, ensure_ascii=False, indent=2), data
        if name == "sillage_skill_health":
            skill = args.get("skill")
            row = next((s for s in a.skills if s["skill"] == skill), None)
            if row is None:
                return f"Skill inconnu ou jamais vu : {skill}", None
            related = [o.to_dict() for o in opportunities(a, th) if o.target == skill]
            data = {"skill": row, "opportunities": related}
            return json.dumps(data, ensure_ascii=False, indent=2), data
        raise KeyError(name)

    # ---------------------------------------------------------- protocole
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if mid is None:  # notification (ex. notifications/initialized) : aucune réponse
            return None

        def ok(result: Any) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        def err(code: int, message: str) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            return ok(
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "sillage", "version": __version__},
                    "instructions": "Mémoire collective des sessions Copilot. Appelez sillage_lessons au début d'une "
                    "tâche avec le dépôt courant pour éviter les erreurs déjà rencontrées par l'équipe.",
                }
            )
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name", "")
            try:
                text, structured = self.call(name, params.get("arguments") or {})
            except KeyError:
                return err(-32602, f"Outil inconnu : {name}")
            except Exception as exc:  # erreur d'exécution : renvoyée à l'agent, pas au protocole
                return ok({"content": [{"type": "text", "text": f"Erreur Sillage : {exc}"}], "isError": True})
            result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
            if isinstance(structured, dict):
                result["structuredContent"] = structured
            elif isinstance(structured, list):
                result["structuredContent"] = {"items": structured}
            return ok(result)
        if method in ("resources/list", "prompts/list"):
            return ok({method.split("/")[0]: []})
        return err(-32601, f"Méthode non prise en charge : {method}")

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp: dict[str, Any] | None = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "JSON invalide"},
                }
            else:
                if isinstance(msg, list):  # lot JSON-RPC
                    resps = [r for r in (self.handle(m) for m in msg if isinstance(m, dict)) if r]
                    if resps:
                        stdout.write(json.dumps(resps, ensure_ascii=False) + "\n")
                        stdout.flush()
                    continue
                resp = self.handle(msg) if isinstance(msg, dict) else None
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()
