# Guide de l'agent d'onboarding

L'agent `sillage-onboarding` prépare un poste pour le pilote. Il s'appuie sur **tout l'historique** des
sessions Copilot CLI du poste, et pas seulement sur la dernière période. Il lève seul les points encore
flous, applique les réglages sûrs après accord et ne pose que les questions impossibles à déduire des
journaux.

* Agent : `sillage/assets/copilot/agents/sillage-onboarding.agent.md`, installé par `sillage copilot-install`.
* Moteur : `sillage onboard`, une commande déterministe sans LLM, qui fonctionne aussi sans l'agent.
* Durée : 15 à 25 minutes par poste.

## Pourquoi cet agent

Certaines questions ne se tranchent qu'avec de vraies sessions : le format des journaux de votre version
de Copilot, la part d'usage dans l'IDE, les noms internes qui apparaîtraient dans un export, les
formulations de correction de l'équipe. Le jeu de démonstration ne peut pas y répondre. L'onboarding
transforme ces inconnues en constats chiffrés, en décisions ou en points à remonter.

## Deux usages

| Qui | Quand | But |
|---|---|---|
| **Responsable du pilote** | En premier, sur son propre poste, avant d'inviter qui que ce soit | Valider Sillage sur des journaux réels et fixer la configuration d'équipe (`sillage.toml`) |
| **Chaque participant** | À l'installation | Poste prêt, motifs de caviardage relus, collecte planifiée |

## Lancer

```bash
sillage copilot-install
copilot --agent sillage-onboarding --allow-tool='shell(sillage:*)'
```

Puis dites simplement « Fais mon onboarding Sillage ». Sans l'agent, le même diagnostic est disponible
avec `sillage onboard` (fiche Markdown), `--format json`, `--partageable` et `--depots depots.txt`.
La commande renvoie le code de sortie 2 si au moins un point est bloquant.

## Ce que l'agent fait seul, demande ou ne fait jamais

| Fait seul (lecture) | Fait après votre accord (écriture) | Ne fait jamais |
|---|---|---|
| `sillage onboard`, `sillage doctor`, `sillage export --preview` | Écrire `sillage.toml` (motifs, lexique, seuils) | Lire directement `~/.copilot/session-state` |
| Résumer chaque constat en une phrase chiffrée | Écrire `depots.txt` et la fiche `onboarding-AAAA-MM-JJ.md` | Exporter ou déposer un bundle |
| Proposer des motifs de caviardage et des regex de correction | `sillage copilot-install` | Modifier un dépôt applicatif ou un AGENTS.md |
| Relancer le diagnostic pour vérifier une correction | Donner la commande de tâche planifiée (vous la lancez) | Transmettre la fiche locale hors du poste |

## Déroulé

1. **Annonce** : ce qui va se passer, tout reste local, chaque écriture est validée.
2. **Diagnostic** : `sillage onboard --format json -o ~/.local/share/sillage/onboarding.json`.
3. **Traitement des points**, dans l'ordre `sessions` → `format` → `installation` → `confidentialite` →
   `corrections` → `depots` → `ide` → `seuils` → `outillage`, selon le tableau de décision ci-dessous.
4. **Confidentialité** : motifs validés un par un, puis relecture complète de `sillage export --preview`.
5. **Corrections** : tournures confirmées ajoutées au lexique, puis nouvelle vérification.
6. **Dépôts** : brouillon `depots.txt`, nettoyé des dépôts hors pilote.
7. **Installation** : agents installés, dossier partagé vérifié, tâche planifiée créée par vous.
8. **Vérification** : `sillage onboard` ne doit plus signaler de point bloquant.
9. **Fiche** : `onboarding-AAAA-MM-JJ.md` en local, plus `onboarding-partageable.md` pour le responsable.

## Les neuf points et leur décision

Chaque point reçoit un statut : ⛔ **bloquant** (le pilote ne peut pas démarrer sur ce poste), ⚠ **attention**
(fonctionne mais à corriger), ❓ **à confirmer** (une décision humaine est nécessaire), ✅ **ok**.

### 1. `sessions` : où sont les sessions et combien ?

* **Mesure** : dossiers `session-state` trouvés (`$COPILOT_HOME`, `~/.copilot` et, sous WSL,
  `/mnt/c/Users/*/.copilot`), nombre de sessions, première et dernière date, rythme hebdomadaire, sessions
  lancées côté Windows.
* **Décision** : aucune session donne ⛔ ; moins de 10 donne ⚠ (constats indicatifs).
* **Action automatique** : si ⛔, l'agent lance `sillage doctor`, cherche avec vous le bon dossier et relance
  avec `--copilot-home`.
* **Question si non résolu** : « Où lancez-vous Copilot : WSL, Windows natif, autre machine ? »

### 2. `format` : Sillage lit-il correctement vos journaux ?

