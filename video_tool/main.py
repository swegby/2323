#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
Video Stitcher Pro — Batch Colored Preset Edition v2.6 Beautiful GUI
=====================================================================
Desktop app to assemble vertical Reels / TikTok / Shorts from 5 parts
with meme text. Works on Windows / Linux / macOS via
Python 3.10+ · PyQt6 · MoviePy 2.1.2 · Pillow · imageio-ffmpeg ·
requests · proglog.

Main render pipeline is pure ffmpeg (fast, parallel segments);
MoviePy is used only as fallback and for frame extraction (preview).
=====================================================================
"""

import os
import sys
import re
import io
import json
import math
import random
import shutil
import subprocess
import threading
import time
import uuid
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- Qt
from PyQt6.QtCore import (
    QSizeF,
    Qt, QThread, QTimer, pyqtSignal, QRectF, QPointF, QObject, QSize,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontMetrics, QImage, QPixmap,
    QLinearGradient, QPainterPath, QFontDatabase, QAction, QKeySequence,
    QFontInfo,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QSlider,
    QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QTabWidget,
    QScrollArea, QFileDialog, QMessageBox, QSizePolicy, QToolTip, QDialog,
    QDialogButtonBox, QFormLayout, QGroupBox, QLayout, QProgressBar,
    QGraphicsDropShadowEffect, QStackedWidget, QAbstractItemView,
)

# ------------------------------------------------------------- Pillow
from PIL import Image, ImageDraw, ImageFont

# ------------------------------------------------------------ MoviePy
try:                                     # moviepy >= 2.0
    from moviepy import VideoFileClip
except Exception:
    try:                                 # legacy 1.x
        from moviepy.editor import VideoFileClip
    except Exception:
        VideoFileClip = None


def _moviepy_quiet() -> None:
    """Убирает отладочный спам moviepy 2.1.2 в консоль.

    В FFMpeg_VideoReader.initialize() стоят безусловные print(self.infos)
    (огромный словарь метаданных) и print(" ".join(cmd)) (полная команда
    ffmpeg) — по два вывода на каждое открытие видео. Подменяем print
    внутри модуля ffmpeg_reader: это потокобезопасно (не трогаем
    sys.stdout) и не глушит чужой вывод.
    """
    try:
        from moviepy.video.io import ffmpeg_reader as _ffr
        _ffr.print = lambda *args, **kwargs: None
    except Exception:
        pass


if VideoFileClip is not None:
    _moviepy_quiet()

import imageio_ffmpeg
import requests

APP_NAME = "Video Stitcher Pro"
APP_VERSION = "v2.6"

# ------------------------------------------------------------ PATHS --
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDERS = ["folder_1", "folder_2", "folder_3", "folder_4", "folder_5"]
FOLDER_DIRS = [os.path.join(BASE_DIR, f) for f in FOLDERS]
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
USED_DIR = os.path.join(OUTPUT_DIR, "used")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
FONT_ANTON = os.path.join(FONTS_DIR, "Anton-Regular.ttf")
FONT_OSWALD = os.path.join(FONTS_DIR, "Oswald-Bold.ttf")
PROJECT_JSON = os.path.join(BASE_DIR, "project.json")
CHARACTERS_JSON = os.path.join(BASE_DIR, "characters.json")
FFMPEG_PATH_JSON = os.path.join(BASE_DIR, "ffmpeg_path.json")

# ------------------------------------------------------- CONSTANTS ---
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    ".ts", ".flv", ".wmv", ".3gp", ".mpeg", ".mpg", ".ogv",
}

# Bright color map used by [blue]...[ /blue] tags and quick buttons
COLOR_MAP: Dict[str, str] = {
    # ---- синие (насыщенные, много оттенков) ----
    "blue":       "#0066FF",   # основной — сочный электрик, не бледный
    "electric":   "#0047FF",   # электрик глубже
    "royal":      "#2952FF",   # королевский
    "neonblue":   "#1F51FF",   # неоновый
    "deepblue":   "#0026FF",   # ультра-глубокий
    "ultramarine": "#3B24FF",  # ультрамарин
    "indigo":     "#4B0DFF",   # индиго
    "navy":       "#0A2FA8",   # тёмно-синий
    "azure":      "#007FFF",   # лазурный
    "sky":        "#2EB8FF",   # небесный
    "babyblue":   "#7FDBFF",   # светло-голубой
    "cyan":       "#00E5FF",   # бирюзово-голубой (старый "blue" был похож на него)
    # ---- остальные ----
    "red":    "#FF4D6D",
    "yellow": "#FFD54F",
    "green":  "#4DFF8C",
    "pink":   "#FF69B4",
    "orange": "#FF8C00",
    "purple": "#BF55FF",
    "white":  "#FFFFFF",
    "black":  "#000000",
    "lime":   "#A6FF00",
    "grey":   "#B0B0B0",
}

# {rand:min-max[:step]}  /  {random:...}  /  {r:...}
RAND_PATTERN = re.compile(
    r"\{\s*(?:rand|random|r)\s*:\s*"
    r"([0-9]+\.?[0-9]*)\s*-\s*([0-9]+\.?[0-9]*)"
    r"(?:\s*:\s*([0-9]+\.?[0-9]*))?\s*\}"
)

# [blue]text[/blue]  [color=#00D5FF]text[/]  hex tags
COLOR_TAG_RE = re.compile(
    r"\[(?:color\s*=\s*)?(#[0-9A-Fa-f]{3,8}|[a-zA-Z0-9_]+)\](.*?)\[/[^\]]*\]",
    re.S,
)

COLOR_NAMES = set(COLOR_MAP.keys()) | {
    "white", "black", "grey", "gray",
}

# normalize {blue} / <blue> style tags into [blue]
_OPEN_TAG_RE = re.compile(r"[\{<]\s*(?:(?:color\s*=\s*)?(#[0-9A-Fa-f]{3,8}|[a-zA-Z0-9_]+))\s*[\}>]")
_CLOSE_TAG_RE = re.compile(r"[\{<]\s*/\s*[^\}>]*\s*[\}>]")

DEFAULT_PRESET = {
    "text": "Текст",
    "relative_rect": {"x": 0.10, "y": 0.12, "w": 0.80, "h": 0.22},
    "font_scale": 1.1,
    "stroke_width": 0,
}

DEFAULT_SEGMENTS = [
    {"duration": 5.0, "video": "", "presets": [dict(DEFAULT_PRESET)] * 1},
    {"duration": 5.0, "video": "", "presets": [dict(DEFAULT_PRESET)] * 1},
    {"duration": 5.0, "video": "", "presets": [dict(DEFAULT_PRESET)] * 1},
    {"duration": 5.0, "video": "", "presets": [dict(DEFAULT_PRESET)] * 1},
    {"duration": 5.0, "video": "", "presets": [dict(DEFAULT_PRESET)] * 1},
]

QUALITY_PRESETS = [
    {"key": "max",   "label": "💎 Макс CRF14 (MOV)", "crf": 14, "preset": "slow"},
    {"key": "high",  "label": "⭐ Высокое CRF18",    "crf": 18, "preset": "medium"},
    {"key": "bal",   "label": "⚖️ Баланс CRF20",    "crf": 20, "preset": "fast"},
    {"key": "speed", "label": "🚀 Скорость CRF23",   "crf": 23, "preset": "veryfast"},
]

RESOLUTIONS = [
    {"label": "1080×1920 Reels",  "w": 1080, "h": 1920},
    {"label": "720×1280",         "w": 720,  "h": 1280},
    {"label": "Оригинал",         "w": 0,    "h": 0},
]

FPS_CHOICES = [30, 24, 60]

# ======================================================================
#  FILE SYSTEM HELPERS
# ======================================================================

def ensure_dirs() -> None:
    for d in FOLDER_DIRS + [OUTPUT_DIR, USED_DIR, FONTS_DIR]:
        os.makedirs(d, exist_ok=True)


def natural_key(s: str) -> List[Any]:
    """Natural sort key: file2.mp4 < file10.mp4."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", str(s))]


def list_videos(folder: str) -> List[str]:
    """All video files in *folder* (flat), naturally sorted."""
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    try:
        for fn in os.listdir(folder):
            full = os.path.join(folder, fn)
            if os.path.isfile(full) and os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                out.append(full)
    except OSError:
        return []
    return sorted(out, key=natural_key)


def open_folder(path: str) -> None:
    """Cross-platform 'open file manager'."""
    if not os.path.exists(path):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                       # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def truncate_middle(s: str, max_len: int = 46) -> str:
    if len(s) <= max_len:
        return s
    half = (max_len - 3) // 2
    return s[:half] + "..." + s[-half:]

# ======================================================================
#  CHARACTERS (folder_1 subfolders)
# ======================================================================

IGNORED_FOLDER_NAMES = {".", "..", "used", "bin", "fonts", "__pycache__"}


def get_character_name(folder_path: str) -> str:
    """Folder name; the root of folder_1 itself is called 'default'."""
    if os.path.abspath(folder_path) == os.path.abspath(FOLDER_DIRS[0]):
        return "default"
    return os.path.basename(os.path.normpath(folder_path))


def list_character_folders() -> List[Tuple[str, str, int]]:
    """Scan folder_1 -> list of (path, name, video_count).

    Every subfolder is a character (even with 0 videos — so a newly
    created character shows up immediately); folder_1 root itself is
    the 'default' character only when it contains videos. Hidden /
    temp / helper folders are ignored.
    """
    res: List[Tuple[str, str, int]] = []
    f1 = FOLDER_DIRS[0]
    if os.path.isdir(f1):
        try:
            for fn in sorted(os.listdir(f1), key=natural_key):
                full = os.path.join(f1, fn)
                if fn.startswith(".") or fn.lower() in IGNORED_FOLDER_NAMES:
                    continue
                if os.path.isdir(full):
                    n = len(list_videos(full))
                    res.append((full, fn, n))
        except OSError:
            pass
        # 'default' character = videos directly in folder_1
        n_default = len(list_videos(f1))
        if n_default > 0:
            res.insert(0, (f1, "default", n_default))
    return res


def get_filtered_character_folders(selected: List[str]) -> List[Tuple[str, str, int]]:
    """All characters, or only the ones in *selected*."""
    all_chars = list_character_folders()
    if not selected:
        return all_chars
    wanted = set(selected)
    return [c for c in all_chars if c[1] in wanted]


def _all_character_dirs() -> List[Tuple[str, str]]:
    """Every subfolder of folder_1 (path, name), even empty ones."""
    out: List[Tuple[str, str]] = []
    f1 = FOLDER_DIRS[0]
    if os.path.isdir(f1):
        try:
            for fn in sorted(os.listdir(f1), key=natural_key):
                full = os.path.join(f1, fn)
                if fn.startswith(".") or fn.lower() in IGNORED_FOLDER_NAMES:
                    continue
                if os.path.isdir(full):
                    out.append((full, fn))
        except OSError:
            pass
    return out

# ======================================================================
#  JSON HELPERS
# ======================================================================

def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    """Coerce anything to int without ever raising."""
    if isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _combo_index(combo, value: Any, default: int = 0) -> int:
    """Resolve an index number OR an item label to a valid combo index."""
    if isinstance(value, bool):
        return default
    try:
        idx = int(float(value))
        if 0 <= idx < combo.count():
            return idx
    except Exception:
        pass
    if isinstance(value, str):
        for i in range(combo.count()):
            if combo.itemText(i) == value:
                return i
    return default


def sanitize_project(data: Dict[str, Any]) -> Dict[str, Any]:
    """Heal known-bad fields in a loaded project so startup never crashes.

    (e.g. batch.mode stored as the combo label text instead of an index)
    """
    try:
        b = data.get("batch")
        if isinstance(b, dict):
            b["enabled"] = bool(b.get("enabled", False))
            b["mode"] = _to_int(b.get("mode"), 0)
            b["count"] = max(0, min(10000, _to_int(b.get("count"), 0)))
            b["threads"] = max(1, min(8, _to_int(b.get("threads"), 3)))
    except Exception:
        pass
    try:
        e = data.get("export")
        if isinstance(e, dict):
            e["resolution_index"] = _to_int(e.get("resolution_index"), 0)
            e["fps"] = _to_int(e.get("fps"), 30)
    except Exception:
        pass
    return data


def save_json(path: str, data: Any) -> bool:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.remove(path + ".tmp")
        except Exception:
            pass
        return False


def sanitize_characters(data: Dict[str, Any]) -> Dict[str, Any]:
    """Heal malformed characters.json so the UI never crashes on it."""
    try:
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("characters"), dict):
            data["characters"] = {}
        chars = data["characters"]
        for name, entry in list(chars.items()):
            if not isinstance(entry, dict):
                chars[name] = {"chat_id": "", "display_name": str(name)}
        sel = data.get("selected_characters", [])
        if not isinstance(sel, list):
            sel = list(sel) if isinstance(sel, (tuple, set)) else []
        sel = [str(s) for s in sel if isinstance(s, str)]
        data["selected_characters"] = sel
        data["bot_token"] = str(data.get("bot_token", "") or "")
        data["auto_send"] = bool(data.get("auto_send", False))
    except Exception:
        pass
    return data


def load_characters() -> Dict[str, Any]:
    data = load_json(CHARACTERS_JSON, {
        "bot_token": "",
        "auto_send": False,
        "characters": {},
        "selected_characters": [],
    })
    return sanitize_characters(data)


def save_characters(data: Dict[str, Any]) -> bool:
    return save_json(CHARACTERS_JSON, data)


def load_saved_ffmpeg_path() -> str:
    d = load_json(FFMPEG_PATH_JSON, {})
    return str(d.get("path", "") or "")


def save_ffmpeg_path(path: str) -> None:
    save_json(FFMPEG_PATH_JSON, {"path": path})
    proj = load_project()
    proj["ffmpeg_path"] = path
    save_json(PROJECT_JSON, proj)


def load_project() -> Dict[str, Any]:
    data = load_json(PROJECT_JSON, {
        "segments": json.loads(json.dumps(DEFAULT_SEGMENTS)),
        "export": {},
        "batch": {},
        "ui": {},
        "ffmpeg_path": "",
    })
    return sanitize_project(data)


# ======================================================================
#  FFMPEG LOCATOR (smart search with memory)
# ======================================================================

