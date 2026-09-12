import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from thank_japan_app.models import Profile, WeeklyScore


class Command(BaseCommand):
    help = (
        "Ground-truth usage metrics that bot/crawler traffic cannot inflate, "
        "for cross-checking against GA4/Cloudflare-reported anomalies: "
        "real registered-user growth, premium user count, and an honest "
        "accounting of which requested metrics the current DB schema "
        "actually can (and cannot) answer at day-level granularity."
    )

    def handle(self, *args, **options):
        today = timezone.localdate()

        self.stdout.write(self.style.SUCCESS("=== 1. 登録ユーザー数の推移(過去7日間、1日ごと) ==="))
        for i in range(6, -1, -1):
            day = today - datetime.timedelta(days=i)
            count = User.objects.filter(date_joined__date=day).count()
            self.stdout.write(f"  {day.isoformat()}: {count}人")
        self.stdout.write(f"  (参考: 登録ユーザー累計 {User.objects.count()}人)")
        self.stdout.write("")

        self.stdout.write(self.style.WARNING("=== 2. 今日の一問への回答数の推移(過去7日間、1日ごと) ==="))
        self.stdout.write(
            "  現状のDBスキーマでは算出できません。回答の正誤判定はセッション内\n"
            "  (daily_question_answered_date)でのみ完結し、ゲスト・会員を問わず\n"
            "  「いつ・何件・正解/不正解どちらだったか」を記録するテーブルが\n"
            "  存在しないためです。会員が正解した場合のみ+1ptがWeeklyScoreに\n"
            "  加算されますが、これは日別ではなく週単位、かつ通常ゲームの得点と\n"
            "  合算されるため、今日の一問だけの日別回答数には使えません。"
        )
        current_week_start = WeeklyScore.get_current_week_start()
        current_week_total = WeeklyScore.objects.filter(week_start=current_week_start).aggregate(
            total=Sum('score')
        )['total'] or 0
        self.stdout.write(
            f"  (参考・日別ではない: 今週({current_week_start.isoformat()}〜)の"
            f"WeeklyScore合計 {current_week_total}pt - 通常ゲームと今日の一問の得点が"
            f"合算された数字で、今日の一問単体の回答数ではありません)"
        )
        self.stdout.write("")

        self.stdout.write(self.style.WARNING("=== 3. 通常ゲームのプレイ回数の推移(過去7日間、1日ごと) ==="))
        self.stdout.write(
            "  現状のDBスキーマでは算出できません。Profile.games_playedは会員ごとの\n"
            "  累計カウンタで、いつプレイしたかの日付情報を持たず、ゲストのプレイは\n"
            "  そもそもカウントされません。"
        )
        total_games_played = Profile.objects.aggregate(total=Sum('games_played'))['total'] or 0
        self.stdout.write(
            f"  (参考・日別ではない: 全会員のgames_played累計 {total_games_played}回 - "
            f"日付なし、ゲスト分は含まれません)"
        )
        self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("=== 4. is_premium=True のユーザー数(現在の合計) ==="))
        premium_count = Profile.objects.filter(is_premium=True).count()
        self.stdout.write(f"  {premium_count}人")
