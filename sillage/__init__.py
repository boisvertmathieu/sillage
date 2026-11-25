"""Sillage — observatoire des sessions GitHub Copilot CLI et moteur d'auto-amélioration
des agents, skills et instructions.

Sillage lit les journaux d'événements locaux de Copilot CLI
(``~/.copilot/session-state/<id>/events.jsonl``), les normalise, les caviarde, puis
en tire des indicateurs, des irritants récurrents, des opportunités d'amélioration
et des « leçons » réinjectables dans les futures sessions (AGENTS.md, serveur MCP).
"""

__version__ = "0.1.0"

#: Version du format des enregistrements normalisés (bundles d'export).
RECORD_SCHEMA_VERSION = 1
