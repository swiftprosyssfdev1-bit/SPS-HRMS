"""
Standalone diagnostic — run this to verify the full RAG pipeline works.

Run from your project root (same folder as manage.py), with the venv active:

    python rag_diagnostic.py

Watch which "STEP" line is the LAST one printed before it dies — that
tells us the exact crash point.
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "prostaff.settings")
django.setup()

print("STEP 1: Django setup OK", flush=True)

from chatbot.rag_engine import _get_embeddings, faiss_dir, _get_llm, FAISS_INDEX_NAME

print("STEP 2: imports OK", flush=True)

embeddings = _get_embeddings()
print("STEP 3: embeddings object created OK", flush=True)

from langchain_community.vectorstores import FAISS

index_path = os.path.join(faiss_dir(), FAISS_INDEX_NAME)
faiss_file = index_path + ".faiss"
pkl_file   = index_path + ".pkl"

if not os.path.isfile(faiss_file) or not os.path.isfile(pkl_file):
    print(f"STEP 4 FAILED: index files not found at {index_path}")
    print("Run:  python manage.py build_rag_index")
    raise SystemExit(1)

print("STEP 4: index files exist OK", flush=True)

store = FAISS.load_local(
    folder_path=faiss_dir(),
    embeddings=embeddings,
    index_name=FAISS_INDEX_NAME,
    allow_dangerous_deserialization=True,
)
print(f"STEP 5: FAISS.load_local() OK — {store.index.ntotal} vectors loaded", flush=True)

print("STEP 6: about to run similarity_search...", flush=True)
docs = store.similarity_search("What happens if I take leave after my 12 days are over", k=4)
print("STEP 7: similarity_search OK, got", len(docs), "docs", flush=True)

print("STEP 8: about to call the LLM (this may take a while on CPU)...", flush=True)
llm = _get_llm()
result = llm.invoke("Say hello in one word.")
print("STEP 9: LLM call OK ->", result.content, flush=True)

print("ALL STEPS PASSED — RAG pipeline is healthy.", flush=True)
