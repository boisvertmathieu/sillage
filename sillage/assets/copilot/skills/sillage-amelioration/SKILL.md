---
name: sillage-amelioration
description: >-
  Analyse des sessions GitHub Copilot CLI avec Sillage : indicateurs d'usage, friction, irritants
  récurrents, santé des skills/agents/serveurs MCP, leçons apprises à réinjecter dans AGENTS.md.
  Utiliser quand l'utilisateur demande comment se passent les sessions Copilot, quels skills ou
  agents posent problème, pourquoi une commande échoue souvent, ou veut améliorer ses instructions.
---

# Sillage — analyser et améliorer à partir des sessions Copilot

Sillage est une ligne de commande (aucun serveur MCP requis). Exécute-la avec l'outil shell.
Ajoute `--bundle-dir <dossier>` pour travailler sur les données de l'équipe (bundles partagés) plutôt
que sur les sessions locales de la personne.

## Commandes

| Besoin | Commande |
|---|---|
| Mettre à jour les données locales (incrémental) | `sillage collect` |
| Vérifier l'installation et le format | `sillage doctor` |
| Rapport (Markdown / HTML) | `sillage report --since 30d` · `sillage report --format html -o rapport.html` |
| Opportunités priorisées | `sillage opportunities --limit 10` (`--format json` pour un traitement) |
| Leçons d'un dépôt | `sillage lessons --repo <owner/nom>` |
| Injecter les leçons dans AGENTS.md | `sillage lessons --repo <owner/nom> --write AGENTS.md` |
| Vue d'équipe | ajouter `--bundle-dir <dossier des bundles>` à report / opportunities / lessons |
| Voir ce qui sortirait, puis partager | `sillage export --preview` · `sillage export -o <dossier>/<nom-aléatoire>.jsonl.gz` |
| Effet d'un correctif (avant/après) | `sillage compare --pivot <date du correctif>` |

## Procédure

1. Lance `sillage report --since 30d` (ou avec `--bundle-dir` pour l'équipe).
2. Résume en 5 lignes : volumes, part de sessions fluides, 3 premières opportunités.
3. Pour chaque opportunité retenue, indique **où** corriger et **quoi** changer, en citant les preuves
   chiffrées (ne jamais inventer de chiffres : tout vient de la sortie de Sillage).
4. Propose d'appliquer les leçons du dépôt courant avec `--write AGENTS.md` (le bloc est idempotent,
   délimité par des marqueurs).

## Règles

- Aucun classement ou jugement individuel : Sillage mesure les outils, pas les personnes.
- Les exports partagés utilisent le niveau `strict` par défaut (aucun mot issu d'un message d'erreur) ;
  `metrics` ou `redacted` seulement sur décision explicite de l'équipe.
