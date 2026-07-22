# logviewer — TODO / Améliorations

État actuel : liste triée par date, filtres niveau/channel, recherche plein
texte, panneau détail avec stacktrace, mémorisation du dernier dossier.

## Priorité haute — usage quotidien ✅ FAIT
- [x] **Suivi temps réel (`tail -f`)** — bouton « Suivre » : lit les nouvelles
      lignes des fichiers chargés et auto-scroll. Gère la rotation/troncature.
- [x] **Filtre par plage de dates/heures** — champs « De » / « À »
      (`AAAA-MM-JJ HH:MM`, ou `HH:MM` seul).
- [x] **Recherche regex + surlignage** — case « regex » ; occurrences
      surlignées dans la colonne message ET le panneau détail.
- [x] **Compteurs par niveau dans la barre** — `Tous N · INFO n · ERROR n …`,
      cliquables pour filtrer (bascule).

## Priorité moyenne — confort ✅ FAIT
- [x] **Copier / exporter** — clic droit : copier la ligne JSON, le message,
      la stacktrace ; bouton *Exporter…* de la sélection filtrée en `.json` / `.csv`.
- [x] **Groupement des doublons** — case *Grouper* : replie les events
      identiques consécutifs en `×N`.
- [x] **Glisser-déposer** de fichiers ET dossiers (`*.log`) sur la fenêtre.
- [x] **Coloration de la stacktrace** — frames applicatives (`/app/`) en bleu,
      `vendor/` en gris.

## Priorité basse — finition ✅ FAIT
- [x] **Thème sombre auto** (suit GNOME `color-scheme`) + palettes adaptées ;
      **taille de police** configurable via `Ctrl +` / `Ctrl -` (persistée).
- [x] **Persistance des filtres** entre sessions (`config.json` → `filters`).
- [x] **Raccourcis clavier** — `Ctrl+F` recherche, `Ctrl+O` ouvrir,
      `F5` recharger, `Ctrl +/-` police.
- [x] **Ctrl+clic sur `fichier:ligne`** dans le détail → ouvre dans PhpStorm
      (ou VS Code). Résolution auto `/app/...` → dépôt `services/*` de dockr,
      surchargeable via `path_map` dans `config.json`.

## Améliorations v2
- [x] **Timeline / histogramme** — bande temporelle colorée par niveau max ;
      clic ou glissé pour cadrer une période. (Requiert `python3-gi-cairo`.)
- [x] **Filtres de date rapides** — boutons `15 min` / `1 h` / `Jour` / `Tout`
      relatifs au dernier event.
- [x] **Filtre multi-niveaux** — boutons compteurs cumulables (ToggleButton),
      « Tous » efface la sélection.
- [x] **Marque-pages / drapeaux** — colonne ★, `Ctrl+B` (bascule),
      `F2`/`Maj+F2` (navigation), toggle « ★ seulement ». (Session uniquement.)
- [x] **Retour à la ligne (wrap)** togglable sur la colonne message.
- [x] **Panneau latéral** : liste des fichiers `.log` d'un dossier,
      multi-sélection pour chargement dynamique, bouton *Dossier…* + refresh.
- [x] **Filtre par tag de context** — clé découverte dynamiquement + valeur
      (liste plafonnée à 300 valeurs les plus fréquentes).
- [x] **Colonne « source »** (fichier d'origine) + combo de filtre par fichier.
- [x] **Multi-formats** — détection auto par ligne : JSON Monolog, PHP error_log,
      Apache error/access, syslog/logs système, texte générique.
- [x] **Support des logs `.gz`** rotés — décompression transparente (`open_text`).
      Non suivis en tail (statiques).
- [x] **Notification desktop** sur ERROR/CRITICAL en mode Suivre (case *Notifier*).
- [x] **Perf gros fichiers** — remplissage progressif non bloquant (idle chunks),
      blob de recherche pré-calculé + compilation unique de la regex,
      re-surlignage de liste plafonné (5000 lignes).
- [x] **Dépôt git** — https://github.com/PHILMOS/logviewer (tests unitaires : à faire).

---
Priorités initiales (haute / moyenne / basse) : toutes implémentées. ✅
v2 : timeline, dates rapides, multi-niveaux, marque-pages, wrap faits.
