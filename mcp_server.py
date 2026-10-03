"""MCP server for the Sky Log Debug Console (debugV4.py).

Gives Claude direct access to the same log sources the GUI watches: it reads
log_viewer_config.json (profiles + entries) and resolves the newest session
folder / log file exactly like the GUI does. Works with or without the GUI
running; the GUI is never modified or disturbed (read-only).

Run (stdio):  python mcp_server.py
"""
import json
import os
import re

from mcp.server.fastmcp import FastMCP

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("LOGVIEWER_CONFIG", os.path.join(SCRIPT_DIR, "log_viewer_config.json"))
DEFAULT_LOG = "script.log"
# Reforger writes "SCRIPT (E):" / "(W):"; the GUI colours the words ERROR/WARNING.
LEVEL_RX = {
    "ERROR": re.compile(r"ERROR|\(E\)", re.I),
    "WARNING": re.compile(r"WARNING|\(W\)", re.I),
    "INFO": re.compile(r"INFO", re.I),
}
MAX_RETURN_CHARS = 60000

mcp = FastMCP("livelog")


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"current_profile": None, "profiles": {}}
    if isinstance(data, dict) and "profiles" in data:
        return data
    return {"current_profile": None, "profiles": {}}


def _all_entries(profile=None):
    """Yield (profile_name, entry) pairs; the current profile comes first."""
    cfg = _load_config()
    profiles = cfg.get("profiles", {})
    order = list(profiles)
    cur = cfg.get("current_profile")
    if profile:
        order = [p for p in order if p.lower() == profile.lower()]
    elif cur in profiles:
        order.remove(cur)
        order.insert(0, cur)
    for p in order:
        for e in profiles[p].get("entries", []):
            yield p, e


def _resolve_base(source, profile=None):
    """source = entry name from the config, or a direct log base directory."""
    if source and os.path.isdir(source):
        return os.path.abspath(source), source
    wanted = (source or "").lower()
    entries = list(_all_entries(profile))
    for _, e in entries:
        if e.get("name", "").lower() == wanted:
            return e["path"], e["name"]
    names = ", ".join(sorted({e.get("name", "?") for _, e in entries})) or "(keine)"
    raise ValueError(f"Unbekannte Quelle '{source}'. Verfügbar: {names}")


def _session_dirs(base):
    if not os.path.isdir(base):
        return []
    dirs = [os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    return sorted(dirs, key=os.path.getmtime, reverse=True)


def _log_path(base, filename=DEFAULT_LOG, session=None):
    """Newest session's log file (same rule as the GUI), or a named session."""
    filename = os.path.basename(filename or DEFAULT_LOG)
    if session:
        d = os.path.join(base, os.path.basename(session))
        if not os.path.isdir(d):
            raise ValueError(f"Session '{session}' nicht gefunden in {base}")
    else:
        dirs = _session_dirs(base)
        if not dirs:
            raise ValueError(f"Keine Session-Ordner in {base}")
        d = dirs[0]
    p = os.path.join(d, filename)
    if not os.path.isfile(p):
        have = ", ".join(sorted(os.listdir(d)))
        raise ValueError(f"{filename} fehlt in {d}. Vorhanden: {have}")
    return p


def _read_lines(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().splitlines()


def _matcher(filter_key="", search="", level="", regex=False):
    f = filter_key.lower() if filter_key else ""
    lv = level.upper() if level else ""
    rx = re.compile(search, re.IGNORECASE) if (search and regex) else None
    s = search.lower() if (search and not regex) else ""

    def ok(line):
        if f and f not in line.lower():
            return False
        if lv and not (LEVEL_RX.get(lv) or re.compile(re.escape(lv))).search(line):
            return False
        if rx and not rx.search(line):
            return False
        if s and s not in line.lower():
            return False
        return True
    return ok


def _clip(text):
    if len(text) <= MAX_RETURN_CHARS:
        return text
    return "[... gekürzt, älterer Teil entfernt ...]\n" + text[-MAX_RETURN_CHARS:]


# ── tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_sources(profile: str = "") -> str:
    """List all log sources configured in the Log Debug Console (profiles and
    entries), with the newest session folder and size/mtime of its script.log."""
    cfg = _load_config()
    out = [f"Aktives Profil im Tool: {cfg.get('current_profile')}", ""]
    for p, e in _all_entries(profile or None):
        base = e["path"]
        info = "kein Log"
        try:
            lp = _log_path(base)
            st = os.stat(lp)
            info = f"{os.path.basename(os.path.dirname(lp))}/{DEFAULT_LOG} ({st.st_size} B)"
        except ValueError:
            pass
        out.append(f"[{p}] {e.get('name')}: {base}\n    neueste: {info}"
                   f" | filter='{e.get('filter', '')}' search='{e.get('search', '')}'"
                   f" paused={e.get('paused', False)}")
    return "\n".join(out)


@mcp.tool()
def list_sessions(source: str, limit: int = 10) -> str:
    """List the newest session folders (one per game start) of a source,
    with the log files each contains."""
    base, name = _resolve_base(source)
    lines = []
    for d in _session_dirs(base)[:limit]:
        files = ", ".join(f"{f} ({os.path.getsize(os.path.join(d, f))} B)"
                          for f in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, f)))
        lines.append(f"{os.path.basename(d)}: {files}")
    return "\n".join(lines) or "Keine Sessions."


