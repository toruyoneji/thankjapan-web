from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from thank_japan_app.models import Profile
from thank_japan_app.views import verify_google_play_purchase


class Command(BaseCommand):
    help = (
        "Fail-safe for premium accounts whose premium_expires_at has passed. "
        "Google Play accounts (which have a stored purchase token) are "
        "re-verified against the Play Developer API and resynced if still "
        "active; everything else past its known expiry is simply expired, "
        "as a backstop for missed PayPal webhooks."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        now_ms = int(now.timestamp() * 1000)

        candidates = Profile.objects.filter(
            is_premium=True,
            premium_expires_at__isnull=False,
            premium_expires_at__lt=now,
        )

        checked = renewed = expired = 0

        for profile in candidates:
            checked += 1

            if profile.google_play_purchase_token:
                purchase = verify_google_play_purchase(profile.google_play_purchase_token)
                expiry_time_ms = int((purchase or {}).get('expiryTimeMillis', 0))
                if purchase and expiry_time_ms > now_ms:
                    profile.premium_expires_at = now + timedelta(milliseconds=expiry_time_ms - now_ms)
                    profile.is_trial = purchase.get('paymentState') == 2
                    profile.save()
                    renewed += 1
                    continue

            profile.is_premium = False
            profile.is_trial = False
            profile.save()
            expired += 1

        self.stdout.write(
            f"Checked {checked} profile(s) past premium_expires_at: "
            f"{renewed} re-verified as still active, {expired} expired."
        )
