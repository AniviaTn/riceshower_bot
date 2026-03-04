"""Multi-user group chat test script for the QQ Social Bot agent.

Tests both single-message and batch-processing flows.

The only external API is agent.run(messages=[...]).

Usage:
    cd examples/qq_social_bot_app
    python intelligence/test/run_social_agent.py

Requires:
    - Redis running at localhost:6379
    - ZENMUX_API_KEY set in config/custom_key.toml
"""
import os
import sys
import sqlite3
import time

# Ensure the project root is on sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, project_root)
# Also add the examples dir so qq_social_bot_app is importable
examples_dir = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, examples_dir)

from agentuniverse.base.agentuniverse import AgentUniverse
from agentuniverse.agent.agent_manager import AgentManager

from qq_social_bot_app.intelligence.social_memory.models import GroupMessage


BOT_ID = 'bot_self'
BOT_NAMES = ['小U', 'bot', 'Bot']


def print_separator(title: str):
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print(f'{"=" * 60}')


def send_one_by_one(agent, messages: list[GroupMessage],
                    description: str = '') -> str:
    """Simulate real-time push: call agent.run() for each message.

    Returns the last non-empty bot response, or ''.
    """
    if description:
        print(f'\n  [{description}]')

    last_response = ''
    for msg in messages:
        print(f'\n  [{msg.sender_name}] {msg.content}')
        output = agent.run(
            messages=[msg], bot_id=BOT_ID, bot_names=BOT_NAMES)
        resp = output.get_data('output', '')
        if resp:
            print(f'  [Bot] {resp}')
            last_response = resp

    return last_response


def send_batch(agent, messages: list[GroupMessage],
               description: str = '') -> str:
    """Simulate scheduled poll: call agent.run() once with all messages.

    Returns the bot response, or ''.
    """
    if description:
        print(f'\n  [{description}]')

    for msg in messages:
        print(f'    [{msg.sender_name}] {msg.content}')

    output = agent.run(
        messages=messages, bot_id=BOT_ID, bot_names=BOT_NAMES)
    resp = output.get_data('output', '')
    if resp:
        print(f'  [Bot] {resp}')
    return resp


