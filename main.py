"""
main.py — Self-Evolving Modular Telegram Bot: YADRO (CORE, O'ZGARMAS)
=======================================================================
DIQQAT — MUHIM XAVFSIZLIK SHARTI:
Ushbu fayl botning O'ZGARMAS yadrosi hisoblanadi. "AI Creator" moduli
(quyida shu faylning ichida joylashgan, chunki u yadroning bir qismi)
o'z-o'zini tahrirlay olmaydi va quyidagilarga HECH QACHON tega olmaydi:

    1) main.py va plugin_loader.py fayllarining kodi/mantig'i
    2) ADMIN_IDS ro'yxati
    3) Xavfsizlik tekshiruvlari (plugin_loader.validate_plugin_source)

AI Creator FAQAT plugins/ papkasi ICHIDAGI yangi *.py fayllarni yozishi
mumkin, va bu ham faqat admin "✅ Tasdiqlash" tugmasini bosgandan so'ng,
plugin_loader.save_plugin_file() orqali (bu funksiya AST asosida xavfli
kodni avtomatik rad etadi) amalga oshadi.

Arxitektura:
    main.py            -> yadro: menyular, admin panel, AI Creator FSM,
                           Undo/Redo, versiyalash
    plugin_loader.py    -> yadro: pluginlarni xavfsiz dinamik yuklash
    plugins/*.py        -> AI tomonidan yaratiladigan/o'zgaruvchan modullar
    history/*           -> har bir tasdiqlangan plugin versiyasi arxivi
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
)

import github_sync
import plugin_loader

# ============================================================
# 1) O'ZGARMAS SOZLAMALAR (IMMUTABLE CONFIG)
# ============================================================
# Admin ID'lari — bu ro'yxatga AI Creator HECH QACHON tega olmaydi.
# O'zgartirish faqat dasturchi tomonidan qo'lda, shu faylni tahrirlash
# orqali amalga oshiriladi.
ADMIN_IDS: set[int] = {6926668577}

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Joriy modelni https://ai.google.dev/gemini-api/docs/models sahifasidan tekshiring —
# model nomlari vaqt o'tishi bilan yangilanib/eskirib turadi.
AI_MODEL = os.getenv("AI_CREATOR_MODEL", "gemini-2.5-flash")

BASE_DIR = Path(__file__).parent
HISTORY_DIR = BASE_DIR / "history"
HISTORY_INDEX_FILE = HISTORY_DIR / "_index.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("core")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ============================================================
# 2) REPLY KEYBOARD MENYULAR
# ============================================================
BTN_HELP = "ℹ️ Yordam"
BTN_PROFILE = "👤 Profil"
BTN_ADMIN_PANEL = "⚙️ Admin Panel"
BTN_AI_CREATOR = "🤖 AI Creator"
BTN_BACK = "🔙 Orqaga"
BTN_PLUGIN_LIST = "🧩 Pluginlar"
BTN_UNDO = "↩️ Undo"
BTN_REDO = "↪️ Redo"


def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    """
    Asosiy foydalanuvchi menyusi. Bu funksiya AI Creator tomonidan
    kengaytirilishi/qayta loyihalanishi mumkin bo'lgan qismdir —
    lekin admin tugmasi ko'rinishi mantiqi yadroda qat'iy saqlanadi.
    """
    rows = [[KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_HELP)]]
    if is_admin(user_id):
        rows.append([KeyboardButton(text=BTN_ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def admin_panel_menu() -> ReplyKeyboardMarkup:
    """Faqat ADMIN_IDS ichidagi foydalanuvchilarga ko'rsatiladigan panel."""
    rows = [
        [KeyboardButton(text=BTN_AI_CREATOR)],
        [KeyboardButton(text=BTN_PLUGIN_LIST)],
        [KeyboardButton(text=BTN_UNDO), KeyboardButton(text=BTN_REDO)],
        [KeyboardButton(text=BTN_BACK)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def draft_review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="ai_apply"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data="ai_reject"),
            ]
        ]
    )


