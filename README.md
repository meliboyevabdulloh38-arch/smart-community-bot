# Smart Community Bot

Smart Community Bot — Telegram guruh va shaxsiy chatlari uchun o‘zbek lotin/kiril, rus va ingliz tillarini aniqlaydigan, moderatsiya, anti-spam, ball, tezkor o‘yin va webhook asosidagi bot.

## Render sozlamalari

Render Web Service uchun quyidagilar ishlatiladi. Muhim eslatma: Render Free’dagi standart fayl tizimi restart yoki redeploy’da tozalanishi mumkin. Ballar, xotira va moderatsiya jurnali uzluksiz saqlanishi kerak bo‘lsa, Render’da persistent disk ulab `BOT_DB_PATH` ni shu disk ichidagi faylga sozlash yoki keyingi bosqichda tashqi PostgreSQL bazasiga ko‘chirish kerak.


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
| `TRANSCRIBE_API_URL` | Ixtiyoriy ovozdan matnga endpoint’i |
| `TRANSCRIBE_API_KEY` | Ixtiyoriy transkripsiya kaliti |
| `VISION_API_URL` | Ixtiyoriy rasm/OCR endpoint’i |
| `VISION_API_KEY` | Ixtiyoriy vision/OCR kaliti |
| `MEDIA_MAX_BYTES` | Media hajmi limiti; standart 12 MB |
| `BOT_DB_PATH` | SQLite fayli yo‘li; disk ulanganida `/var/data/smart-community-bot.sqlite3` kabi yo‘l bering |
| `REQUIRED_CHANNEL_ID` | Ixtiyoriy majburiy obuna kanali ID yoki username |
| `REQUIRED_CHANNEL_URL` | Ixtiyoriy kanal havolasi; obuna bo‘lmaganlarga ko‘rsatiladi |

`BOT_TOKEN` yoki boshqa maxfiy qiymatlarni GitHub’ga joylamang. Ularni faqat Render Environment Variables’da saqlang.

## Ishlayotgan imkoniyatlar

Shaxsiy chatda bot `/start`, `/yordam`, `/til`, `/holat`, `/oyin` va `/ball` buyruqlarini qabul qiladi. Guruhlarda bot oddiy matnli xabarlarni ham avtomatik ko‘rib chiqib, foydalanuvchi tilida qisqa javob beradi. `@mention` yoki bot xabariga reply qilish majburiy emas.

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
| `/rejalashtir 18:30 | matn` | Har kuni UTC bo‘yicha post yuborishni rejalashtiradi |
| `/rejalashtir` | Shu guruhdagi rejalashtirilgan postlarni ko‘rsatadi |
| `/obuna` | Barcha majburiy guruh va kanallarga a’zolikni tekshiradi |
| `/majburiy_qosh` | Admin turgan guruhni majburiy obuna ro‘yxatiga qo‘shadi |
| `/majburiy_royxat` | Majburiy obuna guruhlari ro‘yxatini ko‘rsatadi |
| `/majburiy_ochir` | Admin turgan guruhni ro‘yxatdan olib tashlaydi |

Telegram guruhlarida barcha oddiy xabarlarni olish uchun BotFather’dagi Privacy Mode’ni o‘chirish yoki botni administrator qilish kerak. Anti-spam filtri takroriy va reklama xabarlarini javobsiz qoldiradi. SQLite xotirasi processed Telegram update ID’larini saqlaydi. Shu sababli webhook retry yoki server restart paytida ayni update qayta kelib qolsa, bot uni ikkinchi marta javob bermasdan tashlab yuboradi. Adminlarning ogohlantirish, jim qilish, bloklash, chiqarish va filtr amallari `moderation_actions` jadvalida qayd etilib, `/statistika` javobida so‘nggi amallar ko‘rsatiladi.

## Ixtiyoriy kengaytmalar

Agar `AI_API_URL` va `AI_API_KEY` berilsa, bot OpenAI-compatible chat endpoint’iga savol yuborib, foydalanuvchi tilida javob qaytarishga urinadi. Ovozli xabar va rasmlar xavfsiz hajm limiti bilan qabul qilinadi. `TRANSCRIBE_API_URL`/`TRANSCRIBE_API_KEY` berilsa, ovoz fayli provider’ga yuborilib matnga aylantiriladi; `VISION_API_URL`/`VISION_API_KEY` berilsa, rasm yoki skrinshot OCR va qisqa mazmun tahliliga yuboriladi. Kalitlar berilmaganida bot foydalanuvchiga bu integratsiya hali faollashtirilmaganini o‘z tilida tushuntiradi.

Rejalashtirilgan postlar SQLite jadvaliga yoziladi va bot ishlayotgan paytda har 30 soniyada tekshiriladi; Render Free uyquga ketsa yoki standart fayl tizimi tozalansa, bunday jadval uchun persistent disk yoki tashqi baza kerak bo‘ladi. Admin majburiy obunaga qo‘shmoqchi bo‘lgan guruhning ichida `/majburiy_qosh` ni yuboradi. Kerak bo‘lsa `/majburiy_royxat` bilan ro‘yxat ko‘riladi, `/majburiy_ochir` bilan ayni guruh olib tashlanadi. Bot o‘sha guruhda a’zo yoki admin bo‘lishi kerak. `REQUIRED_CHANNEL_ID` va `REQUIRED_CHANNEL_URL` eski bitta kanal sozlamasi sifatida ham qo‘llab-quvvatlanadi. Voice-chat’da ko‘rinadigan ishtirokchi bo‘lish oddiy Telegram bot tokenidan tashqari userbot/MTProto akkauntini talab qiladi; bunday maxfiy ma’lumotni kodga yozish mumkin emas.

## Lokal tekshiruv

```bash
python3 -m py_compile app.py
uvicorn app:app --host 127.0.0.1 --port 8099
curl http://127.0.0.1:8099/
```

Health endpoint `status`, `service` va faol feature’lar ro‘yxatini qaytaradi.
