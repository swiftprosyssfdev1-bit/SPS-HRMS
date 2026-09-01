"""
Local RAG (Retrieval-Augmented Generation) engine for the Your Assist chatbot.

Runs fully on-machine: Ollama serves both the embedding model and the answer
LLM (no API key, no per-call cost), and FAISS is used as the persistent
vector store. This file has no side effects at import time — every LangChain
import happens lazily inside functions so `python manage.py runserver` still
starts even if Ollama/FAISS aren't installed yet.

Two sources are indexed by `build_rag_index` (see the management command):
  1. HR policy documents dropped into settings.RAG_DOCS_DIR (.pdf, .docx, .txt)
  2. Every active row in chatbot.models.FAQ (question + answer)

Requires (add to your environment):
    pip install langchain langchain-ollama langchain-community \
                langchain-text-splitters faiss-cpu pypdf docx2txt

And Ollama running locally with two models pulled:
    ollama pull qwen3.5:4b
    ollama pull nomic-embed-text
"""
import logging
import os
import re

from django.conf import settings

logger = logging.getLogger(__name__)

# Words too generic to count as "the question is about this" when deciding
# whether retrieved context is actually relevant (see _context_is_relevant).
_STOPWORDS = {
    'this', 'that', 'what', 'when', 'where', 'which', 'who', 'whom', 'whose',
    'will', 'would', 'shall', 'should', 'could', 'about', 'with', 'from',
    'have', 'does', 'doing', 'been', 'being', 'their', 'there', 'here',
    'month', 'today', 'please', 'thanks', 'thank',
}

# FAISS index is stored as two files: <name>.faiss + <name>.pkl
FAISS_INDEX_NAME = "prostaff_kb"

_vectorstore = None  # lazy singleton, one per process


def _get_embeddings():
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(model=getattr(settings, "RAG_EMBED_MODEL", "nomic-embed-text"))


def _get_llm():
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=getattr(settings, "RAG_LLM_MODEL", "qwen3.5:4b"),
        temperature=0,
        timeout=getattr(settings, "RAG_LLM_TIMEOUT", 170),  # seconds; browser gives 180s
    )


def faiss_dir():
    path = str(getattr(settings, "RAG_CHROMA_DIR", settings.BASE_DIR / "rag_index"))
    os.makedirs(path, exist_ok=True)
    return path


# Keep old name as alias so settings.RAG_CHROMA_DIR still works
def chroma_dir():
    return faiss_dir()


def docs_dir():
    path = str(getattr(settings, "RAG_DOCS_DIR", settings.BASE_DIR / "rag_docs"))
    os.makedirs(path, exist_ok=True)
    return path


def _faiss_index_path():
    """Returns the path to the .faiss file (without extension)."""
    return os.path.join(faiss_dir(), FAISS_INDEX_NAME)


def get_vectorstore():
    """Lazily loads the persisted FAISS index. Returns None if the index
    hasn't been built yet, so callers can fall back gracefully instead of
    crashing the chatbot."""
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    try:
        from langchain_community.vectorstores import FAISS
    except Exception:
        logger.exception("langchain-community / faiss-cpu not installed — RAG disabled")
        return None

    index_path = _faiss_index_path()
    faiss_file = index_path + ".faiss"
    pkl_file   = index_path + ".pkl"

    if not os.path.isfile(faiss_file) or not os.path.isfile(pkl_file):
        logger.warning(
            "RAG index not found at %s — run `python manage.py build_rag_index`.",
            index_path,
        )
        return None

    try:
        store = FAISS.load_local(
            folder_path=faiss_dir(),
            embeddings=_get_embeddings(),
            index_name=FAISS_INDEX_NAME,
            allow_dangerous_deserialization=True,
        )
    except Exception:
        logger.exception("Could not load FAISS index (is Ollama running?)")
        return None

    _vectorstore = store
    return _vectorstore


def reset_vectorstore_cache():
    """Call this after rebuilding the index so the next query reloads it
    instead of reusing a stale cached store."""
    global _vectorstore
    _vectorstore = None


