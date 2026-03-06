"""Singleton module holding all memory service instances.

Provides init_services() to initialise everything once, and get_xxx_service()
accessors for each service. Thread-safe initialisation via a simple flag.
"""
import logging
import os

logger = logging.getLogger(__name__)

DATA_ROOT = os.path.expanduser('~/.qq_bot_data')

_id_mapping: 'IDMappingService | None' = None
_markdown_memory: 'MarkdownMemoryService | None' = None
_embedding: 'EmbeddingService | None' = None
_checkpoint: 'CheckpointStore | None' = None
_scheduler: 'SchedulerService | None' = None
_inited = False


def init_services(data_root: str | None = None) -> None:
    """Initialise all memory services. Safe to call multiple times (no-op after first)."""
    global _id_mapping, _markdown_memory, _embedding, _checkpoint, _scheduler, _inited
    if _inited:
        return

    root = data_root or DATA_ROOT

    # Ensure directory structure
    for subdir in ('persona', 'people', 'groups', 'notes', 'db', 'raw_messages'):
        os.makedirs(os.path.join(root, subdir), exist_ok=True)
    os.makedirs(os.path.join(root, 'db', 'chroma'), exist_ok=True)

    from qq_social_bot_app.intelligence.social_memory.id_mapping_service import IDMappingService
    from qq_social_bot_app.intelligence.social_memory.markdown_memory import MarkdownMemoryService
    from qq_social_bot_app.intelligence.social_memory.embedding_service import EmbeddingService
    from qq_social_bot_app.intelligence.social_memory.checkpoint_store import CheckpointStore
    from qq_social_bot_app.intelligence.scheduler.scheduler_service import SchedulerService

    _id_mapping = IDMappingService(
        db_path=os.path.join(root, 'db', 'mappings.db'),
        data_root=root,
    )
    _markdown_memory = MarkdownMemoryService(data_root=root)
    _embedding = EmbeddingService(
        chroma_path=os.path.join(root, 'db', 'chroma'),
        markdown_service=_markdown_memory,
    )
    _checkpoint = CheckpointStore(
        db_path=os.path.join(root, 'db', 'mappings.db'))
    _scheduler = SchedulerService(
        db_path=os.path.join(root, 'db', 'scheduler_jobs.db'))

    _inited = True
    logger.info('Memory services initialised (data_root=%s)', root)


def get_id_mapping_service() -> 'IDMappingService':
    if _id_mapping is None:
        raise RuntimeError('Memory services not initialised. Call init_services() first.')
    return _id_mapping


def get_markdown_service() -> 'MarkdownMemoryService':
    if _markdown_memory is None:
        raise RuntimeError('Memory services not initialised. Call init_services() first.')
    return _markdown_memory


def get_embedding_service() -> 'EmbeddingService':
    if _embedding is None:
        raise RuntimeError('Memory services not initialised. Call init_services() first.')
    return _embedding


def get_checkpoint_store() -> 'CheckpointStore':
    if _checkpoint is None:
        raise RuntimeError('Memory services not initialised. Call init_services() first.')
    return _checkpoint


def get_scheduler_service() -> 'SchedulerService':
    if _scheduler is None:
        raise RuntimeError('Memory services not initialised. Call init_services() first.')
    return _scheduler


def get_data_root() -> str:
    return DATA_ROOT
