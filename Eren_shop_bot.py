# ============================================================
# RME Bot — RomeoSaveMe Recharge Bot
# ============================================================

import os
import re
import json
import uuid
import html
import sqlite3
import requests
import asyncio
import io
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = 5698123475
ALERT_CHAT_ID = int(os.getenv("ALERT_CHAT_ID", "0"))

RME_BASE_URL = "https://romeosaveme.com"
RME_API_KEY = os.getenv("RME_API_KEY", "")
MARGIN = 1.04
USD_RATE = float(os.getenv("USD_RATE", "4900"))

# ---------- Data Directory ----------
DATA_DIR = os.getenv("DATA_DIR", ".")
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        DATA_DIR = "."

MAINTENANCE_FILE = os.path.join(DATA_DIR, "maintenance.json")

def is_maintenance():
    if not os.path.exists(MAINTENANCE_FILE):
        return False
    try:
        with open(MAINTENANCE_FILE, "r") as f:
            data = json.load(f)
            return data.get("status", False)
    except Exception:
        return False

def set_maintenance(status: bool):
    with open(MAINTENANCE_FILE, "w") as f:
        json.dump({"status": status}, f)

USER_DB = os.path.join(DATA_DIR, "users.db")
ACCESS_DB = os.path.join(DATA_DIR, "access.db")
PRICE_DB = os.path.join(DATA_DIR, "prices.db")

print(f"DATA_DIR: {DATA_DIR}")
print(f"ALERT_CHAT_ID: {ALERT_CHAT_ID}")
print(f"RME API: {RME_BASE_URL}")


