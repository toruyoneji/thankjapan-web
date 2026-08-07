import os
import json
import firebase_admin
from firebase_admin import credentials, messaging
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from thank_japan_app.models import FCMDevice, Profile

class Command(BaseCommand):
    help = '2日間プレイしていないユーザーに多言語で通知を送る'

    def handle(self, *args, **options):
        
        if not firebase_admin._apps:
            cred_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
            if cred_json:
                try:
                    cred_dict = json.loads(cred_json)
                    cred = credentials.Certificate(cred_dict)
                    firebase_admin.initialize_app(cred)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Firebase Init Error: {e}"))
                    return

        
        MESSAGES = {
            'ja': {'title': '今日も一つ、単語を覚えてみよう。', 'body': 'あなたはまた一つ、レベルが上がります。'},
            'en': {'title': 'Let\'s learn one word today.', 'body': 'Your level is about to go up once more.'},
            'vi': {'title': 'Hôm nay hãy học một từ mới nhé.', 'body': 'Cấp độ của bạn sẽ tăng thêm một bậc nữa.'},
            'th': {'title': 'วันนี้มาเรียนรู้คำศัพท์ใหม่กันเถอะ', 'body': 'เลเวลของคุณกำลังจะเพิ่มขึ้นอีกขั้นแล้ว'},
            'ko': {'title': '오늘도 단어 하나를 익혀보세요.', 'body': '당신의 레벨이 한 단계 더 올라갈 것입니다.'},
            'zh-hant': {'title': '今天也來記一個單詞吧。', 'body': '你的等級將會再次提升。'},
            'zh-cn': {'title': '今天也来记一个单词吧。', 'body': '你的等级将会再次提升。'},
            'fr': {'title': 'Apprenons un mot aujourd\'hui.', 'body': 'Votre niveau est sur le point d\'augmenter.'},
            'it': {'title': 'Impariamo una parola oggi.', 'body': 'Il tuo livello sta per salire di nuovo.'},
            'es-es': {'title': 'Aprendamos una palabra hoy.', 'body': 'Tu nivel está a punto de subir de nuevo.'},
            'es-mx': {'title': 'Aprendamos una palabra hoy.', 'body': 'Tu nivel está a punto de subir de nuevo.'},
            'de': {'title': 'Lass uns heute ein Wort lernen.', 'body': 'Dein Level wird bald wieder steigen.'},
            'pt': {'title': 'Vamos aprender uma palavra hoje.', 'body': 'O teu nível está prestes a subir novamente.'},
            'pt-br': {'title': 'Vamos aprender uma palavra hoje.', 'body': 'Seu nível está prestes a subir novamente.'},
            'en-in': {'title': 'Let\'s learn one word today.', 'body': 'Your level is about to go up once more.'},
        }

        
        target_date = timezone.now().date() - timedelta(days=2)
        target_profiles = Profile.objects.filter(last_bonus_date=target_date)

        self.stdout.write(f"Targeting {target_profiles.count()} users...")

        sent_count = 0
        for profile in target_profiles:
            devices = FCMDevice.objects.filter(user=profile.user)
            for device in devices:
                lang = device.lang if device.lang in MESSAGES else 'en'
                content = MESSAGES[lang]

                message = messaging.Message(
                    notification=messaging.Notification(
                        title=content['title'],
                        body=content['body'],
                    ),
                    token=device.token,
                    data={'url': f'/?lang={lang}'}
                )

                try:
                    messaging.send(message)
                    sent_count += 1
                except Exception:
                    
                    device.delete()

        self.stdout.write(self.style.SUCCESS(f'Successfully sent {sent_count} reminders.'))