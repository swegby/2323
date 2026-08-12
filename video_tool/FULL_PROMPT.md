# Video Stitcher Pro — Полный финальный промпт v2.6 Beautiful GUI Edition

> Этот файл — официальный технический промпт/спецификация проекта
> **Video Stitcher Pro — Batch Colored Preset Edition v2.6 Beautiful GUI**.
> Реализация — `main.py` (весь код в одном файле).

---

## 1. Название и цель

**Video Stitcher Pro — Batch Colored Preset Edition v2.6 Beautiful GUI** — десктопное
приложение для сборки вертикальных Reels/TikTok/Shorts из **5 частей** с мем-текстом.
Работает на Windows/Linux/macOS через **Python 3.10+ + PyQt6 + MoviePy 2.1.2 +
Pillow + imageio-ffmpeg + requests + proglog**.

Цель: пользователь закидывает 1000+ видео в 5 папок, причем `folder_1` содержит
подпапки-персонажи (alex, masha...), для каждого персонажа задает Telegram-группу,
настраивает для каждого текста свою позицию/размер/обводку/цвет/рандомные числа,
и в один клик собирает батч из сотен видео через быстрый ffmpeg, с авто-отправкой
готовых файлов в Telegram как документ в макс. качестве.

## 2. Стек

- Python 3.10+
- PyQt6 — GUI, QSplitter, QTabWidget, QGraphicsDropShadowEffect
- MoviePy 2.1.2 — только для fallback и для получения длительности, основной рендер через чистый ffmpeg
- Pillow — рендер цветного текста в RGBA PNG с обводкой
- imageio-ffmpeg — bundled ffmpeg + ffprobe
- requests — Telegram Bot API + скачивание шрифтов/ffmpeg

## 3. Структура проекта

```
video_tool/
  main.py — весь код
  FULL_PROMPT.md
  README.md
  requirements.txt: PyQt6, moviepy==2.1.2, Pillow, imageio-ffmpeg, numpy, requests, proglog
  project.json — автосейв всех настроек (сегменты, пресеты, экспорт, батч, ui, ffmpeg_path)
  ffmpeg_path.json — сохраненный путь к ffmpeg
  characters.json — {bot_token, auto_send, characters: {name: {chat_id, display_name}}, selected_characters: []}
  fonts/
    Anton-Regular.ttf — для латиницы, качается с Google Fonts если нет
    Oswald-Bold.ttf   — для кириллицы fallback
  folder_1/
    character folders: alex/, masha/, toowers/ ... каждая содержит видео для этого персонажа
    или default видео прямо в folder_1 если подпапок нет
  folder_2/ ... folder_5/ — общие видео
  output/
    final_video.mp4 / final_video_0001.mp4 ...
    {character}_{0001}.mp4 для батча с персонажами
    used/
      folder_2/ folder_3/ ... — перемещенные использованные если включено
    _temp_{uuid}/ — временные сегменты и текстовые PNG
```

## 4. Управление персонажами (folder_1)

- `list_character_folders()` — сканирует folder_1, возвращает список подпапок с видео +
  если в корне folder_1 есть видео — добавляет base как персонаж `default`.
  Игнорирует `.`, `used`, `__pycache__`, `bin`, `fonts`.
- `get_character_name(path)` — имя папки, для base = "default"
- `list_videos(folder)` — файлы с VIDEO_EXTS, натуральная сортировка по числу в имени
- `characters.json`:

```json
{
  "bot_token": "123456:ABC...",
  "auto_send": true,
  "characters": {
    "alex": {"chat_id": "-100123...", "display_name": "alex"},
    "masha": {"chat_id": "-100456..."}
  },
  "selected_characters": ["alex","masha"]
}
```

- UI блок 👥 Персонажи:
  - Bot Token row: QLineEdit password + 👁 / 💾 / ✅ + чекбокс 📤 Авто-отправка
  - Selectable buttons: FlowLayout с кнопками `QPushButton#CharSelect` checkable,
    стиль: невыбран #1c1c36 border #2a2a52, выбран #2e2a4a border #e94560 color
    #ffcc66 + обводка. Клик = toggle выбора, сохраняет в selected_characters,
    обновляет батч-инфо. Кнопки ✅ Всех / ❌ Снять.
  - Детальный список: для каждой папки-персонажа row
    QFrame #1e1e3a border #2a2a52 radius 10:
    `👤 name | N видео | [Chat ID input] | 💾 | 📁 Открыть папку | 🗑️ Удалить из конфига`
  - Создать персонажа: input + ➕ Создать — создает подпапку `folder_1/name` +
    добавляет в конфиг
  - Hint: каждый персонаж = подпапка, видео туда, укажи Chat ID -100... куда слать файлом
