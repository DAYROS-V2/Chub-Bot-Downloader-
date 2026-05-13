"""
Chub AI Character Downloader

Focused on downloading public Chub character cards.

Examples:
  py chub_downloader.py single "https://chub.ai/characters/creator/slug" --format both
  py chub_downloader.py search "vampire" --pages 2 --format png
  py chub_downloader.py tag "Love,Human" --pages 2 --match all --format both
  py chub_downloader.py event "Gold Rush" --pages 2 --format png
  py chub_downloader.py creator "SomeCreator" --pages 3 --format both
  py chub_downloader.py preview "vampire"
"""

import argparse
import base64
import collections
import csv
import ctypes
import json
import os
import random
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from datetime import datetime

from playwright.sync_api import sync_playwright


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(SCRIPT_DIR, "character exports")
MANIFEST_FILE = os.path.join(SCRIPT_DIR, "downloads_manifest.csv")
CHUB_PROFILE = os.path.join(SCRIPT_DIR, "chrome_debug_profile")
LOG_FILE = os.path.join(SCRIPT_DIR, "downloader.log")

_LOG_SINK = None
_DASHBOARD = None
_VT_ENABLED = None

CHUB_HOME = "https://chub.ai/"
READ_BASE = "https://ro.chub.ai"
GATEWAY_BASE = "https://gateway.chub.ai"
AVATAR_BASE = "https://avatars.charhub.io/avatars"
DEFAULT_PUBLIC_TOKEN = "glpat-UZXEBupEVv2vMCdFDkfJ"
DEBUG_PORT = 9223
CDP_URL = f"http://127.0.0.1:{DEBUG_PORT}"

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 50
SORT_OPTIONS = {
    "user_default": {"label": "User Default", "api": "default"},
    "default": {"label": "User Default", "api": "default"},
    "downloads": {"label": "# Downloads", "api": "download_count"},
    "popularity": {"label": "Popularity", "api": "star_count"},
    "underrated": {"label": "Underrated", "api": "ai_rating"},
    "recent_hits": {"label": "Recent Hits", "api": "trending_downloads"},
    "trending": {"label": "Trending", "api": "trending"},
    "timeline": {"label": "Timeline", "api": "timeline"},
    "evergreen": {"label": "Evergreen Event", "api": "created_at", "topic": "Evergreen"},
    "latest": {"label": "Latest", "api": "created_at"},
    "random": {"label": "Random", "api": "random"},
    # Useful direct API sort names. These are kept for CLI compatibility.
    "created_at": {"label": "Creation Time", "api": "created_at"},
    "last_activity_at": {"label": "Update Time", "api": "last_activity_at"},
    "ai_rating": {"label": "AI Rating", "api": "ai_rating"},
    "trending_downloads": {"label": "Trending Downloads", "api": "trending_downloads"},
    "star_count": {"label": "Popularity", "api": "star_count"},
    "download_count": {"label": "Downloads", "api": "download_count"},
    "rating": {"label": "Rating", "api": "rating"},
    "n_tokens": {"label": "Token Count", "api": "n_tokens"},
}
PROMPT_SORT_KEYS = [
    "user_default",
    "latest",
    "downloads",
    "popularity",
    "underrated",
    "recent_hits",
    "trending",
    "timeline",
    "evergreen",
    "random",
]
CANONICAL_TAGS = {
    "nsfl": "NSFL",
    "nsfw": "NSFW",
    "sfw": "SFW",
    "oc": "OC",
}


def _emit(text):
    if _LOG_SINK is not None:
        _LOG_SINK.write_line(text)
    else:
        print(text)


def header(text):
    width = 58
    _emit("")
    _emit("=" * width)
    _emit(f"  {text}")
    _emit("=" * width)


def info(text):
    _emit(f"    {text}")


def success(text):
    _emit(f"    OK {text}")


def warn(text):
    _emit(f"    !! {text}")


def fail(text):
    _emit(f"    x  {text}")


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


def format_bytes(num):
    num = float(num or 0)
    if num < 1024:
        return f"{int(num)} B"
    if num < 1024 ** 2:
        return f"{num / 1024:.1f} KB"
    if num < 1024 ** 3:
        return f"{num / (1024 ** 2):.1f} MB"
    return f"{num / (1024 ** 3):.2f} GB"


def enable_vt_mode():
    """Best-effort: turn on ANSI escape support on Windows (Win10+). Not required
    by the dashboard anymore since we use the Win32 console API directly, but harmless."""
    global _VT_ENABLED
    if _VT_ENABLED is not None:
        return _VT_ENABLED
    if os.name != "nt":
        _VT_ENABLED = True
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetStdHandle.argtypes = [ctypes.c_int32]
        kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.GetConsoleMode.restype = ctypes.c_int
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetConsoleMode.restype = ctypes.c_int
        ok = False
        for std_handle in (-11, -12):
            handle = kernel32.GetStdHandle(std_handle)
            if not handle:
                continue
            mode = ctypes.c_uint32(0)
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                if std_handle == -11:
                    ok = True
        _VT_ENABLED = ok
        return ok
    except Exception:
        _VT_ENABLED = False
        return False


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _SMALL_RECT(ctypes.Structure):
    _fields_ = [
        ("Left", ctypes.c_short),
        ("Top", ctypes.c_short),
        ("Right", ctypes.c_short),
        ("Bottom", ctypes.c_short),
    ]


class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", _COORD),
        ("dwCursorPosition", _COORD),
        ("wAttributes", ctypes.c_ushort),
        ("srWindow", _SMALL_RECT),
        ("dwMaximumWindowSize", _COORD),
    ]


_K32 = None
_K32_INIT = False


def _kernel32_console():
    """Return a kernel32 binding with the console-API signatures wired up, or None."""
    global _K32, _K32_INIT
    if _K32_INIT:
        return _K32
    _K32_INIT = True
    if os.name != "nt":
        return None
    try:
        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = ctypes.c_void_p
        k.GetStdHandle.argtypes = [ctypes.c_int32]
        k.SetConsoleCursorPosition.restype = ctypes.c_int
        k.SetConsoleCursorPosition.argtypes = [ctypes.c_void_p, _COORD]
        k.GetConsoleScreenBufferInfo.restype = ctypes.c_int
        k.GetConsoleScreenBufferInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_CONSOLE_SCREEN_BUFFER_INFO),
        ]
        k.FillConsoleOutputCharacterW.restype = ctypes.c_int
        k.FillConsoleOutputCharacterW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar,
            ctypes.c_uint32,
            _COORD,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        k.FillConsoleOutputAttribute.restype = ctypes.c_int
        k.FillConsoleOutputAttribute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ushort,
            ctypes.c_uint32,
            _COORD,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        _K32 = k
        return k
    except Exception:
        return None


def _buffer_info():
    k = _kernel32_console()
    if k is None:
        return None
    try:
        handle = k.GetStdHandle(-11)
        if not handle:
            return None
        info = _CONSOLE_SCREEN_BUFFER_INFO()
        if not k.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return None
        return info
    except Exception:
        return None


def _move_cursor_window_top():
    """Move the cursor to (0, srWindow.Top), i.e. the top of the visible viewport,
    regardless of how far the scrollback has scrolled. Returns True on success."""
    k = _kernel32_console()
    info = _buffer_info()
    if k is None or info is None:
        return False
    try:
        handle = k.GetStdHandle(-11)
        return bool(k.SetConsoleCursorPosition(handle, _COORD(0, info.srWindow.Top)))
    except Exception:
        return False


def _clear_visible_window():
    """Fill the visible viewport with spaces and reset cursor to its top-left."""
    k = _kernel32_console()
    info = _buffer_info()
    if k is None or info is None:
        return False
    try:
        handle = k.GetStdHandle(-11)
        top = info.srWindow.Top
        bottom = info.srWindow.Bottom
        width = info.dwSize.X
        cells = (bottom - top + 1) * width
        coord = _COORD(0, top)
        written = ctypes.c_uint32(0)
        k.FillConsoleOutputCharacterW(handle, ctypes.c_wchar(" "), cells, coord, ctypes.byref(written))
        k.FillConsoleOutputAttribute(handle, info.wAttributes, cells, coord, ctypes.byref(written))
        k.SetConsoleCursorPosition(handle, coord)
        return True
    except Exception:
        return False


