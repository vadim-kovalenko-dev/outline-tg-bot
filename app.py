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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, InputFile, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from command_texts import PRIVACY_TEXT, PAYSUPPORT_TEXT, TERMS_TEXT

# Отключаем предупреждения о небезопасных SSL-соединениях
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_TOKEN = os.getenv("TG_API_KEY", "")
OUTLINE_API_URL = os.getenv("OUTLINE_API_URL", "")
DB_NAME = os.path.join(os.path.dirname(__file__), "data/vpn.db")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")
admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(id) for id in admin_ids_str.split(",") if admin_ids_str]

# Subscription names for invoices
SUBSCRIPTION_NAMES = {
    1: ("Подписка на VPN доступ на 1 месяц", "Подписка на 1 месяц"),
    3: ("Подписка на VPN доступ на 3 месяца", "Подписка на 3 месяца"),
    6: ("Подписка на VPN доступ на 6 месяцев", "Подписка на 6 месяцев"),
    12: ("Подписка на VPN доступ на 12 месяцев", "Подписка на 12 месяцев")
}

pending_invoices = {}
# Dictionary to track admin price editing state
admin_price_editing = {}

def load_prices():
    """Load prices from JSON file, create if doesn't exist"""
    prices_file = os.path.join(os.path.dirname(__file__), "data/prices.json")
    
    # Default prices if file doesn't exist
    default_prices = {
        "price_1_month": 80,
        "price_3_months": 210,
        "price_6_months": 390,
        "price_12_months": 720
    }
    
    try:
        if not os.path.exists(prices_file):
            # Create directory if doesn't exist
            os.makedirs(os.path.dirname(prices_file), exist_ok=True)
            # Save default prices
            with open(prices_file, 'w', encoding='utf-8') as f:
                json.dump(default_prices, f, ensure_ascii=False, indent=2)
            return default_prices
        
        # Load existing prices
        with open(prices_file, 'r', encoding='utf-8') as f:
            prices = json.load(f)
            
        # Ensure all required keys exist
        for key, default_value in default_prices.items():
            if key not in prices:
                prices[key] = default_value
                
        # Save updated prices if keys were missing
        with open(prices_file, 'w', encoding='utf-8') as f:
            json.dump(prices, f, ensure_ascii=False, indent=2)
            
        return prices
        
    except Exception as e:
        print(f"Error loading prices: {e}")
        return default_prices

