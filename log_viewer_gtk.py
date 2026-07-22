#!/usr/bin/env python3
"""logviewer — Viewer graphique GTK des logs JSON (Monolog / ELK) de dockr.

Application GNOME native (GTK3, PyGObject) pour lire les fichiers
`json_*.log` en JSON Lines : @timestamp, level, channel, message,
context (class/file/trace), extra, log.

Usage :
    python3 log_viewer_gtk.py [<fichier|glob> ...]

Sans argument, un sélecteur de fichiers s'ouvre.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import math
from collections import Counter
from datetime import datetime, timedelta

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio, Pango  # noqa: E402

# La timeline (Gtk.DrawingArea) exige l'intégration pycairo <-> gi
# (paquet python3-gi-cairo). Sans elle, le signal "draw" échoue : on masque
# alors la timeline au lieu de planter.
try:
    gi.require_foreign("cairo")
    HAS_CAIRO = True
except Exception:
    HAS_CAIRO = False

LEVELS = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]

# palettes de couleurs (clair / sombre)
PALETTE_LIGHT = {
    "DEBUG": "#8a8a8a", "INFO": "#2e7d32", "NOTICE": "#2e7d32",
    "WARNING": "#b8860b", "ERROR": "#c62828", "CRITICAL": "#b71c1c",
    "ALERT": "#b71c1c", "EMERGENCY": "#b71c1c",
    "_hl": "#ffe082", "_app": "#1565c0", "_vendor": "#9e9e9e", "_text": "#000000",
}
PALETTE_DARK = {
    "DEBUG": "#9e9e9e", "INFO": "#81c784", "NOTICE": "#81c784",
    "WARNING": "#ffd54f", "ERROR": "#ef9a9a", "CRITICAL": "#ff5252",
    "ALERT": "#ff5252", "EMERGENCY": "#ff5252",
    "_hl": "#665c00", "_app": "#64b5f6", "_vendor": "#757575", "_text": "#e0e0e0",
}


def hex_to_rgb(color):
    """'#rrggbb' -> (r, g, b) en [0,1]."""
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def is_dark_theme():
    """Détecte le mode sombre GNOME (color-scheme ou thème GTK)."""
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src and src.lookup("org.gnome.desktop.interface", True):
            scheme = Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme")
            if scheme:
                return "dark" in scheme
    except Exception:
        pass
    try:
        s = Gtk.Settings.get_default()
        if s.get_property("gtk-application-prefer-dark-theme"):
            return True
        name = (s.get_property("gtk-theme-name") or "").lower()
        return name.endswith("-dark") or "dark" in name
    except Exception:
        return False


# palette active + alias rétro-compatibles (renseignés dans main())
PALETTE = PALETTE_LIGHT
LEVEL_FG = PALETTE
HL_BG = PALETTE["_hl"]

CONFIG_DIR = os.path.expanduser("~/.config/logviewer")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


def parse_dt(ts):
    """ISO timestamp -> datetime naïf (sans fuseau), ou None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except ValueError:
        return None


