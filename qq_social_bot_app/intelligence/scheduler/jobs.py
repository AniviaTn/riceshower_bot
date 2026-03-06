"""Scheduled job definitions.

Contains async job functions registered with the SchedulerService.
"""
import logging
import os
import time

from qq_social_bot_app.intelligence.social_memory.memory_services import (
    get_checkpoint_store, get_data_root,
)
from qq_social_bot_app.intelligence.social_memory.raw_message_store import RawMessageStore
from qq_social_bot_app.intelligence.social_memory.summarization_service import SummarizationService

logger = logging.getLogger(__name__)

_STALE_THRESHOLD = 86400.0  # 24 hours — groups not summarized in this window are "stale"


async def summarize_all_groups() -> None:
    """Iterate over all known groups and summarize new messages.

    Registered as a periodic cron job (e.g. every 4 hours).
    """
    data_root = get_data_root()
    raw_store = RawMessageStore(
        base_dir=os.path.join(data_root, 'raw_messages'))
    service = SummarizationService(raw_store=raw_store)

    group_ids = raw_store.get_known_group_ids()
    if not group_ids:
        logger.debug('No groups found for summarization')
        return

    logger.info('Starting periodic summarization for %d group(s)', len(group_ids))

    for group_id in group_ids:
        try:
            files = await service.summarize_group(group_id)
            if files:
                logger.info('Summarized group %s: %d files %s',
                            group_id, len(files), files)
        except Exception:
            logger.exception('Failed to summarize group %s', group_id)

    logger.info('Periodic summarization complete')


async def startup_check() -> None:
    """Check for groups that haven't been summarized in over 24 hours.

    Called once at startup as a non-blocking task to catch up on missed
    summarization windows (e.g. after a restart).
    """
    data_root = get_data_root()
    raw_store = RawMessageStore(
        base_dir=os.path.join(data_root, 'raw_messages'))
    checkpoint_store = get_checkpoint_store()
    service = SummarizationService(raw_store=raw_store)

    group_ids = raw_store.get_known_group_ids()
    if not group_ids:
        return

    now = time.time()
    stale_groups = []

    for group_id in group_ids:
        cp = checkpoint_store.get_checkpoint(group_id)
        if cp is None or (now - cp['last_checkpoint']) > _STALE_THRESHOLD:
            stale_groups.append(group_id)

    if not stale_groups:
        logger.info('Startup check: all groups are up to date')
        return

    logger.info('Startup check: %d group(s) need catch-up summarization',
                len(stale_groups))

    for group_id in stale_groups:
        try:
            files = await service.summarize_group(group_id)
            if files:
                logger.info('Startup catch-up: group %s: %d files %s',
                            group_id, len(files), files)
        except Exception:
            logger.exception('Startup catch-up failed for group %s', group_id)

    logger.info('Startup check complete')
