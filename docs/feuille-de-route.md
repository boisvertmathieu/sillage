# Feuille de route et plan d'adoption

## Indicateurs à présenter à la gestion (mensuel)

| Indicateur | Pourquoi |
|---|---|
| Sessions et personnes actives / semaine | Adoption réelle, au-delà des licences. |
| Part de sessions fluides | Qualité de l'expérience ; doit monter quand on corrige les irritants. |
| Heures perdues estimées (ordre de grandeur) | Langage de la gestion ; tendance plus importante que la valeur absolue. |
| Opportunités ouvertes → corrigées (avant/après) | Preuve que la boucle d'amélioration fonctionne. |
| Taux d'activation et de correction des skills | Retour sur investissement de chaque skill/agent de la DP. |
| Coût en contexte (définitions d'outils, sorties volumineuses), taux de cache | Base de mesure pour les optimisations de tokens. |

## Phases

### V0 — individuel (livré)
Collecte locale, rapport, opportunités, leçons, `AGENTS.md`, agent coach (CLI), démo ; serveur MCP optionnel.

### V1 — équipe pilote (2-4 semaines)
* 5 à 10 volontaires : `sillage export` hebdomadaire vers un partage restreint (`examples/collecte-equipe.sh`).
* Rapport d'équipe mensuel (`examples/rapport-equipe.sh`) et revue de 30 min : 3 opportunités choisies,
  un responsable chacune.
* Calibrer le lexique de corrections et les seuils sur les données réelles (`sillage.toml`).

### V2 — boucle d'auto-amélioration outillée
* Agent `sillage-coach` lancé sur les opportunités retenues → PR avec preuves.
* Chaque correction de skill accompagnée d'un cas d'évaluation (banc d'évaluation des skills) ; l'effet est
  vérifié par Sillage 2 à 4 semaines plus tard (avant/après par identifiant d'opportunité).
* Publication mensuelle des leçons par PR dans chaque dépôt (`examples/publier-lecons.sh`) : c'est le
  canal principal tant que la politique Copilot de l'organisation désactive les serveurs MCP tiers.

### V3 — en session (extension Copilot CLI)
Copilot CLI expose des extensions (`.github/extensions/<nom>/extension.mjs`) avec des hooks
`onSessionStart`, `onUserPromptSubmitted`, `onPreToolUse` (`modifiedArgs`, `permissionDecision`,
`additionalContext`) et `onPostToolUse` (`modifiedResult`). Une extension Sillage pourrait :
* injecter les leçons du dépôt au démarrage (`additionalContext`) sans dépendre d'AGENTS.md ;
* avertir *avant* une commande connue pour échouer et suggérer le contournement observé ;
* compresser les sorties volumineuses repérées par Sillage (`modifiedResult`) — première brique du
  « harnais d'efficience » (voir l'idée n° 2 du portefeuille).

### V4 — industrialisation
Entrepôt central (bundles → base analytique), tableaux de bord dans l'outil BI de l'organisation,
corrélation avec les métriques de livraison (délais de PR, défauts) au niveau équipe.
