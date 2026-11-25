# AGENTS.md — Sillage

Instructions pour les agents (Copilot CLI, etc.) qui contribuent à ce dépôt.

## Commandes

- Installer : `pip install -e '.[dev]'`
- Tests : `pytest -q` (doivent tous passer ; aucun accès réseau, aucun accès au vrai `~/.copilot`)
- Lint/format : `ruff check . && ruff format --check .`
- Démo : `python -m sillage demo --output-dir /tmp/sillage-demo`

## Règles non négociables

- **Aucune dépendance d'exécution** : bibliothèque standard Python 3.11+ uniquement.
- **Confidentialité** : tout nouveau champ de `SessionRecord` contenant du texte libre doit être caviardé
  (`redact`) et vidé par `apply_privacy` au niveau `metrics`, avec un test qui le prouve.
- **Aucune vue par personne** : n'ajoutez jamais de tableau, tri ou export par utilisateur.
- **Lecture tolérante** : un champ absent ou un type d'événement inconnu ne doit jamais lever d'exception.
- Toute nouvelle règle d'opportunité : seuil dans `InsightThresholds`, preuves chiffrées, champ `where`,
  test sur le jeu de démo (détection + absence de faux positif).
- Le format d'entrée de référence est le `session-events.schema.json` du paquet npm `@github/copilot`.
