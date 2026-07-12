# Self-Evolving Modular Telegram Bot

Aiogram 3.x asosidagi, admin uchun AI Creator (Gemini API) orqali
o'z-o'zini kengaytira oladigan Telegram bot.

## Arxitektura

- `main.py` — bot yadrosi (O'ZGARMAS): menyular, Admin Panel, AI Creator
  FSM (Chatting → Drafting → Human-in-the-loop review), Undo/Redo.
- `plugin_loader.py` — yadro (O'ZGARMAS): `plugins/` papkasini xavfsiz
  dinamik yuklaydi, AI yozgan kodni AST orqali xavfsizlik tekshiruvidan
  o'tkazadi (`eval`, `exec`, `os.system`, `subprocess` va h.k. bloklanadi;
  `ADMIN_IDS`ga tegish taqiqlanadi).
- `plugins/` — AI Creator tomonidan yaratiladigan/yangilanadigan modullar.
  Har bir fayl o'z ichida `router = Router()` obyektini eksport qiladi.
- `history/` — har bir tasdiqlangan (Apply) o'zgarish arxivi, Undo/Redo
  uchun ishlatiladi (`_index.json` — stack va redo_stack).
- `github_sync.py` — yadro (O'ZGARMAS): Apply/Undo/Redo'dan so'ng
  `plugins/*.py` va `history/*` fayllarini GitHub Contents API orqali
  to'g'ridan-to'g'ri repoga commit qiladi (git binarisiz ishlaydi).

## GitHub sinxronizatsiya (Railway uchun muhim!)

Railway odatda GitHub repoga ulanib, shu repodagi kodni deploy qiladi.
Muammo: bot ishlab turganda AI Creator yozgan pluginlar avval FAQAT
konteynerning lokal diskiga yozilardi — GitHub'ga hech qachon
push bo'lmasdi. Natijada Railway keyingi safar qayta deploy qilganda
(yoki konteyner qayta ishga tushganda) barcha AI yaratgan pluginlar
yo'qolib qolardi.

Buni tuzatish uchun `github_sync.py` qo'shildi — u har bir Apply/Undo/Redo
amalidan so'ng tegishli fayllarni GitHub'ga commit qiladi. Yoqish uchun
Railway loyihangizning **Variables** bo'limiga quyidagilarni qo'shing:

| O'zgaruvchi | Tavsif |
|---|---|
| `GITHUB_TOKEN` | "repo" (yoki "Contents: Read and write") huquqiga ega Personal Access Token |
| `GITHUB_REPO` | `egasi/repo-nomi` — Railway ulangan repo bilan bir xil bo'lishi kerak |
| `GITHUB_BRANCH` | Railway kuzatayotgan branch (odatda `main`) |

**Muhim eslatma:** Railway shu branchni kuzatib turgani uchun har bir
push avtomatik qayta-deploy'ni ishga tushiradi (bot bir necha soniyaga
qayta ishga tushadi). Bu me'yoriy holat — bot shu tarzda haqiqatan ham
"o'z-o'zini evolyutsiya qiladi" va GitHub'dagi kod doim ishlab turgan
holat bilan bir xil bo'lib qoladi.

Agar `GITHUB_TOKEN`/`GITHUB_REPO` sozlanmagan bo'lsa, bot xatosiz
ishlayveradi — faqat sinxronizatsiya o'chirilgan holatda (avvalgi
xatti-harakat: o'zgarishlar qayta deploy'da yo'qoladi).

## Xavfsizlik chegaralari

1. `ADMIN_IDS` ro'yxati va `main.py` / `plugin_loader.py` kodi AI Creator
   tomonidan hech qachon o'zgartirilmaydi — bu yadroning o'zgarmas qismi.
2. AI faqat `plugins/` papkasi ICHIDA yangi `*.py` fayl yozishi mumkin,
   va bu ham faqat admin "✅ Tasdiqlash" tugmasini bosgandan so'ng.
3. Har bir taklif qilingan kod avval AST orqali statik tekshiriladi
   (xavfli chaqiriqlar avtomatik rad etiladi).

## O'rnatish

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # so'ng .env ichiga tokenlaringizni yozing
```

`.env` faylini yuklash uchun `main.py` boshida `os.getenv(...)` ishlatiladi;
xohlasangiz `python-dotenv` qo'shib, `load_dotenv()` chaqirishingiz mumkin,
yoki muhit o'zgaruvchilarini to'g'ridan-to'g'ri serverga eksport qiling:

```bash
export BOT_TOKEN=...
export GEMINI_API_KEY=...
```

## Ishga tushirish

```bash
python main.py
```

## Foydalanish (Admin uchun)

1. Botda ☰ Menu → asosiy menyu ochiladi.
2. `⚙️ Admin Panel` (faqat `ADMIN_IDS` uchun ko'rinadi) → `🤖 AI Creator`.
3. Kerakli funksiyani so'zlab tushuntiring (Chatting Phase).
4. `Start` deb yozing — AI kod loyihasini tayyorlaydi (Drafting Phase).
5. Kodni ko'rib chiqing → `✅ Tasdiqlash` yoki `❌ Rad etish`.
6. Tasdiqlangan plugin darhol `plugins/`ga saqlanadi va yuklanadi.
7. Kerak bo'lsa `↩️ Undo` / `↪️ Redo` orqali oxirgi o'zgarishni
   bekor qiling yoki qayta tiklang (to'liq kuchga kirishi uchun botni
   qayta ishga tushirish tavsiya etiladi).

## Admin ID'ni o'zgartirish

`main.py` ichidagi `ADMIN_IDS` to'plamini qo'lda tahrirlang:

```python
ADMIN_IDS: set[int] = {6926668577}
```