# ============================================================
# 3) FSM HOLATLARI — AI CREATOR (yadro qismi)
# ============================================================
class AICreatorStates(StatesGroup):
    chatting = State()      # a) Chatting Phase — vazifani aniqlashtirish
    drafting = State()      # AI kod yozmoqda
    awaiting_review = State()  # Admin Apply/Reject kutilmoqda


# ============================================================
# 4) VERSION CONTROL — history/ (Undo/Redo)
# ============================================================
def _load_history_index() -> dict:
    if HISTORY_INDEX_FILE.exists():
        return json.loads(HISTORY_INDEX_FILE.read_text(encoding="utf-8"))
    return {"stack": [], "redo_stack": []}


def _save_history_index(idx: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_INDEX_FILE.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def record_history(plugin_name: str, old_source: str | None, new_source: str) -> str:
    """Har bir tasdiqlangan (Apply) o'zgarishni history/ ga yozadi. Snapshot ts'ni qaytaradi."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    snap_dir = HISTORY_DIR / ts
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "new.py").write_text(new_source, encoding="utf-8")
    if old_source is not None:
        (snap_dir / "old.py").write_text(old_source, encoding="utf-8")

    idx = _load_history_index()
    idx["stack"].append({"ts": ts, "plugin": plugin_name, "had_old": old_source is not None})
    idx["redo_stack"] = []  # yangi o'zgarish redo tarixini tozalaydi
    _save_history_index(idx)
    return ts


def undo_last_change() -> tuple[str, str | None] | None:
    """
    Oxirgi tasdiqlangan o'zgarishni bekor qiladi.
    (plugin_name, tiklangan_manba_kod_yoki_None_agar_ochirilgan_bolsa) qaytaradi.
    """
    idx = _load_history_index()
    if not idx["stack"]:
        return None
    entry = idx["stack"].pop()
    snap_dir = HISTORY_DIR / entry["ts"]
    plugin_name = entry["plugin"]

    if entry["had_old"]:
        old_source = (snap_dir / "old.py").read_text(encoding="utf-8")
        plugin_loader.save_plugin_file(plugin_name, old_source)
        result_source = old_source
    else:
        plugin_loader.delete_plugin_file(plugin_name)
        result_source = None

    idx["redo_stack"].append(entry)
    _save_history_index(idx)
    return plugin_name, result_source


def redo_last_change() -> tuple[str, str] | None:
    """Undo qilingan oxirgi o'zgarishni qayta tiklaydi. (plugin_name, manba_kod) qaytaradi."""
    idx = _load_history_index()
    if not idx["redo_stack"]:
        return None
    entry = idx["redo_stack"].pop()
    snap_dir = HISTORY_DIR / entry["ts"]
    plugin_name = entry["plugin"]

    new_source = (snap_dir / "new.py").read_text(encoding="utf-8")
    plugin_loader.save_plugin_file(plugin_name, new_source)

    idx["stack"].append(entry)
    _save_history_index(idx)
    return plugin_name, new_source


# ============================================================
# 4b) GITHUB SINXRONIZATSIYA — yadro qismi
# ============================================================
# Railway GitHub repoga ulangan bo'lsa-da, plugins/ va history/ ichiga
# yozilgan fayllar avvalgi holatda faqat lokal diskda qolib, hech qachon
# repoga push bo'lmasdi. Shu funksiyalar har bir Apply/Undo/Redo'dan so'ng
# tegishli fayllarni GitHub Contents API orqali repoga commit qiladi —
# shunda keyingi Railway deploy'i ham AI yaratgan pluginlarni o'z ichiga oladi.
#
# ESLATMA: Railway shu branchni kuzatib turgani uchun bu push avtomatik
# qayta-deploy'ni ishga tushiradi (bot qisqa vaqtga qayta ishga tushadi) —
# bu kutilgan xatti-harakat, chunki bot shu tarzda "o'z-o'zini evolyutsiya
# qiladi" va GitHub'dagi holat doim ishlayotgan holat bilan mos keladi.
async def sync_plugin_to_github(plugin_name: str, source: str | None, action: str) -> None:
    """`source=None` bo'lsa faylni o'chiradi, aks holda yozadi/yangilaydi."""
    repo_path = f"plugins/{plugin_name}.py"
    message = f"AI Creator: {action} — plugins/{plugin_name}.py"
    if source is None:
        await asyncio.to_thread(github_sync.delete_file, repo_path, message)
    else:
        await asyncio.to_thread(github_sync.push_file, repo_path, source, message)


async def sync_history_snapshot_to_github(ts: str, plugin_name: str, old_source: str | None, new_source: str) -> None:
    """history/<ts>/new.py (va mavjud bo'lsa old.py) ni GitHub'ga yozadi."""
    await asyncio.to_thread(
        github_sync.push_file,
        f"history/{ts}/new.py",
        new_source,
        f"AI Creator: history snapshot — {plugin_name} ({ts})",
    )
    if old_source is not None:
        await asyncio.to_thread(
            github_sync.push_file,
            f"history/{ts}/old.py",
            old_source,
            f"AI Creator: history snapshot (old) — {plugin_name} ({ts})",
        )


async def sync_history_index_to_github() -> None:
    """history/_index.json ni GitHub'ga yozadi (Undo/Redo stekini saqlash uchun)."""
    if HISTORY_INDEX_FILE.exists():
        content = HISTORY_INDEX_FILE.read_text(encoding="utf-8")
        await asyncio.to_thread(
            github_sync.push_file, "history/_index.json", content, "AI Creator: history index yangilandi"
        )


# ============================================================
# 5) AI CREATOR — Chatting & Drafting mexanizmi (yadro qismi)
# ============================================================
AI_CREATOR_SYSTEM_PROMPT = """Sen Telegram bot uchun aiogram 3.x asosida plugin yozuvchi AI mухandissan.
QATʼIY QOIDALAR:
- Faqat BITTA python fayli tarkibini yoz — bu fayl plugins/<nom>.py sifatida saqlanadi.
- Faylda albatta `router = Router()` obyekti va shu routerga ulangan handlerlar bo'lishi kerak.
- HECH QACHON os.system, subprocess, eval, exec, __import__, ctypes ishlatma.
- ADMIN_IDS ro'yxatiga yoki botning boshqa yadro fayllariga (main.py, plugin_loader.py) hech qanday tarzda murojaat qilma yoki ularni o'zgartirishni taklif qilma.
- Javobingni FAQAT quyidagi formatda ber:
FILENAME: <plugin_fayl_nomi_py_siz>
```python
<to'liq python kodi>
```
"""


async def call_ai_creator(conversation: list[dict]) -> tuple[str, str]:
    """
    Gemini API (google-genai SDK) orqali plugin kodi generatsiya qiladi.
    Qaytaradi: (plugin_nomi, python_kodi)

    O'rnatish: pip install google-genai
    Muhit o'zgaruvchisi: export GEMINI_API_KEY=AIza...
    """
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY o'rnatilmagan. Muhit o'zgaruvchisini sozlang:\n"
            "export GEMINI_API_KEY=AIza..."
        )

    from google import genai
    from google.genai import types

    def _generate() -> str:
        client = genai.Client(api_key=GEMINI_API_KEY)
        chat = client.chats.create(
            model=AI_MODEL,
            config=types.GenerateContentConfig(system_instruction=AI_CREATOR_SYSTEM_PROMPT),
        )
        last_text = ""
        for turn in conversation:
            response = chat.send_message(turn["content"])
            last_text = response.text or ""
        return last_text

    # google-genai kutubxonasi sinxron ishlaydi, shuning uchun uni alohida
    # thread'da chaqiramiz — bot event loop'ini bloklamaslik uchun.
    text = await asyncio.to_thread(_generate)

    if "FILENAME:" not in text or "```" not in text:
        raise ValueError("AI javobi kutilgan formatda emas (FILENAME: va kod bloki topilmadi)")

    filename_line = text.split("FILENAME:")[1].split("\n")[0].strip()
    plugin_name = filename_line.replace(".py", "").strip()
    code = text.split("```python")[1].split("```")[0] if "```python" in text else text.split("```")[1].split("```")[0]
    return plugin_name, code.strip()


