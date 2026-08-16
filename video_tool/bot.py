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
    DEFAULT_PRESET,
    ensure_dirs, natural_key, list_videos,
    load_project, load_characters, save_characters,
    get_ffmpeg_exe, is_valid_ffmpeg,
    build_one_final_ffmpeg, download_fonts,
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
                files = {"document": (os.path.basename(file_path), f,
                                      "video/mp4")}
                data = {"chat_id": chat_id, "caption": caption,
                        "disable_notification": True}
                r = requests.post(url, data=data, files=files, timeout=600)
            if r.ok and r.json().get("ok"):
                return True, "ok"
            return False, r.text[:200]
        except Exception as e:
            return False, str(e)

    def send_action(self, chat_id: int, action: str = "upload_document") -> None:
        self.call("sendChatAction", chat_id=chat_id, action=action)


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
#   "state": idle | new_folder | count | durations | ready
#   "folder": (path, name), "count": int,
#   "delete": bool|None, "rand_body": bool|None, "rand_hook": bool|None,
#   "durs": [float]*n, "msg_id": int|None
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

def kb_main() -> List[List[Dict[str, str]]]:
    kb: List[List[Dict[str, str]]] = []
    for i, (_p, name, n) in enumerate(user_folders()):
        kb.append([{"text": f"📁 {name}  ({n} видео)", "callback_data": f"f:{i}"}])
    kb.append([{"text": "➕ Создать папку", "callback_data": "newfolder"},
               {"text": "🔄 Обновить", "callback_data": "refresh"}])
    return kb


def main_menu_text() -> str:
    folders = user_folders()
    pools = body_pools()
    lines = ["<b>🎬 Video Stitcher Bot</b>", ""]
    if folders:
        lines.append("Выбери папку, из которой собрать видео (хуки):")
    else:
        lines.append("Папок пока нет — нажми «➕ Создать папку»,")
        lines.append(f"потом закинь видео в <code>folder_1/&lt;имя&gt;/</code>.")
    lines.append("")
    body = " · ".join(f"{FOLDERS[i+1]}: {len(p)}" for i, p in enumerate(pools))
    lines.append(f"Тело ролика: {body}")
    return "\n".join(lines)


def kb_yes_no(prefix: str, yes: str, no: str) -> List[List[Dict[str, str]]]:
    return [[{"text": yes, "callback_data": f"{prefix}:1"}],
            [{"text": no, "callback_data": f"{prefix}:0"}],
            [{"text": "❌ Отмена", "callback_data": "cancel"}]]


def summary_text(s: Dict[str, Any]) -> str:
    _p, name = s["folder"]
    return "\n".join([
        "<b>📋 Заказ</b>",
        f"📁 Папка (хук): <b>{name}</b>",
        f"🔢 Кол-во видео: <b>{s['count']}</b>",
        f"🗑 Удаление из folder_2-5: <b>{'да' if s['delete'] else 'нет'}</b>",
        f"🎲 Рандом видео из папок 2-5: <b>{'да' if s['rand_body'] else 'по порядку'}</b>",
        f"🎣 Рандом хук из «{name}»: <b>{'да' if s['rand_hook'] else 'по порядку'}</b>",
        f"⏱ Длительности: <b>{fmt_dur(s['durs'])}</b>",
    ])