# ---------- Database Init ----------
def init_db():
    conn = sqlite3.connect(USER_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance REAL DEFAULT 0.0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        game TEXT,
        product TEXT,
        player_id TEXT,
        zone_id TEXT,
        price REAL,
        status TEXT DEFAULT 'pending',
        transaction_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS deposit_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        method TEXT,
        screenshot_id TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(ACCESS_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS bot_access (
        user_id INTEGER PRIMARY KEY,
        status TEXT DEFAULT 'pending',
        requested_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(PRICE_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS manual_price (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        name TEXT,
        price REAL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()


init_db()


# ============================================================
# Part 2 — RomeoSaveMe API Functions
# ============================================================

def rme_headers():
    return {
        "Authorization": f"Bearer {RME_API_KEY}",
        "Content-Type": "application/json",
    }


def rme_get(path, params=None, retries=3):
    url = RME_BASE_URL + path
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=rme_headers(), params=params, timeout=120)
            if response.status_code >= 400:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                try:
                    data = response.json()
                    return None, (data.get("message") or f"HTTP {response.status_code}")
                except Exception:
                    return None, f"HTTP {response.status_code}"
            try:
                return response.json(), None
            except Exception:
                return {"success": False, "message": response.text}, None
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None, str(e)
    return None, "API Error (Retry Failed)"


def rme_post(path, payload, retries=3):
    url = RME_BASE_URL + path
    for attempt in range(retries):
        try:
            response = requests.post(url, headers=rme_headers(), json=payload, timeout=120)
            if response.status_code >= 400:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                try:
                    data = response.json()
                    return None, (data.get("message") or f"HTTP {response.status_code}")
                except Exception:
                    return None, f"HTTP {response.status_code}"
            try:
                return response.json(), None
            except Exception:
                return {"success": False, "message": response.text}, None
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None, str(e)
    return None, "API Error (Retry Failed)"


def rme_get_games():
    return rme_get("/api/v1/games")


def rme_get_products(slug):
    return rme_get(f"/api/v1/games/{slug}/products")


def rme_get_wallet():
    return rme_get("/api/v1/wallet")


def rme_check_player(slug, player_id, zone_id):
    inputs = [str(player_id)]
    if zone_id:
        inputs.append(str(zone_id))
    payload = {
        "gameSlug": slug,
        "inputs": inputs,
    }
    return rme_post("/api/v1/check-player", payload)


def rme_ml_region_check(player_id, zone_id):
    payload = {
        "playerId": str(player_id),
        "zoneId": str(zone_id),
        "game": "mobile_legends",
    }
    return rme_post("/api/v1/ml-region-check", payload)


def rme_create_order(product_code, player_id, zone_id):
    inputs = [str(player_id)]
    if zone_id:
        inputs.append(str(zone_id))
    payload = {
        "productCode": product_code,
        "inputs": inputs,
        "clientRef": "RME-" + uuid.uuid4().hex[:16].upper(),
    }
    return rme_post("/api/v1/orders", payload)


def rme_get_order(order_id):
    return rme_get(f"/api/v1/orders/{order_id}")


def get_final_price(base_price):
    return round(float(base_price) * MARGIN)


def get_usd_price(mmk_price):
    try:
        usd = float(mmk_price) / USD_RATE
        return round(usd, 2)
    except Exception:
        return 0.0


def format_mm_time(created_at):
    """Server Time ကို မြန်မာစံတော်ချိန် (UTC+6:30) ပြောင်းပေးမယ့် Function"""
    try:
        utc_time = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S")
        mm_time = utc_time + timedelta(hours=6, minutes=30)
        return mm_time.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return str(created_at or "-")


# ============================================================
# Part 3 — Balance System + Keyboards + Game/Package List
# ============================================================

def get_user_balance(user_id):
    conn = sqlite3.connect(USER_DB)
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    conn.close()
    return row[0] if row else 0.0


def add_user_balance(user_id, amount):
    conn = sqlite3.connect(USER_DB)
    conn.execute("""INSERT INTO users (user_id, balance, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
        balance = balance + ?
    """, (int(user_id), float(amount), float(amount)))
    conn.commit()
    conn.close()


def deduct_user_balance(user_id, amount):
    conn = sqlite3.connect(USER_DB)
    conn.execute("""UPDATE users SET balance = balance - ?
        WHERE user_id = ? AND balance >= ?
    """, (float(amount), int(user_id), float(amount)))
    affected = conn.total_changes
    conn.commit()
    conn.close()
    return affected > 0


def save_order(user_id, game, product, player_id, zone_id, price, status, transaction_id):
    conn = sqlite3.connect(USER_DB)
    conn.execute("""INSERT INTO orders
        (user_id, game, product, player_id, zone_id, price, status, transaction_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (int(user_id), game, product, str(player_id), str(zone_id),
          float(price), status, str(transaction_id)))
    conn.commit()
    conn.close()


def update_order_status(transaction_id, status):
    conn = sqlite3.connect(USER_DB)
    conn.execute("UPDATE orders SET status=? WHERE transaction_id=?",
                 (str(status).lower(), str(transaction_id)))
    conn.commit()
    conn.close()


def normalize_order_status(value):
    status = str(value or "pending").strip().lower().replace(" ", "_").replace("-", "_")
    if status in {"success", "successful", "completed", "complete", "done", "finished", "succeeded"}:
        return "completed"
    if status in {"failed", "failure", "error", "cancelled", "canceled", "rejected", "refunded"}:
        return "failed"
    # API statuses holding and processing should remain pending.
    if status in {"pending", "processing", "in_progress", "queued", "created", "waiting", "initiated", "holding"}:
        return "pending"
    return status or "pending"


def extract_order_result(data):
    if not isinstance(data, dict):
        return {}
    # Use the data payload when the API wraps its response in "data".
    result = data.get("data", data)
    # Use the nested order object when present.
    if isinstance(result, dict) and isinstance(result.get("order"), dict):
        result = result["order"]
    return result if isinstance(result, dict) else {}

async def monitor_order_status(application, user_id, order_id, product_name="", game_name="", message_id=None):
    """Poll RME until an order reaches a final status, then notify the customer."""
    if not order_id or str(order_id) == "-":
        return
        
    is_first_check = True  # ပထမဆုံး စစ်တာလား ဆိုတာ မှတ်ထားမယ်
    
    for _ in range(40):  # up to about 10 minutes
        if not is_first_check:
            await asyncio.sleep(15)  # ပထမတစ်ခါ မဟုတ်မှ ၁၅ စက္ကန့် စောင့်မယ်
        is_first_check = False
        
        try:
            data, error = await asyncio.to_thread(rme_get_order, str(order_id))
            if error or not data:
                print(f"API Error checking order {order_id}: {error}")
                continue
            result = extract_order_result(data)

            # API status may be returned under status, orderStatus, or state.
            raw_status = (
                result.get("status") or
                result.get("orderStatus") or
                result.get("state") or
                (data.get("status") if isinstance(data, dict) else None) or
                (data.get("orderStatus") if isinstance(data, dict) else None)
            )
            if not raw_status:
                print(f"No status found in API response: {data}")
                continue

            status = normalize_order_status(raw_status)
            print(f"Checking Order {order_id} | API Status: {raw_status} | Normalized: {status}")

            # RME docs: walletRefunded becomes true after the website wallet refund.
            refunded_value = result.get("walletRefunded")
            if refunded_value is None and isinstance(data, dict):
                refunded_value = data.get("walletRefunded")
            wallet_refunded = refunded_value is True or str(refunded_value).strip().lower() in {"true", "1", "yes"}
            refund_amount = 0
            refund_applied = False

            # Only credit our bot wallet after RME confirms a refund, and only once.
            if status == "failed" and wallet_refunded:
                conn = sqlite3.connect(USER_DB)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    saved_order = conn.execute(
                        "SELECT user_id, price, status FROM orders WHERE transaction_id=?",
                        (str(order_id),),
                    ).fetchone()
                    if saved_order and int(saved_order[0]) == int(user_id):
                        refund_amount = float(saved_order[1] or 0)
                        previous_status = normalize_order_status(saved_order[2])
                        if previous_status != "failed":
                            changed = conn.execute(
                                "UPDATE orders SET status='failed' WHERE transaction_id=? AND lower(status)!='failed'",
                                (str(order_id),),
                            ).rowcount
                            if changed:
                                conn.execute(
                                    "UPDATE users SET balance=balance+? WHERE user_id=?",
                                    (refund_amount, int(user_id)),
                                )
                                refund_applied = True
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

            update_order_status(order_id, status)
            
            if status in ("completed", "failed"):
                if status == "completed":
                    message = ("🔔 <b>ORDER SUCCESS!</b>\n\n"
                               "━━━━━━━━━━━━━━━━━━━━\n"
                               "✅ Status: <b>SUCCESS</b>\n"
                               f"🆔 Order ID: <code>{html.escape(str(order_id))}</code>\n"
                               f"💎 Product: <b>{html.escape(str(product_name))}</b>\n"
                               "🎉 Your recharge has been completed.\n\n"
                               "━━━━━━━━━━━━━━━━━━━━\n"
                               "⚡ Power By Eren Bot")
                else:
                    refund_notice = (
                        f"\n\n💸 <b>Refunded: {refund_amount:,.0f} MMK</b>"
                        if refund_applied else ""
                    )
                    message = ("❌ <b>Order Failed</b>\n\n"
                               f"🆔 Order ID: <code>{html.escape(str(order_id))}</code>\n"
                               "Please contact Admin to check your order."
                               f"{refund_notice}")

                # 👇 မက်ဆေ့ချ်အဟောင်းကို ပြင်မယ် (အသစ် မပို့တော့ဘူး)
                try:
                    if message_id:
                        try:
                            await application.bot.edit_message_text(
                                chat_id=user_id,
                                message_id=message_id,
                                text=message,
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            print(
                                f"EDIT MSG ERROR | Order: {order_id} | User: {user_id} | "
                                f"Message ID: {message_id} | {type(e).__name__}: {e}"
                            )
                            # Edit မရရင် Message အသစ် ပြန်ပို့မယ်
                            await application.bot.send_message(
                                chat_id=user_id,
                                text=message,
                                parse_mode="HTML"
                            )
                    else:
                        await application.bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
                except Exception as e:
                    print("ORDER STATUS NOTIFICATION ERROR:", e)

                # Send to Alarm Group
                try:
                    alert_target = ALERT_CHAT_ID if ALERT_CHAT_ID else ADMIN_ID
                    if status == "completed":
                        alert_text = (
                            f"✅ <b>ORDER SUCCESS</b>\n\n"
                            f"Order <code>{html.escape(str(order_id))}</code>\n"
                            f"<b>{html.escape(product_name)}</b> has been delivered successfully.\n\n"
                            f"🎮 {html.escape(game_name)}\n"
                            f"👤 User ID: <code>{user_id}</code>"
                        )
                    else:
                        refund_notice = (
                            f"\n💸 Refunded: <b>{refund_amount:,.0f} MMK</b>"
                            if refund_applied else ""
                        )
                        alert_text = (
                            f"❌ <b>ORDER FAILED</b>\n\n"
                            f"Order <code>{html.escape(str(order_id))}</code>\n"
                            f"<b>{html.escape(product_name)}</b> has failed.\n\n"
                            f"🎮 {html.escape(game_name)}\n"
                            f"👤 User ID: <code>{user_id}</code>"
                            f"{refund_notice}"
                        )
                    await application.bot.send_message(
                        chat_id=alert_target, text=alert_text, parse_mode="HTML",
                    )
                except Exception as e:
                    print("ALARM SEND ERROR:", e)
                return
        except Exception as e:
            print("ORDER STATUS CHECK ERROR:", e)

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["💎 Mobile Legends", "🎮 PUBG"],
            ["🔥 Blood Strike", "🛡️ Arena Breakout"],
            ["💻 Capcut", "✈️ Telegram"],
            ["✨ Gemini", "🔍 Check ID"],
            ["🏆 Top Spenders", "👤 My Profile"],
            ["📞 Contact Admin"],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["⏳ Pending Deposits", "📊 All Users"],
            ["📦 All Orders", "💳 All Deposits"],
            ["📢 Broadcast", "👤 User Info"],
            ["⌨️ Commands Guide"],
            ["🔙 Back to Main Menu"],
        ],
        resize_keyboard=True,
    )


def cancel_keyboard():
    return ReplyKeyboardMarkup(
        [["❌ Cancel Order"]],
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user

    if not await request_access(update, context, force_request=True):
        return

    # Save user info on every /start
    conn = sqlite3.connect(USER_DB)
    conn.execute("""INSERT INTO users (user_id, username, first_name, balance, created_at)
        VALUES (?, ?, ?, 0.0, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
        username = COALESCE(excluded.username, users.username),
        first_name = COALESCE(excluded.first_name, users.first_name)
    """, (int(user.id), user.username, user.first_name))
    conn.commit()
    conn.close()

    await update.message.reply_text(
    f'<tg-emoji emoji-id="6300713883610912960">⚡</tg-emoji> <b>Welcome, {html.escape(user.first_name or "User")}!</b> <tg-emoji emoji-id="6300713883610912960">⚡</tg-emoji>\n'
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    '<tg-emoji emoji-id="6039653741156633541">🛒</tg-emoji> <b>Eren\'s Shop Bot</b>\n\n'
    "👇 Choose from the menu below",
    parse_mode="HTML",
    reply_markup=main_keyboard(),
    )


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    balance = get_user_balance(user.id)
    usd_balance = get_usd_price(balance)
    await update.message.reply_text(
        f"💰 <b>My Balance</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 {html.escape(user.first_name or 'User')}\n"
        f"💰 Balance : <b>{balance:,.0f} MMK</b>\n"
        f"💲 USD : <b>{usd_balance:,.2f}$</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""SELECT game, product, player_id, price, status, transaction_id, created_at
        FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10
    """, (user.id,)).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📦 <b>My Orders</b>\n\n📭 No orders yet",
            parse_mode="HTML", reply_markup=main_keyboard(),
        )
        return

    lines = ["📦 <b>My Orders (Last 10)</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for game, product, player_id, price, status, txn_id, created_at in rows:
        normalized_status = normalize_order_status(status)
        status_emoji = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(normalized_status, "❓")

        # ရက်စွဲကို မြန်မာချိန်ပြောင်းမယ်
        date_str = format_mm_time(created_at)

        lines.append(f"{status_emoji} <b>{html.escape(game)}</b>")
        lines.append(f"📦 {html.escape(product)}")
        lines.append(f"🆔 Player ID: {html.escape(str(player_id))}")
        lines.append(f"💰 Price: <b>{price:,.0f} MMK</b>")
        lines.append(f"🆔 Order ID: <code>{html.escape(str(txn_id))}</code>")
        lines.append(f"📅 Date: {html.escape(str(date_str))}")
        lines.append(f"📌 Status: <b>{html.escape(normalized_status.upper())}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_keyboard())


async def show_deposit_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""SELECT amount, method, status, created_at
        FROM deposit_requests WHERE user_id=? ORDER BY created_at DESC LIMIT 10
    """, (user.id,)).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "💳 <b>Deposit History</b>\n\n📭 No deposit history yet",
            parse_mode="HTML", reply_markup=main_keyboard(),
        )
        return

    lines = ["💳 <b>Deposit History (Last 10)</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for amount, method, status, created_at in rows:
        norm = normalize_order_status(status)
        if str(status or "").lower() == "approved":
            norm = "completed"
        elif str(status or "").lower() in ("rejected", "cancelled", "canceled"):
            norm = "failed"
        status_emoji = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(norm, "❓")

        if method == "USDT":
            amount_text = f"💲 ${get_usd_price(amount):,.2f}"
        else:
            amount_text = f"💵 {float(amount or 0):,.0f} MMK"

        # ရက်စွဲကို မြန်မာချိန်ပြောင်းမယ်
        date_text = format_mm_time(created_at)

        lines.append(f"{status_emoji} <b>{html.escape(amount_text)}</b>")
        lines.append(f"🪙 Method: {html.escape(str(method or '-'))}")
        lines.append(f"📌 Status: {html.escape(norm.upper())}")
        lines.append(f"📅 {html.escape(date_text)}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_keyboard())


async def show_top_spenders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""
        SELECT o.user_id, u.first_name, u.username, SUM(o.price) as total_spent, COUNT(o.id) as total_orders
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.user_id
        WHERE o.status = 'completed'
        GROUP BY o.user_id
        ORDER BY total_spent DESC
        LIMIT 10
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "🏆 <b>Top Spenders</b>\n\n📭 No completed orders yet.",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["🏆 <b>Top 10 Spenders</b>", "━━━━━━━━━━━━━━━━━━━━", ""]

    for index, row in enumerate(rows, start=1):
        user_id, first_name, username, total_spent, total_orders = row

        if index == 1:
            medal = "🥇"
        elif index == 2:
            medal = "🥈"
        elif index == 3:
            medal = "🥉"
        else:
            medal = f"#{index}"

        display_name = first_name or "User"
        username_text = f"@{username}" if username else "-"

        lines.append(f"{medal} <b>{html.escape(display_name)}</b> ({html.escape(username_text)})")
        lines.append(f"💰 Total Spent: <b>{float(total_spent or 0):,.0f} MMK</b>")
        lines.append(f"📦 Orders: {total_orders}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = f"@{user.username}" if user.username else "-"

    # Role: Admin or User
    role = "👑 Admin" if user.id == ADMIN_ID else "👤 User"

    if user.id == ADMIN_ID:
        keyboard = ReplyKeyboardMarkup(
            [["💰 Balance", "📋 Orders"], ["⚙️ Admin Panel"], ["🏠 Back to Menu"]],
            resize_keyboard=True,
        )
    else:
        keyboard = ReplyKeyboardMarkup(
            [["💰 Balance", "📋 Orders"], ["🏠 Back to Menu"]],
            resize_keyboard=True,
        )

    await update.message.reply_text(
        "👤 <b>My Account</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Username: <b>{html.escape(username)}</b>\n"
        f"📛 Role: {role}\n\n"
        "👇 Choose an option below",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

async def handle_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Back to Profile Button
    back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Profile", callback_data="back_to_profile")]])

    if query.data == "profile_deposit":
        context.user_data["state"] = "deposit_amount"
        await query.edit_message_text(
            "💰 <b>Deposit (MMK)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Enter the amount you want to deposit:\n"
            "(Min: 1,000 MMK)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Deposit", callback_data="back_to_profile")]])
        )
    elif query.data == "profile_orders":
        user = query.from_user
        conn = sqlite3.connect(USER_DB)
        rows = conn.execute(
            "SELECT game, product, player_id, price, status, transaction_id, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
            (user.id,),
        ).fetchall()
        conn.close()

        lines = ["📦 <b>My Orders (Last 10)</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
        for game, product, player_id, price, status, txn_id, created_at in rows:
            normalized_status = normalize_order_status(status)
            status_emoji = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(normalized_status, "❓")
            # ရက်စွဲကို မြန်မာချိန်ပြောင်းမယ်
            date_str = format_mm_time(created_at)

            lines.append(f"{status_emoji} <b>{html.escape(str(game))}</b>")
            lines.append(f"📦 {html.escape(str(product))}")
            lines.append(f"🆔 Player ID: {html.escape(str(player_id))}")
            lines.append(f"💰 Price: <b>{float(price or 0):,.0f} MMK</b>")
            lines.append(f"🆔 Order ID: <code>{html.escape(str(txn_id))}</code>")
            lines.append(f"📅 Date: {html.escape(str(date_str))}")
            lines.append(f"📌 Status: <b>{html.escape(normalized_status.upper())}</b>")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
        if not rows:
            lines.append("📭 No orders yet")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=back)

    elif query.data == "back_to_profile":
        user = query.from_user
        username = f"@{user.username}" if user.username else "-"

        # ✅ Role စစ်မယ်
        role = "👑 Admin" if user.id == ADMIN_ID else "👤 User"

        if user.id == ADMIN_ID:
            keyboard = ReplyKeyboardMarkup(
                [["💰 Balance", "📋 Orders"], ["⚙️ Admin Panel"], ["🏠 Back to Menu"]],
                resize_keyboard=True,
            )
        else:
            keyboard = ReplyKeyboardMarkup(
                [["💰 Balance", "📋 Orders"], ["🏠 Back to Menu"]],
                resize_keyboard=True,
            )
        await query.edit_message_text(
            "👤 <b>My Account</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Username: <b>{html.escape(username)}</b>\n"
            f"📛 Role: {role}\n\n"
            "👇 Choose an option below",
            parse_mode="HTML",
        )
        await context.bot.send_message(
            chat_id=user.id,
            text="Please choose from the menu below.",
            reply_markup=keyboard,
        )
    elif query.data == "back_to_main":
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="🏠 Main Menu",
            reply_markup=main_keyboard(),
        )

async def show_game_products(update: Update, context: ContextTypes.DEFAULT_TYPE, slug: str, name: str, filter_type: str = None):
    loading_msg = await update.message.reply_text("⏳ <b>Loading...</b>", parse_mode="HTML")
    data, error = rme_get_products(slug)
    if error:
        await update.message.reply_text(
            f"❌ <b>Error</b>\n\n{html.escape(str(error))}",
            parse_mode="HTML", reply_markup=main_keyboard(),
        )
        await asyncio.sleep(2)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        return

    products = data.get("products", [])

    print(f"=== ALL PRODUCTS (slug={slug}) ===")
    for p in products:
        print(f"  NAME: {p.get('name')} | CODE: {p.get('code')} | PRICE: {p.get('price')}")
    print(f"=== TOTAL: {len(products)} ===")

    if filter_type == "package":
        products = [p for p in products if "UC" not in p.get("name", "").upper()]
    elif filter_type == "uc":
        products = [p for p in products if "UC" in p.get("name", "").upper()]

    if not products:
        await update.message.reply_text(
            f"📭 <b>{name}</b>\n\nNo products available",
            parse_mode="HTML", reply_markup=main_keyboard(),
        )
        await asyncio.sleep(2)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        return

    context.user_data["game_slug"] = slug
    context.user_data["game_name"] = name
    context.user_data["products"] = products

    buttons = []
    for p in products:
        pname = p.get("name", "?")
        base_price = p.get("price", 0)
        price = get_final_price(base_price)
        stock = p.get("stock")
        label = f"{pname} — {price:,.0f} Ks (${get_usd_price(price):,.2f})"
        if stock is not None:
            label += f" | Stock: {stock}"
        buttons.append([label])

    buttons.append(["⬅️ Back to Games", "🏠 Main Menu"])

    await update.message.reply_text(
        f"🎮 <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 {len(products)} Packages\n\n"
        "👇 Choose a package",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
    )

    # Delete loading message after 2 seconds
    await asyncio.sleep(2)
    try:
        await loading_msg.delete()
    except Exception:
        pass

# ============================================================
# Part 4 — Check ID + Double Diamond + Order Flow
# ============================================================

async def show_check_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "check_id_game"
    await update.message.reply_text(
        "🔍 <b>Check ID</b>\n\n"
        "Choose a game:\n\n"
        "• <code>ml</code> — Mobile Legends\n"
        "• <code>pubg</code> — PUBG",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )


async def show_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "deposit_amount"
    await update.message.reply_text(
        "💵 <b>Deposit (MMK)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Enter the amount you want to deposit:\n"
        "(Min: 1,000 MMK)",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup([["❌ Cancel Deposit"]], resize_keyboard=True),
    )

async def process_check_id_zone(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    slug = context.user_data.get("check_slug")
    player_id = context.user_data.get("check_player_id")
    zone_id = text

    loading_msg = await update.message.reply_text("🔍 <b>Checking...</b>", parse_mode="HTML")

    data, error = rme_check_player(slug, player_id, zone_id)
    if error:
        await update.message.reply_text(
            f"❌ <b>Failed</b>\n\n{html.escape(str(error))}",
            parse_mode="HTML", reply_markup=main_keyboard(),
        )
        # Delete checking message after 2 seconds
        await asyncio.sleep(2)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        context.user_data.clear()
        return

    nickname = data.get("nickname") or data.get("username") or data.get("name") or "-"
    region = data.get("region") or "-"

    dd_text = ""
    if slug == "mobile-legends":
        region_data, region_err = rme_ml_region_check(player_id, zone_id)
        if not region_err and region_data:
            bonuses = region_data.get("bonuses", [])
            if bonuses:
                dd_lines = ["", "💎 <b>Double Diamond</b>"]
                for b in bonuses:
                    amount = b.get("amount", "?")
                    eligible = b.get("eligible", False)
                    emoji = "🟢" if eligible else "🔴"
                    dd_lines.append(f"{emoji} <b>{amount}</b>")
                dd_text = "\n".join(dd_lines) + "\n"

    await update.message.reply_text(
        "🔍 <b>ID Result</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Nickname: <b>{html.escape(str(nickname))}</b>\n"
        f"🆔 Player ID: <code>{html.escape(str(player_id))}</code>\n"
        f"🌐 Zone ID: <code>{html.escape(str(zone_id))}</code>\n"
        f"🌍 Region: <b>{html.escape(str(region))}</b>\n"
        f"{dd_text}\n"
        "✅ <b>ID Verified</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )
    # Delete checking message after 2 seconds
    await asyncio.sleep(2)
    try:
        await loading_msg.delete()
    except Exception:
        pass
    context.user_data.clear()


async def process_order_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user = update.effective_user

    if text == "❌ Cancel Order":
        context.user_data.clear()
        await update.message.reply_text("❌ Order cancelled", reply_markup=main_keyboard())
        return

    if text == "❌ No, cancel":
        context.user_data.clear()
        await update.message.reply_text("❌ Order cancelled", reply_markup=main_keyboard())
        return

    if text == "✅ Yes, order":
        last_order = context.user_data.get("last_order_time", 0)
        if time.time() - last_order < 5:
            await update.message.reply_text("⏳ Please wait a few seconds before ordering again.")
            return
        context.user_data["last_order_time"] = time.time()

        product = context.user_data.get("selected_product", {})
        game_name = context.user_data.get("game_name", "")
        player_id = context.user_data.get("player_id")
        zone_id = context.user_data.get("zone_id")
        base_price = product.get("price", 0)
        price = get_final_price(base_price)
        pname = product.get("name", "?")
        code = product.get("code", "")

        loading_msg = await update.message.reply_text("⏳ <b>Processing...</b>", parse_mode="HTML")

        if not deduct_user_balance(user.id, price):
            await update.message.reply_text("❌ Failed to deduct balance", reply_markup=main_keyboard())
            last_order_time = context.user_data.get("last_order_time", time.time())
            context.user_data.clear()
            context.user_data["last_order_time"] = last_order_time
            return

        data, error = rme_create_order(code, player_id, zone_id)
        if error:
            try:
                await loading_msg.delete()
            except Exception:
                pass
            add_user_balance(user.id, price)
            await update.message.reply_text(
                f"❌ <b>Order Failed</b>\n\n{html.escape(str(error))}\n\n💰 Refunded",
                parse_mode="HTML", reply_markup=main_keyboard(),
            )
            last_order_time = context.user_data.get("last_order_time", time.time())
            context.user_data.clear()
            context.user_data["last_order_time"] = last_order_time
            return

        result = extract_order_result(data)
        order_id = (result.get("orderId") or result.get("order_id") or
                    result.get("id") or result.get("transactionId") or
                    result.get("transaction_id") or "-")
        status = normalize_order_status(
            result.get("status") or result.get("orderStatus") or result.get("state") or "pending"
        )

        save_order(user.id, game_name, pname, player_id, zone_id, price, status, order_id)

        new_balance = get_user_balance(user.id)

        # Order Created message ကို ပို့ပြီး message_id ကို သိမ်းထားမယ်
        created_msg = await update.message.reply_text(
            "✅ <b>Order Created</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎮 {html.escape(game_name)}\n"
            f"📦 {html.escape(pname)}\n"
            f"🆔 {html.escape(str(player_id))}\n\n"
            f"🆔 Order ID: <code>{html.escape(str(order_id))}</code>\n"
            f"⏳ Status: <b>{html.escape(str(status).upper())}</b>\n\n"
            f"💰 Balance: <b>{new_balance:,.0f} MMK</b>",
            parse_mode="HTML", reply_markup=main_keyboard(),
        )

        if status not in ("completed", "failed") and str(order_id) != "-":
            context.application.create_task(
                monitor_order_status(
                    context.application, user.id, order_id,
                    product_name=pname,
                    game_name=game_name,
                    message_id=created_msg.message_id,
                )
            )

        # Delete processing message after 2 seconds
        await asyncio.sleep(2)
        try:
            await loading_msg.delete()
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=ALERT_CHAT_ID if ALERT_CHAT_ID else ADMIN_ID,
                text=(
                    f"🔔 <b>NEW ORDER</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 {html.escape(user.first_name or 'User')}\n"
                    f"🆔 <code>{user.id}</code>\n\n"
                    f"🎮 {html.escape(game_name)}\n"
                    f"📦 {html.escape(pname)}\n"
                    f"🆔 {html.escape(str(player_id))}\n"
                    f"🌐 {html.escape(str(zone_id))}\n\n"
                    f"💰 {price:,.0f} MMK\n"
                    f"🆔 <code>{html.escape(str(order_id))}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        last_order_time = context.user_data.get("last_order_time", time.time())
        context.user_data.clear()
        context.user_data["last_order_time"] = last_order_time
        return


# ============================================================
# Part 5 — Access Control + Admin + Text Handler + Main
# ============================================================

# ---------- Access Control ----------
def get_access_status(user_id):
    if int(user_id) == ADMIN_ID:
        return "approved"
    conn = sqlite3.connect(ACCESS_DB)
    row = conn.execute("SELECT status FROM bot_access WHERE user_id=?", (int(user_id),)).fetchone()
    conn.close()
    return row[0] if row else None


def save_access_request(user_id, username=None, first_name=None):
    conn = sqlite3.connect(ACCESS_DB)
    conn.execute("""INSERT INTO bot_access (user_id, status, requested_at)
        VALUES (?, 'pending', CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
        status='pending', requested_at=CURRENT_TIMESTAMP
    """, (int(user_id),))
    conn.commit()
    conn.close()

    # Save user info into users table
    conn = sqlite3.connect(USER_DB)
    conn.execute("""INSERT INTO users (user_id, username, first_name, balance, created_at)
        VALUES (?, ?, ?, 0.0, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
        username = COALESCE(excluded.username, users.username),
        first_name = COALESCE(excluded.first_name, users.first_name)
    """, (int(user_id), username, first_name))
    conn.commit()
    conn.close()


def set_access_status(user_id, status):
    conn = sqlite3.connect(ACCESS_DB)
    conn.execute("""INSERT INTO bot_access (user_id, status, requested_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET status=?
    """, (int(user_id), status, status))
    conn.commit()
    conn.close()


def access_request_keyboard(user_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"access:approve:{user_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"access:reject:{user_id}"),
    ]])


async def request_access(update, context, force_request=False):
    user = update.effective_user
    if not user:
        return False
    if user.id == ADMIN_ID:
        return True

    status = get_access_status(user.id)

    if status is None or (force_request and status in ("pending", "rejected")):
        save_access_request(user.id, user.username, user.first_name)
        username = f"@{user.username}" if user.username else "-"
        admin_text = (
            "🔔 <b>NEW USER REGISTER REQUEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Name: <b>{html.escape(user.first_name or '-')}</b>\n"
            f"🔗 Username: <b>{html.escape(username)}</b>\n"
            f"🆔 User ID: <code>{user.id}</code>\n\n"
            "⚠️ Grant access to this user?"
        )
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML",
                reply_markup=access_request_keyboard(user.id),
            )
        except Exception as e:
            print("ACCESS REQUEST SEND ERROR:", e)

    if status == "rejected" and not force_request:
        if update.message:
            await update.message.reply_text(
                "❌ <b>Access Denied</b>\n\nPress /start to send a new request.",
                parse_mode="HTML",
            )
        return False

    if status == "pending":
        if update.message:
            await update.message.reply_text(
                "⏳ <b>Access Pending</b>\n\nRequest sent to Admin.",
                parse_mode="HTML",
            )
        return False

    if status == "approved":
        return True

    return False


async def handle_access_callback(update, context):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return True

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        return True

    action, uid_text = parts[1], parts[2]
    try:
        user_id = int(uid_text)
    except ValueError:
        return True

    if action == "approve":
        status = "approved"
        label = "✅ APPROVED"
    elif action == "reject":
        status = "rejected"
        label = "❌ REJECTED"
    else:
        return True

    set_access_status(user_id, status)

    try:
        await query.edit_message_text((query.message.text or "") + f"\n\n<b>{label}</b>", parse_mode="HTML")
    except Exception:
        pass

    if status == "approved":
        # Save user info when approved
        try:
            chat = await context.bot.get_chat(user_id)
            conn = sqlite3.connect(USER_DB)
            conn.execute("""INSERT INTO users (user_id, username, first_name, balance, created_at)
                VALUES (?, ?, ?, 0.0, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name)
            """, (int(user_id), chat.username, chat.first_name))
            conn.commit()
            conn.close()
        except Exception as e:
            print("SAVE USER INFO ERROR:", e)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ <b>Access Approved!</b>\n\nPress /start to begin.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await query.answer("Approved ✅")
    else:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ <b>Access Denied</b>\n\nPress /start to send a new request.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await query.answer("Rejected ❌")

    return True


# ---------- Admin Commands ----------
async def maintenance_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    set_maintenance(True)
    await update.message.reply_text(
        "🛠️ <b>Maintenance Mode ON</b>\n\n"
        "Orders and Deposits are now temporarily disabled for all users.",
        parse_mode="HTML"
    )

async def maintenance_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    set_maintenance(False)
    await update.message.reply_text(
        "✅ <b>Maintenance Mode OFF</b>\n\n"
        "The bot is now back to normal operation.",
        parse_mode="HTML"
    )


async def add_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("❌ <code>/addbalance USER_ID AMOUNT</code>", parse_mode="HTML")
        return
    try:
        user_id = int(args[0])
        amount = float(args[1])
    except ValueError:
        await update.message.reply_text("❌ Must be a number")
        return
    add_user_balance(user_id, amount)
    new_bal = get_user_balance(user_id)
    await update.message.reply_text(
        f"✅ Balance added\n\n🆔 {user_id}\n💰 +{amount:,.0f} MMK\n💰 New: <b>{new_bal:,.0f} MMK</b>",
        parse_mode="HTML",
    )


async def deduct_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("❌ <code>/deductbalance USER_ID AMOUNT</code>", parse_mode="HTML")
        return
    try:
        user_id = int(args[0])
        amount = float(args[1])
    except ValueError:
        await update.message.reply_text("❌ Must be a number")
        return
    if deduct_user_balance(user_id, amount):
        new_bal = get_user_balance(user_id)
        await update.message.reply_text(
            f"✅ Balance deducted\n\n🆔 {user_id}\n💰 -{amount:,.0f} MMK\n💰 New: <b>{new_bal:,.0f} MMK</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("❌ Insufficient balance")


async def list_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""SELECT user_id, username, first_name, balance, created_at
        FROM users ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No users yet.")
        return

    total = len(rows)
    total_balance = sum(r[3] for r in rows)

    lines = [
        "👥 <b>All Users</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📊 Total: <b>{total}</b>",
        f"💰 Total Balance: <b>{total_balance:,.0f} MMK</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for uid, uname, fname, balance, created_at in rows:
        uname_text = f"@{uname}" if uname else "-"
        fname_text = html.escape(fname or "-")
        lines.append(f"👤 <b>{fname_text}</b>")
        lines.append(f"   🔗 {uname_text}")
        lines.append(f"   🆔 <code>{uid}</code>")
        lines.append(f"   💰 <b>{balance:,.0f} MMK</b>")
        lines.append("")

    text = "\n".join(lines)

    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


async def all_orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""
        SELECT o.id, o.user_id, u.first_name, u.username, o.game, o.product, o.price, o.status, o.transaction_id, o.created_at
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.user_id
        ORDER BY o.created_at DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 No orders found.", parse_mode="HTML")
        return

    lines = ["📦 <b>All Orders (Last 20)</b>", "━━━━━━━━━━━━━━━━━━━━", ""]

    for o_id, uid, fname, uname, game, product, price, status, txn_id, created_at in rows:
        norm = normalize_order_status(status)
        status_emoji = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(norm, "❓")

        # ရက်စွဲကို မြန်မာချိန်ပြောင်းမယ်
        date_str = format_mm_time(created_at)

        lines.append(f"{status_emoji} <b>{html.escape(str(product))}</b> ({html.escape(str(game))})")
        lines.append(f"👤 {html.escape(fname or 'User')} (@{html.escape(uname or '-')})")
        lines.append(f"🆔 <code>{html.escape(str(txn_id))}</code>")
        lines.append(f"💰 {float(price or 0):,.0f} MMK")
        lines.append(f"📅 {html.escape(str(date_str))}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    text = "\n".join(lines)

    # စာသား အရမ်းရှည်သွားရင် ခွဲပို့မယ်
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=admin_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_keyboard())


async def all_deposits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""
        SELECT d.id, d.user_id, u.first_name, u.username, d.amount, d.method, d.status, d.created_at
        FROM deposit_requests d
        LEFT JOIN users u ON d.user_id = u.user_id
        ORDER BY d.created_at DESC LIMIT 20
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 <b>No deposit history found.</b>",
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )
        return

    status_labels = {
        "completed": "Completed",
        "pending": "Pending",
        "failed": "Failed",
    }
    status_emojis = {
        "completed": "✅",
        "pending": "⏳",
        "failed": "❌",
    }

    lines = ["💳 <b>All Deposits</b>", "<i>Latest 20 transactions</i>", ""]

    for _d_id, _uid, fname, uname, amount, method, status, created_at in rows:
        raw_status = str(status or "pending").strip().lower()
        if raw_status == "approved":
            norm = "completed"
        elif raw_status in ("rejected", "cancelled", "canceled"):
            norm = "failed"
        else:
            norm = normalize_order_status(raw_status)

        status_emoji = status_emojis.get(norm, "❔")
        status_label = status_labels.get(norm, norm.replace("_", " ").title())

        # USDT deposit amounts are stored in MMK equivalent in the database.
        if str(method or "").strip().upper() == "USDT":
            amount_value = f"${get_usd_price(amount):,.2f} USDT"
        else:
            amount_value = f"{float(amount or 0):,.0f} MMK"

        # Show a plus sign only when the deposit was approved.
        if norm == "completed":
            amount_value = f"+{amount_value}"

        display_name = html.escape(str(fname or "User"))
        username = f"@{html.escape(str(uname))}" if uname else ""
        user_line = f"👤 <b>{display_name}</b>"
        if username:
            user_line += f" · {username}"

        date_str = html.escape(str(format_mm_time(created_at)))

        lines.append(
            f"{status_emoji} <b>{html.escape(amount_value)}</b> "
            f"<i>{html.escape(status_label)}</i>"
        )
        lines.append(user_line)
        lines.append(f"📅 {date_str}")
        lines.append("")

    message_text = "\n".join(lines).rstrip()

    # Telegram message limit safety: send the list in multiple messages if needed.
    if len(message_text) <= 4000:
        await update.message.reply_text(
            message_text,
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )
    else:
        chunks = []
        current_chunk = ""
        for entry in lines:
            candidate = f"{current_chunk}\n{entry}" if current_chunk else entry
            if len(candidate) > 3900 and current_chunk:
                chunks.append(current_chunk)
                current_chunk = entry
            else:
                current_chunk = candidate
        if current_chunk:
            chunks.append(current_chunk)

        for chunk in chunks:
            await update.message.reply_text(
                chunk,
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )


async def check_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ <code>/checkbalance USER_ID</code>", parse_mode="HTML")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID must be numeric")
        return
    balance = get_user_balance(user_id)
    await update.message.reply_text(
        f"💰 <b>User Balance</b>\n\n🆔 <code>{user_id}</code>\n💰 <b>{balance:,.0f} MMK</b>",
        parse_mode="HTML",
    )


async def pending_deposits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("""
        SELECT d.id, d.user_id, u.first_name, u.username, d.amount, d.method, d.created_at
        FROM deposit_requests d
        LEFT JOIN users u ON d.user_id = u.user_id
        WHERE d.status = 'pending'
        ORDER BY d.created_at ASC
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "✅ <b>No Pending Deposits</b>\n\nAll deposit requests have been processed.",
            parse_mode="HTML",
        )
        return

    lines = [f"⏳ <b>Pending Deposits</b> ({len(rows)} requests)", "━━━━━━━━━━━━━━━━━━━━", ""]
    for req_id, uid, fname, uname, amount, method, created_at in rows:
        fname_text = html.escape(fname or "Unknown")
        uname_text = html.escape(f"@{uname}" if uname else "-")
        if method == "USDT":
            amount_text = f"💲 ${get_usd_price(amount):,.2f}"
        else:
            amount_text = f"💵 {float(amount or 0):,.0f} MMK"
        lines.append(f"🆔 Request ID: <code>{req_id}</code>")
        lines.append(f"👤 {fname_text} ({uname_text})")
        lines.append(f"📌 User ID: <code>{uid}</code>")
        lines.append(f"💰 Amount: <b>{html.escape(amount_text)}</b>")
        lines.append(f"🪙 Method: <b>{html.escape(str(method or '-'))}</b>")
        lines.append(f"📅 Time: {html.escape(format_mm_time(created_at))}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Split on record boundaries so HTML tags are not cut in the middle.
    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > 3900 and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="HTML")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    message = update.message
    
    # If no text, photo, or sticker is provided, show usage instructions
    if not message.text and not message.photo and not message.sticker:
        await message.reply_text(
            "📢 <b>Broadcast Message</b>\n\n"
            "Usage:\n"
            "• To send text: <code>/broadcast Your message</code>\n"
            "• To send a photo: Send a photo with <code>/broadcast</code> in the caption\n"
            "• To send a sticker: Send a sticker with <code>/broadcast</code>",
            parse_mode="HTML",
        )
        return

    # 🎯 ပုံအောက်က Caption ထဲက /broadcast စာသားကို ဖျက်မယ်
    if message.caption:
        message_text = message.caption.replace("/broadcast", "").strip()
    elif message.text:
        message_text = message.text.replace("/broadcast", "").strip()
    else:
        message_text = ""
    safe_message = html.escape(message_text)

    conn = sqlite3.connect(USER_DB)
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()

    if not rows:
        await message.reply_text("❌ No users found.")
        return

    total, success, failed = len(rows), 0, 0
    status_msg = await message.reply_text(f"⏳ Sending broadcast to {total} users...")
    
    for (uid,) in rows:
        try:
            # If it's a photo, send photo
            if message.photo:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=message.photo[-1].file_id,
                    caption=(
                        "📢 <b>Announcement</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{safe_message}\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🛒 Eren's Shop Bot"
                    ),
                    parse_mode="HTML",
                )
            # If it's a sticker, send sticker
            elif message.sticker:
                await context.bot.send_sticker(chat_id=uid, sticker=message.sticker.file_id)
                # Send text after sticker if provided (Optional)
                if message_text:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=f"📢 <b>Announcement</b>\n\n{safe_message}\n\n🛒 Eren's Shop Bot",
                        parse_mode="HTML",
                    )
            # If it's just text, send text only
            else:
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        "📢 <b>Announcement</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{safe_message}\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🛒 Eren's Shop Bot"
                    ),
                    parse_mode="HTML",
                )
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete</b>\n\n"
        f"📊 Total: <b>{total}</b>\n"
        f"✅ Sent: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode="HTML",
    )


async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    args = context.args
    if len(args) != 1:
        await update.message.reply_text("❌ <code>/userinfo USER_ID</code>", parse_mode="HTML")
        return

    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID must be numeric")
        return

    conn = sqlite3.connect(USER_DB)
    user_row = conn.execute(
        "SELECT username, first_name, balance, created_at FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not user_row:
        conn.close()
        await update.message.reply_text("❌ User not found in database.")
        return

    username, first_name, balance, created_at = user_row
    order_count, total_spent = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders WHERE user_id=?",
        (user_id,),
    ).fetchone()
    deposit_count, total_deposited = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM deposit_requests WHERE user_id=? AND status='approved'",
        (user_id,),
    ).fetchone()
    conn.close()

    # ရက်စွဲကို မြန်မာစံတော်ချိန်ပြောင်းမယ်
    joined_date = format_mm_time(created_at)
    username_text = html.escape(f"@{username}" if username else "-")
    usd_balance = get_usd_price(balance or 0)

    await update.message.reply_text(
        "👤 <b>User Information</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 Name: <b>{html.escape(first_name or '-')}</b>\n"
        f"🔗 Username: {username_text}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📅 Joined: {html.escape(joined_date)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💰 <b>Wallet</b>\n"
        f"💰 Balance: <b>{float(balance or 0):,.0f} MMK</b>\n"
        f"💲 USD: <b>{usd_balance:,.2f}$</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>Activity</b>\n"
        f"📦 Total Orders: <b>{order_count}</b>\n"
        f"💸 Total Spent: <b>{float(total_spent or 0):,.0f} MMK</b>\n"
        f"💳 Total Deposits: <b>{deposit_count}</b>\n"
        f"💵 Total Deposited: <b>{float(total_deposited or 0):,.0f} MMK</b>",
        parse_mode="HTML",
    )


# ---------- Text Handler ----------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    # 🎯 Command တွေ (/start, /broadcast စသဖြင့်) ကို ဒီကနေ ကျော်သွားခိုင်းတာ
    if text.startswith("/"):
        return

    state = context.user_data.get("state")

    # Check Maintenance Mode (Only for Users, Admin can bypass)
    if is_maintenance() and user.id != ADMIN_ID:
        allowed_while_maintenance = (
            "🏠 Main Menu", "👤 My Profile", "💰 Balance", "📋 Orders",
            "💳 Deposit History", "📞 Contact Admin", "⬅️ Back to Menu",
            "🏠 Back to Menu", "❌ Cancel Chat", "📦 My Orders",
            "💰 My Balance"
        )

        blocked_actions = (
            "💵 Deposit", "💳 Deposit", "✅ Yes, order",
            "🇲🇲 MMK (Kpay/UAB/AYA)", "🪙 USDT (BEP20)",
        )

        products = context.user_data.get("products", [])
        is_product_click = False
        for p in products:
            pname = p.get("name", "?")
            base_price = p.get("price", 0)
            price = get_final_price(base_price)
            label = f"{pname} — {price:,.0f} Ks (${get_usd_price(price):,.2f})"
            if text == label:
                is_product_click = True
                break

        if text in blocked_actions or is_product_click or state in (
            "deposit_currency", "deposit_amount", "deposit_screenshot",
            "awaiting_player_id", "awaiting_zone_id", "awaiting_confirm"
        ):
            await update.message.reply_text(
                "🛠️ <b>Maintenance Mode</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "The bot is currently under maintenance.\n"
                "Orders and Deposits are temporarily disabled.\n\n"
                "Please wait and try again later.",
                parse_mode="HTML",
                reply_markup=main_keyboard()
            )
            return

    # Anti-spam cooldown for non-admin menu/product actions. Do not throttle
    # replies inside active multi-step flows (deposit, ID check, order, or live chat).
    active_states = {
        "awaiting_chat_message", "deposit_currency", "deposit_amount", "deposit_screenshot",
        "check_id_game", "check_id_player", "check_id_zone", "awaiting_player_id",
        "awaiting_zone_id", "awaiting_telegram_username", "awaiting_confirm",
    }
    if user.id != ADMIN_ID and state not in active_states:
        now = time.time()
        last_action = context.user_data.get("last_action_time", 0)
        if now - last_action < 2:
            return
        context.user_data["last_action_time"] = now

    # Live Chat state is handled before menu/product matching.
    if text == "❌ Cancel Chat" and state == "awaiting_chat_message":
        context.user_data.clear()
        await start(update, context)
        return

    if state == "awaiting_chat_message":
        try:
            username = f"@{user.username}" if user.username else "-"
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "💬 <b>NEW MESSAGE FROM USER</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👤 Name: <b>{user.first_name or '-'}</b>\n"
                    f"🔗 Username: {username}\n"
                    f"🆔 User ID: <code>{user.id}</code>\n\n"
                    f"📝 Message:\n{text}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"↩️ <i>Reply to this message to answer {user.first_name}.</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print("CHAT SEND ERROR:", e)
            await update.message.reply_text("❌ Could not send your message. Please try again later.")
            return

        sent_msg = await update.message.reply_text("✅ <b>Message Sent To Owner</b>", parse_mode="HTML")
        await asyncio.sleep(3)
        try:
            await sent_msg.delete()
        except Exception:
            pass
        context.user_data.clear()
        await update.message.reply_text("🏠 Main Menu", reply_markup=main_keyboard())
        return

    if text == "🏠 Main Menu":
        context.user_data.clear()
        await start(update, context)
        return

    if text == "⬅️ Back to Games":
        game_name = context.user_data.get("game_name", "")
        if "Mobile Legends" in game_name or "ML SG/MY" in game_name:
            context.user_data["choosing_ml_server"] = True
            keyboard = ReplyKeyboardMarkup(
                [["🎮 Mobile Legends"], ["🌍 Mobile Legends SG/MY"], ["🏠 Main Menu"]],
                resize_keyboard=True
            )
            await update.message.reply_text(
                "💎 <b>Mobile Legends</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Choose a server:\n\n"
                "• <b>Mobile Legends</b> (Global)\n"
                "• <b>Mobile Legends SG/MY</b>",
                parse_mode="HTML", reply_markup=keyboard,
            )
        elif "PUBG" in game_name:
            keyboard = ReplyKeyboardMarkup(
                [["🎮 PUBG Package", "🎮 PUBG UC"], ["🏠 Main Menu"]], resize_keyboard=True
            )
            await update.message.reply_text(
                "🎮 <b>PUBG</b>\n━━━━━━━━━━━━━━━━━━━━\n\nChoose a category:",
                parse_mode="HTML", reply_markup=keyboard,
            )
        elif "Telegram" in game_name:
            await start(update, context)
        else:
            await start(update, context)
        return

    if text == "👤 My Profile":
        await show_profile(update, context)
        return

    if text == "⚙️ Admin Panel" and user.id == ADMIN_ID:
        await update.message.reply_text(
            "👑 <b>Admin Panel</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "👇 Choose an option below",
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )
        return

    if user.id == ADMIN_ID:
        if text == "⏳ Pending Deposits":
            await pending_deposits_cmd(update, context)
            return

        if text == "📊 All Users":
            await list_users_cmd(update, context)
            return

        if text == "📦 All Orders":
            await all_orders_cmd(update, context)
            return

        if text == "💳 All Deposits":
            await all_deposits_cmd(update, context)
            return

        if text == "📢 Broadcast":
            await update.message.reply_text(
                "📢 <b>Broadcast Message</b>\n\n"
                "Please type the command below:\n\n"
                "<code>/broadcast Your message here...</code>\n\n"
                "Example:\n"
                "<code>/broadcast Flash Sale 10% Today!</code>",
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )
            return

        if text == "👤 User Info":
            await update.message.reply_text(
                "👤 <b>User Info</b>\n\n"
                "Please type the command below:\n\n"
                "<code>/userinfo USER_ID</code>\n\n"
                "Example:\n"
                "<code>/userinfo 5698123475</code>",
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )
            return

        if text == "⌨️ Commands Guide":
            await update.message.reply_text(
                "⌨️ <b>Admin Commands Guide</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "💰 <b>Balance</b>\n"
                "• <code>/addbalance ID AMOUNT</code>\n"
                "• <code>/deductbalance ID AMOUNT</code>\n"
                "• <code>/checkbalance ID</code>\n\n"
                "👤 <b>User</b>\n"
                "• <code>/userinfo ID</code>\n"
                "• <code>/listusers</code>\n\n"
                "📢 <b>Others</b>\n"
                "• <code>/broadcast MESSAGE</code>\n"
                "• <code>/pending</code>",
                parse_mode="HTML",
                reply_markup=admin_keyboard(),
            )
            return

        if text == "🔙 Back to Main Menu":
            context.user_data.clear()
            await start(update, context)
            return

    if text == "💰 Balance":
        user = update.effective_user
        balance = get_user_balance(user.id)
        username = f"@{user.username}" if user.username else "-"

        # ✅ Role စစ်မယ်
        role = "👑 Admin" if user.id == ADMIN_ID else "👤 User"

        usd_balance = get_usd_price(balance)

        await update.message.reply_text(
            "💰 <b>Wallet Balance</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Username: <b>{html.escape(username)}</b>\n"
            f"📛 Role: {role}\n\n"
            f"💰 Balance : <b>{balance:,.0f} MMK</b>\n"
            f"💲 USD : <b>{usd_balance:,.2f}$</b>\n\n"
            "👇 Choose an option below",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                [["💵 Deposit", "📋 Orders"], ["💳 Deposit History", "⬅️ Back to Menu"]],
                resize_keyboard=True,
            ),
        )
        return
    if text == "📋 Orders":
        await show_orders(update, context)
        return

    if text == "💳 Deposit History":
        await show_deposit_history(update, context)
        return

    if text in ("💵 Deposit", "💳 Deposit"):
        keyboard = ReplyKeyboardMarkup(
            [["🇲🇲 MMK (Kpay/UAB/AYA)", "🪙 USDT (BEP20)"], ["❌ Cancel Deposit"]],
            resize_keyboard=True,
        )
        context.user_data["state"] = "deposit_currency"
        await update.message.reply_text(
            "💵 <b>Choose Deposit Currency</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Please select how you want to deposit:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if text in ("⬅️ Back to Menu", "🏠 Back to Menu"):
        context.user_data.clear()
        await start(update, context)
        return

    if text == "💰 My Balance":
        await show_balance(update, context)
        return

    if text == "📦 My Orders":
        await show_orders(update, context)
        return

    if text == "💎 Mobile Legends":
        if context.user_data.get("choosing_ml_server"):
            context.user_data.pop("choosing_ml_server", None)
            await show_game_products(update, context, "mobile-legends", "Mobile Legends")
            return
        context.user_data["choosing_ml_server"] = True
        keyboard = ReplyKeyboardMarkup(
            [["🎮 Mobile Legends"], ["🌍 Mobile Legends SG/MY"], ["🏠 Main Menu"]],
            resize_keyboard=True
        )
        await update.message.reply_text(
            "💎 <b>Mobile Legends</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose a server:\n\n"
            "• <b>Mobile Legends</b> (Global)\n"
            "• <b>Mobile Legends SG/MY</b>",
            parse_mode="HTML", reply_markup=keyboard,
        )
        return

    if text == "🌍 Mobile Legends SG/MY":
        context.user_data.pop("choosing_ml_server", None)
        await show_game_products(update, context, "mobile-legends-singapore", "Mobile Legends SG/MY")
        return

    if text == "🎮 Mobile Legends":
        context.user_data.pop("choosing_ml_server", None)
        await show_game_products(update, context, "mobile-legends", "Mobile Legends")
        return

    if text == "🔥 Blood Strike":
        await show_game_products(update, context, "blood-strike", "Blood Strike")
        return
    if text == "🛡️ Arena Breakout":
        await show_game_products(update, context, "arena-brekout", "Arena Breakout")
        return
    if text == "💻 Capcut":
        await show_game_products(update, context, "capcut", "Capcut")
        return
    if text == "✈️ Telegram":
        await show_game_products(update, context, "telegram-star", "Telegram")
        return
    if text == "✨ Gemini":
        await show_game_products(update, context, "gemini", "Gemini")
        return

    if text == "🎮 PUBG":
        keyboard = ReplyKeyboardMarkup(
            [
                ["🎮 PUBG Package", "🎮 PUBG UC"],
                ["🏠 Main Menu"],
            ],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "🎮 <b>PUBG</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Choose a category:\n\n"
            "• <b>PUBG Package</b>\n"
            "• <b>PUBG UC</b>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if text == "🎮 PUBG Package":
        await show_game_products(update, context, "pubg", "PUBG Package", filter_type="package")
        return

    if text == "🎮 PUBG UC":
        await show_game_products(update, context, "pubg-uc", "PUBG UC")
        return

    if text == "🔍 Check ID":
        await show_check_id(update, context)
        return

    if text == "🏆 Top Spenders":
        await show_top_spenders(update, context)
        return

    if text in ("💵 Deposit", "💳 Deposit"):
        keyboard = ReplyKeyboardMarkup(
            [["🇲🇲 MMK (Kpay/UAB/AYA)", "🪙 USDT (BEP20)"], ["❌ Cancel Deposit"]],
            resize_keyboard=True,
        )
        context.user_data["state"] = "deposit_currency"
        await update.message.reply_text(
            "💵 <b>Choose Deposit Currency</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Please select how you want to deposit:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    if text == "📞 Contact Admin":
        context.user_data["state"] = "awaiting_chat_message"
        await update.message.reply_text(
            "💬 <b>Live Chat with Admin</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Please type your message below. Admin will reply as soon as possible.\n\n"
            "⚠️ Please do not send spam or irrelevant messages.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([["❌ Cancel Chat"]], resize_keyboard=True),
        )
        return

    if text == "❌ Cancel Order":
        context.user_data.clear()
        await start(update, context)
        return

    products = context.user_data.get("products", [])
    if products:
        for p in products:
            pname = p.get("name", "?")
            base_price = p.get("price", 0)
            price = get_final_price(base_price)
            stock = p.get("stock")
            label = f"{pname} — {price:,.0f} Ks (${get_usd_price(price):,.2f})"
            if stock is not None:
                label += f" | Stock: {stock}"
            if text == label:
                if stock is not None:
                    try:
                        if float(stock) <= 0:
                            await update.message.reply_text("❌ Out of Stock")
                            return
                    except (TypeError, ValueError):
                        pass
                # Check wallet balance before allowing product selection to continue.
                user_balance = get_user_balance(user.id)
                if user_balance < price:
                    await update.message.reply_text(
                        f"❌ <b>Insufficient Balance</b>\n\n"
                        f"💰 Your Balance: <b>{user_balance:,.0f} MMK</b>\n"
                        f"💰 Required: <b>{price:,.0f} MMK</b>\n\n"
                        "💡 Please top-up your wallet first.",
                        parse_mode="HTML",
                        reply_markup=main_keyboard()
                    )
                    return

                context.user_data["selected_product"] = p
                game_slug = context.user_data.get("game_slug", "")
                if game_slug in ("capcut", "gemini"):
                    context.user_data["player_id"] = "N/A"
                    context.user_data["zone_id"] = "0"
                    context.user_data["state"] = "awaiting_confirm"
                    keyboard = ReplyKeyboardMarkup([["✅ Yes, order", "❌ No, cancel"]], resize_keyboard=True)
                    await update.message.reply_text(
                        "🛒 <b>Order Confirm</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"🎮 {html.escape(context.user_data.get('game_name', ''))}\n"
                        f"📦 {html.escape(pname)}\n\n💰 Price: <b>{price:,.0f} Ks</b>\n\nConfirm order?",
                        parse_mode="HTML", reply_markup=keyboard)
                    return
                if game_slug in ("telegram-star", "telegram-premium"):
                    context.user_data["state"] = "awaiting_telegram_username"
                    await update.message.reply_text(
                        "🛒 <b>Order</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"🎮 {html.escape(context.user_data.get('game_name', ''))}\n"
                        f"📦 {html.escape(pname)}\n💰 {price:,.0f} Ks\n\n"
                        "👤 Enter <b>Telegram Username</b> (e.g. @username)",
                        parse_mode="HTML", reply_markup=cancel_keyboard())
                    return
                context.user_data["state"] = "awaiting_player_id"
                await update.message.reply_text(
                    f"🛒 <b>Order</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🎮 {html.escape(context.user_data.get('game_name', ''))}\n"
                    f"📦 {html.escape(pname)}\n💰 {price:,.0f} Ks\n\n🆔 Enter <b>Player ID</b>",
                    parse_mode="HTML", reply_markup=cancel_keyboard())
                return

    state = context.user_data.get("state")

    if text == "❌ Cancel Deposit" and state in ("deposit_currency", "deposit_amount", "deposit_screenshot"):
        context.user_data.clear()
        await start(update, context)
        return

    if state == "deposit_currency":
        if text == "🇲🇲 MMK (Kpay/UAB/AYA)":
            context.user_data["deposit_currency"] = "MMK"
            context.user_data["state"] = "deposit_amount"
            await update.message.reply_text(
                "💰 <b>Deposit (MMK)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Enter the amount you want to deposit:\n"
                "(Min: 1,000 MMK)",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["❌ Cancel Deposit"]], resize_keyboard=True),
            )
            return
        elif text == "🪙 USDT (BEP20)":
            context.user_data["deposit_currency"] = "USDT"
            context.user_data["state"] = "deposit_amount"
            await update.message.reply_text(
                "🪙 <b>Deposit (USDT - BEP20)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Enter the amount in USD ($):\n"
                "(Min: 2$)",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup([["❌ Cancel Deposit"]], resize_keyboard=True),
            )
            return

    if state == "deposit_amount":
        currency = context.user_data.get("deposit_currency", "MMK")

        if not text.replace('.', '', 1).isdigit():
            await update.message.reply_text("❌ Enter a valid number.")
            return

        amount = float(text)

        if currency == "MMK":
            if amount < 1000:
                await update.message.reply_text("❌ Minimum deposit is 1,000 MMK.")
                return
            mmk_amount = amount
            usd_amount = get_usd_price(amount)
            payment_info = (
                "💙 <b>K Pay</b>\n"
                "   <code>09988579638</code> (Thet Naing Swan)\n\n"
                "💚 <b>UAB Pay</b>\n"
                "   <code>09425160424</code> (Thet Naing Swan)\n\n"
                "❤️ <b>AYA Pay</b>\n"
                "   <code>09678664100</code> (Hnin Hnin Soe)\n\n"
            )
            amount_text = f"💵 Amount: <b>{mmk_amount:,.0f} MMK</b>"
        else:
            if amount < 2:
                await update.message.reply_text("❌ Minimum deposit is 2$.")
                return
            usd_amount = amount
            mmk_amount = amount * USD_RATE
            payment_info = (
                "🪙 <b>USDT (BEP20)</b>\n"
                "   <code>0xd4fdee45299d8f345a2a2084427177d98ea25f85</code>\n"
                "   (Network Fee: 0.01$ | Min: 2$)\n\n"
            )
            amount_text = f"💲 Amount: <b>{usd_amount:,.2f}$</b>"

        context.user_data["deposit_amount"] = mmk_amount  # Store database amount in MMK

        await update.message.reply_text(
            f"💳 <b>Deposit / Add Balance</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{payment_info}"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{amount_text}\n\n"
            "📸 After transferring, send the screenshot to this chat.\n\n"
            "⚠️ Admin will review and add balance.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([["❌ Cancel Deposit"]], resize_keyboard=True),
        )
        context.user_data["state"] = "deposit_screenshot"
        return

    if state == "deposit_screenshot" and text == "❌ Cancel Deposit":
        context.user_data.clear()
        await start(update, context)
        return

    if state == "check_id_game":
        slug = None
        if text.lower() == "ml":
            slug = "mobile-legends"
        elif text.lower() == "pubg":
            slug = "pubg"
        else:
            await update.message.reply_text("❌ Type <code>ml</code> or <code>pubg</code>", parse_mode="HTML")
            return
        context.user_data["check_slug"] = slug
        context.user_data["state"] = "check_id_player"
        await update.message.reply_text(
            "🆔 Enter <b>Player ID</b>",
            parse_mode="HTML", reply_markup=cancel_keyboard(),
        )
        return

    if state == "check_id_player":
        if not text.isdigit():
            await update.message.reply_text("❌ Player ID must be numeric")
            return
        context.user_data["check_player_id"] = text
        context.user_data["state"] = "check_id_zone"
        await update.message.reply_text(
            "🌐 Enter <b>Zone ID</b> (PUBG: type 0)",
            parse_mode="HTML", reply_markup=cancel_keyboard(),
        )
        return

    if state == "check_id_zone":
        if not text.isdigit():
            await update.message.reply_text("❌ Zone ID must be numeric")
            return
        await process_check_id_zone(update, context, text)
        return

    if state == "awaiting_telegram_username":
        username = text.strip()
        if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", username):
            await update.message.reply_text("❌ Enter a valid Telegram username starting with @ (e.g. @username)")
            return
        context.user_data["player_id"] = username
        context.user_data["zone_id"] = "0"
        product = context.user_data.get("selected_product", {})
        price = get_final_price(product.get("price", 0))
        balance = get_user_balance(user.id)
        if balance < price:
            await update.message.reply_text(f"❌ <b>Insufficient Balance</b>\n\n💰 Balance: <b>{balance:,.0f} MMK</b>\n💰 Required: <b>{price:,.0f} MMK</b>", parse_mode="HTML", reply_markup=main_keyboard())
            context.user_data.clear()
            return
        keyboard = ReplyKeyboardMarkup([["✅ Yes, order", "❌ No, cancel"]], resize_keyboard=True)
        await update.message.reply_text(
            "🛒 <b>Order Confirm</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎮 {html.escape(context.user_data.get('game_name', ''))}\n"
            f"📦 {html.escape(str(product.get('name', '?')))}\n"
            f"👤 Telegram: <b>{html.escape(username)}</b>\n\n💰 Price: <b>{price:,.0f} Ks</b>\n\nConfirm order?",
            parse_mode="HTML", reply_markup=keyboard)
        context.user_data["state"] = "awaiting_confirm"
        return

    if state == "awaiting_player_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Player ID must be numeric")
            return
        context.user_data["player_id"] = text
        context.user_data["state"] = "awaiting_zone_id"
        await update.message.reply_text(
            "🌐 Enter <b>Zone ID</b> (PUBG: type 0)",
            parse_mode="HTML", reply_markup=cancel_keyboard(),
        )
        return

    if state == "awaiting_zone_id":
        if not text.isdigit():
            await update.message.reply_text("❌ Zone ID must be numeric")
            return
        context.user_data["zone_id"] = text

        slug = context.user_data.get("game_slug")
        player_id = context.user_data.get("player_id")
        product = context.user_data.get("selected_product", {})
        base_price = product.get("price", 0)
        price = get_final_price(base_price)
        pname = product.get("name", "?")

        loading_msg = await update.message.reply_text("🔍 <b>Checking...</b>", parse_mode="HTML")

        data, error = rme_check_player(slug, player_id, text)
        nickname = "-"
        if not error and data:
            nickname = data.get("nickname") or data.get("username") or data.get("name") or "-"

        balance = get_user_balance(user.id)
        if balance < price:
            await update.message.reply_text(
                f"❌ <b>Insufficient Balance</b>\n\n"
                f"💰 Balance: <b>{balance:,.0f} MMK</b>\n"
                f"💰 Required: <b>{price:,.0f} MMK</b>",
                parse_mode="HTML", reply_markup=main_keyboard(),
            )
            # Delete checking message after 2 seconds
            await asyncio.sleep(2)
            try:
                await loading_msg.delete()
            except Exception:
                pass
            context.user_data.clear()
            return

        keyboard = ReplyKeyboardMarkup(
            [["✅ Yes, order", "❌ No, cancel"]],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            "🛒 <b>Order Confirm</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎮 {html.escape(context.user_data.get('game_name', ''))}\n"
            f"📦 {html.escape(pname)}\n"
            f"🆔 Player ID: {html.escape(player_id)}\n"
            f"🌐 Zone ID: {html.escape(text)}\n"
            f"👤 In-Game: <b>{html.escape(nickname)}</b>\n\n"
            f"💰 Price: <b>{price:,.0f} Ks</b>\n\n"
            "Confirm order?",
            parse_mode="HTML", reply_markup=keyboard,
        )
        # Delete checking message after 2 seconds
        await asyncio.sleep(2)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        context.user_data["state"] = "awaiting_confirm"
        return

    if state == "awaiting_confirm":
        await process_order_confirm(update, context, text)
        return

    await update.message.reply_text("❓ Please use the menu", reply_markup=main_keyboard())


# ---------- Live Chat Media Handlers ----------
async def chat_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = context.user_data.get("state")

    if state != "awaiting_chat_message" or not update.message or not update.message.photo:
        return

    photo_id = update.message.photo[-1].file_id
    caption_text = update.message.caption or ""

    try:
        username = f"@{user.username}" if user.username else "-"
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=(
                "💬 <b>PHOTO FROM USER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Name: <b>{user.first_name or '-'}</b>\n"
                f"🔗 Username: {username}\n"
                f"🆔 User ID: <code>{user.id}</code>\n\n"
                f"📝 Caption:\n{caption_text}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        print("PHOTO SEND ERROR:", e)

    sent_msg = await update.message.reply_text("✅ <b>Photo Sent To Owner</b>", parse_mode="HTML")
    await asyncio.sleep(3)
    try:
        await sent_msg.delete()
    except Exception:
        pass
    context.user_data.clear()
    await update.message.reply_text("🏠 Main Menu", reply_markup=main_keyboard())


async def sticker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    state = context.user_data.get("state")

    if state != "awaiting_chat_message" or not update.message or not update.message.sticker:
        return

    sticker_id = update.message.sticker.file_id
    try:
        username = f"@{user.username}" if user.username else "-"
        await context.bot.send_sticker(chat_id=ADMIN_ID, sticker=sticker_id)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "💬 <b>STICKER FROM USER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Name: <b>{user.first_name or '-'}</b>\n"
                f"🔗 Username: {username}\n"
                f"🆔 User ID: <code>{user.id}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        print("STICKER SEND ERROR:", e)

    sent_msg = await update.message.reply_text("✅ <b>Sticker Sent To Owner</b>", parse_mode="HTML")
    await asyncio.sleep(3)
    try:
        await sent_msg.delete()
    except Exception:
        pass
    context.user_data.clear()
    await update.message.reply_text("🏠 Main Menu", reply_markup=main_keyboard())


async def admin_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    if not update.message or not update.message.reply_to_message:
        return

    replied_message = update.message.reply_to_message
    replied_text = replied_message.text or replied_message.caption or ""
    if not any(marker in replied_text for marker in (
        "NEW MESSAGE FROM USER", "STICKER FROM USER", "PHOTO FROM USER"
    )):
        return

    match = re.search(r"User ID:\s*(\d+)", replied_text)
    if not match:
        await update.message.reply_text("❌ Could not find the user's ID in the replied message.")
        return

    target_user_id = int(match.group(1))
    reply_text = update.message.text or update.message.caption or ""

    try:
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=target_user_id,
                photo=update.message.photo[-1].file_id,
                caption=reply_text or None,
                parse_mode="HTML" if reply_text else None,
            )
        elif update.message.sticker:
            await context.bot.send_sticker(
                chat_id=target_user_id,
                sticker=update.message.sticker.file_id,
            )
        elif reply_text:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "💬 <b>MESSAGE FROM ADMIN</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{reply_text}"
                ),
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ Please reply with text, a photo, or a sticker.")
            return

        # Admin ဆီမှာ ပြတဲ့ "Reply sent" စာလေးကို ၃ စက္ကန့်ပြပြီး ပျောက်မယ်
        sent_msg = await update.message.reply_text("✅ Reply sent to user.")
        await asyncio.sleep(3)
        try:
            await sent_msg.delete()
        except Exception:
            pass
    except Exception as e:
        print("ADMIN REPLY SEND ERROR:", e)
        await update.message.reply_text("❌ Failed to send reply. User may have blocked the bot.")


