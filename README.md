# Outline VPN Telegram Bot

Телеграм-бот для продажи и управления подписками Outline VPN через Telegram Stars. Развертывание через Docker Compose.

## Быстрый старт

```bash
git clone https://github.com/your-repo/outline-vpn-bot.git
cd outline-vpn-bot
cp .env.example .env
# отредактируйте .env файл
docker-compose up -d
```

## Переменные окружения (.env)

```env
TG_API_KEY=токен_бота_от_BotFather
OUTLINE_API_URL=URL_API_вашего_Outline_сервера
PROVIDER_TOKEN=токен_платежной_системы_Telegram
ADMIN_IDS=id_админов_через_запятую
PRICE_1_MONTH=150
PRICE_3_MONTHS=405
PRICE_6_MONTHS=765
PRICE_12_MONTHS=1440
```

## Основные команды

```bash
# Запуск
docker-compose up -d

# Остановка
docker-compose down

# Логи
docker-compose logs -f

# Рестарт
docker-compose restart
```

## Примечания

- Данные сохраняются в папке `./data`
- Убедитесь, что папка `data` существует и доступна для записи
- При проблемах проверьте `.env` файл и логи
