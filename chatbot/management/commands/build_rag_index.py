"""
Builds/rebuilds the local FAISS vector index used by chatbot.rag_engine.

Indexes two sources:
  1. Every .pdf, .docx and .txt file under settings.RAG_DOCS_DIR
     (default: <project_root>/rag_docs — create it and drop your HR policy
     files there)
  2. Every active row in chatbot.models.FAQ (question + answer as one chunk)

Usage:
    python manage.py build_rag_index

Run this again any time you add/change policy documents or FAQs — it wipes
and rebuilds the index from scratch each time, so stale/removed content
never lingers.
"""
import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rebuild the local RAG vector index from HR policy docs + the FAQ table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--docs-dir", default=None,
            help="Override the folder of policy documents to index.",
        )

    def handle(self, *args, **options):
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document

        from chatbot.rag_engine import (
            _get_embeddings, faiss_dir, docs_dir, reset_vectorstore_cache,
            FAISS_INDEX_NAME,
        )

        source_dir = options["docs_dir"] or docs_dir()
        os.makedirs(source_dir, exist_ok=True)

        raw_docs = []

        # 1) Policy documents -------------------------------------------------
        for fname in sorted(os.listdir(source_dir)):
            fpath = os.path.join(source_dir, fname)
            if not os.path.isfile(fpath):
                continue
            ext = fname.lower().rsplit(".", 1)[-1] if "." in fname else ""
            try:
                if ext == "pdf":
                    from langchain_community.document_loaders import PyPDFLoader
                    loaded = PyPDFLoader(fpath).load()
                elif ext == "docx":
                    from langchain_community.document_loaders import Docx2txtLoader
                    loaded = Docx2txtLoader(fpath).load()
                elif ext == "txt":
                    from langchain_community.document_loaders import TextLoader
                    loaded = TextLoader(fpath, encoding="utf-8").load()
                else:
                    continue
                for d in loaded:
                    d.metadata["source_type"] = "policy"
                    d.metadata["source_name"] = fname
                raw_docs += loaded
                self.stdout.write(f"Loaded {fname} ({len(loaded)} page(s))")
            except Exception as e:
                self.stderr.write(self.style.WARNING(f"Skipped {fname}: {e}"))

        # 2) FAQ table ----------------------------------------------------------
        from chatbot.models import FAQ
        faq_count = 0
        for faq in FAQ.objects.filter(is_active=True):
            raw_docs.append(Document(
                page_content=f"Q: {faq.question}\nA: {faq.answer}",
                metadata={
                    "source": f"faq:{faq.id}",
                    "source_type": "faq",
                    "source_name": faq.question,
                },
            ))
            faq_count += 1
        self.stdout.write(f"Loaded {faq_count} active FAQ(s)")

        if not raw_docs:
            self.stdout.write(self.style.WARNING(
                f"Nothing to index — put .pdf/.docx/.txt files in {source_dir} "
                f"and/or add FAQs in the admin, then rerun this command."
            ))
            return

        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)
        chunks = splitter.split_documents(raw_docs)

        target_dir = faiss_dir()
        # Wipe and rebuild rather than incrementally upsert, so edited/removed
        # FAQs and documents never leave stale chunks behind.
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir)
        os.makedirs(target_dir, exist_ok=True)

        from langchain_community.vectorstores import FAISS
        embeddings = _get_embeddings()
        store = FAISS.from_documents(chunks, embeddings)
        store.save_local(folder_path=target_dir, index_name=FAISS_INDEX_NAME)
        reset_vectorstore_cache()

        self.stdout.write(self.style.SUCCESS(
            f"Indexed {len(raw_docs)} source item(s) -> {len(chunks)} chunks into {target_dir}"
        ))
