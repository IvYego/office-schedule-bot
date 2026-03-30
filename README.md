# Telegram Office/Home Schedule Bot

Бот для команды: кто работает из офиса, а кто из дома.

## Что умеет

- После `/start` — выбор имени из списка кнопками, затем доступ к календарю
- **Mini App** — полноэкранный веб-календарь (кнопка «Приложение»), те же данные что у бота
- `/name` — сменить имя
- Календарь с мультивыбором дней "из дома": `/calendar [YYYY-MM]`
- Опция "каждую неделю" для выбранного буднего дня
- Просмотр расписания на день: `/day [YYYY-MM-DD]`
- Просмотр расписания на неделю: `/week [YYYY-MM-DD]`
- Просмотр своей записи: `/myday [YYYY-MM-DD]`
- Удаление отметки "дом" на дату: `/delete YYYY-MM-DD`
- Список активных участников: `/participants`
- Удаление участника (только админ): `/remove_participant @username` или `/remove_participant user_id`
- Суббота и воскресенье всегда "выходной", остальные неотмеченные будни автоматически "офис"

## Быстрый старт

### 1) Создать бота в Telegram

1. Открыть [@BotFather](https://t.me/BotFather)
2. Отправить `/newbot`
3. Придумать имя и `username` бота
4. Сохранить токен (`BOT_TOKEN`)

### 2) Узнать Telegram user id админа

- Открой [@userinfobot](https://t.me/userinfobot) и получи свой `id`
- Этот id будет в переменной `ADMIN_IDS`

### 3) Локальный запуск

```bash
cd OfficeScheduleBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export BOT_TOKEN="токен_из_botfather"
export ADMIN_IDS="123456789"   # можно несколько через запятую
export DB_PATH="schedule.db"    # опционально, по умолчанию schedule.db

python bot.py
```

## Команды в боте

- `/start` — зарегистрироваться/активироваться
- `/help` — помощь
- `/calendar` — открыть календарь текущего месяца
- `/calendar 2026-03` — открыть календарь конкретного месяца
- `/set 2026-03-30 office` — записать день как офис
- `/set 2026-03-30 home` — записать день как дом
- `/day 2026-03-30` — расписание на день
- `/day` — расписание на сегодня
- `/week 2026-03-30` — расписание на неделю даты
- `/week` — расписание на текущую неделю
- `/myday 2026-03-30` — моя запись на день
- `/delete 2026-03-30` — удалить мою запись
- `/participants` — активные участники
- `/remove_participant @username` — удалить участника (только админ)

## Как выложить (деплой)

Ниже самый простой вариант без вебхуков: long polling, который работает на Render/Railway.

### Вариант A: Render

1. Залей папку `OfficeScheduleBot` в GitHub.
2. В Render создай **New Web Service** (или Worker).
3. Build command:
   ```bash
   pip install -r requirements.txt
   ```
4. Start command:
   ```bash
   python bot.py
   ```
5. Добавь Environment Variables:
   - `BOT_TOKEN`
   - `ADMIN_IDS` (например: `123456789,987654321`)
   - `DB_PATH` = `/tmp/schedule.db` (или путь с постоянным диском)
6. Deploy.

Важно: если используешь эфемерный диск, база может сбрасываться при рестарте. Для постоянства подключи persistent disk или вынеси БД в PostgreSQL.

### Вариант B: Railway

1. Создай новый проект из GitHub репозитория с `OfficeScheduleBot`.
2. Укажи Start command: `python bot.py`
3. Добавь переменные окружения:
   - `BOT_TOKEN`
   - `ADMIN_IDS`
   - `DB_PATH` (например, `/app/schedule.db`)
4. Запусти деплой.

## Telegram Mini App (веб-календарь внутри Telegram)

Да — как у сервисов с кнопкой **«Приложение»**: это обычный сайт по **HTTPS**, который Telegram открывает во встроенном браузере. Данные те же, что у бота (одна SQLite, если указать один и тот же `DB_PATH`).

### Что сделать

1. **Домен и HTTPS**  
   Нужен публичный URL (Let’s Encrypt / nginx / Caddy). Без HTTPS мини-аппы не открываются.

2. **Запуск API + статики** (на сервере, рядом с ботом):
   ```bash
   cd office-schedule-bot
   pip install -r requirements.txt
   export BOT_TOKEN="тот же что у бота"
   export DB_PATH="/data/schedule.db"   # тот же файл что у контейнера бота
   export MINIAPP_URL="https://schedule.example.com"  # тот же URL
   uvicorn miniapp_server:app --host 0.0.0.0 --port 8080
   ```
   За reverse-proxy отдай `https://schedule.example.com/` на `http://127.0.0.1:8080`.

3. **Переменная для бота**  
   В `.env` бота добавь `MINIAPP_URL=https://schedule.example.com` (без слэша в конце). Перезапусти бота — в клавиатуре появится кнопка **«Приложение»**.

4. **BotFather**  
   В [@BotFather](https://t.me/BotFather) → твой бот → **Bot Settings → Configure Mini App / Menu Button** — укажи тот же HTTPS URL главной страницы (корень сайта). Так кнопка в меню чата будет совпадать с `WebAppInfo`.

### Команды

- `/app` — если потерялась клавиатура, бот пришлёт одноразовую кнопку **Приложение**.

Если имя в боте ещё не выбрано (`/start`), мини-приложение попросит сначала выбрать имя в чате.

## Права и безопасность

- Удалять участников может только пользователь из `ADMIN_IDS`.
- Участник, удаленный админом, становится неактивным.
- При повторном `/start` пользователь активируется снова.

## Ограничения текущей версии

- Хранение в SQLite (подходит для небольшой команды).
- Нет интеграции с календарями.
- Нет напоминаний по расписанию (можно добавить отдельно).