RAG_PROMPT = """You are **Your Assist**, an AI HR assistant chatbot built into SPS HRMS. You are NOT a human being.

The employee currently logged in is: {user_name} (Role: {user_role}).

SYSTEM KNOWLEDGE & NAVIGATION:
SPS HRMS contains the following Sidebar Modules and Pages. Use this knowledge to guide the user on how to use the system based on their role ({user_role}):
1. Dashboard: Shows daily attendance (Present/Absent/On-Leave). COO and Senior HR have a Branch Switcher here to view metrics per branch.
2. My Profile: View personal details, employment status, and uploaded documents.
3. Attendance (under My Profile): "Punch In/Out" screen. Users click a button to record daily attendance.
4. My Monthly Attendance: Shows a calendar view of past attendance (Present, Absent, Leave).
5. My Payslips: Users can download their monthly salary slips here.
6. My Letters: Users can download official HR documents (Offer letter, warning letter, etc.).
7. Directory: Company-wide list of employees and their roles/branches (Managers, HR, COO, CTO only).
8. Projects: Shows active projects, client names, and assigned team members (Project Managers, HR, COO, CTO only).
9. Hourly Tracker: Users log their daily production numbers and hours worked.
10. Ask a Query: Employees can raise tickets/queries directly to HR or their Manager.
11. Manpower by Branch: (HR/COO only) Shows total employees and attendance statistics across the company.
12. Leave Approval: (Managers, GM, CTO, HR only) Where executives approve or reject pending leave requests from their subordinates.
13. Increment Approval: (Senior HR/COO only) Where salary bumps are approved.
14. Reports: (HR/COO only) Exportable data on payroll and attendance.
15. Team Attendance: (Managers only) See when direct reports punched in/out.

STRICT RULES — follow without exception:
1. Answer the user strictly according to their role ({user_role}). If they ask how to do something their role cannot do (e.g., an Employee asking how to approve leaves), explicitly state they do not have the permission to access that page.
2. You are an AI assistant. NEVER claim to be human, a colleague, or a friend.
3. NEVER reveal, guess, or fabricate: passwords, usernames, credentials, or salaries/payslips of OTHER employees.
4. Do NOT use general knowledge. Do NOT invent policies or company rules outside of the Context provided.
5. If the context does not contain enough information regarding specific company policies, reply with exactly: NO_ANSWER_IN_CONTEXT
6. Be concise and direct. Give them step-by-step navigation instructions if they ask how to find a page.
{history}
Context:
{context}

Question: {question}

Answer:"""


def _format_history(chat_history, max_turns=2):
    """Renders the last max_turns {"q","a"} dicts as a short block, or ""
    if there's no history. Kept separate from RAG_PROMPT's other fields so
    callers with no history don't have to pass an empty string explicitly."""
    if not chat_history:
        return ""
    recent = chat_history[-max_turns:]
    lines = "\n".join(f"Q: {turn.get('q', '')}\nA: {turn.get('a', '')}" for turn in recent if turn.get('q'))
    if not lines:
        return ""
    return f"\nRecent conversation (context only, see rules above):\n{lines}\n"


def _context_is_relevant(question, context):
    """Cheap relevance gate run before the LLM ever sees the retrieved
    chunks. qwen3.5:4b at temperature=0 does not reliably follow the
    NO_ANSWER_IN_CONTEXT instruction when the retrieved context is
    superficially on-topic (shares role names, generic HR phrasing) but
    doesn't actually answer the question — it tends to stitch fragments
    together into a confident-sounding but wrong answer instead. If none
    of the question's substantive (4+ letter, non-stopword) words appear
    anywhere in the retrieved context, the docs almost certainly don't
    cover this question, so we skip the LLM call rather than risk that.
    This is a blunt keyword check, not semantic matching — it will not
    catch every ungrounded case (e.g. a bare pronoun-only follow-up like
    "which project team?" with no distinctive word of its own), but it
    catches the common one: a topic (like "appreciation") that isn't in
    the knowledge base at all."""
    words = {w for w in re.findall(r"[a-z']+", question.lower())
             if len(w) >= 4 and w not in _STOPWORDS}
    if not words:
        return True  # nothing substantive to check against; let the LLM try
    context_lower = context.lower()
    return any(w in context_lower for w in words)


