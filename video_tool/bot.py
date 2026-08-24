#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
Video Stitcher Bot — интерактивный Telegram-бот
=====================================================================
Больше не нужно привязывать бота к какому-то чату: любой, кто пишет
боту, получает меню и готовые видео прямо в свой чат.

Логика:
  • Подпапки folder_1 = «папки» юзера (хуки). Создаёт их сам —
    прямо из бота или руками на диске, называет как хочет (1, 2, 3…).
  • Видео кладутся руками на сервере: хуки — в свою подпапку
    folder_1/<имя>/, тело ролика — folder_2 … folder_5.
  • В боте: выбрал папку → кол-во видео → режим с удалением из
    folder_2-5 или без → рандом видео из папок 2-5 или по порядку →
    рандом хук (рандом видео именно из ВЫБРАННОЙ подпапки folder_1)
    или по порядку → длительность каждого участка с точностью до
    миллисекунд → 🚀 Создать.
  • Бот собирает и отправляет готовые видео сразу в чат, в подписи —
    из какой папки собрано.

Тексты / пресеты / настройки экспорта берутся из project.json
(настраиваются в GUI main.py как раньше). Токен — из characters.json
(bot_token) или переменной окружения BOT_TOKEN.

Запуск:  python bot.py
=====================================================================
"""

import os
import sys
import json
import time
import random
import re
import subprocess
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple

import requests

# ----------------------------------------------------------------------
# main.py импортирует PyQt6 на уровне модуля. Боту GUI не нужен, поэтому
# если PyQt6 не установлен / не загружается (headless-сервер без libGL) —
# подставляем заглушки, чтобы импорт рендер-движка не падал.
# ----------------------------------------------------------------------
def _ensure_qt() -> None:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
        return
    except Exception:
        pass
    import types

    class _StubMeta(type):
        def __getattr__(cls, name):
            return cls

    class _Stub(metaclass=_StubMeta):
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            return _Stub()

        def __getattr__(self, name):
            return _Stub()

    def _make_mod(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)

        def __getattr__(attr, _m=mod):  # PEP 562
            return _Stub

        mod.__getattr__ = __getattr__  # type: ignore[attr-defined]
        return mod

    pkg = types.ModuleType("PyQt6")
    pkg.__path__ = []  # помечаем как пакет
    sys.modules["PyQt6"] = pkg
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        m = _make_mod(f"PyQt6.{sub}")
        sys.modules[f"PyQt6.{sub}"] = m
        setattr(pkg, sub, m)
    print("⚠️ PyQt6 недоступен — бот работает в headless-режиме (это норм).")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ensure_qt()

# --- рендер-движок и хелперы из основного приложения -----------------
from main import (
    BASE_DIR, FOLDER_DIRS, FOLDERS, OUTPUT_DIR,
    DEFAULT_PRESET, VIDEO_EXTS,
    ensure_dirs, natural_key, list_videos,
    load_project, load_characters, save_characters,
    get_ffmpeg_exe, is_valid_ffmpeg, get_video_duration,
    build_one_final_ffmpeg, build_segment_ffmpeg, download_fonts,
    FONT_ANTON, FONT_OSWALD,
)

API = "https://api.telegram.org/bot{token}/{method}"

POLL_TIMEOUT = 50          # long polling, сек
MAX_COUNT = 500            # максимум видео за один заказ
IGNORED_FOLDER_NAMES = {".", "..", "used", "bin", "fonts", "__pycache__"}

# ======================================================================
#  TELEGRAM API (минимальный клиент на requests)
# ======================================================================

class Tg:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, timeout: int = 65, **params) -> Dict[str, Any]:
        url = API.format(token=self.token, method=method)
        try:
            r = requests.post(url, json=params, timeout=timeout)
            return r.json()
        except Exception as e:
            return {"ok": False, "description": str(e)}

    def get_updates(self, offset: int) -> List[Dict[str, Any]]:
        url = API.format(token=self.token, method="getUpdates")
        try:
            r = requests.post(url, json={
                "offset": offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=POLL_TIMEOUT + 15)
            j = r.json()
        except Exception:
            return []
        if j.get("ok"):
            return j.get("result", [])
        return []

    def send(self, chat_id: int, text: str,
             kb: Optional[List[List[Dict[str, str]]]] = None,
             md: bool = True) -> Optional[int]:
        params: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if md:
            params["parse_mode"] = "HTML"
        if kb:
            params["reply_markup"] = {"inline_keyboard": kb}
        j = self.call("sendMessage", **params)
        if j.get("ok"):
            return j["result"]["message_id"]
        return None

    def edit(self, chat_id: int, message_id: int, text: str,
             kb: Optional[List[List[Dict[str, str]]]] = None) -> None:
        params: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id,
                                  "text": text, "parse_mode": "HTML"}
        if kb is not None:
            params["reply_markup"] = {"inline_keyboard": kb}
        self.call("editMessageText", **params)

    def answer_cb(self, cb_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", callback_query_id=cb_id, text=text)

    def send_document(self, chat_id: int, file_path: str,
                      caption: str = "") -> Tuple[bool, str]:
        url = API.format(token=self.token, method="sendDocument")
        try:
            with open(file_path, "rb") as f:
                # application/octet-stream => Telegram шлёт ИМЕННО файлом
                # (с video/mp4 клиенты показывают его как видео с плеером)
                files = {"document": (os.path.basename(file_path), f,
                                      "application/octet-stream")}
                data = {"chat_id": chat_id, "caption": caption,
                        "disable_content_type_detection": True,
                        "disable_notification": True}
                r = requests.post(url, data=data, files=files, timeout=600)
            if r.ok and r.json().get("ok"):
                return True, "ok"
            return False, r.text[:200]
        except Exception as e:
            return False, str(e)

    def send_action(self, chat_id: int, action: str = "upload_document") -> None:
        self.call("sendChatAction", chat_id=chat_id, action=action)

    def get_file_path(self, file_id: str) -> Optional[str]:
        j = self.call("getFile", file_id=file_id)
        if j.get("ok"):
            return (j.get("result") or {}).get("file_path")
        return None

    def download_file(self, file_path: str, dest: str) -> bool:
        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        try:
            with requests.get(url, stream=True, timeout=600) as r:
                if not r.ok:
                    return False
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if chunk:
                            f.write(chunk)
                os.replace(tmp, dest)
            return os.path.isfile(dest) and os.path.getsize(dest) > 0
        except Exception:
            try:
                os.remove(dest + ".part")
            except Exception:
                pass
            return False


# ======================================================================
#  ПАПКИ ЮЗЕРА (подпапки folder_1)
# ======================================================================

def user_folders() -> List[Tuple[str, str, int]]:
    """Все подпапки folder_1 -> [(path, name, кол-во видео)]."""
    res: List[Tuple[str, str, int]] = []
    f1 = FOLDER_DIRS[0]
    if os.path.isdir(f1):
        try:
            for fn in sorted(os.listdir(f1), key=natural_key):
                full = os.path.join(f1, fn)
                if fn.startswith(".") or fn.lower() in IGNORED_FOLDER_NAMES:
                    continue
                if os.path.isdir(full):
                    res.append((full, fn, len(list_videos(full))))
        except OSError:
            pass
    return res


def safe_folder_name(raw: str) -> str:
    bad = '<>:"/\\|?*'
    name = "".join(c for c in raw.strip() if c not in bad)
    return name[:48]


def body_pools() -> List[List[str]]:
    """Видео из folder_2 … folder_5."""
    return [list_videos(d) for d in FOLDER_DIRS[1:]]


# ======================================================================
#  ПАРСИНГ ДЛИТЕЛЬНОСТЕЙ (точность до миллисекунд)
# ======================================================================

def parse_durations(raw: str, n_seg: int) -> Optional[List[float]]:
    """
    '3.25 1.5 1.5 1.5 2' | '3,25 1,5 …' | '3.25,1.5,…' | одно число на все.
    Округление до миллисекунд.
    """
    s = raw.strip().replace(";", " ")
    if " " in s:
        toks = [t.replace(",", ".") for t in s.split() if t]
    else:
        toks = [t.strip().replace(",", ".") for t in s.split(",") if t.strip()]
        if len(toks) == 1 and "," in s and s.count(",") == 1:
            # одиночное «1,5» уже обработано заменой выше
            pass
    try:
        vals = [round(float(t), 3) for t in toks]
    except Exception:
        return None
    if any(v <= 0 or v > 600 for v in vals):
        return None
    if len(vals) == 1:
        vals = vals * n_seg
    if len(vals) != n_seg:
        return None
    return vals


def fmt_dur(vals: List[float]) -> str:
    return " • ".join(f"{v:.3f}с" for v in vals)


# ======================================================================
#  СОСТОЯНИЯ ДИАЛОГА
# ======================================================================
# sessions[chat_id] = {
#   "state": idle | new_folder | count | durations | ready |
#            upload_pick | upload_newfolder | upload | keep_hook_limit
#   "sel": set(имена выбранных папок), "menu_id": int|None,
#   "folders": [(path, name), ...] — выбранные для генерации,
#   "count": int (видео НА КАЖДУЮ папку),
#   "delete": bool|None, "rand_body": bool|None, "rand_hook": bool|None,
#   "keep_hook": bool, "keep_hook_max": float — хук как есть, если <= X сек,
#   "upload_folder": (path, name), "uploaded": int,
#   "durs": [float]*n
# }

sessions: Dict[int, Dict[str, Any]] = {}
sessions_lock = threading.Lock()
build_lock = threading.Lock()          # один рендер-заказ одновременно
busy_chats: set = set()


def get_session(chat_id: int) -> Dict[str, Any]:
    with sessions_lock:
        return sessions.setdefault(chat_id, {"state": "idle"})


def reset_session(chat_id: int) -> Dict[str, Any]:
    with sessions_lock:
        sessions[chat_id] = {"state": "idle"}
        return sessions[chat_id]


# ======================================================================
#  ЭКРАНЫ / КЛАВИАТУРЫ
# ======================================================================

def kb_main(sel: Optional[set] = None) -> List[List[Dict[str, str]]]:
    """Мульти-выбор папок: клик = toggle галочки, потом «Далее»."""
    sel = sel or set()
    kb: List[List[Dict[str, str]]] = []
    for i, (_p, name, n) in enumerate(user_folders()):
        mark = "✅ " if name in sel else ""
        kb.append([{"text": f"{mark}📁 {name}  ({n} видео)",
                    "callback_data": f"f:{i}"}])
    if sel:
        kb.append([{"text": f"▶️ Далее ({len(sel)} папок)",
                    "callback_data": "next"}])
    kb.append([{"text": "➕ Создать папку", "callback_data": "newfolder"},
               {"text": "🔄 Обновить", "callback_data": "refresh"}])
    kb.append([{"text": "📤 Загрузить хуки", "callback_data": "upload"}])
    return kb


def main_menu_text(sel: Optional[set] = None) -> str:
    sel = sel or set()
    folders = user_folders()
    pools = body_pools()
    lines = ["<b>🎬 Video Stitcher Bot</b>", ""]
    if folders:
        lines.append("<b>📁 Папки (хуки):</b>")
        for _p, name, n in folders:
            mark = "✅ " if name in sel else "• "
            lines.append(f"{mark}{name} — <b>{n}</b> видео")
        lines.append("")
        lines.append("Клик по папке = ✅ выбрать, потом «▶️ Далее».")
        lines.append("📤 «Загрузить хуки» — закинуть видео прямо из Telegram.")
    else:
        lines.append("Папок пока нет — нажми «➕ Создать папку»,")
        lines.append("потом загрузи хуки кнопкой «📤 Загрузить хуки» "
                     "или закинь файлы в <code>folder_1/&lt;имя&gt;/</code>.")
    lines.append("")
    body = " · ".join(f"{FOLDERS[i+1]}: <b>{len(p)}</b>"
                      for i, p in enumerate(pools))
    lines.append(f"🎞 Тело ролика: {body}")
    return "\n".join(lines)


def kb_yes_no(prefix: str, yes: str, no: str) -> List[List[Dict[str, str]]]:
    return [[{"text": yes, "callback_data": f"{prefix}:1"}],
            [{"text": no, "callback_data": f"{prefix}:0"}],
            [{"text": "❌ Отмена", "callback_data": "cancel"}]]


def summary_text(s: Dict[str, Any]) -> str:
    names = [name for _p, name in s["folders"]]
    total = s["count"] * len(names)
    if s.get("parts_mode"):
        uniq_line = ("🧬 Микро-уник: <b>да (невидимый рандом)</b>"
                     if s.get("micro_uniq") else "🧬 Микро-уник: <b>нет</b>")
        n = s["count"]
        total_files = n * (len(names) + len(FOLDER_DIRS) - 1)
        return "\n".join([
            "<b>📋 Заказ — фрагменты по отдельности</b>",
            f"📁 Папки хуков: <b>{', '.join(names)}</b>",
            f"🔢 По <b>{n}</b> каждого типа: {n} хуков из каждой папки + "
            f"по {n} видео из папок 2-5  (всего {total_files} файлов)",
            "🎲 Хуки и видео: <b>всегда рандом</b>",
            f"🗑 Удаление отправленных из folder_2-5: "
            f"<b>{'да' if s['delete'] else 'нет'}</b>",
            "✂️ Без надписей, каждый фрагмент отдельным файлом",
            uniq_line,
            "🧹 Метаданные: <b>очищаются полностью</b>",
            "💎 Отправка: <b>документом, без сжатия</b>",
        ])
    if s.get("hooks_only"):
        uniq_line = ("🧬 Микро-уник: <b>да (невидимый рандом)</b>"
                     if s.get("micro_uniq") else "🧬 Микро-уник: <b>нет</b>")
        return "\n".join([
            "<b>📋 Заказ — только хуки</b>",
            f"📁 Папки: <b>{', '.join(names)}</b>",
            f"🔢 Хуков на папку: <b>{s['count']}</b>  (всего {total})",
            f"🎣 Выбор хуков: <b>{'рандом' if s['rand_hook'] else 'по порядку'}</b>",
            "✂️ Без надписей, без склейки с папками 2-5, хук целиком",
            uniq_line,
            "🧹 Метаданные: <b>очищаются полностью</b>",
            "💎 Отправка: <b>документом, без сжатия</b>",
        ])
    if s.get("keep_hook"):
        hook_line = (f"🎬 Хук без изменений: <b>да, если ≤ "
                     f"{s.get('keep_hook_max', 3.5):.3f}с</b> "
                     "(не ускоряется/не режется)")
    else:
        hook_line = "🎬 Хук без изменений: <b>нет, подгоняется под длительность</b>"
    uniq_line = ("🧬 Микро-уник папок 2-5: <b>да (невидимый рандом)</b>"
                 if s.get("micro_uniq")
                 else "🧬 Микро-уник папок 2-5: <b>нет</b>")
    return "\n".join([
        "<b>📋 Заказ</b>",
        f"📁 Папки (хуки): <b>{', '.join(names)}</b>",
        f"🔢 Видео на папку: <b>{s['count']}</b>  (всего {total})",
        f"🗑 Удаление из folder_2-5: <b>{'да' if s['delete'] else 'нет'}</b>",
        f"🎲 Рандом видео из папок 2-5: <b>{'да' if s['rand_body'] else 'по порядку'}</b>",
        f"🎣 Рандом хук из своей папки: <b>{'да' if s['rand_hook'] else 'по порядку'}</b>",
        hook_line,
        uniq_line,
        f"⏱ Длительности: <b>{fmt_dur(s['durs'])}</b>",
        "🧹 Метаданные: <b>очищаются полностью</b>",
        "💎 Отправка: <b>документом, без сжатия</b>",
    ])


# ======================================================================
#  СБОРКА И ОТПРАВКА
# ======================================================================

def process_hook_only(src: str, out_path: str, ffmpeg_exe: str,
                      micro_uniq: bool) -> Tuple[bool, str]:
    """«Только хуки»: видео целиком, без надписей и склейки.

    Без уника — remux copy в mp4 (качество 1:1). С уником — один
    качественный перекод (CRF 16) с невидимым рандомом.
    """
    try:
        if micro_uniq:
            from main import _probe, _micro_uniq_chain
            info = _probe(src, ffmpeg_exe)
            w = int(info.get("w") or 0) or 1080
            h = int(info.get("h") or 0) or 1920
            # чётные размеры для yuv420p
            w -= w % 2
            h -= h % 2
            chain = _micro_uniq_chain(w, h).rstrip(",")
            cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", src, "-vf", chain,
                   "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                   "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2",
                   "-movflags", "+faststart", out_path]
            r = subprocess.run(cmd, capture_output=True, timeout=1800)
            if r.returncode == 0 and os.path.isfile(out_path) \
                    and os.path.getsize(out_path) > 1024:
                return True, ""
            return False, r.stderr.decode("utf-8", "replace")[-300:]
        # ---- без уника: remux без перекода видео (качество 1:1).
        # аудио copy только если это AAC — иначе (PCM/ALAC из MOV)
        # конвертим в AAC, чтобы mp4 играли все плееры/платформы.
        aac_src = False
        try:
            pr = subprocess.run([ffmpeg_exe, "-hide_banner", "-i", src],
                                capture_output=True, text=True, timeout=30)
            aac_src = bool(re.search(r"Audio:\s*aac\b", pr.stderr))
        except Exception:
            pass
        cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
               "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "copy"]
        if aac_src:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2"]
        cmd += ["-movflags", "+faststart", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode == 0 and os.path.isfile(out_path) \
                and os.path.getsize(out_path) > 1024:
            return True, ""
        return False, r.stderr.decode("utf-8", "replace")[-300:]
    except Exception as e:
        return False, str(e)


def clean_metadata(path: str, ffmpeg_exe: str) -> bool:
    """Полная очистка метаданных БЕЗ перекодирования (качество 1:1).

    Убирает: глобальные теги, теги потоков, chapters, encoder-подписи
    (bitexact), handler names. Remux copy → битрейт/качество не трогаются.
    """
    tmp = path + ".clean.mp4"
    cmd = [
        ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-map", "0",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        "-metadata:s:v", "handler_name=",
        "-metadata:s:a", "handler_name=",
        "-movflags", "+faststart",
        "-c", "copy",
        tmp,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return True
    except Exception:
        pass
    try:
        os.remove(tmp)
    except Exception:
        pass
    return False


def load_render_config() -> Tuple[List[List[Dict[str, Any]]], Dict[str, Any], int]:
    """Пресеты текста по сегментам + экспорт из project.json."""
    proj = load_project()
    segments = proj.get("segments") or []
    n_seg = max(1, len(FOLDER_DIRS))
    presets: List[List[Dict[str, Any]]] = []
    for i in range(n_seg):
        seg = segments[i] if i < len(segments) and isinstance(segments[i], dict) else {}
        p = seg.get("presets") or [dict(DEFAULT_PRESET)]
        presets.append(p)
    exp = proj.get("export") or {}
    return presets, exp, n_seg


def run_order(tg: Tg, chat_id: int, s: Dict[str, Any]) -> None:
    """Фоновый поток: для КАЖДОЙ выбранной папки собрать N видео,
    и отправить их ПАЧКОЙ, когда вся папка готова (не по одному).
    """
    folders: List[Tuple[str, str]] = s["folders"]
    count: int = s["count"]
    delete: bool = s["delete"]
    rand_body: bool = s["rand_body"]
    rand_hook: bool = s["rand_hook"]
    keep_hook: bool = bool(s.get("keep_hook"))
    keep_hook_max: float = float(s.get("keep_hook_max", 3.5) or 3.5)
    micro_uniq: bool = bool(s.get("micro_uniq"))
    hooks_only: bool = bool(s.get("hooks_only"))
    parts_mode: bool = bool(s.get("parts_mode"))
    durs: List[float] = s["durs"]

    status_id = tg.send(chat_id, "⏳ Готовлюсь к сборке…")

    def status(txt: str) -> None:
        if status_id:
            tg.edit(chat_id, status_id, txt)

    try:
        ff = get_ffmpeg_exe()
        if not is_valid_ffmpeg(ff):
            status("❌ ffmpeg не найден на сервере.")
            return

        presets, exp, _n = load_render_config()

        pools = body_pools()
        if not hooks_only and not parts_mode:
            empty = [FOLDERS[i + 1] for i, p in enumerate(pools) if not p]
            if empty:
                status("❌ Пустые папки тела ролика: " + ", ".join(empty))
                return

        res_w = int((exp.get("resolution") or {}).get("w", 1080) or 1080)
        res_h = int((exp.get("resolution") or {}).get("h", 1920) or 1920)
        fps = int(exp.get("fps", 30) or 30)
        # 💎 наивысшее качество: CRF не хуже 16, медленный пресет
        crf = min(int(exp.get("crf", 16) or 16), 16)
        preset = "medium"
        with_audio = bool(exp.get("audio", True))
        uppercase = bool(exp.get("uppercase", False))
        ten_bit = bool(exp.get("ten_bit", False))
        blur_fill = bool(exp.get("blur_fill", False))

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        total_made, total_sent, errors = 0, 0, []
        notes: List[str] = []
        t0 = time.time()
        n_folders = len(folders)

        # ================= 🧩 режим «фрагменты по отдельности» =========
        if parts_mode:
            def send_batch(src_list: List[str], label: str,
                           tag: str, delete_after: bool,
                           pool_ref: Optional[List[str]] = None) -> None:
                nonlocal total_made, total_sent
                if not src_list:
                    return
                sent_files: List[Tuple[str, float]] = []
                for i, src in enumerate(src_list, start=1):
                    status(f"🧩 {label}: обрабатываю {i}/{len(src_list)}…")
                    out_path = os.path.join(
                        OUTPUT_DIR, f"{tag}_{i:04d}.mp4")
                    k = 1
                    while os.path.exists(out_path):
                        out_path = os.path.join(
                            OUTPUT_DIR, f"{tag}_{i:04d}_{k:03d}.mp4")
                        k += 1
                    ok, err = process_hook_only(src, out_path, ff,
                                                micro_uniq)
                    if not ok:
                        errors.append(f"{label} #{i}: {err[:100]}")
                        continue
                    clean_metadata(out_path, ff)
                    hd = get_video_duration(out_path, ff)
                    sent_files.append((out_path, hd))
                    total_made += 1
                    if delete_after:
                        try:
                            os.remove(src)
                        except Exception:
                            pass
                        if pool_ref is not None:
                            try:
                                pool_ref.remove(src)
                            except ValueError:
                                pass
                if sent_files:
                    status(f"📤 {label}: отправляю {len(sent_files)}…")
                    tg.send(chat_id, f"📦 <b>{label}</b> — "
                                     f"{len(sent_files)} файлов 👇")
                    for k, (fp, hd) in enumerate(sent_files, start=1):
                        tg.send_action(chat_id)
                        u = " • 🧬 уник" if micro_uniq else ""
                        cap = (f"🧩 {label} • {k}/{len(sent_files)}\n"
                               f"⏱ {hd:.3f}с • 🧹 без метаданных{u}")
                        oks, info = tg.send_document(chat_id, fp, cap)
                        if oks:
                            total_sent += 1
                        else:
                            errors.append(f"отправка {label} #{k}: {info}")

            with build_lock:
                # --- хуки: по count из КАЖДОЙ выбранной папки, рандом ---
                for folder_path, folder_name in folders:
                    hooks = list_videos(folder_path)
                    if not hooks:
                        errors.append(f"«{folder_name}»: нет видео")
                        continue
                    n = min(count, len(hooks))
                    if n < count:
                        notes.append(f"хуков «{folder_name}» только {n}")
                    picks = random.sample(hooks, n)
                    send_batch(picks, f"Хуки «{folder_name}»",
                               f"{folder_name}_hook", False)
                # --- папки 2-5: по count из каждой, рандом ---
                for pi, pool in enumerate(pools):
                    fname = FOLDERS[pi + 1]
                    if not pool:
                        errors.append(f"{fname}: пусто")
                        continue
                    n = min(count, len(pool))
                    if n < count:
                        notes.append(f"в {fname} только {n}")
                    picks = random.sample(pool, n)
                    send_batch(picks, f"Папка {pi + 2} ({fname})",
                               f"{fname}", delete, pool_ref=pool)

            dt = time.time() - t0
            lines = [f"✅ Готово: обработано <b>{total_made}</b>, "
                     f"отправлено <b>{total_sent}</b> фрагментов "
                     f"за {dt:.0f} сек."]
            if notes:
                lines.append("⚠️ " + "\n⚠️ ".join(notes[:4]))
            if errors:
                lines.append("⚠️ Ошибки:\n"
                             + "\n".join("• " + e for e in errors[:5]))
            status("\n".join(lines))
            return

        with build_lock:
            for fi, (folder_path, folder_name) in enumerate(folders, start=1):
                hooks = list_videos(folder_path)
                if not hooks:
                    errors.append(f"«{folder_name}»: нет видео в папке")
                    continue

                # ---------- 🎣 режим «только хуки» ----------
                if hooks_only:
                    real_count = min(count, len(hooks)) if not rand_hook \
                        else count
                    if real_count < count and not rand_hook:
                        notes.append(f"«{folder_name}»: хуков в папке "
                                     f"только {len(hooks)}")
                    ready: List[Tuple[str, List[float]]] = []
                    for i in range(real_count):
                        status(f"🎣 [{fi}/{n_folders}] Папка "
                               f"«{folder_name}»: обрабатываю "
                               f"{i + 1}/{real_count}…")
                        hook = (random.choice(hooks) if rand_hook
                                else hooks[i % len(hooks)])
                        out_path = os.path.join(
                            OUTPUT_DIR, f"{folder_name}_hook_{i + 1:04d}.mp4")
                        k = 1
                        while os.path.exists(out_path):
                            out_path = os.path.join(
                                OUTPUT_DIR,
                                f"{folder_name}_hook_{i + 1:04d}_{k:03d}.mp4")
                            k += 1
                        ok, err = process_hook_only(hook, out_path, ff,
                                                    micro_uniq)
                        if not ok:
                            errors.append(f"«{folder_name}» #{i + 1}: "
                                          f"{err[:100]}")
                            continue
                        clean_metadata(out_path, ff)
                        hd = get_video_duration(out_path, ff)
                        ready.append((out_path, [round(hd, 3)]))
                        total_made += 1
                    if ready:
                        status(f"📤 [{fi}/{n_folders}] Папка "
                               f"«{folder_name}»: отправляю "
                               f"{len(ready)} хуков…")
                        tg.send(chat_id,
                                f"📁 <b>{folder_name}</b> готова — "
                                f"{len(ready)} хуков 👇")
                        for k, (fp, fdurs) in enumerate(ready, start=1):
                            tg.send_action(chat_id)
                            u = " • 🧬 уник" if micro_uniq else ""
                            cap = (f"🎣 {folder_name} • {k}/{len(ready)}\n"
                                   f"⏱ {fdurs[0]:.3f}с • чистый хук • "
                                   f"🧹 без метаданных{u}")
                            oks, info = tg.send_document(chat_id, fp, cap)
                            if oks:
                                total_sent += 1
                            else:
                                errors.append(f"отправка «{folder_name}» "
                                              f"#{k}: {info}")
                    continue

                # при удалении максимум = самый маленький пул folder_2-5
                limit = min(len(p) for p in pools) if pools else 0
                real_count = count
                if delete and count > limit:
                    real_count = limit
                    notes.append(f"«{folder_name}»: с удалением хватило "
                                 f"только на {limit} шт.")
                if real_count <= 0:
                    errors.append(f"«{folder_name}»: в папках 2-5 "
                                  "кончились видео")
                    continue

                # ---------- 1) собираем ВСЕ видео этой папки ----------
                ready: List[Tuple[str, List[float]]] = []
                for i in range(real_count):
                    status(f"🎬 [{fi}/{n_folders}] Папка «{folder_name}»: "
                           f"собираю {i + 1}/{real_count}…")

                    hook = (random.choice(hooks) if rand_hook
                            else hooks[i % len(hooks)])

                    # ---- 🎬 хук без изменений: если он не длиннее лимита,
                    #      сегмент 1 получает длительность = длине хука
                    #      (не ускоряется, не замедляется, не режется)
                    cur_durs = list(durs)
                    if keep_hook:
                        hd = get_video_duration(hook, ff)
                        if 0.05 < hd <= keep_hook_max + 0.005:
                            cur_durs[0] = round(hd, 3)

                    vps = [hook]
                    used_body: List[str] = []
                    ok_pick = True
                    for pool in pools:
                        if not pool:
                            ok_pick = False
                            break
                        v = (random.choice(pool) if rand_body
                             else pool[i % len(pool)])
                        vps.append(v)
                        used_body.append(v)
                    if not ok_pick:
                        errors.append(f"«{folder_name}»: в папках 2-5 "
                                      "кончились видео")
                        break

                    out_name = f"{folder_name}_{i + 1:04d}.mp4"
                    out_path = os.path.join(OUTPUT_DIR, out_name)
                    k = 1
                    while os.path.exists(out_path):
                        out_path = os.path.join(
                            OUTPUT_DIR,
                            f"{folder_name}_{i + 1:04d}_{k:03d}.mp4")
                        k += 1

                    ok, err = build_one_final_ffmpeg(
                        vps, presets, cur_durs, out_path, ff,
                        resolution=(res_w, res_h) if res_w and res_h
                        else (1080, 1920),
                        fps=fps, crf=crf, preset=preset,
                        with_audio=with_audio, uppercase=uppercase,
                        ten_bit=ten_bit, blur_fill=blur_fill,
                        audio_kbps=256,
                        random_flags=None, preset_indices=None,
                        micro_uniq=micro_uniq)
                    if not ok:
                        errors.append(f"«{folder_name}» #{i + 1}: {err[:100]}")
                        continue

                    # ---- 🧹 полная очистка метаданных (без потери качества)
                    clean_metadata(out_path, ff)
                    ready.append((out_path, cur_durs))
                    total_made += 1

                    # ---- удаление использованных из folder_2-5 ----
                    if delete:
                        for pi, v in enumerate(used_body):
                            try:
                                pools[pi].remove(v)
                            except ValueError:
                                pass
                            try:
                                os.remove(v)
                            except Exception:
                                pass

                # ---------- 2) папка готова → шлём ВСЮ пачку разом ----------
                if ready:
                    status(f"📤 [{fi}/{n_folders}] Папка «{folder_name}»: "
                           f"отправляю {len(ready)} видео…")
                    tg.send(chat_id,
                            f"📁 <b>{folder_name}</b> готова — "
                            f"{len(ready)} видео 👇")
                    for k, (fp, fdurs) in enumerate(ready, start=1):
                        tg.send_action(chat_id)
                        u = " • 🧬 уник" if micro_uniq else ""
                        cap = (f"📁 {folder_name} • {k}/{len(ready)}\n"
                               f"⏱ {fmt_dur(fdurs)} • 🧹 без метаданных{u}")
                        oks, info = tg.send_document(chat_id, fp, cap)
                        if oks:
                            total_sent += 1
                        else:
                            errors.append(f"отправка «{folder_name}» "
                                          f"#{k}: {info}")

        dt = time.time() - t0
        names = ", ".join(n for _p, n in folders)
        lines = [f"✅ Готово: собрано <b>{total_made}</b>, отправлено "
                 f"<b>{total_sent}</b> из папок <b>{names}</b> "
                 f"за {dt:.0f} сек."]
        if notes:
            lines.append("⚠️ " + "\n⚠️ ".join(notes[:3]))
        if errors:
            lines.append("⚠️ Ошибки:\n" + "\n".join("• " + e for e in errors[:5]))
        status("\n".join(lines))
    except Exception as e:
        traceback.print_exc()
        status(f"❌ Ошибка: {e}")
    finally:
        busy_chats.discard(chat_id)
        show_main_menu(tg, chat_id)


# ======================================================================
#  ОБРАБОТКА АПДЕЙТОВ
# ======================================================================

def show_main_menu(tg: Tg, chat_id: int) -> None:
    s = reset_session(chat_id)
    s["sel"] = set()
    s["menu_id"] = tg.send(chat_id, main_menu_text(), kb_main())


def refresh_menu(tg: Tg, chat_id: int, s: Dict[str, Any]) -> None:
    """Перерисовать меню выбора папок на месте (toggle галочек)."""
    sel = s.get("sel") or set()
    mid = s.get("menu_id")
    if mid:
        tg.edit(chat_id, mid, main_menu_text(sel), kb_main(sel))
    else:
        s["menu_id"] = tg.send(chat_id, main_menu_text(sel), kb_main(sel))


def default_durations() -> List[float]:
    proj = load_project()
    segs = proj.get("segments") or []
    out: List[float] = []
    for i in range(len(FOLDER_DIRS)):
        d = 2.0
        if i < len(segs) and isinstance(segs[i], dict):
            try:
                d = round(float(segs[i].get("duration", 2.0)), 3)
            except Exception:
                d = 2.0
        out.append(max(0.001, d))
    return out


def start_upload(tg: Tg, chat_id: int, s: Dict[str, Any],
                 path: str, name: str) -> None:
    s["state"] = "upload"
    s["upload_folder"] = (path, name)
    s["uploaded"] = 0
    kb = [[{"text": "✅ Готово", "callback_data": "updone"}],
          [{"text": "❌ Отмена", "callback_data": "cancel"}]]
    tg.send(chat_id,
            f"📤 Кидай видео-хуки в чат — сохраню в папку <b>{name}</b>.\n"
            "Можно несколько подряд (видео или файлом-документом).\n"
            f"⚠️ Лимит Telegram для ботов — до ~20 МБ на файл.\n"
            "Когда закончишь — жми «✅ Готово».", kb)


def extract_incoming_video(msg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Из сообщения достаёт (file_id, имя файла) для видео/док-видео."""
    v = msg.get("video")
    if isinstance(v, dict) and v.get("file_id"):
        name = v.get("file_name") or f"hook_{int(time.time()*1000)}.mp4"
        return v["file_id"], name
    d = msg.get("document")
    if isinstance(d, dict) and d.get("file_id"):
        name = d.get("file_name") or ""
        mime = str(d.get("mime_type") or "")
        ext = os.path.splitext(name)[1].lower()
        if mime.startswith("video/") or ext in VIDEO_EXTS:
            if not name:
                name = f"hook_{int(time.time()*1000)}.mp4"
            return d["file_id"], name
    a = msg.get("animation")
    if isinstance(a, dict) and a.get("file_id"):
        name = a.get("file_name") or f"hook_{int(time.time()*1000)}.mp4"
        return a["file_id"], name
    return None