def save_prices(prices):
    """Save prices to JSON file"""
    try:
        prices_file = os.path.join(os.path.dirname(__file__), "data/prices.json")
        os.makedirs(os.path.dirname(prices_file), exist_ok=True)
        
        with open(prices_file, 'w', encoding='utf-8') as f:
            json.dump(prices, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving prices: {e}")
        return False

# Load prices on startup
prices = load_prices()

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
        print("DEBUG: No key_id found in VPN key data")
        return False
    
    # Проверяем, не содержит ли URL уже endpoint (как в create_vpn_key)
    if OUTLINE_API_URL.endswith('/access-keys'):
        base_url = OUTLINE_API_URL.rstrip('/')
    else:
        base_url = OUTLINE_API_URL
    
    url = f"{base_url}/access-keys/{key_id}"
    
    try:
        print(f"DEBUG: Deleting VPN key with URL: {url}")
        print(f"DEBUG: Key ID: {key_id}")
        # Используем verify=False для игнорирования ошибок SSL (как в официальной документации)
        response = requests.delete(url, verify=False, timeout=30)
        print(f"DEBUG: Response status: {response.status_code}")
        print(f"DEBUG: Response text: {response.text[:500] if response.text else 'No response text'}")
        
        if response.status_code == 204:
            print(f"DEBUG: Successfully deleted Outline key with ID: {key_id}")
            return True
        else:
            print(f"DEBUG: Failed to delete Outline key. Status: {response.status_code}, Response: {response.text}")
            return False
    except Exception as e:
        print(f"DEBUG: Exception revoking Outline key: {e}")
        import traceback
        traceback.print_exc()
        return False

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

def get_main_menu(user_id: int = None):
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопку админ-панели для администраторов
    if user_id and user_id in ADMIN_IDS:
        builder.button(text="👑 Админ-панель", callback_data="admin_panel")
        builder.adjust(1)  # Отдельный ряд для админ-кнопки
    
    # Основные кнопки для всех пользователей
    builder.button(text="🔑 Получить VPN", callback_data="menu_get_vpn")
    builder.button(text="📖 Инструкция", callback_data="menu_info")
    builder.button(text="📊 Статус", callback_data="menu_settings")
    builder.button(text="💳 Оплата", callback_data="menu_payments")
    
    # Настраиваем расположение: если есть админ-кнопка, то 1+2+2, иначе просто 2+2
    if user_id and user_id in ADMIN_IDS:
        builder.adjust(1, 2, 2)
    else:
        builder.adjust(2)
    
    return builder.as_markup()

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    text = (
        "👋 Привет! Добро пожаловать в бот для покупки Outline VPN!\n\n"
        "Этот бот позволяет получить доступ к VPN по подписке с использованием Telegram Stars.\n\n"
        "Выберите опцию ниже:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id))

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
        f"🔑 Ваш VPN ключ:\n<pre>{vpn_key_data.get('accessUrl')}</pre>\n\n"
        f"Подписка действительна до {expires_msk.strftime('%Y-%m-%d %H:%M:%S')} (МСК)."
    )
    await callback.answer()
    await bot.send_message(user_id, text, parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "menu_info")
async def menu_info(callback: types.CallbackQuery):
    text = (
        "📖 Инструкция:\n\n"
        "1. Установите официальное приложение Outline.\n"
        "<a href=\"https://apps.apple.com/app/outline-app/id1356177741\">iOS</a> | "
        "<a href=\"https://play.google.com/store/apps/details?id=org.outline.android.client\">Android</a> | "
        "<a href=\"https://getoutline.org/get-started/\">Windows</a> | "
        "<a href=\"https://apps.apple.com/app/outline-app/id1356178125\">macOS</a>\n\n"
        "2. Нажмите кнопку «💳 Оплата» и оплатите доступ через Telegram Stars.\n\n"
        "3. Скопируйте полученный ключ.\n\n"
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
    
    # Get current prices from JSON
    current_prices = prices
            
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"1 месяц - {current_prices['price_1_month']} ⭐", callback_data="pay_sub_1")],
        [InlineKeyboardButton(text=f"3 месяца - {current_prices['price_3_months']} ⭐", callback_data="pay_sub_3")],
        [InlineKeyboardButton(text=f"6 месяцев - {current_prices['price_6_months']} ⭐", callback_data="pay_sub_6")],
        [InlineKeyboardButton(text=f"12 месяцев - {current_prices['price_12_months']} ⭐", callback_data="pay_sub_12")],
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
    
    # Удаляем предыдущий незавершенный инвойс если существует
    if user_id in pending_invoices:
        try:
            old_msg_id = pending_invoices[user_id]
            await bot.delete_message(chat_id=user_id, message_id=old_msg_id)
        except Exception as e:
            print(f"Failed to delete old invoice: {e}")
        pending_invoices.pop(user_id, None)
        
    try:
        duration = int(callback.data.split("_")[-1])
    except ValueError:
        await callback.answer("Некорректный срок подписки.", show_alert=True)
        return
    
    # Get current prices from JSON
    current_prices = prices
        
    # Map duration to price keys
    price_mapping = {
        1: current_prices['price_1_month'],
        3: current_prices['price_3_months'],
        6: current_prices['price_6_months'],
        12: current_prices['price_12_months']
    }
    
    price_xtr = price_mapping.get(duration)
    if price_xtr is None:
        await callback.answer("Неизвестный срок подписки.", show_alert=True)
        return
        
    payload = f"subscription_{duration}_{price_xtr}"
    invoice_prices = [LabeledPrice(label=SUBSCRIPTION_NAMES[duration][1], amount=price_xtr)]
    
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
            prices=invoice_prices,
            start_parameter="vpn_subscription",
            reply_markup=kb.as_markup()
        )
        pending_invoices[user_id] = invoice_message.message_id
    except Exception as e:
        await callback.message.answer(f"Error creating invoice: {e}")

@dp.callback_query(lambda c: c.data == "subscription_cancel")
async def subscription_cancel_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in pending_invoices:
        message_id = pending_invoices.pop(user_id)
        try:
            await bot.delete_message(chat_id=user_id, message_id=message_id)
        except Exception:
            pass
        await callback.answer("Оплата отменена.")
    else:
        await callback.answer("Нет активных платежей для отмены.", show_alert=True)

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
    
    # Вычисляем время для сообщений
    new_expiry_msk = utc_to_msk(new_expiry)
    
    # Автоматически создаем VPN ключ, если его нет
    user = get_user(user_id)  # Получаем актуальные данные пользователя
    vpn_key_data = None
    
    # Проверяем, есть ли у пользователя VPN ключ
    if not user[2] or user[2].strip() == '' or user[2].lower() == 'null' or user[2].lower() == 'none':
        vpn_key_data = create_vpn_key()
        if vpn_key_data:
            update_vpn_key(user_id, json.dumps(vpn_key_data))
            # Отправляем ключ отдельным сообщением
            await bot.send_message(user_id, f"🔑 Ваш VPN ключ:\n<pre>{vpn_key_data.get('accessUrl')}</pre>", parse_mode="HTML")
        else:
            # Если не удалось создать ключ, сообщаем пользователю
            await bot.send_message(user_id, "⚠️ Не удалось автоматически создать VPN ключ. Пожалуйста, нажмите кнопку '🔑 Получить VPN' в главном меню для создания ключа вручную.")
    
    # Отправляем сообщение об успешной оплате
    await message.answer(f"Оплата прошла успешно! Подписка продлена до {new_expiry_msk.strftime('%Y-%m-%d %H:%M:%S')} (МСК).")

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
            elif 1 <= (expires - now).days <= 3:
                days_left = (expires - now).days
                if days_left == 1:
                    message = "Ваша подписка истекает через 1 день. Продлите подписку для сохранения доступа к VPN."
                elif days_left == 2:
                    message = "Ваша подписка истекает через 2 дня. Продлите подписку для сохранения доступа к VPN."
                else:  # days_left == 3
                    message = "Ваша подписка истекает через 3 дня. Продлите подписку для сохранения доступа к VPN."
                try:
                    await bot.send_message(user_id, message)
                except Exception:
                    pass

