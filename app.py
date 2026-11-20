import os
import traceback

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

import config
import db
import lang_utils
from rag_engine import engine
from llm_client import call_llm, call_llm_json, LLMNotConfiguredError, LLMRateLimitedError
import prompts

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_SIZE

ALLOWED_EXTENSIONS = {"pdf"}

db.init_db()


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def current_user_id() -> int:
    """بدون لاگین واقعی - از فیلد 'user' تو body/query استفاده می‌کنیم، نبود اون یعنی guest."""
    body = request.get_json(silent=True) or {}
    username = body.get("user") or request.args.get("user") or request.form.get("user") or "guest"
    return db.get_or_create_user(username)


def rag_context_text(hits: list) -> str:
    blocks = []
    for i, h in enumerate(hits, start=1):
        blocks.append(f"[منبع {i} | فایل: {h['source_file']} | صفحه: {h['page']}]\n{h['text']}")
    return "\n\n".join(blocks)


def require_documents_message(course: str, sample_text: str):
    """هیچی آپلود نشده؟ پیام «اول منبع بذار» رو به زبون مناسب برمی‌گردونه، وگرنه None."""
    if not engine.has_documents(course=course):
        lang = lang_utils.detect_lang(sample_text)
        return lang_utils.get_message("no_documents", lang)
    return None


def low_relevance_message(hits: list, sample_text: str):
    """اگه بهترین نتیجه اصلاً مرتبط نیست، بدون زحمت صدا زدن مدل میگیم «نمی‌دونم».
    این فقط یه فیلتر سریعه؛ تشخیص اصلی رو خود مدل طبق پرامپت انجام میده."""
    if not hits or hits[0]["relevance"] < config.MIN_RELEVANCE_SCORE:
        lang = lang_utils.detect_lang(sample_text)
        return lang_utils.get_message("not_in_sources", lang)
    return None


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "llm_configured": bool(config.GROQ_API_KEYS),
        "configured_keys": len(config.GROQ_API_KEYS),
    })


