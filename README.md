# logviewer

Viewer pour les logs JSON (Monolog / ELK) de dockr. Deux interfaces :

- **`log_viewer_gtk.py`** — application graphique GNOME (GTK3 / PyGObject).
- **`log_viewer.py`** — viewer terminal (TUI curses, zéro dépendance).

## Mode graphique (GNOME / GTK)

Prérequis : `python3-gi` + GTK3 (déjà présents sous GNOME).
Pour la **timeline**, le paquet `python3-gi-cairo` est requis
(`sudo apt install python3-gi-cairo`) ; sans lui, la timeline est simplement
masquée et le reste fonctionne.

```bash
# Ouvre directement des fichiers
python3 log_viewer_gtk.py <filename>

# Sans argument : ouvre un sélecteur de fichiers
python3 log_viewer_gtk.py
```

Depuis GNOME : chercher **« logviewer »** dans les Activités (un lanceur
`.desktop` est installé dans `~/.local/share/applications/`).

Fonctions :
- liste triée par date (colonnes date/niveau/channel/message, colorée par niveau) ;
- **timeline** temporelle colorée par niveau (clic/glissé pour cadrer une période) ;
- filtres : niveau, channel, **plage de dates** via sélecteur calendrier
  (« De » / « À » : `Gtk.Calendar` + heure/minute) + **boutons rapides**
  (`15 min` / `1 h` / `Jour` / `Tout`), recherche texte ou **regex** avec
  **surlignage** des occurrences ;
- **compteurs par niveau** cliquables (`Tous N · ERROR n · CRITICAL n …`) ;
- **suivi temps réel** (`tail -f`) via le bouton *Suivre* (auto-scroll) ;
- **groupement** des doublons consécutifs (`×N`) via la case *Grouper* ;
- **clic droit** : copier la ligne JSON / le message / la stacktrace ;
- bouton *Exporter…* : sélection affichée vers `.json` ou `.csv` ;
- **glisser-déposer** de fichiers ou dossiers sur la fenêtre ;
- panneau détail avec stacktrace `context.trace` dépliée et **colorée**
  (frames `/app/` en bleu, `vendor/` en gris) ;
- **Ctrl+clic** sur un `fichier:ligne` du détail → ouvre dans PhpStorm/VS Code
  (résolution auto `/app/...` → `services/*`, surchargeable via `path_map`) ;
- **thème sombre auto** (suit GNOME) + police ajustable (`Ctrl +` / `Ctrl -`) ;
- **filtres persistés** entre sessions ;
- bouton *Ouvrir…* qui mémorise le dernier dossier consulté.

### Raccourcis clavier

| Raccourci | Action |
|---|---|
| `Ctrl+F` | focus recherche |
| `Ctrl+O` | ouvrir des fichiers |
| `F5` | recharger |
| `Ctrl +` / `Ctrl -` | taille de police |
| `Ctrl+clic` (détail) | ouvrir `fichier:ligne` dans l'éditeur |

### Configuration (`~/.config/logviewer/config.json`)

- `last_folder` — dernier dossier ouvert
- `font_size` — taille de police
- `filters` — derniers filtres (recherche, niveau, channel, dates…)
- `path_map` — table de correspondance chemin conteneur → hôte, ex :
  `{"/app": "<filename>"}`

## Mode terminal (TUI)

Viewer TUI pour les logs JSON (Monolog / ELK) — zéro dépendance (stdlib Python).

Adapté aux fichiers `json_*.log` en JSON Lines : `@timestamp`, `level`,
`channel`, `message`, `context` (class/file/trace), `extra`, `log`.

## Usage

```bash
python3 log_viewer.py <fichier|glob> [<fichier|glob> ...]
```

Exemples :

```bash
# Un fichier
python3 log_viewer.py <filename>

# Tout un dossier (fusionné + trié par date)
python3 log_viewer.py <filename>

# Tous les crashs, récursif
python3 log_viewer.py <filename>
```


## Touches

| Touche          | Action                                             |
|-----------------|----------------------------------------------------|
| `↑`/`↓` `j`/`k` | naviguer                                           |
| `↵` / espace    | détail (message, class, file, stacktrace, extra)   |
| `/` puis texte  | recherche plein texte                              |
| `n` / `N`       | occurrence suivante / précédente                   |
| `1`-`5`         | filtre niveau DEBUG/INFO/WARNING/ERROR/CRITICAL    |
| `c`             | filtrer par channel de la ligne courante           |
| `0`             | réinitialiser les filtres                          |
| `g` / `G`       | début / fin                                        |
| `q`             | fermer le détail / quitter                         |

## Fonctionnalités

- Colorisation par niveau
- Colonnes `timestamp | level | channel | message`
- Vue détail qui déplie la stacktrace `context.trace`
- Fusion multi-fichiers triée par `@timestamp`
- Robuste aux lignes non-JSON