_REFUSAL_PATTERNS = (
    "as an ai language model",
    "i don't have access to your personal data",
    "i do not have access to your personal data",
    "i'm not able to access personal data",
    "i am not able to access personal data",
)


def _looks_like_generic_refusal(answer):
    """Catches the base-model 'as an AI language model, I don't have
    access...' refusal persona leaking through despite the system prompt
    telling it otherwise — observed on questions like 'show me my
    attendance for the last 7 days', which the bot can genuinely answer
    from the logged-in user's own records."""
    lower = answer.lower()
    return any(p in lower for p in _REFUSAL_PATTERNS)


def _looks_repetitive(answer, min_repeats=3):
    """Detects the 'stitches the same sentence structure together N
    times' degenerate pattern observed from the local LLM on weakly
    grounded questions (e.g. an answer that repeated "The HR system also
    manages employee information, including X" four times with only the
    trailing word changing). A cheap prefix-repeat check, not a real
    coherence check — but catches this specific, observed failure mode."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', answer) if s.strip()]
    if len(sentences) < min_repeats:
        return False
    counts = {}
    for s in sentences:
        prefix = ' '.join(s.split()[:5]).lower()
        counts[prefix] = counts.get(prefix, 0) + 1
        if counts[prefix] >= min_repeats:
            return True
    return False


def rag_answer(user, question, k=6, chat_history=None):
    """Returns an answer string, or None if the index is missing, retrieval
    found nothing relevant, or the model says the context doesn't cover it.
    `user` is used to give the LLM context on who it's talking to.
    `chat_history` is an optional list of {"q": str, "a": str} dicts, most
    recent last — see get_reply()'s docstring in matcher.py."""
    store = get_vectorstore()
    if store is None:
        return None

    try:
        # Pull more candidates than we'll keep (k) so a dynamically-phrased
        # question — one that doesn't match the doc's wording closely — still
        # has a decent chance of surfacing the right chunk somewhere in the
        # pool, then let the relevance gate below narrow it back down.
        scored = store.similarity_search_with_score(question, k=max(k * 2, 8))
    except Exception:
        logger.exception("RAG similarity_search failed")
        return None

    # FAISS returns L2 distance (lower = closer). Keep only chunks that are
    # both reasonably close AND share a real keyword with the question —
    # distance alone is too easily fooled by generic HR phrasing shared
    # across unrelated policies.
    docs = [d for d, score in sorted(scored, key=lambda pair: pair[1])[:k]]

    # Even if docs is empty, we still want to answer conversational questions about the user's identity
    context = "\n\n---\n\n".join(d.page_content for d in docs) if docs else "No specific policy documents found for this query."

    if docs and not _context_is_relevant(question, context):
        logger.info("RAG: retrieved context has no keyword overlap with question — skipping LLM call")
        return None

    # Safely get user info
    user_name = getattr(user, 'first_name', '') or getattr(user, 'username', 'Employee')
    user_role = getattr(user, 'role', 'Employee')

    prompt = RAG_PROMPT.format(
        user_name=user_name, 
        user_role=user_role,
        history=_format_history(chat_history),
        context=context, 
        question=question
    )

    try:
        llm = _get_llm()
        result = llm.invoke(prompt)
        answer = (result.content or "").strip()
    except Exception:
        logger.exception("RAG LLM call failed (is `ollama serve` running?)")
        return None

    if not answer or "NO_ANSWER_IN_CONTEXT" in answer:
        return None

    if _looks_like_generic_refusal(answer) or _looks_repetitive(answer):
        logger.info("RAG: discarding degenerate/refusal-boilerplate answer")
        return None

    return answer
