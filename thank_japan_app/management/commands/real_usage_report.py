import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from thank_japan_app.models import GameActivityLog, Profile


class Command(BaseCommand):
    help = (
        "Ground-truth usage metrics that bot/crawler traffic cannot inflate, "
        "for cross-checking against GA4/Cloudflare-reported anomalies: real "
        "registered-user growth, real daily-question/main-game activity "
        "(from GameActivityLog, logged only on a completed tile submission - "
        "never a bare page view), and the current premium user count."
    )

    GAME_ACTIVITY_LOG_STARTED = datetime.date(2026, 9, 12)

    def _print_activity_series(self, activity_type, today):
        for i in range(6, -1, -1):
            day = today - datetime.timedelta(days=i)
            qs = GameActivityLog.objects.filter(activity_type=activity_type, created_at__date=day)
            total = qs.count()
            correct = qs.filter(is_correct=True).count()
            guest = qs.filter(is_guest=True).count()
            note = '' if day >= self.GAME_ACTIVITY_LOG_STARTED else '(記録開始前)'
            self.stdout.write(f"  {day.isoformat()}: {total}件 (正解{correct}件 / ゲスト{guest}件) {note}".rstrip())

    def handle(self, *args, **options):
        today = timezone.localdate()

        self.stdout.write(self.style.SUCCESS("=== 1. 登録ユーザー数の推移(過去7日間、1日ごと) ==="))
        for i in range(6, -1, -1):
            day = today - datetime.timedelta(days=i)
            count = User.objects.filter(date_joined__date=day).count()
            self.stdout.write(f"  {day.isoformat()}: {count}人")
        self.stdout.write(f"  (参考: 登録ユーザー累計 {User.objects.count()}人)")
        self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("=== 2. 今日の一問への回答数の推移(過去7日間、1日ごと) ==="))
        self.stdout.write(
            "  GameActivityLog(2026-09-12導入、タイル送信を実際に完了した場合のみ記録)"
            "に基づく実測値。導入前の日付は記録が存在しないため0件になります。"
        )
        self._print_activity_series(GameActivityLog.ACTIVITY_DAILY_QUESTION, today)
        self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("=== 3. 通常ゲームのプレイ回数の推移(過去7日間、1日ごと) ==="))
        self.stdout.write(
            "  同じくGameActivityLogに基づく実測値(timeattack/first_taste、"
            "1問の送信ごとに1件)。singleモード(単語復習)は含みません。"
        )
        self._print_activity_series(GameActivityLog.ACTIVITY_MAIN_GAME, today)
        self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("=== 4. is_premium=True のユーザー数(現在の合計) ==="))
        premium_count = Profile.objects.filter(is_premium=True).count()
        self.stdout.write(f"  {premium_count}人")