# آپلود PDF و فرستادنش به RAG engine
@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "فایلی ارسال نشده است (کلید 'file' موردنیاز است)."}), 400

    file = request.files["file"]
    course = request.form.get("course", "عمومی")

    if file.filename == "":
        return jsonify({"error": "نام فایل خالی است."}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "فقط فایل PDF پشتیبانی می‌شود."}), 400

    filename = secure_filename(file.filename)
    save_path = os.path.join(config.UPLOAD_DIR, filename)
    file.save(save_path)

    try:
        result = engine.ingest_file(save_path, filename, course=course)
        return jsonify({"message": "فایل با موفقیت پردازش و ذخیره شد.", **result}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در پردازش فایل: {e}"}), 500


@app.route("/api/documents", methods=["GET"])
def list_documents():
    return jsonify({"documents": engine.list_documents()})


@app.route("/api/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    engine.delete_document(doc_id)
    return jsonify({"message": "سند حذف شد."})


# فقط بازیابی، بدون صدا زدن مدل - برای تست خود retrieval
@app.route("/api/query", methods=["POST"])
def query():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    top_k = body.get("top_k")
    course = body.get("course")

    if not question:
        return jsonify({"error": "فیلد 'question' الزامی است."}), 400

    hits = engine.query(question, top_k=top_k, course=course)
    return jsonify({"question": question, "results": hits})


@app.route("/api/ask", methods=["POST"])
def ask():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    top_k = body.get("top_k")
    course = body.get("course")
    level = body.get("level", "متوسط")  # مبتدی | متوسط | پیشرفته

    if not question:
        return jsonify({"error": "فیلد 'question' الزامی است."}), 400

    no_docs_msg = require_documents_message(course, question)
    if no_docs_msg:
        return jsonify({"question": question, "answer": no_docs_msg, "sources": []})

    hits = engine.query(question, top_k=top_k, course=course)

    no_info_msg = low_relevance_message(hits, question)
    if no_info_msg:
        return jsonify({"question": question, "answer": no_info_msg, "sources": []})

    context_text = rag_context_text(hits)
    user_message = (
        f"منابع بازیابی‌شده:\n{context_text}\n\n"
        f"سؤال دانشجو: {question}\n\n"
        "بر اساس فقط همین منابع پاسخ بده و در پایان بگو از کدام «منبع» (شماره) استفاده کردی."
    )

    system_prompt = prompts.chat_system_prompt(level=level)

    try:
        answer = call_llm(system_prompt, user_message)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({
            "question": question,
            "answer": None,
            "warning": str(e),
            "sources": hits,
        }), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در فراخوانی مدل زبانی: {e}"}), 502

    return jsonify({
        "question": question,
        "answer": answer,
        "sources": [
            {"source_file": h["source_file"], "page": h["page"], "relevance": h["relevance"]}
            for h in hits
        ],
    })


# چت با تاریخچه
@app.route("/api/chat/send", methods=["POST"])
def chat_send():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    conversation_id = body.get("conversation_id")
    level = body.get("level", "متوسط")
    course = body.get("course")
    use_rag = body.get("use_rag", True)

    if not message:
        return jsonify({"error": "فیلد 'message' الزامی است."}), 400

    user_id = current_user_id()

    if not conversation_id:
        conversation_id = db.create_conversation(user_id, mode="chat", course=course, level=level)
    else:
        conv = db.get_conversation(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return jsonify({"error": "گفتگو یافت نشد."}), 404

    db.add_message(conversation_id, "user", message)

    # اگر هنوز هیچ منبعی آپلود نشده، قبل از تماس با مدل زبانی، همین را به کاربر بگو
    if use_rag:
        no_docs_msg = require_documents_message(course, message)
        if no_docs_msg:
            db.add_message(conversation_id, "assistant", no_docs_msg, sources=[])
            return jsonify({"conversation_id": conversation_id, "answer": no_docs_msg, "sources": []})

    # بازیابی منابع مرتبط (RAG)
    sources = []
    rag_block = ""
    hits = []
    if use_rag:
        hits = engine.query(message, course=course)
        no_info_msg = low_relevance_message(hits, message)
        if no_info_msg:
            db.add_message(conversation_id, "assistant", no_info_msg, sources=[])
            return jsonify({"conversation_id": conversation_id, "answer": no_info_msg, "sources": []})
        sources = [{"source_file": h["source_file"], "page": h["page"]} for h in hits]
        rag_block = f"\n\nمنابع:\n{rag_context_text(hits)}"

    # ساخت تاریخچه مکالمه برای مدل (حداکثر ۱۰ پیام آخر، به‌جز همین پیام که تازه ثبت شد)
    history = db.get_messages(conversation_id, limit=11)[:-1]
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message + rag_block})

    system_prompt = prompts.chat_system_prompt(level=level)
    try:
        answer = call_llm(system_prompt, messages=messages)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"conversation_id": conversation_id, "answer": None, "warning": str(e), "sources": sources})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در فراخوانی مدل زبانی: {e}"}), 502

    db.add_message(conversation_id, "assistant", answer, sources=sources)

    return jsonify({"conversation_id": conversation_id, "answer": answer, "sources": sources})


@app.route("/api/chat/history", methods=["GET"])
def chat_history():
    conversation_id = request.args.get("conversation_id", type=int)
    if not conversation_id:
        return jsonify({"error": "پارامتر 'conversation_id' الزامی است."}), 400
    return jsonify({"conversation_id": conversation_id, "messages": db.get_messages(conversation_id)})


@app.route("/api/chat/conversations", methods=["GET"])
def chat_conversations():
    user_id = current_user_id()
    return jsonify({"conversations": db.list_conversations(user_id)})