- При батче: `get_filtered_character_folders()` — если selected не пустой,
  возвращает только выбранных, иначе всех.

## 5. FFMPEG умный поиск с запоминанием

- Файл ffmpeg_path.json + поле в project.json
- `load_saved_ffmpeg_path()`, `save_ffmpeg_path(path)`,
  `is_valid_ffmpeg(path)` — проверяет exists + `ffmpeg -version` returncode 0
- `find_system_ffmpeg()`:
  1. saved path
  2. `shutil.which("ffmpeg")` / `ffmpeg.exe`
  3. common: Windows `C:\ffmpeg\bin\ffmpeg.exe`, `Program Files\ffmpeg\bin`,
     `~\ffmpeg\bin`, `%ProgramData%\chocolatey\bin`, `bin/ffmpeg.exe`;
     Linux `/usr/bin/ffmpeg`, `/usr/local/bin/ffmpeg`, `/opt/homebrew/bin/ffmpeg`,
     `/snap/bin/ffmpeg`, `bin/ffmpeg`
  4. bundled `imageio_ffmpeg.get_ffmpeg_exe()`
- `download_ffmpeg_automatically()` — через `imageio_ffmpeg.get_ffmpeg_exe()`
  (скачивает если нет), fallback для Windows скачивает zip с `BtbN/FFmpeg-Builds`
  latest, распаковывает в `bin/ffmpeg.exe`
- `get_ffmpeg_exe()` — умный: saved → system → bundled → download → "ffmpeg"
- UI блок ⚙️ FFMPEG: лейбл `✅ path` зеленый #3dd598 или `❌ Не найден` красный,
  кнопки 🔍 Найти в системе, ⬇️ Скачать, 📁 Выбрать вручную (QFileDialog),
  ✅ Проверить (показывает `ffmpeg -version`)
- Фоновый тред при старте ищет/скачивает если не найден

## 6. Текст — пресеты на каждый текст (главная фича)

- Каждый текст = отдельный пресет:

```python
{
  'text': '2. Go to [blue]TOOWERS[/blue] {rand:1.1-2.5}',
  'relative_rect': {'x':0.10,'y':0.12,'w':0.80,'h':0.22},
  'font_scale': 1.1,
  'stroke_width': 0
}
```

- `SegmentCard.presets` — list[dict], `current_idx`
- При добавлении нового текста — копирует текущую позицию/размер/обводку как стартовую
- При переключении пресета (◀ ▶ или клик по тегу) — `relative_rect`, `font_scale`,
  `stroke_width` загружаются в UI: слайдеры без сигналов,
  `preview_widget.set_relative_rect()`, `set_font_scale()`, `set_custom_stroke()`,
  `set_preview_text(randomize_text(preset['text']))`
- При драге рамки — обновляется только `presets[current_idx]['relative_rect']`
- При изменении слайдера размера/обводки — обновляется поле в текущем пресете
- Кнопка ⎘ Копир поз — копирует позицию/размер/обводку текущего на все остальные
- Теги — `TagWidget` с иконками 🎨 если есть цвет, 🎲 если есть рандом,
  тултип с позицией
- `get_config()` возвращает `presets` + для совместимости `texts`;
  `set_config()` мигрирует старый формат `texts: ["a","b"]` → пресеты с общей позицией

## 7. Окрас и рандом в тексте

- Окрас: BBCode `[blue]TOOWERS[/blue]`, `[red]`, `[yellow]`, `[green]`, `[cyan]`,
  `[pink]`, `[orange]`, `[purple]`, `[white]`, `[black]`, или hex
  `[#00D5FF]...[/]`, также `{blue}` / `<blue>` нормализуются в `[]`
