"""هسته RAG: گرفتن PDF، تقسیم به چانک، ساخت embedding و ذخیره در ChromaDB برای بازیابی."""
import os
import uuid
import time
import re

import chromadb
from chromadb.api.types import EmbeddingFunction, Documents
from pypdf import PdfReader

import config


class LocalHashingEmbeddingFunction(EmbeddingFunction[Documents]):
    """Fallback بدون اینترنت: بردار bag-of-words با هش کردن کلمات.
    فقط overlap لغوی رو می‌بینه، فهم معنایی نداره - اگه سؤال با مترادف یا
    زبان دیگه پرسیده بشه دقتش میاد پایین. وقتی مدل چندزبانه پایین در
    دسترس نیست به این برمی‌گردیم."""

    def __init__(self, n_features: int = 1024):
        from sklearn.feature_extraction.text import HashingVectorizer
        self._vectorizer = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm="l2",
            ngram_range=(1, 2),
            token_pattern=r"(?u)\b\w+\b",
        )

    def name(self) -> str:
        return "local-hashing-embedding-v1"

    def __call__(self, input):
        matrix = self._vectorizer.transform(input)
        return matrix.toarray().tolist()


class MultilingualEmbeddingFunction(EmbeddingFunction[Documents]):
    """embedding معنایی چندزبانه با sentence-transformers - جمله فارسی و
    معادل انگلیسیش رو نزدیک هم می‌بینه. فقط بار اول دانلود می‌خواد، بعدش
    آفلاین کار می‌کنه."""

    _model = None  # lazy singleton بین چند instance

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        if MultilingualEmbeddingFunction._model is None:
            from sentence_transformers import SentenceTransformer
            MultilingualEmbeddingFunction._model = SentenceTransformer(model_name)
        self._model = MultilingualEmbeddingFunction._model

    def name(self) -> str:
        return "multilingual-minilm-v1"

    def __call__(self, input):
        embeddings = self._model.encode(
            list(input), normalize_embeddings=True, show_progress_bar=False
        )
        return embeddings.tolist()


def build_embedding_function():
    # اگه sentence-transformers نصب نباشه یا دانلود مدل شکست بخوره (اینترنت نیست)
    # برنامه نباید کلاً بالا نیاد، پس میریم رو هش‌بیسِ آفلاین با یه هشدار تو لاگ
    try:
        return MultilingualEmbeddingFunction()
    except Exception as e:
        print(f"⚠️ embedding چندزبانه لود نشد ({e}) - رفتیم رو حالت hashing (دقت کمتر)")
        return LocalHashingEmbeddingFunction()


class RAGEngine:
    def __init__(self):
        # پرسیستنت یعنی داده‌ها رو دیسک می‌مونن، بین ری‌استارت از دست نمیرن
        self.client = chromadb.PersistentClient(path=config.CHROMA_DIR)
        self.embedding_fn = build_embedding_function()

        self.collection = self.client.get_or_create_collection(
            name="course_materials",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def extract_pages(filepath: str):
        """لیست (page_number, text) از یک PDF - شماره صفحه از ۱ شروع میشه"""
        reader = PdfReader(filepath)
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                pages.append((i, text))
        return pages

    @staticmethod
    def chunk_text(text: str, chunk_size: int = None, overlap: int = None):
        chunk_size = chunk_size or config.CHUNK_SIZE
        overlap = overlap or config.CHUNK_OVERLAP
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            # سعی می‌کنیم برش را سر یک نقطه/فاصله بزنیم تا جمله نصفه نشود
            if end < len(text):
                boundary = text.rfind(" ", start + int(chunk_size * 0.6), end)
                if boundary != -1:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
        return chunks

    def ingest_file(self, filepath: str, original_filename: str, course: str = "عمومی"):
        doc_id = str(uuid.uuid4())[:8]
        pages = self.extract_pages(filepath)
        if not pages:
            raise ValueError("متنی از این فایل استخراج نشد (ممکن است اسکن‌شده باشد و نیاز به OCR داشته باشد).")

        ids, documents, metadatas = [], [], []
        for page_num, page_text in pages:
            for chunk_idx, chunk in enumerate(self.chunk_text(page_text)):
                chunk_id = f"{doc_id}_p{page_num}_c{chunk_idx}"
                ids.append(chunk_id)
                documents.append(chunk)
                metadatas.append({
                    "doc_id": doc_id,
                    "source_file": original_filename,
                    "page": page_num,
                    "course": course,
                    "ingested_at": time.time(),
                })

        self.collection.add(ids=ids, documents=documents, metadatas=metadatas)

        return {
            "doc_id": doc_id,
            "source_file": original_filename,
            "course": course,
            "num_pages": len(pages),
            "num_chunks": len(ids),
        }

    def list_documents(self):
        data = self.collection.get(include=["metadatas"])
        docs = {}
        for meta in data["metadatas"]:
            doc_id = meta["doc_id"]
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "source_file": meta["source_file"],
                    "course": meta.get("course", "عمومی"),
                    "num_chunks": 0,
                }
            docs[doc_id]["num_chunks"] += 1
        return list(docs.values())

    def delete_document(self, doc_id: str):
        self.collection.delete(where={"doc_id": doc_id})

    def has_documents(self, course: str = None) -> bool:
        docs = self.list_documents()
        if course:
            docs = [d for d in docs if d["course"] == course]
        return len(docs) > 0

    def query(self, question: str, top_k: int = None, course: str = None):
        top_k = top_k or config.TOP_K_DEFAULT
        where = {"course": course} if course else None

        results = self.collection.query(
            query_texts=[question],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        if not results["documents"] or not results["documents"][0]:
            return []

        hits = []
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            hits.append({
                "text": doc,
                "source_file": meta["source_file"],
                "page": meta["page"],
                "course": meta.get("course", "عمومی"),
                "relevance": round(1 - dist, 3),  # نزدیک‌تر به ۱ یعنی مرتبط‌تر
            })
        return hits


engine = RAGEngine()
