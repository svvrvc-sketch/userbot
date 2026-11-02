from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from fastapi import FastAPI, Request
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact
from supabase import create_client, Client

# ==========================================
# 1. SOZLAMALAR VA SUPABASE ULANISHI
# ==========================================

SESSION_NAME = "bot1" 

BITRIX_WEBHOOK_URL = "https://bitrix24.owa.uz/rest/721/v3lqvmymdsrk8w7t/"
TARGET_STATUS_ID = "NEW"

SUPABASE_URL = "https://gqetxwbveyfliidfyjee.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdxZXR4d2J2ZXlmbGlpZGZ5amVlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU2OTE0ODcsImV4cCI6MjEwMTI2NzQ4N30.w-pBjSyhDcExHyb58AG6e--ZioH41w-jkrt9SPJtu9E"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

API_ID = 38792189
API_HASH = "066cd4f585b3ce48b5b48057b881e648"

userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)
app = FastAPI(title="Bitrix24 & Telegram Automation API")

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))


# ==========================================
# 2. ISMGA QARAB AKA/OPA ANIQLASH FUNKSIYASI
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
# 3. SUPABASE BILAN ISHLASH FUNKSIYALARI
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
# 4. TELEGRAM HODISALARI (FAKAT ODAMLARDAN KELGAN XABARLAR)
# ==========================================

@userbot.on(events.NewMessage(incoming=True))
async def handle_incoming_message(event):
    if event.is_private:
        sender = await event.get_sender()
        
        # ⚠️ BOTLARDAN KELGAN XABARLARNI ETIBORSIZ QOLDIRAMIZ!
        if getattr(sender, 'bot', False):
            return

        sender_id = event.sender_id
        message_text = event.text

        # Bo'sh xabar bo'lsa (stiker yoki fayl bo'lsa) o'tkazib yuboramiz
        if not message_text:
            return
        
        lead_id = get_lead_id_by_user(sender_id)
        
        if not lead_id:
            try:
                response = supabase.table("user_leads").select("lead_id").order("user_id", desc=True).limit(1).execute()
                if response.data:
                    lead_id = response.data[0]["lead_id"]
                    save_user_lead(sender_id, lead_id)
            except Exception as e:
                print(f"❌ Lead ID topishda xatolik: {e}")
        
        if lead_id and message_text:
            print(f"💬 Mijozdan javob keldi (Lead {lead_id}): {message_text}")
            
            comment_payload = {
                "fields": {
                    "ENTITY_ID": lead_id,
                    "ENTITY_TYPE": "lead",
                    "COMMENT": f"💬 Telegramdan mijoz javobi:\n{message_text}"
                }
            }
            
            try:
                res = session.post(
                    f"{BITRIX_WEBHOOK_URL}crm.timeline.comment.add.json", 
                    json=comment_payload, 
                    timeout=10
                )
                res_data = res.json()
                if res.status_code == 200 and res_data.get("result"):
                    print(f"✅ Bitrix24 Lid ({lead_id}) kartochkasiga izoh tushdi!")
                else:
                    print(f"⚠️ Bitrix24 izohni qabul qilmadi: {res_data}")
            except Exception as e:
                print(f"❌ Bitrix24 HTTP so'rovida xatolik: {e}")


@app.on_event("startup")
async def startup_event():
    await userbot.start()
    print("🚀 Telethon Userbot va Supabase bulutli bazasi muvaffaqiyatli ishga tushdi!")

@app.on_event("shutdown")
async def shutdown_event():
    await userbot.disconnect()


# ==========================================
# 5. BITRIX24 WEBHOOK ENDPOINT
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

        # --- TELEFON RAQAMNI AQLLI TOZALASH VA UZB FORMATIGA KELTIRISH ---
        raw_digits = "".join(filter(str.isdigit, phone))

        if len(raw_digits) == 9:
            clean_phone = "+998" + raw_digits
        elif len(raw_digits) == 12 and raw_digits.startswith("998"):
            clean_phone = "+" + raw_digits
        elif len(raw_digits) == 10 and raw_digits.startswith("8"):
            clean_phone = "+998" + raw_digits[1:]
        else:
            clean_phone = "+" + raw_digits

        # 1. Telegram User ID ni topamiz
        user_id = None
        try:
            contact = InputPhoneContact(client_id=0, phone=clean_phone, first_name=contact_name, last_name="")
            result = await userbot(ImportContactsRequest([contact]))
            if result.users:
                user_id = result.users[0].id
        except Exception as tg_err:
            print(f"❌ Telegram kontaktni topishda xatolik: {tg_err}")

        # 2. Yangi kelgan lidni shu userga biriktirib qo'yamiz
        if user_id:
            save_user_lead(user_id, lead_id)

        # 3. 7 kunlik limitni tekshiramiz
        now = datetime.now()
        last_sent_time = get_last_sent_time(clean_phone)

        if last_sent_time:
            if now - last_sent_time < timedelta(days=7):
                print(f"⏩ Telefon {clean_phone} ga 7 kun ichida xabar ketgan. Xabar yuborilmadi, lekin Lid ({lead_id}) ga bog'landi.")
                return {"status": "skipped", "message": "7 kun ichida xabar yuborilgan, lekin lid yangilandi"}

        # 4. Agar limit tugagan bo'lsa, Telegram xabarini yuboramiz
        if user_id:
            try:
                honorific = get_honorific(contact_name)

                welcome_message = (
                    f"Assalomu alaykum, hurmatli **{contact_name}{honorific}**! 👋\n\n"
                    f"Sizni **Open Web Academy** o'quv markazi nomidan kurslarimizga qiziqib, "
                    f"ro'yxatdan o'tganingiz uchun qutlashdan mamnunmiz! ✨\n\n"
                    f"Yaqin vaqt ichida siz bilan **55-405-55-00** raqamidan mutaxassislarimiz bog'lanishadi.\n\n"
                    f"🌐 Rasmiy veb-saytimiz: owa.uz\n\n"
                    f"Agar telefon orqali gaplashish hozircha noqulay bo'lsa yoki savollaringiz bo'lsa, "
                    f"bemalol shu Telegram orqali yozaverishingiz mumkin. Biz doim aloqadamiz! 🤝"
                )
                
                await userbot.send_message(user_id, welcome_message, parse_mode='markdown')
                
                # Supabase bazasiga saqlaymiz
                save_phone_sent_time(clean_phone, now, contact_name)

                print(f"✅ Supabase'ga saqlandi va Telegram xabar ketdi -> {contact_name}{honorific} ({clean_phone})")
            except Exception as tg_err:
                print(f"❌ Telegram yuborishda xatolik: {tg_err}")
        else:
            print(f"⚠️ Telegram akkaunt topilmadi: {clean_phone}")

        return {"status": "success"}

    except Exception as e:
        print(f"❌ Server ichki xatosi: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)