# ============================================================
# 6) ROUTER — YADRO HANDLERLARI
# ============================================================
core_router = Router(name="core")


@core_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Assalomu alaykum! Bot ishga tushdi.\n"
        "Barcha bo'limlar pastdagi Menu (☰) tugmasi orqali ochiladi.",
        reply_markup=main_menu(message.from_user.id),
    )


@core_router.message(F.text == BTN_HELP)
async def handle_help(message: Message):
    await message.answer("Bu — Self-Evolving Modular Bot. Menyudan kerakli bo'limni tanlang.")


@core_router.message(F.text == BTN_PROFILE)
async def handle_profile(message: Message):
    await message.answer(f"👤 ID: <code>{message.from_user.id}</code>")


@core_router.message(F.text == BTN_ADMIN_PANEL)
async def handle_admin_panel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return  # oddiy foydalanuvchiga bu tugma umuman ko'rinmaydi
    await state.clear()
    await message.answer("⚙️ Admin Panel", reply_markup=admin_panel_menu())


@core_router.message(F.text == BTN_BACK)
async def handle_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Asosiy menyu", reply_markup=main_menu(message.from_user.id))


@core_router.message(F.text == BTN_PLUGIN_LIST)
async def handle_plugin_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    plugins = plugin_loader.list_plugin_files()
    text = "🧩 O'rnatilgan pluginlar:\n" + ("\n".join(f"• {p}" for p in plugins) if plugins else "— hozircha yo'q —")
    await message.answer(text)