# حالت معلم: آموزش قدم‌به‌قدم
@app.route("/api/teacher/start", methods=["POST"])
def teacher_start():
    body = request.get_json(silent=True) or {}
    topic = (body.get("topic") or "").strip()
    level = body.get("level", "متوسط")
    course = body.get("course")

    if not topic:
        return jsonify({"error": "فیلد 'topic' الزامی است."}), 400

    user_id = current_user_id()
    conversation_id = db.create_conversation(user_id, mode="teacher", title=f"آموزش: {topic}",
                                              course=course, level=level)
    db.add_message(conversation_id, "user", f"[شروع آموزش] {topic}")

    no_docs_msg = require_documents_message(course, topic)
    if no_docs_msg:
        db.add_message(conversation_id, "assistant", no_docs_msg, sources=[])
        return jsonify({"conversation_id": conversation_id, "answer": no_docs_msg, "sources": []})

    hits = engine.query(topic, course=course)
    no_info_msg = low_relevance_message(hits, topic)
    if no_info_msg:
        db.add_message(conversation_id, "assistant", no_info_msg, sources=[])
        return jsonify({"conversation_id": conversation_id, "answer": no_info_msg, "sources": []})

    sources = [{"source_file": h["source_file"], "page": h["page"]} for h in hits]
    rag_block = f"\n\nمنابع درسی موجود:\n{rag_context_text(hits)}"

    user_message = f"موضوعی که باید آموزش بدهی: «{topic}»{rag_block}\n\nآموزش را از گام اول شروع کن."
    system_prompt = prompts.teacher_system_prompt(level=level)

    try:
        answer = call_llm(system_prompt, user_message=user_message)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"conversation_id": conversation_id, "answer": None, "warning": str(e), "sources": sources})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در فراخوانی مدل زبانی: {e}"}), 502

    db.add_message(conversation_id, "assistant", answer, sources=sources)
    return jsonify({"conversation_id": conversation_id, "answer": answer, "sources": sources})


@app.route("/api/teacher/continue", methods=["POST"])
def teacher_continue():
    body = request.get_json(silent=True) or {}
    conversation_id = body.get("conversation_id")
    message = (body.get("message") or "ادامه بده").strip()

    if not conversation_id:
        return jsonify({"error": "فیلد 'conversation_id' الزامی است."}), 400

    conv = db.get_conversation(conversation_id)
    if not conv:
        return jsonify({"error": "گفتگو یافت نشد."}), 404

    history = db.get_messages(conversation_id, limit=20)
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message})

    db.add_message(conversation_id, "user", message)

    system_prompt = prompts.teacher_system_prompt(level=conv.get("level", "متوسط"))
    try:
        answer = call_llm(system_prompt, messages=messages)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"conversation_id": conversation_id, "answer": None, "warning": str(e)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در فراخوانی مدل زبانی: {e}"}), 502

    db.add_message(conversation_id, "assistant", answer)
    return jsonify({"conversation_id": conversation_id, "answer": answer})


# آزمون: تولید سؤال از منابع + ثبت نتیجه
@app.route("/api/quiz/generate", methods=["POST"])
def quiz_generate():
    body = request.get_json(silent=True) or {}
    topic = (body.get("topic") or "").strip()
    course = body.get("course")
    level = body.get("level", "متوسط")
    num_questions = int(body.get("num_questions", 5))
    question_types = body.get("question_types", ["multiple_choice", "true_false"])

    if not topic:
        return jsonify({"error": "فیلد 'topic' الزامی است."}), 400

    user_id = current_user_id()

    no_docs_msg = require_documents_message(course, topic)
    if no_docs_msg:
        return jsonify({"error": no_docs_msg}), 404

    hits = engine.query(topic, top_k=max(num_questions, 5), course=course)
    no_info_msg = low_relevance_message(hits, topic)
    if no_info_msg:
        return jsonify({"error": no_info_msg}), 404

    content_text = rag_context_text(hits)
    user_message = (
        f"محتوای درسی:\n{content_text}\n\n"
        f"موضوع آزمون: {topic}\n"
        f"تعداد سؤالات: {num_questions}\n"
        f"انواع مجاز سؤال: {', '.join(question_types)}\n"
        "سؤالات را طبق ساختار JSON خواسته‌شده بساز."
    )
    system_prompt = prompts.quiz_generation_system_prompt(level=level)

    try:
        questions = call_llm_json(system_prompt, user_message, max_tokens=3000)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"error": str(e)}), 200
    except ValueError as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در تولید آزمون: {e}"}), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در فراخوانی مدل زبانی: {e}"}), 502

    if not isinstance(questions, list) or not questions:
        return jsonify({"error": "مدل سؤالی تولید نکرد."}), 502

    quiz_id = db.create_quiz(user_id, course=course, topic=topic, title=f"آزمون: {topic}", questions=questions)
    quiz = db.get_quiz(quiz_id)

    # پاسخ صحیح را برای نمایش اولیه‌ی آزمون به دانشجو حذف می‌کنیم (فقط هنگام تصحیح لازم است)
    safe_quiz = dict(quiz)
    safe_quiz["questions"] = [
        {k: v for k, v in q.items() if k not in ("correct_answer", "explanation")}
        for q in quiz["questions"]
    ]
    return jsonify({"quiz": safe_quiz}), 201


