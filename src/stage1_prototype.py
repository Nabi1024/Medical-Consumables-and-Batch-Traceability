# -*- coding: utf-8 -*-
"""
============================================================================
医用耗材批次追溯智能助手系统 —— 阶段 1 最小原型（单文件 / 无 Web 服务）
============================================================================
目标：验证「本地制度文档 → RAG 切片向量化 → BM25+向量混合检索 → LLM 生成回答」
      这一条端到端链路，不涉及任何 Web / 智能体编排 / 工单。

功能：
  1) 读取本地 Markdown（医疗器械监管法规 / 院内 SOP / 批次追溯 / 效期管理 / 不良事件上报）
  2) 按 500 字切片并向量化，存入向量库（FAISS 优先，离线自动退化为纯 Python 暴力检索）
  3) 命令行接收操作员提问，执行 BM25 + 向量混合检索，召回 top3 片段
  4) 把召回上下文拼入提示词后调用 LLM 生成回答
  5) LLM 调用封装为独立函数 call_llm，支持快速切换不同大模型供应商（含离线 Mock）

【零依赖可运行】
  本脚本只用 Python 标准库即可跑通：BM25、离线 TF-IDF 嵌入、纯 Python 向量检索、
  Mock LLM 全部内置。若安装了 faiss-cpu / scikit-learn / requests，会自动启用
  更强的对应实现（FAISS 索引、sklearn TF-IDF、真实大模型 API）。
  安装（可选）： pip install faiss-cpu scikit-learn requests

【模块复用标记说明】
  ★ 可复用核心：后续 Web 后端 / 智能体编排 / 工单追溯阶段直接搬用
  ○ 阶段1专用：仅本原型 CLI 演示用，上线时由 Web 路由 / Agent 主循环替代
============================================================================
"""

import argparse
import glob
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 可选第三方依赖：全部「延迟/保护式」导入，缺失不影响标准库路径的运行
# ---------------------------------------------------------------------------
try:
    import faiss  # type: ignore  # 生产主向量索引（用户指定）
    _FAISS_AVAILABLE = True
except Exception:
    faiss = None
    _FAISS_AVAILABLE = False


# ============================================================================
# ★ 可复用核心模块【文档读取与切片】—— 后续 Web 后端(知识库摄取) / 智能体编排(RAG 管线) 复用
# ============================================================================

def load_markdown_docs(docs_dir: str) -> List[Dict]:
    """
    读取目录下所有 .md 文件，解析为纯文本片段。
    返回列表，元素: {"source": 文件名, "text": 正文}
    说明：真实系统中这里会接入对象存储(MinIO/S3)与数据库(kb_documents)，
          但「文本抽取 + 切块」逻辑完全可复用。
    """
    docs = []
    md_files = sorted(glob.glob(os.path.join(docs_dir, "**", "*.md"), recursive=True))
    if not md_files:
        raise FileNotFoundError(f"在 {docs_dir} 下未找到任何 .md 文档")
    for fp in md_files:
        raw = Path(fp).read_text(encoding="utf-8")
        raw = _strip_frontmatter(raw)          # 去掉 YAML frontmatter
        raw = _clean_markdown(raw)             # 去掉代码块/多余空行
        if raw.strip():
            docs.append({"source": os.path.basename(fp), "text": raw})
    print(f"[摄取] 读取到 {len(md_files)} 个 Markdown 文档，共 {len(docs)} 段正文")
    return docs


def _strip_frontmatter(text: str) -> str:
    """去掉 Markdown 开头的 --- ... --- YAML 元信息块。"""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:]
    return text


def _clean_markdown(text: str) -> str:
    """剔除代码围栏、图片/链接语法，保留可读正文（原型级清洗）。"""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)      # 代码块
    text = re.sub(r"!\[.*?\]\(.*?\)", " ", text)                # 图片
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)        # 链接只留文字
    text = re.sub(r"[#>*_`\\-]+", " ", text)                    # 常见 Markdown 符号
    text = re.sub(r"\s+", " ", text).strip()                     # 合并空白
    return text


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> List[str]:
    """
    按「字」切片：窗口 size 字，步长 size-overlap，保留少量重叠以减小边界截断损失。
    这是 RAG 摄取的核心参数，后续 Web 后端摄取流水线直接复用同一函数。
    """
    step = max(1, size - overlap)
    chunks = [text[i:i + size] for i in range(0, max(1, len(text) - overlap), step)]
    if not chunks:
        chunks = [text]
    return [c for c in chunks if c.strip()]


