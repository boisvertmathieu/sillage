# Charte de confidentialité et d'éthique

Sillage n'est utile que si les équipes lui font confiance. Ces règles sont **intégrées au code et testées**.

## Ce qu'il faut savoir d'abord

* Les données sensibles **existent déjà** sur chaque poste : Copilot CLI conserve dans
  `~/.copilot/session-state/*/events.jsonl` les prompts, les réponses, les commandes et leurs sorties
  complètes, y compris ce qu'une commande a affiché depuis un environnement de production. Sillage ne
  crée pas ce risque. Il lit ces journaux et n'en retient qu'une version réduite.
* Un bundle est **pseudonymisé et minimisé**, pas anonyme au sens strict. Celui qui le reçoit ne voit ni
  nom, ni prompt, ni code, mais un bundle reste celui d'une personne : le dossier partagé peut afficher qui
  l'a déposé (métadonnée « Modifié par » de SharePoint ou du partage réseau). D'où les contrôles d'accès
  décrits plus bas.

## Le parcours d'une donnée

| Étape | Où | Ce qui est conservé |
|---|---|---|
| Journaux Copilot | poste | tout (géré par Copilot, pas par Sillage) |
| Magasin local (`sillage collect`) | poste, `~/.local/share/sillage` | compteurs, noms d'outils, signatures d'erreurs caviardées ; au niveau local `redacted`, courts extraits caviardés |
| Bundle (`sillage export`) | dossier partagé | selon le niveau d'export, voir ci-dessous ; défaut `strict` |
| Rapport d'équipe | curateur, gestion | agrégats seulement, aucune vue par personne, k-anonymat sur les extraits |

## Les trois niveaux d'export

| | `strict` (défaut) | `metrics` | `redacted` |
|---|---|---|---|
| Compteurs, durées, tokens, dates | oui | oui | oui |
| Noms d'outils, skills, agents, serveurs MCP, modèles | oui | oui | oui |
| Nom du dépôt | oui, ou pseudonyme avec `hash_repositories = true` | idem | idem |
| Commande normalisée (`npm run test:unit`, `mvn test`) | oui | oui | oui |
| Erreur | **type + empreinte** : `reseau#9f3a1c2e` | signature caviardée : `npm error Missing script: "<val>"` | idem |
| Commande complète, extraits de sortie ou de correction | non | non | oui, caviardés |

Au niveau `strict`, **aucun mot issu d'un message d'erreur** ne quitte le poste. L'empreinte (SHA-1 de la
signature caviardée) permet de compter la même erreur sur plusieurs postes sans la révéler. Les leçons
d'équipe restent utiles (« `npm test` échoue (erreur de type « script introuvable ») : utiliser plutôt
`npm run test:unit` ») ; le détail du message reste consultable par chacun dans son rapport local.

## Le caviardage

Appliqué à la collecte, **puis de nouveau à l'export** avec les motifs en vigueur ce jour-là : un motif ajouté
plus tard protège aussi les sessions déjà collectées.

* **Intégré** : jetons GitHub/AWS/Slack, JWT, en-têtes Bearer, mots de passe et secrets en `clé=valeur`,
  chaînes de connexion, clés privées, courriels, adresses IP, téléphones, NAS et cartes (contrôle de Luhn),
  noms d'hôtes pleinement qualifiés (`db-prod-01.interne.exemple.qc.ca` → `<hote>`), chemins personnels.
* **Normalisation des signatures** : nombres, UUID, empreintes, URL, chemins et valeurs entre guillemets
  sont remplacés par des jetons (`<n>`, `<chemin>`, `"<val>"`).
* **Propre à l'organisation** (`sillage.toml`, section `[confidentialite]`) : ce qu'aucun motif générique
  ne peut deviner. Exemples :

```toml
[confidentialite]
motifs = [
  '\bdpprod\b',                 # noms d'instances
  '\bprod-[a-z0-9-]+\b',        # namespaces et environnements
  '\bDOS-\d{6}\b',              # format des numéros de dossier
  '\b[A-Z]{2,} [A-Z][a-z]+\b',  # « NOM Prénom » écrit en clair dans un message
]
```

## Risques résiduels et contrôles

| Risque | Exemple | Contrôle |
|---|---|---|
| Donnée métier dans un message d'erreur | `Client TREMBLAY Jean introuvable` | niveau `strict` par défaut ; motifs d'organisation ; revue avec `sillage inspect` |
| Nom interne révélateur | instance `dpprod`, namespace `prod-paiements` | motifs d'organisation ; `strict` |
| Nom de commande ou de script révélateur | `./purge-client-tremblay.sh` | `sillage export --preview` pendant le pilote ; motifs |
| Réidentification d'une personne | dépôt du fichier visible dans le partage, horaires de travail | nom de fichier aléatoire ; accès en lecture réservé au curateur ; aucune vue par personne dans les rapports |
| Conservation excessive | bundles accumulés | rétention de 90 jours glissants sur le dossier partagé |

## Procédure recommandée pour une organisation qui touche la production

1. Garder `export_privacy = "strict"` (défaut).
2. Pendant le pilote, chaque participant exécute une fois `sillage export --preview` : la commande affiche
   **tout le texte** qui sortirait, sans rien écrire. Toute valeur sensible devient un motif dans
   `[confidentialite]`, partagé dans le `sillage.toml` d'équipe.
3. Le curateur passe `sillage inspect` sur les bundles reçus le premier mois.
4. Dossier partagé : écriture pour les participants, **lecture pour le curateur seulement** (dépôt sans
   consultation), rétention de 90 jours.
5. Faire valider ce dispositif par le responsable de la protection des renseignements personnels et de la
   sécurité de l'information (cadre applicable, p. ex. Loi 25 au Québec). Sillage minimise les données mais
   ne remplace pas cette analyse.

## Principes

1. **On mesure les outils, pas les personnes.** Aucun tableau, tri ou export par personne.
2. **Minimisation** : jamais de prompt, de code, de contenu de fichier ni de sortie brute dans un bundle.
3. **Consentement par l'action** : rien ne quitte le poste sans `sillage export` (ou la tâche planifiée
   que la personne a elle-même créée).
4. **Pseudonymisation** HMAC-SHA256 de l'identité et des identifiants de session ; nom de fichier aléatoire.
5. **k-anonymat** (défaut k = 2) sur les extraits affichés dans un rapport d'équipe.
6. **Lecture seule côté agents** : l'agent coach ouvre des PR soumises aux revues et garde-fous ; il ne
   fusionne jamais seul.
7. **Aucun appel réseau** : Sillage n'envoie rien nulle part ; le bundle va seulement là où la personne le
   dépose.
