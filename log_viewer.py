#!/usr/bin/env python3
"""Viewer TUI pour les logs JSON (Monolog / ELK) de dockr.

Adapté aux fichiers data/php/logs/**/json_*.log (JSON Lines).
Chaque ligne est un event Monolog : @timestamp, level, channel, message,
context (class/file/trace pour les crash), extra, log.

Usage :
    python3 utils/log_viewer.py <fichier|glob> [<fichier|glob> ...]

Exemples :
    python3 utils/log_viewer.py data/php/logs/eu-interfaces/json_php_crash-2026-07-16_10.log
    python3 utils/log_viewer.py 'data/php/logs/eu-interfaces/json_*.log'
    python3 utils/log_viewer.py 'data/php/logs/**/json_php_crash-*.log'

Touches :
    ↑/↓ ou j/k   déplacer         ↵ / espace   ouvrir le détail
    PgUp/PgDn    page             g / G         début / fin
    /            recherche texte  n / N         occurrence suiv./préc.
    1..5         filtre niveau (DEBUG..CRITICAL) — bascule
    0            réinitialiser les filtres
    c            filtrer par channel de la ligne courante (bascule)
    q            quitter (ou fermer le détail)
"""
from __future__ import annotations

import curses
import glob
import json
import os
import sys
import textwrap
from datetime import datetime

LEVELS = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
LEVEL_ORDER = {lvl: i for i, lvl in enumerate(LEVELS)}

# paire de couleurs curses par niveau
LEVEL_COLOR = {
    "DEBUG": 1, "INFO": 2, "NOTICE": 2, "WARNING": 3,
    "ERROR": 4, "CRITICAL": 4, "ALERT": 4, "EMERGENCY": 4,
}