def fmt_ts(ts):
    if not ts:
        return "--"
    dt = parse_dt(ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ts[:19]


def expand_paths(patterns):
    files = []
    for pat in patterns:
        pat = os.path.expanduser(pat)
        matched = sorted(glob.glob(pat, recursive=True))
        files.extend(matched if matched else [pat])
    # unicité en gardant l'ordre
    seen = set()
    uniq = []
    for f in files:
        if f not in seen and os.path.isfile(f):
            seen.add(f)
            uniq.append(f)
    return uniq


def parse_line(raw, path, lineno):
    raw = raw.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = {"level": "ERROR", "message": raw, "channel": "?", "@timestamp": ""}
    obj["_file"] = path
    obj["_line"] = lineno
    obj["_dt"] = parse_dt(obj.get("@timestamp"))
    return obj


def load_files(paths):
    events = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, raw in enumerate(fh, 1):
                    ev = parse_line(raw, path, lineno)
                    if ev is not None:
                        events.append(ev)
        except OSError as exc:
            print(f"Impossible de lire {path}: {exc}", file=sys.stderr)
    events.sort(key=lambda e: e.get("@timestamp") or "")
    return events


def build_matcher(text, use_regex):
    text = text.strip()
    if not text:
        return None
    try:
        return re.compile(text if use_regex else re.escape(text), re.IGNORECASE)
    except re.error:
        return None


def highlight_markup(msg, matcher):
    """Message échappé en markup Pango, occurrences surlignées."""
    msg = (msg or "").replace("\n", " ")[:500]
    if not matcher:
        return GLib.markup_escape_text(msg)
    out = []
    last = 0
    for m in matcher.finditer(msg):
        s, e = m.span()
        if e == s:
            continue
        out.append(GLib.markup_escape_text(msg[last:s]))
        out.append(f'<span background="{PALETTE["_hl"]}" foreground="{PALETTE["_text"]}">'
                   + GLib.markup_escape_text(msg[s:e]) + '</span>')
        last = e
    out.append(GLib.markup_escape_text(msg[last:]))
    return "".join(out)


class DateTimePicker(Gtk.MenuButton):
    """Bouton ouvrant un calendrier + heure/minute ; valeur = datetime | None."""

    def __init__(self, default_label, on_change):
        super().__init__()
        self._value = None
        self._on_change = on_change
        self._default_label = default_label
        self.set_label(default_label)

        pop = Gtk.Popover()
        pop.set_relative_to(self)
        self.set_popover(pop)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(box, m)(8)

        self.cal = Gtk.Calendar()
        box.pack_start(self.cal, False, False, 0)

        tbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.minute = Gtk.SpinButton.new_with_range(0, 59, 1)
        for sp in (self.hour, self.minute):
            sp.set_numeric(True)
            sp.set_width_chars(2)
        tbox.pack_start(Gtk.Label(label="Heure :"), False, False, 0)
        tbox.pack_start(self.hour, False, False, 0)
        tbox.pack_start(Gtk.Label(label=":"), False, False, 0)
        tbox.pack_start(self.minute, False, False, 0)
        box.pack_start(tbox, False, False, 0)

        bbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        clear = Gtk.Button(label="Effacer")
        apply = Gtk.Button(label="Appliquer")
        apply.get_style_context().add_class("suggested-action")
        clear.connect("clicked", self._on_clear)
        apply.connect("clicked", self._on_apply)
        bbox.pack_start(clear, True, True, 0)
        bbox.pack_start(apply, True, True, 0)
        box.pack_start(bbox, False, False, 0)

        box.show_all()
        pop.add(box)

    def _on_apply(self, _btn):
        year, month, day = self.cal.get_date()  # month : 0-11
        self._value = datetime(year, month + 1, day,
                               int(self.hour.get_value()), int(self.minute.get_value()))
        self._update_label()
        self.get_popover().popdown()
        self._on_change()

    def _on_clear(self, _btn):
        self._value = None
        self._update_label()
        self.get_popover().popdown()
        self._on_change()

    def _update_label(self):
        self.set_label(self._value.strftime("%Y-%m-%d %H:%M")
                       if self._value else self._default_label)

    def get_value(self):
        return self._value

    def set_value(self, dt):
        self._value = dt
        if dt:
            self.cal.select_month(dt.month - 1, dt.year)
            self.cal.select_day(dt.day)
            self.hour.set_value(dt.hour)
            self.minute.set_value(dt.minute)
        self._update_label()


# colonnes du modèle : ts, level, channel, message(markup), fg-color, index, count
COL_TS, COL_LEVEL, COL_CHAN, COL_MSG, COL_FG, COL_IDX, COL_COUNT = range(7)


class LogViewerWindow(Gtk.ApplicationWindow):
    def __init__(self, app, events):
        super().__init__(application=app, title="logviewer — logs JSON")
        self.set_default_size(1280, 780)
        self.events = events
        self.loaded_paths = []
        self.tracked = {}        # path -> {"off": bytes, "line": lineno}
        self.follow_id = None
        self.dt_start = None
        self.dt_end = None
        self._min_date = None
        self._loading = False    # vrai pendant la restauration des filtres
        self._tl_press_x = None  # début de sélection glissée sur la timeline
        self._tl_min = None
        self._tl_max = None
        cfg = load_config()
        self.font_size = int(cfg.get("font_size", 10))

        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "logviewer.png")
        if os.path.isfile(icon_path):
            try:
                self.set_icon_from_file(icon_path)
            except GLib.Error:
                pass

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        # --- barre d'outils ligne 1 ---
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for m in ("set_margin_top", "set_margin_start", "set_margin_end"):
            getattr(bar, m)(6)
        outer.pack_start(bar, False, False, 0)

        open_btn = Gtk.Button(label="Ouvrir…")
        open_btn.connect("clicked", self.on_open)
        bar.pack_start(open_btn, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Recherche…")
        self.search.connect("search-changed", lambda *_: self.on_search_changed())
        bar.pack_start(self.search, True, True, 0)

        self.regex_chk = Gtk.CheckButton(label="regex")
        self.regex_chk.set_tooltip_text("Interpréter la recherche comme une expression régulière")
        self.regex_chk.connect("toggled", lambda *_: self.on_search_changed())
        bar.pack_start(self.regex_chk, False, False, 0)

        self.level_combo = Gtk.ComboBoxText()
        self.level_combo.append_text("Tous niveaux")
        for lvl in LEVELS:
            self.level_combo.append_text(lvl)
        self.level_combo.set_active(0)
        self.level_combo.connect("changed", lambda *_: self._on_filter_changed())
        bar.pack_start(self.level_combo, False, False, 0)

        self.chan_combo = Gtk.ComboBoxText()
        self.chan_combo.append_text("Tous channels")
        self.chan_combo.set_active(0)
        self.chan_combo.connect("changed", lambda *_: self._on_filter_changed())
        bar.pack_start(self.chan_combo, False, False, 0)

        self.group_chk = Gtk.CheckButton(label="Grouper")
        self.group_chk.set_tooltip_text("Replier les events identiques consécutifs (×N)")
        self.group_chk.connect("toggled", lambda *_: (self.populate(),
                                                      self._persist_filters()))
        bar.pack_start(self.group_chk, False, False, 0)

        self.follow_btn = Gtk.ToggleButton(label="Suivre")
        self.follow_btn.set_tooltip_text("Suivi temps réel (tail -f) des fichiers chargés")
        self.follow_btn.connect("toggled", self.on_follow_toggled)
        bar.pack_start(self.follow_btn, False, False, 0)

        export_btn = Gtk.Button(label="Exporter…")
        export_btn.set_tooltip_text("Exporter les events affichés en JSON ou CSV")
        export_btn.connect("clicked", self.on_export)
        bar.pack_start(export_btn, False, False, 0)

        # --- barre d'outils ligne 2 : compteurs + plage de dates ---
        bar2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(bar2, m)(6)
        outer.pack_start(bar2, False, False, 0)

        self.counts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar2.pack_start(self.counts_box, False, False, 0)

        bar2.pack_end(self._make_date_filters(), False, False, 0)

        # --- timeline (histogramme temporel, cliquable/glissable) ---
        self.timeline = None
        if HAS_CAIRO:
            self.timeline = Gtk.DrawingArea()
            self.timeline.set_size_request(-1, 64)
            self.timeline.set_tooltip_text(
                "Volume d'events dans le temps (couleur = niveau max). "
                "Cliquez ou glissez pour cadrer une période.")
            self.timeline.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                     | Gdk.EventMask.BUTTON_RELEASE_MASK)
            self.timeline.connect("draw", self._draw_timeline)
            self.timeline.connect("button-press-event", self._tl_press)
            self.timeline.connect("button-release-event", self._tl_release)
            outer.pack_start(self.timeline, False, False, 0)

        # --- vue divisée liste / détail ---
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_position(470)
        outer.pack_start(paned, True, True, 0)

        self.store = Gtk.ListStore(str, str, str, str, str, int, int)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.tree = Gtk.TreeView(model=self.filter)
        self.tree.set_fixed_height_mode(True)
        self._add_column("Date/heure", COL_TS, 165)
        self._add_column("Niveau", COL_LEVEL, 90)
        self._add_column("Channel", COL_CHAN, 130)
        self._add_column("Message", COL_MSG, 620, expand=True, markup=True)
        self.tree.get_selection().connect("changed", self.on_select)
        self.tree.connect("button-press-event", self.on_tree_click)
        sc1 = Gtk.ScrolledWindow()
        sc1.add(self.tree)
        paned.pack1(sc1, True, False)

        # panneau détail
        self.detail = Gtk.TextView()
        self.detail.set_editable(False)
        self.detail.set_monospace(True)
        self.detail.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.detail.set_left_margin(8)
        self.detail.set_top_margin(6)
        dbuf = self.detail.get_buffer()
        self._hl_tag = dbuf.create_tag("hl", background=PALETTE["_hl"],
                                       foreground=PALETTE["_text"])
        self._app_tag = dbuf.create_tag("app", foreground=PALETTE["_app"],
                                        weight=Pango.Weight.BOLD)
        self._vendor_tag = dbuf.create_tag("vendor", foreground=PALETTE["_vendor"])
        sc2 = Gtk.ScrolledWindow()
        sc2.add(self.detail)
        paned.pack2(sc2, True, True)

        self.status = Gtk.Statusbar()
        outer.pack_start(self.status, False, False, 0)

        # glisser-déposer de fichiers / dossiers
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect("drag-data-received", self.on_drag_data)

        # Ctrl+clic sur le détail -> ouvrir dans l'éditeur
        self.detail.connect("button-press-event", self.on_detail_click)

        # raccourcis clavier
        self.connect("key-press-event", self.on_key)

        # taille de police (CSS)
        self._css = Gtk.CssProvider()
        for wdg in (self.tree, self.detail):
            wdg.get_style_context().add_provider(
                self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._apply_font()

        self.populate()

    # --- police / thème ---
    def _apply_font(self):
        css = f"* {{ font-size: {self.font_size}pt; }}".encode()
        self._css.load_from_data(css)

    def change_font(self, delta):
        self.font_size = max(6, min(28, self.font_size + delta))
        self._apply_font()
        cfg = load_config()
        cfg["font_size"] = self.font_size
        save_config(cfg)

    # --- raccourcis clavier ---
    def on_key(self, _w, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        kv = event.keyval
        if kv == Gdk.KEY_F5:
            self.reload()
            return True
        if ctrl and kv in (Gdk.KEY_f, Gdk.KEY_F):
            self.search.grab_focus()
            return True
        if ctrl and kv in (Gdk.KEY_o, Gdk.KEY_O):
            self.on_open(None)
            return True
        if ctrl and kv in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.change_font(1)
            return True
        if ctrl and kv in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.change_font(-1)
            return True
        return False

    def reload(self):
        if self.loaded_paths:
            self.set_files(self.loaded_paths)

    def on_drag_data(self, _w, _ctx, _x, _y, data, _info, _time):
        paths = []
        for uri in data.get_uris():
            p = GLib.filename_from_uri(uri)[0] if uri.startswith("file:") else None
            if not p:
                continue
            if os.path.isdir(p):
                paths.append(os.path.join(p, "*.log"))
            elif os.path.isfile(p):
                paths.append(p)
        if paths:
            self.set_files(paths)

    def _make_date_filters(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        # raccourcis relatifs au dernier event chargé
        for label, kind in (("15 min", "15min"), ("1 h", "1h"),
                            ("Jour", "today"), ("Tout", "all")):
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_tooltip_text("Plage relative au dernier event"
                               if kind != "all" else "Effacer la plage de dates")
            b.connect("clicked", self._on_quick_range, kind)
            box.pack_start(b, False, False, 0)
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 2)
        box.pack_start(Gtk.Label(label="De :"), False, False, 0)
        self.date_start = DateTimePicker("(début)", self.on_date_changed)
        box.pack_start(self.date_start, False, False, 0)
        box.pack_start(Gtk.Label(label="À :"), False, False, 0)
        self.date_end = DateTimePicker("(fin)", self.on_date_changed)
        box.pack_start(self.date_end, False, False, 0)
        return box

    def _on_quick_range(self, _btn, kind):
        if kind == "all":
            self.date_start.set_value(None)
            self.date_end.set_value(None)
            self.on_date_changed()
            return
        dates = [e["_dt"] for e in self.events if e.get("_dt")]
        if not dates:
            return
        end = max(dates)
        if kind == "15min":
            start = end - timedelta(minutes=15)
        elif kind == "1h":
            start = end - timedelta(hours=1)
        else:  # today : depuis minuit du jour du dernier event
            start = datetime.combine(end.date(), datetime.min.time())
        self.date_start.set_value(start)
        self.date_end.set_value(end)
        self.on_date_changed()

    def _add_column(self, title, col, width, expand=False, markup=False):
        rend = Gtk.CellRendererText()
        rend.set_property("ellipsize", Pango.EllipsizeMode.END)
        attr = {"markup": col} if markup else {"text": col}
        column = Gtk.TreeViewColumn(title, rend, foreground=COL_FG, **attr)
        column.set_resizable(True)
        column.set_fixed_width(width)
        column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        if expand:
            column.set_expand(True)
        self.tree.append_column(column)

    # --- remplissage ---
    def populate(self):
        matcher = build_matcher(self.search.get_text(), self.regex_chk.get_active())
        self.store.clear()
        dates = [e["_dt"] for e in self.events if e.get("_dt")]
        self._min_date = min(dates).date() if dates else None
        if self.group_chk.get_active():
            idx = 0
            n = len(self.events)
            while idx < n:
                e = self.events[idx]
                key = (e.get("level"), e.get("channel"), e.get("message"))
                count = 1
                while (idx + count < n and count < 9999):
                    f = self.events[idx + count]
                    if (f.get("level"), f.get("channel"), f.get("message")) != key:
                        break
                    count += 1
                self.store.append(self._row(idx, e, matcher, count))
                idx += count
        else:
            for idx, e in enumerate(self.events):
                self.store.append(self._row(idx, e, matcher, 1))
        self._update_counts()
        self.refilter()
        if self.timeline:
            self.timeline.queue_draw()

    def _msg_markup(self, idx, count, matcher):
        markup = highlight_markup(self.events[idx].get("message"), matcher)
        if count > 1:
            return f'<b>×{count}</b>  {markup}'
        return markup

    def _row(self, idx, e, matcher, count=1):
        lvl = e.get("level") or "?"
        return [
            fmt_ts(e.get("@timestamp")),
            lvl,
            (e.get("channel") or "?"),
            self._msg_markup(idx, count, matcher),
            PALETTE.get(lvl, PALETTE["_text"]),
            idx,
            count,
        ]

    def _update_counts(self):
        for child in self.counts_box.get_children():
            self.counts_box.remove(child)
        counts = Counter(e.get("level") or "?" for e in self.events)
        total = Gtk.Button(label=f"Tous {len(self.events)}")
        total.set_relief(Gtk.ReliefStyle.NONE)
        total.connect("clicked", lambda *_: self.level_combo.set_active(0))
        self.counts_box.pack_start(total, False, False, 0)
        for lvl in LEVELS:
            n = counts.get(lvl, 0)
            if not n:
                continue
            btn = Gtk.Button()
            lbl = Gtk.Label()
            lbl.set_markup(
                f'<span foreground="{LEVEL_FG.get(lvl, "#000")}">{lvl} '
                f'<b>{n}</b></span>')
            btn.add(lbl)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(f"Filtrer sur {lvl}")
            btn.connect("clicked", self._on_count_clicked, lvl)
            self.counts_box.pack_start(btn, False, False, 0)
        self.counts_box.show_all()

    def _on_count_clicked(self, _btn, lvl):
        idx = LEVELS.index(lvl) + 1
        # bascule : reclic sur le niveau actif -> tous
        self.level_combo.set_active(0 if self.level_combo.get_active() == idx else idx)

    # --- recherche / dates ---
    def on_search_changed(self):
        matcher = build_matcher(self.search.get_text(), self.regex_chk.get_active())
        # signaler une regex invalide
        invalid = (self.regex_chk.get_active()
                   and self.search.get_text().strip() and matcher is None)
        self.search.get_style_context().remove_class("error")
        if invalid:
            self.search.get_style_context().add_class("error")
        # reconstruire le surlignage des messages
        for row in self.store:
            row[COL_MSG] = self._msg_markup(row[COL_IDX], row[COL_COUNT], matcher)
        self.refilter()
        self._persist_filters()

    def on_date_changed(self):
        self.dt_start = self.date_start.get_value()
        self.dt_end = self.date_end.get_value()
        self.refilter()
        if self.timeline:
            self.timeline.queue_draw()
        self._persist_filters()

    # --- timeline ---
    def _draw_timeline(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        # fond
        bg = 0.13 if PALETTE is PALETTE_DARK else 0.96
        cr.set_source_rgb(bg, bg, bg)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        dates = [e["_dt"] for e in self.events if e.get("_dt")]
        if len(dates) < 2 or w < 2:
            return False
        mn, mx = min(dates), max(dates)
        self._tl_min, self._tl_max = mn, mx
        span = (mx - mn).total_seconds() or 1
        n = min(w, 400)
        buckets = [{} for _ in range(n)]
        for e in self.events:
            dt = e.get("_dt")
            if not dt:
                continue
            b = int((dt - mn).total_seconds() / span * (n - 1))
            lvl = e.get("level") or "?"
            buckets[b][lvl] = buckets[b].get(lvl, 0) + 1
        maxc = max((sum(b.values()) for b in buckets), default=1) or 1
        bw = w / n
        for i, b in enumerate(buckets):
            tot = sum(b.values())
            if not tot:
                continue
            sev = max(b, key=lambda l: LEVELS.index(l) if l in LEVELS else -1)
            r, g, bl = hex_to_rgb(PALETTE.get(sev, PALETTE["_text"]))
            bh = (math.log(tot + 1) / math.log(maxc + 1)) * (h - 4)
            cr.set_source_rgb(r, g, bl)
            cr.rectangle(i * bw, h - bh, max(bw - 0.5, 1), bh)
            cr.fill()
        # surbrillance de la plage sélectionnée
        if self.dt_start or self.dt_end:
            a = max(self.dt_start or mn, mn)
            z = min(self.dt_end or mx, mx)
            xa = (a - mn).total_seconds() / span * w
            xz = (z - mn).total_seconds() / span * w
            cr.set_source_rgba(0.3, 0.55, 0.95, 0.25)
            cr.rectangle(xa, 0, max(xz - xa, 1), h)
            cr.fill()
        return False

    def _tl_press(self, _area, event):
        self._tl_press_x = event.x
        return True

    def _tl_release(self, area, event):
        if self._tl_press_x is None or self._tl_min is None:
            return True
        w = area.get_allocated_width() or 1
        span = (self._tl_max - self._tl_min).total_seconds() or 1
        x0, x1 = sorted((self._tl_press_x, event.x))
        self._tl_press_x = None

        def x_to_dt(x):
            frac = max(0.0, min(x / w, 1.0))
            return self._tl_min + timedelta(seconds=frac * span)

        if x1 - x0 < 3:  # simple clic -> petite fenêtre autour du point
            center = x_to_dt((x0 + x1) / 2)
            half = timedelta(seconds=span * 0.02)
            a, z = center - half, center + half
        else:
            a, z = x_to_dt(x0), x_to_dt(x1)
        self.date_start.set_value(a)
        self.date_end.set_value(z)
        self.on_date_changed()
        return True

    # --- filtrage ---
    def _visible(self, model, it, _data):
        e = self.events[model[it][COL_IDX]]
        lvl_sel = self.level_combo.get_active_text()
        if lvl_sel and lvl_sel != "Tous niveaux" and e.get("level") != lvl_sel:
            return False
        ch_sel = self.chan_combo.get_active_text()
        if ch_sel and ch_sel != "Tous channels" and (e.get("channel") or "?") != ch_sel:
            return False
        if self.dt_start or self.dt_end:
            edt = e.get("_dt")
            if edt is None:
                return False
            if self.dt_start and edt < self.dt_start:
                return False
            if self.dt_end and edt > self.dt_end:
                return False
        matcher = build_matcher(self.search.get_text(), self.regex_chk.get_active())
        if matcher:
            blob = json.dumps(e, ensure_ascii=False, default=str)
            if not matcher.search(blob):
                return False
        return True

    def _on_filter_changed(self):
        if self._loading:
            return
        self.refilter()
        self._persist_filters()

    def refilter(self):
        self.filter.refilter()
        visible = len(self.filter)
        self.status.pop(0)
        follow = " · SUIVI" if self.follow_id else ""
        self.status.push(0, f"{visible} / {len(self.events)} events affichés{follow}")

    # --- persistance des filtres ---
    def _persist_filters(self):
        if self._loading:
            return
        cfg = load_config()
        cfg["filters"] = {
            "search": self.search.get_text(),
            "regex": self.regex_chk.get_active(),
            "group": self.group_chk.get_active(),
            "level": self.level_combo.get_active_text() or "Tous niveaux",
            "channel": self.chan_combo.get_active_text() or "Tous channels",
            "date_start": self.dt_start.isoformat() if self.dt_start else "",
            "date_end": self.dt_end.isoformat() if self.dt_end else "",
        }
        save_config(cfg)

    def _restore_filters(self):
        flt = load_config().get("filters") or {}
        if not flt:
            return
        self._loading = True
        try:
            self.search.set_text(flt.get("search", ""))
            self.regex_chk.set_active(bool(flt.get("regex")))
            self.group_chk.set_active(bool(flt.get("group")))
            self.date_start.set_value(self._iso(flt.get("date_start")))
            self.date_end.set_value(self._iso(flt.get("date_end")))
            self._set_combo(self.level_combo, flt.get("level"))
            self._set_combo(self.chan_combo, flt.get("channel"))
        finally:
            self._loading = False
        self.dt_start = self.date_start.get_value()
        self.dt_end = self.date_end.get_value()

    @staticmethod
    def _iso(text):
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _set_combo(combo, text):
        if not text:
            return
        model = combo.get_model()
        for i, row in enumerate(model):
            if row[0] == text:
                combo.set_active(i)
                return

    # --- sélection / détail ---
    def on_select(self, selection):
        model, it = selection.get_selected()
        buf = self.detail.get_buffer()
        if it is None:
            buf.set_text("")
            return
        e = self.events[model[it][COL_IDX]]
        buf.set_text(self._format_detail(e))
        self._colorize_stacktrace()
        self._apply_detail_highlight()

    def _colorize_stacktrace(self):
        """Distingue les frames applicatives (/app/) du code vendor."""
        buf = self.detail.get_buffer()
        for ln in range(buf.get_line_count()):
            start = buf.get_iter_at_line(ln)
            end = start.copy()
            if not end.forward_to_line_end():
                end = buf.get_end_iter()
            text = buf.get_text(start, end, False)
            if "/vendor/" in text:
                buf.apply_tag(self._vendor_tag, start, end)
            elif "/app/" in text:
                buf.apply_tag(self._app_tag, start, end)

    # --- Ctrl+clic -> ouvrir fichier:ligne dans l'éditeur ---
    def on_detail_click(self, view, event):
        if event.button != 1 or not (event.state & Gdk.ModifierType.CONTROL_MASK):
            return False
        bx, by = view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, int(event.x), int(event.y))
        ok, it = view.get_iter_at_location(bx, by)
        if not ok:
            return False
        buf = view.get_buffer()
        start = buf.get_iter_at_line(it.get_line())
        end = start.copy()
        if not end.forward_to_line_end():
            end = buf.get_end_iter()
        text = buf.get_text(start, end, False)
        m = re.search(r'(/[^\s:()]+\.php)[:(](\d+)', text)
        if not m:
            return False
        self._open_in_editor(m.group(1), int(m.group(2)))
        return True

    def _resolve_source(self, container_path):
        """Traduit un chemin conteneur (/app/...) en chemin hôte réel."""
        if os.path.isfile(container_path):
            return container_path
        cfg = load_config()
        for prefix, host in (cfg.get("path_map") or {}).items():
            if container_path.startswith(prefix):
                cand = os.path.expanduser(host + container_path[len(prefix):])
                if os.path.isfile(cand):
                    return cand
        # auto-résolution sous les dépôts services de dockr
        m = re.search(r"/app/(.+)$", container_path)
        if m:
            base = os.path.expanduser("~/projets/dockr/services")
            hits = glob.glob(os.path.join(base, "*", m.group(1)))
            if len(hits) == 1:
                return hits[0]
        return None

    def _open_in_editor(self, container_path, line):
        host = self._resolve_source(container_path)
        self.status.pop(0)
        if not host:
            self.status.push(0, f"Fichier introuvable sur l'hôte : {container_path} "
                                f"(configurez 'path_map' dans {CONFIG_FILE})")
            return
        editor = shutil.which("phpstorm") or shutil.which("code")
        if not editor:
            self.status.push(0, "Aucun éditeur trouvé (phpstorm/code) dans le PATH")
            return
        try:
            if editor.endswith("code"):
                subprocess.Popen([editor, "-g", f"{host}:{line}"])
            else:
                subprocess.Popen([editor, "--line", str(line), host])
            self.status.push(0, f"Ouverture {host}:{line}")
        except OSError as exc:
            self.status.push(0, f"Échec ouverture : {exc}")

    def _apply_detail_highlight(self):
        buf = self.detail.get_buffer()
        start, end = buf.get_bounds()
        buf.remove_tag(self._hl_tag, start, end)
        matcher = build_matcher(self.search.get_text(), self.regex_chk.get_active())
        if not matcher:
            return
        text = buf.get_text(start, end, False)
        for m in matcher.finditer(text):
            s, e = m.span()
            if e == s:
                continue
            buf.apply_tag(self._hl_tag,
                          buf.get_iter_at_offset(s), buf.get_iter_at_offset(e))

    def _format_detail(self, e):
        lines = [
            f"{e.get('level','?')}   {fmt_ts(e.get('@timestamp'))}   [{e.get('channel','?')}]",
            f"source : {e.get('_file','?')}:{e.get('_line','?')}",
            "",
            "message :",
            f"  {e.get('message','')}",
        ]
        ctx = e.get("context") or {}
        if isinstance(ctx, dict) and ctx:
            lines.append("")
            for key in ("class", "code", "type", "file"):
                if ctx.get(key):
                    lines.append(f"context.{key} : {ctx[key]}")
            trace = ctx.get("trace")
            if trace:
                lines += ["", "--- stacktrace ---", str(trace)]
            other = {k: v for k, v in ctx.items()
                     if k not in ("class", "code", "type", "file", "trace", "message")}
            if other:
                lines += ["", "--- context (autres) ---",
                          json.dumps(other, indent=2, ensure_ascii=False)]
        extra = e.get("extra")
        if extra:
            lines += ["", "--- extra ---", json.dumps(extra, indent=2, ensure_ascii=False)]
        return "\n".join(lines)

    # --- copier / exporter ---
    def _selected_event(self):
        model, it = self.tree.get_selection().get_selected()
        if it is None:
            return None
        return self.events[model[it][COL_IDX]]

    def _clean_event(self, e):
        """Copie de l'event sans les champs internes (_file, _line, _dt)."""
        return {k: v for k, v in e.items() if not k.startswith("_")}

    def _copy(self, text):
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(text or "", -1)

    def on_tree_click(self, _tree, event):
        if event.button != 3:  # clic droit uniquement
            return False
        path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
        if path_info:
            self.tree.get_selection().select_path(path_info[0])
        e = self._selected_event()
        if e is None:
            return False
        menu = Gtk.Menu()
        items = [
            ("Copier la ligne JSON",
             lambda *_: self._copy(json.dumps(self._clean_event(e), ensure_ascii=False))),
            ("Copier le message", lambda *_: self._copy(e.get("message", ""))),
        ]
        trace = (e.get("context") or {}).get("trace") if isinstance(e.get("context"), dict) else None
        if trace:
            items.append(("Copier la stacktrace", lambda *_: self._copy(str(trace))))
        for label, cb in items:
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", cb)
            menu.append(mi)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _visible_events(self):
        """Events actuellement affichés (dans l'ordre de la liste filtrée)."""
        return [self.events[row[COL_IDX]] for row in self.filter]

    def on_export(self, _btn):
        evs = self._visible_events()
        if not evs:
            return
        dlg = Gtk.FileChooserDialog(
            title="Exporter les events affichés", parent=self,
            action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons("Annuler", Gtk.ResponseType.CANCEL,
                        "Enregistrer", Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name("export.json")
        for name, pat in (("JSON (*.json)", "*.json"), ("CSV (*.csv)", "*.csv")):
            f = Gtk.FileFilter()
            f.set_name(name)
            f.add_pattern(pat)
            dlg.add_filter(f)
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            try:
                self._write_export(path, evs)
                self.status.pop(0)
                self.status.push(0, f"{len(evs)} events exportés → {path}")
            except OSError as exc:
                self.status.pop(0)
                self.status.push(0, f"Échec export : {exc}")
        dlg.destroy()

    def _write_export(self, path, evs):
        clean = [self._clean_event(e) for e in evs]
        if path.lower().endswith(".csv"):
            import csv
            with open(path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["timestamp", "level", "channel", "message",
                            "class", "file"])
                for e in evs:
                    ctx = e.get("context") if isinstance(e.get("context"), dict) else {}
                    w.writerow([
                        e.get("@timestamp", ""), e.get("level", ""),
                        e.get("channel", ""), (e.get("message", "") or "").replace("\n", " "),
                        ctx.get("class", ""), ctx.get("file", ""),
                    ])
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(clean, fh, indent=2, ensure_ascii=False)

    # --- suivi temps réel (tail -f) ---
    def on_follow_toggled(self, btn):
        if btn.get_active():
            self._seed_tracking()
            self.follow_id = GLib.timeout_add(1000, self._poll_tail)
        else:
            if self.follow_id:
                GLib.source_remove(self.follow_id)
                self.follow_id = None
        self.refilter()

    def _seed_tracking(self):
        self.tracked = {}
        for path in self.loaded_paths:
            try:
                size = os.path.getsize(path)
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    nlines = sum(1 for _ in fh)
                self.tracked[path] = {"off": size, "line": nlines}
            except OSError:
                continue

    def _poll_tail(self):
        matcher = build_matcher(self.search.get_text(), self.regex_chk.get_active())
        added = 0
        for path, st in self.tracked.items():
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < st["off"]:      # rotation / troncature -> relire
                st["off"], st["line"] = 0, 0
            if size <= st["off"]:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(st["off"])
                    chunk = fh.read()
                    st["off"] = fh.tell()
            except OSError:
                continue
            for raw in chunk.splitlines():
                if not raw.strip():
                    continue
                st["line"] += 1
                ev = parse_line(raw, path, st["line"])
                if ev is None:
                    continue
                idx = len(self.events)
                self.events.append(ev)
                self.store.append(self._row(idx, ev, matcher))
                added += 1
        if added:
            self._update_counts()
            self.refilter()
            if self.timeline:
                self.timeline.queue_draw()
            # auto-scroll vers la fin si des lignes sont visibles
            n = len(self.filter)
            if n:
                self.tree.scroll_to_cell(Gtk.TreePath(n - 1), None, False, 0, 0)
        return True  # continuer le timer

    # --- ouverture de fichiers ---
    def on_open(self, _btn):
        dlg = Gtk.FileChooserDialog(
            title="Ouvrir des fichiers de log", parent=self,
            action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Annuler", Gtk.ResponseType.CANCEL,
                        "Ouvrir", Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)
        flt = Gtk.FileFilter()
        flt.set_name("Logs JSON (*.log)")
        flt.add_pattern("*.log")
        dlg.add_filter(flt)
        allf = Gtk.FileFilter()
        allf.set_name("Tous les fichiers")
        allf.add_pattern("*")
        dlg.add_filter(allf)
        last = load_config().get("last_folder")
        default = os.path.expanduser("~/projets/dockr/data/php/logs")
        folder = last if last and os.path.isdir(last) else default
        if os.path.isdir(folder):
            dlg.set_current_folder(folder)
        if dlg.run() == Gtk.ResponseType.OK:
            paths = dlg.get_filenames()
            cur = dlg.get_current_folder()
            if cur:
                cfg = load_config()
                cfg["last_folder"] = cur
                save_config(cfg)
            self.set_files(paths)
        dlg.destroy()

    def set_files(self, paths):
        # arrêter le suivi en cours avant de recharger
        if self.follow_btn.get_active():
            self.follow_btn.set_active(False)
        self.loaded_paths = expand_paths(paths)
        self.events = load_files(self.loaded_paths)
        self.chan_combo.remove_all()
        self.chan_combo.append_text("Tous channels")
        for ch in sorted({e.get("channel") or "?" for e in self.events}):
            self.chan_combo.append_text(ch)
        self.chan_combo.set_active(0)
        self._restore_filters()
        self.populate()


class LogViewerApp(Gtk.Application):
    def __init__(self, patterns):
        super().__init__(application_id="com.peopulse.logviewer")
        self.patterns = patterns
        self.win = None

    def do_activate(self):
        if not self.win:
            self.win = LogViewerWindow(self, [])
        if self.patterns:
            self.win.set_files(self.patterns)
        self.win.show_all()
        self.win.present()
        if not self.patterns:
            GLib.idle_add(self.win.on_open, None)


def main():
    global PALETTE, LEVEL_FG, HL_BG
    if is_dark_theme():
        PALETTE = PALETTE_DARK
        LEVEL_FG = PALETTE
        HL_BG = PALETTE["_hl"]
        try:
            Gtk.Settings.get_default().set_property(
                "gtk-application-prefer-dark-theme", True)
        except Exception:
            pass
    app = LogViewerApp(sys.argv[1:])
    app.run(None)


if __name__ == "__main__":
    main()
