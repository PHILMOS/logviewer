# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**logviewer** est un viewer de logs pour la stack **dockr**. Deux interfaces
autonomes, **sans dépendance hors stdlib + PyGObject** :

- `log_viewer_gtk.py` — application graphique GNOME (GTK3). Interface principale.
- `log_viewer.py` — viewer terminal (TUI curses). Zéro dépendance.

Formats détectés **par ligne** (voir `PARSERS`) : JSON Monolog/ELK, PHP
error_log, Apache error/access, syslog/logs système, texte générique. Les
fichiers **`.gz`** sont lus directement (`open_text`). Logs dockr sous
`~/projets/dockr/data/php/logs/**`.

Dépendance optionnelle : **`python3-gi-cairo`** pour la timeline (sinon masquée,
cf. `HAS_CAIRO`).

## Commandes

```bash
# GUI : ouvrir des fichiers / globs (~ et ** supportés)
python3 log_viewer_gtk.py '~/projets/dockr/data/php/logs/eu-interfaces/json_*.log'
python3 log_viewer_gtk.py            # sans argument -> sélecteur de fichiers

# TUI terminal
python3 log_viewer.py '<glob>'

# Vérif syntaxe rapide
python3 -c "import ast; ast.parse(open('log_viewer_gtk.py').read())"
```

Prérequis GUI : `python3-gi` + GTK3 (présents par défaut sous GNOME). Vérifier :
`python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk"`

## Tester le GUI (pas de xvfb dans l'environnement)

Il n'y a **pas de framework de test** et **pas de xvfb**. On teste sur le
`DISPLAY` réel de la session GNOME en pilotant l'app par le code : importer le
module via `importlib`, sous-classer `Gtk.Application`, agir dans `do_activate`,
puis `GLib.timeout_add(...)` pour l'action différée (ex. tail) et `self.quit()`.

```python
import importlib.util
spec = importlib.util.spec_from_file_location('gv', 'log_viewer_gtk.py')
gv = importlib.util.module_from_spec(spec); spec.loader.exec_module(gv)
import gi; from gi.repository import Gtk, GLib
class T(Gtk.Application):
    def do_activate(self):
        w = gv.LogViewerWindow(self, []); w.set_files([path]); w.show_all()
        # ... assertions sur w.events, w.store, w.filter ...
        GLib.timeout_add(150, self.quit)
T().run(None)
```

Pour tester le tail : écrire un fichier partiel, `w.follow_btn.set_active(True)`,
ajouter des lignes via un `GLib.timeout_add`, vérifier `len(w.events)` plus tard.
Filtrer le bruit GTK des sorties : `grep -viE 'warning|dbind|accessibility|fixed_height'`.
**Toujours sauvegarder/restaurer** `~/.config/logviewer/config.json` autour d'un
test qui modifie les filtres/police.

## Architecture (log_viewer_gtk.py)

Fonctions module (logique pure, testables isolément) :
- `load_files(paths)` → `parse_line()` par ligne → liste d'events triés par `@timestamp`.
- `parse_line()` **détecte le format** en essayant `PARSERS` dans l'ordre (JSON
  Monolog, PHP error_log, Apache error/access, syslog, puis `parse_generic` en
  filet). Chaque parseur renvoie un dict avec au moins `@timestamp` (ISO),
  `level`, `channel`, `message`. `parse_line()` ajoute les champs **internes
  préfixés `_`** : `_file`, `_line`, `_dt`. `_clean_event()` les retire à l'export.
  Dates non-ISO parsées via `_mkdt()` + `_MONTHS` (indépendant de la locale —
  ne pas utiliser `strptime("%b")` qui casse en locale FR). Niveau texte deviné
  par `_canon_level()` (mots-clés ordonnés). `parse_line()` calcule aussi
  **`_blob`** : le JSON de l'event en minuscules, pré-calculé une fois pour la
  recherche (ne pas refaire de `json.dumps` par ligne dans `_visible`).