@app.route("/api/quiz/<int:quiz_id>", methods=["GET"])
def quiz_get(quiz_id):
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        return jsonify({"error": "آزمون یافت نشد."}), 404
    reveal = request.args.get("reveal_answers") == "1"
    if not reveal:
        quiz["questions"] = [
            {k: v for k, v in q.items() if k not in ("correct_answer", "explanation")}
            for q in quiz["questions"]
        ]
    return jsonify({"quiz": quiz})


@app.route("/api/quiz/list", methods=["GET"])
def quiz_list():
    user_id = current_user_id()
    return jsonify({"quizzes": db.list_quizzes(user_id)})


@app.route("/api/quiz/submit", methods=["POST"])
def quiz_submit():
    """تستی (multiple_choice/true_false) خودکار تصحیح میشه، essay میره پیش LLM برای نمره تقریبی."""
    body = request.get_json(silent=True) or {}
    quiz_id = body.get("quiz_id")
    submitted = body.get("answers", [])
    level = body.get("level", "متوسط")

    if not quiz_id or not submitted:
        return jsonify({"error": "فیلدهای 'quiz_id' و 'answers' الزامی هستند."}), 400

    quiz = db.get_quiz(quiz_id)
    if not quiz:
        return jsonify({"error": "آزمون یافت نشد."}), 404

    user_id = current_user_id()
    questions_by_id = {q["id"]: q for q in quiz["questions"]}

    graded_answers = []
    per_question_feedback = []

    for ans in submitted:
        qid = ans.get("question_id")
        student_answer = (ans.get("student_answer") or "").strip()
        q = questions_by_id.get(qid)
        if not q:
            continue

        if q["question_type"] in ("multiple_choice", "true_false"):
            is_correct = student_answer.strip() == (q["correct_answer"] or "").strip()
            graded_answers.append({
                "question_id": qid, "student_answer": student_answer,
                "is_correct": is_correct, "score": 1.0 if is_correct else 0.0,
                "feedback": q.get("explanation", ""),
            })
            per_question_feedback.append({
                "question_id": qid, "is_correct": is_correct,
                "correct_answer": q["correct_answer"], "explanation": q.get("explanation", ""),
            })
        else:  # essay -> ارزیابی با LLM
            eval_result = {"score": None, "mistakes": [], "explanation": "", "correct_answer": q.get("correct_answer", "")}
            try:
                eval_prompt = prompts.answer_evaluation_system_prompt(level=level)
                eval_msg = (
                    f"سؤال: {q['question_text']}\n"
                    f"پاسخ نمونه/معیار (در صورت وجود): {q.get('correct_answer', '(ندارد)')}\n"
                    f"پاسخ دانشجو: {student_answer}\n"
                )
                eval_result = call_llm_json(eval_prompt, eval_msg, max_tokens=800)
            except (LLMNotConfiguredError, LLMRateLimitedError):
                pass
            except Exception:
                traceback.print_exc()

            graded_answers.append({
                "question_id": qid, "student_answer": student_answer,
                "is_correct": None, "score": eval_result.get("score"),
                "feedback": eval_result.get("explanation", ""),
            })
            per_question_feedback.append({
                "question_id": qid, "score": eval_result.get("score"),
                "mistakes": eval_result.get("mistakes", []),
                "explanation": eval_result.get("explanation", ""),
                "correct_answer": eval_result.get("correct_answer") or q.get("correct_answer", ""),
            })

    result = db.save_quiz_attempt(quiz_id, user_id, graded_answers)
    return jsonify({**result, "feedback": per_question_feedback})