def is_valid_ffmpeg(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        r = subprocess.run(
            [path, "-version"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def find_system_ffmpeg() -> Optional[str]:
    """1) saved path  2) PATH  3) common locations  4) bundled."""
    # 1. saved
    saved = load_saved_ffmpeg_path()
    if saved and is_valid_ffmpeg(saved):
        return saved
    # 2. PATH
    try:
        for cand in ("ffmpeg", "ffmpeg.exe"):
            w = shutil.which(cand)
            if w and is_valid_ffmpeg(w):
                return w
    except Exception:
        pass
    # 3. common locations
    home = os.path.expanduser("~")
    common = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(home, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin", "ffmpeg.exe"),
        os.path.join(BASE_DIR, "bin", "ffmpeg.exe"),
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/snap/bin/ffmpeg",
    ]
    for c in common:
        if is_valid_ffmpeg(c):
            return c
    # 4. bundled with imageio-ffmpeg
    try:
        b = imageio_ffmpeg.get_ffmpeg_exe()
        if b and is_valid_ffmpeg(b):
            return b
    except Exception:
        pass
    return None


def download_ffmpeg_automatically() -> Optional[str]:
    """Bundled ffmpeg via imageio-ffmpeg; Windows fallback: BtbN build."""
    try:
        b = imageio_ffmpeg.get_ffmpeg_exe()
        if b and is_valid_ffmpeg(b):
            return b
    except Exception:
        pass
    # Windows fallback — download static BtbN build into bin/
    if sys.platform.startswith("win"):
        try:
            bin_dir = os.path.join(BASE_DIR, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            exe = os.path.join(bin_dir, "ffmpeg.exe")
            if is_valid_ffmpeg(exe):
                return exe
            url = ("https://github.com/BtbN/FFmpeg-Builds/releases/latest/"
                   "download/ffmpeg-master-latest-win64-gpl.zip")
            print(f"[ffmpeg] downloading {url}")
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                for name in z.namelist():
                    if name.endswith("bin/ffmpeg.exe"):
                        with z.open(name) as src, open(exe, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        break
            if is_valid_ffmpeg(exe):
                return exe
        except Exception as e:
            print("[ffmpeg] BtbN download failed:", e)
    return None


_ffmpeg_cache: Dict[str, Optional[str]] = {}
_ffmpeg_lock = threading.Lock()


def get_available_ram_mb() -> int:
    """Best-effort total physical RAM in MB (0 = unknown)."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return int(ms.ullTotalPhys // (1024 * 1024))
            return 0
        # Linux / macOS
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size // (1024 * 1024))
    except Exception:
        return 0


def adaptive_segment_workers() -> int:
    """min(5, RAM/700MB) — avoid OOM when 5 full-HD encodes run at once."""
    ram = get_available_ram_mb()
    if ram <= 0:
        return 5
    return max(1, min(5, ram // 700))


def get_ffmpeg_exe() -> str:
    """saved → system → bundled → download → 'ffmpeg'."""
    key = "exe"
    if key in _ffmpeg_cache:
        return _ffmpeg_cache[key] or "ffmpeg"
    with _ffmpeg_lock:
        if key in _ffmpeg_cache:
            return _ffmpeg_cache[key] or "ffmpeg"
        found = find_system_ffmpeg()
        if not found:
            found = download_ffmpeg_automatically()
        if not found:
            found = "ffmpeg"
        _ffmpeg_cache[key] = found
    return found


def get_ffmpeg_version(path: str) -> str:
    try:
        r = subprocess.run([path, "-version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=15)
        if r.returncode == 0:
            return r.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return ""


# ------------------------------------------------------- ffprobe-ish --
def _probe(path: str, ffmpeg_exe: str) -> Dict[str, Any]:
    """Duration / has_audio / width / height via `ffmpeg -i` stderr."""
    info: Dict[str, Any] = {"duration": 0.0, "audio": False, "w": 0, "h": 0}
    try:
        r = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-i", path],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        txt = r.stderr or ""
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", txt)
        if m:
            h, mi, s = m.groups()
            info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
        info["audio"] = bool(re.search(r"\bAudio:\s", txt))
        m = re.search(r"(\d{2,5})x(\d{2,5})", txt)
        if m:
            info["w"], info["h"] = int(m.group(1)), int(m.group(2))
        # pixel format: yuv420p10le / p010le / yuv422p10le ... -> 10-bit source
        pm = re.search(r"Video:.*?(yuv\d+p\d+le|p010le|p210le|p016le|yuv444p12le)", txt)
        info["pix_fmt"] = pm.group(1) if pm else ""
        info["ten_bit"] = bool(pm)
        # ---- color info: range + primaries/transfer/matrix ----
        # examples:  yuv420p(tv, bt709, progressive)
        #            yuv420p10le(tv, bt2020nc/bt2020/arib-std-b67, ...)
        #            yuvj420p(pc, progressive)
        info["color_range"] = ""
        info["color_trc"] = ""
        info["color_primaries"] = ""
        # match the parens right after the pixel format token:
        #   ", yuv420p10le(tv, bt2020nc/bt2020/arib-std-b67, ...)"
        cm = re.search(r"Video:[^\n]*?,\s*[a-z0-9]+le?\(([^)]*)\)", txt)
        if not cm:
            cm = re.search(r"Video:[^\n]*?,\s*yuvj?\d+p[\da-z]*\(([^)]*)\)", txt)
        if cm:
            inner = cm.group(1)
            parts = [p.strip() for p in inner.split(",")]
            for p in parts:
                if p in ("tv", "pc"):
                    info["color_range"] = p
                elif "/" in p:                       # matrix/primaries/transfer
                    bits = p.split("/")
                    if len(bits) >= 3:
                        info["color_primaries"] = bits[1].strip()
                        info["color_trc"] = bits[2].strip()
                elif p.startswith("bt") or "smpte" in p or "arib" in p:
                    info["color_primaries"] = p
        info["hdr"] = (
            info["color_trc"] in ("smpte2084", "arib-std-b67")
            or "bt2020" in info["color_primaries"]
        )
    except Exception:
        pass
    return info


def get_video_duration(path: str, ffmpeg_exe: str) -> float:
    if not path or not os.path.isfile(path):
        return 0.0
    d = _probe(path, ffmpeg_exe)["duration"]
    if d > 0:
        return d
    # MoviePy fallback (per spec MoviePy is used for duration)
    try:
        if VideoFileClip is not None:
            with VideoFileClip(path) as clip:
                return float(clip.duration)
    except Exception:
        pass
    return 0.0


def video_has_audio(path: str, ffmpeg_exe: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    return _probe(path, ffmpeg_exe)["audio"]


def get_video_size(path: str, ffmpeg_exe: str) -> Tuple[int, int]:
    p = _probe(path, ffmpeg_exe)
    if p["w"] and p["h"]:
        return p["w"], p["h"]
    try:
        if VideoFileClip is not None:
            with VideoFileClip(path) as clip:
                return int(clip.w), int(clip.h)
    except Exception:
        pass
    return 1080, 1920

# ======================================================================
#  FONTS (Anton latin / Oswald-Bold cyrillic, auto-download)
# ======================================================================

ANTON_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf"
OSWALD_CANDIDATES = [
    "https://raw.githubusercontent.com/google/fonts/main/ofl/oswald/Oswald%5Bwght%5D.ttf",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/oswald/static/Oswald-Bold.ttf",
    "https://raw.githubusercontent.com/googlefonts/oswald/main/fonts/ttf/Oswald-Bold.ttf",
]

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def download_file(url: str, dest: str) -> bool:
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(r.content)
        return os.path.getsize(dest) > 0
    except Exception:
        return False


def download_fonts() -> Tuple[bool, bool]:
    """Returns (anton_ok, oswald_ok). Skips if already present."""
    anton_ok = os.path.isfile(FONT_ANTON)
    oswald_ok = os.path.isfile(FONT_OSWALD)
    if not anton_ok:
        print("[fonts] downloading Anton-Regular.ttf ...")
        anton_ok = download_file(ANTON_URL, FONT_ANTON)
    if not oswald_ok:
        print("[fonts] downloading Oswald-Bold.ttf ...")
        for url in OSWALD_CANDIDATES:
            if download_file(url, FONT_OSWALD):
                oswald_ok = True
                break
    return anton_ok, oswald_ok


def load_pillow_font(path: str, size: int) -> Any:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.load_default(size)          # Pillow >= 9.2
        except Exception:
            return ImageFont.load_default()


_SYSTEM_FONT_CACHE: Optional[Dict[str, str]] = None


def _system_fonts() -> Dict[str, str]:
    """Map script -> system font path (Cyrillic-capable). Cached."""
    global _SYSTEM_FONT_CACHE
    if _SYSTEM_FONT_CACHE is not None:
        return _SYSTEM_FONT_CACHE
    wins = [
        ("cyr", r"C:\Windows\Fonts\arialbd.ttf"),
        ("cyr", r"C:\Windows\Fonts\arial.ttf"),
        ("cyr", r"C:\Windows\Fonts\timesbd.ttf"),
        ("cyr", r"C:\Windows\Fonts\segoeuib.ttf"),
    ]
    unix = [
        ("cyr", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("cyr", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("cyr", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ("cyr", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ("cyr", "/System/Library/Fonts/Supplemental/Arial.ttf"),
        ("cyr", "/System/Library/Fonts/Supplemental/Verdana Bold.ttf"),
        ("latin", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    out: Dict[str, str] = {}
    for script, path in wins + unix:
        if script not in out and os.path.isfile(path):
            out[script] = path
    _SYSTEM_FONT_CACHE = out
    return out


def pick_font_file(text: str) -> str:
    """Anton for latin, Oswald-Bold for Cyrillic, then system fonts."""
    needs_cyr = bool(text and CYRILLIC_RE.search(text))
    if needs_cyr:
        if os.path.isfile(FONT_OSWALD):
            return FONT_OSWALD
        if os.path.isfile(FONT_ANTON):
            return FONT_ANTON
        sysf = _system_fonts().get("cyr", "")
        if sysf:
            return sysf
    else:
        if os.path.isfile(FONT_ANTON):
            return FONT_ANTON
        if os.path.isfile(FONT_OSWALD):
            return FONT_OSWALD
        sysf = _system_fonts().get("latin", _system_fonts().get("cyr", ""))
        if sysf:
            return sysf
    return ""

# ======================================================================
#  TEXT UTILITIES — colors, random numbers, uppercase
# ======================================================================

def normalize_color_tags(raw: str) -> str:
    """{blue}..{/blue} and <blue>..</blue> → [blue]..[/]."""
    if not raw:
        return raw
    raw = _CLOSE_TAG_RE.sub("[/]", raw)                 # {/blue} </blue> → [/]
    raw = _OPEN_TAG_RE.sub(lambda m: "[" + m.group(1) + "]", raw)   # {blue} → [blue]
    return raw


def parse_color_text(raw: str, default: str = "white") -> List[Tuple[str, str]]:
    """[blue]TOOWERS[/blue] → [("TOOWERS", "#00D5FF"), ...]"""
    raw = normalize_color_tags(raw)
    parts: List[Tuple[str, str]] = []
    pos = 0
    for m in COLOR_TAG_RE.finditer(raw):
        if m.start() > pos:
            parts.append((raw[pos:m.start()], default))
        tag, inner = m.group(1), m.group(2)
        color = COLOR_MAP.get(tag.lower())
        if color is None and tag.startswith("#"):
            try:
                c = tag[1:]
                if len(c) in (3, 6, 8):
                    color = "#" + c
            except Exception:
                color = None
        parts.append((inner, color if color else default))
        pos = m.end()
    if pos < len(raw):
        parts.append((raw[pos:], default))
    if not parts:
        parts.append((raw, default))
    return parts


def format_rand_value(v: float) -> str:
    """2.0 → '2', 1000 → '1k', 2100 → '2.1k' (for '$2.1k' memes)."""
    if v >= 1000:
        k = v / 1000.0
        ks = f"{k:.1f}".rstrip("0").rstrip(".")
        return f"{ks}k"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def randomize_text(text: str) -> str:
    """Replace every {rand:min-max[:step]} with a random value."""
    def _one(m: re.Match) -> str:
        lo = float(m.group(1))
        hi = float(m.group(2))
        step = float(m.group(3)) if m.group(3) else None
        if lo > hi:
            lo, hi = hi, lo
        if step and step > 0:
            n = max(1, int(round((hi - lo) / step)))
            v = lo + random.randint(0, n) * step
            v = round(v, 6)
        else:
            if lo == int(lo) and hi == int(hi):
                v = float(random.randint(int(lo), int(hi)))
            else:
                v = round(random.uniform(lo, hi), 3)
        return format_rand_value(v)
    return RAND_PATTERN.sub(_one, text)


def uppercase_content(raw: str) -> str:
    """Uppercase visible text, keep [color] tags intact."""
    raw = normalize_color_tags(raw)
    def _one(m: re.Match) -> str:
        return m.group(0)
    out: List[str] = []
    pos = 0
    for m in COLOR_TAG_RE.finditer(raw):
        if m.start() > pos:
            out.append(raw[pos:m.start()].upper())
        out.append("[" + m.group(1) + "]" + m.group(2).upper() + "[/]")
        pos = m.end()
    if pos < len(raw):
        out.append(raw[pos:].upper())
    return "".join(out)


def has_color_tags(text: str) -> bool:
    return bool(COLOR_TAG_RE.search(normalize_color_tags(text)))


def has_random_tags(text: str) -> bool:
    return bool(RAND_PATTERN.search(text))


def text_max_line_len(text: str) -> int:
    lines = text.split("\n")
    return max((len(l) for l in lines), default=0)


def compute_font_size(raw_text: str, rect: Dict[str, float],
                      canvas_w: int, canvas_h: int,
                      font_scale: float = 1.0,
                      scale_factor: float = 1.0) -> int:
    """UNIFIED font size: same formula as the preview, so the rendered
    text matches what the user sees. No random ±3% shift anymore."""
    return compute_text_px_size(raw_text, rect, canvas_w, canvas_h,
                                font_scale, scale_factor,
                                clamp_min=30, clamp_max=130)


def compute_text_px_size(raw_text: str, rect: Dict[str, float],
                         W: int, H: int,
                         font_scale: float = 1.0,
                         scale_factor: float = 1.0,
                         clamp_min: int = -1, clamp_max: int = -1) -> int:
    """One font-size formula for BOTH the preview widget and the final
    render. Clamps scale proportionally to the canvas width so preview
    (300px) and final (1080/720px) match visually."""
    rw = max(1.0, rect.get("w", 0.8) * W)
    rh = max(1.0, rect.get("h", 0.22) * H)
    base = min(rw * 0.11, rh * 0.50) * max(font_scale, 0.2) * scale_factor
    max_len = text_max_line_len(raw_text)
    corr = max(0.75, 1.0 - (max_len - 22) * 0.01)
    size = base * corr
    if clamp_min < 0:
        clamp_min = max(8, int(30 * W / 1080.0))
    if clamp_max < 0:
        clamp_max = max(14, int(130 * W / 1080.0))
    size = max(clamp_min, min(clamp_max, size))
    return int(round(size))

# ======================================================================
#  PILLOW — colored RGBA text image (final render)
# ======================================================================

def _wrap_runs(runs: List[Tuple[str, str]], max_w: float,
               font_for: Any) -> List[List[Tuple[str, str, Any, float]]]:
    """Wrap (text,color) runs into lines of words with fonts & widths."""
    lines: List[List[Tuple[str, str, Any, float]]] = []
    cur: List[Tuple[str, str, Any, float]] = []
    cur_w = 0.0
    space_w = 0.0

    def flush():
        nonlocal cur, cur_w
        if cur:
            lines.append(cur)
            cur, cur_w = [], 0.0

    for text, color in runs:
        font = font_for(text)
        parts = text.split("\n")
        for pi, part in enumerate(parts):
            if pi > 0:
                flush()
            words = part.split(" ")
            for w in words:
                if w == "":
                    continue
                ww = font.getlength(w)
                space_w = font.getlength(" ")
                if cur and cur_w + space_w + ww > max_w and ww <= max_w:
                    flush()
                if not cur:
                    cur = [(w, color, font, ww)]
                    cur_w = ww
                else:
                    cur.append((w, color, font, ww))
                    cur_w += space_w + ww
    flush()
    return lines


def create_colored_text_image(text: str, canvas_w: int, canvas_h: int,
                              rect: Tuple[int, int, int, int],
                              font_size: int, stroke_width: int,
                              out_path: str) -> bool:
    """Render multiline colored text (with stroke) into RGBA PNG.

    rect = (x, y, w, h) in canvas pixels; text is wrapped to fit,
    centered horizontally + vertically, font auto-shrinks to fit box.
    """
    try:
        x0, y0, w, h = rect
        if w <= 0 or h <= 0:
            w, h = int(canvas_w * 0.8), int(canvas_h * 0.22)
        runs = parse_color_text(text, default="white")
        runs = [(t, c) for t, c in runs if t.strip() != ""]
        if not runs:
            runs = [(text or "Текст", "white")]

        def font_for(txt: str) -> Any:
            fpath = pick_font_file(txt)
            if fpath:
                try:
                    return ImageFont.truetype(fpath, font_size)
                except Exception:
                    pass
            return load_pillow_font(fpath, font_size)

        max_w = w * 0.97
        size = max(8, font_size)
        line_gap = 0.14

        for attempt in range(12):
            def mk_font(txt: str) -> Any:
                fpath = pick_font_file(txt)
                if fpath:
                    try:
                        return ImageFont.truetype(fpath, size)
                    except Exception:
                        pass
                return load_pillow_font(fpath, size)

            lines = _wrap_runs(runs, max_w, mk_font)
            line_h = size * (1 + line_gap)
            total_h = line_h * len(lines)
            max_line_w = 0.0
            for ln in lines:
                lw = sum(item[3] for item in ln) + max(0, len(ln) - 1) * mk_font(ln[0][0]).getlength(" ")
                max_line_w = max(max_line_w, lw)
            if (max_line_w <= max_w and total_h <= h) or size <= 10:
                break
            size = int(size * 0.93)

        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        sw = max(0, int(stroke_width))
        stroke_col = (0, 0, 0, 255)

        start_y = y0 + max(0.0, (h - total_h) / 2.0)
        yy = start_y
        for ln in lines:
            line_w = sum(item[3] for item in ln)
            if len(ln) > 1:
                sp = mk_font(ln[0][0]).getlength(" ")
                line_w += sp * (len(ln) - 1)
            xx = x0 + max(0.0, (w - line_w) / 2.0)
            for word, color, font, ww in ln:
                col = str(color or "#FFFFFF").lstrip("#")
                try:
                    if len(col) in (3, 6, 8):
                        if len(col) == 3:
                            col = "".join(c * 2 for c in col)
                        rgb = tuple(int(col[i:i + 2], 16) for i in (0, 2, 4))
                    else:
                        rgb = (255, 255, 255)
                except Exception:
                    rgb = (255, 255, 255)
                draw.text((xx, yy), word, font=font, fill=rgb + (255,),
                          stroke_width=sw, stroke_fill=stroke_col, anchor="la")
                xx += ww + (sp if len(ln) > 1 else 0)
            yy += line_h

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path, "PNG")
        return True
    except Exception:
        traceback.print_exc()
        # absolute fallback — plain single-color text, no tags
        try:
            runs = parse_color_text(re.sub(r"\[/?[^\]]*\]", "", text), default="white")
            plain = " ".join(t for t, _ in runs) or "Текст"
            font = load_pillow_font(pick_font_file(plain), max(12, font_size))
            img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((x0, y0), plain, font=font, fill=(255, 255, 255, 255))
            img.save(out_path, "PNG")
            return True
        except Exception:
            traceback.print_exc()
            return False

# ======================================================================
#  FFMPEG — SEGMENT BUILDING (fast path)
# ======================================================================

_format_opt_cache: Dict[str, str] = {}


def get_format_color_opt(ffmpeg_exe: str) -> str:
    """Which color-space option the 'format' filter accepts in this build.

    'color_spaces=bt709' (ffmpeg >= 6-ish), 'color_space=bt709' (older),
    or '' (very old) — detected once and cached.
    """
    if ffmpeg_exe in _format_opt_cache:
        return _format_opt_cache[ffmpeg_exe]
    opt = "color_spaces=bt709"
    try:
        r = subprocess.run([ffmpeg_exe, "-hide_banner", "-h", "filter=format"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        txt = (r.stdout or "") + (r.stderr or "")
        if "color_spaces" in txt:
            opt = "color_spaces=bt709"
        elif "color_space" in txt:
            opt = "color_space=bt709"
        else:
            opt = ""
    except Exception:
        pass
    _format_opt_cache[ffmpeg_exe] = opt
    return opt


def build_atempo(factor: float) -> str:
    """atempo chain (single atempo supports 0.5–2.0)."""
    if factor <= 2.0:
        return f"atempo={factor:.6f}"
    n = int(math.ceil(factor / 2.0))
    parts = ["atempo=2.000000"] * (n - 1)
    parts.append(f"atempo={factor / (2 ** (n - 1)):.6f}")
    return ",".join(parts)


def _make_bg_chain(canvas_w: int, canvas_h: int, fps: int,
                   blur_fill: bool = False, prefix: str = "") -> str:
    """Background filter chain ending with the [bg] output label.

    Normal: scale+center-crop. blur_fill: full-frame blurred copy behind the
    centered original (no content lost for non-vertical sources).
    prefix: optional color-normalization filters inserted before scaling
    (HDR tonemap / full-range fix for MOV sources).
    """
    if not blur_fill:
        return (
            f"{prefix}"
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase:"
            f"flags=bicubic,crop={canvas_w}:{canvas_h}:exact=1,fps={fps}[bg]"
        )
    return (
        f"{prefix}"
        f"split[bgA][fgA];"
        f"[bgA]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase:"
        f"flags=bicubic,crop={canvas_w}:{canvas_h}:exact=1,"
        f"boxblur=20:5,eq=brightness=-0.12,fps={fps}[blur];"
        f"[fgA]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease:"
        f"flags=bicubic[fgvid];"
        f"[blur][fgvid]overlay=(W-w)/2:(H-h)/2[bg]"
    )


def _micro_uniq_chain(canvas_w: int, canvas_h: int) -> str:
    """Invisible-to-the-eye uniquification filters (fresh random each call).

    Changes the pixel data (hence hash/fingerprint) without visible impact:
      • sub-pixel shift: upscale + crop with a random 1-6 px offset
      • micro eq: brightness ±0.004, contrast/saturation ±0.008
      • micro hue rotation ±0.4°
      • faint film grain (strength 1) with a random seed
    """
    pad = 6                                  # work area for the shift crop
    dx = random.randint(0, 4)
    dy = random.randint(0, 4)
    br = random.uniform(-0.004, 0.004)
    ct = random.uniform(0.992, 1.008)
    sat = random.uniform(0.992, 1.008)
    hue = random.uniform(-0.4, 0.4)
    seed = random.randint(0, 2 ** 31 - 1)
    return (
        f"scale={canvas_w + pad}:{canvas_h + pad}:flags=bicubic,"
        f"crop={canvas_w}:{canvas_h}:{dx}:{dy},"
        f"eq=brightness={br:.5f}:contrast={ct:.5f}:saturation={sat:.5f},"
        f"hue=h={hue:.3f},"
        f"noise=alls=1:allf=t:all_seed={seed},"
    )


def build_segment_ffmpeg(input_video: str, text_png: str, x: int, y: int,
                         target_dur: float, is_first: bool, orig_dur: float,
                         seg_out: str, ffmpeg_exe: str, crf: int = 18,
                         preset: str = "medium", fps: int = 30,
                         with_audio: bool = True,
                         canvas_w: int = 1080, canvas_h: int = 1920,
                         ten_bit: bool = False, blur_fill: bool = False,
                         audio_kbps: int = 192, micro_uniq: bool = False,
                         progress_cb=None) -> Tuple[bool, str]:
    """One vertical segment via a single ffmpeg call.

    ten_bit  — keep 10-bit depth when the source is 10-bit (no banding).
    blur_fill— blurred background fill instead of hard crop for wrong aspect.
    micro_uniq — invisible pixel-level uniquification (random sub-pixel
    shift + micro color/grain) so every output has a different fingerprint.
    """
    try:
        target_dur = max(0.3, float(target_dur))
        orig_dur = max(0.05, float(orig_dur or target_dur))
        has_audio = with_audio and video_has_audio(input_video, ffmpeg_exe)

        # probe source color info (HDR / full-range MOVs from iPhone etc.)
        src_info: Dict[str, Any] = {}
        try:
            src_info = _probe(input_video, ffmpeg_exe)
        except Exception:
            src_info = {}

        # decide output pixel format (10-bit only when source is 10-bit)
        use10 = False
        if ten_bit:
            use10 = bool(src_info.get("ten_bit"))
        pix = "yuv420p10le" if use10 else "yuv420p"

        # ---- color normalization prefix for MOV/iPhone sources ----
        # HDR (HLG/PQ/bt2020): proper tonemap to SDR bt709 — otherwise the
        # colors get oversaturated / oversharpened-looking after a naive
        # matrix reinterpretation.
        # Full-range (pc/yuvj420p): explicit pc->tv conversion — otherwise
        # contrast gets crushed/boosted ("повышенная резкость" look).
        color_fix = ""
        if src_info.get("hdr"):
            color_fix = (
                "zscale=t=linear:npl=100,format=gbrpf32le,"
                "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,"
            )
        elif src_info.get("color_range") == "pc":
            color_fix = (
                "scale=in_range=pc:out_range=tv,"
                "setparams=range=tv:colorspace=bt709,"
            )

        bg_chain = _make_bg_chain(canvas_w, canvas_h, fps, blur_fill,
                                  prefix=color_fix)

        cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]
        vf: List[str] = []
        need_silent = False
        audio_idx = -1            # which input provides the audio stream
        trim_out = None           # output -t option
        speed_factor = 1.0
        # silent track must match the actual video output duration,
        # otherwise the container gets padded to the longer audio
        silent_dur = target_dur if is_first else min(orig_dur, target_dur)

        # normalize audio for safe concat -c copy: constant 44.1k, async
        # resample kills MOV/iPhone priming-sample drift, apad guarantees
        # the track is at least as long as the video (then -t cuts exact)
        A_NORM = "aresample=44100:async=1:first_pts=0,apad"

        if is_first and orig_dur > target_dur:
            # ---------- SPEED UP (first segment too long) ----------
            speed_factor = orig_dur / target_dur
            cmd += ["-i", input_video]
            vf.append(f"[0:v]setpts=PTS/{speed_factor:.6f},{bg_chain}")
            if has_audio:
                vf.append(f"[0:a]{build_atempo(speed_factor)},{A_NORM}[a]")
                audio_idx = 0
        elif is_first and orig_dur < target_dur:
            # ---------- LOOP (first segment too short) ----------
            loop = max(0, int(math.ceil(target_dur / orig_dur)) - 1)
            cmd += ["-stream_loop", str(loop), "-i", input_video]
            vf.append(f"[0:v]{bg_chain}")
            if has_audio:
                vf.append(f"[0:a]{A_NORM}[a]")
                audio_idx = 0
            trim_out = target_dur
        else:
            # ---------- TRIM FROM END / use whole ----------
            if orig_dur > target_dur:
                ss = max(0.0, orig_dur - target_dur)
                cmd += ["-ss", f"{ss:.3f}"]
                trim_out = target_dur
            cmd += ["-i", input_video]
            vf.append(f"[0:v]{bg_chain}")
            if has_audio:
                vf.append(f"[0:a]{A_NORM}[a]")
                audio_idx = 0

        # silent track when audio requested but source has none
        if with_audio and audio_idx < 0:
            need_silent = True
            cmd += ["-f", "lavfi", "-t", f"{silent_dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            vf.append("[1:a]anull[a]")
            audio_idx = 1

        png_idx = 2 if need_silent else 1
        cmd += ["-loop", "1", "-i", text_png]
        vf.append(f"[{png_idx}:v]format=rgba[fg]")
        cs_opt = get_format_color_opt(ffmpeg_exe)
        fmt = f"format={pix}" + (":" + cs_opt if cs_opt else "")
        # text PNG is FULL canvas with the text already drawn at its absolute
        # position -> overlay at 0:0 (any other offset would shift it off-screen)
        uniq = _micro_uniq_chain(canvas_w, canvas_h) if micro_uniq else ""
        vf.append(f"[bg][fg]overlay=0:0:shortest=1,{uniq}{fmt}[v]")

        cmd += ["-filter_complex", ";".join(vf)]
        cmd += ["-map", "[v]"]
        if audio_idx >= 0:
            cmd += ["-map", "[a]"]
        else:
            cmd += ["-an"]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", pix, "-threads", "2"]
        if use10:
            cmd += ["-profile:v", "high10", "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709"]
        if audio_idx >= 0:
            cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k",
                    "-ar", "44100", "-ac", "2"]
        # -t is mandatory: apad makes the audio endless, and MOV sources
        # often report audio slightly longer than video — cut both exactly
        out_t = trim_out if trim_out else (target_dur if is_first
                                           else min(orig_dur, target_dur))
        cmd += ["-t", f"{out_t:.3f}"]
        # identical timebase in every segment -> concat -c copy never breaks
        cmd += ["-video_track_timescale", "90000"]
        cmd += ["-movflags", "+faststart", seg_out]

        if progress_cb:
            progress_cb(5)
        r = subprocess.run(cmd, capture_output=True, timeout=1800)
        if r.returncode != 0:
            return False, r.stderr.decode("utf-8", "replace")[-1500:]
        if not os.path.isfile(seg_out) or os.path.getsize(seg_out) < 1024:
            return False, "output file missing/small"
        if progress_cb:
            progress_cb(15)
        return True, ""
    except Exception as e:
        return False, str(e)


def concat_videos(seg_files: List[str], out_path: str, ffmpeg_exe: str,
                  with_audio: bool = True) -> Tuple[bool, str]:
    """Instant concat via demuxer (-c copy). Fallback: re-encode concat."""
    if not seg_files:
        return False, "no segments"
    tmp_list = os.path.join(os.path.dirname(out_path) or OUTPUT_DIR,
                            "concat_list.txt")
    try:
        with open(tmp_list, "w", encoding="utf-8") as f:
            for s in seg_files:
                f.write("file '" + s.replace("\\", "/").replace("'", "'\\''") + "'\n")
        # video: lossless stream copy. audio: cheap re-encode — AAC frames
        # are 1024 samples, so each segment's audio is a hair longer than
        # its video; pure -c copy then yields non-monotonic DTS (broken
        # timestamps, players stutter — especially with MOV sources).
        # aresample=async=1 re-times audio into one continuous track.
        cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
               "-fflags", "+genpts",
               "-f", "concat", "-safe", "0", "-i", tmp_list,
               "-c:v", "copy"]
        if with_audio:
            cmd += ["-af", "aresample=44100:async=1:first_pts=0",
                    "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
                    "-shortest"]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
            return True, ""
        # ----- fallback: re-encode filter concat -----
        n = len(seg_files)
        inputs: List[str] = []
        for s in seg_files:
            inputs += ["-i", s]
        fc_parts = []
        for i in range(n):
            fc_parts.append(f"[{i}:v]")
            if with_audio:
                fc_parts.append(f"[{i}:a]")
        fc = "".join(fc_parts) + f"concat=n={n}:v=1:a={1 if with_audio else 0}[vout]"
        if with_audio:
            fc += "[aout]"
        cmd2 = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
            "-filter_complex", fc, "-map", "[vout]"]
        if with_audio:
            cmd2 += ["-map", "[aout]"]
        cmd2 += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                 "-pix_fmt", "yuv420p"]
        if with_audio:
            cmd2 += ["-c:a", "aac", "-b:a", "192k"]
        cmd2 += ["-movflags", "+faststart", out_path]
        r2 = subprocess.run(cmd2, capture_output=True, timeout=1200)
        if r2.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 1024:
            return True, ""
        return False, (r.stderr or r2.stderr).decode("utf-8", "replace")[-1500:]
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.remove(tmp_list)
        except Exception:
            pass


# ======================================================================
#  ONE FINAL VIDEO — 5 parallel segments → concat
# ======================================================================

def build_one_final_ffmpeg(video_paths: List[str],
                           segment_presets: List[List[Dict[str, Any]]],
                           target_durs: List[float],
                           out_path: str,
                           ffmpeg_exe: str,
                           resolution: Tuple[int, int] = (1080, 1920),
                           fps: int = 30, crf: int = 18,
                           preset: str = "medium",
                           with_audio: bool = True,
                           uppercase: bool = False,
                           ten_bit: bool = False,
                           blur_fill: bool = False,
                           audio_kbps: int = 192,
                           random_flags: Optional[List[bool]] = None,
                           preset_indices: Optional[List[int]] = None,
                           micro_uniq: bool = False,
                           progress_cb=None) -> Tuple[bool, str]:
    """Full pipeline: render 5 segments in parallel, concat, cleanup.

    random_flags[i]=False -> use segment i's CURRENT preset (the one shown
    in the preview) instead of a random one.
    micro_uniq=True -> segments 2-5 (folder_2..folder_5 material) get an
    invisible random pixel-level uniquification, so every build has a
    different hash/fingerprint even from the same source files.
    """
    canvas_w, canvas_h = resolution
    temp = os.path.join(OUTPUT_DIR, f"_temp_{uuid.uuid4().hex[:8]}")
    os.makedirs(temp, exist_ok=True)
    seg_files: List[str] = []
    errors: List[str] = []

    def _seg_work(i: int, vp: str) -> Optional[str]:
        try:
            presets = segment_presets[i] or [dict(DEFAULT_PRESET)]
            if random_flags is not None and not random_flags[i]:
                idx = preset_indices[i] if preset_indices else 0
                p = dict(presets[min(max(0, idx), len(presets) - 1)])
            else:
                p = dict(random.choice(presets))
            raw = randomize_text(p.get("text", "Текст"))
            if uppercase:
                raw = uppercase_content(raw)
            rect = p.get("relative_rect") or dict(DEFAULT_PRESET["relative_rect"])
            fs = compute_font_size(raw, rect, canvas_w, canvas_h,
                                   p.get("font_scale", 1.0),
                                   scale_factor=0.75 if canvas_w < 1000 else 1.0)
            x = int(rect.get("x", 0.10) * canvas_w)
            y = int(rect.get("y", 0.12) * canvas_h)
            rw = max(1, int(rect.get("w", 0.80) * canvas_w))
            rh = max(1, int(rect.get("h", 0.22) * canvas_h))
            png = os.path.join(temp, f"text_{i}_{uuid.uuid4().hex[:6]}.png")
            if not create_colored_text_image(raw, canvas_w, canvas_h,
                                             (x, y, rw, rh), fs,
                                             p.get("stroke_width", 0), png):
                return None
            seg_out = os.path.join(temp, f"seg_{i}.mp4")
            orig_dur = get_video_duration(vp, ffmpeg_exe)
            ok, err = build_segment_ffmpeg(
                vp, png, x, y, target_durs[i], is_first=(i == 0), orig_dur=orig_dur,
                seg_out=seg_out, ffmpeg_exe=ffmpeg_exe, crf=crf, preset=preset,
                fps=fps, with_audio=with_audio, canvas_w=canvas_w, canvas_h=canvas_h,
                ten_bit=ten_bit, blur_fill=blur_fill, audio_kbps=audio_kbps,
                micro_uniq=(micro_uniq and i >= 1),
            )
            if not ok:
                errors.append(f"seg{i}: {err}")
                return None
            return seg_out
        except Exception as e:
            errors.append(f"seg{i}: {e}")
            return None

    try:
        n_workers = adaptive_segment_workers()
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futures = {ex.submit(_seg_work, i, vp): i
                       for i, vp in enumerate(video_paths)}
            done = 0
            for fut in as_completed(futures):
                res = fut.result()
                if res:
                    seg_files.append(res)
                done += 1
                if progress_cb:
                    progress_cb(int(done / len(video_paths) * 70))
        # sequential retry pass for segments that failed under load
        if len(seg_files) < len(video_paths):
            for i, vp in enumerate(video_paths):
                if any(os.path.abspath(s).endswith(f"seg_{i}.mp4") for s in seg_files):
                    continue
                res = _seg_work(i, vp)
                if res:
                    seg_files.append(res)
                if progress_cb:
                    progress_cb(int(len(seg_files) / len(video_paths) * 70))
        seg_files.sort(key=lambda s: int(re.search(r"seg_(\d+)", s).group(1)))
        if len(seg_files) < len(video_paths):
            return False, "; ".join(errors[:3]) or "segment render failed"
        if progress_cb:
            progress_cb(80)
        ok, err = concat_videos(seg_files, out_path, ffmpeg_exe, with_audio)
        if not ok:
            return False, err
        if progress_cb:
            progress_cb(95)
        return True, ""
    finally:
        shutil.rmtree(temp, ignore_errors=True)

# ======================================================================
#  TELEGRAM
# ======================================================================

def send_video_via_telegram(bot_token: str, chat_id: str, file_path: str,
                            caption: str = "") -> Tuple[bool, str]:
    """Send video as document (max quality) to a chat/group.

    Returns (ok, info). If the chat was upgraded to a supergroup the API
    returns migrate_to_chat_id — we auto-retry with the new id and report it
    ("migrated:<new_id>") so the app can persist the fix.
    """
    if not bot_token:
        return False, "нет токена бота"
    if not chat_id:
        return False, "нет Chat ID"
    if not os.path.isfile(file_path):
        return False, f"файл не найден: {file_path}"
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"

    def _post(cid: str) -> requests.Response:
        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f, "video/mp4")}
            data = {"chat_id": cid, "caption": caption or "",
                    "disable_notification": True}
            return requests.post(url, data=data, files=files, timeout=300)

    try:
        r = _post(chat_id)
        if r.ok:
            return True, "ok"
        # ---- chat upgraded: group -> supergroup, auto-migrate ----
        try:
            j = r.json()
            mig = j.get("parameters", {}).get("migrate_to_chat_id")
            if mig:
                new_id = str(mig)
                r2 = _post(new_id)
                if r2.ok:
                    return True, "migrated:" + new_id
                return False, r2.text[:200]
        except Exception:
            pass
        return False, r.text[:300]
    except Exception as e:
        return False, str(e)


def make_test_video(path: str) -> bool:
    """Generate a tiny 1s test video (color bars) for Telegram test-send."""
    try:
        ff = get_ffmpeg_exe()
        r = subprocess.run(
            [ff, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=s=320x568:d=1:r=24",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             path],
            capture_output=True, timeout=120)
        return r.returncode == 0 and os.path.isfile(path)
    except Exception:
        return False


def test_bot_token(token: str) -> Tuple[bool, str]:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=20)
        if r.ok:
            j = r.json().get("result", {})
            return True, f"@{j.get('username','')} ({j.get('first_name','')})"
        return False, r.text[:200]
    except Exception as e:
        return False, str(e)

# ======================================================================
#  FALLBACK — MoviePy pipeline (used if fast ffmpeg path fails)
# ======================================================================

def _call_any(obj: Any, names: Tuple[str, ...], *args, **kwargs) -> Any:
    """MoviePy 1.x/2.x compatible method call."""
    for n in names:
        m = getattr(obj, n, None)
        if m is not None:
            return m(*args, **kwargs)
    raise AttributeError(f"none of {names} found on {type(obj).__name__}")


def run_moviepy(video_paths: List[str],
                segment_presets: List[List[Dict[str, Any]]],
                target_durs: List[float], out_path: str,
                resolution: Tuple[int, int] = (1080, 1920),
                fps: int = 30, with_audio: bool = True,
                uppercase: bool = False,
                random_flags: Optional[List[bool]] = None,
                preset_indices: Optional[List[int]] = None,
                progress_cb=None) -> Tuple[bool, str]:
    if VideoFileClip is None:
        return False, "MoviePy not installed"
    try:
        from moviepy import concatenate_videoclips, CompositeVideoClip, ImageClip
        canvas_w, canvas_h = resolution
        temp = os.path.join(OUTPUT_DIR, f"_temp_{uuid.uuid4().hex[:8]}")
        os.makedirs(temp, exist_ok=True)
        clips = []
        try:
            for i, vp in enumerate(video_paths):
                presets = segment_presets[i] or [dict(DEFAULT_PRESET)]
                if random_flags is not None and not random_flags[i]:
                    idx = preset_indices[i] if preset_indices else 0
                    p = dict(presets[min(max(0, idx), len(presets) - 1)])
                else:
                    p = dict(random.choice(presets))
                raw = randomize_text(p.get("text", "Текст"))
                if uppercase:
                    raw = uppercase_content(raw)
                rect = p.get("relative_rect") or dict(DEFAULT_PRESET["relative_rect"])
                fs = compute_font_size(raw, rect, canvas_w, canvas_h,
                                       p.get("font_scale", 1.0),
                                       scale_factor=0.75 if canvas_w < 1000 else 1.0)
                x = int(rect.get("x", 0.10) * canvas_w)
                y = int(rect.get("y", 0.12) * canvas_h)
                rw = max(1, int(rect.get("w", 0.80) * canvas_w))
                rh = max(1, int(rect.get("h", 0.22) * canvas_h))
                png = os.path.join(temp, f"text_{i}.png")
                create_colored_text_image(raw, canvas_w, canvas_h, (x, y, rw, rh),
                                          fs, p.get("stroke_width", 0), png)
                clip = VideoFileClip(vp)
                # scale+center-crop to canvas
                clip = _call_any(clip, ("resized", "resize"), height=canvas_h)
                if clip.w > canvas_w:
                    x_crop = (clip.w - canvas_w) // 2
                    clip = _call_any(clip, ("cropped", "crop"),
                                     x1=x_crop, x2=x_crop + canvas_w)
                clip = _call_any(clip, ("resized", "resize"), (canvas_w, canvas_h))
                if i == 0 and clip.duration > target_durs[0]:
                    clip = _call_any(clip, ("with_speed_scaled", "with_speed", "speedx"),
                                     clip.duration / target_durs[0])
                if clip.duration > target_durs[i]:
                    clip = _call_any(clip, ("subclipped", "subclip"),
                                     clip.duration - target_durs[i],
                                     clip.duration)
                ic = ImageClip(png)
                ic = _call_any(ic, ("with_duration", "set_duration"), clip.duration)
                # full-canvas PNG, already positioned -> place at 0,0
                ic = _call_any(ic, ("with_position", "set_position"), (0, 0))
                comp = CompositeVideoClip([clip, ic], size=(canvas_w, canvas_h))
                clips.append(comp)
                if progress_cb:
                    progress_cb(int((i + 1) / len(video_paths) * 50))
            final = concatenate_videoclips(clips)
            final.write_videofile(
                out_path, fps=fps, codec="libx264", audio=with_audio,
                audio_codec="aac", preset="medium", threads=4,
                logger=None, bitrate=None, ffmpeg_params=["-crf", "18"],
            )
            return True, ""
        finally:
            for c in clips:
                try:
                    c.close()
                except Exception:
                    pass
            shutil.rmtree(temp, ignore_errors=True)
    except Exception as e:
        return False, str(e)

# ======================================================================
#  QSS — BEAUTIFUL DARK THEME
# ======================================================================

# ---- Design tokens (dark, blue-tinted greys, 4px grid) ----
# bg-base      #0E0F14   window background (not pure black)
# bg-surface-1 #171923   cards
# bg-surface-2 #1E212C   nested sections / inputs
# bg-surface-3 #282C3A   hover
# border       #262B37   subtle borders
# border-strong#3A4152   hover borders
# text-primary #E8EAF2
# text-secondary #9BA1B3
# text-muted   #6E7484
# accent       #5B7CFF -> #7A5CFF  (primary, calm indigo)
# accent-amber #E8A33D             (batch, muted amber)
# ok #4CC38A / err #E85D75

QSS = """
* { font-family: 'Segoe UI', 'SF Pro Display', 'Inter', 'Roboto', sans-serif; }
QWidget {
    background: #0E0F14;
    color: #E8EAF2;
    font-size: 13px;
}
QMainWindow, QDialog { background: #0E0F14; }
QToolTip {
    background: #1E212C; color: #E8EAF2;
    border: 1px solid #3A4152; border-radius: 8px; padding: 5px 9px;
}
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ================= CARDS ================= */
QFrame#Card {
    background: #171923;
    border: 1px solid #262B37;
    border-radius: 14px;
}
QFrame#CardHeader {
    background: #171923;
    border: 1px solid #262B37;
    border-radius: 14px;
}
QFrame#Section {
    background: #1E212C;
    border: 1px solid #262B37;
    border-radius: 10px;
}
QFrame#CharRow {
    background: #1E212C;
    border: 1px solid #262B37;
    border-radius: 10px;
}
QFrame#SegCard {
    background: #171923;
    border: 1px solid #262B37;
    border-radius: 16px;
}