- `build_matcher(text, use_regex)` → `re.Pattern | None` (échappe si non-regex,
  `IGNORECASE`). Une regex invalide renvoie `None`.
- `highlight_markup()` produit le markup Pango de la colonne message.
- `is_dark_theme()` + `PALETTE_LIGHT`/`PALETTE_DARK` : la palette active est le
  global **`PALETTE`** (fixé dans `main()` selon le thème). Toutes les couleurs
  (niveaux, surlignage `_hl`, frames `_app`/`_vendor`, texte `_text`) y passent.

UI :
- `LogViewerApp(Gtk.Application)` (application_id `com.peopulse.logviewer`,
  requis pour les notifications) → crée une unique `LogViewerWindow`.
- Disposition : `Gtk.Paned` horizontal = **panneau latéral fichiers** (gauche,
  `ListBox` multi-sélection d'un dossier) | contenu (droite). Contenu : barres
  d'outils, **timeline** (`Gtk.DrawingArea`, si `HAS_CAIRO`), `Gtk.TreeView` sur
  `Gtk.ListStore` (+ `Gtk.TreeModelFilter`), panneau détail, barre de statut.
- **Tout le filtrage passe par `_visible()`** : marque-pages, niveaux
  (`active_levels`, multi), channel, **source** (fichier), tag de **context**
  (`ctx_key`/`ctx_val`), plage de dates, puis `self._cur_matcher` sur `_blob`.
  Changer un filtre → `refilter()`, qui **compile la regex une seule fois**
  (`_cur_matcher`) puis `self.filter.refilter()`.
- Colonnes **`COL_*`** (`range(9)`) : ts, level, chan, msg(markup), fg, idx,
  count, bookmark(★), source. `COL_IDX` pointe vers `self.events`, `COL_COUNT`
  porte le `×N` du groupement.
- **`populate()` est progressif** : construit la liste de travail puis insère par
  lots (`POPULATE_CHUNK`) via `GLib.idle_add(_populate_step)` pour ne pas geler
  l'UI (statut « Chargement… N/total »). Petits volumes insérés d'un coup.
- `DateTimePicker(Gtk.MenuButton)` : calendrier + heure/minute, `get/set_value`.

Invariants importants :
- Les events sont **triés à l'ouverture** ; le **tail** (`_poll_tail`) ajoute en
  fin sans re-trier, suit offset octet + n° ligne par fichier dans `self.tracked`
  (gère rotation/troncature ; les `.gz` sont exclus du tail). Émet une
  notification GNOME sur `NOTIFY_LEVELS` si la case *Notifier* est active.
- Re-surlignage de la liste (recherche) : modifie `COL_MSG` via `_msg_markup()`
  en préservant `COL_COUNT`, et **plafonné à `MARKUP_MAX` lignes** (perf).
- Marque-pages (`self.bookmarks`, index) : **session uniquement**, effacés au
  rechargement. Combos source/context reconstruits dans `set_files()`.

## Configuration : `~/.config/logviewer/config.json`

Clés lues/écrites par `load_config`/`save_config` :
- `last_folder` — dernier dossier du sélecteur.
- `font_size` — taille police (CSS, `Ctrl +/-`).
- `filters` — état complet des filtres, restauré dans `set_files()` **avec le
  garde `self._loading`** pour ne pas re-persister pendant la restauration.
- `path_map` — correspondance chemin conteneur → hôte pour le Ctrl+clic éditeur.

## Résolution de chemin (Ctrl+clic → PhpStorm/VS Code)

`_resolve_source()` traduit un chemin conteneur (`/app/...`) en chemin hôte :
1. chemin tel quel s'il existe ; 2. préfixes de `path_map` ; 3. **auto-résolution**
en cherchant `~/projets/dockr/services/*/<reste après /app/>` (retenu si unique).

## Intégration GNOME

- Lanceur : `~/.local/share/applications/logviewer.desktop` (+ copie sur le
  Bureau, marquée `gio set ... metadata::trusted true`).
- Icône : `logviewer.png` (chargée par la fenêtre et le `.desktop`).