@dp.message(Command("admin"))
async def admin_panel_handler(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Доступ запрещен")
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Изменить стоимость", callback_data="admin_change_prices")],
        [InlineKeyboardButton(text="📥 Скачать БД", callback_data="admin_download_db")],
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


@dp.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel_callback_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен", show_alert=True)
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Изменить стоимость", callback_data="admin_change_prices")],
        [InlineKeyboardButton(text="📥 Скачать БД", callback_data="admin_download_db")],
        [InlineKeyboardButton(text="Закрыть", callback_data="admin_close")]
    ])
    await callback.answer()
    await bot.send_message(callback.from_user.id, "Административная панель:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен", show_alert=True)
        return
    
    await callback.answer()  # Убрать анимацию загрузки сразу
        
    total_users = get_total_users()
    active_subs = get_active_subscriptions()
    
    text = (
        f"📊 Общая статистика:\n"
        f"Пользователи: {total_users}\n"
        f"Активные подписки: {active_subs}"
    )
    await bot.send_message(callback.from_user.id, text)

@dp.callback_query(lambda c: c.data == "admin_users")
async def admin_users_handler(callback: types.CallbackQuery):
    await callback.answer()  # Убрать анимацию загрузки
    await show_users_page(callback.from_user.id, 0)

async def show_users_page(user_id: int, page: int):
    """Показать страницу с пользователями"""
    users = get_all_users()
    if not users:
        text = "👥 Пользователи не найдены."
        await bot.send_message(user_id, text, parse_mode="HTML")
        return
    
    # Разбиваем на страницы по 10 пользователей
    users_per_page = 10
    total_pages = (len(users) + users_per_page - 1) // users_per_page
    
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    
    start_idx = page * users_per_page
    end_idx = min(start_idx + users_per_page, len(users))
    page_users = users[start_idx:end_idx]
    
    lines = []
    for user in page_users:
        user_id_val, username, vpn_key, ton_wallet, pending_comment, created_at, subscription_expires = user
        
        # Формируем кликабельное имя пользователя
        if username and username.strip():
            # Пользователь с username: @username как ссылка
            user_link = f'<a href="tg://user?id={user_id_val}">@{username}</a>'
        else:
            # Пользователь без username: ID как ссылка
            user_link = f'<a href="tg://user?id={user_id_val}">ID: {user_id_val}</a>'
        
        # Формируем информацию о подписке
        if subscription_expires:
            try:
                expires = datetime.fromisoformat(subscription_expires)
                expires_msk = utc_to_msk(expires)
                formatted_date = expires_msk.strftime("%Y-%m-%d %H:%M:%S")
                if expires > datetime.utcnow():
                    active = "✅"
                else:
                    active = "❌"
                subscription_info = f"📅 Подписка: {formatted_date} {active}"
            except Exception:
                subscription_info = "📅 Подписка: Ошибка формата"
        else:
            subscription_info = "📅 Подписка: Нет подписки"
        
        # Формируем строки для пользователя (новый формат)
        lines.append(f"👤 {user_link}")
        lines.append(f"🆔 ID: {user_id_val}")
        lines.append(f"{subscription_info}")
        lines.append("")  # Пустая строка между пользователями
    
    # Убираем последнюю пустую строку
    if lines and lines[-1] == "":
        lines.pop()
    
    # Добавляем заголовок с информацией о странице
    header = f"📋 Список пользователей\nСтраница {page + 1} из {total_pages}\n\n"
    text = header + "\n".join(lines)
    
    # Создаем клавиатуру с кнопками навигации
    keyboard_buttons = []
    
    if total_pages > 1:
        if page > 0:
            keyboard_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_users_page_{page - 1}"))
        
        keyboard_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="admin_users_page_info"))
        
        if page < total_pages - 1:
            keyboard_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"admin_users_page_{page + 1}"))
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons]) if keyboard_buttons else None
    
    # Отправляем сообщение
    await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("admin_users_page_"))
