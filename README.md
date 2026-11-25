# Sillage

**Observatoire des sessions GitHub Copilot CLI et moteur d'auto-amélioration des agents, skills et instructions.**

> Le *sillage* est la trace qu'un navire laisse derrière lui. Chaque session Copilot laisse la sienne :
> Sillage la lit pour que les suivantes aillent plus vite.

Nos sessions Copilot produisent des milliers d'événements (appels d'outils, erreurs, corrections,
refus de permission, skills invoqués, sous-agents, tokens...). Aujourd'hui, ils dorment dans
`~/.copilot/session-state/` et **personne ne les exploite** : on ne sait pas quels skills aident ou nuisent,
quelles erreurs reviennent chaque semaine chez tout le monde, ni ce que coûte notre configuration MCP.

Sillage transforme ces journaux en trois choses :

1. **Visibilité** : un tableau de bord d'équipe (adoption, friction, échecs, coût en contexte, santé des
   skills/agents/serveurs MCP), **sans jamais classer les personnes**.
2. **Opportunités d'amélioration** priorisées et prouvées : « ce hook de garde-fou plante dans 39 sessions »,
   « ce skill est corrigé 0,7 fois par session », « `npm test` échoue dans ce dépôt, l'équipe contourne
   avec `npm run test:unit` »... avec **où** corriger (SKILL.md, .agent.md, AGENTS.md, hook, mcp-config).
3. **Leçons apprises réinjectées** dans les futures sessions, par `AGENTS.md` (bloc idempotent) ou en direct
   par un **serveur MCP** (`sillage_lessons`) : la boucle d'auto-amélioration se ferme.

```mermaid
flowchart LR
  A["Sessions Copilot CLI<br/>~/.copilot/session-state/*/events.jsonl"] -->|collect<br/>caviardage| B["Magasin local<br/>enregistrements normalisés"]
  B -->|export niveau metrics| C["Bundles anonymisés<br/>(partage d'équipe)"]
  B --> D["Analyse<br/>friction · erreurs · skills · MCP · contexte"]
  C --> D
  D --> E["Rapport HTML / MD<br/>gestion & équipes"]
  D --> F["Opportunités<br/>(où corriger, preuves)"]
  D --> G["Leçons apprises"]
  F -->|agent sillage-coach| H["PR : SKILL.md, agents,<br/>AGENTS.md, garde-fous"]
  G -->|AGENTS.md · MCP sillage_lessons| A
  H -->|revue + garde-fous| A
```

## Démarrage rapide (WSL)

```bash
pipx install git+https://<votre-forge>/sillage.git   # ou : pip install -e .   (Python ≥ 3.11, aucune dépendance)
```

```bash
sillage demo                 # démonstration complète sur 150 sessions synthétiques -> ./sillage-demo/
```

```bash
sillage doctor               # vérifie où sont vos sessions, versions de Copilot, dérive de format
```

```bash
sillage report --since 30d --format html -o rapport.html
```

```bash
sillage opportunities --limit 10
```

```bash
sillage lessons --repo mon-org/mon-depot --write AGENTS.md
```

Sous WSL, Sillage lit `~/.copilot` **et** les sessions lancées côté Windows (`/mnt/c/Users/*/.copilot`).

## Commandes

| Commande | Rôle |
|---|---|
| `sillage collect` | Lit les sessions (incrémental) et met à jour le magasin local (`~/.local/share/sillage`). |
| `sillage report` | Rapport `md` (défaut), `html` (tableau de bord autonome) ou `json`. |
| `sillage opportunities` | Opportunités priorisées : preuves, action proposée, où corriger. `--category`, `--format json`. |
| `sillage lessons` | Leçons apprises ; `--repo`, `--skill`, `--write AGENTS.md`, `--format agents-md`. |
| `sillage export` | Bundle `.jsonl.gz` anonymisé à partager (niveau `metrics` par défaut). |
| `sillage report --bundle *.jsonl.gz` | Vue d'équipe à partir des bundles de chacun. |
| `sillage mcp` | Serveur MCP stdio en lecture seule (mémoire collective pour les agents). |
| `sillage copilot-install [--mcp]` | Installe l'agent `sillage-coach` et le skill `sillage-amelioration` dans `~/.copilot`. |
| `sillage doctor` | Diagnostic : sessions trouvées, versions de Copilot CLI, types d'événements inconnus. |
| `sillage init-config` | Écrit un `sillage.toml` commenté (seuils, confidentialité, motifs de correction). |

