# Telegram AI Assistant

Telegram-бот на Python с историей диалогов, долгосрочной памятью, профилем пользователя, задачами и напоминаниями. Для генерации ответов используется локальная модель через Ollama.

## Requirements

- Python 3.10+
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- [Ollama](https://ollama.com/) with the model `qwen2.5:7b`

## Setup

1. Install Python dependencies:

   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Install and start Ollama, then download the model:

   ```bash
   ollama pull qwen2.5:7b
   ```

3. Copy `.env.example` to `.env` and add your Telegram bot token:

   ```env
   TELEGRAM_BOT_TOKEN=your_real_token_here
   ```

4. Start the bot:

   ```bash
   python main.py
   ```

The bot creates `memory.db` locally on first run. Do not commit `.env` or `memory.db`.

## Security

- Never publish your real Telegram bot token or other credentials.
- If a token was ever committed or shared publicly, revoke it via BotFather and create a new one.
- User messages, memory facts, tasks, and reminders are stored in the local SQLite database.
