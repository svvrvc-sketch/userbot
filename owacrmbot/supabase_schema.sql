-- ==============================================================
-- OWA CRM USERBOT: SUPABASE MA'LUMOTLAR BAZASI STRUKTURASI
-- Ushbu SQL kodini Supabase boshqaruv panelidagi "SQL Editor" bo'limiga
-- tashlab "Run" tugmasini bosing.
-- ==============================================================

-- 1. Salomlashish xabari yuborilgan akkauntlar jadvali
CREATE TABLE IF NOT EXISTS greeted_users (
    user_id BIGINT PRIMARY KEY,
    name TEXT,
    greeted_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Bot sozlamalari va Render uchun StringSession saqlovchi jadval
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Boshlang'ich sozlamalarni kiritish
INSERT INTO bot_settings (key, value)
VALUES 
    ('welcome_message', 'Ассалому алайкум! Менинг исмим Парвиз.

Курсимизда ўз ўрнингизни нақд қилиб қўйиш учун тўловни кўрсатилган картага амалга ошириб, чекини шу ерга юборсангиз бўлади:

💳 5614 6816 0130 9234
👤 Равшан Ибрагимов (бугалтер)'),
    ('auto_greet_enabled', '1'),
    ('notify_admin', '1'),
    ('admin_id', '')
ON CONFLICT (key) DO NOTHING;

-- RLS (Row Level Security) - barcha so'rovlar uchun ochiq qilish (Service/Anon kalit orqali)
ALTER TABLE greeted_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all operations for greeted_users" ON greeted_users FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations for bot_settings" ON bot_settings FOR ALL USING (true) WITH CHECK (true);

