"""轻量 RAG 检索：切片 + 向量化 + 向量数据库检索。

- embedding：fastembed（bge-small-zh 语义向量）/ TF-IDF 降级
- 向量数据库：Milvus Lite（嵌入式，本地文件持久化，招聘最常用）/ 内存余弦（零依赖降级）

用法：
    r = MemoryRetriever(persist_dir="./.rag_store")
    r.index({"架构设计": "...", "前端交付": "..."})
    hits = r.retrieve("贪吃蛇怎么实现", top_k=3)
"""
import math
import os
import re
import tempfile
from collections import Counter


def split_chunks(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """按段落切块，尽量不破坏语义单元；超长段落硬切并带重叠。"""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 1 <= chunk_size:
            cur = (cur + "\n" + p).strip() if cur else p
        else:
            if cur:
                chunks.append(cur)
                cur = ""
            while len(p) > chunk_size:
                chunks.append(p[:chunk_size])
                p = p[chunk_size - overlap:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def _tokenize(text: str) -> list[str]:
    """中文按单字 + bigram，英文/数字按词（简化分词）。"""
    text = text.lower()
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    words = re.findall(r"[a-z0-9]+", text)
    grams = list(cjk)
    for i in range(len(cjk) - 1):
        grams.append(cjk[i] + cjk[i + 1])
    return words + grams


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class MemoryRetriever:
    def __init__(self, persist_dir: str | None = None):
        self._chunks: list[tuple[str, str]] = []   # (source, text)
        self._vectors: list[list[float]] = []
        self._mode: str = "none"                   # semantic / tfidf / none
        self._embedder = None
        self._vocab: list[str] = []
        self._df: dict[str, int] = {}
        self._n_docs = 0
        self._persist_dir = persist_dir            # 向量库落盘目录
        self._milvus_client = None
        self._store_backend = "memory"             # milvus / memory

    @property
    def backend(self) -> str:
        """当前检索后端：milvus（向量数据库）/ memory（内存余弦）。"""
        return self._store_backend

    # ---------- embedding 后端 ----------
    def _try_load_embedder(self):
        if self._embedder is not None:
            return self._embedder
        try:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding("BAAI/bge-small-zh-v1.5")
        except Exception:
            self._embedder = None
        return self._embedder

    def _build_vocab(self, texts: list[str]) -> None:
        df: Counter = Counter()
        for t in texts:
            for tok in set(_tokenize(t)):
                df[tok] += 1
        self._df = dict(df)
        self._n_docs = len(texts)
        self._vocab = sorted(self._df.keys())

    def _tfidf(self, text: str) -> list[float]:
        toks = _tokenize(text)
        tf = Counter(toks)
        n = max(self._n_docs, 1)
        vec: list[float] = []
        for t in self._vocab:
            if t in tf:
                idf = math.log((1 + n) / (1 + self._df[t])) + 1.0
                vec.append(tf[t] * idf)
            else:
                vec.append(0.0)
        return vec

    def _compute_embeddings(self, texts: list[str]) -> list[list[float]]:
        """返回向量并设置 self._mode（semantic / tfidf）。"""
        model = self._try_load_embedder()
        if model is not None:
            try:
                vecs = [list(map(float, v)) for v in model.embed(texts)]
                self._mode = "semantic"
                return vecs
            except Exception:
                pass
        self._build_vocab(texts)
        self._mode = "tfidf"
        return [self._tfidf(t) for t in texts]

    def _embed_query(self, query: str) -> list[float]:
        if self._mode == "semantic" and self._embedder is not None:
            try:
                return list(map(float, list(self._embedder.embed([query]))[0]))
            except Exception:
                pass
        return self._tfidf(query)

    # ---------- 索引 ----------
    def index(self, documents: dict[str, str],
              chunk_size: int = 500, overlap: int = 100) -> int:
        self._chunks = []
        for source, text in documents.items():
            for c in split_chunks(text, chunk_size, overlap):
                self._chunks.append((source, c))
        if not self._chunks:
            self._mode = "none"
            self._store_backend = "memory"
            return 0

        texts = [c for _, c in self._chunks]
        embeddings = self._compute_embeddings(texts)
        if self._store_to_milvus(embeddings):
            self._store_backend = "milvus"
        else:
            self._store_backend = "memory"
            self._vectors = embeddings
        return len(self._chunks)

    def _store_to_milvus(self, embeddings: list[list[float]]) -> bool:
        """把切片 + 向量写入 Milvus Lite（本地嵌入式，文件持久化）。"""
        try:
            from pymilvus import MilvusClient
            dim = len(embeddings[0])
            if self._persist_dir:
                os.makedirs(self._persist_dir, exist_ok=True)
                db = os.path.join(self._persist_dir, "milvus.db")
            else:
                db = os.path.join(tempfile.mkdtemp(), "milvus.db")
            client = MilvusClient(db)
            if client.has_collection("memory"):
                client.drop_collection("memory")
            client.create_collection(collection_name="memory",
                                     dimension=dim, metric_type="COSINE")
            data = []
            for i, (source, text) in enumerate(self._chunks):
                data.append({
                    "id": i,
                    "vector": embeddings[i],
                    "text": text,
                    "source": source,
                })
            client.insert(collection_name="memory", data=data)
            self._milvus_client = client
            return True
        except Exception:
            return False

    # ---------- 检索 ----------
    def retrieve(self, query: str, top_k: int = 4) -> list[str]:
        if not self._chunks:
            return []
        qvec = self._embed_query(query)
        if self._store_backend == "milvus" and self._milvus_client is not None:
            hits = self._query_milvus(qvec, top_k)
            if hits:
                return hits
        return self._query_memory(qvec, top_k)

    def _query_milvus(self, qvec: list[float], top_k: int) -> list[str]:
        try:
            results = self._milvus_client.search(
                collection_name="memory",
                data=[qvec],
                limit=top_k,
                output_fields=["text", "source"],
            )
            hits: list[str] = []
            for hit in (results[0] if results else []):
                entity = hit.get("entity", {})
                source = entity.get("source", "")
                text = entity.get("text", "")
                hits.append(f"【{source}】\n{text.strip()}")
            return hits
        except Exception:
            return []

    def _query_memory(self, qvec: list[float], top_k: int) -> list[str]:
        scored = [(_cosine(qvec, v), i) for i, v in enumerate(self._vectors)]
        scored.sort(key=lambda x: -x[0])
        hits: list[str] = []
        for score, i in scored[:top_k]:
            if score <= 0.0:
                continue
            source, text = self._chunks[i]
            hits.append(f"【{source}】\n{text.strip()}")
        if not hits and scored:
            source, text = self._chunks[scored[0][1]]
            hits.append(f"【{source}】\n{text.strip()}")
        return hits