class FileLogSink:
    """Thread-safe append-only logger used while the dashboard renders the screen."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write(f"# Chub downloader log started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        except Exception:
            pass

    def write_line(self, text):
        with self.lock:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            except Exception:
                pass


class Dashboard:
    """Thread-safe counters for the live dashboard."""

    def __init__(self):
        self.lock = threading.Lock()
        self.start_time = time.time()
        self.files_saved = 0
        self.bytes_saved = 0
        self.cards_attempted = 0
        self.cards_skipped_exists = 0
        self.cards_skipped_dupes = 0
        self.cards_failed = 0
        self.recent_card_times = collections.deque()
        self.recent_byte_events = collections.deque()
        self.current_page = 0
        self.current_action = "Starting..."
        self.concurrency = 1
        self.target = ""
        self.mode = ""

    def _trim(self, now):
        cutoff = now - 60
        while self.recent_card_times and self.recent_card_times[0] < cutoff:
            self.recent_card_times.popleft()
        while self.recent_byte_events and self.recent_byte_events[0][0] < cutoff:
            self.recent_byte_events.popleft()

    def set_meta(self, target=None, concurrency=None, mode=None, page=None):
        with self.lock:
            if target is not None:
                self.target = str(target)[:80]
            if concurrency:
                self.concurrency = int(concurrency)
            if mode is not None:
                self.mode = str(mode)
            if page is not None:
                self.current_page = int(page)

    def update_action(self, text):
        with self.lock:
            self.current_action = str(text)[:80]

    def record_file(self, byte_count):
        with self.lock:
            now = time.time()
            self.files_saved += 1
            self.bytes_saved += int(byte_count or 0)
            self.recent_byte_events.append((now, int(byte_count or 0)))
            self._trim(now)

    def record_card(self, kind):
        with self.lock:
            now = time.time()
            if kind == "attempted":
                self.cards_attempted += 1
                self.recent_card_times.append(now)
            elif kind == "skipped_exists":
                self.cards_skipped_exists += 1
            elif kind == "skipped_dupe":
                self.cards_skipped_dupes += 1
            elif kind == "failed":
                self.cards_failed += 1
            self._trim(now)

    def snapshot(self):
        with self.lock:
            now = time.time()
            elapsed = max(0.1, now - self.start_time)
            self._trim(now)
            recent_cards = len(self.recent_card_times)
            recent_bytes = sum(b for _, b in self.recent_byte_events)
            return {
                "elapsed": elapsed,
                "files_saved": self.files_saved,
                "bytes_saved": self.bytes_saved,
                "cards_attempted": self.cards_attempted,
                "cards_skipped_exists": self.cards_skipped_exists,
                "cards_skipped_dupes": self.cards_skipped_dupes,
                "cards_failed": self.cards_failed,
                "cards_per_min_recent": recent_cards,
                "cards_per_sec_recent": recent_cards / 60.0,
                "bytes_per_sec_recent": recent_bytes / 60.0,
                "cards_per_min_overall": self.cards_attempted / elapsed * 60,
                "bytes_per_sec_overall": self.bytes_saved / elapsed,
                "current_page": self.current_page,
                "current_action": self.current_action,
                "concurrency": self.concurrency,
                "target": self.target,
                "mode": self.mode,
            }


def _clear_console():
    try:
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")
    except Exception:
        pass


_DASHBOARD_WIDTH = 78
_DASHBOARD_HEIGHT = 22


def _build_dashboard_lines(snap, log_path):
    bar = "=" * _DASHBOARD_WIDTH
    elapsed_seconds = int(snap["elapsed"])
    mode_text = (snap["mode"] or "-")[:30]
    target_text = (snap["target"] or "-")[:30]
    action_text = (snap["current_action"] or "-")[:_DASHBOARD_WIDTH - 12]
    log_text = str(log_path)
    if len(log_text) > _DASHBOARD_WIDTH - 9:
        log_text = "..." + log_text[-(_DASHBOARD_WIDTH - 12):]

    lines = [
        bar,
        "  Chub Downloader - Live Dashboard           (Ctrl+C in this window to stop)",
        bar,
        "",
        f"  Mode     : {mode_text:<30}  Target  : {target_text}",
        f"  Page     : {str(snap['current_page']):<30}  Batch   : {snap['concurrency']} cards / batch",
        f"  Elapsed  : {format_duration(snap['elapsed']):<30}  Seconds : {elapsed_seconds}",
        f"  Action   : {action_text}",
        "",
        f"  Files saved      : {snap['files_saved']:<10}  Total bytes : {format_bytes(snap['bytes_saved'])}",
        f"  Cards attempted  : {snap['cards_attempted']:<10}  Skipped (exists) : {snap['cards_skipped_exists']}",
        f"  Failed           : {snap['cards_failed']:<10}  Skipped (dupes)  : {snap['cards_skipped_dupes']}",
        "",
        f"  Recent (60s) : {snap['cards_per_min_recent']:6.1f} cards/min   {snap['cards_per_sec_recent']:.2f} cards/sec   {format_bytes(snap['bytes_per_sec_recent'])}/sec",
        f"  Overall      : {snap['cards_per_min_overall']:6.1f} cards/min   {format_bytes(snap['bytes_per_sec_overall'])}/sec average",
        "",
        f"  Log file : {log_text}",
        "             (live mirror in the 2nd console window)",
        "",
        bar,
        "",
        "",
    ]
    # Force fixed height so nothing left over from a wider render leaks through.
    while len(lines) < _DASHBOARD_HEIGHT:
        lines.append("")
    return lines[:_DASHBOARD_HEIGHT]


def _paint_frame(padded_lines):
    """Write the frame, padded to a fixed width, in a single flush."""
    sys.stdout.write("\n".join(padded_lines))
    sys.stdout.flush()


def render_dashboard_loop(dashboard, stop_event, log_path, refresh=0.5):
    enable_vt_mode()  # harmless if not supported; primary path is Win32
    have_win32 = _clear_visible_window()
    if not have_win32:
        _clear_console()

    while not stop_event.is_set():
        snap = dashboard.snapshot()
        lines = _build_dashboard_lines(snap, log_path)
        padded = [line[:_DASHBOARD_WIDTH].ljust(_DASHBOARD_WIDTH) for line in lines]

        # Pin the cursor to the top of the visible viewport every frame so the
        # dashboard repaints in place, even if Chrome-launch output scrolled
        # the buffer beforehand. Fall back to cls if the API call ever fails.
        if have_win32 and not _move_cursor_window_top():
            have_win32 = False
        if not have_win32:
            _clear_console()

        _paint_frame(padded)
        stop_event.wait(refresh)


def open_log_window(log_path):
    """Spawn a second console window that tails the log file. Returns the Popen or None."""
    if os.name != "nt":
        return None
    try:
        escaped = log_path.replace("'", "''")
        ps_command = (
            "$Host.UI.RawUI.WindowTitle = 'Chub Downloader - Debug Log'; "
            f"Write-Host 'Tailing: {escaped}' -ForegroundColor Cyan; "
            "Write-Host 'This window mirrors per-card download activity. Close any time.' -ForegroundColor DarkGray; "
            "Write-Host ''; "
            f"Get-Content -LiteralPath '{escaped}' -Wait -Tail 500"
        )
        return subprocess.Popen(
            ["powershell.exe", "-NoExit", "-Command", ps_command],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except Exception:
        return None


def print_final_summary(dashboard):
    snap = dashboard.snapshot()
    bar = "=" * 64
    print(bar)
    print("  Chub Downloader - Final Summary")
    print(bar)
    if snap["mode"]:
        print(f"  Mode             : {snap['mode']}")
    if snap["target"]:
        print(f"  Target           : {snap['target']}")
    print(f"  Elapsed          : {format_duration(snap['elapsed'])}")
    print(f"  Files saved      : {snap['files_saved']}")
    print(f"  Bytes saved      : {format_bytes(snap['bytes_saved'])}")
    print(f"  Cards attempted  : {snap['cards_attempted']}")
    print(f"  Skipped (exists) : {snap['cards_skipped_exists']}")
    print(f"  Skipped (dupes)  : {snap['cards_skipped_dupes']}")
    print(f"  Failed           : {snap['cards_failed']}")
    if snap["elapsed"] > 0:
        print(f"  Avg rate         : {snap['cards_per_min_overall']:.1f} cards/min")
        print(f"  Avg bandwidth    : {format_bytes(snap['bytes_per_sec_overall'])}/sec")
    print(bar)


def find_chrome():
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
                     "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def launch_chrome(start_url=CHUB_HOME):
    if is_port_open(DEBUG_PORT):
        success(f"Chrome debug port {DEBUG_PORT} already open")
        return True

    chrome_path = os.environ.get("CHROME_PATH", find_chrome())
    if not chrome_path:
        fail("Could not find Chrome.")
        info("Set CHROME_PATH if Chrome is installed somewhere unusual.")
        return False

    info("Launching real Chrome...")
    info(f"Path: {chrome_path}")
    info(f"Profile: {CHUB_PROFILE}")

    cmd = [
        chrome_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={CHUB_PROFILE}",
        start_url,
    ]
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    print("    Waiting for Chrome", end="", flush=True)
    for _ in range(30):
        time.sleep(1)
        print(".", end="", flush=True)
        if is_port_open(DEBUG_PORT):
            print(" ready!")
            return True

    print(" timeout!")
    fail("Chrome started but the debug port never opened.")
    return False


def text_value(value):
    return value.strip() if isinstance(value, str) else ""


def sanitize_filename(value, fallback="character-card", max_length=140):
    raw = text_value(value) or fallback
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or fallback


def creator_and_slug(full_path):
    full_path = text_value(full_path).strip("/")
    if "/" in full_path:
        creator, slug = full_path.split("/", 1)
    else:
        creator, slug = "unknown", full_path or "character"
    return sanitize_filename(creator, "unknown"), sanitize_filename(slug, "character")


def output_path_for(node, extension, branch="main", output_group=""):
    full_path = text_value(node.get("fullPath")) or text_value(node.get("full_path"))
    creator, slug = creator_and_slug(full_path)
    folder_name = sanitize_filename(output_group, "unknown") if output_group else creator
    folder = os.path.join(EXPORT_DIR, folder_name)
    filename = f"{sanitize_filename(branch, 'main')}_{slug}_spec_v2.{extension}"
    return os.path.join(folder, filename)


def discover_public_token():
    env_token = os.environ.get("CHUB_AUTH_TOKEN") or os.environ.get("CHUB_TOKEN")
    if env_token and len(env_token.strip()) > 3:
        return env_token.strip()

    try:
        html = urllib.request.urlopen(CHUB_HOME, timeout=20).read().decode("utf-8", errors="ignore")
        app_match = re.search(r'src="(/assets/App-[^"]+\.js)"', html)
        if app_match:
            js_url = urllib.parse.urljoin(CHUB_HOME, app_match.group(1))
            bundle = urllib.request.urlopen(js_url, timeout=30).read().decode("utf-8", errors="ignore")
            token_match = re.search(r'kYr="([^"]+)"', bundle)
            if token_match:
                return token_match.group(1)
    except Exception:
        pass

    return DEFAULT_PUBLIC_TOKEN


def parse_character_target(value):
    source = text_value(value).strip('"').strip("'")
    if not source:
        return ""

    if source.startswith("@"):
        return source[1:].strip("/")

    parsed = urllib.parse.urlparse(source)
    if parsed.netloc:
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if len(parts) >= 3 and parts[0].lower() == "characters":
            return "/".join(parts[1:3])
        if len(parts) >= 2:
            return "/".join(parts[-2:])
        return source.strip("/")

    source = source.replace("\\", "/").strip("/")
    if source.lower().startswith("characters/"):
        source = source.split("/", 1)[1]
    return source


def parse_creator(value):
    source = text_value(value).strip('"').strip("'")
    if not source:
        return ""
    if source.startswith("@"):
        return source[1:].strip("/")

    parsed = urllib.parse.urlparse(source)
    if parsed.netloc:
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0].lower() in ("users", "creators", "profile", "profiles"):
            return parts[1]
        if parts:
            return parts[-1]
    return source.strip("/")


def parse_tags(value):
    source = text_value(value).strip('"').strip("'")
    if not source:
        return []

    parsed_source = urllib.parse.urlparse(source)
    if parsed_source.netloc:
        query_topics = urllib.parse.parse_qs(parsed_source.query).get("topics")
        if query_topics:
            return parse_tags(",".join(query_topics))

    tags = []
    for raw_part in re.split(r"[,;]+", source):
        part = raw_part.strip().strip("/")
        if not part:
            continue

        parsed = urllib.parse.urlparse(part)
        if parsed.netloc:
            pieces = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
            if len(pieces) >= 2 and pieces[0].lower() == "tags":
                tags.append(pieces[1])
            elif pieces:
                tags.append(pieces[-1])
            continue

        if part.lower().startswith("tags/"):
            part = part.split("/", 1)[1]
        tags.append(urllib.parse.unquote(part).strip())

    seen = set()
    clean = []
    for tag in tags:
        if not tag:
            continue
        tag = CANONICAL_TAGS.get(tag.lower(), tag)
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(tag)
    return clean


def merge_topics(*values):
    tags = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                tags.extend(parse_tags(str(item)))
        else:
            tags.extend(parse_tags(str(value or "")))

    seen = set()
    merged = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    return ",".join(merged)


def resolve_sort(sort_key, username_filtered=False):
    if username_filtered and sort_key == "trending":
        return "n_favorites"
    if username_filtered and sort_key == "recent_hits":
        return "download_count"
    if username_filtered and sort_key == "timeline":
        return "created_at"
    selected = SORT_OPTIONS.get(sort_key, SORT_OPTIONS["latest"])
    return selected.get("api", "created_at")


def extra_topic_for_sort(sort_key):
    selected = SORT_OPTIONS.get(sort_key) or {}
    return selected.get("topic", "")


def tag_folder_name(topic_string):
    tags = parse_tags(topic_string)
    if not tags:
        return ""
    return " + ".join(tags)


def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "", text_value(value).lower())


def character_full_path(node):
    return text_value(node.get("fullPath")) or text_value(node.get("full_path"))


def character_identity(node):
    for key in ("project_uuid", "id", "projectId", "project_id"):
        value = node.get(key)
        if value not in (None, ""):
            return f"id:{value}"
    full_path = character_full_path(node)
    return f"path:{full_path.lower()}" if full_path else ""


def character_series_key(node):
    full_path = character_full_path(node)
    creator = ""
    slug = ""
    if "/" in full_path:
        creator, slug = full_path.split("/", 1)

    if creator and slug:
        base_slug = re.sub(r"[-_][0-9a-f]{8,}$", "", slug.lower())
        base_slug = re.sub(r"[-_][0-9a-f-]{32,}$", "", base_slug)
        return f"series:{creator.lower()}:{normalized_key(base_slug)}"

    name_key = normalized_key(node.get("name"))
    if creator and name_key:
        return f"series:{creator.lower()}:{name_key}"

    identity = character_identity(node)
    return f"series:{identity}" if identity else ""


def character_seen_keys(node):
    return {key for key in (character_identity(node), character_series_key(node)) if key}


def load_seen_character_keys():
    seen = set()
    if not os.path.exists(MANIFEST_FILE):
        return seen
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                node = {
                    "id": row.get("project_id", ""),
                    "name": row.get("name", ""),
                    "fullPath": row.get("full_path", ""),
                }
                seen.update(character_seen_keys(node))
    except Exception as exc:
        warn(f"Could not read manifest for duplicate checks: {exc}")
    return seen


def browser_json_request(page, url, token, method="GET", body=None):
    result = page.evaluate(
        """
        async ({ url, token, method, body }) => {
            const stored = window.localStorage.getItem("URQL_TOKEN");
            const authToken = stored && stored.length > 3 ? stored : token;
            const headers = {
                "Accept": "application/json, text/plain, */*",
                "samwise": authToken,
                "CH-API-KEY": authToken,
                "private-token": authToken,
            };
            if (body !== null && body !== undefined) {
                headers["Content-Type"] = "application/json";
            }
            try {
                const resp = await fetch(url, {
                    method,
                    credentials: "omit",
                    headers,
                    body: body !== null && body !== undefined ? JSON.stringify(body) : undefined,
                });
                const text = await resp.text();
                let data = null;
                try { data = JSON.parse(text); } catch (e) {}
                return {
                    ok: resp.ok,
                    status: resp.status,
                    data,
                    text: text.slice(0, 1000),
                    contentType: resp.headers.get("content-type") || "",
                };
            } catch (e) {
                return { ok: false, status: 0, data: null, text: e.message || String(e), contentType: "" };
            }
        }
        """,
        {"url": url, "token": token, "method": method, "body": body},
    )
    return result


def browser_binary_request(page, url):
    result = page.evaluate(
        """
        async (url) => {
            const blobToBase64 = (blob) => new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => {
                    const value = String(reader.result || "");
                    resolve(value.includes(",") ? value.split(",").pop() : value);
                };
                reader.onerror = () => reject(new Error("Failed to read blob."));
                reader.readAsDataURL(blob);
            });

            try {
                const resp = await fetch(url, { credentials: "omit" });
                if (!resp.ok) {
                    return { ok: false, status: resp.status, contentType: resp.headers.get("content-type") || "", text: await resp.text().catch(() => "") };
                }
                const blob = await resp.blob();
                return {
                    ok: true,
                    status: resp.status,
                    contentType: blob.type || resp.headers.get("content-type") || "",
                    base64: await blobToBase64(blob),
                };
            } catch (e) {
                return { ok: false, status: 0, contentType: "", text: e.message || String(e) };
            }
        }
        """,
        url,
    )
    if not result.get("ok"):
        raise RuntimeError(f"Binary fetch failed: HTTP {result.get('status', '?')} {result.get('text', '')[:200]}")
    return base64.b64decode(result.get("base64", "")), result.get("contentType", "")


def browser_image_as_png(page, url):
    result = page.evaluate(
        """
        async (url) => {
            const blobToBase64 = (blob) => new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => {
                    const value = String(reader.result || "");
                    resolve(value.includes(",") ? value.split(",").pop() : value);
                };
                reader.onerror = () => reject(new Error("Failed to read blob."));
                reader.readAsDataURL(blob);
            });

            try {
                const resp = await fetch(url, { credentials: "omit" });
                if (!resp.ok) {
                    return { ok: false, status: resp.status, text: await resp.text().catch(() => "") };
                }
                const sourceBlob = await resp.blob();
                if (sourceBlob.type === "image/png") {
                    return { ok: true, base64: await blobToBase64(sourceBlob) };
                }
                const bitmap = await createImageBitmap(sourceBlob);
                const canvas = document.createElement("canvas");
                canvas.width = bitmap.width;
                canvas.height = bitmap.height;
                canvas.getContext("2d").drawImage(bitmap, 0, 0);
                bitmap.close();
                const pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
                if (!pngBlob) {
                    return { ok: false, status: 0, text: "Canvas conversion failed." };
                }
                return { ok: true, base64: await blobToBase64(pngBlob) };
            } catch (e) {
                return { ok: false, status: 0, text: e.message || String(e) };
            }
        }
        """,
        url,
    )
    if not result.get("ok"):
        raise RuntimeError(f"Image fetch failed: HTTP {result.get('status', '?')} {result.get('text', '')[:200]}")
    return base64.b64decode(result.get("base64", ""))


def browser_batch_card_downloads(page, token, cards, branch="main"):
    """Fetch card.json + PNG/avatar for many cards in parallel inside the browser.

    Each card dict needs: key, project_id, png_url, avatar_url, need_card_json, need_png.
    Returns a parallel list with: card_json/card_source, png_base64/png_source,
    avatar_png_base64, plus error fields when something failed.
    """
    payload = {
        "token": token,
        "branch": branch,
        "gatewayBase": GATEWAY_BASE,
        "cards": cards,
    }
    return page.evaluate(
        """
        async ({ token, branch, gatewayBase, cards }) => {
            const stored = window.localStorage.getItem("URQL_TOKEN");
            const authToken = stored && stored.length > 3 ? stored : token;
            const headers = {
                "Accept": "application/json, text/plain, */*",
                "samwise": authToken,
                "CH-API-KEY": authToken,
                "private-token": authToken,
            };

            const blobToBase64 = (blob) => new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => {
                    const value = String(reader.result || "");
                    resolve(value.includes(",") ? value.split(",").pop() : value);
                };
                reader.onerror = () => reject(new Error("Failed to read blob."));
                reader.readAsDataURL(blob);
            });

            const fetchJSON = async (url) => {
                try {
                    const resp = await fetch(url, { credentials: "omit", headers });
                    const text = await resp.text();
                    let data = null;
                    try { data = JSON.parse(text); } catch (e) {}
                    return { ok: resp.ok, status: resp.status, data, text: text.slice(0, 300) };
                } catch (e) {
                    return { ok: false, status: 0, text: e.message || String(e) };
                }
            };

            const fetchPng = async (url) => {
                try {
                    const resp = await fetch(url, { credentials: "omit" });
                    if (!resp.ok) {
                        return { ok: false, status: resp.status, text: await resp.text().catch(() => "") };
                    }
                    const blob = await resp.blob();
                    return { ok: true, base64: await blobToBase64(blob), contentType: blob.type };
                } catch (e) {
                    return { ok: false, status: 0, text: e.message || String(e) };
                }
            };

            const fetchAvatarAsPng = async (url) => {
                try {
                    const resp = await fetch(url, { credentials: "omit" });
                    if (!resp.ok) {
                        return { ok: false, status: resp.status, text: await resp.text().catch(() => "") };
                    }
                    const sourceBlob = await resp.blob();
                    if (sourceBlob.type === "image/png") {
                        return { ok: true, base64: await blobToBase64(sourceBlob) };
                    }
                    const bitmap = await createImageBitmap(sourceBlob);
                    const canvas = document.createElement("canvas");
                    canvas.width = bitmap.width;
                    canvas.height = bitmap.height;
                    canvas.getContext("2d").drawImage(bitmap, 0, 0);
                    bitmap.close();
                    const pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
                    if (!pngBlob) return { ok: false, status: 0, text: "Canvas conversion failed." };
                    return { ok: true, base64: await blobToBase64(pngBlob) };
                } catch (e) {
                    return { ok: false, status: 0, text: e.message || String(e) };
                }
            };

            const processCard = async (card) => {
                const out = { key: card.key };
                const tasks = [];
                if (card.need_card_json && card.project_id) {
                    const cardUrl = `${gatewayBase}/api/v4/projects/${card.project_id}/repository/files/card.json/raw?ref=${encodeURIComponent(branch)}&response_type=blob`;
                    tasks.push(fetchJSON(cardUrl).then((r) => {
                        if (r.ok && r.data && typeof r.data === "object") {
                            out.card_json = r.data;
                            out.card_source = "repository";
                        } else {
                            out.card_json_status = r.status;
                            out.card_json_error = r.text;
                        }
                    }));
                }
                if (card.need_png && card.png_url) {
                    tasks.push(fetchPng(card.png_url).then((r) => {
                        if (r.ok) {
                            out.png_base64 = r.base64;
                            out.png_source = "chara_card_v2.png";
                        } else {
                            out.png_status = r.status;
                            out.png_error = r.text;
                        }
                    }));
                }
                if (card.need_png && card.avatar_url) {
                    tasks.push(fetchAvatarAsPng(card.avatar_url).then((r) => {
                        if (r.ok) {
                            out.avatar_png_base64 = r.base64;
                        } else {
                            out.avatar_status = r.status;
                            out.avatar_error = r.text;
                        }
                    }));
                }
                try {
                    await Promise.all(tasks);
                } catch (e) {
                    out.batch_error = (e && e.message) ? e.message : String(e);
                }
                return out;
            };

            return await Promise.all(cards.map(processCard));
        }
        """,
        payload,
    )


def make_png_chunk(chunk_type, data):
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def embed_card_data_in_png(png_bytes, card_json_text):
    signature = b"\x89PNG\r\n\x1a\n"
    if not png_bytes.startswith(signature):
        raise ValueError("Image conversion did not return a valid PNG")

    chunks = []
    offset = len(signature)
    while offset + 8 <= len(png_bytes):
        length = struct.unpack(">I", png_bytes[offset:offset + 4])[0]
        chunk_type = png_bytes[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        data = png_bytes[data_start:data_end]
        if crc_end > len(png_bytes):
            raise ValueError("PNG ended before the final chunk")

        keep = True
        if chunk_type == b"tEXt":
            keyword = data.split(b"\x00", 1)[0].decode("latin-1", errors="ignore").lower()
            keep = keyword not in ("chara", "ccv3")
        if keep:
            chunks.append((chunk_type, data))

        offset = crc_end
        if chunk_type == b"IEND":
            break

    encoded_card = base64.b64encode(card_json_text.encode("utf-8"))
    text_chunk = make_png_chunk(b"tEXt", b"chara\x00" + encoded_card)

    output = bytearray(signature)
    inserted = False
    for chunk_type, data in chunks:
        if chunk_type == b"IEND" and not inserted:
            output.extend(text_chunk)
            inserted = True
        output.extend(make_png_chunk(chunk_type, data))

    if not inserted:
        output.extend(text_chunk)
        output.extend(make_png_chunk(b"IEND", b""))

    return bytes(output)


def unwrap_character_response(data):
    if isinstance(data, dict):
        if isinstance(data.get("node"), dict):
            return data["node"]
        if isinstance(data.get("data"), dict):
            return data["data"]
        if isinstance(data.get("character"), dict):
            return data["character"]
    return {}


def fetch_character(page, token, target):
    character_path = parse_character_target(target)
    if not character_path:
        raise ValueError("Missing character URL/path")

    api_path = urllib.parse.quote(character_path, safe="/")
    url = f"{READ_BASE}/api/characters/{api_path}?full=true&nocache={random.random()}"
    result = browser_json_request(page, url, token)
    if not result.get("ok"):
        raise RuntimeError(f"Character fetch failed: HTTP {result.get('status', '?')} {result.get('text', '')[:250]}")

    node = unwrap_character_response(result.get("data"))
    if not node:
        raise RuntimeError("Character API returned no usable character object")
    return node


def fetch_card_json(page, token, node, branch="main"):
    project_id = node.get("id")
    if project_id:
        url = (
            f"{GATEWAY_BASE}/api/v4/projects/{project_id}/repository/files/card.json/raw"
            f"?ref={urllib.parse.quote(branch)}&response_type=blob"
        )
        result = browser_json_request(page, url, token)
        if result.get("ok") and isinstance(result.get("data"), dict):
            return result["data"], "repository"

    return build_fallback_card_json(node), "api-definition"


def build_fallback_card_json(node):
    definition = node.get("definition") if isinstance(node.get("definition"), dict) else {}
    full_path = text_value(node.get("fullPath")) or text_value(definition.get("full_path"))
    creator, _ = creator_and_slug(full_path)
    first_mes = text_value(definition.get("first_message"))
    alt = definition.get("alternate_greetings") if isinstance(definition.get("alternate_greetings"), list) else []

    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": text_value(definition.get("name")) or text_value(node.get("name")) or "Unknown",
            "description": text_value(definition.get("description")) or text_value(definition.get("personality")),
            "personality": text_value(definition.get("personality")),
            "scenario": text_value(definition.get("scenario")),
            "first_mes": first_mes,
            "mes_example": text_value(definition.get("example_dialogs")),
            "creator_notes": text_value(node.get("description")) or text_value(node.get("tagline")),
            "system_prompt": text_value(definition.get("system_prompt")),
            "post_history_instructions": text_value(definition.get("post_history_instructions")),
            "alternate_greetings": [str(item) for item in alt if str(item).strip()],
            "tags": node.get("topics") if isinstance(node.get("topics"), list) else [],
            "creator": creator,
            "character_version": f"https://chub.ai/characters/{full_path}" if full_path else "",
            "extensions": definition.get("extensions") if isinstance(definition.get("extensions"), dict) else {},
        },
    }


def save_bytes(path, data, overwrite=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not overwrite:
        warn(f"Exists, skipping {os.path.basename(path)}")
        return path, False
    with open(path, "wb") as f:
        f.write(data)
    if _DASHBOARD is not None:
        _DASHBOARD.record_file(len(data))
    return path, True


def save_text(path, text, overwrite=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and not overwrite:
        warn(f"Exists, skipping {os.path.basename(path)}")
        return path, False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
    if _DASHBOARD is not None:
        _DASHBOARD.record_file(len(text.encode("utf-8")))
    return path, True


def append_manifest(node, export_format, output_path, source):
    exists = os.path.exists(MANIFEST_FILE)
    with open(MANIFEST_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "downloaded_at",
                "format",
                "name",
                "full_path",
                "project_id",
                "source",
                "output_path",
            ],
        )
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "downloaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "format": export_format,
                "name": text_value(node.get("name")),
                "full_path": text_value(node.get("fullPath")) or text_value(node.get("full_path")),
                "project_id": node.get("id", ""),
                "source": source,
                "output_path": output_path,
            }
        )


def card_png_url(node):
    for key in ("max_res_url", "avatar_url", "avatar"):
        value = text_value(node.get(key))
        if value and value.endswith("chara_card_v2.png"):
            return value
    avatar_url = text_value(node.get("avatar_url")) or text_value(node.get("avatar"))
    if avatar_url:
        return avatar_url.replace("avatar.webp", "chara_card_v2.png")
    full_path = text_value(node.get("fullPath")) or text_value(node.get("full_path"))
    return f"{AVATAR_BASE}/{full_path}/chara_card_v2.png" if full_path else ""


def avatar_url(node):
    value = text_value(node.get("avatar_url")) or text_value(node.get("avatar"))
    if value:
        return value
    full_path = text_value(node.get("fullPath")) or text_value(node.get("full_path"))
    return f"{AVATAR_BASE}/{full_path}/avatar.webp" if full_path else ""


def download_character(
    page,
    token,
    target=None,
    known_node=None,
    export_format="png",
    branch="main",
    overwrite=False,
    output_group="",
):
    node = known_node or fetch_character(page, token, target)
    name = text_value(node.get("name")) or text_value(node.get("fullPath")) or str(node.get("id", "character"))
    full_path = text_value(node.get("fullPath")) or text_value(node.get("full_path"))
    info(f"Downloading {name} ({full_path})")

    saved = []
    card_json = None
    card_source = ""
    formats = ["png", "json"] if export_format == "both" else [export_format]

    if "json" in formats or "png" in formats:
        card_json, card_source = fetch_card_json(page, token, node, branch)

    if "json" in formats:
        json_path = output_path_for(node, "json", branch, output_group=output_group)
        json_text = json.dumps(card_json, indent=2, ensure_ascii=False)
        path, wrote = save_text(json_path, json_text, overwrite=overwrite)
        if wrote:
            success(f"Saved {os.path.basename(path)}")
            append_manifest(node, "json", path, card_source)
        saved.append(path)

    if "png" in formats:
        png_path = output_path_for(node, "png", branch, output_group=output_group)
        png_url = card_png_url(node)
        png_bytes = None
        source = "chara_card_v2.png"
        if png_url:
            try:
                png_bytes, _ = browser_binary_request(page, png_url)
            except Exception as exc:
                warn(f"Card PNG fetch failed, rebuilding from avatar: {exc}")

        if not png_bytes:
            source = "avatar+json"
            source_avatar = avatar_url(node)
            if not source_avatar:
                raise RuntimeError("No card PNG or avatar URL available for PNG export")
            avatar_png = browser_image_as_png(page, source_avatar)
            png_bytes = embed_card_data_in_png(avatar_png, json.dumps(card_json, ensure_ascii=False))

        path, wrote = save_bytes(png_path, png_bytes, overwrite=overwrite)
        if wrote:
            success(f"Saved {os.path.basename(path)}")
            append_manifest(node, "png", path, source)
        saved.append(path)

    return saved


def download_node_batch(
    page,
    token,
    nodes,
    export_format="png",
    branch="main",
    overwrite=False,
    output_group="",
):
    """Download many character cards in parallel via a single browser batch call.

    Returns (saved_files, attempted, skipped_existing, failed).
    """
    formats = ["png", "json"] if export_format == "both" else [export_format]
    want_json = "json" in formats
    want_png = "png" in formats

    pending = []
    skipped_existing = 0

    for node in nodes:
        json_path = output_path_for(node, "json", branch, output_group=output_group) if want_json else None
        png_path = output_path_for(node, "png", branch, output_group=output_group) if want_png else None

        need_json_file = want_json and (overwrite or not os.path.exists(json_path))
        need_png_file = want_png and (overwrite or not os.path.exists(png_path))

        if not need_json_file and not need_png_file:
            skipped_existing += 1
            if _DASHBOARD is not None:
                _DASHBOARD.record_card("skipped_exists")
            full_path = character_full_path(node) or "character"
            info(f"  - already saved: {full_path}")
            continue

        png_url_value = card_png_url(node) if need_png_file else ""
        av_url_value = avatar_url(node) if need_png_file else ""
        project_id = node.get("id")
        project_id_str = str(project_id) if project_id not in (None, "", 0) else ""

        pending.append({
            "node": node,
            "json_path": json_path,
            "png_path": png_path,
            "need_json_file": need_json_file,
            "need_png_file": need_png_file,
            "card_request": {
                "key": project_id_str or character_full_path(node) or f"idx{len(pending)}",
                "project_id": project_id_str,
                "png_url": png_url_value,
                "avatar_url": av_url_value,
                "need_card_json": need_json_file or need_png_file,
                "need_png": need_png_file,
            },
        })

    if not pending:
        return 0, 0, skipped_existing, 0

    requests = [item["card_request"] for item in pending]
    try:
        results = browser_batch_card_downloads(page, token, requests, branch=branch)
        if not isinstance(results, list) or len(results) != len(requests):
            raise RuntimeError("Browser batch returned an unexpected payload")
    except Exception as exc:
        warn(f"Batch fetch failed ({exc}); retrying cards sequentially.")
        results = None

    saved_files = 0
    failed = 0

    if results is None:
        for item in pending:
            node = item["node"]
            if _DASHBOARD is not None:
                _DASHBOARD.record_card("attempted")
            try:
                saved = download_character(
                    page,
                    token,
                    known_node=node,
                    export_format=export_format,
                    branch=branch,
                    overwrite=overwrite,
                    output_group=output_group,
                )
                if saved:
                    saved_files += len(saved)
            except Exception as exc:
                failed += 1
                if _DASHBOARD is not None:
                    _DASHBOARD.record_card("failed")
                fail(f"{character_full_path(node) or node.get('name', 'character')}: {exc}")
        return saved_files, len(pending), skipped_existing, failed

    for item, res in zip(pending, results):
        node = item["node"]
        name = text_value(node.get("name")) or character_full_path(node) or str(node.get("id", "character"))
        if _DASHBOARD is not None:
            _DASHBOARD.record_card("attempted")
            _DASHBOARD.update_action(f"Writing {name}")
        if not isinstance(res, dict):
            res = {}

        card_json = res.get("card_json")
        card_source = res.get("card_source", "")
        if (item["need_json_file"] or item["need_png_file"]) and not card_json:
            card_json = build_fallback_card_json(node)
            card_source = "api-definition"

        try:
            if item["need_json_file"]:
                text = json.dumps(card_json, indent=2, ensure_ascii=False)
                path, wrote = save_text(item["json_path"], text, overwrite=overwrite)
                if wrote:
                    success(f"Saved {os.path.basename(path)}")
                    append_manifest(node, "json", path, card_source)
                    saved_files += 1

            if item["need_png_file"]:
                png_bytes = None
                png_source = "chara_card_v2.png"
                if res.get("png_base64"):
                    try:
                        png_bytes = base64.b64decode(res["png_base64"])
                    except Exception:
                        png_bytes = None
                if not png_bytes and res.get("avatar_png_base64"):
                    try:
                        avatar_png = base64.b64decode(res["avatar_png_base64"])
                        png_bytes = embed_card_data_in_png(avatar_png, json.dumps(card_json, ensure_ascii=False))
                        png_source = "avatar+json"
                    except Exception as exc:
                        warn(f"Avatar conversion failed for {name}: {exc}")

                if not png_bytes:
                    err = res.get("png_error") or res.get("avatar_error") or "no PNG source available"
                    raise RuntimeError(err)

                path, wrote = save_bytes(item["png_path"], png_bytes, overwrite=overwrite)
                if wrote:
                    success(f"Saved {os.path.basename(path)}")
                    append_manifest(node, "png", path, png_source)
                    saved_files += 1
        except Exception as exc:
            failed += 1
            if _DASHBOARD is not None:
                _DASHBOARD.record_card("failed")
            fail(f"{name}: {exc}")

    return saved_files, len(pending), skipped_existing, failed


def fetch_character_page(
    page,
    token,
    page_num,
    query="",
    username="",
    sort="latest",
    per_page=DEFAULT_PER_PAGE,
    topics="",
    inclusive_or=False,
    include_forks=False,
):
    per_page = max(1, min(int(per_page), MAX_PER_PAGE))
    topic_string = merge_topics(topics, extra_topic_for_sort(sort))
    api_sort = resolve_sort(sort, username_filtered=bool(username))
    if username and api_sort != resolve_sort(sort, username_filtered=False):
        info(f"Using Chub-compatible creator sort: {api_sort}")

    params = {
        "excludetopics": "",
        "first": per_page,
        "page": page_num,
        "namespace": "characters",
        "search": "",
        "include_forks": "true" if include_forks else "false",
        "nsfw": "true",
        "nsfw_only": "false",
        "require_custom_prompt": "false",
        "require_example_dialogues": "false",
        "require_images": "false",
        "require_expressions": "false",
        "nsfl": "true",
        "asc": "false",
        "min_ai_rating": "0",
        "min_tokens": "50",
        "max_tokens": "100000",
        "chub": "true",
        "count": "false",
        "require_lore": "false",
        "exclude_mine": "false",
        "require_lore_embedded": "false",
        "require_lore_linked": "false",
        "language": "",
        "sort": api_sort,
        "min_tags": "2",
        "inclusive_or": "true" if inclusive_or else "false",
        "recommended_verified": "false",
        "require_alternate_greetings": "false",
    }
    if query:
        params["search"] = query
    if username:
        params["username"] = username
    if topic_string:
        params["topics"] = topic_string

    url = f"{READ_BASE}/search?{urllib.parse.urlencode(params)}"
    info(f"Fetching page {page_num}...")
    result = browser_json_request(page, url, token, method="POST", body={})
    if not result.get("ok"):
        raise RuntimeError(f"Search failed: HTTP {result.get('status', '?')} {result.get('text', '')[:250]}")

    payload = result.get("data") or {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
    total = data.get("count")
    info(f"Found {len(nodes)} character(s) on page {page_num}")
    if not nodes and any(tag.lower() == "nsfl" for tag in parse_tags(topic_string)):
        warn("NSFL is account-gated on Chub. Log in with option 8 and enable the account content setting if this account returns empty pages.")
    return nodes, total


def search_characters(
    page,
    token,
    query="",
    username="",
    pages=1,
    sort="latest",
    per_page=DEFAULT_PER_PAGE,
    topics="",
    inclusive_or=False,
    include_forks=False,
):
    if int(pages) == -1:
        warn("Preview mode treats -1 as one page so it does not hold an infinite result list.")
        pages = 1

    per_page = max(1, min(int(per_page), MAX_PER_PAGE))
    all_nodes = []
    total = None
    for page_num in range(1, max(1, int(pages)) + 1):
        nodes, page_total = fetch_character_page(
            page,
            token,
            page_num,
            query=query,
            username=username,
            sort=sort,
            per_page=per_page,
            topics=topics,
            inclusive_or=inclusive_or,
            include_forks=include_forks,
        )
        if total is None:
            total = page_total
        all_nodes.extend(nodes)

        if len(nodes) < per_page:
            break
        time.sleep(random.uniform(0.15, 0.35))

    return all_nodes, total


def download_character_pages(
    page,
    token,
    query="",
    username="",
    pages=1,
    sort="latest",
    per_page=DEFAULT_PER_PAGE,
    topics="",
    inclusive_or=False,
    export_format="png",
    branch="main",
    overwrite=False,
    output_group="",
    include_forks=False,
    concurrency=4,
):
    infinite = int(pages) == -1
    page_num = 1
    end_page = None if infinite else max(1, int(pages))
    saved_count = 0
    attempted_total = 0
    skipped_existing_total = 0
    failed_total = 0
    duplicate_total = 0
    seen = load_seen_character_keys()
    empty_passes = 0
    infinite_random = infinite and resolve_sort(sort, username_filtered=bool(username)) == "random"
    chunk_size = max(1, min(int(concurrency) if concurrency else 1, 20))

    if infinite:
        info("Infinite mode is ON. Close the CMD window or press Ctrl+C to stop.")
        if infinite_random:
            info("Random sort has unstable pages, so infinite mode refreshes page 1 each pass.")
    if seen:
        info(f"Duplicate guard loaded {len(seen)} saved character key(s).")
    info(f"Parallel downloads per batch: {chunk_size}")

    while infinite or page_num <= end_page:
        request_page = 1 if infinite_random else page_num
        if _DASHBOARD is not None:
            _DASHBOARD.set_meta(page=request_page)
            _DASHBOARD.update_action(f"Fetching page {request_page}")
        nodes, total = fetch_character_page(
            page,
            token,
            request_page,
            query=query,
            username=username,
            sort=sort,
            per_page=per_page,
            topics=topics,
            inclusive_or=inclusive_or,
            include_forks=include_forks,
        )

        if total is not None:
            info(f"Total matching results reported by Chub: {total}")

        if not nodes:
            warn(f"No characters found on page {request_page}")
            if infinite:
                wait_seconds = 60
                empty_passes += 1
                if not infinite_random and page_num > 1:
                    info(f"Infinite mode: reached the current end. Waiting {wait_seconds}s, then restarting at page 1.")
                    page_num = 1
                else:
                    info(f"Infinite mode: waiting {wait_seconds}s, then retrying page {request_page}.")
                if _DASHBOARD is not None:
                    _DASHBOARD.update_action(f"Idle - waiting {wait_seconds}s for new cards")
                time.sleep(wait_seconds)
                continue
            break

        fresh_nodes = []
        page_duplicate_count = 0
        for node in nodes:
            seen_keys = character_seen_keys(node)
            if seen_keys and seen.intersection(seen_keys):
                page_duplicate_count += 1
                if _DASHBOARD is not None:
                    _DASHBOARD.record_card("skipped_dupe")
                continue
            seen.update(seen_keys)
            fresh_nodes.append(node)

        duplicate_total += page_duplicate_count
        if page_duplicate_count:
            info(f"Skipped {page_duplicate_count} duplicate result(s) on page {request_page}.")

        info(f"Downloading page {request_page} ({len(fresh_nodes)} new card(s), {chunk_size} at a time)...")
        page_saved_files = 0

        for start in range(0, len(fresh_nodes), chunk_size):
            chunk = fresh_nodes[start:start + chunk_size]
            chunk_end = min(start + chunk_size, len(fresh_nodes))
            print()
            info(f"[page {request_page} cards {start + 1}-{chunk_end}/{len(fresh_nodes)}]")
            if _DASHBOARD is not None:
                _DASHBOARD.update_action(
                    f"Page {request_page} cards {start + 1}-{chunk_end}/{len(fresh_nodes)}"
                )

            chunk_saved, chunk_attempted, chunk_existing, chunk_failed = download_node_batch(
                page,
                token,
                chunk,
                export_format=export_format,
                branch=branch,
                overwrite=overwrite,
                output_group=output_group,
            )
            saved_count += chunk_saved
            page_saved_files += chunk_saved
            attempted_total += chunk_attempted
            skipped_existing_total += chunk_existing
            failed_total += chunk_failed
            time.sleep(random.uniform(0.1, 0.25))

        if infinite:
            slept_for_no_new = False
            if page_saved_files == 0:
                empty_passes += 1
                wait_seconds = min(300, 30 * empty_passes)
                info(f"Infinite mode: no new cards this pass. Waiting {wait_seconds}s before continuing.")
                time.sleep(wait_seconds)
                slept_for_no_new = True
            else:
                empty_passes = 0

            if infinite_random:
                time.sleep(random.uniform(0.2, 0.6))
                continue
            if len(nodes) < max(1, min(int(per_page), MAX_PER_PAGE)):
                wait_seconds = 60
                if not slept_for_no_new:
                    info(f"Infinite mode: reached the current end. Waiting {wait_seconds}s, then restarting at page 1.")
                    time.sleep(wait_seconds)
                else:
                    info("Infinite mode: reached the current end. Restarting at page 1.")
                page_num = 1
                continue

        page_num += 1
        time.sleep(random.uniform(0.2, 0.6))

    print()
    info("------ Run Summary ------")
    info(f"  Files saved          : {saved_count}")
    info(f"  Cards attempted      : {attempted_total}")
    info(f"  Skipped (exists)     : {skipped_existing_total}")
    info(f"  Skipped (duplicates) : {duplicate_total}")
    info(f"  Failed               : {failed_total}")
    return saved_count


def flatten_events(events_payload):
    events = []
    if not isinstance(events_payload, dict):
        return events
    for bucket in ("active", "completed", "draft", "cancelled"):
        bucket_events = events_payload.get(bucket)
        if isinstance(bucket_events, list):
            for event in bucket_events:
                if isinstance(event, dict):
                    event = dict(event)
                    event["_bucket"] = bucket
                    events.append(event)
    return events


def fetch_events(page, token):
    result = browser_json_request(page, f"{READ_BASE}/api/events", token)
    if not result.get("ok"):
        raise RuntimeError(f"Event fetch failed: HTTP {result.get('status', '?')} {result.get('text', '')[:250]}")
    return flatten_events(result.get("data") or {})


def resolve_event_tag(page, token, event_value):
    source = text_value(event_value)
    if not source:
        return ""

    parsed = urllib.parse.urlparse(source)
    if parsed.netloc:
        pieces = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        if len(pieces) >= 2 and pieces[0].lower() == "events":
            source = pieces[1]

    events = fetch_events(page, token)
    source_key = source.lower()
    for event in events:
        candidates = [
            str(event.get("id", "")),
            text_value(event.get("title")),
            text_value(event.get("event_tag")),
        ]
        if any(candidate.lower() == source_key for candidate in candidates if candidate):
            tag = text_value(event.get("event_tag")) or text_value(event.get("title"))
            info(f"Event matched: {event.get('title')} -> tag '{tag}'")
            return tag

    warn(f"Could not find an event named '{source}', using it as a tag")
    return source


def print_preview(nodes, total=None, limit=10):
    if total is not None:
        info(f"Total matching results: {total}")
    for index, node in enumerate(nodes[:limit], 1):
        topics = ", ".join(node.get("topics") or [])
        info(f"{index}. {node.get('name', 'Unknown')} - {node.get('fullPath', '')}")
        if topics:
            info(f"   Tags: {topics[:120]}")


def connect_browser(playwright, visible=False):
    token = discover_public_token()
    if not launch_chrome(CHUB_HOME):
        raise RuntimeError("Could not launch/connect to Chrome.")

    browser = playwright.chromium.connect_over_cdp(CDP_URL)
    chub_page = None
    any_page = None

    for context in browser.contexts:
        for page in context.pages:
            any_page = page
            if "chub.ai" in page.url:
                chub_page = page
                break
        if chub_page:
            break

    if chub_page is None:
        if any_page is not None:
            chub_page = any_page
        elif browser.contexts:
            chub_page = browser.contexts[0].new_page()
        else:
            context = browser.new_context()
            chub_page = context.new_page()

    if "chub.ai" not in chub_page.url:
        chub_page.goto(CHUB_HOME, wait_until="domcontentloaded", timeout=60000)
    else:
        chub_page.wait_for_load_state("domcontentloaded", timeout=60000)

    return browser, chub_page, token


def run_login():
    header("Chub Login/Profile Setup")
    info("Opening real Chrome using the Janitor-style debug profile.")
    info("Log in with Google, enable whatever content settings you need, then come back here.")
    with sync_playwright() as p:
        browser, page, _ = connect_browser(p, visible=True)
        page.goto(CHUB_HOME, wait_until="domcontentloaded", timeout=60000)
        input("    Press Enter here after Chub is logged in and ready...")
    success(f"Saved browser profile: {CHUB_PROFILE}")


def prompt_format():
    print()
    print("    Format")
    print("    [1]  PNG card")
    print("    [2]  JSON")
    print("    [3]  Both")
    choice = input("    Pick format [default 1]: ").strip() or "1"
    return {"1": "png", "2": "json", "3": "both"}.get(choice, "png")


def prompt_sort():
    print()
    print("    Sort")
    options = PROMPT_SORT_KEYS
    for index, key in enumerate(options, 1):
        print(f"    [{index}]  {SORT_OPTIONS[key]['label']}")
    choice = input("    Pick sort [default 2 - Latest]: ").strip() or "2"
    try:
        return options[max(0, min(int(choice) - 1, len(options) - 1))]
    except ValueError:
        return "latest"


def prompt_match():
    print()
    print("    Tag Matching")
    print("    [1]  All tags (Love AND Human)")
    print("    [2]  Any tag (Love OR Human)")
    choice = input("    Pick matching [default 1]: ").strip() or "1"
    return "any" if choice == "2" else "all"


def prompt_concurrency(default=4):
    print()
    raw = input(f"    Batch size - cards downloaded at once [default {default}, pick 1-20]: ").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, 20))


def run_menu():
    header("Chub AI Character Downloader")
    print()
    print("    [1]  Download single character")
    print("    [2]  Search and download")
    print("    [3]  Creator download")
    print("    [4]  Tag download")
    print("    [5]  Event download")
    print("    [6]  Preview search")
    print("    [7]  Preview tag")
    print("    [8]  Login / setup Chub profile")
    print("    [9]  Exit")
    print()
    choice = input("    Pick a mode (1-9): ").strip()

    if choice == "9":
        return

    args = argparse.Namespace(
        visible=False,
        overwrite=False,
        include_forks=False,
        branch="main",
        per_page=DEFAULT_PER_PAGE,
        pages=1,
        sort="latest",
        topics="",
        match="all",
        limit=10,
        concurrency=4,
    )

    if choice == "1":
        args.command = "single"
        args.target = input("    Paste character URL/path: ").strip()
        args.format = prompt_format()
        run_with_browser(args)
    elif choice == "2":
        args.command = "search"
        args.concurrency = prompt_concurrency()
        args.query = input("    Search text: ").strip()
        args.pages = int(input("    Pages [default 1, -1 forever]: ").strip() or "1")
        args.sort = prompt_sort()
        args.format = prompt_format()
        run_with_browser(args)
    elif choice == "3":
        args.command = "creator"
        args.concurrency = prompt_concurrency()
        args.creator = input("    Creator username/profile URL: ").strip()
        args.pages = int(input("    Pages [default 1, -1 forever]: ").strip() or "1")
        args.sort = prompt_sort()
        args.format = prompt_format()
        run_with_browser(args)
    elif choice == "4":
        args.command = "tag"
        args.concurrency = prompt_concurrency()
        args.tags = input("    Tag(s), comma separated: ").strip()
        args.pages = int(input("    Pages [default 1, -1 forever]: ").strip() or "1")
        args.sort = prompt_sort()
        args.match = prompt_match()
        args.format = prompt_format()
        run_with_browser(args)
    elif choice == "5":
        args.command = "event"
        args.concurrency = prompt_concurrency()
        args.event = input("    Event name/tag/URL: ").strip()
        args.pages = int(input("    Pages [default 1, -1 forever]: ").strip() or "1")
        args.sort = prompt_sort()
        args.format = prompt_format()
        run_with_browser(args)
    elif choice == "6":
        args.command = "preview"
        args.query = input("    Search text: ").strip()
        args.sort = prompt_sort()
        run_with_browser(args)
    elif choice == "7":
        args.command = "preview_tag"
        args.tags = input("    Tag(s), comma separated: ").strip()
        args.sort = prompt_sort()
        args.match = prompt_match()
        run_with_browser(args)
    elif choice == "8":
        run_login()
    else:
        fail("Unknown choice")


def run_with_browser(args):
    global _LOG_SINK, _DASHBOARD

    use_dashboard = args.command in ("search", "creator", "tag", "event")
    log_proc = None
    render_thread = None
    stop_event = None

    with sync_playwright() as p:
        browser, page, token = connect_browser(p, visible=getattr(args, "visible", False))

        if use_dashboard:
            enable_vt_mode()
            try:
                _LOG_SINK = FileLogSink(LOG_FILE)
                _DASHBOARD = Dashboard()
                target_value = (
                    getattr(args, "query", None)
                    or getattr(args, "tags", None)
                    or getattr(args, "creator", None)
                    or getattr(args, "event", None)
                    or ""
                )
                mode_label = {
                    "search": "Search download",
                    "creator": "Creator download",
                    "tag": "Tag download",
                    "event": "Event download",
                }.get(args.command, args.command)
                _DASHBOARD.set_meta(
                    target=str(target_value or ""),
                    concurrency=getattr(args, "concurrency", 4),
                    mode=mode_label,
                )
                log_proc = open_log_window(LOG_FILE)
                stop_event = threading.Event()
                render_thread = threading.Thread(
                    target=render_dashboard_loop,
                    args=(_DASHBOARD, stop_event, LOG_FILE),
                    daemon=True,
                )
                render_thread.start()
            except Exception as exc:
                _LOG_SINK = None
                _DASHBOARD = None
                use_dashboard = False
                fail(f"Dashboard setup failed, continuing in plain mode: {exc}")

        try:
            if args.command == "single":
                header("Single Character")
                saved = download_character(
                    page,
                    token,
                    target=args.target,
                    export_format=args.format,
                    branch=args.branch,
                    overwrite=args.overwrite,
                )
                info(f"Finished: {len(saved)} file reference(s)")

            elif args.command in ("search", "creator", "tag", "event", "preview", "preview_tag"):
                is_creator = args.command == "creator"
                is_tag = args.command in ("tag", "preview_tag")
                is_event = args.command == "event"
                is_preview = args.command in ("preview", "preview_tag")
                if is_creator:
                    title = "Creator Download"
                elif is_tag:
                    title = "Preview Tag" if is_preview else "Tag Download"
                elif is_event:
                    title = "Event Download"
                else:
                    title = "Preview Search" if is_preview else "Search Download"
                header(title)
                query = "" if (is_creator or is_tag or is_event) else args.query
                username = parse_creator(args.creator) if is_creator else ""
                topics = args.topics
                output_group = ""
                if is_tag:
                    topics = merge_topics(args.tags, args.topics)
                    output_group = tag_folder_name(topics)
                    info(f"Tags: {topics}")
                    info(f"Output folder group: {output_group}")
                elif is_event:
                    event_tag = resolve_event_tag(page, token, args.event)
                    topics = merge_topics(event_tag, args.topics)
                    output_group = tag_folder_name(topics)
                    info(f"Event tag: {topics}")

                if is_preview:
                    nodes, total = search_characters(
                        page,
                        token,
                        query=query,
                        username=username,
                        pages=args.pages,
                        sort=args.sort,
                        per_page=args.per_page,
                        topics=topics,
                        inclusive_or=getattr(args, "match", "all") == "any",
                        include_forks=getattr(args, "include_forks", False),
                    )
                    print_preview(nodes, total=total, limit=args.limit)
                    return

                saved_count = download_character_pages(
                    page,
                    token,
                    query=query,
                    username=username,
                    pages=args.pages,
                    sort=args.sort,
                    per_page=args.per_page,
                    topics=topics,
                    inclusive_or=getattr(args, "match", "all") == "any",
                    export_format=args.format,
                    branch=args.branch,
                    overwrite=args.overwrite,
                    output_group=output_group,
                    include_forks=getattr(args, "include_forks", False),
                    concurrency=getattr(args, "concurrency", 4),
                )
                info(f"Finished: {saved_count} file(s) saved")
        except KeyboardInterrupt:
            warn("Stopped by user")
        finally:
            # Chrome is launched as an external Janitor-style debug browser.
            # Leave it open so the saved login/profile state stays easy to reuse.
            if use_dashboard:
                if stop_event is not None:
                    stop_event.set()
                if render_thread is not None:
                    render_thread.join(timeout=2)
                _clear_console()
                if _DASHBOARD is not None:
                    print_final_summary(_DASHBOARD)
                    print()
                    print(f"  Per-card log saved to: {LOG_FILE}")
                    if log_proc is not None:
                        print("  The second console window stays open until you close it.")
                _LOG_SINK = None
                _DASHBOARD = None


def build_parser():
    parser = argparse.ArgumentParser(description="Download Chub AI character cards.")
    parser.add_argument("--visible", action="store_true", help="Show the browser while fetching.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files instead of skipping.")
    parser.add_argument("--branch", default="main", help="Repository branch/ref for card.json downloads.")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("login", help="Open the persistent Chub browser profile so you can log in once.")

    single = sub.add_parser("single", help="Download one character by URL or full path.")
    single.add_argument("target")
    single.add_argument("--format", choices=["png", "json", "both"], default="png")

    search = sub.add_parser("search", help="Search Chub and download matching characters.")
    search.add_argument("query")
    search.add_argument("--pages", type=int, default=1)
    search.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    search.add_argument("--sort", choices=list(SORT_OPTIONS.keys()), default="latest")
    search.add_argument("--topics", default="")
    search.add_argument("--match", choices=["all", "any"], default="all", help="How to match multiple --topics tags.")
    search.add_argument("--include-forks", action="store_true", help="Include forked copies in search results.")
    search.add_argument("--format", choices=["png", "json", "both"], default="png")
    search.add_argument("--concurrency", type=int, default=4, help="Parallel card downloads per batch (1-20).")

    creator = sub.add_parser("creator", help="Download characters by creator username/profile URL.")
    creator.add_argument("creator")
    creator.add_argument("--pages", type=int, default=1)
    creator.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    creator.add_argument("--sort", choices=list(SORT_OPTIONS.keys()), default="latest")
    creator.add_argument("--topics", default="")
    creator.add_argument("--match", choices=["all", "any"], default="all", help="How to match multiple --topics tags.")
    creator.add_argument("--include-forks", action="store_true", help="Include forked copies in search results.")
    creator.add_argument("--format", choices=["png", "json", "both"], default="png")
    creator.add_argument("--concurrency", type=int, default=4, help="Parallel card downloads per batch (1-20).")

    tag = sub.add_parser("tag", help="Download characters by one or more tags.")
    tag.add_argument("tags", help="Tag, tag URL, or comma-separated tags such as Love,Human.")
    tag.add_argument("--pages", type=int, default=1)
    tag.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    tag.add_argument("--sort", choices=list(SORT_OPTIONS.keys()), default="latest")
    tag.add_argument("--topics", default="", help="Extra tags to combine with the positional tags.")
    tag.add_argument("--match", choices=["all", "any"], default="all")
    tag.add_argument("--include-forks", action="store_true", help="Include forked copies in search results.")
    tag.add_argument("--format", choices=["png", "json", "both"], default="png")
    tag.add_argument("--concurrency", type=int, default=4, help="Parallel card downloads per batch (1-20).")

    event = sub.add_parser("event", help="Download characters from a Chub event tag.")
    event.add_argument("event", help="Event title, event tag, event id, or /events URL.")
    event.add_argument("--pages", type=int, default=1)
    event.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    event.add_argument("--sort", choices=list(SORT_OPTIONS.keys()), default="latest")
    event.add_argument("--topics", default="", help="Extra tags to combine with the event tag.")
    event.add_argument("--match", choices=["all", "any"], default="all")
    event.add_argument("--include-forks", action="store_true", help="Include forked copies in search results.")
    event.add_argument("--format", choices=["png", "json", "both"], default="png")
    event.add_argument("--concurrency", type=int, default=4, help="Parallel card downloads per batch (1-20).")

    preview = sub.add_parser("preview", help="Preview search results without downloading.")
    preview.add_argument("query")
    preview.add_argument("--pages", type=int, default=1)
    preview.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    preview.add_argument("--sort", choices=list(SORT_OPTIONS.keys()), default="latest")
    preview.add_argument("--topics", default="")
    preview.add_argument("--match", choices=["all", "any"], default="all", help="How to match multiple --topics tags.")
    preview.add_argument("--include-forks", action="store_true", help="Include forked copies in search results.")
    preview.add_argument("--limit", type=int, default=10)

    preview_tag = sub.add_parser("preview-tag", help="Preview tag results without downloading.")
    preview_tag.set_defaults(command="preview_tag")
    preview_tag.add_argument("tags", help="Tag, tag URL, or comma-separated tags such as Love,Human.")
    preview_tag.add_argument("--pages", type=int, default=1)
    preview_tag.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    preview_tag.add_argument("--sort", choices=list(SORT_OPTIONS.keys()), default="latest")
    preview_tag.add_argument("--topics", default="")
    preview_tag.add_argument("--match", choices=["all", "any"], default="all")
    preview_tag.add_argument("--include-forks", action="store_true", help="Include forked copies in search results.")
    preview_tag.add_argument("--limit", type=int, default=10)

    return parser


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) == 1:
        run_menu()
        return

    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    if args.command == "login":
        run_login()
        return
    run_with_browser(args)


if __name__ == "__main__":
    main()
