"""همه مقادیر حساس از .env خونده میشن، هیچ‌وقت نباید تو فرانت‌اند یا کد بیان."""
import os
from dotenv import load_dotenv

load_dotenv()

# تا ۳ کلید Groq می‌تونی بدی، llm_client بینشون می‌چرخه تا دیرتر ریت‌لیمیت بخوریم
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_KEY_2 = os.environ.get("GROQ_API_KEY_2", "")
GROQ_API_KEY_3 = os.environ.get("GROQ_API_KEY_3", "")
GROQ_API_KEYS = [k.strip() for k in [GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3] if k and k.strip()]
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# اگه بهترین نتیجه بازیابی از این کمتر باشه، به‌جای حدس زدن میگیم «در منابع نیست».
# با embedding چندزبانه‌ی فعلی ۰.۲ خوب جواب میده؛ اگه جواب‌های درست فیلتر میشن
# تو .env کمش کن (مثلاً 0.12)، اگه جواب نامربوط برمی‌گرده زیادش کن.
MIN_RELEVANCE_SCORE = float(os.environ.get("MIN_RELEVANCE_SCORE", 0.2))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "data", "uploads")
CHROMA_DIR = os.path.join(BASE_DIR, "data", "chroma")

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 900))       # کاراکتر در هر چانک
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 150))  # همپوشانی بین چانک‌ها
TOP_K_DEFAULT = int(os.environ.get("TOP_K_DEFAULT", 4))
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", 25 * 1024 * 1024))  # ۲۵ مگ

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)

DB_PATH = os.path.join(BASE_DIR, "data", "student_assistant.db")

