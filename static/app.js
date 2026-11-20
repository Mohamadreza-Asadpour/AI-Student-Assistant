const API = "";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// state سراسری - user/course/level تو localStorage نگه داشته میشن
const state = {
  user: localStorage.getItem("asa_user") || "guest",
  course: localStorage.getItem("asa_course") || "",
  level: localStorage.getItem("asa_level") || "متوسط",
  chatConversationId: null,
  teacherConversationId: null,
  currentQuiz: null,       // {id, questions:[...]}
  quizSubmitted: false,
};

function persistProfile() {
  localStorage.setItem("asa_user", state.user);
  localStorage.setItem("asa_course", state.course);
  localStorage.setItem("asa_level", state.level);
}

function escapeHtml(str) {
  return (str || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok && !data.warning) {
    throw new Error(data.error || `خطای سرور (${res.status})`);
  }
  return data;
}

function initProfileInputs() {
  $("#userInput").value = state.user;
  $("#courseInput").value = state.course;
  $("#levelInput").value = state.level;

  $("#userInput").addEventListener("change", (e) => { state.user = e.target.value.trim() || "guest"; persistProfile(); });
  $("#courseInput").addEventListener("change", (e) => { state.course = e.target.value.trim(); persistProfile(); });
  $("#levelInput").addEventListener("change", (e) => { state.level = e.target.value; persistProfile(); });
}

function initDarkMode() {
  const saved = localStorage.getItem("asa_dark") === "1";
  if (saved) document.body.classList.add("dark");
  $("#darkToggle").textContent = saved ? "☀️" : "🌙";
  $("#darkToggle").addEventListener("click", () => {
    const isDark = document.body.classList.toggle("dark");
    localStorage.setItem("asa_dark", isDark ? "1" : "0");
    $("#darkToggle").textContent = isDark ? "☀️" : "🌙";
  });
}

async function checkHealth() {
  const el = $("#serverStatus");
  try {
    const data = await api("/api/health");
    el.textContent = data.llm_configured
      ? "متصل به سرور — کلید هوش مصنوعی فعال"
      : "متصل به سرور — کلید API تنظیم نشده (فقط بازیابی منابع کار می‌کند)";
    el.className = "server-status " + (data.llm_configured ? "ok" : "warn");
  } catch {
    el.textContent = "عدم اتصال به سرور Backend";
    el.className = "server-status warn";
  }
}

function initModeNav() {
  $$(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchMode(btn.dataset.mode));
  });
}

