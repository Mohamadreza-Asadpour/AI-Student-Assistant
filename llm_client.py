"""کلاینت Groq (فرمت OpenAI Chat Completions). کلیدها فقط سمت سرور می‌مونن.
اگه چند کلید تو .env باشه (GROQ_API_KEY, _2, _3)، round-robin بینشون می‌چرخیم و
اگه یکی 429 یا هر خطای دیگه بده، همون درخواست با کلید بعدی دوباره امتحان میشه."""
import itertools
import json
import re
import threading

import requests

import config

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_key_cycle_lock = threading.Lock()
_key_cycle = itertools.cycle(config.GROQ_API_KEYS) if config.GROQ_API_KEYS else None


class LLMNotConfiguredError(Exception):
    pass


class LLMRateLimitedError(Exception):
    """همه کلیدها ریت‌لیمیت خوردن."""
    pass


def _next_key() -> str:
    with _key_cycle_lock:
        return next(_key_cycle)


def call_llm(system_prompt: str, user_message: str = None, messages: list = None,
             max_tokens: int = 1200) -> str:
    """یا user_message بده یا messages (تاریخچه کامل)، هر دو که خالی باشن ارور میده."""
    if not config.GROQ_API_KEYS:
        raise LLMNotConfiguredError(
            "هیچ GROQ_API_KEY تنظیم نشده است. آن را در فایل .env قرار دهید."
        )

    if messages is None:
        if user_message is None:
            raise ValueError("یکی از user_message یا messages باید مقداردهی شود.")
        messages = [{"role": "user", "content": user_message}]

    # Groq مثل OpenAI سیستم‌پرامپت رو تو همون آرایه messages می‌خواد، نه فیلد جدا
    full_messages = [{"role": "system", "content": system_prompt}, *messages]
    payload = {
        "model": config.GROQ_MODEL,
        "max_tokens": max_tokens,
        "messages": full_messages,
    }

    num_keys = len(config.GROQ_API_KEYS)
    last_error = None

    for attempt in range(num_keys):
        key = _next_key()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        except requests.exceptions.RequestException as e:
            last_error = e
            continue

        # اولش فقط رو 429 سوییچ می‌کردیم، ولی اگه یه کلید expired/نامعتبر باشه
        # Groq بهش 401/403 میده و کل request شکست می‌خورد با اینکه کلیدای دیگه سالمن.
        # پس حالا رو هر خطای غیر 2xx میریم سراغ کلید بعدی.
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:300]
            last_error = requests.exceptions.HTTPError(
                f"{resp.status_code} از کلید #{attempt + 1} از {num_keys}: {detail}", response=resp
            )
            continue

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()

    if isinstance(last_error, requests.exceptions.HTTPError) and last_error.response is not None \
            and last_error.response.status_code == 429:
        raise LLMRateLimitedError(
            f"همه‌ی {num_keys} کلید API به محدودیت نرخ (Rate Limit) خورده‌اند. کمی صبر کنید یا "
            "کلید جدید اضافه کنید."
        )
    raise last_error or RuntimeError("خطای نامشخص در فراخوانی مدل زبانی.")


def call_llm_json(system_prompt: str, user_message: str, max_tokens: int = 2000):
    """وقتی خروجی JSON لازم داریم: آزمون، برنامه مطالعه، ارزیابی پاسخ."""
    raw = call_llm(system_prompt, user_message=user_message, max_tokens=max_tokens)
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"خروجی مدل JSON معتبر نبود: {e}\nخروجی خام: {raw[:500]}")


def build_rag_system_prompt(level: str = "متوسط") -> str:
    return (
        "تو یک دستیار آموزشی هستی که فقط بر اساس «منابع ارائه‌شده» به دانشجو پاسخ می‌دهی.\n"
        f"سطح دانشجو: {level} (پاسخ را متناسب با این سطح ساده یا فنی بیان کن).\n"
        "قوانین:\n"
        "۱. فقط از اطلاعات موجود در بخش «منابع» استفاده کن.\n"
        "۲. اگر پاسخ در منابع نبود، صریح بگو «در منابع ارائه‌شده اطلاعات کافی برای پاسخ قطعی وجود ندارد» "
        "و در صورت امکان یک راهنمایی کلی (نه قطعی) ارائه بده.\n"
        "۳. در پایان پاسخ، نام فایل و شماره صفحه‌ی منبعی که از آن استفاده کردی را ذکر کن.\n"
        "۴. مفاهیم سخت را با مثال ساده توضیح بده.\n"
    )