# ============================================================================
# ★ 可复用核心模块【轻量中文分词】—— BM25 / 嵌入 / 混合检索共用的基础工具
# ============================================================================

def _tokenize(text: str) -> List[str]:
    """中文拆单字、英文数字拆词并小写——适配中英混合制度文本。"""
    text = text.lower()
    toks = re.findall(r"[a-z0-9]+", text)               # 英文/数字词
    toks += re.findall(r"[\u4e00-\u9fff]", text)        # 中文单字
    return toks


# ============================================================================
# ★ 可复用核心模块【嵌入层 EmbeddingProvider】—— Web 后端(向量检索服务) / 智能体编排(RAG 管线) 复用
#   设计要点：供应商可插拔。默认走离线 TF-IDF（零依赖可跑），生产可换句向量/嵌入 API。
#   所有 embedder 统一返回 List[List[float]]（不依赖 numpy），便于纯 Python 与 FAISS 双路复用。
# ============================================================================

class EmbeddingProvider:
    """嵌入抽象基类：子类实现 encode(texts)->List[List[float]]。"""

    def encode(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class OfflineTfidfEmbeddingProvider(EmbeddingProvider):
    """
    纯 Python 离线 TF-IDF 嵌入（零依赖）。语义能力弱于句向量，但无需网络与模型下载，
    保证原型在任何环境跑通；生产环境替换为 sentence-transformers(bge-m3 等) 即可，接口不变。
    """

    def __init__(self, max_features: int = 20000):
        self._max_features = max_features
        self._vocab: List[str] = []          # 词表（顺序即向量维度顺序）
        self._idf: Dict[str, float] = {}
        self._fitted = False

    def fit(self, corpus: List[str]) -> None:
        docs_tokens = [_tokenize(t) for t in corpus]
        df: Dict[str, int] = {}
        for toks in docs_tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        # 按文档频率(df)排序，截取高频词构成词表，限制维度
        top = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))[: self._max_features]
        self._vocab = [w for w, _ in top]
        N = len(docs_tokens)
        # 平滑 IDF：log((N+1)/(df+1)) + 1
        self._idf = {w: math.log((N + 1) / (df[w] + 1)) + 1.0 for w in self._vocab}
        self._fitted = True

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not self._fitted:
            self.fit(texts)
        dim = len(self._vocab)
        out: List[List[float]] = []
        for t in texts:
            toks = _tokenize(t)
            tf: Dict[str, int] = {}
            for w in toks:
                tf[w] = tf.get(w, 0) + 1
            vec = [0.0] * dim
            for j, w in enumerate(self._vocab):
                if w in tf:
                    vec[j] = tf[w] * self._idf[w]
            out.append(_l2_normalize_list(vec))
        return out