- Карта `COLOR_MAP` яркие цвета
- `parse_color_text(raw, default='white')` → list[(text, color)]
  regex `\[(?:color=)?(#[0-9A-Fa-f]{3,8}|[a-zA-Z0-9]+)\](.*?)\[/[^\]]*\]`
- Рандом: `{rand:min-max[:step]}` / `{r:...}` / `{random:...}`
  - `{rand:1.1-2.5}` → 1.1,1.2,...2.5 с шагом 0.1
  - `{rand:1.1-2.5:0.1}` явно шаг 0.1
  - `{rand:25-100}` → целое 25-100
  - Фича: `.0` убирается: 2.0 → 2, 1.0 → 1k (для `$2.1k`)
  - Regex `RAND_PATTERN = \{\s*(?:rand|random|r)\s*:\s*([0-9]+\.?[0-9]*)\s*-\s*([0-9]+\.?[0-9]*)(?:\s*:\s*([0-9]+\.?[0-9]*))?\s*\}`
  - `randomize_text(text)` заменяет все такие теги каждый раз случайным числом
- UI: быстрые кнопки `$1k {rand:1.1-2.5}`, `25-100`, `1.1-2.5:0.1` + диалог
  ⚙️ Диапазон с полями мин/макс/шаг
- В превью рандомится при каждой загрузке пресета, в финале — при каждой сборке
  пресета (новый рандом каждый раз)

## 8. Мультилайн и лимит

- Лимит убран, до 100+ вариаций
- `QTextEdit` 68-72px, Enter перенос, Ctrl+Enter добавить
- `\n` сохраняется и в превью и в финале, word-wrap по max_width

## 9. Превью-плеер и прямоугольник — оптимизация

- `VideoPreviewWidget` 300×534, 9:16, focusable для стрелок
- Фон — первый кадр видео (или скраб-кадр) через `VideoFileClip.get_frame(t)` →
  QImage → QPixmap, **сразу ресайз до 300×534 FastTransformation** для экономии RAM x10
- Safe zones — пунктир white 18% alpha, только граница
- Прямоугольник — только белая рамка 2.0px, без заливки. 8 ручек розовых #ff6b81 10px
- Drag/Resize: `rectChanging` эмитится на mouseMove (живое превью без сохранения),
  `rectChanged` только на mouseRelease (сохранение)
- Клавиатура: стрелки двигают на 1px, Shift+стрелка на 5px
- Точные спинбоксы X Y W H 0.0-1.0 шаг 0.01 decimals 3
- `TagFlowWidget` — flow layout для чипов

**Оптимизация лагов (критично для 1000+ видео):**
- Автосохранение дебаунс: `QTimer singleShot 700ms` — `_on_config_changed`
  стартует таймер, а не сразу пишет project.json
- Превью бэкграунд грузится в `threading.Thread daemon`
- Скраб: слайдер 0-1000, `valueChanged` → `_pending_scrub_t = t` +
  `QTimer 180ms singleShot`, только один поток `_loading` флаг,
  если уже грузится — перезапускает таймер на 120мс
- Чекбокс `Скраб` вкл/выкл, глобальный `Эконом (без превью)` — вообще не грузит
  видео в превью, только #151528 фон
- Шрифт в превью: адаптивный `min(w*0.11, h*0.50)*font_scale`, длина берется по
  самой длинной строке, мягкая коррекция `max(0.70, 1-(max_len-22)*0.015)`,
  мин 12 макс 40pt, line_h = fm.height()+6, центрирование по вертикали

## 10. Управление длительностью

- `QDoubleSpinBox` 0.5-60 сек шаг 0.5
- Сегмент 0 (первый): если исходное длинее target — ускоряется
  `factor = orig/target` через `setpts=PTS/factor` в ffmpeg; если короче — loop
  `stream_loop ceil(target/orig)-1` + `trim=duration=target`
- Сегменты 1-4: если длинее — обрезка с конца `start = orig-target` через `-ss`
  перед `-i`; если короче — целиком (effective = orig)

## 11. Экспорт — быстрый ffmpeg + качество для MOV

- UI: комбо Разрешение `1080×1920 Reels / 720×1280 / Оригинал`, FPS 30/24/60,
  **Качество** `💎 Макс CRF16 для MOV / ⭐ Высокое CRF18 - рекоменд /
  ⚖️ Баланс CRF20 / 🚀 Скорость CRF23`, чекбоксы Со звуком, CAPS, Эконом
