import os
import re
import asyncio
import sqlite3
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
    FloodWaitError
)
from supabase import create_client, Client

# ==========================================
# 1. SOZLAMALAR VA MUHIT O'ZGARUVCHILARI
# ==========================================

API_ID = int(os.getenv("API_ID", "38792189"))
API_HASH = os.getenv("API_HASH", "066cd4f585b3ce48b5b48057b881e648")
BOT_TOKEN = os.getenv("BOT_TOKEN", "9089922346:AAH9POzsPiqL6j9moAThW09t0oDTy_s_r3Q")

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://sxdhrllgwjijwhrgcoze.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_f20KB1RZ8DGbF-Ld9LvI9g_xBGvUKeL")

# Standart salomlashish matni
DEFAULT_WELCOME_MESSAGE = (
    "Ассалому алайкум! Менинг исмим Парвиз.\n\n"
    "Курсимизда ўз ўрнингизни нақд қилиб қўйиш учун тўловни кўрсатилган картага амалга ошириб, "
    "чекини шу ерга юборсангиз бўлади:\n\n"
    "💳 5614 6816 0130 9234\n"
    "👤 Равшан Ибрагимов (бугалтер)"
)

DB_PATH = os.path.join(os.path.dirname(__file__), "owacrm.db")

# Supabase mijozi
supabase: Client = None
try:
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("⚡ Supabase mijoziga ulandi!")
except Exception as e:
    print(f"⚠️ Supabase ulanishida ogohlantirish: {e}")


# ==========================================
# 2. MA'LUMOTLAR BAZASI (SUPABASE + SQLITE HYBRID)
# ==========================================

replied_user_ids = set()
admin_states = {}