class SimpleHashEmbeddingProvider(EmbeddingProvider):
    """
    纯 Python 哈希词袋嵌入——作为「无 TF-IDF 也能跑」的终极回退。
    质量最低，仅保证流程贯通；优先使用 OfflineTfidfEmbeddingProvider。
    """

    def __init__(self, dim: int = 1024):
        self._dim = dim

    def encode(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for t in texts:
            vec = [0.0] * self._dim
            for w in _tokenize(t):
                vec[hash(w) % self._dim] += 1.0
            out.append(_l2_normalize_list(vec))
        return out


class SklearnTfidfEmbeddingProvider(EmbeddingProvider):
    """若安装了 scikit-learn 则启用更稳健的 TF-IDF（延迟导入，缺包不影响其他模块）。"""

    def __init__(self, max_features: int = 20000):
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        self._vec = TfidfVectorizer(
            max_features=max_features,
            token_pattern=r"(?u)\b\w+\b|[\u4e00-\u9fff]",  # 中英混合分词
        )
        self._fitted = False

    def fit(self, corpus: List[str]) -> None:
        self._vec.fit(corpus)
        self._fitted = True

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not self._fitted:
            self._vec.fit(texts)
        import numpy as np  # type: ignore
        X = self._vec.transform(texts).toarray().astype("float32")
        return (_l2_normalize_np(X)).tolist()


class ApiEmbeddingProvider(EmbeddingProvider):
    """调用 OpenAI 兼容 /embeddings 接口（如 DeepSeek / 通义 / 自建）。生产推荐。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    def encode(self, texts: List[str]) -> List[List[float]]:
        import requests  # 延迟导入，仅真实调用时才需要
        resp = requests.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": texts},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        vecs = [d["embedding"] for d in data]
        return [_l2_normalize_list(list(v)) for v in vecs]


def _l2_normalize_list(vec: List[float]) -> List[float]:
    """纯 Python L2 归一化。"""
    s = math.sqrt(sum(x * x for x in vec))
    if s == 0.0:
        return vec
    return [x / s for x in vec]


def _l2_normalize_np(X):
    """numpy 版 L2 归一化（sklearn 路径使用）。"""
    import numpy as np
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return X / norms


def get_embedding_provider(name: str, cfg: dict) -> EmbeddingProvider:
    """
    嵌入供应商工厂：
      'auto' 优先 sklearn TF-IDF，缺失则退回纯 Python 离线 TF-IDF；
      'tfidf' 强制离线 TF-IDF；'hash' 哈希回退；'api' 远程嵌入。
    """
    if name == "api":
        return ApiEmbeddingProvider(cfg["embed_base_url"], cfg["embed_api_key"], cfg["embed_model"])
    if name == "hash":
        return SimpleHashEmbeddingProvider()
    if name == "sklearn":
        return SklearnTfidfEmbeddingProvider()
    # auto / tfidf
    if name in ("auto", "tfidf"):
        try:
            return SklearnTfidfEmbeddingProvider()
        except Exception:
            print("[嵌入] 未安装 scikit-learn，自动使用纯 Python 离线 TF-IDF（零依赖）。"
                  "可 pip install scikit-learn 提升质量。")
            return OfflineTfidfEmbeddingProvider()
    return OfflineTfidfEmbeddingProvider()


# ============================================================================
# ★ 可复用核心模块【向量库 VectorStore】—— Web 后端(向量检索服务) / 智能体编排(RAG 管线) 复用
#   设计：FAISS 可用时构建 IndexFlatIP（归一化后等价于余弦）作为生产主索引；
#        离线环境自动退化为纯 Python 暴力余弦检索，语义等价、原型规模下性能足够。
#   search() 统一返回 [(chunk_index, similarity), ...]，similarity 越大越相关。
# ============================================================================

class VectorStore:

    def __init__(self):
        self._use_faiss = _FAISS_AVAILABLE
        self._index = None                  # FAISS 索引（如有）
        self._vecs: List[List[float]] = []  # 纯 Python 回退：存原始向量
        self._chunks: List[Dict] = []

    def build(self, embeddings: List[List[float]], chunks: List[Dict]) -> None:
        d = len(embeddings[0]) if embeddings else 0
        self._chunks = chunks
        if self._use_faiss:
            try:
                import numpy as np
                arr = np.array(embeddings, dtype="float32")
                self._index = faiss.IndexFlatIP(d)   # 内积（已归一化 => 余弦相似度）
                self._index.add(arr)
                print(f"[向量库] 使用 FAISS(IndexFlatIP) 构建索引，维度={d}，文档块={len(chunks)}")
                return
            except Exception as e:
                print(f"[向量库] FAISS 构建失败，退回纯 Python 检索：{e}")
                self._use_faiss = False
        # 纯 Python 回退
        self._vecs = embeddings
        print(f"[向量库] 使用纯 Python 暴力余弦检索（离线模式），维度={d}，文档块={len(chunks)}")

    def search(self, query_vec: List[float], top_k: int) -> List[Tuple[int, float]]:
        """召回 top_k，返回 (chunk_index, 相似度) 按相似度降序。"""
        if self._use_faiss and self._index is not None:
            import numpy as np
            D, I = self._index.search(np.array([query_vec], dtype="float32"), top_k)
            return [(int(I[0][i]), float(D[0][i])) for i in range(len(I[0])) if I[0][i] != -1]
        # 纯 Python：暴力余弦
        scored = []
        for i, v in enumerate(self._vecs):
            scored.append((i, _cosine(query_vec, v)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def _cosine(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度（输入已 L2 归一化时等于点积）。"""
    if not a or not b:
        return 0.0
    s = 0.0
    for x, y in zip(a, b):
        s += x * y
    return s


# ============================================================================
# ★ 可复用核心模块【BM25 词法检索】—— 智能体编排(RAG 混合检索) 复用
#   中文按单字、英文按词做 Okapi BM25；生产可换 jieba 分词或更优检索库。
# ============================================================================

class BM25:
    def __init__(self, docs_tokens: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs_tokens
        self.N = len(docs_tokens)
        self.avgdl = sum(len(d) for d in docs_tokens) / self.N if self.N else 0.0
        self.df: Dict[str, int] = {}
        for d in docs_tokens:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (self.N - df + 0.5) / (df + 0.5)) for t, df in self.df.items()}

    def search(self, query_tokens: List[str], top_k: int) -> List[Tuple[int, float]]:
        scores = []
        for i, d in enumerate(self.docs):
            dl = len(d)
            tf: Dict[str, int] = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in query_tokens:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / (
                        f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                    )
            scores.append((i, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


# ============================================================================
# ★ 可复用核心模块【混合检索 hybrid_retrieve】—— 智能体编排(RAG 管线) 复用
#   向量相似度 与 BM25 分数做 min-max 归一后加权融合；权重可调。
#   （生产可升级为 Reciprocal Rank Fusion，无需归一，更稳健）
# ============================================================================

def hybrid_retrieve(
    query: str,
    vector_store: VectorStore,
    bm25: BM25,
    embedder: EmbeddingProvider,
    top_k: int = 3,
    w_vec: float = 0.6,
    w_bm25: float = 0.4,
    candidate_k: int = 12,
) -> List[Dict]:
    q_tokens = _tokenize(query)
    q_vec = embedder.encode([query])[0]

    vec_hits = dict(vector_store.search(q_vec, candidate_k))    # {idx: similarity}
    bm25_hits = dict(bm25.search(q_tokens, candidate_k))        # {idx: score}

    nv = _minmax(vec_hits)     # 归一化向量相似度
    nb = _minmax(bm25_hits)    # 归一化 BM25 分数

    fused: Dict[int, float] = {}
    for i in set(nv) | set(nb):
        fused[i] = w_vec * nv.get(i, 0.0) + w_bm25 * nb.get(i, 0.0)

    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]
    results = []
    for idx, score in ranked:
        chunk = dict(vector_store._chunks[idx])
        chunk["score"] = round(score, 4)
        results.append(chunk)
    return results


def _minmax(d: Dict[int, float]) -> Dict[int, float]:
    if not d:
        return {}
    lo, hi = min(d.values()), max(d.values())
    if hi == lo:
        return {k: 1.0 for k in d}
    return {k: (v - lo) / (hi - lo) for k, v in d.items()}


# ============================================================================
# ★ 可复用核心模块【LLM 网关 LLMProvider + call_llm】—— 智能体编排(所有阶段) 复用
#   设计要点：调用逻辑封装为独立函数 call_llm；工厂 get_llm_provider 按名切换供应商。
# ============================================================================

class LLMProvider:
    """LLM 抽象基类，子类实现 chat(messages)->str。"""

    def chat(self, messages: List[Dict], **kwargs) -> str:
        raise NotImplementedError

    def stream_chat(self, messages: List[Dict], **kwargs):
        """
        ★ 流式生成（阶段2 为支持 SSE 新增）。
        默认实现退化为「一次性返回整段」，各子类可覆盖为真正的逐片流式。
        """
        yield self.chat(messages, **kwargs)


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI 兼容 Chat Completions 实现。DeepSeek / 通义(qwen) / Moonshot / 自建 vLLM
    均为此协议，只需改 base_url / api_key / model —— 这就是「快速切换供应商」的落点。
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    def chat(self, messages: List[Dict], temperature: float = 0.3, max_tokens: int = 1024) -> str:
        import requests  # 延迟导入，缺包时仅真实调用才报错
        resp = requests.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json={"model": self._model, "messages": messages, "temperature": temperature,
                  "max_tokens": max_tokens},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def stream_chat(self, messages: List[Dict], temperature: float = 0.3, max_tokens: int = 1024):
        """真正的逐片流式：OpenAI 兼容接口 stream=True，按 SSE 行解析 delta.content。"""
        import requests  # 延迟导入
        resp = requests.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json={"model": self._model, "messages": messages, "temperature": temperature,
                  "max_tokens": max_tokens, "stream": True},
            timeout=60,
            stream=True,
        )
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
                delta = obj["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError, IndexError):
                continue


class MockLLMProvider(LLMProvider):
    """
    离线占位实现：未配置 API Key 时也能跑通整条链路。
    它不会真正「生成」，而是把已召回的制度片段与问题回显，便于无密钥演示与单元测试。
    """

    def _build_answer(self, messages: List[Dict]) -> str:
        # 兼容两种调用形态：
        #  · RAG 路径（chat_app）：system 含「【来源: xxx.md】」检索片段，无工具结果块；
        #  · Agent 路径（agent_core._synthesize）：工具结果以「【工具 X 返回】{json}」
        #    形式烤进 system 文本（不会以 role=tool 传递，避免真实 LLM 接口报错），
        #    且末尾会追加「请根据上述上下文回答」占位 user 消息，需排除。
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        sys_txt = "\n".join(m["content"] for m in messages if m["role"] == "system")
        real_user_q = user_msgs[0] if user_msgs else ""

        # 制度来源文件名（RAG 路径的「【来源: xxx.md】」标记）
        sources: List[str] = re.findall(r"【来源:\s*([^】]+\.md)】", sys_txt)
        sources = list(dict.fromkeys(sources))

        # Agent 路径：解析烤进提示词的「【工具 X 返回】{json}」结果块
        tool_blocks: List[str] = []
        for seg in re.split(r"【工具\s*", sys_txt):
            m = re.match(r"([^】]+?)\s*返回】\s*(\{.*\})\s*$", seg, re.DOTALL)
            if not m:
                continue
            name = m.group(1).strip()
            try:
                obj = json.loads(m.group(2))
                part = obj.get("summary") or m.group(2)
            except Exception:
                part = m.group(2)
            tool_blocks.append(f"· 【{name}】{part}")

        head = "[离线 Mock 回复 · 未调用真实大模型，配置 LLM_API_KEY 后即为真实生成]\n"
        if tool_blocks:
            body = "根据已执行工具返回的结果，综合答复如下：\n" + "\n".join(tool_blocks) + "\n"
        elif real_user_q and "请根据上述上下文回答" not in real_user_q:
            body = f"针对您的问题「{real_user_q}」，"
        else:
            body = ""
        if sources:
            body += "已检索到相关制度来源：" + "、".join(sources) + "\n"
        if not tool_blocks and not sources:
            body = "暂无相关制度或数据可参考。"
        return head + body

    def chat(self, messages: List[Dict], **kwargs) -> str:
        return self._build_answer(messages)

    def stream_chat(self, messages: List[Dict], **kwargs):
        """模拟打字机：把整段答案按「词/标点」切片，逐片 yield 并轻微停顿。"""
        text = self._build_answer(messages)
        # 按中英文词 + 标点切分，尽量贴近自然断句
        pieces = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+|[^\s\u4e00-\u9fff a-zA-Z0-9]", text)
        buf = ""
        for p in pieces:
            buf += p
            # 遇到标点或累积到一定长度再吐出，减少事件数
            if p in "，。、；：！？\n" or len(buf) >= 6:
                yield buf
                buf = ""
                time.sleep(0.015)   # 仅演示用，生产真实流式无需 sleep
        if buf:
            yield buf


def get_llm_provider(name: str, cfg: dict) -> LLMProvider:
    """LLM 供应商工厂。预设常见供应商 base_url，可被 --base-url / --api-key / --model 覆盖。"""
    presets = {
        "openai":   ("https://api.openai.com/v1", "gpt-4o-mini"),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "qwen":     ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
        "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    }
    if name in ("mock",) or not cfg.get("llm_api_key"):
        return MockLLMProvider()
    base, model = presets.get(name, (cfg["llm_base_url"], cfg["llm_model"]))
    base = cfg.get("llm_base_url") or base
    model = cfg.get("llm_model") or model
    return OpenAICompatibleProvider(base, cfg["llm_api_key"], model)


def call_llm(messages: List[Dict], provider: Optional[LLMProvider] = None, cfg: Optional[dict] = None) -> str:
    """
    ★ 独立封装的 LLM 调用函数（用户要求）。
    切换供应商 = 仅改 provider 参数 / 配置，业务逻辑零改动。
    阶段1直接调用；Web 后端与智能体编排阶段原样复用此函数。
    """
    cfg = cfg or DEFAULT_CONFIG
    provider = provider or get_llm_provider(cfg["llm_provider"], cfg)
    return provider.chat(messages)


# ============================================================================
# ★ 可复用核心模块【提示词组装 build_messages】—— 智能体编排(提示词模板) 复用
#   强制「仅依据给定制度片段作答 + 标注来源 + 未知则明说」，满足医疗合规要求。
# ============================================================================

SYSTEM_TEMPLATE = """你是一名「医用耗材批次追溯智能助手」，面向医院业务操作员。
规则：
1. 只能依据下方【制度片段】回答问题，严禁编造法规条款或数据。
2. 每个结论必须标注【来源: 文件名】。
3. 若片段中无相关信息，明确回答「制度库中未找到相关信息」，不要猜测。
4. 回答简洁、面向一线操作，使用中文。

【制度片段】
{context}
"""


def build_messages(query: str, contexts: List[Dict]) -> List[Dict]:
    context_blob = "\n\n".join(
        f"【来源: {c['source']}】\n{c['text']}" for c in contexts
    ) or "（未检索到相关制度片段）"
    return [
        {"role": "system", "content": SYSTEM_TEMPLATE.format(context=context_blob)},
        {"role": "user", "content": query},
    ]


# ============================================================================
# ○ 阶段1专用【索引构建 + 命令行交互】—— 上线时由 Web 路由 / Agent 主循环替代，不复用
# ============================================================================

DEFAULT_CONFIG = {
    "llm_provider": "mock",      # mock / openai / deepseek / qwen / moonshot（有 api_key 时自动非 mock）
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "",
    "embed_provider": "auto",    # auto / tfidf / sklearn / hash / api
    "embed_base_url": "",
    "embed_api_key": "",
    "embed_model": "",
}


def build_index(docs_dir: str, cfg: dict) -> Tuple[VectorStore, BM25, List[Dict], EmbeddingProvider]:
    """摄取→切片→嵌入→建向量索引+BM25 索引（阶段1一次性内存构建）。"""
    docs = load_markdown_docs(docs_dir)
    chunks: List[Dict] = []
    for d in docs:
        for piece in chunk_text(d["text"], size=500, overlap=50):
            chunks.append({"source": d["source"], "text": piece})
    print(f"[切片] 共生成 {len(chunks)} 个 500 字文档块")

    embedder = get_embedding_provider(cfg["embed_provider"], cfg)
    if isinstance(embedder, (OfflineTfidfEmbeddingProvider, SklearnTfidfEmbeddingProvider)):
        embedder.fit([c["text"] for c in chunks])   # TF-IDF 类需先拟合语料
    embeddings = embedder.encode([c["text"] for c in chunks])

    vs = VectorStore()
    vs.build(embeddings, chunks)

    bm25 = BM25([_tokenize(c["text"]) for c in chunks])
    return vs, bm25, chunks, embedder


def answer_question(query: str, vs: VectorStore, bm25: BM25, embedder: EmbeddingProvider, cfg: dict, top_k: int):
    """阶段1问答一次：混合检索 -> 组装提示词 -> 调 LLM。"""
    contexts = hybrid_retrieve(query, vs, bm25, embedder, top_k=top_k)
    messages = build_messages(query, contexts)
    answer = call_llm(messages, cfg=cfg)
    return contexts, answer


def _ensure_sample_docs(docs_dir: str) -> None:
    """若目录为空则写入 5 类样例制度文档，保证原型开箱即跑。真实项目请替换为正式制度文件。"""
    os.makedirs(docs_dir, exist_ok=True)
    if glob.glob(os.path.join(docs_dir, "*.md")):
        return
    samples = {
        "01_医疗器械监管法规.md": "医疗器械监督管理条例要求医疗器械经营企业建立并执行进货查验记录制度。"
                                  "植入类医疗器械应当永久保存使用记录，确保可追溯。耗材追溯系统需留存供应商资质、"
                                  "注册证号与批次信息，任何出库与使用环节都要可回溯到具体批次。",
        "02_院内耗材SOP.md": "科室申领耗材须填写申领单并注明用途与手术排程。护士长复核后由库房发放。"
                             "高值耗材实行一物一码，低值耗材按批次管理。退回耗材须确认包装完整且无污染方可重新入库。",
        "03_批次追溯规范.md": "批次追溯以批号为主键，串联入库、领用、使用、退回、质控五个环节。"
                              "每一环节记录操作人、时间、地点与数量。系统需提供按耗材+批号的全链路时间线查询。",
        "04_效期管理要求.md": "耗材效期实行近效期先出（FEFO）原则。距失效不足三个月纳入预警，不足一个月冻结发放。"
                              "过期耗材必须隔离存放并启动不合格品处置流程，禁止用于临床。",
        "05_不良事件上报规范.md": "发生或发现医疗器械不良事件，操作员应在二十四小时内通过系统提交质量异常工单。"
                                  "工单须包含耗材名称、批号、事件描述与初步处理。管理员接单后组织评估并按规定上报监测机构。",
    }
    for name, txt in samples.items():
        Path(os.path.join(docs_dir, name)).write_text(txt, encoding="utf-8")
    print(f"[样例] 已在 {docs_dir} 生成 5 份样例制度文档（请替换为真实制度）")


def _print_result(query: str, contexts: List[Dict], answer: str) -> None:
    print("\n--- 召回制度片段（top{}）---".format(len(contexts)))
    for i, c in enumerate(contexts, 1):
        print(f"[{i}] 来源:{c['source']} | 融合分:{c['score']}")
        print("   " + c["text"][:120] + ("…" if len(c["text"]) > 120 else ""))
    print("\n--- 助手回答 ---")
    print(answer + "\n")


def main():
    parser = argparse.ArgumentParser(description="医用耗材批次追溯智能助手 · 阶段1 原型")
    parser.add_argument("--docs", default="./docs", help="Markdown 制度文档目录")
    parser.add_argument("--llm", default="mock", help="LLM 供应商: mock/openai/deepseek/qwen/moonshot")
    parser.add_argument("--model", default="", help="模型名（覆盖预设）")
    parser.add_argument("--api-key", default="", help="LLM API Key（设置后自动启用真实大模型）")
    parser.add_argument("--base-url", default="", help="LLM 兼容接口 base_url（覆盖预设）")
    parser.add_argument("--embed", default="auto", help="嵌入: auto/tfidf/sklearn/hash/api")
    parser.add_argument("--top-k", type=int, default=3, help="混合检索召回条数")
    parser.add_argument("--ask", default="", help="一次性提问（不进入交互循环）")
    parser.add_argument("--init-sample", action="store_true", help="若目录为空则生成样例文档")
    parser.add_argument("--selftest", action="store_true", help="离线自检：内置提问验证链路")
    args = parser.parse_args()

    if args.init_sample:
        _ensure_sample_docs(args.docs)
    elif not glob.glob(os.path.join(args.docs, "**", "*.md"), recursive=True):
        _ensure_sample_docs(args.docs)

    cfg = dict(DEFAULT_CONFIG)
    cfg.update({
        "llm_provider": args.llm, "llm_api_key": args.api_key,
        "llm_base_url": args.base_url, "llm_model": args.model,
        "embed_provider": args.embed,
    })

    print(f"[环境] FAISS 可用: {_FAISS_AVAILABLE} | 嵌入提供方: {cfg['embed_provider']} | LLM: {cfg['llm_provider']}")
    vs, bm25, chunks, embedder = build_index(args.docs, cfg)

    if args.ask:
        ctx, ans = answer_question(args.ask, vs, bm25, embedder, cfg, args.top_k)
        _print_result(args.ask, ctx, ans)
        return

    if args.selftest:
        for q in ["高值耗材怎么管理？", "效期快到了怎么办？", "不良事件要多久上报？",
                  "批次追溯要记录哪些环节？", "申领耗材要走什么流程？"]:
            ctx, ans = answer_question(q, vs, bm25, embedder, cfg, args.top_k)
            _print_result(q, ctx, ans)
        return

    print("\n=== 医用耗材批次追溯智能助手（阶段1原型）===")
    print("输入问题回车提交；输入 exit 退出。\n")
    while True:
        try:
            q = input("操作员> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit"):
            break
        ctx, ans = answer_question(q, vs, bm25, embedder, cfg, args.top_k)
        _print_result(q, ctx, ans)


if __name__ == "__main__":
    main()
