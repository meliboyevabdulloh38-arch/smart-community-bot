# Smart Community Bot

Smart Community Bot — Telegram guruh va shaxsiy chatlari uchun o‘zbek lotin/kiril, rus va ingliz tillarini aniqlaydigan, moderatsiya, anti-spam, ball, tezkor o‘yin va webhook asosidagi bot.

## Render sozlamalari

Render Web Service uchun quyidagilar ishlatiladi:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

Render Environment Variables bo‘limida quyidagilar bo‘lishi kerak:

| O‘zgaruvchi | Vazifasi |
|---|---|
| `BOT_TOKEN` | BotFather bergan Telegram tokeni |
| `WEBHOOK_SECRET` | Webhook manzilini himoyalovchi tasodifiy maxfiy qiymat |
| `AI_API_URL` | Ixtiyoriy AI provider endpoint’i |
| `AI_API_KEY` | Ixtiyoriy AI provider kaliti |
| `AI_MODEL` | Ixtiyoriy model nomi |

`BOT_TOKEN` yoki boshqa maxfiy qiymatlarni GitHub’ga joylamang. Ularni faqat Render Environment Variables’da saqlang.

## Ishlayotgan imkoniyatlar

Shaxsiy chatda bot `/start`, `/yordam`, `/til`, `/holat`, `/oyin` va `/ball` buyruqlarini qabul qiladi. Guruhlarda bot har bir xabarga javob bermaydi: foydalanuvchi botni `@mention` qilishi yoki bot xabariga reply qilishi kerak.

Guruh adminlari foydalanuvchi xabariga reply qilib o‘zbekcha buyruqlarni ishlatishi mumkin:

| Buyruq | Vazifasi |
|---|---|
| `/ogohlantir` | Ogohlantirish beradi; 3 ta ogohlantirishdan keyin bloklashga urinadi |
| `/jim 10` | Foydalanuvchini berilgan daqiqaga yozishdan cheklaydi |
| `/jimdanchiqar` | Jimlikni olib tashlaydi |
| `/blok` | Foydalanuvchini bloklaydi |
| `/blokdanchiqar` | Bloklangan foydalanuvchini qaytaradi |
| `/hayda` | Foydalanuvchini guruhdan chiqaradi |
| `/statistika` | Kuzatilgan a’zolar, ball va ogohlantirishlar xulosasi |
| `/filtr + so‘z` | Spam yoki taqiqlangan iborani filtrga qo‘shadi |
| `/filtr - so‘z` | Filtr iborasini olib tashlaydi |
| `/sozlamalar` | Guruh sozlamalarini ko‘rsatadi |
| `/xulosa` | Guruh faoliyati bo‘yicha qisqa xulosa |

SQLite xotirasi processed Telegram update ID’larini saqlaydi. Shu sababli webhook retry yoki server restart paytida ayni update qayta kelib qolsa, bot uni ikkinchi marta javob bermasdan tashlab yuboradi.

## Ixtiyoriy kengaytmalar

Agar `AI_API_URL` va `AI_API_KEY` berilsa, bot OpenAI-compatible chat endpoint’iga savol yuborib, foydalanuvchi tilida javob qaytarishga urinadi. Ovozli xabar va rasmlar hozircha qabul qilinadi va qo‘shimcha voice/OCR integratsiyasi kerakligi haqida javob beriladi. Bu modullarni ulash uchun qo‘shimcha provider yoki doimiy saqlash xizmati talab qilinishi mumkin.

Rejalashtirilgan postlar, majburiy obuna tekshiruvi, kengaytirilgan statistika paneli va alohida voice-chat yordamchi akkaunti keyingi bosqichlar uchun ajratilgan. Voice-chat’da ko‘rinadigan ishtirokchi bo‘lish oddiy Telegram bot tokenidan tashqari userbot/MTProto akkauntini talab qiladi; bunday maxfiy ma’lumotni kodga yozish mumkin emas.

## Lokal tekshiruv

```bash
python3 -m py_compile app.py
uvicorn app:app --host 127.0.0.1 --port 8099
curl http://127.0.0.1:8099/
```

Health endpoint `status`, `service` va faol feature’lar ro‘yxatini qaytaradi.