- Маппинг качества: Max CRF16 preset medium (для MOV ProRes почти без потерь),
  High CRF18 medium (рекоменд), Balance CRF20 fast, Speed CRF23 veryfast
- `get_export_config()` возвращает resolution, force_reels, fps, audio, uppercase,
  crf, preset, quality_text
- Быстрый путь `build_one_final_ffmpeg()`:
  - Для 5 сегментов параллельно `ThreadPoolExecutor(max_workers=5)`:
    - Выбирает случайный пресет (с его rect, font_scale, stroke)
    - `randomize_text()` для {rand:}
    - Uppercase если включен (только контент, теги цвета сохраняются)
    - Считает font_size: `base = min(rect_w*0.11, rect_h*0.55) * font_scale`,
      коррекция по max_len строки `max(0.75, 1-(len-22)*0.01)`, кламп 30-130pt,
      для 720p *0.75, shift ±3%
    - `create_colored_text_image()` → RGBA PNG `text_{i}_{uuid}.png`
    - `build_segment_ffmpeg(...)` — ffmpeg команда с
      `scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,
      crop=1080:1920:exact=1,fps=30,setpts` для скорости,
      `overlay=x:y:shortest=1,format=yuv420p:colorspace=bt709`,
      `-preset {preset} -crf {crf} -pix_fmt yuv420p -movflags +faststart`
  - Склейка 5 сегментов через `concat demuxer -c copy` — мгновенно, без
    перекодирования. Fallback через filter concat если copy fails
  - Cleanup temp
- Для одиночного видео `BuildWorker.run()` вызывает `build_one_final_ffmpeg`
  с прогрессом 0-88%, fallback на старый MoviePy `run_moviepy()` если ffmpeg упадет
- Для батча `BatchBuildWorker` — учитывает персонажей и многопоток

## 12. Батч режим 1000+ видео

- UI карточка `SidebarCard`:
  - Чекбокс `Включить батч`
  - Комбо Режим: `Последовательно — до min` (1-й со всех, 2-й со всех...)
    до min(len), `Рандом 2-5 (f1 по порядку, 2-5 рандом)`
  - Чекбоксы `🗑️ Удалять 2-5`, `📦 В used` (безопасно перемещает в output/used/folder_N)
  - Спин Сколько: 0=Auto (min для последовательно, len(folder_1) для рандома), до 10000
  - Спин Потоков: 1-8, дефолт 3 — сколько видео рендерить параллельно
  - Инфо лейбл `CountInfo`: `В папках: 1:45 (выбрано 2/5: alex, masha) | 2:100 |
    ... → будет 20 видео [Последовательно]`
  - Превью следующего батча
- `BatchBuildWorker`:
  - `get_filtered_character_folders()` — учитывает выбор персонажей
  - Считает total: сумма по каждому выбранному персонажу
    `min(len(char_videos), len(other))` для последовательно или
    `len(char_videos)` для рандом
  - Если threads==1: последовательно цикл по всем задачам
    `tasks = [(char_folder, vid_idx, global_idx) ...]`, для каждой выбирает
    video_paths (для последовательно берет same idx из folder_2..5, для рандом —
    random.choice с Lock если delete_used), вызывает `build_one_final_ffmpeg`,
    `file_done` сигнал, удаление/перемещение если включено
  - Если threads>1: `ThreadPoolExecutor(max_workers=threads)` параллельно, с
    `Lock` для remaining pools, `as_completed` для прогресса
    `Батч {completed}/{total} готов (потоков N)`
  - После каждого видео — если `auto_send` и `bot_token` и `chat_id` для
    персонажа есть — `threading.Thread(target=send_video_via_telegram, ...)`
    — отправка файлом в макс качестве
  - Прогресс 0-90% подготовка + рендер, 100% готово, `finished_ok` с папкой output

## 13. Красивый GUI редизайн v2.6

- Фон #0a0a14, карточки #151528 / #131326, бордер #252545, radius 16-20px,
  hover #35356a