async def admin_users_page_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Access Denied", show_alert=True)
        return
    
    try:
        if callback.data == "admin_users_page_info":
            await callback.answer("Текущая страница")
            return
        
        page_str = callback.data.replace("admin_users_page_", "")
        page = int(page_str)
        await callback.answer()
        await show_users_page(callback.from_user.id, page)
    except ValueError:
        await callback.answer("Ошибка навигации", show_alert=True)

@dp.callback_query(lambda c: c.data == "admin_download_db")
async def admin_download_db_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен", show_alert=True)
        return
    
    await callback.answer()
    
    # Проверяем существование файла базы данных
    if not os.path.exists(DB_NAME):
        print(f"DEBUG: Файл БД не найден по пути: {DB_NAME}")
        await bot.send_message(callback.from_user.id, "❌ Файл базы данных не найден.")
        return
    
    # Создаем имя файла с текущей датой
    current_date = datetime.now().strftime("%Y-%m-%d")
    filename = f"vpn_backup_{current_date}.db"
    
    try:
        # Проверяем размер файла для диагностики
        file_size = os.path.getsize(DB_NAME)
        print(f"DEBUG: Путь к файлу БД: {DB_NAME}")
        print(f"DEBUG: Размер файла БД: {file_size} байт")
        
        # Читаем файл в память и отправляем через BufferedInputFile
        with open(DB_NAME, 'rb') as db_file:
            file_data = db_file.read()
        
        document = BufferedInputFile(file_data, filename=filename)
        await bot.send_document(
            chat_id=callback.from_user.id,
            document=document,
            caption=f"📁 Резервная копия базы данных\nДата: {current_date}\nРазмер: {file_size} байт"
        )
        print(f"DEBUG: Файл БД успешно отправлен администратору {callback.from_user.id}")
    except Exception as e:
        print(f"Ошибка при отправке файла базы данных: {e}")
        import traceback
        traceback.print_exc()  # Детальный вывод ошибки
        await bot.send_message(callback.from_user.id, f"❌ Ошибка при отправке файла: {str(e)}")

@dp.callback_query(lambda c: c.data == "admin_change_prices")
async def admin_change_prices_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен", show_alert=True)
        return
    
    user_id = callback.from_user.id
    admin_price_editing[user_id] = True
    
    await callback.answer()
    await bot.send_message(
        user_id,
        "Введите стоимость подписки в формате:\n"
        "80 210 390 720"
    )

@dp.message(F.text)
async def handle_admin_price_input(message: types.Message):
    user_id = message.from_user.id
    
    # Проверяем, является ли пользователь администратором и находится в режиме редактирования цен
    if user_id not in ADMIN_IDS or user_id not in admin_price_editing:
        return
    
    text = message.text.strip()
    
    # Проверяем на отмену
    if text.lower() in ["отмена", "cancel"]:
        admin_price_editing.pop(user_id, None)
        await message.answer("❌ Изменение стоимости отменено.")
        return
    
    # Проверяем формат: 4 числа через пробел
    try:
        parts = text.split()
        if len(parts) != 4:
            raise ValueError
        
        prices_list = [int(part) for part in parts]
        
        # Проверяем, что все числа положительные
        for price in prices_list:
            if price <= 0:
                raise ValueError
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Введите 4 числа через пробел.\n"
            "Пример: 80 210 390 720"
        )
        # НЕ удаляем из admin_price_editing - оставляем в режиме редактирования
        return
    
    # Создаем новый словарь цен
    new_prices = {
        "price_1_month": prices_list[0],
        "price_3_months": prices_list[1],
        "price_6_months": prices_list[2],
        "price_12_months": prices_list[3]
    }
    
    # Сохраняем в JSON
    if save_prices(new_prices):
        # Обновляем глобальную переменную prices
        global prices
        prices = new_prices
        
        # Удаляем из режима редактирования только при успехе
        admin_price_editing.pop(user_id, None)
        
        await message.answer(
            f"✅ Стоимость изменена:\n\n"
            f"1 месяц - {prices_list[0]} ⭐\n"
            f"3 месяца - {prices_list[1]} ⭐\n"
            f"6 месяцев - {prices_list[2]} ⭐\n"
            f"12 месяцев - {prices_list[3]} ⭐"
        )
    else:
        await message.answer(
            "❌ Ошибка при сохранении цен. Попробуйте позже."
        )
        # При ошибке сохранения тоже НЕ удаляем из admin_price_editing

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
