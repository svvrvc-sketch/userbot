import os
import sqlite3
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from fastapi import FastAPI, Request
from telethon import TelegramClient, events
from supabase import create_client, Client

# ==========================================
# 1. ASOSIY SOZLAMALAR VA BOT TOKEN
# ==========================================

SESSION_NAME = "bot1" 
API_ID = 38792189
API_HASH = "066cd4f585b3ce48b5b48057b881e648"

# Ulangan Bot Tokeni:
BOT_TOKEN = "9089922346:AAH9POzsPiqL6j9moAThW09t0oDTy_s_r3Q"

# Yangi yozgan foydalanuvchiga yuboriladigan salomlashish xabari:
WELCOME_MESSAGE = (
    "Ассалому алайкум! Менинг исмим Парвиз.\n\n"
    "Курсимизда ўз ўрнингизни нақд қилиб қўйиш учун тўловни кўрсатилган картага амалга ошириб, "
    "чекини шу ерга юборсангиз бўлади:\n\n"
    "💳 5614 6816 0130 9234\n"
    "👤 Равшан Ибрагимов (бугалтер)"
)

# Bitrix24 va Supabase sozlamalari
BITRIX_WEBHOOK_URL = "https://bitrix24.owa.uz/rest/721/v3lqvmymdsrk8w7t/"
TARGET_STATUS_ID = "NEW"

SUPABASE_URL = "https://sxdhrllgwjijwhrgcoze.supabase.co"
SUPABASE_KEY = "sb_publishable_f20KB1RZ8DGbF-Ld9LvI9g_xBGvUKeL"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)
app = FastAPI(title="Bitrix24 & Telegram Automation API")

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))


# ==========================================
# 2. JAVOB BERILGAN USERLAR BAZASI (SQLITE + XOTIRA KESHI)
# Yangi foydalanuvchini sekundsiz (0 soniyada) aniqlash uchun
# ==========================================

DB_PATH = os.path.join(os.path.dirname(__file__), "replied_users.db")
replied_user_ids = set()

def init_db():
    """Baza jadvalini yaratish va avval salom berilganlarni xotiraga yuklash"""
    global replied_user_ids
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS greeted_users (
                user_id INTEGER PRIMARY KEY,
                greeted_at TEXT
            )
        """)
        conn.commit()
        
        cursor.execute("SELECT user_id FROM greeted_users")
        rows = cursor.fetchall()
        replied_user_ids = {row[0] for row in rows}
        conn.close()
        print(f"📦 Bazadan {len(replied_user_ids)} ta avval salom berilgan akkauntlar xotiraga yuklandi.")
    except Exception as e:
        print(f"❌ SQLite bazani yuklashda xatolik: {e}")

def mark_user_greeted(user_id: int):
    """Foydalanuvchiga salom yuborilganini bazaga va xotiraga saqlash"""
    replied_user_ids.add(user_id)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO greeted_users (user_id, greeted_at) VALUES (?, ?)",
            (user_id, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ SQLite saqlashda xatolik: {e}")

init_db()


# ==========================================
# 3. ISMGA QARAB AKA/OPA ANIQLASH FUNKSIYASI
# ==========================================

def get_honorific(name: str) -> str:
    if not name or name.strip().lower() == "mijoz":
        return ""
    
    clean_name = name.strip().lower()

    female_endings = (
        'a', 'ya', 'oy', 'niso', 'mola', 'noza', 'bona', 'ina', 
        'nora', 'ira', 'gul', 'bonu', 'xonzoda', 'begim'
    )
    male_exceptions = ['yahyo', 'mustafo', 'murodilla', 'sadrilla', 'injil', 'iso', 'musa']

    if clean_name in male_exceptions:
        return " aka"

    if clean_name.endswith(female_endings):
        return " opa"
    
    return " aka"


# ==========================================
# 4. SUPABASE BILAN ISHLASH FUNKSIYALARI
# ==========================================

def get_last_sent_time(phone: str):
    try:
        response = supabase.table("processed_phones").select("last_sent_at").eq("phone", phone).execute()
        if response.data:
            return datetime.fromisoformat(response.data[0]["last_sent_at"])
    except Exception as e:
        print(f"❌ Supabase o'qishda xatolik: {e}")
    return None

def save_phone_sent_time(phone: str, dt: datetime, name: str):
    try:
        supabase.table("processed_phones").upsert({
            "phone": phone,
            "last_sent_at": dt.isoformat(),
            "name": name
        }).execute()
    except Exception as e:
        print(f"❌ Supabase'ga raqam/ism saqlashda xatolik: {e}")

def save_user_lead(user_id: int, lead_id: str):
    try:
        supabase.table("user_leads").upsert({
            "user_id": user_id,
            "lead_id": str(lead_id)
        }).execute()
    except Exception as e:
        print(f"❌ Supabase'ga User-Lead saqlashda xatolik: {e}")

def get_lead_id_by_user(user_id: int):
    try:
        response = supabase.table("user_leads").select("lead_id").eq("user_id", user_id).execute()
        if response.data:
            return response.data[-1]["lead_id"]
    except Exception as e:
        print(f"❌ Supabase'dan Lead ID olishda xatolik: {e}")
    return None


# ==========================================
# 5. TELEGRAM HODISALARI (FAQATGINA YANGI CHATLARGA SALOMLASHISH)
# ==========================================

async def scan_existing_dialogs():
    """Mavjud eski chatlarni xotiraga olib, ularga salom bormasligini ta'minlash"""
    try:
        count = 0
        async for dialog in userbot.iter_dialogs(limit=1000):
            if dialog.is_user:
                replied_user_ids.add(dialog.id)
                count += 1
        print(f"🔒 Jami {count} ta mavjud eski chatlar aniqlandi va himoyalandi.")
    except Exception as e:
        print(f"⚠️ Eski dialoglarni olishda xatolik: {e}")