def save_incoming_video(tg: Tg, chat_id: int, s: Dict[str, Any],
                        msg: Dict[str, Any]) -> None:
    """Скачивает присланное видео в выбранную папку загрузки."""
    got = extract_incoming_video(msg)
    if not got:
        tg.send(chat_id, "❌ Это не видео. Кидай видео или видео-файлом, "
                         "или жми «✅ Готово».")
        return
    file_id, fname = got
    path, name = s["upload_folder"]
    os.makedirs(path, exist_ok=True)

    base = safe_folder_name(os.path.splitext(os.path.basename(fname))[0]) or "hook"
    ext = os.path.splitext(fname)[1].lower()
    if ext not in VIDEO_EXTS:
        ext = ".mp4"
    dest = os.path.join(path, base + ext)
    k = 1
    while os.path.exists(dest):
        dest = os.path.join(path, f"{base}_{k:03d}{ext}")
        k += 1

    fp = tg.get_file_path(file_id)
    if not fp:
        tg.send(chat_id, "❌ Не смог получить файл (возможно, больше 20 МБ — "
                         "лимит Telegram для ботов). Закинь его руками в "
                         f"<code>folder_1/{name}/</code>.")
        return
    if not tg.download_file(fp, dest):
        tg.send(chat_id, "❌ Ошибка скачивания, попробуй ещё раз.")
        return
    s["uploaded"] = s.get("uploaded", 0) + 1
    total = len(list_videos(path))
    tg.send(chat_id, f"✅ Сохранил как <code>{os.path.basename(dest)}</code> "
                     f"→ 📁 <b>{name}</b> (всего в папке: {total}). "
                     "Кидай ещё или жми «✅ Готово».")


