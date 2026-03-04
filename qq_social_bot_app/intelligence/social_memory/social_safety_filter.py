"""Social safety filter to prevent surveillance-like behaviour.

Filters context injected into prompts based on sensitivity levels:
- Level 0 (public facts): Always allowed
- Level 1 (casual observations): Allowed
- Level 2 (personal details): Used as behavioural reference only, not mentioned directly
- Level 3 (sensitive/private): Completely filtered out

Also applies time-based decay: old episodes are down-weighted.
"""
import time


class SocialSafetyFilter:
    """Social safety filter - prevents the bot from appearing creepy or surveillance-like."""

    # How old an episode must be (in seconds) before it starts decaying
    EPISODE_DECAY_THRESHOLD = 7 * 24 * 3600  # 7 days

    @staticmethod
    def filter_context(user_context: str, group_context: str,
                       episode_context: str,
                       sensitivity_threshold: int = 1,
                       episodes_raw: list = None) -> dict:
        """Filter assembled context strings based on sensitivity.

        Args:
            user_context: Formatted user profile + relationship context.
            group_context: Formatted group profile context.
            episode_context: Formatted episode memories.
            sensitivity_threshold: Max sensitivity level to include (0-3).
            episodes_raw: Optional list of raw episode dicts. When provided,
                episodes are filtered by sensitivity_level and time decay,
                then re-formatted inline (overriding episode_context).

        Returns:
            Dict with filtered 'user_context', 'group_context', 'episode_context',
            and 'safety_note'.
        """
        info_was_filtered = False

        # --- Filter episodes from raw data when available ---
        if episodes_raw is not None:
            now = time.time()
            kept = []
            for ep in episodes_raw:
                sl = ep.get('sensitivity_level', 0)
                # L3 always filtered
                if sl >= 3:
                    info_was_filtered = True
                    continue
                # Filter above threshold
                if sl > sensitivity_threshold:
                    info_was_filtered = True
                    continue
                # Time-based decay check
                if not SocialSafetyFilter.should_use_memory(ep, now):
                    continue
                kept.append(ep)

            # Re-format kept episodes
            if kept:
                parts = ['[Recent Episodes]']
                for ep in kept:
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
                filtered_episodes = '\n'.join(parts)
            else:
                filtered_episodes = ''
        else:
            # Fallback: line-level filtering on pre-formatted text
            filtered_episodes = SocialSafetyFilter._filter_episode_lines(
                episode_context, sensitivity_threshold)
            if filtered_episodes != episode_context:
                info_was_filtered = True

        # --- Filter user_context lines by sensitivity markers ---
        filtered_user_context = SocialSafetyFilter._filter_user_context_lines(
            user_context, sensitivity_threshold)
        if filtered_user_context != user_context:
            info_was_filtered = True

        # --- Build safety note ---
        safety_note = (
            'IMPORTANT: Use the social context below to adjust your tone and style. '
            'Do NOT directly reveal that you remember specific personal details '
            'unless the user brings them up first. Never list facts about a user '
            'unprompted. Be natural, not creepy.'
        )
        if info_was_filtered:
            safety_note += (
                ' NOTE: Some information has been filtered for privacy. '
                'Do not attempt to recall or reference information you do not see here.'
            )

        return {
            'user_context': filtered_user_context,
            'group_context': group_context,
            'episode_context': filtered_episodes,
            'safety_note': safety_note,
        }

    @staticmethod
    def should_use_memory(episode: dict, now: float = None) -> bool:
        """Check if an episode should be included in context.

        Returns False for:
        - Archived episodes
        - Episodes older than 30 days with low salience
        """
        if now is None:
            now = time.time()

        if episode.get('status') == 'archived':
            return False

        age = now - episode.get('created_at', now)
        salience = episode.get('salience_score', 0.5)

        # Old + low salience -> skip
        if age > 30 * 24 * 3600 and salience < 0.3:
            return False

        return True

    @staticmethod
    def _filter_episode_lines(episode_context: str,
                              sensitivity_threshold: int) -> str:
        """Filter pre-formatted episode lines by [L2]/[L3] markers."""
        if not episode_context:
            return episode_context

        lines = episode_context.split('\n')
        kept = []
        for line in lines:
            # Always remove L3
            if '[L3]' in line:
                continue
            # Remove L2 when threshold < 2
            if '[L2]' in line and sensitivity_threshold < 2:
                continue
            kept.append(line)

        return '\n'.join(kept)

    @staticmethod
    def _filter_user_context_lines(user_context: str,
                                   sensitivity_threshold: int) -> str:
        """Filter user context lines containing sensitivity markers."""
        if not user_context:
            return user_context

        lines = user_context.split('\n')
        kept = []
        for line in lines:
            # Always remove L3 markers
            if '[L3]' in line:
                continue
            # Remove L2 when threshold < 2
            if '[L2]' in line and sensitivity_threshold < 2:
                continue
            kept.append(line)

        return '\n'.join(kept)
