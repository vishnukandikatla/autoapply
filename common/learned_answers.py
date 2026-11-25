"""
Intelligent memory for screening questions.
Remembers answers typed into the terminal and matches them against future
questions across runs using exact matching, keyword root overlap, and fuzzy similarity.
Stored as plain JSON at learned_answers.json in the project root.
"""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

_STORE_PATH = Path(__file__).resolve().parent.parent / "learned_answers.json"

STOPWORDS = {
    "are", "you", "in", "to", "or", "the", "a", "an", "is", "of", "your",
    "for", "with", "have", "do", "what", "please", "kindly", "enter", "state",
    "specify", "how", "would", "can", "if", "this", "that", "on", "at", "be",
    "which", "our", "we", "from", "as", "any", "per", "all", "my", "me",
    "give", "tell", "select", "choose", "provide", "share"
}


def _stem(word: str) -> str:
    word = word.lower()
    for suffix in ("tional", "tion", "ing", "able", "ment", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def _extract_roots(text: str) -> set[str]:
    clean = re.sub(r"https?://\S+", "", text.lower())
    clean = re.sub(r"[^\w\s]", " ", clean)
    words = [w for w in clean.split() if w not in STOPWORDS and len(w) > 1]
    return {_stem(w) for w in words}


def _normalize(question: str) -> str:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"[^\w\s]", "", q)
    return q


def _load() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    _STORE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_answer(question: str, answer: str):
    """Saves a user-provided answer for future applications."""
    if not question or not answer:
        return
    data = _load()
    key = _normalize(question)
    data[key] = answer.strip()
    _save(data)
    print(f"  💾 Saved answer for future use: '{answer.strip()}' (question: {question[:70]}...)")


def get_answer(question: str) -> str | None:
    """
    Finds a saved answer for the question.
    1. Exact normalized match
    2. Significant keyword root overlap (>= 65% content root match with at least 2 common roots)
    3. High fuzzy sequence similarity (>= 75%)
    """
    data = _load()
    if not data or not question:
        return None

    norm_q = _normalize(question)
    # 1. Exact match
    if norm_q in data:
        return data[norm_q]

    # 2. Keyword root overlap & fuzzy matching
    q_roots = _extract_roots(question)
    best_score = 0.0
    best_answer = None
    best_match_q = None

    for stored_q, ans in data.items():
        stored_roots = _extract_roots(stored_q)
        if not stored_roots or not q_roots:
            continue

        common_roots = q_roots & stored_roots
        if len(common_roots) >= 2:
            overlap = len(common_roots) / min(len(q_roots), len(stored_roots))
            seq_ratio = SequenceMatcher(None, stored_q, norm_q).ratio()
            score = max(overlap, seq_ratio)
            if overlap >= 0.65 and score > best_score:
                best_score = score
                best_answer = ans
                best_match_q = stored_q

        # Fallback to high sequence similarity
        seq = SequenceMatcher(None, stored_q, norm_q).ratio()
        if seq >= 0.75 and seq > best_score:
            best_score = seq
            best_answer = ans
            best_match_q = stored_q

    if best_answer:
        return best_answer

    return None


def match_option(saved_answer: str, options: list[dict]) -> dict | None:
    """
    Matches a saved answer against a list of radio/checkbox options
    using exact text, semantic Yes/No intent, or substring matching.
    """
    if not saved_answer or not options:
        return None

    clean_ans = saved_answer.strip().lower()

    # 1. Exact text match
    for opt in options:
        if opt.get("label", "").strip().lower() == clean_ans:
            return opt

    # 2. Semantic Yes/No matching
    if clean_ans in ("yes", "y", "true", "yes - interested", "interested"):
        for opt in options:
            lbl = opt.get("label", "").strip().lower()
            if (
                lbl.startswith("yes")
                or "willing" in lbl
                or "agree" in lbl
                or "interested" in lbl
            ):
                return opt
    elif clean_ans in ("no", "n", "false", "not willing", "disagree"):
        for opt in options:
            lbl = opt.get("label", "").strip().lower()
            if (
                lbl.startswith("no")
                or "not willing" in lbl
                or "disagree" in lbl
                or "cannot" in lbl
            ):
                return opt

    # 3. Substring containment
    for opt in options:
        lbl = opt.get("label", "").strip().lower()
        if clean_ans in lbl or lbl in clean_ans:
            return opt

    return None