* **Mesure** : versions de Copilot CLI, types d'événements inconnus, présence des champs indispensables
  (`toolCallId`, `success`, `permissionRequest.kind`, `result.kind`…, seuil de 90 %), événements rejoués
  (`checkpoint: true`, ignorés au comptage), source des codes de sortie shell (champ structuré
  `shellExecution.exitCode` ou marqueur texte), lignes illisibles.
* **Décision** : un champ indispensable sous 90 % donne ⛔ (le format a changé) ; des types inconnus donnent ⚠.
* **Action automatique** : aucune correction possible sur le poste. L'agent prépare
  `sillage onboard --partageable` pour le responsable de Sillage, qui ajoute les types à
  `KNOWN_EVENT_TYPES` ou adapte la lecture. Le format de référence est celui des types générés de
  `@github/copilot-sdk` (`dist/generated/session-events.d.ts`).
* **Question** : aucune. C'est un point technique, remonté tel quel.

### 3. `installation` : le poste est-il prêt pour la collecte hebdomadaire ?

* **Mesure** : Python, système (WSL ou non), `copilot` sur le PATH, agents Sillage installés,
  `$SILLAGE_PARTAGE` défini et accessible en écriture, tâche planifiée « Sillage collecte » présente.
* **Décision** : un élément manquant donne ⚠.
* **Action automatique** : `sillage copilot-install` après accord. L'agent fournit ensuite la commande de
  tâche planifiée adaptée au poste : `schtasks.exe` vers WSL, ou `examples/collecte-equipe.ps1` sous
  Windows natif. Vous la lancez vous-même.
* **Question** : « Quel est le chemin du dossier partagé du pilote, et qui en est le curateur ? »

### 4. `confidentialite` : que sortirait-il d'un export ?

* **Mesure** : toutes les valeurs textuelles qu'un export contiendrait au niveau configuré (défaut
  `strict`), plus celles du niveau `metrics` pour protéger aussi le magasin local. Des heuristiques
  repèrent :
  * les environnements de production (`dpprod`, `prod-paiements`, `srv-prod-01`) ;
  * les identifiants de dossier ou de billet (`DOS-123456`) ;
  * les « NOM Prénom » ;
  * les serveurs internes ;
  * les longs numéros.

  Chaque motif proposé indique s'il **sort au niveau actuel** ou s'il reste local.
* **Décision** : un motif qui sort au niveau actuel donne ❓ ; un niveau d'export autre que `strict` donne ⚠ ;
  `hash_repositories = true` sans sel d'équipe donne ⛔.
* **Action automatique** : après accord sur chaque motif, écriture dans `[confidentialite] motifs` de
  `sillage.toml`, puis `sillage export --preview`, relu en entier jusqu'à ce qu'aucune valeur sensible
  n'apparaisse.
* **Question** : « Lesquels de ces motifs retenir pour le `sillage.toml` d'équipe ? » Le responsable du
  pilote consolide les motifs de tous les participants dans un seul fichier.

### 5. `corrections` : le lexique reconnaît-il vos formulations ?

* **Mesure** : taux de corrections reconnues, exemples reconnus (pour vérifier la précision) et messages
  courts **non reconnus** qui ressemblent à une correction (« plutôt », « au lieu », « encore », « still »…).
  Les exemples sont caviardés et affichés seulement en local.
* **Décision** : des candidats non reconnus donnent ❓.
* **Action automatique** : pour chaque tournure confirmée, une regex (minuscules, sans accents) ajoutée à
  `[corrections] extra_patterns`, puis un nouveau `sillage onboard` pour vérifier qu'elle est reconnue.
* **Question** : « Ces messages sont-ils des corrections de l'agent ? »

### 6. `depots` : quels dépôts, quels clones, quels AGENTS.md ?

* **Mesure** : dépôts vus dans les sessions, part des sessions hors dépôt, clone local (`gitRoot`) et, pour
  chacun, présence d'AGENTS.md, du bloc de leçons et de l'extrait d'instructions Sillage.
* **Décision** : plus de 50 % des sessions hors dépôt donne ⚠ (les leçons par dépôt toucheront peu ces
  sessions) ; un AGENTS.md sans extrait Sillage donne ❓.
* **Action automatique** : `sillage onboard --depots depots.txt` après accord. L'agent ne modifie aucun
  AGENTS.md : l'extrait et les leçons arrivent par PR, avec `examples/publier-lecons.sh`.
* **Question** : « Pour quels dépôts publier les leçons pendant le pilote ? »

### 7. `ide` : quelle part de l'usage Sillage ne voit-il pas ?

* **Mesure** : nombre de conversations Copilot Chat de VS Code sur le poste (Linux, macOS, Windows, et
  Windows vu de WSL), comparé aux sessions CLI sur 90 jours. **Seuls les fichiers sont comptés, aucun n'est
  lu.**