# ======================================================================
#  СБОРКА И ОТПРАВКА
# ======================================================================

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
    """Фоновый поток: собрать N видео и слать их в чат по мере готовности."""
    folder_path, folder_name = s["folder"]
    count: int = s["count"]
    delete: bool = s["delete"]
    rand_body: bool = s["rand_body"]
    rand_hook: bool = s["rand_hook"]
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

        hooks = list_videos(folder_path)
        if not hooks:
            status(f"❌ В папке «{folder_name}» нет видео.\n"
                   f"Закинь файлы в <code>folder_1/{folder_name}/</code>")
            return
        pools = body_pools()
        empty = [FOLDERS[i + 1] for i, p in enumerate(pools) if not p]
        if empty:
            status("❌ Пустые папки тела ролика: " + ", ".join(empty))
            return

        # при удалении максимум = самый маленький пул folder_2-5
        limit = min(len(p) for p in pools)
        real_count = count
        note = ""
        if delete and count > limit:
            real_count = limit
            note = (f"\n⚠️ С удалением хватает видео только на {limit} шт. "
                    f"(меньше всего в самой маленькой папке)")

        res_w = int((exp.get("resolution") or {}).get("w", 1080) or 1080)
        res_h = int((exp.get("resolution") or {}).get("h", 1920) or 1920)
        fps = int(exp.get("fps", 30) or 30)
        crf = int(exp.get("crf", 20) or 20)
        preset = str(exp.get("preset", "fast") or "fast")
        with_audio = bool(exp.get("audio", True))
        uppercase = bool(exp.get("uppercase", False))
        ten_bit = bool(exp.get("ten_bit", False))
        blur_fill = bool(exp.get("blur_fill", False))

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        made, sent, errors = 0, 0, []
        t0 = time.time()

        with build_lock:
            for i in range(real_count):
                status(f"🎬 Папка «{folder_name}»: собираю {i + 1}/{real_count}…"
                       f"{note}")

                # ---- хук: рандом или по порядку из ВЫБРАННОЙ подпапки ----
                if not hooks:
                    errors.append("хуки закончились")
                    break
                hook = (random.choice(hooks) if rand_hook
                        else hooks[i % len(hooks)])

                # ---- тело: folder_2-5 ----
                vps = [hook]
                used_body: List[str] = []
                ok_pick = True
                for pool in pools:
                    if not pool:
                        ok_pick = False
                        break
                    v = random.choice(pool) if rand_body else pool[i % len(pool)]
                    vps.append(v)
                    used_body.append(v)
                if not ok_pick:
                    errors.append("в одной из папок 2-5 кончились видео")
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
                    vps, presets, durs, out_path, ff,
                    resolution=(res_w, res_h) if res_w and res_h else (1080, 1920),
                    fps=fps, crf=crf, preset=preset,
                    with_audio=with_audio, uppercase=uppercase,
                    ten_bit=ten_bit, blur_fill=blur_fill,
                    audio_kbps=192,
                    random_flags=None, preset_indices=None)
                if not ok:
                    errors.append(f"#{i + 1}: {err[:120]}")
                    continue
                made += 1

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

                # ---- отправка сразу в чат ----
                tg.send_action(chat_id)
                cap = (f"📁 {folder_name} • {made}/{real_count}\n"
                       f"⏱ {fmt_dur(durs)}")
                oks, info = tg.send_document(chat_id, out_path, cap)
                if oks:
                    sent += 1
                else:
                    errors.append(f"отправка #{i + 1}: {info}")

        dt = time.time() - t0
        lines = [f"✅ Готово: собрано <b>{made}</b>, отправлено <b>{sent}</b> "
                 f"из папки <b>{folder_name}</b> за {dt:.0f} сек."]
        if note:
            lines.append(note.strip())
        if errors:
            lines.append("⚠️ Ошибки:\n" + "\n".join("• " + e for e in errors[:5]))
        status("\n".join(lines))
    except Exception as e:
        traceback.print_exc()
        status(f"❌ Ошибка: {e}")
    finally:
        busy_chats.discard(chat_id)
        reset_session(chat_id)
        tg.send(chat_id, main_menu_text(), kb_main())


# ======================================================================
#  ОБРАБОТКА АПДЕЙТОВ
# ======================================================================

def show_main_menu(tg: Tg, chat_id: int) -> None:
    reset_session(chat_id)
    tg.send(chat_id, main_menu_text(), kb_main())


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
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    s = get_session(chat_id)

    if text.startswith("/start") or text.startswith("/menu") or text == "/help":
        show_main_menu(tg, chat_id)
        return
    if text.startswith("/cancel"):
        show_main_menu(tg, chat_id)
        return

    state = s.get("state", "idle")

    if state == "new_folder":
        name = safe_folder_name(text)
        if not name:
            tg.send(chat_id, "❌ Некорректное имя, попробуй ещё раз.")
            return
        path = os.path.join(FOLDER_DIRS[0], name)
        os.makedirs(path, exist_ok=True)
        tg.send(chat_id,
                f"✅ Папка <b>{name}</b> создана.\n"
                f"Закинь видео в <code>folder_1/{name}/</code> и жми «🔄 Обновить».")
        show_main_menu(tg, chat_id)
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

    # idle / прочее
    show_main_menu(tg, chat_id)


def handle_callback(tg: Tg, cb: Dict[str, Any]) -> None:
    chat_id = cb["message"]["chat"]["id"]
    data = cb.get("data", "")
    cb_id = cb["id"]
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

    if data.startswith("f:"):
        tg.answer_cb(cb_id)
        try:
            idx = int(data.split(":")[1])
        except Exception:
            return
        folders = user_folders()
        if idx < 0 or idx >= len(folders):
            tg.send(chat_id, "❌ Папка не найдена, обнови меню.")
            show_main_menu(tg, chat_id)
            return
        path, name, n = folders[idx]
        if n == 0:
            tg.send(chat_id,
                    f"⚠️ В папке <b>{name}</b> нет видео.\n"
                    f"Закинь файлы в <code>folder_1/{name}/</code> и обнови меню.")
            return
        reset_session(chat_id)
        s = get_session(chat_id)
        s["folder"] = (path, name)
        s["state"] = "count"
        tg.send(chat_id,
                f"📁 Папка <b>{name}</b> ({n} хуков).\n\n"
                f"🔢 Сколько видео создать? Отправь число (1-{MAX_COUNT}):")
        return

    if data.startswith("del:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_delete":
            return
        s["delete"] = data.endswith(":1")
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
        name = s["folder"][1] if s.get("folder") else "?"
        tg.send(chat_id,
                f"🎣 <b>Рандом хук?</b>\nВидео из выбранной папки «{name}» "
                "(не из всех подпапок folder_1 — только из этой):",
                kb_yes_no("hook",
                          f"🎲 Рандомный хук из «{name}»",
                          "📑 Хуки по порядку"))
        return

    if data.startswith("hook:"):
        tg.answer_cb(cb_id)
        if s.get("state") != "wait_hook":
            return
        s["rand_hook"] = data.endswith(":1")
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

    offset = 0
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