@core_router.message(F.text == BTN_UNDO)
async def handle_undo(message: Message):
    if not is_admin(message.from_user.id):
        return
    result = undo_last_change()
    if result:
        plugin, restored_source = result
        await sync_plugin_to_github(plugin, restored_source, "Undo")
        await sync_history_index_to_github()
        await message.answer(f"↩️ Bekor qilindi: plugins/{plugin}.py oldingi holatga qaytarildi.\n⚠️ To'liq kuchga kirishi uchun botni qayta ishga tushiring.")
    else:
        await message.answer("Bekor qilinadigan o'zgarish topilmadi.")


@core_router.message(F.text == BTN_REDO)
async def handle_redo(message: Message):
    if not is_admin(message.from_user.id):
        return
    result = redo_last_change()
    if result:
        plugin, restored_source = result
        await sync_plugin_to_github(plugin, restored_source, "Redo")
        await sync_history_index_to_github()
        await message.answer(f"↪️ Qayta tiklandi: plugins/{plugin}.py.\n⚠️ To'liq kuchga kirishi uchun botni qayta ishga tushiring.")
    else:
        await message.answer("Qayta tiklanadigan o'zgarish topilmadi.")


# ---------- AI Creator: Chatting Phase ----------
@core_router.message(F.text == BTN_AI_CREATOR)
async def handle_ai_creator_entry(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return  # AI Creator faqat Admin Panel ichida va faqat adminlarga
    await state.set_state(AICreatorStates.chatting)
    await state.update_data(conversation=[])
    await message.answer(
        "🤖 AI Creator ishga tushdi.\n\n"
        "Menga qanday plugin/funksiya kerakligini yozing (masalan: "
        "\"foydalanuvchilarga ob-havo ko'rsatadigan tugma qo'sh\").\n"
        "Suhbat davomida vazifani aniqlashtiramiz.\n\n"
        "Tayyor bo'lganda — <b>Start</b> deb yozing, men kodni yozib beraman.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Start")], [KeyboardButton(text=BTN_BACK)]],
            resize_keyboard=True,
        ),
    )