def ask_uniq(tg: Tg, chat_id: int, s: Dict[str, Any]) -> None:
    s["state"] = "wait_uniq"
    if s.get("hooks_only"):
        title = "🧬 <b>Микро-уник хуков?</b>"
        yes = "🧬 Да, уникализировать хуки"
    elif s.get("parts_mode"):
        title = "🧬 <b>Микро-уник фрагментов?</b>"
        yes = "🧬 Да, уникализировать все фрагменты"
    else:
        title = "🧬 <b>Микро-уник папок 2-5?</b>"
        yes = "🧬 Да, уникализировать (сегменты 2-5)"
    tg.send(chat_id,
            f"{title}\n"
            "Каждое видео получает невидимый глазу рандом: сдвиг пикселей "
            "меньше 0.5%, микро-яркость/контраст/оттенок, лёгкое зерно. "
            "Хэш и цифровой отпечаток у каждого ролика будут разными — "
            "для Instagram/TikTok это уникальный контент:",
            kb_yes_no("uniq", yes, "📄 Нет, без уника"))


def ask_durations(tg: Tg, chat_id: int, s: Dict[str, Any]) -> None:
    s["state"] = "durations"
    dd = default_durations()
    kb = [[{"text": f"✅ По умолчанию ({fmt_dur(dd)})",
            "callback_data": "dur:def"}],
          [{"text": "❌ Отмена", "callback_data": "cancel"}]]
    tg.send(chat_id,
            "⏱ <b>Длительность каждого участка</b> (точность до миллисекунд).\n\n"
            f"Отправь {len(FOLDER_DIRS)} чисел через пробел, например:\n"
            "<code>3.250 1.500 1.500 1.500 2.000</code>\n\n"
            "Или одно число — оно применится ко всем участкам.",
            kb)


