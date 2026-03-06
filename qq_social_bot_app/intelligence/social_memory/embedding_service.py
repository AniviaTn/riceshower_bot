"""Embedding service using ChromaDB + DashScope text-embedding-v3.

Provides semantic search over Markdown memory files.
"""
import logging
import os
import time
from typing import List, Optional

import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

logger = logging.getLogger(__name__)

_COLLECTION_NAME = 'memory_files'
_BATCH_SIZE = 25  # DashScope max batch per call


class DashScopeEmbeddingFunction(EmbeddingFunction[Documents]):
    """ChromaDB custom embedding function using DashScope text-embedding-v3."""

    def __init__(self, api_key: str | None = None,
                 model: str = 'text-embedding-v3'):
        self._api_key = api_key or os.environ.get('DASHSCOPE_API_KEY', '')
        self._model = model

    def __call__(self, input: Documents) -> Embeddings:
        import dashscope

        if self._api_key:
            dashscope.api_key = self._api_key

        all_embeddings: Embeddings = []

        for i in range(0, len(input), _BATCH_SIZE):
            batch = input[i:i + _BATCH_SIZE]
            resp = dashscope.TextEmbedding.call(
                model=self._model,
                input=batch,
                dimension=1024,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f'DashScope embedding failed: {resp.code} {resp.message}')
            for item in resp.output['embeddings']:
                all_embeddings.append(item['embedding'])

        return all_embeddings


class EmbeddingService:
    """Manages ChromaDB collection for memory file embeddings."""

    def __init__(self, chroma_path: str,
                 markdown_service: 'MarkdownMemoryService' = None):
        os.makedirs(chroma_path, exist_ok=True)
        self._client = chromadb.PersistentClient(path=chroma_path)
        self._embed_fn = DashScopeEmbeddingFunction()
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=self._embed_fn,
            metadata={'hnsw:space': 'cosine'},
        )
        self._markdown_service = markdown_service

    # ----------------------------------------------------------
    # Index operations
    # ----------------------------------------------------------

    def index_file(self, rel_path: str, content: str) -> None:
        """Upsert a document into the collection. ID = relative path."""
        meta = self._build_metadata(rel_path)
        self._collection.upsert(
            ids=[rel_path],
            documents=[content],
            metadatas=[meta],
        )

    def remove_file(self, rel_path: str) -> None:
        """Remove a document from the collection."""
        try:
            self._collection.delete(ids=[rel_path])
        except Exception:
            logger.debug('Failed to remove %s from index', rel_path, exc_info=True)

    def search(self, query: str, n_results: int = 5,
               scope: str | None = None,
               user_id: str | None = None,
               group_id: str | None = None) -> List[dict]:
        """Semantic search with optional filters.

        Returns list of {path, content, score, scope, entity_id}.
        """
        where = self._build_where_filter(scope, user_id, group_id)

        kwargs = dict(
            query_texts=[query],
            n_results=n_results,
        )
        if where:
            kwargs['where'] = where

        try:
            results = self._collection.query(**kwargs)
        except Exception:
            logger.exception('ChromaDB search failed')
            return []

        items = []
        if results and results.get('ids'):
            ids = results['ids'][0]
            docs = results['documents'][0] if results.get('documents') else [''] * len(ids)
            distances = results['distances'][0] if results.get('distances') else [0.0] * len(ids)
            metadatas = results['metadatas'][0] if results.get('metadatas') else [{}] * len(ids)

            for i, doc_id in enumerate(ids):
                items.append({
                    'path': doc_id,
                    'content': docs[i],
                    'score': 1.0 - distances[i],  # cosine distance → similarity
                    'scope': metadatas[i].get('scope', ''),
                    'entity_id': metadatas[i].get('entity_id', ''),
                })

        return items

    def reindex_all(self) -> int:
        """Full reindex from markdown files. Only runs if collection is empty."""
        count = self._collection.count()
        if count > 0:
            logger.info('ChromaDB collection has %d docs, skipping reindex', count)
            return count

        if not self._markdown_service:
            logger.warning('No markdown service available for reindex')
            return 0

        files = self._markdown_service.enumerate_indexable_files()
        if not files:
            logger.info('No indexable files found')
            return 0

        logger.info('Reindexing %d files into ChromaDB...', len(files))
        indexed = 0

        # Batch upsert for efficiency
        batch_ids = []
        batch_docs = []
        batch_metas = []

        for rel_path in files:
            content = self._markdown_service.read_file(rel_path)
            if not content or not content.strip():
                continue
            batch_ids.append(rel_path)
            batch_docs.append(content)
            batch_metas.append(self._build_metadata(rel_path))

            if len(batch_ids) >= _BATCH_SIZE:
                self._collection.upsert(
                    ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                indexed += len(batch_ids)
                batch_ids, batch_docs, batch_metas = [], [], []

        if batch_ids:
            self._collection.upsert(
                ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            indexed += len(batch_ids)

        logger.info('Reindexed %d files', indexed)
        return indexed

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    @staticmethod
    def _build_metadata(rel_path: str) -> dict:
        """Build metadata dict from a relative path."""
        parts = rel_path.split('/')
        scope = parts[0] if parts else ''
        entity_id = parts[1] if len(parts) > 2 else ''
        return {
            'path': rel_path,
            'scope': scope,
            'entity_id': entity_id,
            'updated_at': time.time(),
        }

    @staticmethod
    def _build_where_filter(scope: str | None, user_id: str | None,
                            group_id: str | None) -> dict | None:
        """Build ChromaDB where filter from search parameters."""
        conditions = []

        if scope and scope != 'all':
            conditions.append({'scope': scope})

        if user_id:
            conditions.append({'scope': 'people'})
            conditions.append({'entity_id': user_id})

        if group_id:
            conditions.append({'scope': 'groups'})
            conditions.append({'entity_id': group_id})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {'$and': conditions}
