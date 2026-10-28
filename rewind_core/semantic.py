"""Local semantic matching. Pure stdlib by default (synonym-aware sparse vectors); optional dense model."""
import math
import os
import re

from .util import *  # noqa: F401,F403

# words that mean the same thing in debugging notes collapse to one canonical term
_GROUPS = [
    "increase raise bump boost extend expand grow enlarge higher",
    "decrease lower reduce cut shrink lessen drop smaller",
    "timeout deadline ttl expire expiry",
    "error exception failure crash bug fault problem",
    "retry reattempt backoff",
    "fix repair resolve patch solve correct",
    "remove delete strip discard",
    "add insert append introduce include",
    "change modify alter update edit rewrite",
    "cache memoize memoise store",
    "slow latency lag sluggish performance",
    "request call fetch query",
    "server backend service endpoint",
    "limit cap threshold maximum",
    "connection socket pool",
    "login signin auth authentication authorize",
    "wait sleep delay pause",
]
_CANON = {w: g.split()[0] for g in _GROUPS for w in g.split()}
_STOP = set("the a an to of in on for and or is it with by at from this that was be as are into up".split())
_dense = None


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def _terms(text):
    text = re.sub(r"(\d+)\s*(?:s|sec|secs|seconds)\b", r"\1", (text or "").lower())
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in _STOP]
    # synonyms are looked up before and after stemming: raising -> rais -> raise -> increase
    return [_CANON.get(w) or _CANON.get(_stem(w)) or _CANON.get(_stem(w) + "e") or _stem(w) for w in words]


def embed(text):
    """Sparse unit vector: canonical words, word pairs and character trigrams (typo tolerance)."""
    terms = _terms(text)
    vec = {}
    for t in terms:
        vec["w:" + t] = vec.get("w:" + t, 0) + 1.0
        for i in range(len(t) - 2):
            k = "c:" + t[i:i + 3]
            vec[k] = vec.get(k, 0) + 0.3
    for a, b in zip(terms, terms[1:]):
        vec["b:" + a + " " + b] = vec.get("b:" + a + " " + b, 0) + 0.6
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


def _load_dense():
    """Opt-in neural embeddings: REWIND_EMBEDDINGS=st and sentence-transformers installed (stays local)."""
    global _dense
    if _dense is None:
        _dense = False
        if os.environ.get("REWIND_EMBEDDINGS") == "st":
            try:
                from sentence_transformers import SentenceTransformer
                _dense = SentenceTransformer(os.environ.get("REWIND_MODEL", "all-MiniLM-L6-v2"))
            except Exception:  # missing package or model: quietly use the built-in matcher
                _dense = False
    return _dense


def similarity(a, b):
    """0..1 similarity between two short texts."""
    model = _load_dense()
    if model:
        va, vb = model.encode([a or "", b or ""], normalize_embeddings=True)
        return max(0.0, float((va * vb).sum()))
    ea, eb = embed(a), embed(b)
    if len(ea) > len(eb):
        ea, eb = eb, ea
    cosine = sum(v * eb.get(k, 0.0) for k, v in ea.items())
    return max(cosine, 0.9 * _fuzzy(_terms(a), _terms(b)))


def _fuzzy(ta, tb):
    """Typo tolerance: long words that nearly match (databse/database) count as shared."""
    if not ta or not tb:
        return 0.0
    import difflib
    matched = 0.0
    for t in ta:
        if t in tb:
            matched += 1
        elif len(t) >= 5:
            best = max((difflib.SequenceMatcher(None, t, u).ratio() for u in tb if len(u) >= 5), default=0)
            matched += best if best >= 0.8 else 0
    return 2 * matched / (len(ta) + len(tb))


def best_matches(query, items, text_of, limit=5, floor=0.2):
    """items ranked by similarity to query; [(score, item)]."""
    scored = [(similarity(query, text_of(it)), it) for it in items]
    return sorted([s for s in scored if s[0] >= floor], key=lambda s: -s[0])[:limit]
