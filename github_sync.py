"""
github_sync.py — Bot yadrosining bir qismi (O'ZGARMAS / IMMUTABLE)
===================================================================
Railway odatda GitHub repoga ulangan holda ishlaydi (har bir push'da
avtomatik qayta deploy qiladi), lekin bot ishga tushgandan keyin
plugins/ va history/ ichiga yozadigan fayllar FAQAT konteynerning
lokal diskida qoladi — GitHub'ga hech qachon push bo'lmaydi. Natijada
keyingi deploy'da (yoki konteyner qayta ishga tushganda) barcha AI
tomonidan yaratilgan pluginlar yo'qoladi, chunki Railway qayta GitHub'dan
eski kodni tortib oladi.

Ushbu modul git buyruqlariga muhtoj emas (Railway konteynerida `git`
binari bo'lmasligi mumkin) — buning o'rniga GitHub REST "Contents API"
orqali fayllarni to'g'ridan-to'g'ri repoga commit qiladi:
    https://docs.github.com/en/rest/repos/contents

Kerakli muhit o'zgaruvchilari (Railway → Variables):
    GITHUB_TOKEN   — "repo" huquqiga ega Personal Access Token
    GITHUB_REPO    — "egasi/repo-nomi" ko'rinishida
    GITHUB_BRANCH  — ixtiyoriy, standart: "main"

Agar GITHUB_TOKEN yoki GITHUB_REPO o'rnatilmagan bo'lsa, sinxronizatsiya
sokin (xatosiz) o'chirilgan holda ishlaydi — bot GitHub'siz ham ishlayveradi,
faqat o'zgarishlar qayta deploy'da saqlanmaydi (avvalgi xatti-harakat).

DIQQAT: bu fayl ham yadroning bir qismi — AI Creator uni o'zgartira olmaydi.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger("github_sync")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")  # masalan: "username/repo-name"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

API_ROOT = "https://api.github.com"
TIMEOUT = 15  # sekund

SYNC_ENABLED = bool(GITHUB_TOKEN and GITHUB_REPO)

if not SYNC_ENABLED:
    logger.warning(
        "GITHUB_TOKEN va/yoki GITHUB_REPO o'rnatilmagan — GitHub sinxronizatsiyasi "
        "O'CHIRILGAN. Pluginlar faqat lokal diskda saqlanadi va qayta deploy'da "
        "yo'qoladi. Yoqish uchun Railway Variables'ga GITHUB_TOKEN va GITHUB_REPO "
        "qo'shing."
    )


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_file_sha(repo_path: str) -> Optional[str]:
    """Repo'dagi faylning joriy `sha` qiymatini qaytaradi (mavjud bo'lsa)."""
    url = f"{API_ROOT}/repos/{GITHUB_REPO}/contents/{repo_path}"
    resp = requests.get(
        url, headers=_headers(), params={"ref": GITHUB_BRANCH}, timeout=TIMEOUT
    )
    if resp.status_code == 200:
        return resp.json().get("sha")
    if resp.status_code == 404:
        return None
    logger.error("GitHub GET xatosi (%s): %s %s", repo_path, resp.status_code, resp.text)
    return None


def push_file(repo_path: str, content: str, message: str) -> bool:
    """
    `content`ni repo ichidagi `repo_path` manziliga commit qiladi
    (fayl mavjud bo'lsa — yangilaydi, bo'lmasa — yaratadi).
    Muvaffaqiyatli bo'lsa True, aks holda False qaytaradi (bot ishini
    to'xtatmaydi — faqat xatoni logga yozadi).
    """
    if not SYNC_ENABLED:
        return False
    try:
        sha = _get_file_sha(repo_path)
        url = f"{API_ROOT}/repos/{GITHUB_REPO}/contents/{repo_path}"
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        resp = requests.put(url, headers=_headers(), json=payload, timeout=TIMEOUT)
        if resp.status_code in (200, 201):
            logger.info("GitHub'ga push qilindi: %s", repo_path)
            return True
        logger.error("GitHub PUT xatosi (%s): %s %s", repo_path, resp.status_code, resp.text)
        return False
    except requests.RequestException as e:
        logger.error("GitHub'ga ulanishda xato (%s): %s", repo_path, e)
        return False


def delete_file(repo_path: str, message: str) -> bool:
    """Repo'dagi faylni o'chiradi (mavjud bo'lsa)."""
    if not SYNC_ENABLED:
        return False
    try:
        sha = _get_file_sha(repo_path)
        if sha is None:
            return True  # allaqachon yo'q — bajarildi deb hisoblaymiz
        url = f"{API_ROOT}/repos/{GITHUB_REPO}/contents/{repo_path}"
        payload = {"message": message, "sha": sha, "branch": GITHUB_BRANCH}
        resp = requests.delete(url, headers=_headers(), json=payload, timeout=TIMEOUT)
        if resp.status_code == 200:
            logger.info("GitHub'dan o'chirildi: %s", repo_path)
            return True
        logger.error("GitHub DELETE xatosi (%s): %s %s", repo_path, resp.status_code, resp.text)
        return False
    except requests.RequestException as e:
        logger.error("GitHub'dan o'chirishda xato (%s): %s", repo_path, e)
        return False
