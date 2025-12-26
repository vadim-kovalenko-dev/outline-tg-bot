import asyncio
import json
import os
import requests
import sqlite3
import calendar
import urllib3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from command_texts import PRIVACY_TEXT, PAYSUPPORT_TEXT, TERMS_TEXT

# Отключаем предупреждения о небезопасных SSL-соединениях
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_TOKEN = os.getenv("TG_API_KEY", "")
OUTLINE_API_URL = os.getenv("OUTLINE_API_URL", "")
DB_NAME = "data/vpn.db"
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")
admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(id) for id in admin_ids_str.split(",") if admin_ids_str]

# Subscription prices in Telegram Stars
PRICE_1_MONTH = int(os.getenv("PRICE_1_MONTH"))
PRICE_3_MONTHS = int(os.getenv("PRICE_3_MONTHS"))
PRICE_6_MONTHS = int(os.getenv("PRICE_6_MONTHS"))
PRICE_12_MONTHS = int(os.getenv("PRICE_12_MONTHS"))

# Subscription names for invoices
SUBSCRIPTION_NAMES = {
    1: ("Подписка на VPN доступ на 1 месяц", "Подписка на 1 месяц"),
    3: ("Подписка на VPN доступ на 3 месяца", "Подписка на 3 месяца"),
    6: ("Подписка на VPN доступ на 6 месяцев", "Подписка на 6 месяцев"),
    12: ("Подписка на VPN доступ на 12 месяцев", "Подписка на 12 месяцев")
}

pending_invoices = {}  # user_id -> {"message_id": int, "duration": int, "price": int, "payload": str, "description": str, "title": str}

def utc_to_msk(utc_dt: datetime) -> datetime:
    """Конвертирует время из UTC в МСК (UTC+3)"""
    return utc_dt + timedelta(hours=3)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            vpn_key TEXT,
            ton_wallet TEXT,
            pending_comment TEXT,
            created_at TEXT,
            subscription_expires TEXT
        )
    """)
    conn.commit()
    conn.close()

def db_connect():
    return sqlite3.connect(DB_NAME)

def get_user(user_id: int):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, vpn_key, ton_wallet, pending_comment, created_at, subscription_expires
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        return cursor.fetchone()

def add_user(user_id: int, username: str, vpn_key: str, ton_wallet: str = None, pending_comment: str = None):
    with db_connect() as conn:
        cursor = conn.cursor()
        created_at = datetime.utcnow().isoformat()
        cursor.execute("""
            INSERT INTO users (user_id, username, vpn_key, ton_wallet, pending_comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, username, vpn_key, ton_wallet, pending_comment, created_at))
        conn.commit()

def update_vpn_key(user_id: int, vpn_key: str):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET vpn_key = ? WHERE user_id = ?", (vpn_key, user_id))
        conn.commit()

def update_subscription(user_id: int, expires: str):
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET subscription_expires = ? WHERE user_id = ?", (expires, user_id))
        conn.commit()

def get_all_users_with_subscription():
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, subscription_expires, vpn_key FROM users WHERE subscription_expires IS NOT NULL")
        return cursor.fetchall()

def get_total_users():
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def get_active_subscriptions():
    now = datetime.utcnow().isoformat()
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_expires > ?", (now,))
        return cursor.fetchone()[0]

