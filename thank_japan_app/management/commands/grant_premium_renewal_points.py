from django.core.management.base import BaseCommand

from thank_japan_app.models import Profile
from thank_japan_app.views import grant_renewal_rewards_if_due


class Command(BaseCommand):
    help = (
        "Daily batch for the premium continuation benefit: grants +50pt "
        "for each elapsed month of a real (non-trial) paid subscription, "
        "anchored per-profile to when they were first seen as no-longer-"
        "trial (see Profile.premium_paid_started_at). Covers both PayPal "
        "and Google Play uniformly - this is the reliable primary path; "
        "the PayPal ACTIVATED webhook also tries the same grant "
        "opportunistically for faster crediting, but the (user, "
        "period_start) uniqueness on PremiumRenewalReward is what "
        "actually prevents a double grant either way, not which caller "
        "gets there first."
    )

    def handle(self, *args, **options):
        candidates = Profile.objects.filter(is_premium=True, is_trial=False)

        checked = newly_anchored = granted_profiles = total_grants = 0

        for profile in candidates:
            checked += 1
            was_unanchored = profile.premium_paid_started_at is None

            count = grant_renewal_rewards_if_due(profile)

            if was_unanchored:
                newly_anchored += 1
            if count:
                granted_profiles += 1
                total_grants += count

        self.stdout.write(
            f"Checked {checked} active non-trial premium profile(s): "
            f"{newly_anchored} newly anchored, {granted_profiles} profile(s) "
            f"granted {total_grants} reward(s) total."
        )