@core_router.message(StateFilter(AICreatorStates.chatting), F.text.lower() == "start")
async def handle_ai_creator_start_drafting(message: Message, state: FSMContext):
    data = await state.get_data()
    conversation = data.get("conversation", [])
    if not conversation:
        await message.answer("Avval menga qanday plugin kerakligini yozib bering.")
        return

    await state.set_state(AICreatorStates.drafting)
    await message.answer("⏳ Kod yozilmoqda...")

    try:
        plugin_name, code = await call_ai_creator(conversation)
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}")
        await state.set_state(AICreatorStates.chatting)
        return

    await state.update_data(draft_plugin_name=plugin_name, draft_code=code)
    await state.set_state(AICreatorStates.awaiting_review)

    preview = code if len(code) < 3500 else code[:3500] + "\n... (qisqartirildi)"
    await message.answer(
        f"📄 Taklif etilgan plugin: <code>plugins/{plugin_name}.py</code>\n\n"
        f"<pre>{preview}</pre>",
        reply_markup=draft_review_kb(),
    )


@core_router.message(StateFilter(AICreatorStates.chatting))
async def handle_ai_creator_chat(message: Message, state: FSMContext):
    data = await state.get_data()
    conversation = data.get("conversation", [])
    conversation.append({"role": "user", "content": message.text})
    await state.update_data(conversation=conversation)
    await message.answer("Tushunarli. Yana qo'shimcha talab bormi? Tayyor bo'lsa — <b>Start</b> deb yozing.")


# ---------- AI Creator: Human-in-the-loop (Apply / Reject) ----------
@core_router.callback_query(F.data == "ai_apply", StateFilter(AICreatorStates.awaiting_review))
async def handle_ai_apply(call: CallbackQuery, state: FSMContext, dispatcher: Dispatcher):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return

    data = await state.get_data()
    plugin_name = data["draft_plugin_name"]
    code = data["draft_code"]

    old_source = plugin_loader.read_plugin_file(plugin_name) or None
    try:
        plugin_loader.save_plugin_file(plugin_name, code)
    except ValueError as e:
        await call.message.edit_text(f"❌ Xavfsizlik tekshiruvidan o'tmadi:\n{e}")
        await state.set_state(AICreatorStates.chatting)
        await call.answer()
        return

    ts = record_history(plugin_name, old_source, code)
    plugin_loader.reload_plugin(dispatcher, plugin_name)

    action = "yangilandi" if old_source else "yaratildi"
    await sync_plugin_to_github(plugin_name, code, action)
    await sync_history_snapshot_to_github(ts, plugin_name, old_source, code)
    await sync_history_index_to_github()

    if github_sync.SYNC_ENABLED:
        await call.message.edit_text(
            f"✅ Tasdiqlandi va o'rnatildi: <code>plugins/{plugin_name}.py</code>\n"
            f"📤 GitHub repoga push qilindi."
        )
    else:
        await call.message.edit_text(
            f"✅ Tasdiqlandi va o'rnatildi: <code>plugins/{plugin_name}.py</code>\n"
            f"⚠️ GitHub sinxronizatsiyasi o'chirilgan (GITHUB_TOKEN/GITHUB_REPO "
            f"sozlanmagan) — o'zgarish qayta deploy'da yo'qolishi mumkin."
        )
    await state.clear()
    await call.message.answer("Admin Panel", reply_markup=admin_panel_menu())
    await call.answer()


@core_router.callback_query(F.data == "ai_reject", StateFilter(AICreatorStates.awaiting_review))
async def handle_ai_reject(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await call.message.edit_text("❌ Rad etildi. Kod saqlanmadi.")
    await state.set_state(AICreatorStates.chatting)
    await call.message.answer("Yana talablarni yozishingiz mumkin, yoki <b>Start</b> deb qayta urinib ko'ring.")
    await call.answer()


# ============================================================
# 7) ISHGA TUSHIRISH
# ============================================================
async def set_menu_button(bot: Bot) -> None:
    """Telegram interfeysidagi ☰ Menu tugmasini sozlaydi."""
    await bot.set_my_commands([BotCommand(command="start", description="Botni ishga tushirish")])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main() -> None:
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        logger.warning("BOT_TOKEN muhit o'zgaruvchisi orqali o'rnatilmagan!")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(core_router)

    # plugins/ papkasidagi barcha mavjud pluginlarni yuklash
    plugin_loader.ensure_plugins_dir()
    loaded = plugin_loader.load_all_plugins(dp)
    logger.info("Yuklangan pluginlar: %s", loaded)

    await set_menu_button(bot)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