def get_all_users():
    with db_connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, vpn_key, ton_wallet, pending_comment, created_at, subscription_expires
            FROM users
        """)
        return cursor.fetchall()

def add_months(sourcedate: datetime, months: int) -> datetime:
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = sourcedate.day
    try:
        return sourcedate.replace(year=year, month=month, day=day)
    except ValueError:
        last_day = calendar.monthrange(year, month)[1]
        return sourcedate.replace(year=year, month=month, day=last_day)

def create_vpn_key():
    # Проверяем, не содержит ли URL уже endpoint
    if OUTLINE_API_URL.endswith('/access-keys'):
        url = OUTLINE_API_URL
    else:
        url = f"{OUTLINE_API_URL}/access-keys"
    payload = {"name": "New Key"}
    
    try:
        print(f"DEBUG: Creating VPN key with URL: {url}")
        print(f"DEBUG: Payload: {payload}")
        # Используем verify=False для игнорирования ошибок SSL
        response = requests.post(
            url, 
            json=payload, 
            headers={"Content-Type": "application/json"}, 
            verify=False,
            timeout=30
        )
        print(f"DEBUG: Response status: {response.status_code}")
        print(f"DEBUG: Response text: {response.text[:500]}")
        if response.status_code == 201:
            return response.json()
        else:
            print("Unexpected server response:", response.status_code, response.text)
            return None
    except Exception as e:
        print("Exception creating key:", e)
        import traceback
        traceback.print_exc()
        return None

def revoke_vpn_key(vpn_key_data: dict):
    key_id = vpn_key_data.get("id")
    if not key_id:
        return False
    url = f"{OUTLINE_API_URL}/access-keys/{key_id}"
    try:
        response = requests.delete(url, verify=True, timeout=10)
        if response.status_code == 204:
            return True
        else:
            print("Failed to delete Outline key:", response.status_code, response.text)
            return False
    except Exception as e:
        print("Exception revoking Outline key:", e)
        return False

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔑 Получить VPN", callback_data="menu_get_vpn")
    builder.button(text="📖 Инструкция", callback_data="menu_info")
    builder.button(text="📊 Статус", callback_data="menu_settings")
    builder.button(text="💳 Оплата", callback_data="menu_payments")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    text = (
        "👋 Привет! Добро пожаловать в бот для покупки Outline VPN!\n\n"
        "Этот бот позволяет получить доступ к VPN по подписке с использованием Telegram Stars.\n\n"
        "Выберите опцию ниже:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(lambda c: c.data == "menu_get_vpn")
async def menu_get_vpn(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if not user:
        await callback.answer("❌ Сначала приобретите доступ к VPN.", show_alert=True)
        return
        
    if not user[6]:
        await callback.answer("❌ Сначала приобретите доступ к VPN.", show_alert=True)
        return
        
    try:
        expires = datetime.fromisoformat(user[6])
    except Exception:
        expires = None
        
    if not expires or expires <= datetime.utcnow():
        if user[2]:
            try:
                vpn_data = json.loads(user[2])
                if vpn_data:
                    revoke_vpn_key(vpn_data)
                    update_vpn_key(user_id, None)
            except json.JSONDecodeError:
                pass
                
        update_subscription(user_id, None)
        await callback.answer("Ваша подписка истекла. Пожалуйста, оформите новую подписку.", show_alert=True)
        return
        
    vpn_key_data = None
    if user[2]:
        try:
            vpn_key_data = json.loads(user[2])
        except json.JSONDecodeError:
            pass
            
    if not vpn_key_data:
        vpn_key_data = create_vpn_key()
        if vpn_key_data:
            update_vpn_key(user_id, json.dumps(vpn_key_data))
        else:
            await callback.answer()
            await bot.send_message(user_id, "❌ Не удалось создать VPN доступ. Пожалуйста, попробуйте позже.")
            return
            
    expires_msk = utc_to_msk(expires)
    text = (
        f"🔑 Ваш VPN доступ:\n<pre>{vpn_key_data.get('accessUrl')}</pre>\n\n"
        f"Подписка действительна до {expires_msk.strftime('%Y-%m-%d %H:%M:%S')} (МСК)."
    )
    await callback.answer()
    await bot.send_message(user_id, text, parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "menu_info")
async def menu_info(callback: types.CallbackQuery):
    text = (
        "📖 Инструкция:\n"
        "1. Установите официальное приложение Outline.\n"
        "<a href=\"https://apps.apple.com/app/outline-app/id1356177741\">iOS</a> | "
        "<a href=\"https://play.google.com/store/apps/details?id=org.outline.android.client\">Android</a> | "
        "<a href=\"https://getoutline.org/get-started/\">Windows</a> | "
        "<a href=\"https://apps.apple.com/app/outline-app/id1356178125\">macOS</a>\n"
        "2. Нажмите кнопку «💳 Оплата» и оплатите доступ через Telegram Stars.\n"
        "3. Скопируйте полученный ключ.\n"
        "4. Подключитесь: откройте Outline, нажмите на «+» в углу, вставьте ключ и добавьте сервер, а затем нажмите «Подключить»."
    )
    await callback.answer()
    await bot.send_message(callback.from_user.id, text, parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(lambda c: c.data == "menu_settings")
async def menu_settings(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    if user and user[6]:
        try:
            expiry = datetime.fromisoformat(user[6])
            expiry_msk = utc_to_msk(expiry)
            if expiry > datetime.utcnow():
                subscription_info = f"Подписка активна до {expiry_msk.strftime('%Y-%m-%d %H:%M:%S')} (МСК)."
            else:
                subscription_info = f"Подписка истекла {expiry_msk.strftime('%Y-%m-%d %H:%M:%S')} (МСК)."
        except Exception:
            subscription_info = "Не удалось определить статус подписки."
    else:
        subscription_info = "Нет активной подписки. Оформите подписку для доступа к VPN."
    await callback.answer()
    await bot.send_message(callback.from_user.id, f"📊 Статус вашей подписки:\n\n{subscription_info}")


@dp.callback_query(lambda c: c.data == "menu_payments")
async def menu_payments(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    if user and user[6]:
        try:
            expires = datetime.fromisoformat(user[6])
            if expires and expires > datetime.utcnow():
                await callback.answer("У вас уже есть активная подписка. Для продления выберите срок.", show_alert=True)
        except Exception:
            pass
            
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"1 месяц - {PRICE_1_MONTH} ⭐", callback_data="pay_sub_1")],
        [InlineKeyboardButton(text=f"3 месяца - {PRICE_3_MONTHS} ⭐", callback_data="pay_sub_3")],
        [InlineKeyboardButton(text=f"6 месяцев - {PRICE_6_MONTHS} ⭐", callback_data="pay_sub_6")],
        [InlineKeyboardButton(text=f"12 месяцев - {PRICE_12_MONTHS} ⭐", callback_data="pay_sub_12")],
        [InlineKeyboardButton(text="Отмена", callback_data="subscription_selection_cancel")]
    ])
    await callback.answer()
    await bot.send_message(callback.from_user.id, "💳 Выберите срок подписки для доступа к VPN:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "subscription_selection_cancel")
async def subscription_selection_cancel(callback: types.CallbackQuery):
    try:
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("pay_sub_"))
async def process_subscription_payment(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in pending_invoices:
        # Вместо простого alert показываем сообщение с кнопками для управления незавершенным платежом
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Отменить текущий платеж", callback_data="cancel_pending_payment")
        kb.button(text="🔄 Повторить оплату", callback_data="retry_payment")
        kb.adjust(1)
        await callback.answer()
        await bot.send_message(
            user_id,
            "У вас уже есть незавершенный платеж. Завершите или отмените его перед созданием нового.",
            reply_markup=kb.as_markup()
        )
        return
        
    try:
        duration = int(callback.data.split("_")[-1])
    except ValueError:
        await callback.answer("Некорректный срок подписки.", show_alert=True)
        return
        
    price_mapping = {
        1: PRICE_1_MONTH,
        3: PRICE_3_MONTHS,
        6: PRICE_6_MONTHS,
        12: PRICE_12_MONTHS
    }
    
    price_xtr = price_mapping.get(duration)
    if price_xtr is None:
        await callback.answer("Неизвестный срок подписки.", show_alert=True)
        return
        
    payload = f"subscription_{duration}_{price_xtr}"
    prices = [LabeledPrice(label=SUBSCRIPTION_NAMES[duration][1], amount=price_xtr)]
    
    kb = InlineKeyboardBuilder()
    kb.button(text=f"Оплатить {price_xtr} ⭐", pay=True)
    kb.button(text="Отмена", callback_data="subscription_cancel")
    kb.adjust(1)
    
    await callback.answer()
    try:
        invoice_message = await bot.send_invoice(
            chat_id=user_id,
            title="Покупка VPN подписки",
            description=SUBSCRIPTION_NAMES[duration][0],
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=prices,
            start_parameter="vpn_subscription",
            reply_markup=kb.as_markup()
        )
        pending_invoices[user_id] = {
            "message_id": invoice_message.message_id,
            "duration": duration,
            "price": price_xtr,
            "payload": payload,
            "description": SUBSCRIPTION_NAMES[duration][0],
            "title": "Покупка VPN подписки"
        }
    except Exception as e:
        await callback.message.answer(f"Error creating invoice: {e}")

@dp.callback_query(lambda c: c.data == "subscription_cancel")
async def subscription_cancel_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in pending_invoices:
        invoice_data = pending_invoices.pop(user_id)
        message_id = invoice_data["message_id"]
        try:
            await bot.delete_message(chat_id=user_id, message_id=message_id)
        except Exception:
            pass
        await callback.answer("Оплата отменена.")
    else:
        await callback.answer("Нет активных платежей для отмены.", show_alert=True)

@dp.callback_query(lambda c: c.data == "cancel_pending_payment")
async def cancel_pending_payment_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in pending_invoices:
        invoice_data = pending_invoices.pop(user_id)
        message_id = invoice_data["message_id"]
        try:
            await bot.delete_message(chat_id=user_id, message_id=message_id)
        except Exception:
            pass
        await callback.answer("Незавершенный платеж отменен.", show_alert=True)
        # Удаляем сообщение с кнопками управления платежом
        try:
            await bot.delete_message(chat_id=user_id, message_id=callback.message.message_id)
        except Exception:
            pass
    else:
        await callback.answer("Незавершенных платежей не найдено.", show_alert=True)

@dp.callback_query(lambda c: c.data == "retry_payment")
async def retry_payment_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in pending_invoices:
        # Получаем данные о предыдущем платеже
        invoice_data = pending_invoices[user_id]
        message_id = invoice_data["message_id"]
        duration = invoice_data["duration"]
        price_xtr = invoice_data["price"]
        payload = invoice_data["payload"]
        description = invoice_data["description"]
        title = invoice_data["title"]
        
        # Удаляем старое сообщение с инвойсом
        try:
            await bot.delete_message(chat_id=user_id, message_id=message_id)
        except Exception:
            pass
        
        # Удаляем сообщение с кнопками управления платежом
        try:
            await bot.delete_message(chat_id=user_id, message_id=callback.message.message_id)
        except Exception:
            pass
        
        # Создаем новый инвойс с теми же параметрами
        prices = [LabeledPrice(label=SUBSCRIPTION_NAMES[duration][1], amount=price_xtr)]
        
        kb = InlineKeyboardBuilder()
        kb.button(text=f"Оплатить {price_xtr} ⭐", pay=True)
        kb.button(text="Отмена", callback_data="subscription_cancel")
        kb.adjust(1)
        
        try:
            invoice_message = await bot.send_invoice(
                chat_id=user_id,
                title=title,
                description=description,
                payload=payload,
                provider_token=PROVIDER_TOKEN,
                currency="XTR",
                prices=prices,
                start_parameter="vpn_subscription",
                reply_markup=kb.as_markup()
            )
            # Обновляем данные в pending_invoices с новым message_id
            pending_invoices[user_id] = {
                "message_id": invoice_message.message_id,
                "duration": duration,
                "price": price_xtr,
                "payload": payload,
                "description": description,
                "title": title
            }
            await callback.answer("Платеж повторно отправлен.", show_alert=True)
        except Exception as e:
            await callback.answer(f"Ошибка при создании инвойса: {e}", show_alert=True)
    else:
        await callback.answer("Незавершенных платежей не найдено. Вы можете создать новый платеж.", show_alert=True)
        # Удаляем сообщение с кнопками управления платежом
        try:
            await bot.delete_message(chat_id=user_id, message_id=callback.message.message_id)
        except Exception:
            pass

@dp.pre_checkout_query()
async def pre_checkout_query_handler(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.content_type == types.ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: types.Message):
    payload = message.successful_payment.invoice_payload
    try:
        parts = payload.split("_")
        if parts[0] != "subscription":
            return
        duration = int(parts[1])
    except Exception:
        await message.answer("Error processing payment data.")
        return
        
    user_id = message.from_user.id
    user = get_user(user_id)
    now = datetime.utcnow()
    
    if not user:
        username = message.from_user.username or str(user_id)
        add_user(user_id, username, None)
        
    if user and user[6]:
        try:
            current_expiry = datetime.fromisoformat(user[6])
            if current_expiry > now:
                new_expiry = add_months(current_expiry, duration)
            else:
                new_expiry = add_months(now, duration)
        except Exception:
            new_expiry = add_months(now, duration)
    else:
        new_expiry = add_months(now, duration)
        
    update_subscription(user_id, new_expiry.isoformat())
    
    if user_id in pending_invoices:
        pending_invoices.pop(user_id)
        
    new_expiry_msk = utc_to_msk(new_expiry)
    
    await message.answer(
        f"Оплата прошла успешно! Подписка продлена до {new_expiry_msk.strftime('%Y-%m-%d %H:%M:%S')} (МСК)."
    )

async def subscription_reminder():
    while True:
        await asyncio.sleep(24 * 3600)
        users = get_all_users_with_subscription()
        now = datetime.utcnow()
        for user_id, sub_expires, vpn_key in users:
            try:
                expires = datetime.fromisoformat(sub_expires)
            except Exception:
                continue
                
            if expires <= now:
                if vpn_key:
                    try:
                        vpn_data = json.loads(vpn_key)
                        if vpn_data:
                            revoke_vpn_key(vpn_data)
                            update_vpn_key(user_id, None)
                    except json.JSONDecodeError:
                        pass
                        
                update_subscription(user_id, None)
                try:
                    await bot.send_message(user_id, "Ваша подписка истекла. Оформите новую подписку для продолжения доступа к VPN.")
                except Exception:
                    pass
            elif (expires - now).days <= 5:
                try:
                    await bot.send_message(user_id, f"Напоминание: Ваша подписка истечет через {(expires - now).days} дней. Продлите подписку для сохранения доступа к VPN.")
                except Exception:
                    pass

@dp.message(Command("admin"))
async def admin_panel_handler(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Доступ запрещен")
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="Закрыть", callback_data="admin_close")]
    ])
    await message.answer("Административная панель:", reply_markup=keyboard)


@dp.message(Command("privacy"))
async def privacy_handler(message: types.Message):
    await message.answer(PRIVACY_TEXT, parse_mode="HTML")


@dp.message(Command("paysupport"))
async def pay_support_handler(message: types.Message):
    await message.answer(PAYSUPPORT_TEXT, parse_mode="HTML")


@dp.message(Command("terms"))
async def terms_handler(message: types.Message):
    await message.answer(TERMS_TEXT, parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен", show_alert=True)
        return
        
    total_users = get_total_users()
    active_subs = get_active_subscriptions()
    
    text = (
        f"📊 Общая статистика:\n"
        f"Пользователи: {total_users}\n"
        f"Активные подписки: {active_subs}"
    )
    await callback.answer()
    await bot.send_message(callback.from_user.id, text)

@dp.callback_query(lambda c: c.data == "admin_users")
async def admin_users_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access Denied", show_alert=True)
        return
        
    users = get_all_users()
    if not users:
        text = "Пользователи не найдены."
    else:
        lines = []
        for user in users:
            user_id, username, vpn_key, ton_wallet, pending_comment, created_at, subscription_expires = user
            
            if subscription_expires:
                try:
                    expires = datetime.fromisoformat(subscription_expires)
                    expires_msk = utc_to_msk(expires)
                    formatted_date = expires_msk.strftime("%Y-%m-%d %H:%M:%S")
                    if expires > datetime.utcnow():
                        active = "✅"
                    else:
                        active = "❌"
                    line = f"ID: {user_id}, Имя: {username}, Подписка: {formatted_date} {active}"
                except Exception:
                    line = f"ID: {user_id}, Имя: {username}, Подписка: Ошибка формата"
            else:
                line = f"ID: {user_id}, Имя: {username}, Подписка: Нет подписки"
            lines.append(line)
            
        text = "\n".join(lines)
        
    await callback.answer()
    
    max_message_length = 4096
    if len(text) <= max_message_length:
        await bot.send_message(callback.from_user.id, text)
    else:
        chunks = [text[i:i+max_message_length] for i in range(0, len(text), max_message_length)]
        for chunk in chunks:
            await bot.send_message(callback.from_user.id, chunk)

@dp.callback_query(lambda c: c.data == "admin_close")
async def admin_close_handler(callback: types.CallbackQuery):
    await callback.answer("Админ-панель закрыта.")
    try:
        await bot.delete_message(callback.from_user.id, callback.message.message_id)
    except Exception:
        pass

async def main():
    init_db()
    asyncio.create_task(subscription_reminder())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