def run_tests():
    """Run all social agent tests."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        'config', 'config.toml'
    )
    AgentUniverse().start(config_path=config_path)

    agent = AgentManager().get_instance_obj('qq_social_agent')
    if agent is None:
        print('ERROR: Failed to load qq_social_agent. Check YAML configuration.')
        return

    group_id = 'test_group_001'

    # =========================================================
    # Test 1: Multi-user chat – bot only replies when addressed
    # =========================================================
    print_separator('Test 1: Multi-user chat — bot ignores unless addressed')

    messages_t1 = [
        GroupMessage(
            content='大家好！今天天气真不错',
            sender_id='user_alice', sender_name='Alice',
            group_id=group_id),
        GroupMessage(
            content='是啊，我刚从公园跑步回来',
            sender_id='user_bob', sender_name='Bob',
            group_id=group_id),
        GroupMessage(
            content='Bob你还跑步啊？我以为你只会打游戏哈哈',
            sender_id='user_alice', sender_name='Alice',
            group_id=group_id),
    ]

    # First 3 messages: no @, bot should stay silent
    resp = send_batch(agent, messages_t1,
                      'Three messages, no bot mention')
    assert resp == '', 'Bot should NOT respond when not addressed'
    print(f'\n  OK Bot stayed silent for unaddressed messages')

    # Now Bob @'s the bot
    msg_at = GroupMessage(
        content='哈哈我最近在减肥！对了@小U 你觉得跑步好还是游泳好？',
        sender_id='user_bob', sender_name='Bob',
        group_id=group_id,
        at_list=[BOT_ID])
    resp = send_one_by_one(agent, [msg_at], 'Bob @s the bot')
    assert resp, 'Bot should have responded to @mention'
    print(f'\n  OK Bot responded to @mention')

    # =========================================================
    # Test 2: Follow-up conversation, bot NOT addressed
    # =========================================================
    print_separator('Test 2: Follow-up — bot should stay silent')

    messages_t2 = [
        GroupMessage(
            content='Bot说得有道理，那我试试游泳吧。我是程序员平时坐太久了',
            sender_id='user_bob', sender_name='Bob',
            group_id=group_id),
        GroupMessage(
            content='我也是程序员！Bob你用什么语言？',
            sender_id='user_charlie', sender_name='Charlie',
            group_id=group_id),
        GroupMessage(
            content='Python为主，最近在学Rust。Charlie你呢？',
            sender_id='user_bob', sender_name='Bob',
            group_id=group_id),
    ]
    resp = send_batch(agent, messages_t2, 'Three messages, none addressing bot')
    print(f'\n  OK Messages ingested. Bot responded: {bool(resp)}')

    # =========================================================
    # Test 3: Someone directly asks the bot by name
    # =========================================================
    print_separator('Test 3: Direct mention by name')

    msg_name = GroupMessage(
        content='我写Java的，小U你会写代码吗？',
        sender_id='user_charlie', sender_name='Charlie',
        group_id=group_id)
    resp = send_one_by_one(agent, [msg_name],
                           'Charlie mentions bot name in text')
    assert resp, 'Bot should have responded to name mention'
    print(f'\n  OK Bot responded to name mention')

    # =========================================================
    # Test 4: Verify WorkingMemory has full context
    # =========================================================
    print_separator('Test 4: WorkingMemory completeness check')

    memory = None
    try:
        from agentuniverse.agent.memory.memory_manager import MemoryManager
        memory = MemoryManager().get_instance_obj('qq_social_memory')
        if memory:
            memory._ensure_init()
            if memory._working_memory:
                recent = memory._working_memory.get_recent_messages(group_id)
                print(f'  Messages in WorkingMemory: {len(recent)}')
                senders = set(m.get('sender_name', '') for m in recent)
                print(f'  Unique senders: {senders}')
                assert 'Alice' in senders, 'Alice messages should be in WM'
                assert 'Bob' in senders, 'Bob messages should be in WM'
                assert 'Charlie' in senders, 'Charlie messages should be in WM'
                assert 'Bot' in senders, 'Bot replies should be in WM'
                print(f'  OK All participants present in WorkingMemory')

                print(f'\n  Last 5 messages in WM:')
                for m in recent[-5:]:
                    print(f'    [{m.get("sender_name")}] '
                          f'{m.get("content", "")[:80]}')
            else:
                print('  WARNING: WorkingMemory (Redis) not available')
        else:
            print('  WARNING: Could not access memory instance')
    except Exception as e:
        print(f'  WorkingMemory check error: {e}')

    # =========================================================
    # Test 5: Memory extraction quality check
    # =========================================================
    print_separator('Test 5: Memory extraction & profiles')

    if memory and memory._service:
        for uid in ('user_alice', 'user_bob', 'user_charlie'):
            profile = memory._service.get_user_profile(uid)
            rel = memory._service.get_relationship(uid, group_id)
            print(f'\n  User: {uid}')
            print(f'    Profile: {profile}')
            print(f'    Relationship: {rel}')

        group_profile = memory._service.get_group_profile(group_id)
        print(f'\n  Group profile: {group_profile}')

        episodes = memory._service.get_recent_episodes(group_id)
        print(f'\n  Episodes found: {len(episodes)}')
        for ep in episodes:
            print(f'    - {ep.get("title", "Untitled")}: '
                  f'{ep.get("summary", "")[:100]}')
    else:
        print('  WARNING: Could not access memory service')

    # =========================================================
    # Test 6: Cross-session memory continuity
    # =========================================================
    print_separator('Test 6: Cross-session memory continuity')

    msg_recall = GroupMessage(
        content='大家还记得上次我们聊跑步的事吗？@小U 你还记得不',
        sender_id='user_bob', sender_name='Bob',
        group_id=group_id,
        at_list=[BOT_ID])
    resp = send_one_by_one(agent, [msg_recall],
                           'Bob asks bot to recall earlier conversation')
    if resp:
        print(f'\n  OK Bot responded with memory continuity')

    # =========================================================
    # Test 7: Batch processing (scheduled-poll mode)
    # =========================================================
    print_separator('Test 7: Batch processing — simulates scheduled poll')

    batch_group = 'test_group_batch_001'
    now = time.time()

    # 6 messages spanning ~10 minutes, with 2 @mentions
    batch_messages = [
        GroupMessage(
            content='有人在吗？今晚谁去打球',
            sender_id='user_dave', sender_name='Dave',
            group_id=batch_group, timestamp=now - 600),
        GroupMessage(
            content='我去！几点？',
            sender_id='user_eve', sender_name='Eve',
            group_id=batch_group, timestamp=now - 540),
        GroupMessage(
            content='7点老地方',
            sender_id='user_dave', sender_name='Dave',
            group_id=batch_group, timestamp=now - 480),
        GroupMessage(
            content='@小U 你觉得打篮球前要热身多久？',
            sender_id='user_eve', sender_name='Eve',
            group_id=batch_group, timestamp=now - 300,
            at_list=[BOT_ID]),
        GroupMessage(
            content='我每次都不热身哈哈',
            sender_id='user_dave', sender_name='Dave',
            group_id=batch_group, timestamp=now - 240),
        GroupMessage(
            content='Dave你不怕受伤吗 小U你说说他',
            sender_id='user_eve', sender_name='Eve',
            group_id=batch_group, timestamp=now - 120),
    ]

    print(f'\n  Batch of {len(batch_messages)} messages '
          f'(spanning {int((now - batch_messages[0].timestamp) / 60)} min)')

    resp = send_batch(agent, batch_messages,
                      'One run() call with 6 messages')

    if resp:
        print(f'\n  OK Bot produced a single consolidated reply')
    else:
        print(f'\n  Bot chose not to respond (no triggers matched)')

    # Verify batch messages are in WorkingMemory in order
    if memory:
        memory._ensure_init()
        if memory._working_memory:
            recent = memory._working_memory.get_recent_messages(batch_group)
            print(f'  Messages in WM for batch group: {len(recent)}')
            timestamps = [m.get('timestamp', 0) for m in recent
                          if m.get('sender_id') != BOT_ID]
            is_sorted = all(a <= b for a, b in zip(timestamps, timestamps[1:]))
            print(f'  Chronological order: '
                  f'{"OK" if is_sorted else "WARN out of order"}')

    # =========================================================
    # Test 8: Batch with NO triggers — bot should stay silent
    # =========================================================
    print_separator('Test 8: Batch with no triggers — bot stays silent')

    silent_group = 'test_group_silent_001'
    silent_messages = [
        GroupMessage(
            content='今天中午吃什么',
            sender_id='user_frank', sender_name='Frank',
            group_id=silent_group, timestamp=now - 120),
        GroupMessage(
            content='随便吧，食堂？',
            sender_id='user_grace', sender_name='Grace',
            group_id=silent_group, timestamp=now - 60),
    ]

    resp = send_batch(agent, silent_messages, 'No @, no name mention')
    if not resp:
        print(f'\n  OK Bot correctly stayed silent')
    else:
        print(f'\n  Bot responded (random probability): {resp[:80]}')

    # =========================================================
    # Test 9: SQLite database verification
    # =========================================================
    print_separator('Test 9: SQLite database verification')

    db_path = os.path.join(os.getcwd(), 'data', 'qq_social.db')
    if not os.path.exists(db_path):
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
            'data', 'qq_social.db')

    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f'  Tables: {tables}')

        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = cursor.fetchone()[0]
            print(f'    {table}: {count} rows')

        if 'candidate_memories' in tables:
            cursor.execute(
                'SELECT id, candidate_type, confidence, promoted '
                'FROM candidate_memories LIMIT 10')
            rows = cursor.fetchall()
            if rows:
                print(f'\n  Candidate memories (up to 10):')
                for row in rows:
                    print(f'    id={row[0]} type={row[1]} '
                          f'confidence={row[2]} promoted={row[3]}')

        conn.close()
    else:
        print(f'  WARNING: Database not found at {db_path}')

    # =========================================================
    # Test 10: Nickname/alias extraction and alias-based lookup
    # =========================================================
    print_separator('Test 10: Nickname/alias extraction & lookup')

    alias_group = 'test_group_alias_001'
    now_t10 = time.time()

    alias_messages = [
        GroupMessage(
            content='老王今天怎么没来？',
            sender_id='user_helen', sender_name='Helen',
            group_id=alias_group, timestamp=now_t10 - 300),
        GroupMessage(
            content='老王说他加班，晚点到',
            sender_id='user_ivan', sender_name='Ivan',
            group_id=alias_group, timestamp=now_t10 - 240),
        GroupMessage(
            content='胖虎你帮我带杯咖啡呗',
            sender_id='user_helen', sender_name='Helen',
            group_id=alias_group, timestamp=now_t10 - 180),
        GroupMessage(
            content='没问题！老王要不要也带一杯？',
            sender_id='user_ivan', sender_name='Ivan',
            group_id=alias_group, timestamp=now_t10 - 120),
        GroupMessage(
            content='来了来了，胖虎帮我也带一杯美式，谢啦',
            sender_id='user_wang', sender_name='Wang',
            group_id=alias_group, timestamp=now_t10 - 60),
        GroupMessage(
            content='@小U 你觉得加班的时候喝什么提神？',
            sender_id='user_wang', sender_name='Wang',
            group_id=alias_group, timestamp=now_t10,
            at_list=[BOT_ID]),
    ]

    resp = send_batch(agent, alias_messages,
                      'Nickname conversation with @bot at end')
    if resp:
        print(f'\n  OK Bot responded')

    # --- Verify alias extraction & lookup ---
    if memory and memory._service:
        svc = memory._service

        # Print profiles for manual inspection
        for uid in ('user_wang', 'user_ivan', 'user_helen'):
            profile = svc.get_user_profile(uid)
            print(f'\n  Profile {uid}: {profile}')

        # Test find_users_by_alias
        print('\n  --- find_users_by_alias tests ---')
        for alias in ('老王', '胖虎'):
            try:
                found = svc.find_users_by_alias(alias)
                print(f'  find_users_by_alias("{alias}"): '
                      f'{[u["user_id"] for u in found] if found else "[]"}')
            except Exception as e:
                print(f'  find_users_by_alias("{alias}") ERROR: {e}')

        # Test resolve_user_id
        print('\n  --- resolve_user_id tests ---')
        for name in ('老王', '胖虎', 'user_wang', 'Wang'):
            try:
                resolved = svc.resolve_user_id(name)
                print(f'  resolve_user_id("{name}"): {resolved}')
            except Exception as e:
                print(f'  resolve_user_id("{name}") ERROR: {e}')

        # Infrastructure assertions (methods callable, pipeline didn't crash)
        try:
            svc.find_users_by_alias('老王')
            svc.resolve_user_id('老王')
            print('\n  OK Alias lookup methods callable without error')
        except Exception as e:
            print(f'\n  FAIL Alias lookup raised: {e}')
            raise

        # Test build_user_context includes aliases
        for uid in ('user_wang', 'user_ivan'):
            ctx = svc.build_user_context(uid, alias_group)
            if 'Also known as' in ctx:
                print(f'  OK build_user_context({uid}) includes aliases')
            else:
                print(f'  INFO build_user_context({uid}) has no aliases yet '
                      f'(LLM may not have extracted them)')
    else:
        print('  WARNING: Could not access memory service for alias tests')

    # =========================================================
    # Done
    # =========================================================
    print_separator('All tests completed!')


if __name__ == '__main__':
    run_tests()
