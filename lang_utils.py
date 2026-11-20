"""تشخیص فارسی/انگلیسی - فقط برای اینکه پیام‌های سیستمی زبان درست رو نشون بدن، نه یه language detector دقیق."""
import re

_PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")


def detect_lang(text: str) -> str:
    """خروجی: 'fa' یا 'en'"""
    if not text:
        return "fa"
    persian_chars = len(_PERSIAN_RE.findall(text))
    return "fa" if persian_chars > 0 else "en"


MESSAGES = {
    "no_documents": {
        "fa": (
            "📂 هنوز هیچ منبعی آپلود نکرده‌اید.\n\n"
            "این دستیار فقط بر اساس فایل‌هایی که شما آپلود می‌کنید پاسخ می‌دهد و از "
            "دانش عمومی خودش استفاده نمی‌کند. لطفاً ابتدا از بخش «منابع درسی» یک فایل "
            "PDF آپلود کنید، سپس سؤال بپرسید."
        ),
        "en": (
            "📂 You haven't uploaded any sources yet.\n\n"
            "This assistant only answers based on the files you upload — it will not use "
            "its own general knowledge. Please upload a PDF from the \"Resources\" section "
            "first, then ask your question."
        ),
    },
    "not_in_sources": {
        "fa": (
            "🤷 نمی‌دانم — این موضوع در منابعی که آپلود کرده‌اید پیدا نشد. "
            "لطفاً منبع مرتبط دیگری آپلود کنید یا سؤال را به‌گونه‌ای دیگر بپرسید."
        ),
        "en": (
            "🤷 I don't know — this isn't covered in the sources you've uploaded. "
            "Please upload a more relevant source or rephrase your question."
        ),
    },
}


def get_message(key: str, lang: str) -> str:
    return MESSAGES.get(key, {}).get(lang) or MESSAGES.get(key, {}).get("fa", "")