/* ================= LABELS ================= */
QLabel#CardTitle { font-size: 13px; font-weight: 600; color: #F2F4F9; }
QLabel#CardSub { color: #9BA1B3; font-size: 11px; background: transparent; }
QLabel#Hint { color: #6E7484; font-size: 11px; background: transparent; }
QLabel#StatusOk { color: #4CC38A; font-weight: 600; }
QLabel#StatusBad { color: #E85D75; font-weight: 600; }
QLabel#Info { color: #B4B9C9; }
QLabel#BigTitle { font-size: 15px; font-weight: 700; color: #F2F4F9; }

/* ================= BUTTONS ================= */
QPushButton {
    background: #1E212C;
    color: #E8EAF2;
    border: 1px solid #303648;
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 22px;
}
QPushButton:hover { background: #282C3A; border-color: #3A4152; }
QPushButton:pressed { background: #242735; }
QPushButton:disabled { color: #5A6070; background: #16181F; border-color: #22252F; }

QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #5B7CFF, stop:1 #7A5CFF);
    color: #FFFFFF; font-weight: 600; font-size: 13px;
    border: none; border-radius: 9px; padding: 8px 20px;
    min-height: 24px;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #6D8AFF, stop:1 #8A6EFF);
}
QPushButton#Primary:disabled { background: #2A2E3D; color: #6E7484; }

QPushButton#BatchBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #E8A33D, stop:1 #F2B95C);
    color: #241A05; font-weight: 600; font-size: 13px;
    border: none; border-radius: 9px; padding: 8px 20px;
    min-height: 24px;
}
QPushButton#BatchBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #F0B04C, stop:1 #F7C36F);
}
QPushButton#BatchBtn:disabled { background: #3A3323; color: #8A7A50; }

QPushButton#SmallBtn { padding: 4px 10px; font-size: 12px; border-radius: 7px; min-height: 18px; }
QPushButton#Danger { color: #E85D75; border-color: #4A2A35; }
QPushButton#Danger:hover { background: #33202A; }

/* character selectable chips */
QPushButton#CharSelect {
    background: #1E212C; color: #B4B9C9;
    border: 1px solid #303648; border-radius: 10px;
    padding: 5px 12px; font-size: 12px;
}
QPushButton#CharSelect:hover { background: #282C3A; border-color: #3A4152; }
QPushButton#CharSelect:checked {
    background: #232B4A; color: #C9D4FF;
    border: 1px solid #5B7CFF; font-weight: 600;
}

/* preset tag chips */
QPushButton#TagChip {
    background: #1E212C; color: #B4B9C9;
    border: 1px solid #303648; border-radius: 9px;
    padding: 3px 10px; font-size: 11px; max-height: 24px;
}
QPushButton#TagChip:hover { background: #282C3A; border-color: #3A4152; }
QPushButton#TagChip:checked {
    background: #232B4A; color: #C9D4FF;
    border: 1px solid #5B7CFF; font-weight: 600;
}

/* ================= FILMSTRIP ================= */
QPushButton#ThumbBtn {
    background: #13151C; border: 2px solid #262B37;
    border-radius: 12px; padding: 0px;
}
QPushButton#ThumbBtn:hover { border-color: #3A4152; background: #16181F; }
QPushButton#ThumbBtn:checked { border: 2px solid #5B7CFF; background: #1A1D27; }
QPushButton#ThumbBtn:focus { outline: none; }

/* ================= INPUTS ================= */
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #13151C; color: #E8EAF2;
    border: 1px solid #303648; border-radius: 8px;
    padding: 6px 10px; selection-background-color: #3A4D8F;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #5B7CFF;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #1E212C; border: 1px solid #303648;
    selection-background-color: #2A3254; color: #E8EAF2;
    border-radius: 8px; padding: 4px;
}

QCheckBox { spacing: 8px; color: #B4B9C9; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid #303648; background: #13151C;
}
QCheckBox::indicator:hover { border-color: #3A4152; }
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #5B7CFF, stop:1 #7A5CFF);
    border-color: #5B7CFF;
}

QSlider::groove:horizontal { height: 4px; background: #262B37; border-radius: 2px; }
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5B7CFF, stop:1 #7A5CFF);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #E8EAF2; border: 1px solid #0E0F14;
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #FFFFFF; }

QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; }

/* sidebar tabs — subtle segmented control */
QTabWidget#SidebarTabs::pane { border: none; background: transparent; }
QTabWidget#SidebarTabs QTabBar {
    background: #13151C;
    border: 1px solid #262B37;
    border-radius: 10px;
    padding: 3px;
}
QTabWidget#SidebarTabs QTabBar::tab {
    background: transparent;
    color: #9BA1B3;
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
    margin: 0px 1px;
    font-size: 12px;
    font-weight: 500;
}
QTabWidget#SidebarTabs QTabBar::tab:hover { background: #1E212C; color: #E8EAF2; }
QTabWidget#SidebarTabs QTabBar::tab:selected {
    background: #232B4A; color: #C9D4FF;
    font-weight: 600;
}

/* ================= SCROLLBARS ================= */
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #303648; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3A4152; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: #303648; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }

QProgressBar {
    background: #13151C; border: 1px solid #262B37;
    border-radius: 6px; text-align: center; color: #E8EAF2;
    font-size: 11px; min-height: 16px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                                stop:0 #5B7CFF, stop:1 #7A5CFF);
    border-radius: 5px;
}

QSplitter::handle { background: #262B37; width: 1px; }
QGroupBox {
    border: 1px solid #262B37; border-radius: 10px; margin-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 5px;
    color: #9BA1B3; background: transparent;
}

/* round play/pause button on preview */
QPushButton#PlayBtn {
    background: rgba(14,15,20,0.6); color: #E8EAF2;
    border: 1px solid rgba(232,234,242,0.3); border-radius: 15px;
    font-size: 13px; padding: 0px;
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
}
QPushButton#PlayBtn:hover { background: rgba(91,124,255,0.85); border-color: #E8EAF2; }

