# AI Student Assistant

A study assistant that answers questions **only from the PDFs you give it** — your own lecture notes, slides, or textbook chapters — instead of making things up from general knowledge. It also runs quizzes on that material, teaches it step by step, summarizes it, and tracks which topics you keep getting wrong.

I built this because I wanted an assistant that doesn't bluff. A lot of "AI tutor" demos will happily answer anything, which is exactly the wrong behavior for actually studying for an exam — you want it grounded in *your* material, and honest when the answer just isn't in there.

## How it works

Flask backend, a plain HTML/CSS/JS frontend (no framework, no build step), SQLite for history, and ChromaDB as the vector store. The LLM is Groq (OpenAI-compatible Chat Completions API).

When you upload a PDF:

1. Text gets pulled out page by page (`pypdf`), so every answer can point back to a page number.
2. Each page is chunked into ~900-character pieces with some overlap, so a concept split across a page boundary doesn't get cut in half.
3. Each chunk gets embedded with a multilingual sentence-transformers model and stored in ChromaDB. Multilingual matters here — if your source is in Persian but you ask a question in English (or the other way around), it still finds the right chunk. If `sentence-transformers` isn't installed or the model can't download, it falls back to a local hashing vectorizer so the app still runs, just with weaker retrieval.

When you ask something, it retrieves the closest chunks and only *then* calls the LLM — with a strict system prompt telling it to answer from those chunks alone, cite the file/page, and say "I don't know" rather than guess. There's also a relevance-score floor in `app.py` that short-circuits before even calling the LLM if the best match is clearly unrelated, so you're not burning API calls on garbage retrieval.

## Features

- **Chat** — grounded Q&A with conversation history, so follow-up questions make sense
- **Teacher mode** — breaks a topic into steps and teaches one step at a time instead of dumping everything at once
- **Quiz generator** — builds multiple-choice / true-false / essay questions straight from your uploaded material, grades the objective ones automatically and uses the LLM to grade essay answers
- **Summaries** — key points + important formulas/definitions pulled out separately
- **Weakness tracking** — looks at your past quiz answers and tells you which topics you're actually weak in, then gives a few concrete suggestions
- **Study planner** — given days left and hours/day, builds a day-by-day plan that weights your weak topics more heavily
- **Standalone answer evaluation** — grade any question/answer pair outside of a formal quiz

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # if you don't have one, see below — then fill in your Groq key(s)
python app.py
```

Open `http://localhost:5000`.

`.env` needs at least:

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com/keys](https://console.groq.com/keys). You can set `GROQ_API_KEY_2` / `GROQ_API_KEY_3` too — the client round-robins across whichever ones are set and automatically retries on the next key if one fails or gets rate-limited, which matters a lot on Groq's free tier.

Other knobs you can tune in `.env` (all optional, sane defaults in `config.py`):

| Variable | What it does |
|---|---|
| `GROQ_MODEL` | which Groq model to call |
| `MIN_RELEVANCE_SCORE` | how strict the "is this actually relevant" filter is before calling the model |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | how PDFs get split before embedding |
| `TOP_K_DEFAULT` | how many chunks get retrieved per question |

## What's not here

There's no real authentication — a "username" field in the sidebar is enough to separate histories between people on the same machine, there's no password or session security behind it. It's also PDF-only for now (a scanned/image-only PDF won't extract text without OCR, which isn't wired up). Good enough for personal study use; I wouldn't put it in front of strangers on the open internet as-is.

## Project layout

```
app.py          # routes
rag_engine.py   # PDF -> chunks -> embeddings -> ChromaDB, and retrieval
llm_client.py   # Groq client, key rotation
prompts.py      # system prompt per mode (chat/teacher/quiz/summary/...)
lang_utils.py   # fa/en detection for system messages
db.py           # SQLite: users, conversations, quizzes, study plans
config.py       # env vars, paths, tunables
static/         # frontend (vanilla JS, no build step)
```