def get_sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    global replied_user_ids
    # 1. SQLite lokal bazasini sozlash (zaxira va tezkor ishlash uchun)
    with get_sqlite() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS greeted_users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                greeted_at TEXT
            )
        """)
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('welcome_message', ?)", (DEFAULT_WELCOME_MESSAGE,))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_greet_enabled', '1')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('notify_admin', '1')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_id', '')")
        conn.commit()

        cursor.execute("SELECT user_id FROM greeted_users")
        replied_user_ids = {row[0] for row in cursor.fetchall()}

    # 2. Supabase dan avval salom berilganlarni xotiraga yuklash
    if supabase:
        try:
            res = supabase.table("greeted_users").select("user_id").execute()
            if res.data:
                for row in res.data:
                    replied_user_ids.add(row["user_id"])
                print(f"☁️ Supabase bazasidan {len(res.data)} ta foydalanuvchi xotiraga yuklandi.")
        except Exception as e:
            print(f"ℹ️ Supabase 'greeted_users' jadvali hali ochilmagan bo'lishi mumkin (SQLite ishlamoqda): {e}")

    print(f"📦 Jami xotirada: {len(replied_user_ids)} ta avval salom berilgan akkauntlar mavjud.")

def get_setting(key: str, default: str = "") -> str:
    # Avval Supabase dan tekshiramiz
    if supabase:
        try:
            res = supabase.table("bot_settings").select("value").eq("key", key).execute()
            if res.data:
                return res.data[0]["value"]
        except Exception:
            pass

    # SQLite dan olamiz
    with get_sqlite() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default

def set_setting(key: str, value: str):
    # Supabase ga yozamiz
    if supabase:
        try:
            supabase.table("bot_settings").upsert({"key": key, "value": str(value)}).execute()
        except Exception as e:
            print(f"⚠️ Supabase'ga sozlama saqlashda xatolik: {e}")

    # SQLite ga yozamiz
    with get_sqlite() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def mark_user_greeted(user_id: int, name: str = ""):
    replied_user_ids.add(user_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Supabase ga yozish (Renderda hech qachon o'chib ketmaydi)
    if supabase:
        try:
            supabase.table("greeted_users").upsert({
                "user_id": user_id,
                "name": name,
                "greeted_at": now_str
            }).execute()
        except Exception as e:
            print(f"⚠️ Supabase greeted_users saqlashda: {e}")

    # SQLite ga yozish
    with get_sqlite() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO greeted_users (user_id, name, greeted_at) VALUES (?, ?, ?)",
            (user_id, name, now_str)
        )
        conn.commit()

def get_stats():
    total = len(replied_user_ids)
    recent = []

    if supabase:
        try:
            res = supabase.table("greeted_users").select("user_id, name, greeted_at").order("greeted_at", desc=True).limit(5).execute()
            if res.data:
                return total, res.data
        except Exception:
            pass

    with get_sqlite() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, name, greeted_at FROM greeted_users ORDER BY greeted_at DESC LIMIT 5")
        rows = cursor.fetchall()
        recent = [{"user_id": r[0], "name": r[1], "greeted_at": r[2]} for r in rows]

    return total, recent

init_db()


# ==========================================
# 3. TELEGRAM CLIENTLAR (RENDER UCHUN STRINGSESSION QO'LLAB-QUVVATLASH)
# ==========================================

# Renderda fayllar o'chib ketmasligi uchun sessiyani StringSession orqali Supabase'da saqlaymiz
user_session_string = get_setting("userbot_string_session", "")
if not user_session_string and os.path.exists("bot1.session"):
    # Agar fayl sessiya bo'lsa, o'shandan foydalanamiz
    userbot = TelegramClient("bot1", API_ID, API_HASH)
else:
    userbot = TelegramClient(StringSession(user_session_string), API_ID, API_HASH)

admin_bot = TelegramClient("admin_bot", API_ID, API_HASH)


# ==========================================
# 4. BOSHQARUV PANELI INTERFEYSI
# ==========================================

async def get_main_panel_text():
    is_auth = False
    user_info = "Ulanmagan ❌"
    
    try:
        if await userbot.is_user_authorized():
            is_auth = True
            me = await userbot.get_me()
            phone = f"+{me.phone}" if me.phone else "Raqamsiz"
            user_info = f"**{me.first_name}** ({phone}) ✅"
    except Exception:
        pass

    auto_greet = "Yoqilgan 🟢" if get_setting("auto_greet_enabled") == "1" else "O'chirilgan 🔴"
    notify = "Yoqilgan 🔔" if get_setting("notify_admin") == "1" else "O'chirilgan 🔕"
    total_greeted, _ = get_stats()

    text = (
        "🤖 **USERBOT BOSHQARUV PANELI (SUPABASE & RENDER)**\n\n"
        f"👤 **Userbot holati:** {user_info}\n"
        f"⚡ **Avto-salomlashish:** {auto_greet}\n"
        f"🔔 **Admin xabarnomasi:** {notify}\n"
        f"👥 **Jami salom berilganlar:** `{total_greeted}` ta\n"
        f"🚫 **Kontaktga qo'shish:** O'chirilgan (Xavfsiz)\n"
        f"☁️ **Bulutli baza:** Faol\n\n"
        "Boshqarish uchun quyidagi tugmalardan birini tanlang:"
    )
    return text, is_auth

async def get_main_keyboard():
    is_auth = False
    try:
        is_auth = await userbot.is_user_authorized()
    except Exception:
        pass

    auto_greet_btn = "🔴 Avto-salomni o'chirish" if get_setting("auto_greet_enabled") == "1" else "🟢 Avto-salomni yoqish"
    notify_btn = "🔕 Xabarnomani o'chirish" if get_setting("notify_admin") == "1" else "🔔 Xabarnomani yoqish"

    buttons = []
    if not is_auth:
        buttons.append([Button.inline("📱 Userbotga kirish (Login)", b"btn_login")])
    else:
        buttons.append([Button.inline("ℹ️ Akkaunt ma'lumoti", b"btn_account_info"), Button.inline("🚪 Akkauntdan chiqish", b"btn_logout")])

    buttons.append([Button.inline(auto_greet_btn, b"btn_toggle_greet"), Button.inline(notify_btn, b"btn_toggle_notify")])
    buttons.append([Button.inline("✍️ Salom matnini o'zgartirish", b"btn_edit_text"), Button.inline("📊 Statistika", b"btn_stats")])
    buttons.append([Button.inline("🔄 Yangilash", b"btn_refresh")])

    return buttons


# ==========================================
# 5. ADMIN BOT HODISALARI
# ==========================================

@admin_bot.on(events.NewMessage(pattern=r'^/start'))
async def handle_start(event):
    if not event.is_private:
        return
        
    sender_id = str(event.sender_id)
    current_admin = get_setting("admin_id")
    
    if not current_admin:
        set_setting("admin_id", sender_id)
        current_admin = sender_id
        await event.reply("👑 **Siz boshqaruv botining asosiy admini sifatida tayinlandingiz!**")

    if current_admin != sender_id:
        await event.reply("⛔ Kechirasiz, bu bot shaxsiy boshqaruv paneli hisoblanadi.")
        return

    admin_states.pop(event.sender_id, None)
    text, _ = await get_main_panel_text()
    keyboard = await get_main_keyboard()
    await event.reply(text, buttons=keyboard)


@admin_bot.on(events.CallbackQuery)
async def handle_callback(event):
    sender_id = str(event.sender_id)
    current_admin = get_setting("admin_id")
    
    if current_admin and current_admin != sender_id:
        await event.answer("Ruxsat yo'q!", alert=True)
        return

    data = event.data.decode('utf-8')
    chat_id = event.sender_id

    if data == "btn_refresh":
        text, _ = await get_main_panel_text()
        keyboard = await get_main_keyboard()
        await event.edit(text, buttons=keyboard)
        await event.answer("Yangilandi!")

    elif data == "btn_cancel":
        admin_states.pop(chat_id, None)
        text, _ = await get_main_panel_text()
        keyboard = await get_main_keyboard()
        await event.edit(text, buttons=keyboard)
        await event.answer("Bekor qilindi")

    elif data == "btn_login":
        if await userbot.is_user_authorized():
            await event.answer("Userbot allaqachon ulangan!", alert=True)
            return

        admin_states[chat_id] = {'step': 'WAIT_PHONE'}
        await event.edit(
            "📱 **Userbot uchun telefon raqamingizni kiriting:**\n\n"
            "Format: `+998901234567`\n\n"
            "Bekor qilish uchun pastdagi tugmani bosing:",
            buttons=[[Button.inline("❌ Bekor qilish", b"btn_cancel")]]
        )

    elif data == "btn_account_info":
        if not await userbot.is_user_authorized():
            await event.answer("Userbot ulanmagan!", alert=True)
            return
        me = await userbot.get_me()
        info_text = (
            "📱 **Ulangan Akkaunt Ma'lumotlari:**\n\n"
            f"👤 Ism: **{me.first_name}** {me.last_name or ''}\n"
            f"📞 Telefon: `+{me.phone}`\n"
            f"🆔 ID: `{me.id}`\n"
            f"🔗 Username: @{me.username if me.username else 'mavjud emas'}\n"
            f"⚡ Holati: Faol va xabarlarni qabul qilmoqda"
        )
        await event.edit(info_text, buttons=[[Button.inline("⬅️ Orqaga", b"btn_refresh")]])

    elif data == "btn_logout":
        await event.edit(
            "⚠️ **Haqiqatan ham userbot akkauntidan chiqmoqchimisiz?**",
            buttons=[
                [Button.inline("✅ Ha, chiqish", b"btn_confirm_logout")],
                [Button.inline("❌ Yo'q, bekor qilish", b"btn_refresh")]
            ]
        )

    elif data == "btn_confirm_logout":
        try:
            await userbot.log_out()
            set_setting("userbot_string_session", "")
            await event.answer("Akkauntdan chiqildi!", alert=True)
        except Exception as e:
            await event.answer(f"Xatolik: {e}", alert=True)
        
        text, _ = await get_main_panel_text()
        keyboard = await get_main_keyboard()
        await event.edit(text, buttons=keyboard)

    elif data == "btn_toggle_greet":
        curr = get_setting("auto_greet_enabled")
        new_val = "0" if curr == "1" else "1"
        set_setting("auto_greet_enabled", new_val)
        status = "o'chirildi 🔴" if new_val == "0" else "yoqildi 🟢"
        await event.answer(f"Avto-salomlashish {status}!")
        text, _ = await get_main_panel_text()
        keyboard = await get_main_keyboard()
        await event.edit(text, buttons=keyboard)

    elif data == "btn_toggle_notify":
        curr = get_setting("notify_admin")
        new_val = "0" if curr == "1" else "1"
        set_setting("notify_admin", new_val)
        status = "o'chirildi 🔕" if new_val == "0" else "yoqildi 🔔"
        await event.answer(f"Admin xabarnomasi {status}!")
        text, _ = await get_main_panel_text()
        keyboard = await get_main_keyboard()
        await event.edit(text, buttons=keyboard)

    elif data == "btn_edit_text":
        current_msg = get_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)
        admin_states[chat_id] = {'step': 'WAIT_WELCOME_TEXT'}
        await event.edit(
            f"📝 **Joriy salomlashish matni:**\n\n"
            f"\"\"\"\n{current_msg}\n\"\"\"\n\n"
            f"✍️ **Yangi salomlashish matnini yozib yuboring:**\n"
            f"*(Bekor qilish uchun pastdagi tugmani bosing)*",
            buttons=[[Button.inline("❌ Bekor qilish", b"btn_cancel")]]
        )

    elif data == "btn_stats":
        total, recent = get_stats()
        stats_text = f"📊 **SALOMLASHISH STATISTIKASI**\n\n"
        stats_text += f"👥 Jami salom yuborilgan yangi akkauntlar: **{total} ta**\n\n"
        
        if recent:
            stats_text += "🕒 **Oxirgi yozgan foydalanuvchilar:**\n"
            for r in recent:
                name = r.get('name') or 'Noma\'lum'
                uid = r.get('user_id')
                time_str = r.get('greeted_at')
                stats_text += f"• **{name}** (ID: `{uid}`) — _{time_str}_\n"
        else:
            stats_text += "_Hali hech kimga salom yuborilmadi._\n"

        await event.edit(stats_text, buttons=[[Button.inline("⬅️ Orqaga", b"btn_refresh")]])


@admin_bot.on(events.NewMessage(incoming=True))
async def handle_admin_text(event):
    if not event.is_private:
        return

    chat_id = event.sender_id
    text = (event.text or "").strip()

    if text.startswith("/"):
        return

    if chat_id not in admin_states:
        return

    state = admin_states[chat_id]
    step = state.get('step')

    # 1. Telefon raqam
    if step == 'WAIT_PHONE':
        phone = re.sub(r'[\s\-\(\)]', '', text)
        if not phone.startswith('+'):
            phone = '+' + phone

        if len(phone) < 9:
            await event.reply("❌ Noto'g'ri telefon raqam! Format: `+998901234567`\nQaytadan kiriting:")
            return

        msg = await event.reply("⏳ Telegramdan tasdiqlash kodi so'ralmoqda...")
        try:
            if not userbot.is_connected():
                await userbot.connect()
                
            res = await userbot.send_code_request(phone)
            admin_states[chat_id] = {
                'step': 'WAIT_CODE',
                'phone': phone,
                'phone_code_hash': res.phone_code_hash
            }
            await msg.edit(
                f"📩 **{phone} raqamiga Telegram orqali tasdiqlash kodi yuborildi!**\n\n"
                f"Iltimos, kodni kiriting:\n\n"
                f"💡 *Maslahat: Telegram xavfsizlik maqsadida kodni o'chirib qo'ymasligi uchun raqamlar orasiga probel qo'yib yuboring (Masalan: `1 2 3 4 5`)*",
                buttons=[[Button.inline("❌ Bekor qilish", b"btn_cancel")]]
            )
        except FloodWaitError as e:
            admin_states.pop(chat_id, None)
            await msg.edit(f"⏳ Juda ko'p urinish bo'ldi. {e.seconds} sekunddan so'ng qayta urinib ko'ring.")
        except PhoneNumberInvalidError:
            await msg.edit("❌ Bunday telefon raqam Telegramda ro'yxatdan o'tmagan! Qaytadan kiriting:")
        except Exception as e:
            admin_states.pop(chat_id, None)
            await msg.edit(f"❌ Xatolik yuz berdi: {str(e)}")

    # 2. Kod
    elif step == 'WAIT_CODE':
        code = re.sub(r'\D', '', text)
        if not code:
            await event.reply("❌ Kod faqat raqamlardan iborat bo'lishi kerak. Qaytadan kiriting:")
            return

        phone = state['phone']
        phone_code_hash = state['phone_code_hash']
        msg = await event.reply("⏳ Tizimga kirilmoqda...")

        try:
            await userbot.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            admin_states.pop(chat_id, None)
            
            # StringSession ni saqlab qo'yamiz (Render uchun o'ta muhim!)
            try:
                session_str = userbot.session.save()
                set_setting("userbot_string_session", session_str)
                print("💾 Userbot StringSession Supabase'ga saqlandi!")
            except Exception as se:
                print(f"⚠️ Session saqlashda ogohlantirish: {se}")

            me = await userbot.get_me()
            asyncio.create_task(scan_existing_dialogs())
            panel_text, _ = await get_main_panel_text()
            keyboard = await get_main_keyboard()
            
            await msg.edit(
                f"🎉 **Tabriklaymiz! Userbot muvaffaqiyatli ulandi!**\n\n"
                f"👤 Akkaunt: **{me.first_name}**\n"
                f"📞 Raqam: `+{me.phone}`\n"
                f"⚡ Endi bu akkauntga yangi yozganlarga sekundsiz salomlashish xabari ketadi!\n"
                f"🚫 Kontaktga qo'shish funksiyasi o'chirilgan."
            )
            await event.reply(panel_text, buttons=keyboard)

        except SessionPasswordNeededError:
            admin_states[chat_id] = {
                'step': 'WAIT_PASSWORD',
                'phone': phone
            }
            await msg.edit(
                "🔐 **Ushbu akkauntda Ikki Bosqichli Parol (2FA Cloud Password) o'rnatilgan.**\n\n"
                "Iltimos, Telegram 2FA parolingizni kiriting:",
                buttons=[[Button.inline("❌ Bekor qilish", b"btn_cancel")]]
            )
        except PhoneCodeInvalidError:
            await msg.edit("❌ Tasdiqlash kodi xato! Iltimos, kodni to'g'ri tekshirib qaytadan kiriting:")
        except PhoneCodeExpiredError:
            admin_states.pop(chat_id, None)
            await msg.edit("⚠️ Kodning amal qilish muddati tugagan. Iltimos, qaytadan boshlang.")
        except Exception as e:
            await msg.edit(f"❌ Xatolik yuz berdi: {str(e)}")

    # 3. 2FA Parol
    elif step == 'WAIT_PASSWORD':
        password = text
        msg = await event.reply("⏳ Parol tekshirilmoqda...")
        try:
            await userbot.sign_in(password=password)
            admin_states.pop(chat_id, None)

            # StringSession saqlash
            try:
                session_str = userbot.session.save()
                set_setting("userbot_string_session", session_str)
            except Exception:
                pass

            me = await userbot.get_me()
            asyncio.create_task(scan_existing_dialogs())
            panel_text, _ = await get_main_panel_text()
            keyboard = await get_main_keyboard()

            await msg.edit(
                f"🎉 **Tabriklaymiz! Userbot muvaffaqiyatli ulandi!**\n\n"
                f"👤 Akkaunt: **{me.first_name}**\n"
                f"📞 Raqam: `+{me.phone}`\n"
                f"⚡ Tizim to'liq faol holatda!"
            )
            await event.reply(panel_text, buttons=keyboard)
        except Exception as e:
            await msg.edit(f"❌ Parol noto'g'ri: {str(e)}\nQaytadan parolni kiriting:")

    # 4. Yangi salom matni
    elif step == 'WAIT_WELCOME_TEXT':
        set_setting("welcome_message", text)
        admin_states.pop(chat_id, None)
        await event.reply("✅ **Salomlashish matni muvaffaqiyatli saqlandi!**")
        panel_text, _ = await get_main_panel_text()
        keyboard = await get_main_keyboard()
        await event.reply(panel_text, buttons=keyboard)


# ==========================================
# 6. USERBOT HODISALARI (FAQATGINA YANGI CHATLARGA SEKUNDSIZ JAVOB)
# ==========================================

async def scan_existing_dialogs():
    """Bot ishga tushganda barcha mavjud eski chatlarni xotiraga oladi va ularga salom bormaydi"""
    try:
        count = 0
        async for dialog in userbot.iter_dialogs(limit=1000):
            if dialog.is_user:
                replied_user_ids.add(dialog.id)
                count += 1
        print(f"🔒 Jami {count} ta mavjud eski chatlar aniqlandi va himoyalandi (ularga xabar bormaydi).")
    except Exception as e:
        print(f"⚠️ Eski chatlarni skanerlashda xatolik: {e}")

@userbot.on(events.NewMessage(incoming=True))
async def handle_userbot_incoming(event):
    if not event.is_private:
        return

    # O'zimiz yuborgan xabarlarni inkor qilamiz
    if event.out:
        return

    sender = await event.get_sender()
    if not sender or getattr(sender, 'bot', False):
        return

    sender_id = event.sender_id
    first_name = getattr(sender, 'first_name', '') or ''
    username = f"@{sender.username}" if getattr(sender, 'username', None) else "Mavjud emas"

    # 1. Sozlamadan avto-salomlashish yoqilganligini tekshirish
    if get_setting("auto_greet_enabled") != "1":
        return

    # 2. KONTAKTLAR FILTRI: Agar bu sizning kontaktingizda bo'lsa, aslo salom bormaydi!
    if getattr(sender, 'contact', False):
        replied_user_ids.add(sender_id)
        return

    # 3. KESH TEKSHIRUVI: Agar allaqachon salom berilgan yoki eski chat deb belgilangan bo'lsa
    if sender_id in replied_user_ids:
        return

    # 4. CHAT TARIXI TEKSHIRUVI (FAQAT BIRINCHI MARTA YOZGAN YANGI CHATLARNI ANIQLASH):
    try:
        # Oxirgi 3 ta xabarni tekshiramiz
        history = await userbot.get_messages(sender_id, limit=3)
        # Agar chatda 1 tadan ko'p xabar bo'lsa, demak bu eski yozishilgan chat!
        if len(history) > 1:
            mark_user_greeted(sender_id, first_name)
            print(f"⏩ Eski chat aniqlandi (tarixda xabarlar bor). Salom yuborilmadi: {first_name} (ID: {sender_id})")
            return
    except Exception as e:
        print(f"⚠️ Tarixni tekshirishda ogohlantirish: {e}")

    # 5. FAQATGINA HAQIQIY YANGI CHAT BO'LGANDAGINA SEKUNDSIZ SALOM YUBORAMIZ:
    mark_user_greeted(sender_id, first_name)
    welcome_text = get_setting("welcome_message", DEFAULT_WELCOME_MESSAGE)
    
    try:
        await event.reply(welcome_text)
        print(f"⚡ YANGI CHATGA sekundsiz to'lov xabari yuborildi: {first_name} (ID: {sender_id})")

        # Boshqaruv botida adminga bildirishnoma
        if get_setting("notify_admin") == "1":
            admin_id = get_setting("admin_id")
            if admin_id:
                try:
                    await admin_bot.send_message(
                        int(admin_id),
                        f"🔔 **Yangi mijoz birinchi marta yozdi!**\n\n"
                        f"👤 Ism: **{first_name}**\n"
                        f"🆔 ID: `{sender_id}`\n"
                        f"🔗 Username: {username}\n"
                        f"💬 Xabari: _{event.text[:100] if event.text else '[Media/Stiker]' }_\n\n"
                        f"⚡ Unga darhol to'lov rekvizitlari yuborildi.\n"
                        f"🚫 Kontaktga qo'shilmadi."
                    )
                except Exception as notify_err:
                    print(f"⚠️ Adminga xabar yuborishda xatolik: {notify_err}")

    except Exception as e:
        print(f"❌ Salom yuborishda xatolik: {e}")


# ==========================================
# 7. FASTAPI ILovasi (RENDER UCHUN HEALTHCHECK VA VEB-SERVER)
# ==========================================

async def run_bots():
    print("=" * 50)
    print("🚀 Boshqaruv Boti ishga tushirilmoqda...")
    await admin_bot.start(bot_token=BOT_TOKEN)
    bot_me = await admin_bot.get_me()
    print(f"🤖 Boshqaruv Boti faol: @{bot_me.username}")
    
    print("📱 Userbot tekshirilmoqda...")
    await userbot.connect()
    if await userbot.is_user_authorized():
        me = await userbot.get_me()
        print(f"✅ Userbot allaqachon ulangan: {me.first_name} (+{me.phone})")
        asyncio.create_task(scan_existing_dialogs())
    else:
        print(f"⚠️ Userbot hali ulanmagan. Boshqaruv botiga kiring (@{bot_me.username}) va raqam orqali kiring!")
    print("=" * 50)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dastur ishga tushganda botlarni ishga tushirish
    asyncio.create_task(run_bots())
    yield
    # Dastur to'xtaganda
    try:
        await admin_bot.disconnect()
        await userbot.disconnect()
    except Exception:
        pass

app = FastAPI(title="OWA Userbot & CRM Automation", lifespan=lifespan)

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "OWA Telegram Userbot & Admin Bot",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health():
    return {"status": "ok"}


# ==========================================
# 8. LOKAL ISHGA TUSHIRISH
# ==========================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