def show_summary(tg: Tg, chat_id: int, s: Dict[str, Any]) -> None:
    s["state"] = "ready"
    kb = [[{"text": "🚀 Создать", "callback_data": "go"}],
          [{"text": "❌ Отмена", "callback_data": "cancel"}]]
    tg.send(chat_id, summary_text(s), kb)


def handle_message(tg: Tg, msg: Dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    # ---- работаем ТОЛЬКО в личке: группы/каналы полностью игнорим ----
    if chat.get("type") != "private":
        return
    chat_id = chat["id"]
    text = (msg.get("text") or "").strip()
    s = get_session(chat_id)

    if text.startswith("/start") or text.startswith("/menu") or text == "/help":
        show_main_menu(tg, chat_id)
        return
    if text.startswith("/cancel"):
        show_main_menu(tg, chat_id)
        return

    state = s.get("state", "idle")

    # ---- 📤 режим загрузки: принимаем видео-сообщения ----
    if state == "upload" and not text:
        save_incoming_video(tg, chat_id, s, msg)
        return

    # не-текстовые сообщения (видео, фото, стикеры, сервисные) — игнорим,
    # чтобы бот не спамил меню в ответ на всё подряд
    if not text:
        return

    if state == "new_folder":
        name = safe_folder_name(text)
        if not name:
            tg.send(chat_id, "❌ Некорректное имя, попробуй ещё раз.")
            return
        path = os.path.join(FOLDER_DIRS[0], name)
        os.makedirs(path, exist_ok=True)
        tg.send(chat_id,
                f"✅ Папка <b>{name}</b> создана.\n"
                f"Закинь видео в <code>folder_1/{name}/</code>, или грузи "
                "прямо из Telegram — кнопка «📤 Загрузить хуки».")
        show_main_menu(tg, chat_id)
        return

    if state == "upload_newfolder":
        name = safe_folder_name(text)
        if not name:
            tg.send(chat_id, "❌ Некорректное имя, попробуй ещё раз.")
            return
        path = os.path.join(FOLDER_DIRS[0], name)
        os.makedirs(path, exist_ok=True)
        start_upload(tg, chat_id, s, path, name)
        return

    if state == "keep_hook_limit":
        try:
            lim = round(float(text.replace(",", ".")), 3)
        except Exception:
            tg.send(chat_id, "❌ Отправь число в секундах, например "
                             "<code>3.5</code>.")
            return
        if lim <= 0 or lim > 600:
            tg.send(chat_id, "❌ От 0.001 до 600 секунд.")
            return
        s["keep_hook_max"] = lim
        ask_uniq(tg, chat_id, s)
        return

    if state == "count":
        try:
            n = int(text)
        except Exception:
            tg.send(chat_id, "❌ Отправь просто число, например <code>10</code>.")
            return
        if n < 1 or n > MAX_COUNT:
            tg.send(chat_id, f"❌ От 1 до {MAX_COUNT}.")
            return
        s["count"] = n
        if s.get("hooks_only"):
            # только хуки: папки 2-5 не участвуют — сразу к выбору хука
            s["delete"] = False
            s["rand_body"] = False
            s["state"] = "wait_hook"
            tg.send(chat_id,
                    "🎣 <b>Как брать хуки из папки?</b>",
                    kb_yes_no("hook",
                              "🎲 Рандомные хуки",
                              "📑 По порядку"))
            return
        if s.get("parts_mode"):
            # фрагменты: хук и видео всегда рандом — только вопрос удаления
            s["rand_hook"] = True
            s["rand_body"] = True
            s["keep_hook"] = False
            s["state"] = "wait_delete"
            tg.send(chat_id,
                    "🗑 <b>Удалять использованные видео из папок 2-5?</b>",
                    kb_yes_no("del",
                              "🗑 Да, удалять отправленные",
                              "📌 Нет, оставлять"))
            return
        s["state"] = "wait_delete"
        tg.send(chat_id,
                "🗑 <b>Режим использования папок 2-5</b>",
                kb_yes_no("del",
                          "🗑 С удалением использованных из folder_2-5",
                          "📌 Без удаления (файлы остаются)"))
        return

    if state == "durations":
        durs = parse_durations(text, len(FOLDER_DIRS))
        if durs is None:
            tg.send(chat_id,
                    f"❌ Не понял. Нужно {len(FOLDER_DIRS)} чисел (или одно), "
                    "например: <code>3.250 1.5 1.5 1.5 2</code>")
            return
        s["durs"] = durs
        show_summary(tg, chat_id, s)
        return

    if state == "upload":
        tg.send(chat_id, "📤 Кидай видео, или жми «✅ Готово» / /cancel.")
        return

    # idle / прочее
    show_main_menu(tg, chat_id)


def handle_callback(tg: Tg, cb: Dict[str, Any]) -> None:
    chat = (cb.get("message") or {}).get("chat") or {}
    cb_id = cb["id"]
    # ---- только личка ----
    if chat.get("type") != "private":
        tg.answer_cb(cb_id)
        return
    chat_id = chat["id"]
    data = cb.get("data", "")
    s = get_session(chat_id)

    if chat_id in busy_chats and data == "go":
        tg.answer_cb(cb_id, "Уже собираю, подожди 🙏")
        return

    if data == "refresh":
        tg.answer_cb(cb_id, "Обновил")
        show_main_menu(tg, chat_id)
        return

    if data == "cancel":
        tg.answer_cb(cb_id, "Отменено")
        show_main_menu(tg, chat_id)
        return

    if data == "newfolder":
        tg.answer_cb(cb_id)
        s["state"] = "new_folder"
        tg.send(chat_id, "✏️ Напиши имя новой папки (например <code>1</code>):")
        return

    # ---------- 📤 загрузка хуков из Telegram ----------
    if data == "upload":
        tg.answer_cb(cb_id)
        s["state"] = "upload_pick"
        kb: List[List[Dict[str, str]]] = []
        for i, (_p, name, n) in enumerate(user_folders()):
            kb.append([{"text": f"📁 {name}  ({n} видео)",
                        "callback_data": f"up:{i}"}])
        kb.append([{"text": "➕ Новая папка", "callback_data": "upnew"}])
        kb.append([{"text": "❌ Отмена", "callback_data": "cancel"}])
        tg.send(chat_id,
                "📤 <b>Загрузка хуков</b>\nВ какую папку грузим? "
                "Выбери существующую или создай новую:", kb)
        return

    if data == "upnew":
        tg.answer_cb(cb_id)
        s["state"] = "upload_newfolder"
        tg.send(chat_id, "✏️ Напиши имя новой папки для хуков:")
        return

    if data.startswith("up:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "upload_pick":
            return
        try:
            idx = int(data.split(":")[1])
        except Exception:
            return
        folders = user_folders()
        if idx < 0 or idx >= len(folders):
            tg.send(chat_id, "❌ Папка не найдена, обнови меню.")
            show_main_menu(tg, chat_id)
            return
        path, name, _n = folders[idx]
        start_upload(tg, chat_id, s, path, name)
        return

    if data == "updone":
        tg.answer_cb(cb_id)
        if s.get("state") != "upload":
            return
        n = s.get("uploaded", 0)
        name = s["upload_folder"][1] if s.get("upload_folder") else "?"
        tg.send(chat_id, f"✅ Загрузка в <b>{name}</b> завершена: "
                         f"+{n} видео.")
        show_main_menu(tg, chat_id)
        return

    if data.startswith("f:"):
        try:
            idx = int(data.split(":")[1])
        except Exception:
            tg.answer_cb(cb_id)
            return
        folders = user_folders()
        if idx < 0 or idx >= len(folders):
            tg.answer_cb(cb_id, "Папка не найдена")
            show_main_menu(tg, chat_id)
            return
        path, name, n = folders[idx]
        if n == 0:
            tg.answer_cb(cb_id, f"В папке «{name}» нет видео!")
            return
        # ---- toggle выбора ----
        sel = s.setdefault("sel", set())
        if name in sel:
            sel.discard(name)
            tg.answer_cb(cb_id, f"➖ {name}")
        else:
            sel.add(name)
            tg.answer_cb(cb_id, f"✅ {name}")
        refresh_menu(tg, chat_id, s)
        return

    if data == "next":
        sel = s.get("sel") or set()
        if not sel:
            tg.answer_cb(cb_id, "Сначала выбери хотя бы одну папку")
            return
        tg.answer_cb(cb_id)
        folders = [(p, nm) for p, nm, n in user_folders()
                   if nm in sel and n > 0]
        if not folders:
            tg.answer_cb(cb_id, "В выбранных папках нет видео")
            return
        menu_id = s.get("menu_id")
        reset_session(chat_id)
        s = get_session(chat_id)
        s["folders"] = folders
        s["menu_id"] = menu_id
        s["state"] = "wait_mode"
        names = ", ".join(nm for _p, nm in folders)
        kb = [[{"text": "🎬 Полное видео (хук + папки 2-5)",
                "callback_data": "mode:full"}],
              [{"text": "🎣 Только хуки (без надписей и склейки)",
                "callback_data": "mode:hooks"}],
              [{"text": "🧩 Фрагменты по отдельности (хуки + папки 2-5)",
                "callback_data": "mode:parts"}],
              [{"text": "❌ Отмена", "callback_data": "cancel"}]]
        tg.send(chat_id,
                f"📁 Выбрано папок: <b>{len(folders)}</b> ({names}).\n\n"
                "⚙️ <b>Что делаем?</b>\n"
                "• Полное видео — как обычно: хук + сегменты из папок 2-5 "
                "с надписями.\n"
                "• Только хуки — чистые видео из выбранных папок: без "
                "надписей, без папок 2-5.\n"
                "• Фрагменты — всё по отдельности файлами: N хуков без "
                "надписей + N видео из каждой папки 2-5 (хук и видео "
                "всегда рандом).", kb)
        return

    if data.startswith("mode:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_mode":
            return
        s["hooks_only"] = data.endswith(":hooks")
        s["parts_mode"] = data.endswith(":parts")
        s["state"] = "count"
        if s["parts_mode"]:
            tg.send(chat_id,
                    "🔢 <b>Сколько файлов каждого типа?</b>\n"
                    "Например 3 = 3 хука + по 3 видео из папок 2, 3, 4, 5.\n"
                    f"Отправь число (1-{MAX_COUNT}):")
            return
        what = "хуков" if s["hooks_only"] else "видео"
        tg.send(chat_id,
                f"🔢 Сколько {what} создать <b>для каждой папки</b>? "
                f"Отправь число (1-{MAX_COUNT}):")
        return

    if data.startswith("del:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_delete":
            return
        s["delete"] = data.endswith(":1")
        if s.get("parts_mode"):
            # фрагменты: рандом всегда — сразу к унику
            ask_uniq(tg, chat_id, s)
            return
        s["state"] = "wait_body"
        tg.send(chat_id,
                "🎲 <b>Как брать видео из папок 2-5?</b>",
                kb_yes_no("body",
                          "🎲 Рандомные видео из папок",
                          "📑 По порядку"))
        return

    if data.startswith("body:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_body":
            return
        s["rand_body"] = data.endswith(":1")
        s["state"] = "wait_hook"
        names = ", ".join(nm for _p, nm in (s.get("folders") or []))
        tg.send(chat_id,
                f"🎣 <b>Рандом хук?</b>\nКаждое видео берёт хук только из "
                f"СВОЕЙ папки ({names}), не из всех подпапок folder_1:",
                kb_yes_no("hook",
                          "🎲 Рандомный хук из своей папки",
                          "📑 Хуки по порядку"))
        return

    if data.startswith("hook:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_hook":
            return
        s["rand_hook"] = data.endswith(":1")
        if s.get("hooks_only"):
            # только хуки: они всегда идут как есть — сразу к унику
            s["keep_hook"] = False
            ask_uniq(tg, chat_id, s)
            return
        s["state"] = "wait_keep"
        tg.send(chat_id,
                "🎬 <b>Хук без изменений?</b>\n"
                "Если видео-хук не длиннее лимита — оставить его как есть: "
                "не ускорять, не замедлять, не резать (длительность 1-го "
                "участка тогда = длине хука):",
                kb_yes_no("keep",
                          "🎬 Да, оставить как есть (если ≤ лимита)",
                          "⏱ Нет, подгонять под длительность"))
        return

    if data.startswith("keep:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_keep":
            return
        if data.endswith(":1"):
            s["keep_hook"] = True
            s["state"] = "keep_hook_limit"
            kb = [[{"text": "✅ Лимит 3.5 сек", "callback_data": "keeplim:3.5"}],
                  [{"text": "❌ Отмена", "callback_data": "cancel"}]]
            tg.send(chat_id,
                    "⏱ <b>Максимальная длина хука</b> для режима «как есть».\n"
                    "Отправь число в секундах (например <code>3.5</code> или "
                    "<code>4.250</code>) — если хук длиннее, он будет "
                    "подгоняться как обычно:",
                    kb)
        else:
            s["keep_hook"] = False
            ask_uniq(tg, chat_id, s)
        return

    if data.startswith("keeplim:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "keep_hook_limit":
            return
        try:
            s["keep_hook_max"] = float(data.split(":")[1])
        except Exception:
            s["keep_hook_max"] = 3.5
        ask_uniq(tg, chat_id, s)
        return

    if data.startswith("uniq:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_uniq":
            return
        s["micro_uniq"] = data.endswith(":1")
        if s.get("hooks_only") or s.get("parts_mode"):
            # хуки/фрагменты идут целиком — длительности не нужны
            s["durs"] = default_durations()
            show_summary(tg, chat_id, s)
            return
        ask_durations(tg, chat_id, s)
        return

    if data == "dur:def":
        tg.answer_cb(cb_id)
        if s.get("state") != "durations":
            return
        s["durs"] = default_durations()
        show_summary(tg, chat_id, s)
        return

    if data == "go":
        if s.get("state") != "ready":
            tg.answer_cb(cb_id)
            return
        tg.answer_cb(cb_id, "Поехали 🚀")
        busy_chats.add(chat_id)
        order = dict(s)
        threading.Thread(target=run_order, args=(tg, chat_id, order),
                         daemon=True).start()
        reset_session(chat_id)
        return

    tg.answer_cb(cb_id)


# ======================================================================
#  ENTRY POINT
# ======================================================================

def get_token() -> str:
    tok = os.environ.get("BOT_TOKEN", "").strip()
    if tok:
        return tok
    chars = load_characters()
    return str(chars.get("bot_token", "") or "")


def main() -> None:
    ensure_dirs()
    if not os.path.isfile(FONT_ANTON) or not os.path.isfile(FONT_OSWALD):
        try:
            download_fonts()
        except Exception:
            pass

    token = get_token()
    if not token:
        print("❌ Нет токена бота. Укажи bot_token в characters.json "
              "или переменную окружения BOT_TOKEN.")
        sys.exit(1)

    tg = Tg(token)
    me = tg.call("getMe")
    if not me.get("ok"):
        print("❌ Токен не работает:", me.get("description"))
        sys.exit(1)
    print(f"✅ Бот @{me['result'].get('username')} запущен. Ctrl+C — стоп.")

    # ---- пропускаем накопившийся бэклог апдейтов, чтобы бот не
    #      спамил ответами на старые сообщения после перезапуска ----
    offset = 0
    try:
        j = tg.call("getUpdates", **{"offset": -1, "timeout": 0})
        if j.get("ok") and j.get("result"):
            offset = j["result"][-1]["update_id"] + 1
            print(f"⏭ Пропущено старых апдейтов до #{offset - 1}")
    except Exception:
        pass
    while True:
        try:
            updates = tg.get_updates(offset)
            for u in updates:
                offset = max(offset, u["update_id"] + 1)
                try:
                    if "message" in u:
                        handle_message(tg, u["message"])
                    elif "callback_query" in u:
                        handle_callback(tg, u["callback_query"])
                except Exception:
                    traceback.print_exc()
        except KeyboardInterrupt:
            print("\n👋 Стоп.")
            break
        except Exception:
            traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()
