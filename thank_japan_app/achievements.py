"""Badge catalog and unlock logic for the account settings achievement shelf.

The catalog is plain Python (not DB-backed) so adding a new badge is just a
new entry here, no migration. Thresholds are chosen to line up with numbers
already meaningful elsewhere in the app: the rank names/thresholds in
Profile.rank_info, the streak-count tiers used for the fire-emoji escalation
on the account settings page, and the combo tier already used in
build_combo_share_message (>= 7 = 'high').
"""

from django.db import IntegrityError, transaction

from .models import Achievement

ACHIEVEMENT_CATALOG = [
    {'code': 'score_50', 'metric': 'total_score', 'threshold': 50, 'emoji': '🌱'},
    {'code': 'score_300', 'metric': 'total_score', 'threshold': 300, 'emoji': '⚔️'},
    {'code': 'score_1200', 'metric': 'total_score', 'threshold': 1200, 'emoji': '👑'},
    {'code': 'score_2000', 'metric': 'total_score', 'threshold': 2000, 'emoji': '🗾'},
    {'code': 'streak_7', 'metric': 'streak_count', 'threshold': 7, 'emoji': '🔥'},
    {'code': 'streak_30', 'metric': 'streak_count', 'threshold': 30, 'emoji': '🔥👑'},
    {'code': 'streak_100', 'metric': 'streak_count', 'threshold': 100, 'emoji': '💯'},
    {'code': 'games_1', 'metric': 'games_played', 'threshold': 1, 'emoji': '🎮'},
    {'code': 'combo_7', 'metric': 'best_combo', 'threshold': 7, 'emoji': '🔥'},
    {'code': 'combo_12', 'metric': 'best_combo', 'threshold': 12, 'emoji': '⚡'},
]

# Codes shown as a toast on the top page (earned via the daily login/streak
# flow) vs. on the game result page (earned via playing a game). Purely a
# display-routing detail — the unlock check itself doesn't care which flow
# triggered it.
STREAK_ACHIEVEMENT_CODES = {'streak_7', 'streak_30', 'streak_100'}


def _current_metrics(profile):
    return {
        'total_score': profile.total_score,
        'streak_count': profile.streak_count,
        'best_combo': profile.best_combo,
        'games_played': profile.games_played,
    }


def check_and_unlock_achievements(profile):
    """Create Achievement rows for any newly-met thresholds. Returns the
    catalog entries that were newly unlocked by this call (empty if none)."""
    metrics = _current_metrics(profile)
    already_unlocked = set(
        Achievement.objects.filter(user_id=profile.user_id).values_list('code', flat=True)
    )
    newly_unlocked = []
    for entry in ACHIEVEMENT_CATALOG:
        if entry['code'] in already_unlocked:
            continue
        if metrics[entry['metric']] >= entry['threshold']:
            try:
                # Own savepoint per badge: if a concurrent request unlocks
                # the same badge first, the unique_together constraint
                # trips here rather than as an unhandled 500 later, and
                # only this one insert is rolled back.
                with transaction.atomic():
                    Achievement.objects.create(user_id=profile.user_id, code=entry['code'])
            except IntegrityError:
                continue
            newly_unlocked.append(entry)
    return newly_unlocked


def get_achievement_progress(profile):
    """Full catalog annotated with unlock state and current/goal for display,
    in catalog order, for the account settings badge shelf."""
    metrics = _current_metrics(profile)
    unlocked_codes = set(
        Achievement.objects.filter(user_id=profile.user_id).values_list('code', flat=True)
    )
    progress = []
    for entry in ACHIEVEMENT_CATALOG:
        current = metrics[entry['metric']]
        progress.append({
            'code': entry['code'],
            'emoji': entry['emoji'],
            'unlocked': entry['code'] in unlocked_codes,
            'current': min(current, entry['threshold']),
            'goal': entry['threshold'],
        })
    return progress
