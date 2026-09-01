from django.core.management.base import BaseCommand
from django.utils import timezone
from thank_japan_app.models import FCMDevice, DailyQuestion
from firebase_admin import messaging
import firebase_admin
from thank_japan_app.firebase_utils import get_firebase_credentials

# Same 15-language set used throughout the app (en-in shares 'en' text).
DAILY_QUESTION_NOTIFICATION_MESSAGES = {
    'ja': {'title': '今日の一問が届きました🐾', 'body': '今日はどんな単語かな？1日1回だけの挑戦です！'},
    'en': {'title': "Today's Daily Question is here🐾", 'body': 'What word awaits today? Just one try a day!'},
    'zh-hant': {'title': '今日一題已送達🐾', 'body': '今天會是什麼單字呢？每天只有一次機會！'},
    'zh-cn': {'title': '今日一题已送达🐾', 'body': '今天会是什么单词呢？每天只有一次机会！'},
    'ko': {'title': '오늘의 한 문제가 도착했어요🐾', 'body': '오늘은 어떤 단어일까요? 하루에 한 번뿐인 도전!'},
    'de': {'title': 'Deine Frage des Tages ist da🐾', 'body': 'Welches Wort wartet heute? Nur einmal am Tag!'},
    'fr': {'title': 'La question du jour est arrivée🐾', 'body': "Quel mot vous attend aujourd'hui ? Un seul essai par jour !"},
    'es-es': {'title': '¡Tu pregunta del día ya está aquí!🐾', 'body': '¿Qué palabra te espera hoy? ¡Solo un intento al día!'},
    'es-mx': {'title': '¡Tu pregunta del día ya está aquí!🐾', 'body': '¿Qué palabra te espera hoy? ¡Solo un intento al día!'},
    'it': {'title': 'La tua domanda del giorno è arrivata🐾', 'body': 'Quale parola ti aspetta oggi? Un solo tentativo al giorno!'},
    'pt': {'title': 'A sua pergunta do dia chegou🐾', 'body': 'Que palavra te espera hoje? Só uma tentativa por dia!'},
    'pt-br': {'title': 'A sua pergunta do dia chegou🐾', 'body': 'Que palavra te espera hoje? Só uma tentativa por dia!'},
    'vi': {'title': 'Câu hỏi hôm nay đã đến🐾', 'body': 'Hôm nay là từ gì nhỉ? Chỉ một lần thử mỗi ngày!'},
    'th': {'title': 'คำถามประจำวันของคุณมาแล้ว🐾', 'body': 'วันนี้จะเป็นคำว่าอะไรนะ? ท้าทายได้วันละครั้งเท่านั้น!'},
    'en-in': {'title': "Today's Daily Question is here🐾", 'body': 'What word awaits today? Just one try a day!'},
}


class Command(BaseCommand):
    help = "Send the Daily Question FCM notification to every opted-in user (Profile.daily_question_notify=True)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be sent without actually sending or touching the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = timezone.localdate()  # TIME_ZONE='Asia/Tokyo' -> JST "today"
        devices = FCMDevice.objects.filter(user__profile__daily_question_notify=True)

        if dry_run:
            existing = DailyQuestion.objects.filter(date=today).select_related('word').first()
            word_desc = existing.word.englishname if existing else '(not selected yet)'
            self.stdout.write(
                f"Dry run - would send to {devices.count()} device(s) for {today} (word={word_desc})."
            )
            return

        # Safety net: this batch is meant to run a couple of minutes after
        # select_daily_question (see Heroku Scheduler setup), but never send a
        # notification pointing at a day with no question selected yet - reuse
        # the exact same selection logic so this can never disagree with it.
        daily_question, _ = DailyQuestion.objects.get_or_create_for_date(today)
        if daily_question is None:
            self.stdout.write(self.style.ERROR(
                f"No candidate words available for {today} - aborting send (run select_daily_question first)."
            ))
            return

        if not firebase_admin._apps:
            cred = get_firebase_credentials()
            if cred:
                firebase_admin.initialize_app(cred)

        success_count = 0
        fail_count = 0
        for device in devices:
            lang = device.lang if device.lang in DAILY_QUESTION_NOTIFICATION_MESSAGES else 'en'
            content = DAILY_QUESTION_NOTIFICATION_MESSAGES[lang]

            message = messaging.Message(
                notification=messaging.Notification(title=content['title'], body=content['body']),
                token=device.token,
                data={'url': f'/daily/?lang={lang}'},
            )

            try:
                messaging.send(message)
                success_count += 1
            except Exception:
                device.delete()
                fail_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Daily Question notification for {today}: sent to {success_count}, "
            f"removed {fail_count} invalid token(s)."
        ))