* **Décision** : un usage IDE au moins égal à la moitié de l'usage CLI donne ❓.
* **Action automatique** : aucune. Le constat sert à cadrer le discours auprès de la gestion : les
  indicateurs du pilote couvrent la CLI seulement.
* **Question** : « L'usage dans l'IDE doit-il entrer dans le périmètre plus tard ? »

### 8. `seuils` : les seuils sont-ils adaptés au volume ?

* **Mesure** : nombre d'opportunités avec les seuils actuels, sur tout l'historique et sur 28 jours.
* **Décision** : aucune opportunité malgré 20 sessions ou plus sur 28 jours, ou plus de 30 opportunités pour
  une seule personne, donne ❓.
* **Action automatique** : proposer `min_sessions` dans `[insights]` (cible : 5 à 15 opportunités par
  rapport mensuel d'équipe).
* **Question** : « Combien de personnes participeront au pilote ? »

### 9. `outillage` : quelles cibles d'amélioration existent ?

* **Mesure** : skills disponibles et utilisés, agents personnalisés, serveurs MCP et leurs statuts
  (`disabled` ou `stopped` signifient une désactivation, souvent par la politique de l'organisation), hooks
  et leurs échecs.
* **Décision** : informatif ; confirme notamment le mode sans MCP.
* **Action automatique** : aucune. Ces cibles alimenteront l'agent coach pendant le pilote.

## Questions que les journaux ne peuvent pas trancher

L'agent les pose toujours, une à une, avec sa recommandation :

1. Nombre de participants au pilote (calibrage des seuils).
2. Chemin du dossier partagé et nom du curateur. Recommandation : écriture pour les participants, lecture
   pour le curateur seulement, rétention de 90 jours.
3. Motifs de caviardage retenus pour l'équipe.
4. Dépôts concernés par la publication des leçons.
5. Validation du dispositif par le responsable de la protection des renseignements personnels (Loi 25).
6. Selon le diagnostic : tournures de correction, périmètre IDE, `hash_repositories`.

## Livrables

| Fichier | Contenu | Partage |
|---|---|---|
| `onboarding-AAAA-MM-JJ.md` | Statut final de chaque point, décisions, questions restantes | **Local** |
| `sillage onboard` (fiche) | Constats détaillés, chemins, noms de dépôts, extraits caviardés | **Local** |
| `onboarding-partageable.md` | Statuts et constats sans aucun texte de session (versions, types inconnus, compteurs) | Vers le responsable du pilote |
| `sillage.toml` | Motifs, lexique, seuils validés | Consolidé par le responsable, puis distribué |
| `depots.txt` | `owner/nom  /chemin/du/clone` | Pour le curateur |

## Critères de démarrage du pilote

Le responsable du pilote démarre quand :

- [ ] son propre onboarding ne signale **aucun point bloquant** ;
- [ ] le point `format` est ✅ ou ⚠ sur au moins deux postes réels (fiches partageables) ;
- [ ] le `sillage.toml` d'équipe contient les motifs retenus, et chaque participant a relu
      `sillage export --preview` ;
- [ ] le dossier partagé existe avec les droits recommandés ;
- [ ] le dispositif est validé par le responsable de la protection des renseignements personnels ;
- [ ] la tâche planifiée est créée chez chaque participant.

## Confidentialité de l'onboarding

* `sillage onboard` lit les journaux **sur le poste** et n'écrit rien d'autre que le magasin local de
  Sillage et les fichiers demandés.
* L'agent tourne dans Copilot : les extraits caviardés de la fiche, issus de **vos propres messages**,
  passent par le modèle Copilot, comme vos sessions d'origine. Aucun autre service n'est contacté.
* `--partageable` retire chemins, noms de dépôts, extraits et exemples de motifs. Il ne reste que des
  statuts et des constats techniques.

## Dépannage

| Symptôme | Cause probable | Que faire |
|---|---|---|
| Code de sortie 2, `sessions` ⛔ | Dossier `.copilot` ailleurs | `sillage doctor`, puis `--copilot-home <dossier>` |
| Tâche planifiée « non vérifiable » | Ni `schtasks.exe` ni Windows natif | Vérifier à la main dans le Planificateur de tâches |
| `format` ⛔ après une mise à jour de Copilot | Nouveau format de journal | Envoyer la fiche partageable ; mettre Sillage à jour |
| Beaucoup de motifs « NOM Prénom » | Mots en majuscules dans des sorties d'outils | Ne retenir que les vrais cas ; la liste est indicative |
| Usage IDE élevé | Copilot surtout utilisé dans VS Code | Le dire à la gestion ; périmètre CLI pour le pilote |

## Limites

* Les heuristiques (motifs, tournures de correction) proposent ; l'humain décide.
* Les conversations Copilot de l'IDE sont comptées, pas analysées.
* Les journaux ne disent pas si l'AGENTS.md racine a été chargé. L'effet des leçons se mesure avec
  `sillage compare --pivot <date de fusion>` 2 à 4 semaines plus tard.
