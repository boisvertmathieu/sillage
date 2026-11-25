# Charte de confidentialité et d'éthique

Sillage n'est utile que si les équipes lui font confiance. Ces règles sont **intégrées au code** (et testées),
pas seulement écrites.

## Principes

1. **On mesure les outils, pas les personnes.** Aucun tableau, tri ou export par personne. Les pseudonymes
   ne servent qu'à compter des personnes distinctes (adoption, k-anonymat).
2. **Minimisation.** Le magasin local ne garde jamais les journaux bruts ni les prompts, le code, les sorties
   d'outils ou le contenu des fichiers. Seuls des compteurs, des noms (outils, skills, agents, serveurs MCP,
   dépôts) et des signatures d'erreurs normalisées sont conservés.
3. **Caviardage systématique** de tout extrait (niveau `redacted`) : jetons GitHub/AWS/Slack, JWT, Bearer,
   mots de passe, chaînes de connexion, clés privées, courriels, IP, téléphones, NAS et cartes (contrôle
   de Luhn), chemins personnels (`/home/<nom>`, `C:\Users\<nom>`).
4. **Consentement par l'action.** Rien ne quitte le poste sans `sillage export` explicite. Le niveau
   d'export par défaut est `metrics` : **aucun texte libre**. Le niveau `redacted` à l'échelle de l'équipe
   demande une décision explicite.
5. **Pseudonymisation** HMAC-SHA256 : identité (`utilisateur@poste`) et identifiants de session. Sel local
   aléatoire par défaut ; sel d'équipe partagé (`SILLAGE_SALT`) seulement si l'on veut des comptes de
   personnes distinctes entre postes, à conserver comme un secret.
6. **k-anonymat** (défaut k = 2) : dans un rapport d'équipe, un extrait n'est affiché que s'il concerne au
   moins k personnes distinctes.
7. **Lecture seule côté agents.** Le serveur MCP n'expose que des outils de lecture. L'agent coach ouvre des
   PR soumises aux revues et garde-fous habituels ; il ne fusionne jamais seul.

## À valider avec votre organisation avant un déploiement d'équipe

* Avis du responsable de la protection des renseignements personnels et de la sécurité de l'information
  (cadre légal applicable à votre organisation, p. ex. Loi 25 au Québec) — Sillage minimise les données mais
  ne remplace pas cette analyse.
* Communication aux équipes : finalité (améliorer les outils), données collectées, niveau d'export, droit de
  ne pas participer, durée de conservation des bundles.
* Emplacement de dépôt des bundles (partage restreint) et durée de conservation (ex. 90 jours glissants).
* Les noms de dépôts sont conservés par défaut ; `hash_repositories = true` les pseudonymise à l'export.

## Ce que Sillage ne fait pas

* Il n'évalue pas la performance individuelle et ne doit jamais servir à cela.
* Il ne lit pas le contenu des fichiers modifiés, des prompts ni des réponses du modèle au niveau `metrics`.
* Il n'envoie rien sur le réseau : pas de télémétrie, pas d'appel à un service externe.