Options communes : `--since 30d|12w|2026-10-01`, `--repo owner/nom`, `--copilot-home <dir>`, `-o fichier`.

## Ce que Sillage détecte

| Signal | Exemple d'opportunité | Où corriger |
|---|---|---|
| Erreur d'outil récurrente (signature normalisée) + **contournement observé** | `mvn test` échoue (PKIX) dans 31 sessions ; solution vue 12 fois : `mvn -s ~/.m2/settings-dp.xml test` | AGENTS.md du dépôt |
| Skill souvent corrigé après invocation | « servicenow-incident » : 0,7 correction / session | SKILL.md |
| Skill dormant (chargé partout, jamais déclenché) | « confluence-recherche » : 0 % d'activation | description du skill |
| Serveur MCP qui ne démarre pas / appels en échec | « dynatrace » : 53 sessions sans le serveur | mcp-config.json |
| Sous-agent instable, définition d'agent invalide | « aq-tests » échoue dans 53 % des cas | .agent.md |
| Refus humains répétés → garde-fou manquant | `git push` refusé 36 fois | hook / règle deny + instructions |
| Approbations répétées → bruit de permissions | `git status` approuvé 265 fois | profil d'autorisations |
| Hook de garde-fou qui plante | `preToolUse : jq: not found` dans 39 sessions | .github/hooks |
| Coût en contexte | définitions d'outils = 16 % de la fenêtre ; `terraform plan` = 28 000 car. / appel | profils MCP, compression |
| Dépôt à forte friction, boucles d'échec | `dp/portail-citoyen` : 14 boucles | AGENTS.md |

Détails des calculs : [docs/methodologie.md](docs/methodologie.md).

## Intégration Copilot CLI

```bash
sillage copilot-install --mcp
```

* **Serveur MCP `sillage`** (lecture seule) : `sillage_lessons`, `sillage_opportunities`, `sillage_overview`,
  `sillage_skill_health`. Ajoutez à vos AGENTS.md l'extrait affiché par la commande pour que l'agent
  consulte les leçons au début de chaque tâche.
* **Agent `sillage-coach`** : prend une opportunité, lit le fichier ciblé, propose le plus petit correctif
  et ouvre une PR avec les preuves. Il ne fusionne jamais seul : vos garde-fous et revues s'appliquent.
* **Skill `sillage-amelioration`** : permet de demander en langage naturel « comment se passent nos sessions ? ».

## Confidentialité et éthique (résumé)

* **Rien ne quitte le poste** sans `sillage export` explicite.
* Le magasin local ne contient **jamais** les journaux bruts : seulement compteurs, noms d'outils/skills,
  signatures d'erreurs normalisées et (niveau `redacted`) de courts extraits **caviardés** (jetons, courriels,
  IP, NAS, cartes, chemins personnels...).
* Les bundles partagés sont au niveau `metrics` par défaut : **aucun texte libre**, identifiants re-pseudonymisés.
* **Aucun classement individuel**, k-anonymat sur les extraits dans les rapports d'équipe.

Charte complète : [docs/gouvernance.md](docs/gouvernance.md).

## Compatibilité

Format d'entrée : journaux `events.jsonl` de **GitHub Copilot CLI 1.0.x**, conformes au schéma
`session-events.schema.json` livré dans le paquet npm `@github/copilot`. Le lecteur est tolérant (champs
manquants, lignes tronquées, types inconnus) et `sillage doctor` signale toute dérive de format après une
mise à jour de Copilot. Python 3.11+, bibliothèque standard uniquement (adapté aux postes corporatifs).

## Développement

```bash
pip install -e '.[dev]'
```

```bash
ruff check . && pytest -q
```

Feuille de route : [docs/feuille-de-route.md](docs/feuille-de-route.md).