# خلاصه‌سازی
@app.route("/api/summary", methods=["POST"])
def summary():
    body = request.get_json(silent=True) or {}
    topic = (body.get("topic") or "").strip()
    doc_id = body.get("doc_id")
    course = body.get("course")
    level = body.get("level", "متوسط")

    if not topic and not doc_id:
        return jsonify({"error": "یکی از فیلدهای 'topic' یا 'doc_id' الزامی است."}), 400

    no_docs_msg = require_documents_message(course, topic)
    if no_docs_msg:
        return jsonify({"summary": None, "warning": no_docs_msg, "sources": []}), 200

    hits = engine.query(topic or "خلاصه کل مطلب", top_k=8, course=course)
    if doc_id:
        hits = [h for h in hits if h.get("source_file")] or hits  # فیلتر ساده در صورت نیاز گسترش می‌یابد

    no_info_msg = low_relevance_message(hits, topic)
    if no_info_msg:
        return jsonify({"summary": None, "warning": no_info_msg, "sources": []}), 200

    content_text = rag_context_text(hits)
    user_message = f"متن زیر را خلاصه کن:\n\n{content_text}"
    system_prompt = prompts.summary_system_prompt(level=level)

    try:
        result = call_llm(system_prompt, user_message=user_message)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"summary": None, "warning": str(e), "sources": hits}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در فراخوانی مدل زبانی: {e}"}), 502

    return jsonify({
        "summary": result,
        "sources": [{"source_file": h["source_file"], "page": h["page"]} for h in hits],
    })


# تحلیل نقاط ضعف
@app.route("/api/weakness", methods=["GET"])
def weakness():
    user_id = current_user_id()
    weak_topics = db.get_weak_topics(user_id)

    if not weak_topics:
        return jsonify({"weak_topics": [], "advice": "هنوز داده‌ی کافی از آزمون‌های قبلی برای تحلیل وجود ندارد."})

    level = request.args.get("level", "متوسط")
    topics_text = "\n".join(
        f"- {t['topic']}: {int(t['weakness_ratio']*100)}٪ خطا (از {t['total_answers']} سؤال)"
        for t in weak_topics
    )
    system_prompt = prompts.weakness_advice_system_prompt(level=level)
    try:
        advice = call_llm(system_prompt, user_message=f"موضوعات ضعیف دانشجو:\n{topics_text}")
    except (LLMNotConfiguredError, LLMRateLimitedError):
        advice = None
    except Exception:
        traceback.print_exc()
        advice = None

    return jsonify({"weak_topics": weak_topics, "advice": advice})


# برنامه مطالعه
@app.route("/api/planner", methods=["POST"])
def planner():
    body = request.get_json(silent=True) or {}
    course = (body.get("course") or "").strip()
    days_remaining = body.get("days_remaining")
    daily_hours = body.get("daily_hours")

    if not course or not days_remaining or not daily_hours:
        return jsonify({"error": "فیلدهای 'course'، 'days_remaining' و 'daily_hours' الزامی هستند."}), 400

    user_id = current_user_id()
    weak_topics = db.get_weak_topics(user_id)
    weak_text = (
        "\n".join(f"- {t['topic']} ({int(t['weakness_ratio']*100)}٪ خطا)" for t in weak_topics)
        if weak_topics else "داده‌ای از نقاط ضعف موجود نیست."
    )

    user_message = (
        f"درس: {course}\n"
        f"روزهای باقی‌مانده تا امتحان: {days_remaining}\n"
        f"ساعت مطالعه روزانه: {daily_hours}\n"
        f"نقاط ضعف قبلی دانشجو:\n{weak_text}\n"
    )
    system_prompt = prompts.study_planner_system_prompt()

    try:
        plan = call_llm_json(system_prompt, user_message, max_tokens=2500)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"error": str(e)}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در تولید برنامه: {e}"}), 502

    plan_id = db.save_study_plan(user_id, course, int(days_remaining), float(daily_hours), plan)
    return jsonify({"plan_id": plan_id, "plan": plan}), 201


# ارزیابی یه پاسخ تکی، خارج از آزمون رسمی
@app.route("/api/evaluate", methods=["POST"])
def evaluate():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    student_answer = (body.get("student_answer") or "").strip()
    reference_answer = body.get("reference_answer", "")
    level = body.get("level", "متوسط")

    if not question or not student_answer:
        return jsonify({"error": "فیلدهای 'question' و 'student_answer' الزامی هستند."}), 400

    user_message = (
        f"سؤال: {question}\n"
        f"پاسخ نمونه/معیار (در صورت وجود): {reference_answer or '(ندارد)'}\n"
        f"پاسخ دانشجو: {student_answer}\n"
    )
    system_prompt = prompts.answer_evaluation_system_prompt(level=level)

    try:
        result = call_llm_json(system_prompt, user_message, max_tokens=800)
    except (LLMNotConfiguredError, LLMRateLimitedError) as e:
        return jsonify({"error": str(e)}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"خطا در ارزیابی پاسخ: {e}"}), 502

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode, use_reloader=False)