- HeaderCard градиент #151528→#1e1e3a
- QSplitter горизонтальный: слева 380-440px сайдбар со скроллом, справа — сегменты
- Левая панель: Экспорт карточка, Персонажи карточка, Батч карточка, FFMPEG карточка
- Правая панель: Селектор сегментов — 5 кнопок `SegmentTab` checkable 84×48px,
  выбранная градиентом #e94560→#ff6b81; `QTabWidget` с 5 табами, каждый содержит
  `SegmentCard` (340-420px шириной, shadow 24px blur); внутри карточки секции в
  `QFrame background #1a1a32 border #252545 radius 12px`; Preview 300×534,
  X Y W H спинбоксы; Scrub в отдельном фрейме
- Bottom: HeaderCard с статусом, прогрессбаром 320px, кнопками Build (градиент)
  и BuildBatch (оранжевый градиент #ff9500→#ffcc66)
- QSS полностью переписан: скроллбары 8px #2a2a52, чекбоксы 18px radius 6px,
  слайдеры handle 16px с бордером #0a0a14, табы, кнопки ColorBtn,
  CharSelect checked #2e2a4a border #e94560 color #ffcc66

## 14. Автосохранение и обработка ошибок

- `project.json` — сегменты (presets), экспорт (resolution, fps, crf, preset,
  audio, uppercase), батч (mode, delete, move, count, threads), ui (индексы
  комбо, чекбоксы)
- `QTimer` autosave 5с + debounce 700ms на configChanged + 500ms на batch config
- При загрузке миграция старого формата `texts:[]` → `presets`
- Ошибки: try/except вокруг VideoFileClip.get_frame, Text image creation fallback
  без тегов, ffmpeg fallback на re-encode concat, сообщения QMessageBox

## 15. Алгоритм сборки одного видео (ffmpeg fast)

1. Найти ffmpeg через `get_ffmpeg_exe()` (saved → PATH → common → bundled → download)
2. Собрать `video_paths` — 5 файлов (для folder_1 — из выбранного персонажа)
3. `build_one_final_ffmpeg`:
   a. Для каждого из 5 сегментов параллельно:
      i. Выбрать случайный пресет из `presets` сегмента
      ii. `randomize_text()` для {rand:}
      iii. Uppercase если включен (только контент)
      iv. Рассчитать font_size: `min(rect_w*0.11, rect_h*0.55)*font_scale`,
          коррекция по max_line_len `max(0.75,1-(len-22)*0.01)`,
          кламп 30-130, shift ±3%
      v. `create_colored_text_image()` → PNG
      vi. `build_segment_ffmpeg()` → ffmpeg с scale lanczos, crop, fps,
          setpts если нужно, overlay PNG
   b. Склеить 5 mp4 через concat demuxer `-c copy`
   c. Очистить temp
4. Если включен Telegram auto_send — отправить файл через `sendDocument`

---

## 📝 Приложение: изменения v2.7 — Beautiful GUI доработки

По результатам тестирования UI (v2.6 → v2.7) внесены изменения:

1. **🎞 Фильмстрэп сегментов** — вместо 5 табов сверху теперь лента мини-превью
   всех 5 сегментов (кадр видео + номер + длительность), клик = открыть сегмент.
2. **▶ Живой видеопревью** — активный сегмент проигрывает своё видео прямо в
   превью (MoviePy-поток, ~12 fps, loop, без звука) с кнопкой паузы и таймкодом.
   Неактивные сегменты показывают статичный кадр (ffmpeg single-frame, дешёво по RAM).
3. **Эконом-режим по RAM** — превью/фильмстрэп грузятся одиночным ffmpeg-кадром
   (а не full-res MoviePy), треды стопаются при переключении сегмента; только один
   сегмент играет одновременно → нет OOM даже на 2 ГБ RAM.
4. **Устранены наезды** — убран QGraphicsDropShadowEffect (ломает layout в
   scroll-областях), новый QSS: карточки/секции/фильмстрэп с правильными отступами,
   чипы-пресеты переносятся на 2 строки (высота 66px).
5. **Заглушки вместо чёрного квадрата** — «🎬 Видео не выбрано» / «⏳ Загрузка…» /
   «💾 Эконом-режим» с иконкой и подсказкой; клик по заглушке = выбор видео.
6. **Восстановление последнего сегмента** — при старте открывается сохранённый
   таб (ui.tab), превью активного сегмента стартует автоматически.
