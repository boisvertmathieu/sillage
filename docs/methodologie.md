# Méthodologie

Tout est **déterministe et explicable** : mêmes journaux, même résultat ; chaque opportunité cite ses preuves.
Aucun LLM n'intervient dans la mesure. Un agent (coach) n'intervient qu'en aval, pour rédiger les correctifs.

## 1. Source

`~/.copilot/session-state/<id>/events.jsonl` (et l'ancien format `session-state/<id>.jsonl`). Un événement
par ligne : `{id, timestamp, parentId, type, data, agentId?}`. Les événements `ephemeral` (deltas de
streaming) ne sont pas persistés par Copilot. Les événements portant un `agentId` ou un `parentToolCallId`
proviennent d'un sous-agent : ils comptent dans les appels d'outils (marqués `subagent`) mais pas dans les
tours de conversation de l'utilisateur.

Événements exploités : `session.start/resume/context_changed`, `user.message`, `assistant.turn_start`,
`assistant.message`, `assistant.usage`, `tool.execution_start/complete`, `skill.invoked`,
`session.skills_loaded`, `session.custom_agents_updated`, `subagent.*`, `session.mcp_servers_loaded`,
`session.mcp_server_status_changed`, `permission.requested/completed`, `hook.start/end`, `abort`,
`session.error`, `model.call_failure`, `session.compaction_complete`, `session.truncation`,
`session.usage_info`, `session.task_complete`, `exit_plan_mode.completed`, `session.shutdown`.

## 2. Normalisation

| Mesure | Règle |
|---|---|
| Échec d'outil | `success: false`, **ou** outil shell dont la sortie contient `exited/completed with exit code N` avec N ≠ 0. |
| Cible d'un appel | shell : commande normalisée (`cd app && npm run test:unit -- -w` → `npm run test:unit`, options et valeurs d'options ignorées) ; fichier : extension ; MCP : nom de l'outil. |
| Signature d'erreur | ligne la plus représentative (mots-clés d'erreur), caviardée, puis chemins/nombres/UUID/chaînes → jetons (`<chemin>`, `<n>`...). Les longues chaînes d'exceptions gardent le début **et** la fin (cause racine). |
| Boucle d'échec | même outil + même cible + même signature en échec **3 fois d'affilée**. |
| Contournement observé | après un échec shell, premier succès de la **même famille** de commande (même exécutable) dans les 5 appels suivants : commande différente (`npm test` → `npm run test:unit`) ou mêmes commande et options différentes (`mvn test` → `mvn -s … test`, niveau `redacted` seulement). |
| Correction | message utilisateur après une réponse de l'agent correspondant au lexique FR/EN (« non, », « c'est faux », « ça ne marche toujours pas », « je t'ai dit », « revert »...), extensible dans `sillage.toml`. |
| Reformulation | message très similaire (ratio ≥ 0,85) au précédent. |
| Attribution à un skill | une correction n'est attribuée au skill invoqué que dans les **2 messages utilisateur** qui suivent ; jamais si elle suit immédiatement un refus de permission (elle vise alors l'action refusée). |
| Session abandonnée | fin sur une erreur ou une interruption sans tâche complétée, ou `shutdownType: error`. |
| Temps actif | somme des intervalles entre événements, chaque intervalle plafonné à 5 min. |

## 3. Score de friction (0-100)

| Composante | Poids | Saturation |
|---|---|---|
| Corrections | 15 | 2 |
| Refus de permission par l'humain | 15 | 2 |
| Interruptions (abort) | 15 | 1 |
| Taux d'échec d'outils | 20 | 40 % |
| Boucles d'échec | 10 | 1 |
| Erreurs de session / modèle | 10 | 2 |
| Compactions / troncatures | 5 | 2 |
| Reformulations | 10 | 2 |

Niveaux : fluide < 15 ≤ accrochages < 35 ≤ difficile < 60 ≤ pénible. Poids réglables (`[friction]`).

**Temps perdu estimé** (indicatif) : corrections × 2 min, refus × 1, interruptions × 3, échecs × 0,5,
boucles × 5, reformulations × 1,5 (réglable). À présenter comme un ordre de grandeur, jamais comme une
mesure de productivité individuelle.

## 4. Règles d'opportunités

| # | Règle | Seuil par défaut |
|---|---|---|
| R1 | Erreur d'outil récurrente (hors échecs de tests attendus en TDD) | ≥ 3 sessions |
| R2 | Skill souvent corrigé (corrections / session) **ou** friction nettement supérieure | ≥ 0,5 / session ou +10 pts |
| R3 | Skill dormant | chargé ≥ 10 sessions, activation ≤ 5 % |
| R4 | Serveur MCP : échecs de démarrage / appels en échec | ≥ 3 sessions / ≥ 20 % sur ≥ 10 appels |
| R5 | Sous-agent instable / définition d'agent invalide | ≥ 20 % d'échecs sur ≥ 5 exécutions |
| R6 | Permissions : approuvées à répétition (allowlist) / refusées à répétition (garde-fou) | ≥ 5 approbations / ≥ 3 refus |
| R7 | Hook en échec | ≥ 3 sessions |
| R8 | Dépôt à friction élevée ou boucles d'échec | +10 pts ou ≥ 3 boucles |
| R9 | Contexte : définitions d'outils ≥ 15 % de la fenêtre ; commande shell ≥ 8 000 car. en moyenne | — |

La priorité combine sessions touchées, personnes touchées, occurrences et gravité (un garde-fou cassé pèse
plus qu'un bruit de permissions). Les leçons reprennent R1 (avec le contournement observé), R6 (refus) et R2.

## 5. Limites connues

* Le lexique de corrections est heuristique : il produit quelques faux positifs/négatifs. Ajoutez vos
  formulations d'équipe dans `[corrections] extra_patterns`.
* Le format des journaux appartient à GitHub et évolue vite (plusieurs versions par semaine) : lecture
  tolérante + `sillage doctor` pour détecter les types d'événements inconnus.
* Les opportunités sont des **hypothèses étayées**, pas des verdicts : l'humain (ou l'agent coach, sous revue)
  valide la cause avant de corriger.