def load_files(patterns):
    """Charge tous les events des fichiers correspondant aux patterns."""
    events = []
    files = []
    for pat in patterns:
        pat = os.path.expanduser(pat)
        matched = sorted(glob.glob(pat, recursive=True))
        files.extend(matched if matched else [pat])
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, raw in enumerate(fh, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        obj = {"level": "ERROR", "message": raw,
                               "channel": "?", "@timestamp": "",
                               "_parse_error": True}
                    obj["_file"] = path
                    obj["_line"] = lineno
                    events.append(obj)
        except OSError as exc:
            print(f"Impossible de lire {path}: {exc}", file=sys.stderr)
    return events


def fmt_ts(ts):
    """Raccourcit l'ISO timestamp en HH:MM:SS (ou le jour si présent)."""
    if not ts:
        return "--:--:--"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M:%S")
    except ValueError:
        return ts[:14]


class Viewer:
    def __init__(self, events):
        self.all = events
        self.filtered = events
        self.level_filter = set()      # niveaux actifs (vide = tous)
        self.channel_filter = None
        self.search = ""
        self.cursor = 0
        self.top = 0
        self.detail = None             # event affiché en plein écran, ou None

    # --- filtrage ---
    def apply_filters(self):
        res = self.all
        if self.level_filter:
            res = [e for e in res if e.get("level") in self.level_filter]
        if self.channel_filter:
            res = [e for e in res if e.get("channel") == self.channel_filter]
        if self.search:
            s = self.search.lower()
            res = [e for e in res
                   if s in json.dumps(e, ensure_ascii=False).lower()]
        self.filtered = res
        self.cursor = min(self.cursor, max(0, len(res) - 1))
        self.top = 0

    def find(self, direction):
        if not self.search or not self.filtered:
            return
        s = self.search.lower()
        n = len(self.filtered)
        for step in range(1, n + 1):
            idx = (self.cursor + direction * step) % n
            blob = json.dumps(self.filtered[idx], ensure_ascii=False).lower()
            if s in blob:
                self.cursor = idx
                return

    # --- rendu liste ---
    def draw_list(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        # en-tête
        flt = []
        if self.level_filter:
            flt.append("lvl:" + ",".join(sorted(self.level_filter,
                                                 key=lambda x: LEVEL_ORDER.get(x, 9))))
        if self.channel_filter:
            flt.append("chan:" + self.channel_filter)
        if self.search:
            flt.append("/" + self.search)
        header = f" logs: {len(self.filtered)}/{len(self.all)} events "
        if flt:
            header += "| " + " ".join(flt) + " "
        stdscr.attron(curses.A_REVERSE)
        stdscr.addnstr(0, 0, header.ljust(w), w)
        stdscr.attroff(curses.A_REVERSE)

        body = h - 2
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + body:
            self.top = self.cursor - body + 1

        for row in range(body):
            idx = self.top + row
            if idx >= len(self.filtered):
                break
            e = self.filtered[idx]
            lvl = (e.get("level") or "?")
            ts = fmt_ts(e.get("@timestamp"))
            chan = (e.get("channel") or "?")[:14]
            msg = (e.get("message") or "").replace("\n", " ")
            line = f"{ts}  {lvl:<8} {chan:<14} {msg}"
            y = row + 1
            attr = curses.color_pair(LEVEL_COLOR.get(lvl, 0))
            if idx == self.cursor:
                attr |= curses.A_REVERSE
            stdscr.addnstr(y, 0, line.ljust(w), w, attr)

        footer = " ↑↓ naviguer  ↵ détail  / rechercher  1-5 niveau  c channel  0 reset  q quitter "
        stdscr.attron(curses.A_DIM)
        stdscr.addnstr(h - 1, 0, footer.ljust(w), w)
        stdscr.attroff(curses.A_DIM)
        stdscr.noutrefresh()

    # --- rendu détail ---
    def draw_detail(self, stdscr):
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        e = self.detail
        stdscr.attron(curses.A_REVERSE)
        title = f" {e.get('level','?')} — {fmt_ts(e.get('@timestamp'))} — {e.get('channel','?')} "
        stdscr.addnstr(0, 0, title.ljust(w), w)
        stdscr.attroff(curses.A_REVERSE)

        lines = []
        lines.append(("message", e.get("message", "")))
        ctx = e.get("context") or {}
        if isinstance(ctx, dict):
            for key in ("class", "code", "type", "file"):
                if ctx.get(key):
                    lines.append((f"context.{key}", str(ctx[key])))
            trace = ctx.get("trace")
        else:
            trace = None
        lines.append(("file", f"{e.get('_file','?')}:{e.get('_line','?')}"))

        out = []
        for label, val in lines:
            for i, seg in enumerate(textwrap.wrap(val, w - 16) or [""]):
                prefix = f"{label:>13}  " if i == 0 else " " * 15
                out.append(prefix + seg)
        # trace
        if trace:
            out.append("")
            out.append("  --- stacktrace ---")
            for tl in str(trace).split("\n"):
                out.extend(textwrap.wrap(tl, w - 2, subsequent_indent="    ") or [""])
        # extra brut
        extra = e.get("extra")
        if extra:
            out.append("")
            out.append("  --- extra ---")
            for tl in json.dumps(extra, indent=2, ensure_ascii=False).split("\n"):
                out.append("  " + tl)

        body = h - 2
        top = getattr(self, "_detail_top", 0)
        top = max(0, min(top, max(0, len(out) - body)))
        self._detail_top = top
        for row in range(body):
            idx = top + row
            if idx >= len(out):
                break
            stdscr.addnstr(row + 1, 0, out[idx], w)
        foot = f" ↑↓ défiler  q/↵ retour   ({top+1}-{min(top+body,len(out))}/{len(out)}) "
        stdscr.attron(curses.A_DIM)
        stdscr.addnstr(h - 1, 0, foot.ljust(w), w)
        stdscr.attroff(curses.A_DIM)
        stdscr.noutrefresh()
        self._detail_lines = len(out)

    # --- saisie recherche ---
    def prompt(self, stdscr, label):
        h, w = stdscr.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        stdscr.addnstr(h - 1, 0, (label + " ").ljust(w), w, curses.A_REVERSE)
        stdscr.move(h - 1, len(label) + 1)
        stdscr.refresh()
        try:
            s = stdscr.getstr(h - 1, len(label) + 1, 200).decode("utf-8", "replace")
        except Exception:
            s = ""
        curses.noecho()
        curses.curs_set(0)
        return s.strip()

    # --- boucle principale ---
    def run(self, stdscr):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)

        while True:
            if self.detail is not None:
                self.draw_detail(stdscr)
            else:
                self.draw_list(stdscr)
            curses.doupdate()
            ch = stdscr.getch()

            if self.detail is not None:
                body = stdscr.getmaxyx()[0] - 2
                if ch in (ord("q"), 27, curses.KEY_ENTER, 10, 13):
                    self.detail = None
                    self._detail_top = 0
                elif ch in (curses.KEY_DOWN, ord("j")):
                    self._detail_top += 1
                elif ch in (curses.KEY_UP, ord("k")):
                    self._detail_top = max(0, self._detail_top - 1)
                elif ch == curses.KEY_NPAGE:
                    self._detail_top += body
                elif ch == curses.KEY_PPAGE:
                    self._detail_top = max(0, self._detail_top - body)
                continue

            body = stdscr.getmaxyx()[0] - 2
            if ch in (ord("q"), 27):
                break
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.cursor = min(len(self.filtered) - 1, self.cursor + 1)
            elif ch in (curses.KEY_UP, ord("k")):
                self.cursor = max(0, self.cursor - 1)
            elif ch == curses.KEY_NPAGE:
                self.cursor = min(len(self.filtered) - 1, self.cursor + body)
            elif ch == curses.KEY_PPAGE:
                self.cursor = max(0, self.cursor - body)
            elif ch == ord("g"):
                self.cursor = 0
            elif ch == ord("G"):
                self.cursor = max(0, len(self.filtered) - 1)
            elif ch in (curses.KEY_ENTER, 10, 13, ord(" ")):
                if self.filtered:
                    self.detail = self.filtered[self.cursor]
                    self._detail_top = 0
            elif ch == ord("/"):
                self.search = self.prompt(stdscr, "/")
                self.apply_filters()
            elif ch == ord("n"):
                self.find(1)
            elif ch == ord("N"):
                self.find(-1)
            elif ch in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
                mapping = {ord("1"): "DEBUG", ord("2"): "INFO",
                           ord("3"): "WARNING", ord("4"): "ERROR",
                           ord("5"): "CRITICAL"}
                lvl = mapping[ch]
                self.level_filter ^= {lvl}
                self.apply_filters()
            elif ch == ord("c"):
                if self.filtered:
                    cur = self.filtered[self.cursor].get("channel")
                    self.channel_filter = None if self.channel_filter == cur else cur
                    self.apply_filters()
            elif ch == ord("0"):
                self.level_filter = set()
                self.channel_filter = None
                self.search = ""
                self.apply_filters()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    events = load_files(sys.argv[1:])
    if not events:
        print("Aucun event trouvé.", file=sys.stderr)
        sys.exit(1)
    # tri chronologique
    events.sort(key=lambda e: e.get("@timestamp") or "")
    curses.wrapper(Viewer(events).run)


if __name__ == "__main__":
    main()
