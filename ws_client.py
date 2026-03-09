#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point: initialise aU, memory services, scheduler, and start WS server."""

import asyncio
import logging
import os
import sys

os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from agentuniverse.base.agentuniverse import AgentUniverse
from qq_social_bot_app.intelligence.social_memory.memory_services import (
    init_services, get_scheduler_service,
)
from qq_social_bot_app.intelligence.scheduler.jobs import (
    summarize_all_groups, startup_check,
)
from qq_social_bot_app.intelligence.utils import bot_config
from qq_social_bot_app.intelligence.onebot.dispatcher import handler, now_str

from websockets.asyncio.server import serve

CONFIG_PATH = os.path.join(PROJECT_ROOT, 'qq_social_bot_app', 'config', 'config.toml')


async def main() -> None:
    print(f"[{now_str()}] Initialising AgentUniverse...")
    AgentUniverse().start(config_path=CONFIG_PATH)
    print(f"[{now_str()}] Agent framework ready.")

    print(f"[{now_str()}] Initialising memory services...")
    init_services()
    print(f"[{now_str()}] Memory services ready.")

    print(f"[{now_str()}] Starting scheduler...")
    scheduler = get_scheduler_service()
    scheduler.start()

    scheduler.add_cron_job(
        job_id='summarize_all_groups',
        func=summarize_all_groups,
        cron_expr='0 */4 * * *',
    )
    print(f"[{now_str()}] Scheduler ready. Jobs: {scheduler.list_jobs()}")

    asyncio.create_task(startup_check())

    host = bot_config.get_ws_host()
    port = bot_config.get_ws_port()
    ws_path = bot_config.get_ws_path()

    print(f"[{now_str()}] Starting WS server at ws://{host}:{port}{ws_path}")
    async with serve(handler, host, port):
        print(f"[{now_str()}] Waiting for NapCat reverse WS connection...")
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    asyncio.run(main())