@userbot.on(events.NewMessage(incoming=True))
async def handle_incoming_message(event):
    if not event.is_private:
        return

    if event.out:
        return

    sender = await event.get_sender()
    if not sender or getattr(sender, 'bot', False):
        return

    sender_id = event.sender_id
    message_text = (event.text or "").strip()

    # 1. Kontaktda bo'lsa, aslo salom bormaydi
    if getattr(sender, 'contact', False):
        replied_user_ids.add(sender_id)
        return

    # 2. Avval yozilgan bo'lsa, o'tkazib yuborish
    if sender_id in replied_user_ids:
        pass
    else:
        # 3. Chat tarixini tekshirish (faqat birinchi marta yozgan yangi chatlar uchun)
        try:
            history = await userbot.get_messages(sender_id, limit=3)
            if len(history) > 1:
                mark_user_greeted(sender_id)
                print(f"⏩ Eski chat (tarix mavjud). Salom yuborilmadi: {sender_id}")
            else:
                # FAQATGINA YANGI CHAT:
                mark_user_greeted(sender_id)
                await event.reply(WELCOME_MESSAGE)
                name_display = getattr(sender, 'first_name', '') or str(sender_id)
                print(f"⚡ YANGI CHATGA sekundsiz salomlashish xabari yuborildi: {name_display} (ID: {sender_id})")
        except Exception as e:
            print(f"❌ Salomlashish xabarini yuborishda xatolik: {e}")

    # 2. Bitrix24 integratsiyasi (agar mavjud bo'lsa, mijoz javobini Bitrixga yetkazish)
    if message_text:
        lead_id = get_lead_id_by_user(sender_id)
        if not lead_id:
            try:
                response = supabase.table("user_leads").select("lead_id").order("user_id", desc=True).limit(1).execute()
                if response.data:
                    lead_id = response.data[0]["lead_id"]
                    save_user_lead(sender_id, lead_id)
            except Exception as e:
                print(f"❌ Lead ID topishda xatolik: {e}")

        if lead_id:
            print(f"💬 Mijozdan xabar (Lead {lead_id}): {message_text}")
            comment_payload = {
                "fields": {
                    "ENTITY_ID": lead_id,
                    "ENTITY_TYPE": "lead",
                    "COMMENT": f"💬 Telegramdan mijoz javobi:\n{message_text}"
                }
            }
            try:
                session.post(
                    f"{BITRIX_WEBHOOK_URL}crm.timeline.comment.add.json", 
                    json=comment_payload, 
                    timeout=5
                )
            except Exception as e:
                print(f"❌ Bitrix24 izoh yuborishda xatolik: {e}")


# ==========================================
# 6. SERVER ISHGA TUSHISHI VA TO'XTATILISHI
# ==========================================

@app.on_event("startup")
async def startup_event():
    if BOT_TOKEN:
        print(f"🔑 Bot Token orqali ulanmoqda: {BOT_TOKEN[:10]}...")
        await userbot.start(bot_token=BOT_TOKEN)
        print("🤖 Telegram Bot muvaffaqiyatli ishga tushdi!")
    else:
        await userbot.start()
        print("🚀 Telethon Userbot muvaffaqiyatli ishga tushdi!")
    
    import asyncio
    asyncio.create_task(scan_existing_dialogs())
    print("⚡ Yangi yozgan akkauntlarga salomlashish xizmati faol!")

@app.on_event("shutdown")
async def shutdown_event():
    await userbot.disconnect()


# ==========================================
# 7. BITRIX24 WEBHOOK ENDPOINT
# (Kontaktlarga qo'shish funksiyasi to'liq olib tashlangan)
# ==========================================

@app.post("/webhook")
async def bitrix_webhook(request: Request):
    try:
        form_data = await request.form()
        data = dict(form_data)
        
        lead_id = data.get("data[FIELDS][ID]")
        if not lead_id:
            return {"status": "error", "message": "Lead ID topilmadi"}

        lead_res = session.get(f"{BITRIX_WEBHOOK_URL}crm.lead.get.json", params={"id": lead_id}, timeout=10)
        if lead_res.status_code != 200:
            return {"status": "error", "message": "Lead olinmadi"}
            
        lead_data = lead_res.json().get("result", {})
        status = lead_data.get("STATUS_ID")
        contact_id = lead_data.get("CONTACT_ID")

        if status != TARGET_STATUS_ID:
            print(f"⏩ Lead {lead_id} statusi '{status}'. O'tkazib yuborildi.")
            return {"status": "skipped", "message": "Status NEW emas"}

        phone = None
        contact_name = "Mijoz"
        
        if contact_id:
            contact_res = session.get(f"{BITRIX_WEBHOOK_URL}crm.contact.get.json", params={"id": contact_id}, timeout=10)
            if contact_res.status_code == 200:
                contact_data = contact_res.json().get("result", {})
                first_name = contact_data.get("NAME")
                if first_name:
                    contact_name = first_name.strip()
                phone_list = contact_data.get("PHONE", [])
                if phone_list and isinstance(phone_list, list):
                    phone = phone_list[0].get("VALUE")

        if not phone:
            return {"status": "warning", "message": "Phone not found"}

        return {"status": "success", "message": "Kontaktga qo'shish o'chirilgan"}

    except Exception as e:
        print(f"❌ Server ichki xatosi: {e}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