QLabel#TimeBadge {
    background: rgba(14,15,20,0.6); color: #E8EAF2;
    border: 1px solid rgba(232,234,242,0.25); border-radius: 7px;
    padding: 2px 7px; font-size: 10px;
}
"""

# ======================================================================
#  FLOW LAYOUT (for chips)
# ======================================================================

class FlowLayout(QLayout):
    """Standard Qt flow layout, PyQt6 port."""
    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._items: List[Any] = []
        self._h = hspacing
        self._v = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        while self.count():
            item = self.takeAt(0)
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()

    def addItem(self, item): self._items.append(item)

    def count(self): return len(self._items)

    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        if 0 <= i < len(self._items):
            return self._items.pop(i)
        return None

    def expandingDirections(self): return Qt.Orientation(0)

    def hasHeightForWidth(self): return True

    def heightForWidth(self, width):
        return self._do_layout(QRectF(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(QRectF(rect), False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRectF, test_only: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_h = 0
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w
            if next_x > rect.right() - m.right() and line_h > 0:
                x = rect.x() + m.left()
                y = y + line_h + self._v
                next_x = x + w
                line_h = 0
            if not test_only:
                item.setGeometry(
                    QRectF(QPointF(x, y), QSizeF(float(w), float(h))).toRect())
            x = next_x + self._h
            line_h = max(line_h, h)
        return int(y + line_h - rect.y() + m.bottom())

# ======================================================================
#  TAG WIDGET — one preset chip
# ======================================================================

class TagWidget(QPushButton):
    clickedIdx = pyqtSignal(int)

    def __init__(self, index: int, preset: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.index = index
        self.setObjectName("TagChip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update(preset)
        self.clicked.connect(lambda: self.clickedIdx.emit(self.index))

    def _update(self, preset: Dict[str, Any]):
        text = str(preset.get("text", "Текст")).replace("\n", " ")
        if len(text) > 18:
            text = text[:16] + "…"
        icon = ""
        if has_color_tags(text):
            icon += " 🎨"
        if has_random_tags(text):
            icon += " 🎲"
        self.setText(text + icon)
        r = preset.get("relative_rect", {})
        self.setToolTip(
            f"#{self.index + 1} · x={r.get('x', 0):.2f} y={r.get('y', 0):.2f} "
            f"w={r.get('w', 0):.2f} h={r.get('h', 0):.2f}"
        )


class TagFlowWidget(QWidget):
    """Scrollable flow of preset chips — grows to fit rows, then scrolls."""
    tagClicked = pyqtSignal(int)

    MAX_H = 128          # 5 rows
    MIN_H = 44           # 1 row

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chips: List[TagWidget] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMinimumHeight(self.MIN_H)
        self._scroll.setMaximumHeight(self.MAX_H)
        self._scroll.setFixedHeight(self.MIN_H)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._inner = QWidget()
        self._flow = FlowLayout(self._inner, margin=0, hspacing=5, vspacing=5)
        self._scroll.setWidget(self._inner)
        lay.addWidget(self._scroll)

    def _fit_height(self):
        """Measure the real laid-out height of the chips flow and grow to fit
        (up to MAX_H, then scroll). Two passes so the measurement is exact."""
        if not self._chips:
            return
        try:
            # temp tall height so Qt lays out ALL rows
            self._scroll.setFixedHeight(self.MAX_H)
            QApplication.processEvents()
            inner_h = self._inner.height()
            need = inner_h + 4
            self._scroll.setFixedHeight(max(self.MIN_H, min(self.MAX_H, need)))
        except Exception:
            self._scroll.setFixedHeight(self.MIN_H)

    def set_presets(self, presets: List[Dict[str, Any]], current: int):
        for c in self._chips:
            self._flow.removeWidget(c)
            c.setVisible(False)
            c.setParent(None)
            c.deleteLater()
        self._chips = []
        for i, p in enumerate(presets):
            chip = TagWidget(i, p)
            chip.setChecked(i == current)
            chip.clickedIdx.connect(self._on_click)
            self._flow.addWidget(chip)
            self._chips.append(chip)
        QTimer.singleShot(0, self._fit_height)

    def set_current(self, idx: int):
        for i, c in enumerate(self._chips):
            c.setChecked(i == idx)

    def update_chip(self, idx: int, preset: Dict[str, Any]):
        if 0 <= idx < len(self._chips):
            self._chips[idx]._update(preset)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, self._fit_height)

    def _on_click(self, idx: int):
        self.tagClicked.emit(idx)

# ======================================================================
#  VIDEO PREVIEW PLAYER — plays the segment's actual video
# ======================================================================

PREV_W, PREV_H = 300, 534
HANDLE = 10
PINK = QColor("#ff6b81")


class _PlayerSignal(QObject):
    frame = pyqtSignal(object, float)          # (QImage | None, time)
    meta = pyqtSignal(float)                   # total duration


def _frame_to_qimage(frame: Any) -> Optional[QImage]:
    """numpy RGB frame (PREV_H, PREV_W, 3) -> small QImage."""
    try:
        from PIL import Image as _PILImage
        pil = _PILImage.fromarray(frame).convert("RGB")
        if pil.size != (PREV_W, PREV_H):
            pil = pil.resize((PREV_W, PREV_H), _PILImage.Resampling.BILINEAR)
        data = pil.tobytes("raw", "RGB")
        return QImage(data, PREV_W, PREV_H, PREV_W * 3,
                      QImage.Format.Format_RGB888).copy()
    except Exception:
        return None


class VideoPreviewWidget(QWidget):
    """9:16 preview that really plays the video (MoviePy frames in a
    background thread, looped, muted) + draggable text rectangle.

    Size-adaptive: the widget size is set by the parent (set_preview_size)
    and all painting/hit-testing uses self.width()/self.height() so nothing
    overflows in narrow or tall windows.
    """

    rectChanging = pyqtSignal()
    rectChanged = pyqtSignal()
    requestVideo = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(240, 427)
        self.setMaximumSize(300, 534)

        self._pix: Optional[QPixmap] = None
        self._video_path: str = ""
        self._total_dur: float = 0.0
        self._cur_t: float = 0.0
        self._playing = False
        self._econ = False
        self._thread = None
        self._stop_evt = threading.Event()
        self._sig = _PlayerSignal()
        self._sig.frame.connect(self._on_player_frame)
        self._sig.meta.connect(self._on_player_meta)

        self._rect = QRectF(0.10, 0.12, 0.80, 0.22)
        self._font_scale = 1.1
        self._stroke = 0
        self._text = "Текст"
        self._drag_mode = None
        self._drag_offset = QPointF()
        self._drag_start = QRectF()
        self._text_runs: List[Tuple[str, QColor]] = []

        # play/pause button + time badge
        self.btn_play = QPushButton("▶", self)
        self.btn_play.setObjectName("PlayBtn")
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.clicked.connect(self.toggle_play)
        self.time_badge = QLabel("0:00", self)
        self.time_badge.setObjectName("TimeBadge")
        self.set_preview_size(272)

    # ----------------------------------------------------- public API
    def set_preview_size(self, w: int):
        w = max(200, min(340, int(w)))
        self.setFixedSize(w, int(w * PREV_H / PREV_W))
        self._place_children()

    def _place_children(self):
        w, h = self.width(), self.height()
        self.btn_play.setFixedSize(34, 34)
        self.btn_play.move(w - 44, 8)
        self.btn_play.raise_()
        self.time_badge.adjustSize()
        self.time_badge.move(8, h - 30)
        self.time_badge.raise_()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_children()

    def set_relative_rect(self, rect: Dict[str, float]):
        self._rect = QRectF(
            float(rect.get("x", 0.10)), float(rect.get("y", 0.12)),
            float(rect.get("w", 0.80)), float(rect.get("h", 0.22)),
        )
        self.update()

    def get_relative_rect(self) -> Dict[str, float]:
        return {
            "x": round(self._rect.x(), 4), "y": round(self._rect.y(), 4),
            "w": round(self._rect.width(), 4), "h": round(self._rect.height(), 4),
        }

    def set_font_scale(self, fs: float):
        self._font_scale = max(0.2, float(fs)); self.update()

    def set_custom_stroke(self, sw: int):
        self._stroke = max(0, int(sw)); self.update()

    def set_preview_text(self, raw: str):
        self._text = raw or ""; self._rebuild_runs(); self.update()

    def set_econ(self, on: bool):
        self._econ = on
        if on:
            self.stop_playback()

    def _rebuild_runs(self):
        try:
            self._text_runs = [
                (t, QColor(c)) for t, c in parse_color_text(
                    randomize_text(self._text), default="white")
                if t.strip()
            ]
        except Exception:
            self._text_runs = [(self._text, QColor("#ffffff"))]

    # ---------------------------------------------------- video player
    def load_video(self, path: str, duration: float = 0.0):
        self.stop_playback()
        self._video_path = path or ""
        self._total_dur = duration or 0.0
        self._cur_t = 0.0
        self._pix = None
        if path and os.path.isfile(path) and not self._econ:
            self._start_player(path)
        self.update()

    def stop_playback(self):
        self._stop_evt.set()
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=1.5)
        self._thread = None
        self._playing = False
        self.btn_play.setText("▶")
        self._stop_evt = threading.Event()

    def toggle_play(self):
        if not self._video_path:
            self.requestVideo.emit()
            return
        if self._playing:
            self._playing = False
            self.btn_play.setText("▶")
        else:
            self._playing = True
            self.btn_play.setText("⏸")
            if self._thread is None or not self._thread.is_alive():
                self._start_player(self._video_path)
        self.update()

    def _start_player(self, path: str):
        self._stop_evt = threading.Event()
        self._playing = True
        self.btn_play.setText("⏸")

        def run():
            clip = None
            try:
                if VideoFileClip is None:
                    raise RuntimeError("MoviePy not available")
                clip = VideoFileClip(path)
                total = float(clip.duration)
                self._sig.meta.emit(total)
                try:
                    small = clip.resized((PREV_W, PREV_H))
                except Exception:
                    small = clip
                while not self._stop_evt.is_set():
                    for frame in small.iter_frames(fps=12, dtype="uint8"):
                        if self._stop_evt.is_set():
                            break
                        img = _frame_to_qimage(frame)
                        self._sig.frame.emit(img, 1.0 / 12.0)
                        time.sleep(0.025)
                    if self._stop_evt.is_set():
                        break
            except Exception as e:
                print("[preview] play error:", e)
                self._sig.frame.emit(None, 0.0)
            finally:
                if clip is not None:
                    try:
                        clip.close()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def _on_player_frame(self, img, t):
        if self._playing and img is not None:
            self._pix = QPixmap.fromImage(img)
            self._cur_t += t
            self.update()
        elif img is None and self._pix is None:
            self.update()

    def _on_player_meta(self, total: float):
        self._total_dur = total

    def _fmt(self, t: float) -> str:
        t = max(0.0, t)
        return f"{int(t // 60)}:{int(t % 60):02d}"

    def show_frame_at(self, img: QImage, t: float):
        if img is not None:
            self._pix = QPixmap.fromImage(img)
            self._cur_t = t
            self.update()

    # ------------------------------------------------------- painting
    def paintEvent(self, ev):
        W, H = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._pix is not None:
            p.drawPixmap(0, 0, W, H, self._pix)
        else:
            p.fillRect(self.rect(), QColor("#131328"))
            p.setPen(QPen(QColor("#5a5a92")))
            f = QFont(); f.setPixelSize(int(H * 0.09))
            p.setFont(f)
            p.drawText(QRectF(0, H * 0.28, W, H * 0.12),
                       Qt.AlignmentFlag.AlignCenter, "🎬")
            f2 = QFont(); f2.setPixelSize(int(H * 0.026)); f2.setBold(True)
            p.setFont(f2)
            p.setPen(QPen(QColor("#b9b7de")))
            if not self._video_path:
                p.drawText(QRectF(0, H * 0.40, W, H * 0.05),
                           Qt.AlignmentFlag.AlignCenter, "Видео не выбрано")
                f3 = QFont(); f3.setPixelSize(int(H * 0.022))
                p.setFont(f3)
                p.setPen(QPen(QColor("#6d6b99")))
                p.drawText(QRectF(0, H * 0.46, W, H * 0.04),
                           Qt.AlignmentFlag.AlignCenter,
                           "Нажми «📁 Выбрать видео» или 🎲")
            else:
                p.drawText(QRectF(0, H * 0.40, W, H * 0.05),
                           Qt.AlignmentFlag.AlignCenter,
                           "⏳ Загрузка видео…" if not self._econ
                           else "💾 Эконом-режим")

        # safe zones
        pen = QPen(QColor(255, 255, 255, 46))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(1)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(1, 1, W - 2, H - 2))

        # text rectangle
        r = QRectF(self._rect.x() * W, self._rect.y() * H,
                   self._rect.width() * W, self._rect.height() * H)
        pen = QPen(QColor("#ffffff")); pen.setWidth(2)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)

        hs = max(8, min(10, W // 30))
        p.setBrush(PINK); p.setPen(Qt.PenStyle.NoPen)
        for px, py in [
            (r.left(), r.top()), (r.center().x(), r.top()), (r.right(), r.top()),
            (r.left(), r.center().y()), (r.right(), r.center().y()),
            (r.left(), r.bottom()), (r.center().x(), r.bottom()), (r.right(), r.bottom()),
        ]:
            p.drawEllipse(QRectF(px - hs / 2, py - hs / 2, hs, hs))

        self._draw_preview_text(p, r)

        self.time_badge.setText(
            f"{self._fmt(self._cur_t)} / {self._fmt(self._total_dur)}")
        self._place_children()

    def _draw_preview_text(self, p: QPainter, rect: QRectF):
        if not self._text_runs:
            return
        # UNIFIED size: same formula + clamps as the final render,
        # so what you see in the preview is exactly what gets burned in.
        W, H = self.width(), self.height()
        rendered = " ".join(t for t, _ in self._text_runs) or self._text
        size = compute_text_px_size(
            rendered,
            {"w": rect.width() / W, "h": rect.height() / H,
             "x": 0, "y": 0},
            W, H, self._font_scale)
        family = "Anton" if not any(CYRILLIC_RE.search(t) for t, _ in self._text_runs) else "Oswald"
        fnt = QFont(family); fnt.setPixelSize(int(size))
        fm = QFontMetrics(fnt)
        lines: List[List[Tuple[str, QColor, int]]] = []
        cur: List[Tuple[str, QColor, int]] = []
        cur_w = 0
        max_w = int(rect.width() * 0.97)
        space_w = fm.horizontalAdvance(" ")
        for text, color in self._text_runs:
            for pi, part in enumerate(text.split("\n")):
                if pi > 0 and cur:
                    lines.append(cur); cur, cur_w = [], 0
                for word in part.split(" "):
                    if not word:
                        continue
                    ww = fm.horizontalAdvance(word)
                    if cur and cur_w + space_w + ww > max_w and ww <= max_w:
                        lines.append(cur); cur, cur_w = [], 0
                    if not cur:
                        cur = [(word, color, ww)]; cur_w = ww
                    else:
                        cur.append((word, color, ww)); cur_w += space_w + ww
        if cur:
            lines.append(cur)
        line_h = fm.height() + 6
        total_h = line_h * len(lines)
        yy = rect.center().y() - total_h / 2.0
        for ln in lines:
            lw = sum(x[2] for x in ln) + space_w * max(0, len(ln) - 1)
            xx = rect.center().x() - lw / 2.0
            for word, color, ww in ln:
                if self._stroke > 0:
                    path = QPainterPath()
                    path.addText(QPointF(xx, yy + fm.ascent()), fnt, word)
                    p.setPen(QPen(QColor("#000000"), self._stroke * 2,
                                  Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                  Qt.PenJoinStyle.RoundJoin))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
                p.setPen(QPen(color)); p.setFont(fnt)
                p.drawText(QPointF(xx, yy + fm.ascent()), word)
                xx += ww + space_w
            yy += line_h

    # ------------------------------------------------------ mouse / keys
    def _hit_test(self, pos: QPointF) -> Optional[str]:
        W, H = self.width(), self.height()
        r = QRectF(self._rect.x() * W, self._rect.y() * H,
                   self._rect.width() * W, self._rect.height() * H)
        hs = max(8, min(10, W // 30)) + 2
        for name, pt in {
            "tl": QPointF(r.left(), r.top()), "tr": QPointF(r.right(), r.top()),
            "bl": QPointF(r.left(), r.bottom()), "br": QPointF(r.right(), r.bottom()),
        }.items():
            if (pos - pt).manhattanLength() <= hs:
                return name
        if r.adjusted(-6, -6, 6, 6).contains(pos):
            return "move"
        return None

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            if not self._video_path:
                self.requestVideo.emit()
                return
            self.setFocus()
            self._drag_mode = self._hit_test(ev.position())
            self._drag_offset = ev.position()
            self._drag_start = QRectF(self._rect)

    def mouseMoveEvent(self, ev):
        if self._drag_mode is None:
            return
        W, H = self.width(), self.height()
        delta = ev.position() - self._drag_offset
        if self._drag_mode == "move":
            self._rect = QRectF(
                self._drag_start.x() + delta.x() / W,
                self._drag_start.y() + delta.y() / H,
                self._drag_start.width(), self._drag_start.height())
        else:
            r = QRectF(self._drag_start)
            dx = delta.x() / W; dy = delta.y() / H
            if "l" in self._drag_mode:
                r.setLeft(min(r.right() - 0.04, r.left() + dx))
            if "r" in self._drag_mode:
                r.setRight(max(r.left() + 0.04, r.right() + dx))
            if "t" in self._drag_mode:
                r.setTop(min(r.bottom() - 0.04, r.top() + dy))
            if "b" in self._drag_mode:
                r.setBottom(max(r.top() + 0.04, r.bottom() + dy))
            self._rect = r
        self._clamp()
        self.rectChanging.emit()
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag_mode is not None:
            self._drag_mode = None
            self.rectChanged.emit()
            self.update()

    def keyPressEvent(self, ev):
        W, H = self.width(), self.height()
        step = 5.0 if (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 1.0
        if ev.key() == Qt.Key.Key_Left:
            self._rect.moveLeft(self._rect.left() + step / W)
        elif ev.key() == Qt.Key.Key_Right:
            self._rect.moveLeft(self._rect.left() - step / W)
        elif ev.key() == Qt.Key.Key_Up:
            self._rect.moveTop(self._rect.top() + step / H)
        elif ev.key() == Qt.Key.Key_Down:
            self._rect.moveTop(self._rect.top() - step / H)
        else:
            super().keyPressEvent(ev)
            return
        self._clamp()
        self.rectChanging.emit()
        self.rectChanged.emit()
        self.update()

    def _clamp(self):
        r = self._rect
        r.setLeft(max(0.0, min(0.99, r.left())))
        r.setTop(max(0.0, min(0.99, r.top())))
        r.setWidth(max(0.04, min(1.0 - r.left(), r.width())))
        r.setHeight(max(0.04, min(1.0 - r.top(), r.height())))
        self._rect = r


THUMB_W, THUMB_H = 118, 210   # aspect reference for the filmstrip


class SegmentThumb(QPushButton):
    def __init__(self, index: int):
        super().__init__()
        self.index = index
        self.setObjectName("ThumbBtn")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pix: Optional[QPixmap] = None
        self._name = "—"
        self._dur = ""
        self._has_video = False

    def set_thumb(self, img: Optional[QImage], name: str = "", dur: float = 0.0):
        self._has_video = img is not None
        self._pix = QPixmap.fromImage(img) if img is not None else None
        if name:
            self._name = truncate_middle(os.path.basename(name), 14)
        if dur > 0:
            self._dur = f"{int(dur // 60)}:{int(dur % 60):02d}"
        self.update()

    def paintEvent(self, ev):
        W, H = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 10, 10)
        p.setClipPath(path)

        if self._pix is not None:
            p.drawPixmap(r, self._pix)
        else:
            p.fillRect(r, QColor("#151530"))
            f = QFont(); f.setPixelSize(int(H * 0.17))
            p.setFont(f)
            p.setPen(QPen(QColor("#5a5a92")))
            p.drawText(QRectF(r), Qt.AlignmentFlag.AlignCenter,
                       "🎬" if not self._has_video else "⏳")

        # bottom gradient
        g = QLinearGradient(0, r.height() * 0.55, 0, r.height())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(5, 5, 14, 220))
        p.fillRect(r, g)

        # number badge
        bd = max(18, min(24, int(W * 0.20)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#e94560"))
        p.drawEllipse(QRectF(r.x() + 6, r.y() + 6, bd, bd))
        f = QFont(); f.setPixelSize(int(bd * 0.55)); f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(QRectF(r.x() + 6, r.y() + 6, bd, bd),
                   Qt.AlignmentFlag.AlignCenter, str(self.index + 1))

        # name + duration
        f = QFont(); f.setPixelSize(max(9, int(H * 0.055))); f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(QRectF(r.x() + 6, r.height() - int(H * 0.22), r.width() - 12,
                          int(H * 0.10)),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._name)
        f2 = QFont(); f2.setPixelSize(max(8, int(H * 0.05)))
        p.setFont(f2)
        p.setPen(QPen(QColor("#b9b7de")))
        p.drawText(QRectF(r.x() + 6, r.height() - int(H * 0.11), r.width() - 12,
                          int(H * 0.09)),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._dur or "0:00")

        # play icon
        if self._has_video:
            pc = max(18, min(24, int(W * 0.20)))
            p.setPen(QPen(QColor(255, 255, 255, 200)))
            p.setBrush(QColor(10, 10, 22, 120))
            p.drawEllipse(QRectF(r.width() - pc - 8, 6, pc, pc))
            f3 = QFont(); f3.setPixelSize(int(pc * 0.5))
            p.setFont(f3)
            p.setPen(QPen(QColor("#ffffff")))
            p.drawText(QRectF(r.width() - pc - 8, 6, pc, pc),
                       Qt.AlignmentFlag.AlignCenter, "▶")


class SegmentFilmstrip(QWidget):
    """Row of 5 mini-previews that scale with the available width."""
    segmentSelected = pyqtSignal(int)

    THUMB_MIN = 84
    THUMB_MAX = 122

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(6)
        self._cap = QLabel("Сегменты")
        self._cap.setObjectName("CardTitle")
        lay.addWidget(self._cap)
        self._row = QHBoxLayout()
        self._row.setSpacing(10)
        self.thumbs: List[SegmentThumb] = []
        for i in range(5):
            t = SegmentThumb(i)
            t.clicked.connect(lambda _, k=i: self.segmentSelected.emit(k))
            self._row.addWidget(t)
            self.thumbs.append(t)
        self._row.addStretch(1)
        lay.addLayout(self._row)

    def set_active(self, idx: int):
        for i, t in enumerate(self.thumbs):
            t.setChecked(i == idx)

    def set_thumb(self, idx: int, img: Optional[QImage], name: str = "", dur: float = 0.0):
        if 0 <= idx < len(self.thumbs):
            self.thumbs[idx].set_thumb(img, name, dur)

    def _fit(self):
        """Fixed thumbnail size (no stretching); centered in the strip."""
        avail = max(420, self.width() - 60)
        tw = min(104, max(self.THUMB_MIN, (avail - 4 * 12) // 5))
        th = int(tw * THUMB_H / THUMB_W)
        for t in self.thumbs:
            t.setFixedSize(tw, th)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()


class _ThumbLoader(QObject):
    loaded = pyqtSignal(int, object, str, float)   # idx, QImage, name, dur

class _ThumbLoader(QObject):
    loaded = pyqtSignal(int, object, str, float)   # idx, QImage, name, dur


_thumb_semaphore = threading.BoundedSemaphore(1)   # one ffmpeg thumb at a time


def load_thumb_async(idx: int, path: str, loader: _ThumbLoader):
    """Grab the first frame with a single low-res ffmpeg call (cheap RAM)."""
    def run():
        img = None
        name = ""
        dur = 0.0
        try:
            if path and os.path.isfile(path):
                name = os.path.basename(path)
                ff = get_ffmpeg_exe()
                dur = get_video_duration(path, ff)
                with _thumb_semaphore:
                    r = subprocess.run(
                        [ff, "-hide_banner", "-loglevel", "error", "-ss", "0.2",
                         "-i", path, "-frames:v", "1",
                         "-vf", f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,"
                                f"crop={THUMB_W}:{THUMB_H}",
                         "-f", "image2pipe", "-c:v", "png", "-"],
                        capture_output=True, timeout=30)
                if r.returncode == 0 and r.stdout:
                    from PIL import Image as _PIL
                    pil = _PIL.open(io.BytesIO(r.stdout)).convert("RGB")
                    data = pil.tobytes("raw", "RGB")
                    qimg = QImage(data, pil.size[0], pil.size[1],
                                  pil.size[0] * 3,
                                  QImage.Format.Format_RGB888).copy()
                    img = qimg
        except Exception:
            img = None
        loader.loaded.emit(idx, img, name, dur)
    threading.Thread(target=run, daemon=True).start()

# ======================================================================
#  SEGMENT CARD
# ======================================================================

class SegmentCard(QFrame):
    configChanged = pyqtSignal()

    def __init__(self, index: int, main_window=None):
        super().__init__()
        self.index = index
        self.main = main_window
        self.setObjectName("SegCard")
        self.setMinimumWidth(340)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.video_path: str = ""
        self.duration: float = 5.0
        self.presets: List[Dict[str, Any]] = [dict(DEFAULT_PRESET)]
        self.current_idx: int = 0
        self.econ = False            # economy (no preview frames)
        self.scrub_enabled = True
        self._orig_dur: float = 0.0

        # preview frame loading (scrub)
        self._loading = False
        self._pending_t: Optional[float] = None
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.timeout.connect(self._do_scrub)
        self._frame_sig = _FrameSignal()
        self._frame_sig.result.connect(self._on_frame)

        self._build_ui()
        self._load_preset_into_ui(block=True)

    # ------------------------------------------------------------ ui --
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(12)

        # ================= HEADER =================
        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel(str(self.index + 1))
        badge.setObjectName("SegBadge")
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "QLabel#SegBadge { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 #e94560, stop:1 #ff6b81); color: white; font-size: 16px;"
            " font-weight: 800; border-radius: 17px; }")
        head.addWidget(badge)
        tbox = QVBoxLayout()
        tbox.setSpacing(1)
        title = QLabel(f"Сегмент {self.index + 1}")
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 16px;")
        tbox.addWidget(title)
        self.video_label = QLabel("видео не выбрано")
        self.video_label.setObjectName("CardSub")
        self.video_label.setToolTip("")
        self.video_label.setMinimumWidth(60)
        tbox.addWidget(self.video_label)
        head.addLayout(tbox)
        head.addStretch(1)
        self.btn_rand_video = QPushButton("Случайное")
        self.btn_rand_video.setObjectName("SmallBtn")
        self.btn_rand_video.setToolTip("Случайное видео из папки folder_%d" % (self.index + 1))
        self.btn_rand_video.clicked.connect(self._random_video)
        self.btn_pick = QPushButton("Выбрать видео")
        self.btn_pick.setObjectName("SmallBtn")
        self.btn_pick.clicked.connect(self._pick_video)
        head.addWidget(self.btn_rand_video)
        head.addWidget(self.btn_pick)
        root.addLayout(head)

        # ================= PREVIEW + SCRUB =================
        prev_frame = QFrame()
        prev_frame.setObjectName("Section")
        pvl = QVBoxLayout(prev_frame)
        pvl.setContentsMargins(12, 12, 12, 10)
        pvl.setSpacing(8)
        pvrow = QHBoxLayout()
        pvrow.addStretch(1)
        self.preview = VideoPreviewWidget()
        self.preview.rectChanging.connect(self._on_rect_changing)
        self.preview.rectChanged.connect(self._on_rect_changed)
        self.preview.requestVideo.connect(self._pick_video)
        pvrow.addWidget(self.preview)
        pvrow.addStretch(1)
        pvl.addLayout(pvrow)

        scrub_row = QHBoxLayout()
        scrub_row.setSpacing(8)
        scrub_row.addWidget(QLabel("⏩"))
        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 1000)
        self.scrub_slider.setValue(0)
        self.scrub_slider.valueChanged.connect(self._on_scrub)
        scrub_row.addWidget(self.scrub_slider, 1)
        self.chk_scrub = QCheckBox("Скраб")
        self.chk_scrub.setChecked(True)
        self.chk_scrub.toggled.connect(self._on_scrub_toggled)
        scrub_row.addWidget(self.chk_scrub)
        pvl.addLayout(scrub_row)

        dur_row = QHBoxLayout()
        dur_row.setSpacing(8)
        dur_row.addWidget(QLabel("Длительность:"))
        self.dur_spin = QDoubleSpinBox()
        self.dur_spin.setRange(0.5, 60.0)
        self.dur_spin.setSingleStep(0.5)
        self.dur_spin.setDecimals(2)
        self.dur_spin.setValue(5.0)
        self.dur_spin.setFixedWidth(95)
        self.dur_spin.setSuffix(" с")
        self.dur_spin.valueChanged.connect(self._on_duration_changed)
        dur_row.addWidget(self.dur_spin)
        self.effective_label = QLabel("")
        self.effective_label.setObjectName("Hint")
        dur_row.addWidget(self.effective_label, 1)
        pvl.addLayout(dur_row)
        root.addWidget(prev_frame)

        # ================= TEXT PRESETS =================
        pres_frame = QFrame()
        pres_frame.setObjectName("Section")
        pl = QVBoxLayout(pres_frame)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(7)

        pbar = QHBoxLayout()
        pbar.setSpacing(6)
        pbar.addWidget(QLabel("Тексты:"))
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setObjectName("SmallBtn")
        self.btn_prev.setFixedWidth(30)
        self.btn_prev.setToolTip("Предыдущий текст")
        self.btn_prev.clicked.connect(lambda: self.switch_preset(self.current_idx - 1))
        self.btn_next = QPushButton("▶")
        self.btn_next.setObjectName("SmallBtn")
        self.btn_next.setFixedWidth(30)
        self.btn_next.setToolTip("Следующий текст")
        self.btn_next.clicked.connect(lambda: self.switch_preset(self.current_idx + 1))
        self.preset_counter = QLabel("1/1")
        self.preset_counter.setObjectName("Hint")
        self.chk_random = QCheckBox("🎲 Случайный")
        self.chk_random.setChecked(True)
        self.chk_random.setToolTip(
            "При сборке брать случайный текст из списка.\n"
            "Сними галочку — чтобы всегда собирался именно этот текст "
            "(тот, что виден в превью).")
        self.chk_random.toggled.connect(self._on_random_toggled)
        pbar.addWidget(self.chk_random)
        self.btn_add = QPushButton("+ Добавить")
        self.btn_add.setObjectName("SmallBtn")
        self.btn_add.clicked.connect(self._add_preset)
        self.btn_copypos = QPushButton("Копир. позицию")
        self.btn_copypos.setObjectName("SmallBtn")
        self.btn_copypos.setToolTip("Скопировать позицию/размер/обводку на все тексты")
        self.btn_copypos.clicked.connect(self._copy_position_all)
        self.btn_del = QPushButton("Удалить")
        self.btn_del.setObjectName("SmallBtn")
        self.btn_del.setToolTip("Удалить текущий текст")
        self.btn_del.clicked.connect(self._del_preset)
        pbar.addWidget(self.btn_prev)
        pbar.addWidget(self.btn_next)
        pbar.addWidget(self.preset_counter)
        pbar.addStretch(1)
        pbar.addWidget(self.btn_add)
        pbar.addWidget(self.btn_copypos)
        pbar.addWidget(self.btn_del)
        pl.addLayout(pbar)

        self.tag_flow = TagFlowWidget()
        self.tag_flow.tagClicked.connect(self._on_tag_clicked)
        pl.addWidget(self.tag_flow)
        self.random_hint = QLabel("")
        self.random_hint.setObjectName("CardSub")
        self.random_hint.setWordWrap(True)
        pl.addWidget(self.random_hint)
        root.addWidget(pres_frame)

        # ================= TEXT EDITOR =================
        ed_frame = QFrame()
        ed_frame.setObjectName("Section")
        edl = QVBoxLayout(ed_frame)
        edl.setContentsMargins(12, 10, 12, 10)
        edl.setSpacing(6)
        edl.addWidget(QLabel("Текст (Enter — перенос строки · Ctrl+Enter — новый)"))
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "Текст сегмента…\n"
            "Цвет: [blue]текст[/blue] · Рандом: {rand:1.1-2.5} · {rand:25-100} · $2.1k")
        self.text_edit.setFixedHeight(74)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.textChanged.connect(self._on_text_changed)
        edl.addWidget(self.text_edit)

        qr = QHBoxLayout()
        qr.setSpacing(5)
        for label, txt in [
            ("$1k 1.1-2.5", "{rand:1.1-2.5}"),
            ("25-100", "{rand:25-100}"),
            ("шаг 0.1", "{rand:1.1-2.5:0.1}"),
        ]:
            b = QPushButton(label)
            b.setObjectName("SmallBtn")
            b.setToolTip("Вставить: " + txt)
            b.clicked.connect(lambda _, t=txt: self._insert_text(t))
            qr.addWidget(b)
        self.btn_range = QPushButton("Диапазон…")
        self.btn_range.setObjectName("SmallBtn")
        self.btn_range.clicked.connect(self._range_dialog)
        self.btn_shuffle = QPushButton("Перегенерить")
        self.btn_shuffle.setObjectName("SmallBtn")
        self.btn_shuffle.setToolTip("Пересоздать случайные числа в превью")
        self.btn_shuffle.clicked.connect(self._reshuffle)
        qr.addWidget(self.btn_range)
        qr.addWidget(self.btn_shuffle)
        qr.addStretch(1)
        edl.addLayout(qr)

        colors = ["blue", "red", "yellow", "green", "cyan", "pink",
                  "orange", "purple", "white", "black"]
        crow = QHBoxLayout()
        crow.setSpacing(5)
        crow.addWidget(QLabel("Цвет:"))
        self._color_btns = []
        for name in colors:
            b = QPushButton()
            b.setObjectName("ColorBtn")
            b.setStyleSheet(f"QPushButton#ColorBtn {{ background: {COLOR_MAP[name]}; }}")
            b.setToolTip(f"[{name}]…[/{name}]")
            b.clicked.connect(lambda _, n=name: self._insert_color(n))
            self._color_btns.append(b)
            crow.addWidget(b)
        crow.addStretch(1)
        edl.addLayout(crow)

        # ---- вторая строка: оттенки синего ----
        blues = ["electric", "royal", "neonblue", "deepblue",
                 "ultramarine", "indigo", "navy", "azure", "sky", "babyblue"]
        brow = QHBoxLayout()
        brow.setSpacing(5)
        brow.addWidget(QLabel("Синие:"))
        for name in blues:
            b = QPushButton()
            b.setObjectName("ColorBtn")
            b.setStyleSheet(f"QPushButton#ColorBtn {{ background: {COLOR_MAP[name]}; }}")
            b.setToolTip(f"[{name}]…[/{name}]  {COLOR_MAP[name]}")
            b.clicked.connect(lambda _, n=name: self._insert_color(n))
            self._color_btns.append(b)
            brow.addWidget(b)
        brow.addStretch(1)
        edl.addLayout(brow)
        root.addWidget(ed_frame)

        # ================= POSITION / SIZE =================
        pos_frame = QFrame()
        pos_frame.setObjectName("Section")
        pgl = QGridLayout(pos_frame)
        pgl.setContentsMargins(12, 10, 12, 10)
        pgl.setHorizontalSpacing(10)
        pgl.setVerticalSpacing(6)
        pgl.addWidget(QLabel("Позиция (0–1)"), 0, 0, 1, 4)
        self.sp_x = QDoubleSpinBox(); self.sp_y = QDoubleSpinBox()
        self.sp_w = QDoubleSpinBox(); self.sp_h = QDoubleSpinBox()
        for i, (sp, label) in enumerate([(self.sp_x, "X"), (self.sp_y, "Y"),
                                         (self.sp_w, "W"), (self.sp_h, "H")]):
            sp.setRange(0.0, 1.0); sp.setDecimals(3); sp.setSingleStep(0.01)
            sp.valueChanged.connect(self._on_spin_changed)
            pgl.addWidget(QLabel(label), 1, i * 2)
            pgl.addWidget(sp, 1, i * 2 + 1)
        pgl.setColumnStretch(8, 1)

        pgl.addWidget(QLabel("Размер:"), 2, 0)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(20, 250)
        self.size_slider.setValue(110)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        pgl.addWidget(self.size_slider, 2, 1, 1, 6)
        self.size_val = QLabel("1.10")
        self.size_val.setObjectName("Hint")
        self.size_val.setFixedWidth(40)
        pgl.addWidget(self.size_val, 2, 7)

        pgl.addWidget(QLabel("Обводка:"), 3, 0)
        self.stroke_slider = QSlider(Qt.Orientation.Horizontal)
        self.stroke_slider.setRange(0, 12)
        self.stroke_slider.setValue(0)
        self.stroke_slider.valueChanged.connect(self._on_stroke_changed)
        pgl.addWidget(self.stroke_slider, 3, 1, 1, 6)
        self.stroke_val = QLabel("0")
        self.stroke_val.setObjectName("Hint")
        self.stroke_val.setFixedWidth(40)
        pgl.addWidget(self.stroke_val, 3, 7)
        root.addWidget(pos_frame)

        root.addStretch(1)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        try:
            # keep the preview at a fixed professional size (no stretching);
            # the card is inside a scroll area, so it never gets squeezed
            self.preview.set_preview_size(272)
        except Exception:
            pass

    # ----------------------------------------------------- preset mgmt
    def current_preset(self) -> Dict[str, Any]:
        if not self.presets:
            self.presets = [dict(DEFAULT_PRESET)]
            self.current_idx = 0
        return self.presets[self.current_idx]

    def _on_random_toggled(self, _=None):
        self._emit_config()

    def _add_preset(self):
        base = dict(self.current_preset())
        base["text"] = self.text_edit.toPlainText() or "Новый текст"
        self.presets.append(base)
        self.current_idx = len(self.presets) - 1
        self._refresh_tags()
        self._load_preset_into_ui(block=True)
        self._emit_config()

    def _del_preset(self):
        if len(self.presets) <= 1:
            QMessageBox.information(self, APP_NAME, "Хотя бы один текст обязателен.")
            return
        del self.presets[self.current_idx]
        self.current_idx = max(0, min(self.current_idx, len(self.presets) - 1))
        self._refresh_tags()
        self._load_preset_into_ui(block=True)
        self._emit_config()

    def switch_preset(self, idx: int):
        idx = idx % len(self.presets)
        self.current_idx = idx
        self._refresh_tags()
        self._load_preset_into_ui(block=True)

    def _on_tag_clicked(self, idx: int):
        self.switch_preset(idx)

    def _refresh_tags(self):
        self.tag_flow.set_presets(self.presets, self.current_idx)
        self.preset_counter.setText(f"{self.current_idx + 1}/{len(self.presets)}")
        if hasattr(self, "random_hint"):
            if len(self.presets) > 1:
                self.random_hint.setText(
                    f"🎲 При сборке берётся 1 случайный текст из {len(self.presets)}")
            else:
                self.random_hint.setText("")

    def _copy_position_all(self):
        cur = self.current_preset()
        for p in self.presets:
            p["relative_rect"] = dict(cur.get("relative_rect", {}))
            p["font_scale"] = cur.get("font_scale", 1.1)
            p["stroke_width"] = cur.get("stroke_width", 0)
        self._emit_config()
        self._refresh_tags()

    def _load_preset_into_ui(self, block: bool = False):
        p = self.current_preset()
        ws = [self.text_edit, self.dur_spin, self.size_slider,
              self.stroke_slider, self.sp_x, self.sp_y, self.sp_w, self.sp_h]
        if block:
            for w in ws:
                w.blockSignals(True)
        self.text_edit.setPlainText(p.get("text", ""))
        self.size_slider.setValue(int(p.get("font_scale", 1.1) * 100))
        self.stroke_slider.setValue(p.get("stroke_width", 0))
        r = p.get("relative_rect", {})
        self.sp_x.setValue(r.get("x", 0.10))
        self.sp_y.setValue(r.get("y", 0.12))
        self.sp_w.setValue(r.get("w", 0.80))
        self.sp_h.setValue(r.get("h", 0.22))
        self.preview.set_relative_rect(r)
        self.preview.set_font_scale(p.get("font_scale", 1.1))
        self.preview.set_custom_stroke(p.get("stroke_width", 0))
        self.preview.set_preview_text(self._caps(p.get("text", "")))
        if block:
            for w in ws:
                w.blockSignals(False)
        self._update_size_labels()
        self._refresh_tags()

    # ------------------------------------------------------ text edits
    def _caps(self, raw: str) -> str:
        """Apply CAPS to match the final render when the export option is on."""
        try:
            if self.main and self.main.export_card.chk_caps.isChecked():
                return uppercase_content(raw)
        except Exception:
            pass
        return raw

    def _refresh_preview_text(self):
        self.preview.set_preview_text(
            self._caps(self.text_edit.toPlainText()))

    def _on_text_changed(self):
        self.current_preset()["text"] = self.text_edit.toPlainText()
        self._refresh_preview_text()
        self.tag_flow.update_chip(self.current_idx, self.current_preset())
        self._emit_config()

    def _insert_text(self, t: str):
        cur = self.text_edit.textCursor()
        cur.insertText(t)
        self._reshuffle()

    def _insert_color(self, name: str):
        cur = self.text_edit.textCursor()
        sel = cur.selectedText()
        if sel:
            cur.insertText(f"[{name}]{sel}[/{name}]")
        else:
            cur.insertText(f"[{name}]текст[/{name}]")
        self._reshuffle()

    def _reshuffle(self):
        self._refresh_preview_text()
        self.preview.update()

    def _range_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("⚙️ Диапазон случайных чисел")
        dlg.setMinimumWidth(300)
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        mn = QDoubleSpinBox(); mn.setRange(-1e6, 1e6); mn.setValue(1.1); mn.setDecimals(3)
        mx = QDoubleSpinBox(); mx.setRange(-1e6, 1e6); mx.setValue(2.5); mx.setDecimals(3)
        st = QDoubleSpinBox(); st.setRange(0.0, 1e6); st.setValue(0.0); st.setDecimals(3)
        form.addRow("Мин:", mn)
        form.addRow("Макс:", mx)
        form.addRow("Шаг (0 = авто):", st)
        lay.addLayout(form)
        hint = QLabel("Пример: {rand:1.1-2.5} → случайное число 1.1 … 2.5\n"
                      "Целые числа: {rand:25-100}\nК-формат: $2.1k — значения ≥1000 → '1k'/'2.1k'")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            step = f":{st.value():g}" if st.value() > 0 else ""
            tag = f"{{rand:{mn.value():g}-{mx.value():g}{step}}}"
            self._insert_text(tag)

    # ------------------------------------------------------- duration
    def _on_duration_changed(self, v: float):
        self.duration = float(v)
        self._update_effective()
        self._emit_config()

    def _update_effective(self):
        if self.index > 0 and self._orig_dur and self._orig_dur < self.duration:
            self.effective_label.setText(
                f"(исходное короче — будет целиком {self._orig_dur:.1f}с)")
        else:
            self.effective_label.setText("")

    def _on_rect_changing(self):
        r = self.preview.get_relative_rect()
        ws = [self.sp_x, self.sp_y, self.sp_w, self.sp_h]
        for w in ws:
            w.blockSignals(True)
        self.sp_x.setValue(r["x"]); self.sp_y.setValue(r["y"])
        self.sp_w.setValue(r["w"]); self.sp_h.setValue(r["h"])
        for w in ws:
            w.blockSignals(False)
        self.tag_flow.update_chip(self.current_idx, self.current_preset())

    def _on_rect_changed(self):
        r = self.preview.get_relative_rect()
        self.current_preset()["relative_rect"] = r
        self.tag_flow.update_chip(self.current_idx, self.current_preset())
        self._emit_config()

    def _on_spin_changed(self, _):
        self.current_preset()["relative_rect"] = {
            "x": self.sp_x.value(), "y": self.sp_y.value(),
            "w": self.sp_w.value(), "h": self.sp_h.value(),
        }
        self.preview.set_relative_rect(self.current_preset()["relative_rect"])
        self._emit_config()

    def _on_size_changed(self, v: int):
        fs = v / 100.0
        self.current_preset()["font_scale"] = fs
        self.preview.set_font_scale(fs)
        self._update_size_labels()
        self._emit_config()

    def _on_stroke_changed(self, v: int):
        self.current_preset()["stroke_width"] = v
        self.preview.set_custom_stroke(v)
        self._update_size_labels()
        self._emit_config()

    def _update_size_labels(self):
        self.size_val.setText(f"{self.size_slider.value() / 100.0:.2f}")
        self.stroke_val.setText(str(self.stroke_slider.value()))
        self.size_val.adjustSize()
        self.stroke_val.adjustSize()

    # --------------------------------------------------------- videos
    def _pick_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Выбрать видео для сегмента {self.index + 1}",
            FOLDER_DIRS[self.index] if os.path.isdir(FOLDER_DIRS[self.index]) else BASE_DIR,
            "Видео (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts *.flv *.wmv *.3gp)")
        if path:
            self.set_video(path)

    def _random_video(self):
        folder = FOLDER_DIRS[self.index]
        vids = list_videos(folder)
        if vids:
            self.set_video(random.choice(vids))
        elif self.main:
            self.main.status("Нет видео в папке " + FOLDERS[self.index], bad=True)

    def set_video(self, path: str):
        self.video_path = path
        self.video_label.setText(truncate_middle(os.path.basename(path), 34))
        self.video_label.setToolTip(path)
        ff = get_ffmpeg_exe()
        self._orig_dur = get_video_duration(path, ff)
        self._update_effective()
        self.scrub_slider.setValue(0)
        active = bool(self.main and self.main.is_active_segment(self.index))
        self.preview.load_video(path if active else "", self._orig_dur)
        if not active:
            # show a static first frame (no player thread)
            self._show_static_frame(path)
        self._emit_config()

    def _show_static_frame(self, path: str):
        """Display one low-res frame without starting playback."""
        def run():
            img = self._grab_frame(path, 0.2)
            self._frame_sig.result.emit(img, 0.0)
        threading.Thread(target=run, daemon=True).start()

    def clear_video(self):
        self.video_path = ""
        self.video_label.setText("видео не выбрано")
        self.video_label.setToolTip("")
        self.preview.stop_playback()
        self.preview.load_video("")
        self.scrub_slider.setValue(0)
        self._emit_config()

    # ---------------------------------------------------------- scrub
    def _on_scrub_toggled(self, on: bool):
        self.scrub_enabled = on
        if not on:
            self._scrub_timer.stop()

    def _on_scrub(self, v: int):
        if not self.scrub_enabled or not self.video_path or self.econ:
            return
        self.preview._playing = False
        self.preview.btn_play.setText("▶")
        dur = self._orig_dur or self.duration
        t = v / 1000.0 * max(dur, 0.5)
        self._pending_t = t
        if self._loading:
            self._scrub_timer.start(120)
        else:
            self._scrub_timer.start(180)

    def _do_scrub(self):
        if self._loading or self.econ or not self.video_path:
            return
        t = self._pending_t if self._pending_t is not None else 0.0
        self._load_frame(t)

    @staticmethod
    def _grab_frame(path: str, t: float) -> Optional[QImage]:
        """One low-res frame via ffmpeg (cheap on RAM)."""
        try:
            ff = get_ffmpeg_exe()
            ss = max(0.0, t)
            r = subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-ss", f"{ss:.2f}",
                 "-i", path, "-frames:v", "1",
                 "-vf", f"scale={PREV_W}:{PREV_H}:force_original_aspect_ratio=increase,"
                        f"crop={PREV_W}:{PREV_H}",
                 "-f", "image2pipe", "-c:v", "png", "-"],
                capture_output=True, timeout=30)
            if r.returncode == 0 and r.stdout:
                from PIL import Image as _PIL
                pil = _PIL.open(io.BytesIO(r.stdout)).convert("RGB")
                data = pil.tobytes("raw", "RGB")
                return QImage(data, PREV_W, PREV_H, PREV_W * 3,
                              QImage.Format.Format_RGB888).copy()
        except Exception:
            pass
        return None

    def _load_frame(self, t: float):
        if self.econ or not self.video_path:
            return
        if self._loading:
            self._pending_t = t
            self._scrub_timer.start(120)
            return
        self._loading = True
        vp = self.video_path
        tt = t

        def run():
            img = self._grab_frame(vp, tt)
            self._frame_sig.result.emit(img, tt)

        threading.Thread(target=run, daemon=True).start()

    def _on_frame(self, img, t):
        self._loading = False
        if img is not None:
            self.preview.show_frame_at(img, t)
        if self._pending_t is not None and self._pending_t != t:
            self._scrub_timer.start(120)
        self._pending_t = None

    # ---------------------------------------------------------- config
    def get_config(self) -> Dict[str, Any]:
        return {
            "video": self.video_path,
            "duration": self.duration,
            "presets": self.presets,
            "current_idx": self.current_idx,
            "random_pick": self.chk_random.isChecked(),
            "texts": [p.get("text", "") for p in self.presets],   # compat
        }

    def set_config(self, cfg: Dict[str, Any]):
        try:
            if cfg.get("video"):
                self.video_path = cfg["video"]
                self.video_label.setText(truncate_middle(os.path.basename(self.video_path), 30))
                self.video_label.setToolTip(self.video_path)
            self.duration = float(cfg.get("duration", 5.0))
            self.dur_spin.setValue(self.duration)
            # migrate old format texts:[...] -> presets
            presets = cfg.get("presets")
            if not presets and cfg.get("texts"):
                rect = cfg.get("relative_rect") or DEFAULT_PRESET["relative_rect"]
                fs = cfg.get("font_scale", 1.1)
                sw = cfg.get("stroke_width", 0)
                presets = [{"text": t, "relative_rect": dict(rect),
                            "font_scale": fs, "stroke_width": sw}
                           for t in cfg["texts"]]
            if presets:
                self.presets = []
                for p in presets:
                    np_ = dict(p)
                    np_["relative_rect"] = dict(p.get("relative_rect", DEFAULT_PRESET["relative_rect"]))
                    np_["font_scale"] = float(p.get("font_scale", 1.1))
                    np_["stroke_width"] = int(p.get("stroke_width", 0))
                    self.presets.append(np_)
            self.current_idx = max(0, min(int(cfg.get("current_idx", 0)),
                                          len(self.presets) - 1))
            if "random_pick" in cfg:
                self.chk_random.setChecked(bool(cfg["random_pick"]))
            self._load_preset_into_ui(block=True)
            if self.video_path and os.path.isfile(self.video_path):
                self._orig_dur = get_video_duration(self.video_path, get_ffmpeg_exe())
                self._update_effective()
                self.scrub_slider.setValue(0)
                # static frame; the active segment's player is started by
                # MainWindow._on_tab_changed once the saved tab is applied
                self.preview.load_video("")
                self._show_static_frame(self.video_path)
        except Exception:
            traceback.print_exc()

    def _emit_config(self):
        self.configChanged.emit()

# ======================================================================
#  THREAD->GUI SIGNAL BRIDGE (frame loading)
# ======================================================================

class _FrameSignal(QObject):
    result = pyqtSignal(object, float)


# ======================================================================
#  SIDEBAR CARD (base)
# ======================================================================

class SidebarCard(QFrame):
    def __init__(self, title: str, main_window=None):
        super().__init__()
        self.main = main_window
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)

    def _add_section(self, title: str = "") -> QFrame:
        f = QFrame()
        f.setObjectName("Section")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("Hint")
            lay.addWidget(lbl)
        self.layout().addWidget(f)
        f._inner = lay
        return f

# ======================================================================
#  EXPORT CARD
# ======================================================================

class ExportCard(SidebarCard):
    configChanged = pyqtSignal()

    def __init__(self, main_window=None):
        super().__init__("Экспорт", main_window)
        s1 = self._add_section("Разрешение")
        self.res_combo = QComboBox()
        self.res_combo.addItems([r["label"] for r in RESOLUTIONS])
        self.res_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.res_combo.setMinimumContentsLength(10)
        s1._inner.addWidget(self.res_combo)

        row = QHBoxLayout()
        vbox = QVBoxLayout()
        vbox.addWidget(QLabel("FPS"))
        self.fps_combo = QComboBox()
        self.fps_combo.addItems([str(f) for f in FPS_CHOICES])
        self.fps_combo.setCurrentIndex(0)
        vbox.addWidget(self.fps_combo)
        row.addLayout(vbox)
        qbox = QVBoxLayout()
        qbox.addWidget(QLabel("Качество"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems([q["label"] for q in QUALITY_PRESETS])
        self.quality_combo.setCurrentIndex(1)
        self.quality_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.quality_combo.setMinimumContentsLength(10)
        qbox.addWidget(self.quality_combo)
        row.addLayout(qbox, 1)
        s1._inner.addLayout(row)

        s2 = self._add_section()
        self.chk_audio = QCheckBox("Со звуком")
        self.chk_audio.setChecked(True)
        self.chk_caps = QCheckBox("CAPS (заглавные буквы)")
        self.chk_tenbit = QCheckBox("10-бит цвет (для MOV)")
        self.chk_tenbit.setToolTip("Сохранить 10-бит глубину, если исходник 10-бит "
                                   "(iPhone HEVC / ProRes в .mov). Убирает бандинг на градиентах.")
        self.chk_blur = QCheckBox("Размытый фон вместо обрезки")
        self.chk_blur.setToolTip("Для горизонтальных/квадратных видео: заполнить экран "
                                 "размытой копией вместо жёсткого кропа — контент не теряется.")
        self.chk_econ = QCheckBox("Эконом (без превью)")
        self.chk_econ.setToolTip("Не грузить видео в превью — экономия RAM при 1000+ видео")
        s2._inner.addWidget(self.chk_audio)
        s2._inner.addWidget(self.chk_caps)
        s2._inner.addWidget(self.chk_tenbit)
        s2._inner.addWidget(self.chk_blur)
        s2._inner.addWidget(self.chk_econ)

        s3 = self._add_section("Куда")
        orow = QHBoxLayout()
        self.out_label = QLabel(truncate_middle(OUTPUT_DIR, 34))
        self.out_label.setObjectName("Info")
        self.out_label.setToolTip(OUTPUT_DIR)
        orow.addWidget(self.out_label, 1)
        btn_open = QPushButton("Открыть папку")
        btn_open.setObjectName("SmallBtn")
        btn_open.setToolTip("Открыть папку output")
        btn_open.clicked.connect(lambda: open_folder(OUTPUT_DIR))
        orow.addWidget(btn_open)
        s3._inner.addLayout(orow)

        for w in (self.res_combo, self.fps_combo, self.quality_combo,
                  self.chk_audio, self.chk_caps, self.chk_tenbit, self.chk_blur,
                  self.chk_econ):
            w.currentIndexChanged.connect(self._changed) if isinstance(w, QComboBox) \
                else w.toggled.connect(self._changed)

    def _changed(self, _=None):
        self.configChanged.emit()

    def get_export_config(self) -> Dict[str, Any]:
        qi = self.quality_combo.currentIndex()
        q = QUALITY_PRESETS[qi]
        res = RESOLUTIONS[self.res_combo.currentIndex()]
        return {
            "resolution": res,
            "resolution_index": self.res_combo.currentIndex(),
            "force_reels": self.res_combo.currentIndex() == 0,
            "fps": int(self.fps_combo.currentText()),
            "audio": self.chk_audio.isChecked(),
            "uppercase": self.chk_caps.isChecked(),
            "ten_bit": self.chk_tenbit.isChecked(),
            "blur_fill": self.chk_blur.isChecked(),
            "econ": self.chk_econ.isChecked(),
            "crf": q["crf"],
            "preset": q["preset"],
            "quality_text": q["label"],
        }

    def set_export_config(self, cfg: Dict[str, Any]):
        if not isinstance(cfg, dict):
            cfg = {}
        for w in (self.res_combo, self.fps_combo, self.quality_combo,
                  self.chk_audio, self.chk_caps, self.chk_tenbit, self.chk_blur,
                  self.chk_econ):
            w.blockSignals(True)
        if "resolution_index" in cfg:
            self.res_combo.setCurrentIndex(
                _combo_index(self.res_combo, cfg["resolution_index"], 0))
        if "fps" in cfg:
            fps = _to_int(cfg["fps"], 30)
            found = False
            for i in range(self.fps_combo.count()):
                if str(self.fps_combo.itemText(i)) == str(fps):
                    self.fps_combo.setCurrentIndex(i)
                    found = True
                    break
            if not found:
                self.fps_combo.setCurrentIndex(0)
        if "quality_text" in cfg:
            for i, q in enumerate(QUALITY_PRESETS):
                if q["label"] == cfg["quality_text"]:
                    self.quality_combo.setCurrentIndex(i)
                    break
        if "audio" in cfg: self.chk_audio.setChecked(bool(cfg["audio"]))
        if "uppercase" in cfg: self.chk_caps.setChecked(bool(cfg["uppercase"]))
        if "ten_bit" in cfg: self.chk_tenbit.setChecked(bool(cfg["ten_bit"]))
        if "blur_fill" in cfg: self.chk_blur.setChecked(bool(cfg["blur_fill"]))
        if "econ" in cfg: self.chk_econ.setChecked(bool(cfg["econ"]))
        for w in (self.res_combo, self.fps_combo, self.quality_combo,
                  self.chk_audio, self.chk_caps, self.chk_tenbit, self.chk_blur,
                  self.chk_econ):
            w.blockSignals(False)

# ======================================================================
#  CHARACTERS CARD
# ======================================================================

class CharactersCard(SidebarCard):
    configChanged = pyqtSignal()
    tokenChecked = pyqtSignal(bool, str, str)    # thread -> GUI bridge
    testSendDone = pyqtSignal(bool, str, str)    # ok, info, char name

    def __init__(self, main_window=None):
        super().__init__("Персонажи", main_window)
        self.tokenChecked.connect(self._token_check_done)
        self.testSendDone.connect(self._on_test_send_done)
        self.chars = load_characters()

        # ---- bot token row
        s = self._add_section("Telegram Bot")
        # token edit + actions (2 rows so it fits any sidebar width)
        token_row = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("123456:ABC-DEF…")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setClearButtonEnabled(False)
        token_row.addWidget(self.token_edit, 1)
        self.btn_eye = QPushButton("Показать")
        self.btn_eye.setObjectName("SmallBtn")
        self.btn_eye.clicked.connect(self._toggle_token_visibility)
        self.btn_save_token = QPushButton("Сохранить")
        self.btn_save_token.setObjectName("SmallBtn")
        self.btn_save_token.setToolTip("Сохранить токен")
        self.btn_save_token.clicked.connect(self._save_token)
        token_row.addWidget(self.btn_eye)
        token_row.addWidget(self.btn_save_token)
        s._inner.addLayout(token_row)
        token_row2 = QHBoxLayout()
        self.btn_check_token = QPushButton("Проверить")
        self.btn_check_token.setObjectName("SmallBtn")
        self.btn_check_token.setToolTip("Проверить токен (getMe)")
        self.btn_check_token.clicked.connect(self._check_token)
        self.btn_test_send = QPushButton("Тест отправки")
        self.btn_test_send.setObjectName("SmallBtn")
        self.btn_test_send.setToolTip("Отправить тестовое видео в чат персонажа "
                                      "(если chat_id устарел — исправится автоматически)")
        self.btn_test_send.clicked.connect(self._test_send)
        token_row2.addWidget(self.btn_check_token)
        token_row2.addWidget(self.btn_test_send)
        token_row2.addStretch(1)
        s._inner.addLayout(token_row2)
        self.chk_auto = QCheckBox("Авто-отправка в Telegram")
        self.chk_auto.setToolTip("Отправлять каждый готовый файл как документ (макс. качество)")
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        s._inner.addWidget(self.chk_auto)
        self.token_status = QLabel("")
        self.token_status.setObjectName("Hint")
        s._inner.addWidget(self.token_status)

        # ---- selectable character buttons
        s2 = self._add_section("Выбор для батча")
        self.char_flow_wrap = QWidget()
        self.char_flow = FlowLayout(self.char_flow_wrap, margin=0, hspacing=5, vspacing=5)
        s2._inner.addWidget(self.char_flow_wrap)
        sel_row = QHBoxLayout()
        btn_all = QPushButton("Все")
        btn_all.setObjectName("SmallBtn")
        btn_all.clicked.connect(lambda: self._select_all(True))
        btn_none = QPushButton("Снять")
        btn_none.setObjectName("SmallBtn")
        btn_none.clicked.connect(lambda: self._select_all(False))
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch(1)
        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.setObjectName("SmallBtn")
        self.btn_refresh.setToolTip("Пересканировать папки")
        self.btn_refresh.clicked.connect(self.refresh)
        sel_row.addWidget(self.btn_refresh)
        s2._inner.addLayout(sel_row)

        # ---- detail list
        s3 = self._add_section("Chat ID для каждого персонажа")
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setMinimumHeight(120)
        self.detail_scroll.setMaximumHeight(240)
        self.detail_host = QWidget()
        self.detail_vbox = QVBoxLayout(self.detail_host)
        self.detail_vbox.setContentsMargins(0, 0, 0, 0)
        self.detail_vbox.setSpacing(6)
        self.detail_scroll.setWidget(self.detail_host)
        s3._inner.addWidget(self.detail_scroll)

        # ---- create character
        s4 = self._add_section("Создать персонажа")
        create_row = QHBoxLayout()
        self.new_char_edit = QLineEdit()
        self.new_char_edit.setPlaceholderText("имя (alex, masha…)")
        create_row.addWidget(self.new_char_edit, 1)
        btn_create = QPushButton("Создать")
        btn_create.setObjectName("SmallBtn")
        btn_create.clicked.connect(self._create_character)
        create_row.addWidget(btn_create)
        s4._inner.addLayout(create_row)

        hint = QLabel("Каждый персонаж = подпапка в folder_1. Клади видео туда, "
                      "укажи Chat ID (например -1001234567890) — готовые файлы "
                      "будут отправлены в эту группу как документ.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().addWidget(hint)

        self._populate_from_file()
        self.refresh()

    # ---------------------------------------------------------- token
    def _toggle_token_visibility(self):
        if self.token_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_eye.setText("Скрыть")
        else:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_eye.setText("👁")

    def _save_token(self):
        self.chars["bot_token"] = self.token_edit.text().strip()
        save_characters(self.chars)
        self.token_status.setText("💾 Токен сохранён")
        self._emit_config()

    def _check_token(self):
        tok = self.token_edit.text().strip()
        if not tok:
            QMessageBox.warning(self, APP_NAME, "Введи токен бота.")
            return
        self.token_status.setText("⏳ Проверка…")
        QApplication.processEvents()

        def check():
            return test_bot_token(tok)

        def done():
            ok, info = check()
            self.tokenChecked.emit(ok, info, tok)
        threading.Thread(target=done, daemon=True).start()

    def _token_check_done(self, ok: bool, info: str, tok: str):
        if ok:
            self.chars["bot_token"] = tok
            save_characters(self.chars)
            self.token_status.setText(f"✅ Бот работает: {info}")
        else:
            self.token_status.setText(f"❌ Ошибка: {info[:60]}")

    def _on_auto_toggled(self, on: bool):
        self.chars["auto_send"] = on
        save_characters(self.chars)
        self._emit_config()

    def _test_send(self):
        """Send a tiny test video to the first character that has a chat id."""
        tok = self.token_edit.text().strip()
        if not tok:
            QMessageBox.warning(self, APP_NAME, "Введи токен бота.")
            return
        # find a char with chat_id
        chars_cfg = self.chars.get("characters", {})
        candidates = list(chars_cfg.items())
        if not candidates:
            QMessageBox.warning(
                self, APP_NAME,
                "Нет персонажей с Chat ID.\nУкажи Chat ID в списке персонажей "
                "(например -1001234567890), нажми 💾, затем попробуй снова.")
            return
        name, entry = candidates[0]
        chat_id = str(entry.get("chat_id", "") or "")
        if not chat_id:
            QMessageBox.warning(
                self, APP_NAME,
                f"У персонажа «{name}» не указан Chat ID.\nВпиши его и нажми 💾.")
            return
        self.token_status.setText(f"⏳ Отправка теста в чат {name}…")
        QApplication.processEvents()
        tmp = os.path.join(OUTPUT_DIR, "_tg_test.mp4")

        def work():
            make_test_video(tmp)
            ok, info = send_video_via_telegram(tok, chat_id, tmp, caption="Тест Video Stitcher Pro")
            self.testSendDone.emit(ok, info, name)

        threading.Thread(target=work, daemon=True).start()

    def _on_test_send_done(self, ok: bool, info: str, name: str):
        if ok:
            if info.startswith("migrated:"):
                new_id = info.split(":", 1)[1]
                self.chars.setdefault("characters", {}).setdefault(name, {})["chat_id"] = new_id
                save_characters(self.chars)
                self.refresh()
                self.token_status.setText(
                    f"✅ Тест отправлен! Chat ID обновлён → {new_id}")
            else:
                self.token_status.setText("✅ Тест отправлен в чат! Проверь Telegram.")
        else:
            self.token_status.setText(f"❌ Ошибка: {info[:80]}")

    # ----------------------------------------------------- characters
    def _populate_from_file(self):
        d = load_characters()
        # merge: keep folders that no longer exist but keep config
        self.chars = d
        self.token_edit.setText(d.get("bot_token", ""))
        self.chk_auto.setChecked(bool(d.get("auto_send", False)))

    def refresh(self):
        self.chars = sanitize_characters(self.chars)
        try:
            folders = list_character_folders()
        except Exception:
            folders = []
        try:
            cfg = self.chars.get("characters", {})
            # prune configs whose folder does not exist on disk at all
            existing = set()
            for _, name, _ in folders:
                existing.add(name)
            for name, folder in _all_character_dirs():
                existing.add(get_character_name(folder))
            cfg = {k: v for k, v in cfg.items() if k in existing}
            self.chars["characters"] = cfg
        except Exception:
            cfg = {}
        save_characters(self.chars)

        # ---- flow buttons
        for i in reversed(range(self.char_flow.count())):
            item = self.char_flow.takeAt(i)
            w = item.widget()
            if w:
                w.setVisible(False)
                w.setParent(None)
                w.deleteLater()
        self._char_buttons: Dict[str, QPushButton] = {}
        selected = set(self.chars.get("selected_characters", []))
        for path, name, n in folders:
            b = QPushButton(f"{name} ({n})")
            b.setObjectName("CharSelect")
            b.setCheckable(True)
            b.setChecked(name in selected)
            b.clicked.connect(lambda _, nm=name: self._toggle_char(nm))
            self.char_flow.addWidget(b)
            self._char_buttons[name] = b

        # ---- detail rows
        while self.detail_vbox.count():
            item = self.detail_vbox.takeAt(0)
            w = item.widget()
            if w:
                w.setVisible(False)
                w.setParent(None)
                w.deleteLater()
        for path, name, n in folders:
            self.detail_vbox.addWidget(self._make_char_row(path, name, n))
        self.detail_vbox.addStretch(1)
        self._emit_config()

    def _make_char_row(self, path: str, name: str, count: int) -> QFrame:
        """Compact 2-row character card: name+count on top, chat+actions below.

        Keeps the minimum width small so the sidebar never overflows.
        """
        row = QFrame()
        row.setObjectName("CharRow")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(6)

        # row 1: name + video count
        head = QHBoxLayout()
        head.setSpacing(6)
        icon = "👤"
        if name == "default":
            icon = "📁"
        name_lbl = QLabel(f"{icon} {name}")
        name_lbl.setStyleSheet("font-weight: 600; color: #E8EAF2;")
        cnt_lbl = QLabel(f"· {count} видео")
        cnt_lbl.setObjectName("CardSub")
        head.addWidget(name_lbl)
        head.addWidget(cnt_lbl)
        head.addStretch(1)
        lay.addLayout(head)

        # row 2: chat id + actions
        act = QHBoxLayout()
        act.setSpacing(5)
        chat = self.chars.get("characters", {}).get(name, {}).get("chat_id", "")
        edit = QLineEdit(chat)
        edit.setPlaceholderText("-100…")
        edit.setMinimumWidth(50)
        act.addWidget(edit, 1)
        btn_save = QPushButton("Сохранить")
        btn_save.setObjectName("SmallBtn")
        btn_save.setToolTip("Сохранить Chat ID")
        btn_save.clicked.connect(
            lambda _, nm=name, e=edit: self._save_chat_id(nm, e.text().strip()))
        btn_open = QPushButton("Папка")
        btn_open.setObjectName("SmallBtn")
        btn_open.setToolTip("Открыть папку")
        btn_open.clicked.connect(lambda _, p=path: open_folder(p))
        btn_del = QPushButton("Удалить")
        btn_del.setObjectName("SmallBtn")
        btn_del.setObjectName("Danger")
        btn_del.setToolTip("Удалить из конфига (папка останется)")
        btn_del.clicked.connect(lambda _, nm=name: self._remove_char(nm))
        act.addWidget(btn_save)
        act.addWidget(btn_open)
        act.addWidget(btn_del)
        lay.addLayout(act)
        return row

    def _save_chat_id(self, name: str, chat_id: str):
        chars = self.chars.setdefault("characters", {})
        entry = chars.setdefault(name, {})
        entry["chat_id"] = chat_id
        entry["display_name"] = entry.get("display_name", name)
        save_characters(self.chars)
        self.status_hint(f"💾 Chat ID сохранён для {name}")
        self._emit_config()

    def _remove_char(self, name: str):
        self.chars.setdefault("characters", {}).pop(name, None)
        sel = self.chars.get("selected_characters", [])
        if name in sel:
            sel.remove(name)
        save_characters(self.chars)
        self.refresh()

    def _create_character(self):
        name = self.new_char_edit.text().strip().lower()
        name = re.sub(r"[^a-z0-9_-]", "_", name)
        if not name:
            return
        folder = os.path.join(FOLDER_DIRS[0], name)
        os.makedirs(folder, exist_ok=True)
        self.chars.setdefault("characters", {})[name] = {"chat_id": "", "display_name": name}
        save_characters(self.chars)
        self.new_char_edit.clear()
        self.refresh()

    def _toggle_char(self, name: str):
        sel = set(self.chars.get("selected_characters", []))
        if name in sel:
            sel.discard(name)
        else:
            sel.add(name)
        self.chars["selected_characters"] = sorted(sel)
        save_characters(self.chars)
        self._emit_config()

    def _select_all(self, on: bool):
        names = [name for _, name, _ in list_character_folders()]
        self.chars["selected_characters"] = names if on else []
        save_characters(self.chars)
        self.refresh()

    def status_hint(self, msg: str):
        if self.main:
            self.main.status(msg, bad=False)

    def get_filtered_character_folders(self) -> List[Tuple[str, str, int]]:
        selected = self.chars.get("selected_characters", [])
        return get_filtered_character_folders(selected)

    def _emit_config(self):
        self.configChanged.emit()

# ======================================================================
#  BATCH CARD
# ======================================================================

class BatchCard(SidebarCard):
    configChanged = pyqtSignal()

    def __init__(self, main_window=None):
        super().__init__("Батч-режим", main_window)
        s = self._add_section()
        self.chk_enable = QCheckBox("Включить батч")
        self.chk_enable.setChecked(False)
        s._inner.addWidget(self.chk_enable)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Последовательно (до min)",
            "Рандом 2-5",
        ])
        self.mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mode_combo.setMinimumContentsLength(12)
        s._inner.addWidget(self.mode_combo)

        row1 = QHBoxLayout()
        self.chk_delete = QCheckBox("Удалять 2–5")
        self.chk_move = QCheckBox("В used")
        self.chk_move.setToolTip("Безопасно перемещать использованные видео в output/used")
        row1.addWidget(self.chk_delete)
        row1.addWidget(self.chk_move)
        row1.addStretch(1)
        s._inner.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Сколько:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(0, 10000)
        self.count_spin.setValue(0)
        self.count_spin.setSpecialValueText("0 = Auto")
        self.count_spin.setFixedWidth(90)
        row2.addWidget(self.count_spin)
        row2.addStretch(1)
        row2.addWidget(QLabel("Потоков:"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 8)
        self.threads_spin.setValue(3)
        self.threads_spin.setFixedWidth(60)
        row2.addWidget(self.threads_spin)
        s._inner.addLayout(row2)

        self.count_info = QLabel("…")
        self.count_info.setObjectName("Info")
        self.count_info.setWordWrap(True)
        s._inner.addWidget(self.count_info)

        self.next_preview = QLabel("")
        self.next_preview.setObjectName("Hint")
        self.next_preview.setWordWrap(True)
        s._inner.addWidget(self.next_preview)

        hint = QLabel("Каждый персонаж из folder_1 + folder_2…5 → одно готовое Reels-видео. "
                      "0 = Auto: min длин для «Последовательно», len(folder_1) для «Рандом».")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().addWidget(hint)

        for w in (self.chk_enable, self.mode_combo, self.chk_delete, self.chk_move,
                  self.count_spin, self.threads_spin):
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._changed)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._changed)
            else:
                w.valueChanged.connect(self._changed)

    def _changed(self, _=None):
        self.configChanged.emit()

    def get_batch_config(self) -> Dict[str, Any]:
        return {
            "enabled": self.chk_enable.isChecked(),
            "mode": self.mode_combo.currentIndex(),          # 0 seq, 1 random
            "delete": self.chk_delete.isChecked(),
            "move": self.chk_move.isChecked(),
            "count": self.count_spin.value(),
            "threads": self.threads_spin.value(),
        }

    def set_batch_config(self, cfg: Dict[str, Any]):
        if not isinstance(cfg, dict):
            cfg = {}
        for w in (self.chk_enable, self.mode_combo, self.chk_delete, self.chk_move,
                  self.count_spin, self.threads_spin):
            w.blockSignals(True)
        if "enabled" in cfg: self.chk_enable.setChecked(bool(cfg["enabled"]))
        if "mode" in cfg:
            self.mode_combo.setCurrentIndex(_combo_index(self.mode_combo, cfg["mode"], 0))
        if "delete" in cfg: self.chk_delete.setChecked(bool(cfg["delete"]))
        if "move" in cfg: self.chk_move.setChecked(bool(cfg["move"]))
        if "count" in cfg:
            self.count_spin.setValue(max(0, min(10000, _to_int(cfg["count"], 0))))
        if "threads" in cfg:
            self.threads_spin.setValue(max(1, min(8, _to_int(cfg["threads"], 3))))
        for w in (self.chk_enable, self.mode_combo, self.chk_delete, self.chk_move,
                  self.count_spin, self.threads_spin):
            w.blockSignals(False)

    def compute_batch_info(self) -> Dict[str, Any]:
        """Counts + expected total for current selection."""
        char_folders = (self.main.characters_card.get_filtered_character_folders()
                        if self.main else list_character_folders())
        others = [list_videos(d) for d in FOLDER_DIRS[1:]]
        others_len = [len(v) for v in others]
        mode = self.mode_combo.currentIndex()
        info = {"chars": [], "total": 0, "mode": mode, "others": others_len}
        total = 0
        for path, name, n in char_folders:
            if mode == 0:
                m = min([n] + others_len)
            else:
                m = n
            info["chars"].append((name, n, m))
            total += m
        cnt = self.count_spin.value()
        info["total"] = min(total, cnt) if cnt > 0 else total
        return info

    def update_info(self):
        info = self.compute_batch_info()
        parts = [f"1: {c[1]}" for c in info["chars"]]
        parts += [f"{i + 2}: {info['others'][i]}" for i in range(4)]
        mode_txt = "Последовательно" if info["mode"] == 0 else "Рандом"
        self.count_info.setText(
            f"В папках: {', '.join(parts)} → будет {info['total']} видео [{mode_txt}]")
        # next batch preview
        if self.main:
            nxt = self.main.peek_next_batch(info["chars"])
            self.next_preview.setText(nxt)

# ======================================================================
#  FFMPEG CARD
# ======================================================================

class FfmpegCard(SidebarCard):
    configChanged = pyqtSignal()
    findDone = pyqtSignal(object)          # thread -> GUI bridge
    downloadDone = pyqtSignal(object)

    def __init__(self, main_window=None):
        super().__init__("FFMPEG", main_window)
        self.findDone.connect(self._find_done)
        self.downloadDone.connect(self._download_done)
        s = self._add_section()
        self.status_label = QLabel("⏳ Поиск ffmpeg…")
        self.status_label.setObjectName("StatusBad")
        self.status_label.setWordWrap(True)
        s._inner.addWidget(self.status_label)
        self.version_label = QLabel("")
        self.version_label.setObjectName("Hint")
        self.version_label.setWordWrap(True)
        s._inner.addWidget(self.version_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        b1 = QPushButton("Найти в системе")
        b1.setObjectName("SmallBtn")
        b1.clicked.connect(self._find)
        b2 = QPushButton("Скачать")
        b2.setObjectName("SmallBtn")
        b2.setToolTip("Скачать bundled ffmpeg (imageio-ffmpeg / BtbN)")
        b2.clicked.connect(self._download)
        b3 = QPushButton("Выбрать…")
        b3.setObjectName("SmallBtn")
        b3.clicked.connect(self._manual)
        b4 = QPushButton("Проверить")
        b4.setObjectName("SmallBtn")
        b4.clicked.connect(self._check)
        grid.addWidget(b1, 0, 0)
        grid.addWidget(b2, 0, 1)
        grid.addWidget(b3, 1, 0)
        grid.addWidget(b4, 1, 1)
        for i in range(2):
            grid.setColumnStretch(i, 1)
        s._inner.addLayout(grid)

        hint = QLabel("Путь запоминается в ffmpeg_path.json и подхватывается "
                      "автоматически при старте.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        self.layout().addWidget(hint)

        self._busy = False

    def _find(self):
        self._busy = True
        self.status_label.setText("⏳ Поиск…")
        self.status_label.setObjectName("StatusBad")
        QApplication.processEvents()

        def work():
            return find_system_ffmpeg()

        def done():
            path = work()
            self.findDone.emit(path)
        threading.Thread(target=done, daemon=True).start()

    def _find_done(self, path: Optional[str]):
        self._busy = False
        if path:
            save_ffmpeg_path(path)
            _ffmpeg_cache.clear()
            self.set_status_ok(path)
        else:
            self.status_label.setText("❌ Не найден — нажми «⬇️ Скачать»")
            self.status_label.setObjectName("StatusBad")
        self._emit_config()

    def _download(self):
        if self._busy:
            return
        self._busy = True
        self.status_label.setText("⬇️ Скачивание ffmpeg…")
        self.status_label.setObjectName("StatusBad")
        QApplication.processEvents()

        def work():
            return download_ffmpeg_automatically()

        def done():
            path = work()
            self.downloadDone.emit(path)
        threading.Thread(target=done, daemon=True).start()

    def _download_done(self, path: Optional[str]):
        self._busy = False
        if path:
            save_ffmpeg_path(path)
            _ffmpeg_cache.clear()
            self.set_status_ok(path)
        else:
            self.status_label.setText("❌ Не удалось скачать ffmpeg")
            self.status_label.setObjectName("StatusBad")
        self._emit_config()

    def _manual(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать ffmpeg", BASE_DIR,
            "ffmpeg (ffmpeg ffmpeg.exe);;Все файлы (*)")
        if path:
            if is_valid_ffmpeg(path):
                save_ffmpeg_path(path)
                _ffmpeg_cache.clear()
                self.set_status_ok(path)
            else:
                QMessageBox.warning(self, APP_NAME, "Это не похоже на рабочий ffmpeg.")
            self._emit_config()

    def _check(self):
        path = get_ffmpeg_exe()
        if is_valid_ffmpeg(path):
            self.set_status_ok(path)
            ver = get_ffmpeg_version(path)
            QMessageBox.information(self, APP_NAME, ver or "ffmpeg работает")
        else:
            self.status_label.setText("❌ ffmpeg не работает")
            self.status_label.setObjectName("StatusBad")

    def set_status_ok(self, path: str):
        self.status_label.setText(f"✅ {truncate_middle(path, 44)}")
        self.status_label.setObjectName("StatusOk")
        ver = get_ffmpeg_version(path)
        self.version_label.setText(ver[:80] if ver else "")
        if self.main:
            self.main.status("ffmpeg готов: " + (ver.split(" ")[0] if ver else "ok"))

    def _emit_config(self):
        self.configChanged.emit()

# ======================================================================
#  WORKERS
# ======================================================================

class BuildWorker(QThread):
    """Single video build: 5 segments → fast ffmpeg path → concat."""
    progress = pyqtSignal(int, str)
    file_done = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window

    def run(self):
        try:
            self.progress.emit(2, "Сборка видео…")
            ff = get_ffmpeg_exe()
            if not is_valid_ffmpeg(ff):
                self.failed.emit("ffmpeg не найден. Открой «⚙️ FFMPEG» и скачай.")
                return

            # ---- choose 5 videos
            vids1 = list_videos(FOLDER_DIRS[0])
            char_folders = list_character_folders()
            if not vids1 and char_folders:
                # fall back to first character folder that has videos
                for _, folder, n in char_folders:
                    if n > 0:
                        vids1 = list_videos(folder)
                        break
            if not vids1:
                self.failed.emit(f"Нет видео в {FOLDER_DIRS[0]} (или подпапках-персонажах).")
                return
            idx = self.main.next_build_index
            video_paths = []
            for i, folder in enumerate(FOLDER_DIRS):
                vids = vids1 if i == 0 else list_videos(folder)
                if not vids:
                    self.failed.emit(f"Папка {FOLDERS[i]} пуста — нужно минимум 1 видео в каждой.")
                    return
                video_paths.append(vids[idx % len(vids)])
            if len(video_paths) < 5:
                self.failed.emit("Нужно 5 видео (folder_1…folder_5).")
                return
            self.main.next_build_index = (idx + 1) % (len(vids1) if vids1 else 1)

            # ---- gather config
            presets = [c.presets for c in self.main.cards]
            durs = [c.duration for c in self.main.cards]
            rand_flags = [c.chk_random.isChecked() for c in self.main.cards]
            rand_idx = [c.current_idx for c in self.main.cards]
            exp = self.main.export_card.get_export_config()
            canvas = self._canvas_size(exp)
            self.progress.emit(5, "Подготовка сегментов…")

            # unique output name
            out_name = f"final_video_{self.main.video_counter:04d}.mp4"
            self.main.video_counter += 1
            out_path = os.path.join(OUTPUT_DIR, out_name)

            def cb(pct):
                self.progress.emit(int(5 + pct * 0.83), f"Рендер {FOLDERS}…")

            ok, err = build_one_final_ffmpeg(
                video_paths, presets, durs, out_path, ff,
                resolution=canvas,
                fps=exp["fps"], crf=exp["crf"], preset=exp["preset"],
                with_audio=exp["audio"], uppercase=exp["uppercase"],
                ten_bit=exp.get("ten_bit", False),
                blur_fill=exp.get("blur_fill", False),
                audio_kbps=256 if exp["quality_text"].startswith("💎") else 192,
                random_flags=rand_flags, preset_indices=rand_idx,
                progress_cb=cb)
            if not ok:
                # MoviePy fallback
                self.progress.emit(40, "ffmpeg не сработал — fallback MoviePy…")
                ok2, err2 = run_moviepy(
                    video_paths, presets, durs, out_path,
                    resolution=canvas, fps=exp["fps"],
                    with_audio=exp["audio"], uppercase=exp["uppercase"],
                    random_flags=rand_flags, preset_indices=rand_idx)
                if not ok2:
                    self.failed.emit(f"Ошибка: {err}\nMoviePy: {err2}")
                    return
            self.progress.emit(97, "Готово")
            self.file_done.emit(out_path)
            self.finished_ok.emit(out_path)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))

    def _canvas_size(self, exp: Dict[str, Any]) -> Tuple[int, int]:
        res = exp["resolution"]
        if res["w"] > 0:
            return res["w"], res["h"]
        # "Оригинал" — use first video resolution
        vids1 = list_videos(FOLDER_DIRS[0])
        if vids1:
            w, h = get_video_size(vids1[0], get_ffmpeg_exe())
            if w > 0:
                return w, h
        return 1080, 1920


class BatchBuildWorker(QThread):
    """Batch build for characters with multi-threading + Telegram."""
    progress = pyqtSignal(int, str)
    file_done = pyqtSignal(str, str)          # (out_path, character)
    finished_ok = pyqtSignal(str)             # output dir
    failed = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            ff = get_ffmpeg_exe()
            if not is_valid_ffmpeg(ff):
                self.failed.emit("ffmpeg не найден.")
                return
            exp = self.main.export_card.get_export_config()
            bcfg = self.main.batch_card.get_batch_config()
            canvas = self.main.build_worker_canvas(exp)
            presets = [c.presets for c in self.main.cards]
            durs = [c.duration for c in self.main.cards]
            rand_flags = [c.chk_random.isChecked() for c in self.main.cards]
            rand_idx = [c.current_idx for c in self.main.cards]

            char_folders = self.main.characters_card.get_filtered_character_folders()
            if not char_folders:
                self.failed.emit("Нет персонажей с видео в folder_1.")
                return
            others = [list_videos(d) for d in FOLDER_DIRS[1:]]
            if any(not v for v in others):
                self.failed.emit("В папках folder_2…folder_5 должны быть видео.")
                return

            # ---------- build task list ----------
            tasks: List[Tuple[str, str, int]] = []   # (char_folder, char_name, vid_idx)
            mode = bcfg["mode"]
            for path, name, n in char_folders:
                cv = list_videos(path)
                if mode == 0:
                    m = min(len(cv), *[len(v) for v in others])
                else:
                    m = len(cv)
                for i in range(m):
                    tasks.append((path, name, i))
            cnt = bcfg["count"]
            if cnt > 0:
                tasks = tasks[:cnt]
            total = len(tasks)
            if total == 0:
                self.failed.emit("Нечего собирать — 0 задач.")
                return

            self.progress.emit(1, f"Батч: {total} видео, потоков {bcfg['threads']}")

            # ---------- helpers ----------
            pools = [list(v) for v in others]
            pools_lock = threading.Lock()
            chars_cfg = self.main.characters_card.chars
            bot_token = str(chars_cfg.get("bot_token", "") or "")
            auto_send = bool(chars_cfg.get("auto_send", False))
            completed = 0
            completed_lock = threading.Lock()

            def pick_videos(folder1_vid: str, char_name: str, vid_idx: int) -> Optional[List[str]]:
                paths = [folder1_vid]
                for pi in range(4):
                    pool = pools[pi]
                    if mode == 0:
                        with pools_lock:
                            if not pool:
                                return None
                            v = pool[vid_idx % len(pool)]
                            if bcfg["delete"] or bcfg["move"]:
                                pool.remove(v)
                        paths.append(v)
                    else:
                        with pools_lock:
                            if not pool:
                                return None
                            v = random.choice(pool)
                            if bcfg["delete"] or bcfg["move"]:
                                pool.remove(v)
                        paths.append(v)
                return paths

            def handle_used(video_paths: List[str]):
                for pi, vp in enumerate(video_paths[1:], start=1):
                    if bcfg["move"] and os.path.isfile(vp):
                        dest_dir = os.path.join(USED_DIR, FOLDERS[pi])
                        os.makedirs(dest_dir, exist_ok=True)
                        try:
                            shutil.move(vp, os.path.join(
                                dest_dir, os.path.basename(vp)))
                        except Exception:
                            pass
                    elif bcfg["delete"] and os.path.isfile(vp):
                        try:
                            os.remove(vp)
                        except Exception:
                            pass

            def build_task(task: Tuple[str, str, int]) -> Optional[str]:
                char_folder, char_name, vid_idx = task
                cv = list_videos(char_folder)
                if vid_idx >= len(cv):
                    return None
                f1 = cv[vid_idx]
                vp = pick_videos(f1, char_name, vid_idx)
                if not vp:
                    return None
                out_name = f"{char_name}_{vid_idx + 1:04d}.mp4"
                out_path = os.path.join(OUTPUT_DIR, out_name)
                if os.path.exists(out_path):
                    base, ext = os.path.splitext(out_name)
                    k = 1
                    while os.path.exists(out_path):
                        out_path = os.path.join(
                            OUTPUT_DIR, f"{base}_{k:03d}{ext}")
                        k += 1
                ok, err = build_one_final_ffmpeg(
                    vp, presets, durs, out_path, ff, resolution=canvas,
                    fps=exp["fps"], crf=exp["crf"], preset=exp["preset"],
                    with_audio=exp["audio"], uppercase=exp["uppercase"],
                    ten_bit=exp.get("ten_bit", False),
                    blur_fill=exp.get("blur_fill", False),
                    audio_kbps=256 if exp["quality_text"].startswith("💎") else 192,
                    random_flags=rand_flags, preset_indices=rand_idx)
                if not ok:
                    print(f"[batch] {out_name} failed: {err}")
                    return None
                handle_used(vp)
                # Telegram auto-send is handled on the GUI thread via
                # file_done -> MainWindow.auto_send_file (auto-migrates chat id)
                return out_path

            # ---------- run ----------
            def bump():
                nonlocal completed
                with completed_lock:
                    completed += 1
                    return completed

            if bcfg["threads"] <= 1:
                for t in tasks:
                    if self._stop:
                        break
                    out = build_task(t)
                    pct = int(completed / total * 88)
                    if out:
                        self.progress.emit(pct, f"Батч {completed}/{total} готово")
                        self.file_done.emit(out, t[1])
                    else:
                        self.progress.emit(pct, f"Батч {completed}/{total} (ошибка)")
                    bump()
            else:
                with ThreadPoolExecutor(max_workers=bcfg["threads"]) as ex:
                    futs = {ex.submit(build_task, t): t for t in tasks}
                    for fut in as_completed(futs):
                        if self._stop:
                            break
                        t = futs[fut]
                        out = fut.result()
                        with completed_lock:
                            completed += 1
                            pct = int(completed / total * 88)
                        if out:
                            self.progress.emit(
                                pct, f"Батч {completed}/{total} готов (потоков {bcfg['threads']})")
                            self.file_done.emit(out, t[1])
                        else:
                            self.progress.emit(
                                pct, f"Батч {completed}/{total} (ошибка)")
            self.progress.emit(100, f"Батч завершён: {completed}/{total}")
            self.finished_ok.emit(OUTPUT_DIR)
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(str(e))

# ======================================================================
#  MAIN WINDOW
# ======================================================================

class MainWindow(QMainWindow):
    ffmpegReady = pyqtSignal(str)     # thread -> GUI bridge
    tgResult = pyqtSignal(bool, str, str, str)   # ok, info, char_name, fname

    def __init__(self):
        super().__init__()
        self.ffmpegReady.connect(self._ffmpeg_found)
        self.tgResult.connect(self._on_tg_result)
        self.setWindowTitle(f"{APP_NAME} — Batch Colored Preset Edition {APP_VERSION}")
        self.setMinimumSize(1280, 860)
        self.resize(1500, 960)
        self.setStyleSheet(QSS)

        self.next_build_index = 0
        self.video_counter = 1
        self._busy = False
        self._failed_shown = False

        ensure_dirs()
        self.project = load_project()

        # ---- autosave
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(5000)
        self._save_timer.timeout.connect(self.save_project)
        self._save_timer.start()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(700)
        self._debounce.timeout.connect(self.save_project)
        self._batch_debounce = QTimer(self)
        self._batch_debounce.setSingleShot(True)
        self._batch_debounce.setInterval(500)
        self._batch_debounce.timeout.connect(self.save_project)

        self._build_ui()
        self._load_project_into_ui()

        # ---- background jobs at startup
        # thumbnail loader for the filmstrip
        self.thumb_loader = _ThumbLoader()
        self.thumb_loader.loaded.connect(self._on_thumb_loaded)
        self._thumb_debounce = QTimer(self)
        self._thumb_debounce.setSingleShot(True)
        self._thumb_debounce.setInterval(400)
        self._thumb_debounce.timeout.connect(self._refresh_thumbnails)

        self._startup_threads()

    # ------------------------------------------------------------ UI --
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ============ header (2 rows — nothing can overlap) ============
        header = QFrame()
        header.setObjectName("CardHeader")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(16, 8, 16, 8)
        hlay.setSpacing(4)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        title = QLabel(f"🎬 {APP_NAME}")
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 17px;")
        ver = QLabel(APP_VERSION)
        ver.setObjectName("Hint")
        row1.addWidget(title)
        row1.addWidget(ver)
        row1.addStretch(1)
        self.btn_build = QPushButton("▶ Build")
        self.btn_build.setObjectName("Primary")
        self.btn_build.setToolTip("Собрать одно видео (5 сегментов)")
        self.btn_build.setMinimumWidth(120)
        self.btn_build.clicked.connect(self.on_build)
        row1.addWidget(self.btn_build)
        self.btn_batch = QPushButton("BuildBatch")
        self.btn_batch.setObjectName("BatchBtn")
        self.btn_batch.setToolTip("Собрать батч по всем персонажам")
        self.btn_batch.setMinimumWidth(130)
        self.btn_batch.clicked.connect(self.on_build_batch)
        row1.addWidget(self.btn_batch)
        hlay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.status_label = QLabel("Готов к работе")
        self.status_label.setObjectName("Info")
        self.status_label.setMinimumWidth(120)
        row2.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setMinimumWidth(140)
        self.progress.setMaximumWidth(360)
        self.progress.setValue(0)
        row2.addWidget(self.progress)
        hlay.addLayout(row2)
        root.addWidget(header)

        # ============ splitter ============
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        # ---- left sidebar: 4 tabs (each section gets its own scroll) ----
        self.sidebar_host = QWidget()
        self.sidebar_host.setMinimumWidth(336)
        self.sidebar_host.setMaximumWidth(400)
        self.sidebar_host.setSizePolicy(QSizePolicy.Policy.Preferred,
                                        QSizePolicy.Policy.Expanding)
        sbl = QVBoxLayout(self.sidebar_host)
        sbl.setContentsMargins(0, 0, 0, 0)
        sbl.setSpacing(0)

        self.export_card = ExportCard(self)
        self.characters_card = CharactersCard(self)
        self.batch_card = BatchCard(self)
        self.ffmpeg_card = FfmpegCard(self)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setObjectName("SidebarTabs")
        self.sidebar_tabs.setDocumentMode(True)
        self._sidebar_pages = []
        for label, card in [
            ("Экспорт", self.export_card),
            ("Персонажи", self.characters_card),
            ("Батч", self.batch_card),
            ("FFMPEG", self.ffmpeg_card),
        ]:
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.Shape.NoFrame)
            page.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page.setWidget(card)
            self.sidebar_tabs.addTab(page, label)
            self._sidebar_pages.append(page)
        sbl.addWidget(self.sidebar_tabs)
        splitter.addWidget(self.sidebar_host)

        # ---- right panel
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        # ============ filmstrip — mini previews of all 5 segments ============
        self.filmstrip = SegmentFilmstrip(self)
        self.filmstrip.segmentSelected.connect(self._select_segment)
        rl.addWidget(self.filmstrip)

        # ============ stacked segment editor ============
        self.stack = QStackedWidget()
        self.cards: List[SegmentCard] = []
        for i in range(5):
            card = SegmentCard(i, self)
            card.configChanged.connect(self._on_config_changed)
            self.cards.append(card)
            wrap = QScrollArea()
            wrap.setWidgetResizable(True)
            wrap.setFrameShape(QFrame.Shape.NoFrame)
            wrap.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            wrap.setWidget(card)
            self.stack.addWidget(wrap)
        self.stack.setCurrentIndex(0)
        self.filmstrip.set_active(0)
        rl.addWidget(self.stack, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 1100])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self._splitter = splitter
        root.addWidget(splitter, 1)

        # signal wiring
        self.export_card.configChanged.connect(self._on_config_changed)
        self.characters_card.configChanged.connect(self._on_batch_config_changed)
        self.batch_card.configChanged.connect(self._on_batch_config_changed)
        self.ffmpeg_card.configChanged.connect(self._on_config_changed)

        self.stack.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, idx: int):
        self.filmstrip.set_active(idx)
        # only the visible segment plays; stop others to save CPU
        for i, card in enumerate(self.cards):
            if i != idx:
                card.preview.stop_playback()
            else:
                card.preview.setFocus()
                if card.video_path and os.path.isfile(card.video_path):
                    if not card.preview._thread or not card.preview._thread.is_alive():
                        card.preview.load_video(card.video_path, card._orig_dur)

    def _select_segment(self, idx: int):
        if 0 <= idx < self.stack.count():
            self.stack.setCurrentIndex(idx)

    def is_active_segment(self, idx: int) -> bool:
        return self.stack.currentIndex() == idx

    def _refresh_thumbnails(self):
        if not hasattr(self, "thumb_loader"):
            return
        for i, card in enumerate(self.cards):
            if not card.video_path or card.econ:
                self.filmstrip.set_thumb(i, None,
                                         os.path.basename(card.video_path) if card.video_path else "",
                                         card._orig_dur)
            else:
                load_thumb_async(i, card.video_path, self.thumb_loader)

    # -------------------------------------------------- project I/O --
    def _on_config_changed(self):
        if not self._busy:
            self._debounce.start()
            self._thumb_debounce.start()
            try:
                econ = self.export_card.chk_econ.isChecked()
                for card in self.cards:
                    card.econ = econ
                    card.preview.set_econ(econ)
                if econ:
                    for i, card in enumerate(self.cards):
                        self.filmstrip.set_thumb(i, None, "", card._orig_dur)
            except Exception:
                pass

    def _on_batch_config_changed(self):
        if not self._busy:
            self._batch_debounce.start()
            QTimer.singleShot(50, self.batch_card.update_info)

    def save_project(self):
        self.project = {
            "segments": [c.get_config() for c in self.cards],
            "export": self.export_card.get_export_config(),
            "batch": self.batch_card.get_batch_config(),
            "ui": {
                "tab": self.stack.currentIndex(),
                "sidebar_tab": self.sidebar_tabs.currentIndex(),
                "next_build_index": self.next_build_index,
                "video_counter": self.video_counter,
            },
            "ffmpeg_path": load_saved_ffmpeg_path(),
        }
        save_json(PROJECT_JSON, self.project)

    def _load_project_into_ui(self):
        try:
            self._busy = True
            try:
                segs = self.project.get("segments")
                if isinstance(segs, list) and len(segs) == 5:
                    for i, cfg in enumerate(segs):
                        self.cards[i].set_config(cfg)
            except Exception:
                traceback.print_exc()
            try:
                self.export_card.set_export_config(self.project.get("export", {}))
            except Exception:
                traceback.print_exc()
            try:
                self.batch_card.set_batch_config(self.project.get("batch", {}))
            except Exception:
                traceback.print_exc()
            ui = self.project.get("ui", {}) if isinstance(
                self.project.get("ui", {}), dict) else {}
            self.next_build_index = max(0, _to_int(ui.get("next_build_index"), 0))
            self.video_counter = max(1, _to_int(ui.get("video_counter"), 1))
            self.stack.setCurrentIndex(min(4, max(0, _to_int(ui.get("tab"), 0))))
        except Exception:
            traceback.print_exc()
        finally:
            self._busy = False
        try:
            self.batch_card.update_info()
        except Exception:
            pass
        try:
            ui = self.project.get("ui", {}) if isinstance(
                self.project.get("ui", {}), dict) else {}
            side_tab = _to_int(ui.get("sidebar_tab"), 0)
            self.sidebar_tabs.setCurrentIndex(min(3, max(0, side_tab)))
        except Exception:
            pass

    # ------------------------------------------------------ actions --
    def status(self, msg: str, bad: bool = False):
        self.status_label.setText(msg)
        if bad:
            self.status_label.setStyleSheet("color: #ff6b81;")
        else:
            self.status_label.setStyleSheet("")

    def on_build(self):
        if self._busy:
            self.status("Уже выполняется задача…", bad=True)
            return
        self._busy = True
        self._set_buttons_enabled(False)
        self.progress.setValue(0)
        self.status("▶ Сборка…")
        self.worker = BuildWorker(self)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished_ok.connect(self._on_build_ok)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()

    def on_build_batch(self):
        if self._busy:
            self.status("Уже выполняется задача…", bad=True)
            return
        bcfg = self.batch_card.get_batch_config()
        if not bcfg["enabled"]:
            if QMessageBox.question(
                    self, APP_NAME,
                    "Батч-режим выключен. Запустить всё равно (все персонажи)?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self._busy = True
        self._set_buttons_enabled(False)
        self.progress.setValue(0)
        self.status("⚡ Батч-сборка…")
        self.batch_worker = BatchBuildWorker(self)
        self.batch_worker.progress.connect(self._on_worker_progress)
        self.batch_worker.file_done.connect(self._on_batch_file_done)
        self.batch_worker.finished_ok.connect(self._on_batch_ok)
        self.batch_worker.failed.connect(self._on_worker_failed)
        self.batch_worker.finished.connect(self._on_worker_done)
        self.batch_worker.start()

    def _set_buttons_enabled(self, on: bool):
        self.btn_build.setEnabled(on)
        self.btn_batch.setEnabled(on)

    def _on_worker_progress(self, pct: int, msg: str):
        self.progress.setValue(max(0, min(100, pct)))
        self.status(msg)

    def _on_build_ok(self, path: str):
        self.status(f"✅ Готово: {os.path.basename(path)}")
        self.progress.setValue(100)
        self.auto_send_file(path)

    def _on_batch_file_done(self, path: str, char: str):
        self.status(f"📦 {os.path.basename(path)} — {char}")
        self.auto_send_file(path, char)

    def _on_batch_ok(self, out_dir: str):
        self.status(f"✅ Батч готов — смотри папку output")
        self.progress.setValue(100)

    def _on_worker_failed(self, msg: str):
        self.status("❌ Ошибка", bad=True)
        self._failed_shown = True
        QMessageBox.critical(self, APP_NAME, msg[:2000])

    def _on_worker_done(self):
        self._busy = False
        self._failed_shown = False
        self._set_buttons_enabled(True)

    # --------------------------------------------------- telegram send
    def auto_send_file(self, out_path: str, char_name: str = None):
        """Auto-send a finished file to Telegram (GUI thread; worker does HTTP).

        Auto-fixes an outdated chat id when Telegram reports the group was
        upgraded to a supergroup (migrate_to_chat_id).
        """
        try:
            chars = self.characters_card.chars
            bot_token = str(chars.get("bot_token", "") or "")
            if not chars.get("auto_send", False) or not bot_token:
                return
            if not char_name:
                sel = list(chars.get("selected_characters", []))
                if not sel:
                    sel = [c[1] for c in list_character_folders()]
                for name in sel + ["default"]:
                    cid = str(chars.get("characters", {}).get(name, {}).get("chat_id", "") or "")
                    if cid:
                        char_name = name
                        break
            if not char_name:
                self.status("Нет Chat ID — отправка в Telegram пропущена", bad=True)
                return
            chat_id = str(chars.get("characters", {}).get(char_name, {}).get("chat_id", "") or "")
            if not chat_id:
                self.status(f"Нет Chat ID у {char_name} — отправка пропущена", bad=True)
                return
            self.status(f"📤 Отправляю {os.path.basename(out_path)} в Telegram ({char_name})…")

            def work():
                ok, info = send_video_via_telegram(
                    bot_token, chat_id, out_path, caption=char_name)
                self.tgResult.emit(ok, info, char_name, os.path.basename(out_path))
            threading.Thread(target=work, daemon=True).start()
        except Exception as e:
            print("[telegram] auto_send_file:", e)

    def _on_tg_result(self, ok: bool, info: str, char_name: str, fname: str):
        if ok:
            if info.startswith("migrated:"):
                new_id = info.split(":", 1)[1]
                chars = self.characters_card.chars
                chars.setdefault("characters", {}).setdefault(char_name, {})["chat_id"] = new_id
                save_characters(chars)
                self.characters_card.refresh()
                self.status(f"✅ {fname} отправлен ({char_name}). Chat ID обновлён → {new_id}")
            else:
                self.status(f"✅ {fname} отправлен в Telegram ({char_name})")
        else:
            self.status(f"❌ Telegram: {info[:90]}", bad=True)

    # ------------------------------------------------------ helpers --
    def build_worker_canvas(self, exp: Dict[str, Any]) -> Tuple[int, int]:
        res = exp["resolution"]
        if res["w"] > 0:
            return res["w"], res["h"]
        # "Оригинал" — probe the first video available in folder_1
        probe = list_videos(FOLDER_DIRS[0])
        if not probe:
            for path, _, _ in list_character_folders():
                probe = list_videos(path)
                if probe:
                    break
        if probe:
            w, h = get_video_size(probe[0], get_ffmpeg_exe())
            if w > 0:
                return w, h
        return 1080, 1920

    def peek_next_batch(self, chars: List[Tuple[str, int, int]]) -> str:
        """Preview of the next batch: character video names + others."""
        try:
            parts = []
            for name, total, m in chars:
                folder = os.path.join(FOLDER_DIRS[0], name)
                vids = list_videos(folder)
                if not vids:
                    continue
                idx = self.next_build_index % len(vids)
                first = os.path.basename(vids[idx])
                parts.append(f"{name} → {first} (+{max(0, m - 1)} ещё)")
            return "След.: " + "; ".join(parts[:3]) if parts else ""
        except Exception:
            return ""

    def closeEvent(self, ev):
        self.save_project()
        try:
            for card in self.cards:
                card.preview.stop_playback()
        except Exception:
            pass
        try:
            if hasattr(self, "worker") and self.worker.isRunning():
                self.worker.stop()
        except Exception:
            pass
        super().closeEvent(ev)

    # --------------------------------------------------- startup bg --
    def _startup_threads(self):
        # ffmpeg find/download in background
        def find_ff():
            try:
                exe = get_ffmpeg_exe()
                if is_valid_ffmpeg(exe):
                    self.ffmpegReady.emit(exe)
            except Exception:
                pass
        threading.Thread(target=find_ff, daemon=True).start()

        # fonts download in background
        def fonts():
            try:
                download_fonts()
                for fp in (FONT_ANTON, FONT_OSWALD):
                    if os.path.isfile(fp):
                        QFontDatabase.addApplicationFont(fp)
            except Exception:
                pass
        threading.Thread(target=fonts, daemon=True).start()

        # video counters / chars
        QTimer.singleShot(300, self.characters_card.refresh)
        QTimer.singleShot(400, self.batch_card.update_info)
        # filmstrip thumbnails for the 5 segments
        QTimer.singleShot(500, self._refresh_thumbnails)

    def _on_thumb_loaded(self, idx: int, img, name: str, dur: float):
        try:
            self.filmstrip.set_thumb(idx, img, name, dur)
            card = self.cards[idx]
            if dur > 0 and not card._orig_dur:
                card._orig_dur = dur
        except Exception:
            pass

    def _ffmpeg_found(self, exe: str):
        self.ffmpeg_card.set_status_ok(exe)

# ======================================================================
#  ENTRY POINT
# ======================================================================

def main():
    ensure_dirs()
    if not os.path.isfile(FONT_ANTON) or not os.path.isfile(FONT_OSWALD):
        # try fonts synchronously-ish (fast, small files)
        try:
            download_fonts()
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("VideoTool")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    # Windows-консоль в cp1251/cp1252 (в т.ч. при выводе в файл):
    # не падаем на печати эмодзи/русского текста.
    for _stream in (sys.stdout, sys.stderr):
        try:
            if _stream is not None and hasattr(_stream, "reconfigure"):
                _stream.reconfigure(errors="replace")
        except Exception:
            pass
    main()