@mcp.tool()
def read_log(source: str, lines: int = 200, filter_key: str = "", search: str = "",
             level: str = "", regex: bool = False, file: str = DEFAULT_LOG,
             session: str = "") -> str:
    """Read the last N lines (chronological order) of a source's newest log.

    source: entry name from list_sources (e.g. 'WB Server') or a log base directory.
    filter_key: keep only lines containing this text (like the GUI 'Key' field).
    search: keep only lines matching this text (or regex if regex=true).
    level: ERROR | WARNING | INFO to restrict to that severity.
    file: script.log (default), console.log or error.log.
    session: a specific session folder name instead of the newest."""
    base, _ = _resolve_base(source)
    path = _log_path(base, file, session or None)
    ok = _matcher(filter_key, search, level, regex)
    sel = [l for l in _read_lines(path) if ok(l)][-max(1, lines):]
    header = f"# {path} ({len(sel)} Zeilen)\n"
    return header + _clip("\n".join(sel))


@mcp.tool()
def get_errors(source: str, lines: int = 100, include_warnings: bool = True,
               file: str = DEFAULT_LOG, session: str = "") -> str:
    """Return the most recent ERROR (and optionally WARNING) lines of a source."""
    base, _ = _resolve_base(source)
    path = _log_path(base, file, session or None)
    rxs = [LEVEL_RX["ERROR"]] + ([LEVEL_RX["WARNING"]] if include_warnings else [])
    sel = [l for l in _read_lines(path) if any(rx.search(l) for rx in rxs)][-max(1, lines):]
    return f"# {path} ({len(sel)} Treffer)\n" + _clip("\n".join(sel))


@mcp.tool()
def search_log(source: str, pattern: str, regex: bool = False, context: int = 0,
               max_results: int = 50, file: str = DEFAULT_LOG, session: str = "") -> str:
    """Search the whole log of a source for a pattern, optionally with N lines
    of context around each hit. Returns the most recent max_results hits."""
    base, _ = _resolve_base(source)
    path = _log_path(base, file, session or None)
    all_lines = _read_lines(path)
    ok = _matcher(search=pattern, regex=regex)
    hits = [i for i, l in enumerate(all_lines) if ok(l)]
    total = len(hits)
    hits = hits[-max(1, max_results):]
    blocks = []
    for i in hits:
        a, b = max(0, i - context), min(len(all_lines), i + context + 1)
        blocks.append("\n".join(f"{n + 1}: {all_lines[n]}" for n in range(a, b)))
    sep = "\n--\n" if context else "\n"
    return f"# {path}: {total} Treffer, zeige {len(hits)}\n" + _clip(sep.join(blocks))


@mcp.tool()
def tail_since(source: str, cursor: str = "", file: str = DEFAULT_LOG,
               filter_key: str = "", level: str = "", max_lines: int = 500) -> str:
    """Incremental 'live' reading. Call without cursor first (returns the
    cursor positioned at the current end of the log, no lines), then repeatedly
    with the returned cursor to get only lines written since the last call.
    A new session (game restart) is detected and read from its start."""
    base, _ = _resolve_base(source)
    path = _log_path(base, file)
    size = os.path.getsize(path)
    pos = 0
    if not cursor:
        return json.dumps({"cursor": f"{path}|{size}", "lines": [], "note": "Cursor initialisiert"})
    cpath, _, cpos = cursor.rpartition("|")
    if cpath == path and cpos.isdigit() and int(cpos) <= size:
        pos = int(cpos)  # same file, not truncated; otherwise start from 0
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read()
    # only consume complete lines so a half-written line isn't split
    end = data.rfind(b"\n") + 1
    new = data[:end].decode("utf-8", errors="ignore").splitlines()
    ok = _matcher(filter_key=filter_key, level=level)
    new = [l for l in new if ok(l)]
    truncated = max(0, len(new) - max_lines)
    return json.dumps({
        "cursor": f"{path}|{pos + end}",
        "new_session": cpath != path,
        "dropped": truncated,
        "lines": new[-max_lines:],
    }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
