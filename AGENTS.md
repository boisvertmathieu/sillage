# AGENTS.md — Sillage

Instructions pour les agents (Copilot CLI, etc.) qui contribuent à ce dépôt.

## Commandes

- Installer : `pip install -e '.[dev]'`
- Tests : `pytest -q` (doivent tous passer ; aucun accès réseau, aucun accès au vrai `~/.copilot`)
- Lint/format : `ruff check . && ruff format --check .`
- Démo : `python -m sillage demo --output-dir /tmp/sillage-demo`
- Diagnostic d'onboarding : `python -m sillage onboard` (code de sortie 2 si un point est bloquant)

## Règles non négociables

- **Aucune dépendance d'exécution** : bibliothèque standard Python 3.11+ uniquement.
- **Confidentialité** : tout nouveau champ de `SessionRecord` contenant du texte doit être caviardé
  (`redact`, avec les motifs de l'organisation), retraité par `apply_privacy` (vidé au niveau `metrics`,
  réduit à `type#empreinte` ou vidé au niveau `strict`), avec un test qui le prouve. Le niveau d'export
  par défaut reste `strict`.
- **Aucune vue par personne** : n'ajoutez jamais de tableau, tri ou export par utilisateur.
- **Lecture tolérante** : un champ absent ou un type d'événement inconnu ne doit jamais lever d'exception.
- Toute nouvelle règle d'opportunité : seuil dans `InsightThresholds`, preuves chiffrées, champ `where`,
  test sur le jeu de démo (détection + absence de faux positif).
- Le format d'entrée de référence est `dist/generated/session-events.d.ts` du paquet npm `@github/copilot-sdk`
  (le paquet `@github/copilot` ne contient plus le schéma depuis la 1.0.116). Un événement `checkpoint: true`
  ne doit jamais être recompté.
- Pseudonymes de personne et de session : toujours le sel local du poste, jamais `$SILLAGE_SALT`.
- Tout nouveau point d'onboarding (`sillage/onboard.py`) : statut, constats `shareable` sans aucun texte de
  session, test sur un historique synthétique. Mettre à jour `docs/guide-agent-onboarding.md`.