# ---------- Deposit Photo Handler ----------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return

    user = update.effective_user
    state = context.user_data.get("state")

    # 🎯 Admin က /broadcast နဲ့ ပုံပို့ရင် ဒီကုဒ်ကို ကျော်သွားခိုင်းတာ
    if user.id == ADMIN_ID and update.message.caption and update.message.caption.startswith("/broadcast"):
        return

    if state == "awaiting_chat_message":
        return
    if state != "deposit_screenshot":
        await update.message.reply_text("❓ Please use the menu", reply_markup=main_keyboard())
        return

    file_id = update.message.photo[-1].file_id
    amount = context.user_data.get("deposit_amount", 0)

    # Save to DB
    currency = context.user_data.get("deposit_currency", "MMK")
    conn = sqlite3.connect(USER_DB)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO deposit_requests (user_id, amount, method, screenshot_id) VALUES (?, ?, ?, ?)",
        (int(user.id), float(amount), currency, file_id),
    )
    req_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Send to Admin
    alert_target = ALERT_CHAT_ID if ALERT_CHAT_ID else ADMIN_ID
    try:
        await context.bot.send_photo(
            chat_id=alert_target,
            photo=file_id,
            caption=(
                "💳 <b>NEW DEPOSIT REQUEST</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Name: <b>{html.escape(user.first_name or '-')}</b>\n"
                f"🔗 Username: @{user.username or '-'}\n"
                f"🆔 User ID: <code>{user.id}</code>\n\n"
                + (f"💵 Amount: <b>{amount:,.0f} MMK</b>\n" if currency == "MMK" else f"💲 Amount: <b>${get_usd_price(amount):,.2f}</b>\n")
                + f"🪙 Method: <b>{currency}</b>\n"
                f"🆔 Request ID: <code>{req_id}</code>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"deposit:approve:{req_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"deposit:reject:{req_id}"),
            ]]),
        )
    except Exception as e:
        print("DEPOSIT SEND ERROR:", e)

    await update.message.reply_text(
        "✅ <b>Screenshot received.</b>\n\nAdmin will review and add balance shortly.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )
    context.user_data.clear()


async def handle_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Admin only", show_alert=True)
        return True

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return True

    action, req_id_text = parts[1], parts[2]
    try:
        req_id = int(req_id_text)
    except ValueError:
        await query.answer()
        return True

    conn = sqlite3.connect(USER_DB)
    row = conn.execute(
        "SELECT user_id, amount, status FROM deposit_requests WHERE id=?", (req_id,)
    ).fetchone()
    if not row:
        conn.close()
        await query.answer("Request not found!", show_alert=True)
        return True

    user_id, amount, current_status = row
    current_status = str(current_status or "pending").lower()
    if current_status != "pending":
        conn.close()
        await query.answer(f"Already {current_status}!", show_alert=True)
        return True

    if action == "approve":
        # Mark the request first to prevent repeated clicks from crediting twice.
        changed = conn.execute(
            "UPDATE deposit_requests SET status='approved' WHERE id=? AND status='pending'",
            (req_id,),
        ).rowcount
        if not changed:
            conn.close()
            await query.answer("Request already processed!", show_alert=True)
            return True
        conn.commit()
        conn.close()

        add_user_balance(user_id, amount)
        await query.answer("Approved ✅")
        try:
            new_bal = get_user_balance(user_id)
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"✅ <b>Deposit Approved!</b>\n\n"
                      f"💰 Amount: <b>{float(amount):,.0f} MMK</b>\n"
                      f"💰 New Balance: <b>{float(new_bal):,.0f} MMK</b>"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        try:
            await query.edit_message_caption(
                caption=(query.message.caption or "") + "\n\n<b>✅ APPROVED</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass

    elif action == "reject":
        # Database ထဲကနေ User ဘာနဲ့ Deposit လုပ်ခဲ့လဲဆိုတာကို ဆွဲထုတ်မယ်
        method_row = conn.execute("SELECT method FROM deposit_requests WHERE id=?", (req_id,)).fetchone()
        method = method_row[0] if method_row else "MMK"

        changed = conn.execute(
            "UPDATE deposit_requests SET status='rejected' WHERE id=? AND status='pending'",
            (req_id,),
        ).rowcount
        conn.commit()
        conn.close()
        if not changed:
            await query.answer("Request already processed!", show_alert=True)
            return True

        await query.answer("Rejected ❌")

        # Method အလိုက် စာသားကို ခွဲပြမယ်
        if method == "USDT":
            reject_text = f"💲 Amount: <b>${get_usd_price(float(amount)):,.2f}</b>"
        else:
            reject_text = f"💵 Amount: <b>{float(amount):,.0f} MMK</b>"

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"❌ <b>Deposit Rejected</b>\n\n"
                      f"Your deposit request of {reject_text} was rejected. Please contact Admin."),
                parse_mode="HTML",
            )
        except Exception:
            pass
        try:
            await query.edit_message_caption(
                caption=(query.message.caption or "") + "\n\n<b>❌ REJECTED</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
    else:
        conn.close()
        await query.answer()

    return True


async def recover_pending_orders(application):
    await asyncio.sleep(10)
    conn = sqlite3.connect(USER_DB)
    rows = conn.execute(
        "SELECT user_id, transaction_id, product, game FROM orders WHERE status='pending'"
    ).fetchall()
    conn.close()

    for user_id, order_id, product_name, game_name in rows:
        if order_id and order_id != "-":
            print(f"=== RECOVERING ORDER: {order_id} ===")
            application.create_task(
                monitor_order_status(
                    application, user_id, str(order_id),
                    product_name=product_name, game_name=game_name
                    # message_id မပါလို့ အသစ်ပြန်ပို့ပေးသွားမှာပါ
                )
            )

async def error_handler(update, context):
    print("BOT ERROR:", context.error)


# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not found.")

    async def post_init(app):
        await recover_pending_orders(app)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("maintenance_on", maintenance_on_cmd))
    app.add_handler(CommandHandler("maintenance_off", maintenance_off_cmd))
    app.add_handler(CommandHandler("main_on", maintenance_on_cmd))
    app.add_handler(CommandHandler("main_off", maintenance_off_cmd))
    app.add_handler(CommandHandler("addbalance", add_balance_cmd))
    app.add_handler(CommandHandler("deductbalance", deduct_balance_cmd))
    app.add_handler(CommandHandler("checkbalance", check_balance_cmd))
    app.add_handler(CommandHandler("pending", pending_deposits_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    # CommandHandler does not process commands placed in photo captions.
    # Route admin photo captions beginning with /broadcast to broadcast_cmd too.
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.CaptionRegex(r"^/broadcast(?:@\w+)?(?:\s|$)"),
        broadcast_cmd,
    ))
    app.add_handler(CommandHandler("userinfo", userinfo_cmd))
    app.add_handler(CommandHandler("listusers", list_users_cmd))
    app.add_handler(CommandHandler("listuser", list_users_cmd))
    app.add_handler(CallbackQueryHandler(handle_access_callback, pattern=r"^access:"))
    app.add_handler(CallbackQueryHandler(handle_deposit_callback, pattern=r"^deposit:"))
    app.add_handler(CallbackQueryHandler(handle_profile_callback, pattern=r"^(profile_|back_to_profile$|back_to_main$)"))
    # Admin reply handler must be registered before generic photo/text handlers.
    app.add_handler(MessageHandler(
        filters.REPLY & (filters.TEXT | filters.PHOTO | filters.Sticker.ALL),
        admin_reply_handler,
    ))
    # Chat photos are handled first; deposit photos continue to photo_handler in group 1.
    app.add_handler(MessageHandler(filters.PHOTO, chat_photo_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler), group=1)
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)

    print("Bot is running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
