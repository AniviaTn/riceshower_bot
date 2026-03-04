"""Data models for the QQ Social Bot memory system.

Includes:
- GroupMessage dataclass: standardised input format for group chat messages.
- SQLAlchemy ORM tables: user_profiles, group_profiles, relationship_edges,
  episode_memories, candidate_memories.
- Helper functions: init_db(), get_session().
"""
import json
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import (
    Column, String, Float, Integer, Text, Boolean,
    UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


# ============================================================
# GroupMessage – Agent input wrapper
# ============================================================

@dataclass
class GroupMessage:
    """Group chat message object - standard input format for the agent."""
    content: str
    sender_id: str
    sender_name: str
    group_id: str
    timestamp: float = field(default_factory=time.time)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    reply_to: str = ''
    at_list: list = field(default_factory=list)  # IDs of users being @'d

    def to_dict(self) -> dict:
        return {
            'content': self.content,
            'sender_id': self.sender_id,
            'sender_name': self.sender_name,
            'group_id': self.group_id,
            'timestamp': self.timestamp,
            'message_id': self.message_id,
            'reply_to': self.reply_to,
            'at_list': self.at_list,
        }


# ============================================================
# JSON column helper
# ============================================================

class JSONEncodedColumn(Text):
    """Marker type – actual ser/de handled via property accessors on models."""
    pass


def _json_get(row, col_name, default=None):
    """Deserialise a JSON text column value."""
    raw = getattr(row, col_name, None)
    if raw is None:
        return default if default is not None else {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def _json_set(value):
    """Serialise a Python object to a JSON string for storage."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# ============================================================
# ORM Tables
# ============================================================

class UserProfile(Base):
    """Layer 2: per-user profile across all groups."""
    __tablename__ = 'user_profiles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(128), unique=True, nullable=False, index=True)
    platform = Column(String(32), default='qq')
    preferred_name = Column(String(128), default='')
    alias_list = Column(Text, default='[]')           # JSON list
    style_tags = Column(Text, default='[]')            # JSON list
    interest_tags = Column(Text, default='[]')         # JSON list
    boundary_tags = Column(Text, default='[]')         # JSON list
    stable_facts = Column(Text, default='{}')          # JSON dict
    confidence = Column(Float, default=0.0)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'platform': self.platform,
            'preferred_name': self.preferred_name,
            'alias_list': _json_get(self, 'alias_list', []),
            'style_tags': _json_get(self, 'style_tags', []),
            'interest_tags': _json_get(self, 'interest_tags', []),
            'boundary_tags': _json_get(self, 'boundary_tags', []),
            'stable_facts': _json_get(self, 'stable_facts', {}),
            'confidence': self.confidence,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class GroupProfile(Base):
    """Layer 3: per-group profile."""
    __tablename__ = 'group_profiles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(String(128), unique=True, nullable=False, index=True)
    group_name = Column(String(256), default='')
    style_tags = Column(Text, default='[]')
    tone_tags = Column(Text, default='[]')
    common_topics = Column(Text, default='[]')
    common_memes = Column(Text, default='[]')
    bot_tolerance_level = Column(Integer, default=5)   # 1-10
    taboo_tags = Column(Text, default='[]')
    last_summary = Column(Text, default='')
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'group_id': self.group_id,
            'group_name': self.group_name,
            'style_tags': _json_get(self, 'style_tags', []),
            'tone_tags': _json_get(self, 'tone_tags', []),
            'common_topics': _json_get(self, 'common_topics', []),
            'common_memes': _json_get(self, 'common_memes', []),
            'bot_tolerance_level': self.bot_tolerance_level,
            'taboo_tags': _json_get(self, 'taboo_tags', []),
            'last_summary': self.last_summary,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class RelationshipEdge(Base):
    """Layer 2: relationship between a user and the bot within a group."""
    __tablename__ = 'relationship_edges'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(128), nullable=False, index=True)
    group_id = Column(String(128), nullable=False, index=True)
    familiarity_score = Column(Float, default=0.0)     # 0-1
    trust_score = Column(Float, default=0.5)           # 0-1
    banter_score = Column(Float, default=0.0)          # 0-1
    seriousness_preference = Column(Float, default=0.5)  # 0-1
    interaction_count = Column(Integer, default=0)
    last_interaction_at = Column(Float, default=time.time)
    recent_interactions_summary = Column(Text, default='')
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)

    __table_args__ = (
        UniqueConstraint('user_id', 'group_id', name='uq_user_group'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'group_id': self.group_id,
            'familiarity_score': self.familiarity_score,
            'trust_score': self.trust_score,
            'banter_score': self.banter_score,
            'seriousness_preference': self.seriousness_preference,
            'interaction_count': self.interaction_count,
            'last_interaction_at': self.last_interaction_at,
            'recent_interactions_summary': self.recent_interactions_summary,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class EpisodeMemory(Base):
    """Layer 4: memorable shared episodes / events."""
    __tablename__ = 'episode_memories'

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(16), default='group')        # 'group' | 'user'
    group_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(128), default='')
    title = Column(String(512), default='')
    summary = Column(Text, default='')
    participants = Column(Text, default='[]')           # JSON list
    tags = Column(Text, default='[]')                   # JSON list
    mood = Column(String(64), default='')
    salience_score = Column(Float, default=0.5)         # 0-1
    start_time = Column(Float, default=time.time)
    end_time = Column(Float, nullable=True)
    status = Column(String(32), default='active')       # active / archived
    sensitivity_level = Column(Integer, default=0)      # 0-3
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'scope': self.scope,
            'group_id': self.group_id,
            'user_id': self.user_id,
            'title': self.title,
            'summary': self.summary,
            'participants': _json_get(self, 'participants', []),
            'tags': _json_get(self, 'tags', []),
            'mood': self.mood,
            'salience_score': self.salience_score,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'status': self.status,
            'sensitivity_level': self.sensitivity_level,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class CandidateMemory(Base):
    """Buffer: candidate memories awaiting promotion to long-term storage."""
    __tablename__ = 'candidate_memories'

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_type = Column(String(32), nullable=False)  # user_profile / group_profile / episode / relationship
    scope = Column(String(16), default='group')
    group_id = Column(String(128), default='', index=True)
    user_id = Column(String(128), default='')
    content = Column(Text, default='{}')               # JSON – the extracted data
    sensitivity_level = Column(Integer, default=0)     # 0-3
    confidence = Column(Float, default=0.5)
    evidence_count = Column(Integer, default=1)
    promoted = Column(Boolean, default=False)
    created_at = Column(Float, default=time.time)
    expire_at = Column(Float, nullable=True)           # optional TTL

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'candidate_type': self.candidate_type,
            'scope': self.scope,
            'group_id': self.group_id,
            'user_id': self.user_id,
            'content': _json_get(self, 'content', {}),
            'sensitivity_level': self.sensitivity_level,
            'confidence': self.confidence,
            'evidence_count': self.evidence_count,
            'promoted': self.promoted,
            'created_at': self.created_at,
            'expire_at': self.expire_at,
        }


class PersonaMemory(Base):
    """Layer 5: per-group agent persona/personality that learns from interactions."""
    __tablename__ = 'persona_memories'

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(String(128), default='', index=True)  # '' = global
    core_traits = Column(Text, default='[]')           # JSON list
    speaking_style = Column(Text, default='""')        # JSON str
    learned_boundaries = Column(Text, default='[]')    # JSON list
    preferred_patterns = Column(Text, default='[]')    # JSON list
    confidence = Column(Float, default=0.0)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time, onupdate=time.time)

    __table_args__ = (
        UniqueConstraint('group_id', name='uq_persona_group'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'group_id': self.group_id,
            'core_traits': _json_get(self, 'core_traits', []),
            'speaking_style': _json_get(self, 'speaking_style', ''),
            'learned_boundaries': _json_get(self, 'learned_boundaries', []),
            'preferred_patterns': _json_get(self, 'preferred_patterns', []),
            'confidence': self.confidence,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


# ============================================================
# Database initialisation helpers
# ============================================================

def init_db(db_path: str):
    """Create engine + tables. Returns the SQLAlchemy Engine."""
    engine = create_engine(
        f'sqlite:///{db_path}',
        echo=False,
        connect_args={'check_same_thread': False},
    )
    # Enable WAL mode for better concurrent read performance
    @event.listens_for(engine, 'connect')
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.close()

    Base.metadata.create_all(engine)

    # Migration guard: add sensitivity_level to episode_memories if missing
    with engine.connect() as conn:
        try:
            result = conn.execute(
                __import__('sqlalchemy').text(
                    "PRAGMA table_info(episode_memories)"))
            columns = {row[1] for row in result}
            if 'sensitivity_level' not in columns:
                conn.execute(
                    __import__('sqlalchemy').text(
                        "ALTER TABLE episode_memories "
                        "ADD COLUMN sensitivity_level INTEGER DEFAULT 0"))
                conn.commit()
        except Exception:
            pass  # Table may not exist yet on first run

    return engine


def get_session(engine):
    """Create a new SQLAlchemy session."""
    Session = sessionmaker(bind=engine)
    return Session()
