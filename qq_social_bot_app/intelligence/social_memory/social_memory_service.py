"""Layers 2/3/4 + CandidateBuffer: SQLite-backed social memory CRUD service.

Provides read/write operations for:
- UserProfile (Layer 2)
- RelationshipEdge (Layer 2)
- GroupProfile (Layer 3)
- EpisodeMemory (Layer 4)
- CandidateMemory (Buffer)

Also provides helpers to build formatted context strings for prompt injection.
"""
import logging
import time
from typing import List, Optional

from qq_social_bot_app.intelligence.social_memory.models import (
    UserProfile, GroupProfile, RelationshipEdge, EpisodeMemory,
    CandidateMemory, PersonaMemory, init_db, get_session, _json_set, _json_get,
)

logger = logging.getLogger(__name__)


class SocialMemoryService:
    """CRUD service for the four social memory layers + candidate buffer."""

    def __init__(self, db_path: str):
        self.engine = init_db(db_path)

    def _session(self):
        return get_session(self.engine)

    # ==============================================================
    # Layer 2: UserProfile
    # ==============================================================

    def get_user_profile(self, user_id: str) -> Optional[dict]:
        session = self._session()
        try:
            row = session.query(UserProfile).filter_by(user_id=user_id).first()
            return row.to_dict() if row else None
        finally:
            session.close()

    def upsert_user_profile(self, user_id: str, updates: dict):
        session = self._session()
        try:
            row = session.query(UserProfile).filter_by(user_id=user_id).first()
            if row is None:
                row = UserProfile(user_id=user_id, created_at=time.time())
                session.add(row)
            json_fields = ('alias_list', 'style_tags', 'interest_tags',
                           'boundary_tags', 'stable_facts')
            merge_list_fields = ('alias_list',)
            for k, v in updates.items():
                if k in merge_list_fields and isinstance(v, list):
                    existing = _json_get(row, k, [])
                    merged = list(existing)
                    for item in v:
                        if item and item not in merged:
                            merged.append(item)
                    setattr(row, k, _json_set(merged))
                elif k in json_fields:
                    setattr(row, k, _json_set(v))
                elif hasattr(row, k) and k not in ('id', 'user_id', 'created_at'):
                    setattr(row, k, v)
            row.updated_at = time.time()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==============================================================
    # Layer 3: GroupProfile
    # ==============================================================

    def get_group_profile(self, group_id: str) -> Optional[dict]:
        session = self._session()
        try:
            row = session.query(GroupProfile).filter_by(group_id=group_id).first()
            return row.to_dict() if row else None
        finally:
            session.close()

    def upsert_group_profile(self, group_id: str, updates: dict):
        session = self._session()
        try:
            row = session.query(GroupProfile).filter_by(group_id=group_id).first()
            if row is None:
                row = GroupProfile(group_id=group_id, created_at=time.time())
                session.add(row)
            json_fields = ('style_tags', 'tone_tags', 'common_topics',
                           'common_memes', 'taboo_tags')
            for k, v in updates.items():
                if k in json_fields:
                    setattr(row, k, _json_set(v))
                elif hasattr(row, k) and k not in ('id', 'group_id', 'created_at'):
                    setattr(row, k, v)
            row.updated_at = time.time()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==============================================================
    # Layer 2: RelationshipEdge
    # ==============================================================

    def get_relationship(self, user_id: str, group_id: str) -> Optional[dict]:
        session = self._session()
        try:
            row = (session.query(RelationshipEdge)
                   .filter_by(user_id=user_id, group_id=group_id).first())
            return row.to_dict() if row else None
        finally:
            session.close()

    def upsert_relationship(self, user_id: str, group_id: str, updates: dict):
        session = self._session()
        try:
            row = (session.query(RelationshipEdge)
                   .filter_by(user_id=user_id, group_id=group_id).first())
            if row is None:
                row = RelationshipEdge(
                    user_id=user_id, group_id=group_id, created_at=time.time())
                session.add(row)
            for k, v in updates.items():
                if hasattr(row, k) and k not in ('id', 'user_id', 'group_id', 'created_at'):
                    setattr(row, k, v)
            row.updated_at = time.time()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def increment_interaction(self, user_id: str, group_id: str):
        """Increment interaction_count and update last_interaction_at."""
        session = self._session()
        try:
            row = (session.query(RelationshipEdge)
                   .filter_by(user_id=user_id, group_id=group_id).first())
            if row is None:
                row = RelationshipEdge(
                    user_id=user_id, group_id=group_id,
                    interaction_count=1,
                    last_interaction_at=time.time(),
                    created_at=time.time(),
                )
                session.add(row)
            else:
                row.interaction_count = (row.interaction_count or 0) + 1
                row.last_interaction_at = time.time()
                row.updated_at = time.time()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==============================================================
    # Alias-based user lookup
    # ==============================================================

    def find_users_by_alias(self, alias: str) -> List[dict]:
        """Find users whose alias_list contains the given alias.

        Uses SQLite LIKE for pre-filtering, then Python exact match.
        """
        session = self._session()
        try:
            # Pre-filter with LIKE to avoid scanning every row in Python
            rows = (session.query(UserProfile)
                    .filter(UserProfile.alias_list.like(f'%{alias}%'))
                    .all())
            results = []
            for row in rows:
                aliases = _json_get(row, 'alias_list', [])
                if alias in aliases:
                    results.append(row.to_dict())
            return results
        finally:
            session.close()

    def resolve_user_id(self, name_or_alias: str) -> Optional[str]:
        """Resolve a name or alias to a user_id.

        Cascade: exact user_id → preferred_name → alias_list.
        Returns the first matching user_id, or None.
        """
        session = self._session()
        try:
            # 1. Exact user_id match
            row = session.query(UserProfile).filter_by(
                user_id=name_or_alias).first()
            if row:
                return row.user_id

            # 2. preferred_name match
            row = session.query(UserProfile).filter_by(
                preferred_name=name_or_alias).first()
            if row:
                return row.user_id

            # 3. alias_list match (LIKE pre-filter + exact check)
            rows = (session.query(UserProfile)
                    .filter(UserProfile.alias_list.like(
                        f'%{name_or_alias}%'))
                    .all())
            for row in rows:
                aliases = _json_get(row, 'alias_list', [])
                if name_or_alias in aliases:
                    return row.user_id

            return None
        finally:
            session.close()

    # ==============================================================
    # Layer 4: EpisodeMemory
    # ==============================================================

    def get_recent_episodes(self, group_id: str, limit: int = 5) -> List[dict]:
        session = self._session()
        try:
            rows = (session.query(EpisodeMemory)
                    .filter_by(group_id=group_id, status='active')
                    .order_by(EpisodeMemory.created_at.desc())
                    .limit(limit).all())
            return [r.to_dict() for r in rows]
        finally:
            session.close()

    def create_episode(self, data: dict) -> int:
        session = self._session()
        try:
            json_fields = ('participants', 'tags')
            kwargs = {}
            for k, v in data.items():
                if k in json_fields:
                    kwargs[k] = _json_set(v)
                else:
                    kwargs[k] = v
            kwargs.setdefault('created_at', time.time())
            kwargs.setdefault('updated_at', time.time())
            row = EpisodeMemory(**kwargs)
            session.add(row)
            session.commit()
            return row.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_episode_status(self, episode_id: int, status: str):
        session = self._session()
        try:
            row = session.query(EpisodeMemory).get(episode_id)
            if row:
                row.status = status
                row.updated_at = time.time()
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==============================================================
    # CandidateMemory (Buffer)
    # ==============================================================

    def add_candidate(self, candidate: dict):
        session = self._session()
        try:
            kwargs = dict(candidate)
            if 'content' in kwargs and not isinstance(kwargs['content'], str):
                kwargs['content'] = _json_set(kwargs['content'])
            kwargs.setdefault('created_at', time.time())
            row = CandidateMemory(**kwargs)
            session.add(row)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_pending_candidates(self, min_confidence: float = 0.5) -> List[dict]:
        session = self._session()
        try:
            rows = (session.query(CandidateMemory)
                    .filter(CandidateMemory.promoted == False,
                            CandidateMemory.confidence >= min_confidence)
                    .order_by(CandidateMemory.created_at.desc())
                    .all())
            return [r.to_dict() for r in rows]
        except Exception:
            logger.exception('Failed to get pending candidates')
            return []
        finally:
            session.close()

    def promote_candidate(self, candidate_id: int):
        session = self._session()
        try:
            row = session.query(CandidateMemory).get(candidate_id)
            if row:
                row.promoted = True
                row.updated_at = time.time() if hasattr(row, 'updated_at') else None
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def cleanup_expired_candidates(self):
        """Remove expired and promoted candidates."""
        session = self._session()
        try:
            now = time.time()
            session.query(CandidateMemory).filter(
                (CandidateMemory.promoted == True) |
                ((CandidateMemory.expire_at != None) &
                 (CandidateMemory.expire_at < now))
            ).delete(synchronize_session=False)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==============================================================
    # Layer 5: PersonaMemory
    # ==============================================================

    def get_persona(self, group_id: str) -> Optional[dict]:
        """Get persona for a specific group (or global if group_id='')."""
        session = self._session()
        try:
            row = session.query(PersonaMemory).filter_by(group_id=group_id).first()
            return row.to_dict() if row else None
        finally:
            session.close()

    def upsert_persona(self, group_id: str, updates: dict):
        """Create or update persona for a group. Merges list fields."""
        session = self._session()
        try:
            row = session.query(PersonaMemory).filter_by(group_id=group_id).first()
            if row is None:
                row = PersonaMemory(group_id=group_id, created_at=time.time())
                session.add(row)

            # List fields that should be merged (append unique items)
            merge_fields = ('learned_boundaries', 'preferred_patterns', 'core_traits')
            for k, v in updates.items():
                if k in merge_fields:
                    existing = _json_get(row, k, [])
                    if isinstance(v, list):
                        merged = list(existing)
                        for item in v:
                            if item not in merged:
                                merged.append(item)
                        setattr(row, k, _json_set(merged))
                    else:
                        setattr(row, k, _json_set(v))
                elif k == 'speaking_style':
                    setattr(row, k, _json_set(v))
                elif hasattr(row, k) and k not in ('id', 'group_id', 'created_at'):
                    setattr(row, k, v)

            row.updated_at = time.time()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def build_persona_context(self, group_id: str) -> str:
        """Build formatted persona context by merging global + group persona."""
        global_persona = self.get_persona('')
        group_persona = self.get_persona(group_id) if group_id else None

        # Merge: group-specific overrides global
        core_traits = []
        speaking_style = ''
        learned_boundaries = []
        preferred_patterns = []

        if global_persona:
            core_traits.extend(global_persona.get('core_traits', []))
            speaking_style = global_persona.get('speaking_style', '')
            learned_boundaries.extend(global_persona.get('learned_boundaries', []))
            preferred_patterns.extend(global_persona.get('preferred_patterns', []))

        if group_persona:
            # Append unique items from group
            for trait in group_persona.get('core_traits', []):
                if trait not in core_traits:
                    core_traits.append(trait)
            if group_persona.get('speaking_style'):
                speaking_style = group_persona['speaking_style']
            for b in group_persona.get('learned_boundaries', []):
                if b not in learned_boundaries:
                    learned_boundaries.append(b)
            for p in group_persona.get('preferred_patterns', []):
                if p not in preferred_patterns:
                    preferred_patterns.append(p)

        if not any([core_traits, speaking_style, learned_boundaries, preferred_patterns]):
            return ''

        parts = ['[Your Persona]']
        if core_traits:
            parts.append(f'  Core traits: {", ".join(core_traits)}')
        if speaking_style:
            parts.append(f'  Speaking style: {speaking_style}')
        if learned_boundaries:
            parts.append(f'  Boundaries: {", ".join(learned_boundaries)}')
        if preferred_patterns:
            parts.append(f'  Preferred patterns: {", ".join(preferred_patterns)}')

        return '\n'.join(parts)

    # ==============================================================
    # Context builders for prompt injection
    # ==============================================================

    def build_user_context(self, user_id: str, group_id: str) -> str:
        """Build a formatted user-context string for the current speaker."""
        parts = []
        profile = self.get_user_profile(user_id)
        if profile:
            name = profile.get('preferred_name') or user_id
            parts.append(f'[User: {name}]')
            if profile.get('alias_list'):
                parts.append(f'  Also known as: {", ".join(profile["alias_list"])}')
            if profile.get('style_tags'):
                parts.append(f'  Style: {", ".join(profile["style_tags"])}')
            if profile.get('interest_tags'):
                parts.append(f'  Interests: {", ".join(profile["interest_tags"])}')
            if profile.get('stable_facts'):
                facts = profile['stable_facts']
                if isinstance(facts, dict) and facts:
                    facts_str = '; '.join(f'{k}: {v}' for k, v in facts.items())
                    parts.append(f'  Facts: {facts_str}')

        rel = self.get_relationship(user_id, group_id)
        if rel:
            parts.append(f'  Familiarity: {rel.get("familiarity_score", 0):.1f}')
            parts.append(f'  Trust: {rel.get("trust_score", 0.5):.1f}')
            parts.append(f'  Interactions: {rel.get("interaction_count", 0)}')
            if rel.get('recent_interactions_summary'):
                parts.append(f'  Recent: {rel["recent_interactions_summary"]}')

        return '\n'.join(parts) if parts else ''

    def build_group_context(self, group_id: str) -> str:
        """Build a formatted group-context string."""
        profile = self.get_group_profile(group_id)
        if not profile:
            return ''
        parts = []
        name = profile.get('group_name') or group_id
        parts.append(f'[Group: {name}]')
        if profile.get('style_tags'):
            parts.append(f'  Style: {", ".join(profile["style_tags"])}')
        if profile.get('tone_tags'):
            parts.append(f'  Tone: {", ".join(profile["tone_tags"])}')
        if profile.get('common_topics'):
            parts.append(f'  Topics: {", ".join(profile["common_topics"])}')
        if profile.get('common_memes'):
            parts.append(f'  Memes/Jokes: {", ".join(profile["common_memes"])}')
        parts.append(f'  Bot tolerance: {profile.get("bot_tolerance_level", 5)}/10')
        if profile.get('taboo_tags'):
            parts.append(f'  Taboos: {", ".join(profile["taboo_tags"])}')
        return '\n'.join(parts)

    def build_episode_context(self, group_id: str, limit: int = 3) -> str:
        """Build a formatted episode-context string from recent episodes."""
        episodes = self.get_recent_episodes(group_id, limit=limit)
        if not episodes:
            return ''
        parts = ['[Recent Episodes]']
        for ep in episodes:
            title = ep.get('title', 'Untitled')
            summary = ep.get('summary', '')
            mood = ep.get('mood', '')
            participants = ep.get('participants', [])
            line = f'  - {title}'
            if mood:
                line += f' ({mood})'
            if participants:
                line += f' [participants: {", ".join(participants)}]'
            parts.append(line)
            if summary:
                parts.append(f'    {summary}')
        return '\n'.join(parts)