function switchMode(mode) {
  $$(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${mode}`));
  if (mode === "resources") loadDocuments();
  if (mode === "weakness" && !$("#weaknessResult").dataset.loaded) loadWeakness();
}

// چت
const AVATARS = { user: "🧑", assistant: "🤖", system: "" };

function renderChatMessage(container, role, content, sources) {
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;

  if (role !== "system") {
    const avatar = document.createElement("div");
    avatar.className = `avatar ${role}-av`;
    avatar.textContent = AVATARS[role] || "";
    row.appendChild(avatar);
  }

  const bubble = document.createElement("div");
  bubble.className = "msg";
  bubble.innerHTML = escapeHtml(content).replace(/\n/g, "<br>");
  if (sources && sources.length) {
    const srcWrap = document.createElement("div");
    srcWrap.className = "sources";
    sources.forEach((s) => {
      const chip = document.createElement("span");
      chip.className = "source-chip";
      chip.textContent = `📌 ${s.source_file} — صفحه ${s.page}`;
      srcWrap.appendChild(chip);
    });
    bubble.appendChild(srcWrap);
  }
  row.appendChild(bubble);

  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
  return row;
}

function renderTypingIndicator(container) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.innerHTML = `
    <div class="avatar assistant-av">🤖</div>
    <div class="msg"><span class="typing-dots"><span></span><span></span><span></span></span></div>
  `;
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
  return row;
}

function clearEmptyState(container) {
  const empty = container.querySelector(".empty-state");
  if (empty) empty.remove();
}

async function sendChatMessage(e) {
  e.preventDefault();
  const input = $("#chatInput");
  const message = input.value.trim();
  if (!message) return;

  const win = $("#chatWindow");
  clearEmptyState(win);
  renderChatMessage(win, "user", message, null);
  input.value = "";

  const thinking = renderTypingIndicator(win);

  try {
    const data = await api("/api/chat/send", {
      method: "POST",
      body: JSON.stringify({
        user: state.user, message, level: state.level,
        course: state.course || undefined,
        conversation_id: state.chatConversationId,
      }),
    });
    thinking.remove();
    state.chatConversationId = data.conversation_id || state.chatConversationId;
    if (data.answer) {
      renderChatMessage(win, "assistant", data.answer, data.sources);
    } else if (data.warning) {
      renderChatMessage(win, "system", "⚠️ " + data.warning, null);
    }
  } catch (err) {
    thinking.remove();
    renderChatMessage(win, "system", "⚠️ " + err.message, null);
  }
}

function newChat() {
  state.chatConversationId = null;
  const win = $("#chatWindow");
  win.innerHTML = `<div class="empty-state"><div class="empty-icon">💬</div><strong>گفتگوی جدید</strong><p>یک سؤال درسی بنویس تا شروع کنیم.</p></div>`;
}

async function toggleHistoryDrawer() {
  const drawer = $("#historyDrawer");
  const isOpen = drawer.classList.toggle("open");
  if (!isOpen) return;
  drawer.innerHTML = `<div class="small muted">در حال بارگذاری...</div>`;
  try {
    const data = await api(`/api/chat/conversations?user=${encodeURIComponent(state.user)}`);
    if (!data.conversations.length) {
      drawer.innerHTML = `<div class="small muted">هنوز گفتگویی ثبت نشده.</div>`;
      return;
    }
    drawer.innerHTML = "";
    data.conversations.forEach((c) => {
      const item = document.createElement("div");
      item.className = "history-item";
      const date = new Date(c.created_at * 1000).toLocaleDateString("fa-IR");
      item.innerHTML = `<span>${c.title || "گفتگوی چت"} <span class="meta">(${c.message_count} پیام)</span></span><span class="meta">${date}</span>`;
      item.addEventListener("click", () => loadConversation(c.id));
      drawer.appendChild(item);
    });
  } catch (err) {
    drawer.innerHTML = `<div class="small muted">خطا در بارگذاری تاریخچه.</div>`;
  }
}

async function loadConversation(conversationId) {
  const win = $("#chatWindow");
  win.innerHTML = "";
  try {
    const data = await api(`/api/chat/history?conversation_id=${conversationId}`);
    state.chatConversationId = conversationId;
    if (!data.messages.length) {
      win.innerHTML = `<div class="empty-state"><div class="empty-icon">🗂</div><strong>این گفتگو خالی است</strong></div>`;
      return;
    }
    data.messages.forEach((m) => renderChatMessage(win, m.role, m.content, m.sources));
    $("#historyDrawer").classList.remove("open");
  } catch (err) {
    win.innerHTML = `<div class="empty-state"><div class="empty-icon">⚠️</div><strong>خطا در بارگذاری گفتگو</strong></div>`;
  }
}

// حالت معلم
async function startTeaching() {
  const topicInput = $("#teacherTopicInput");
  const topic = topicInput.value.trim();
  if (!topic) return;

  const win = $("#teacherWindow");
  win.innerHTML = "";
  const thinking = renderTypingIndicator(win);
  $("#teacherStartBtn").disabled = true;

  try {
    const data = await api("/api/teacher/start", {
      method: "POST",
      body: JSON.stringify({ user: state.user, topic, level: state.level, course: state.course || undefined }),
    });
    thinking.remove();
    state.teacherConversationId = data.conversation_id;
    if (data.answer) {
      renderChatMessage(win, "assistant", data.answer, data.sources);
      $("#teacherContinueForm").classList.remove("hidden");
    } else if (data.warning) {
      renderChatMessage(win, "system", "⚠️ " + data.warning, null);
    }
  } catch (err) {
    thinking.remove();
    renderChatMessage(win, "system", "⚠️ " + err.message, null);
  } finally {
    $("#teacherStartBtn").disabled = false;
  }
}

async function continueTeaching(e) {
  e.preventDefault();
  const input = $("#teacherContinueInput");
  const message = input.value.trim() || "ادامه بده";
  if (!state.teacherConversationId) return;

  const win = $("#teacherWindow");
  renderChatMessage(win, "user", message, null);
  input.value = "";
  const thinking = renderTypingIndicator(win);

  try {
    const data = await api("/api/teacher/continue", {
      method: "POST",
      body: JSON.stringify({ conversation_id: state.teacherConversationId, message }),
    });
    thinking.remove();
    if (data.answer) renderChatMessage(win, "assistant", data.answer, null);
    else if (data.warning) renderChatMessage(win, "system", "⚠️ " + data.warning, null);
  } catch (err) {
    thinking.remove();
    renderChatMessage(win, "system", "⚠️ " + err.message, null);
  }
}

// آزمون
async function generateQuiz() {
  const topic = $("#quizTopicInput").value.trim();
  const numQuestions = parseInt($("#quizNumInput").value, 10) || 5;
  const types = $$('#quizGenerateCard input[type=checkbox]:checked').map((c) => c.value);
  if (!topic) return;
  if (!types.length) { alert("حداقل یک نوع سؤال را انتخاب کن."); return; }

  const area = $("#quizArea");
  area.innerHTML = `<div class="result-box">در حال ساخت آزمون از روی منابع...</div>`;
  $("#quizGenerateBtn").disabled = true;

  try {
    const data = await api("/api/quiz/generate", {
      method: "POST",
      body: JSON.stringify({
        user: state.user, topic, course: state.course || undefined,
        level: state.level, num_questions: numQuestions, question_types: types,
      }),
    });
    if (data.error) {
      area.innerHTML = `<div class="result-box">⚠️ ${escapeHtml(data.error)}</div>`;
      return;
    }
    state.currentQuiz = data.quiz;
    state.quizSubmitted = false;
    renderQuiz();
  } catch (err) {
    area.innerHTML = `<div class="result-box">⚠️ ${escapeHtml(err.message)}</div>`;
  } finally {
    $("#quizGenerateBtn").disabled = false;
  }
}

const TYPE_LABELS = { multiple_choice: "چهارگزینه‌ای", true_false: "درست/غلط", essay: "تشریحی" };

function renderQuiz() {
  const area = $("#quizArea");
  const quiz = state.currentQuiz;
  area.innerHTML = "";

  const title = document.createElement("h3");
  title.textContent = quiz.title;
  title.style.marginBottom = "12px";
  area.appendChild(title);

  quiz.questions.forEach((q, idx) => {
    const card = document.createElement("div");
    card.className = "quiz-card";
    card.dataset.questionId = q.id;

    let inner = `<div><span class="q-number">${idx + 1}</span><span class="q-text">${escapeHtml(q.question_text)}</span><span class="q-type-tag">${TYPE_LABELS[q.question_type] || q.question_type}</span></div>`;

    if (q.question_type === "essay") {
      inner += `<textarea class="q-essay-input" placeholder="پاسخ خودت رو بنویس..."></textarea>`;
    } else {
      inner += `<div class="q-options">`;
      (q.options || []).forEach((opt, oi) => {
        inner += `<label class="q-option" data-value="${escapeHtml(opt)}">
          <input type="radio" name="q_${q.id}" value="${escapeHtml(opt)}"> ${escapeHtml(opt)}
        </label>`;
      });
      inner += `</div>`;
    }
    inner += `<div class="q-feedback-slot"></div>`;
    card.innerHTML = inner;
    area.appendChild(card);
  });

  const submitBtn = document.createElement("button");
  submitBtn.className = "btn primary";
  submitBtn.textContent = "ثبت پاسخ‌ها";
  submitBtn.style.marginTop = "8px";
  submitBtn.addEventListener("click", submitQuiz);
  area.appendChild(submitBtn);
}

async function submitQuiz() {
  if (state.quizSubmitted) return;
  const quiz = state.currentQuiz;
  const answers = quiz.questions.map((q) => {
    let studentAnswer = "";
    if (q.question_type === "essay") {
      const ta = document.querySelector(`.quiz-card[data-question-id="${q.id}"] .q-essay-input`);
      studentAnswer = ta ? ta.value.trim() : "";
    } else {
      const checked = document.querySelector(`input[name="q_${q.id}"]:checked`);
      studentAnswer = checked ? checked.value : "";
    }
    return { question_id: q.id, student_answer: studentAnswer };
  });

  const area = $("#quizArea");
  const banner = document.createElement("div");
  banner.className = "result-box";
  banner.textContent = "در حال تصحیح آزمون...";
  area.appendChild(banner);

  try {
    const data = await api("/api/quiz/submit", {
      method: "POST",
      body: JSON.stringify({ quiz_id: quiz.id, user: state.user, answers, level: state.level }),
    });
    state.quizSubmitted = true;
    banner.remove();
    renderQuizResults(data);
  } catch (err) {
    banner.textContent = "⚠️ " + err.message;
  }
}

function renderQuizResults(result) {
  const scoreBanner = document.createElement("div");
  scoreBanner.className = "quiz-score-banner";
  scoreBanner.innerHTML = `<span class="score-label">نتیجه آزمون</span><span class="score-num" dir="ltr">${result.score} / ${result.total}</span>`;
  $("#quizArea").prepend(scoreBanner);

  if (result.total > 0 && result.score / result.total >= 0.7) {
    launchConfetti();
  }

  result.feedback.forEach((fb) => {
    const card = document.querySelector(`.quiz-card[data-question-id="${fb.question_id}"]`);
    if (!card) return;
    const slot = card.querySelector(".q-feedback-slot");

    // غیرفعال کردن ورودی‌ها
    card.querySelectorAll("input, textarea").forEach((el) => (el.disabled = true));

    if ("is_correct" in fb) {
      card.querySelectorAll(".q-option").forEach((opt) => {
        if (opt.dataset.value === fb.correct_answer) opt.classList.add("correct");
        const input = opt.querySelector("input");
        if (input.checked && !fb.is_correct) opt.classList.add("incorrect");
      });
      slot.innerHTML = `<div class="q-feedback ${fb.is_correct ? "correct" : "incorrect"}">
        ${fb.is_correct ? "✅ درست بود!" : "❌ نادرست. پاسخ صحیح: " + escapeHtml(fb.correct_answer)}
        ${fb.explanation ? "<br>" + escapeHtml(fb.explanation) : ""}
      </div>`;
    } else {
      const scoreTxt = fb.score != null ? `نمره: ${Math.round(fb.score * 100)}٪` : "در انتظار بررسی";
      const mistakes = (fb.mistakes || []).map((m) => `<li>${escapeHtml(m)}</li>`).join("");
      slot.innerHTML = `<div class="q-feedback graded">
        ${scoreTxt}<br>${escapeHtml(fb.explanation || "")}
        ${mistakes ? `<ul>${mistakes}</ul>` : ""}
        ${fb.correct_answer ? `<em>پاسخ نمونه: ${escapeHtml(fb.correct_answer)}</em>` : ""}
      </div>`;
    }
  });
}

function launchConfetti() {
  const pieces = ["🎉", "✨", "🎊", "⭐", "🥳"];
  for (let i = 0; i < 24; i++) {
    const el = document.createElement("div");
    el.className = "confetti-piece";
    el.textContent = pieces[Math.floor(Math.random() * pieces.length)];
    el.style.left = Math.random() * 100 + "vw";
    el.style.animationDuration = 2 + Math.random() * 1.5 + "s";
    el.style.fontSize = 14 + Math.random() * 14 + "px";
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }
}

// خلاصه‌ساز
async function runSummary() {
  const topic = $("#summaryTopicInput").value.trim();
  if (!topic) return;
  const box = $("#summaryResult");
  box.textContent = "در حال خلاصه‌سازی...";
  $("#summaryBtn").disabled = true;

  try {
    const data = await api("/api/summary", {
      method: "POST",
      body: JSON.stringify({ topic, course: state.course || undefined, level: state.level }),
    });
    if (data.error) { box.innerHTML = `⚠️ ${escapeHtml(data.error)}`; return; }
    if (!data.summary) { box.innerHTML = `⚠️ ${escapeHtml(data.warning || "پاسخی دریافت نشد.")}`; return; }

    let html = escapeHtml(data.summary).replace(/\n/g, "<br>");
    if (data.sources && data.sources.length) {
      html += `<div class="sources" style="margin-top:14px">` +
        data.sources.map((s) => `<span class="source-chip">📌 ${escapeHtml(s.source_file)} — صفحه ${s.page}</span>`).join(" ") +
        `</div>`;
    }
    box.innerHTML = html;
  } catch (err) {
    box.innerHTML = "⚠️ " + escapeHtml(err.message);
  } finally {
    $("#summaryBtn").disabled = false;
  }
}

// نقاط ضعف
async function loadWeakness() {
  const box = $("#weaknessResult");
  box.textContent = "در حال تحلیل نتایج آزمون‌های قبلی...";
  box.dataset.loaded = "1";

  try {
    const data = await api(`/api/weakness?user=${encodeURIComponent(state.user)}&level=${encodeURIComponent(state.level)}`);
    if (!data.weak_topics.length) {
      box.innerHTML = `<div class="empty-state" style="margin:0"><div class="empty-icon">🧠</div><strong>هنوز داده‌ای موجود نیست</strong><p>${escapeHtml(data.advice || "")}</p></div>`;
      return;
    }
    let html = "";
    data.weak_topics.forEach((t) => {
      const pct = Math.round(t.weakness_ratio * 100);
      html += `<div class="weak-topic-row">
        <span class="topic-name">${escapeHtml(t.topic)}</span>
        <div class="weak-bar-track"><div class="weak-bar-fill" style="width:${pct}%"></div></div>
        <span class="weak-pct">${pct}٪</span>
      </div>`;
    });
    if (data.advice) html += `<div style="margin-top:16px; padding-top:16px; border-top:1px solid var(--line)">${escapeHtml(data.advice).replace(/\n/g, "<br>")}</div>`;
    box.innerHTML = html;
  } catch (err) {
    box.innerHTML = "⚠️ " + escapeHtml(err.message);
  }
}

// برنامه مطالعه
async function generatePlan() {
  const course = $("#plannerCourseInput").value.trim();
  const daysRemaining = parseInt($("#plannerDaysInput").value, 10);
  const dailyHours = parseFloat($("#plannerHoursInput").value);
  if (!course || !daysRemaining || !dailyHours) return;

  const box = $("#plannerResult");
  box.innerHTML = `<div class="result-box">در حال ساخت برنامه مطالعه...</div>`;
  $("#plannerBtn").disabled = true;

  try {
    const data = await api("/api/planner", {
      method: "POST",
      body: JSON.stringify({ user: state.user, course, days_remaining: daysRemaining, daily_hours: dailyHours }),
    });
    if (data.error) { box.innerHTML = `<div class="result-box">⚠️ ${escapeHtml(data.error)}</div>`; return; }

    const plan = data.plan;
    let html = "";
    if (plan.overview) html += `<div class="plan-overview">${escapeHtml(plan.overview)}</div>`;
    (plan.days || []).forEach((d) => {
      html += `<div class="plan-day">
        <div class="day-badge">${d.day}</div>
        <div class="day-body">
          <h4>${escapeHtml(d.focus || "روز " + d.day)}</h4>
          <div class="hours">⏱ ${d.hours} ساعت</div>
          <div class="topics">${(d.topics || []).map((t) => `<span class="topic-chip">${escapeHtml(t)}</span>`).join("")}</div>
        </div>
      </div>`;
    });
    box.innerHTML = html;
  } catch (err) {
    box.innerHTML = `<div class="result-box">⚠️ ${escapeHtml(err.message)}</div>`;
  } finally {
    $("#plannerBtn").disabled = false;
  }
}

// منابع درسی
async function loadDocuments() {
  const list = $("#docList");
  list.innerHTML = `<div class="small muted">در حال بارگذاری...</div>`;
  try {
    const data = await api("/api/documents");
    if (!data.documents.length) {
      list.innerHTML = `<div class="empty-state" style="margin:0"><div class="empty-icon">📁</div><strong>هنوز فایلی آپلود نشده</strong><p>یک PDF از بالا اضافه کن.</p></div>`;
      return;
    }
    list.innerHTML = "";
    data.documents.forEach((d) => {
      const row = document.createElement("div");
      row.className = "doc-row";
      row.innerHTML = `<span class="doc-icon">📄</span>
        <div class="doc-info"><strong>${escapeHtml(d.source_file)}</strong><span>${escapeHtml(d.course)} — ${d.num_chunks} بخش</span></div>
        <button class="btn ghost">حذف</button>`;
      row.querySelector("button").addEventListener("click", async () => {
        await api(`/api/documents/${d.doc_id}`, { method: "DELETE" });
        loadDocuments();
      });
      list.appendChild(row);
    });
  } catch (err) {
    list.innerHTML = `<div class="small muted">⚠️ ${escapeHtml(err.message)}</div>`;
  }
}

async function uploadFile(file) {
  const status = $("#uploadStatus");
  status.textContent = "در حال پردازش...";
  const fd = new FormData();
  fd.append("file", file);
  fd.append("course", $("#uploadCourseInput").value.trim() || state.course || "عمومی");

  try {
    const res = await fetch(API + "/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "خطا در آپلود");
    status.textContent = `✅ ${data.source_file} — ${data.num_pages} صفحه، ${data.num_chunks} بخش ذخیره شد.`;
    loadDocuments();
  } catch (err) {
    status.textContent = "⚠️ " + err.message;
  }
}

function initUpload() {
  const zone = $("#dropZone");
  const input = $("#fileInput");
  zone.addEventListener("click", (e) => {
    if (e.target.tagName !== "INPUT") input.click();
  });
  input.addEventListener("change", () => { if (input.files[0]) uploadFile(input.files[0]); });
  ["dragover", "dragenter"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("dragover"); })
  );
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file && file.type === "application/pdf") uploadFile(file);
  });
}

// bootstrap
function autoGrowTextarea(el) {
  el.addEventListener("input", () => {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initProfileInputs();
  initDarkMode();
  initModeNav();
  initUpload();
  checkHealth();

  $("#chatForm").addEventListener("submit", sendChatMessage);
  $("#newChatBtn").addEventListener("click", newChat);
  $("#historyToggleBtn").addEventListener("click", toggleHistoryDrawer);

  $("#teacherStartBtn").addEventListener("click", startTeaching);
  $("#teacherContinueForm").addEventListener("submit", continueTeaching);

  $("#quizGenerateBtn").addEventListener("click", generateQuiz);
  $("#summaryBtn").addEventListener("click", runSummary);
  $("#weaknessRefreshBtn").addEventListener("click", loadWeakness);
  $("#plannerBtn").addEventListener("click", generatePlan);

  [$("#chatInput"), $("#teacherContinueInput")].forEach(autoGrowTextarea);

  // ارسال با اینتر (بدون شیفت)
  [$("#chatInput"), $("#teacherContinueInput")].forEach((ta) => {
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        ta.closest("form").dispatchEvent(new Event("submit", { cancelable: true }));
      }
    });
  });
});
