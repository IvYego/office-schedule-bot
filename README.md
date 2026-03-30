# Telegram Office/Home Schedule

Команда отмечает в **мини-приложении**, кто работает из дома; **бот** присылает ежедневную сводку «кто в офисе».

## Что умеет

- `/start` — выбор **имени** из списка (кнопки), без имени приложение недоступно
- **Mini App** — календарь: дни «дом» / остальные будни считаются «офис», сб и вс без офиса в логике дня
- **Ежедневное сообщение** всем, кто выбрал имя:
  - **Пн–Пт:** «Сегодня в офисе: …» и «Завтра в офисе: …»
  - **Суббота:** сообщений **нет**
  - **Воскресенье:** только «Завтра в офисе: …» (понедельник)
- `/name` — сменить имя
- `/participants` — список участников
- `/remove_participant` — только админ

Расписание в чате **не редактируется** (только имя и просмотр участников).

## Быстрый старт

### 1) Создать бота

1. [@BotFather](https://t.me/BotFather) → `/newbot`
2. Сохранить `BOT_TOKEN`

### 2) Telegram id админа

[@userinfobot](https://t.me/userinfobot) → `ADMIN_IDS`

### 3) Локальный запуск бота

```bash
cd OfficeScheduleBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export BOT_TOKEN="..."
export ADMIN_IDS="123456789"
export DB_PATH="schedule.db"

python bot.py
```

Нужен пакет с **job-queue** (в `requirements.txt` уже указан `python-telegram-bot[job-queue]`).

### Переменные рассылки (опционально)

| Переменная | По умолчанию |
|------------|----------------|
| `DIGEST_ENABLED` | `true` |
| `DIGEST_HOUR` | `9` |
| `DIGEST_MINUTE` | `0` |
| `DIGEST_TIMEZONE` | `Europe/Moscow` |

## Команды в боте

- `/start` — имя и приветствие
- `/app` — кнопка «Приложение», если потерялась клавиатура
- `/name` — сменить имя
- `/participants` — участники
- `/menu` / `/help` — кнопки и текст помощи
- `/remove_participant @user` — удалить участника (админ)

## Деплой (Render / Railway / свой сервер)

```bash
pip install -r requirements.txt
python bot.py
```

Переменные: `BOT_TOKEN`, `ADMIN_IDS`, `DB_PATH`, при необходимости `MINIAPP_URL` и `DIGEST_*`.

## Telegram Mini App

Нужен **HTTPS**. Подними `uvicorn miniapp_server:app` и прокси на тот же домен; `BOT_TOKEN`, `DB_PATH` и `MINIAPP_URL` должны совпадать с ботом. Подробности — в истории коммитов или см. `.env.example`.

## Права

- Удалять участников может только `ADMIN_IDS`.

## Ограничения

- SQLite; два процесса (бот + miniapp) — один файл БД и `pip install` с `[job-queue]`.
