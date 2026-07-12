"""
plugin_loader.py — Bot yadrosining bir qismi (O'ZGARMAS / IMMUTABLE)
=====================================================================
DIQQAT: Ushbu fayl "AI Creator" moduli tomonidan HECH QACHON
o'zgartirilmaydi va yozilmaydi. Bu botning xavfsizlik yadrosiga oid
qism bo'lib, faqat dasturchi (inson) tomonidan qo'lda tahrirlanadi.

Vazifasi:
  - plugins/ papkasidagi *.py fayllarni dinamik (importlib) yuklash
  - Har bir plugin ichidan `router` (aiogram Router) obyektini topib,
    uni Dispatcher'ga ulash
  - AI yozgan kodni plugins/ ga saqlashdan OLDIN statik xavfsizlik
    tekshiruvidan (AST orqali) o'tkazish
  - Plugin fayllarini FAQAT plugins/ papkasi ICHIDA saqlash
    (path traversal / papkadan tashqariga chiqishni bloklash)

AI Creator faqat quyidagilarni bajara oladi:
  - plugins/<nom>.py fayllarini yaratish/yangilash (validate_plugin_source
    orqali tekshirilgandan so'ng)
AI Creator HECH QACHON quyidagilarga tega olmaydi:
  - main.py, plugin_loader.py fayllarining o'ziga
  - ADMIN_IDS ro'yxatiga
  - plugins/ papkasidan tashqaridagi har qanday faylga
"""

from __future__ import annotations

import ast
import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType

from aiogram import Dispatcher, Router

logger = logging.getLogger("plugin_loader")

BASE_DIR = Path(__file__).parent
PLUGINS_DIR = BASE_DIR / "plugins"
PLUGINS_PACKAGE = "plugins"

# AI yozgan kodda statik ravishda man etilgan modul/chaqiriqlar.
# Bu ro'yxat botning xavfsizlik siyosati bo'lib, AI uni o'zgartira olmaydi.
FORBIDDEN_MODULES = {"subprocess", "socket", "ctypes", "pty", "ftplib"}
FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_ATTR_CHAINS = {"os.system", "os.popen", "os.remove", "shutil.rmtree"}

_loaded_modules: dict[str, ModuleType] = {}


def ensure_plugins_dir() -> None:
    """plugins/ papkasi va __init__.py mavjudligini ta'minlaydi."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    init_file = PLUGINS_DIR / "__init__.py"
    if not init_file.exists():
        init_file.write_text("# plugins package (dinamik yuklanadi)\n", encoding="utf-8")


def validate_plugin_source(source: str) -> tuple[bool, str]:
    """
    AI tomonidan yozilgan kodni saqlashdan OLDIN tekshiradi.
    Xavfli chaqiriqlar/importlar topilsa (False, sabab) qaytaradi.
    Bu funksiya AI Creator tomonidan chaqiriladi, lekin O'ZI o'zgartirilmaydi.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"Sintaksis xatosi: {e}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod_names = (
                [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for m in mod_names:
                if m.split(".")[0] in FORBIDDEN_MODULES:
                    return False, f"Man etilgan modul importi: {m}"

        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None)
            if name in FORBIDDEN_CALL_NAMES:
                return False, f"Man etilgan chaqiriq: {name}"
            if isinstance(func, ast.Attribute):
                owner = getattr(func.value, "id", "")
                chain = f"{owner}.{func.attr}"
                if chain in FORBIDDEN_ATTR_CHAINS:
                    return False, f"Man etilgan chaqiriq: {chain}"

    if "ADMIN_IDS" in source:
        return False, "Plugin kodi ADMIN_IDS ro'yxatiga tegishi mumkin emas"

    return True, "OK"


def _safe_plugin_path(plugin_name: str) -> Path:
    """Plugin nomini plugins/ papkasi ICHIGA qat'iy cheklaydi."""
    safe_name = Path(plugin_name).name  # papka bo'laklarini olib tashlaydi
    if not safe_name or safe_name.startswith("."):
        raise ValueError("Noto'g'ri plugin nomi")
    if not safe_name.endswith(".py"):
        safe_name += ".py"
    if safe_name in {"main.py", "plugin_loader.py", "__init__.py"}:
        raise ValueError("Bu nom yadro fayllari uchun band qilingan")
    path = (PLUGINS_DIR / safe_name).resolve()
    if PLUGINS_DIR.resolve() not in path.parents:
        raise ValueError("Plugin papkadan tashqariga chiqishga urinish aniqlandi")
    return path


def save_plugin_file(plugin_name: str, source: str) -> Path:
    """Tasdiqlangan (Apply bosilgan) kodni FAQAT plugins/ ichiga yozadi."""
    ok, msg = validate_plugin_source(source)
    if not ok:
        raise ValueError(f"Xavfsizlik tekshiruvidan o'tmadi: {msg}")
    ensure_plugins_dir()
    path = _safe_plugin_path(plugin_name)
    path.write_text(source, encoding="utf-8")
    return path


def read_plugin_file(plugin_name: str) -> str:
    path = _safe_plugin_path(plugin_name)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def delete_plugin_file(plugin_name: str) -> None:
    path = _safe_plugin_path(plugin_name)
    if path.exists():
        path.unlink()
    sys.modules.pop(f"{PLUGINS_PACKAGE}.{path.stem}", None)


def list_plugin_files() -> list[str]:
    ensure_plugins_dir()
    return sorted(p.stem for p in PLUGINS_DIR.glob("*.py") if p.stem != "__init__")


def _import_or_reload(name: str) -> ModuleType:
    full_name = f"{PLUGINS_PACKAGE}.{name}"
    if full_name in sys.modules:
        module = importlib.reload(sys.modules[full_name])
    else:
        module = importlib.import_module(full_name)
    _loaded_modules[name] = module
    return module


def load_all_plugins(dp: Dispatcher) -> list[str]:
    """
    plugins/ papkasidagi barcha modullarni import qilib, ichidagi
    `router` obyektini Dispatcher'ga ulaydi. Bot ishga tushganda
    va har bir yangi plugin tasdiqlanganda chaqiriladi.
    """
    ensure_plugins_dir()
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    loaded = []
    for name in list_plugin_files():
        try:
            module = _import_or_reload(name)
            router = getattr(module, "router", None)
            if isinstance(router, Router):
                dp.include_router(router)
                loaded.append(name)
                logger.info("Plugin yuklandi: %s", name)
            else:
                logger.warning("Plugin '%s' ichida 'router' topilmadi, o'tkazib yuborildi", name)
        except Exception as e:
            logger.error("Plugin yuklashda xato (%s): %s", name, e)
    return loaded


def reload_plugin(dp: Dispatcher, name: str) -> ModuleType:
    """
    Bitta pluginni qayta yuklaydi va Dispatcher'ga qayta ulaydi.
    ESLATMA: aiogram 3.x'da Router'ni Dispatcher'dan "o'chirish" imkoni
    cheklangan — shu sababli Undo/Redo/Reject operatsiyalaridan so'ng
    to'liq tozalik uchun botni qayta ishga tushirish tavsiya etiladi.
    Shunga qaramay, yangi/yangilangan pluginlar shu funksiya orqali
    darhol ulanadi (mavjud xotiradagi eski router hali ham faol qolishi
    mumkin — restart shart).
    """
    module = _import_or_reload(name)
    router = getattr(module, "router", None)
    if isinstance(router, Router):
        dp.include_router(router)
    return module
