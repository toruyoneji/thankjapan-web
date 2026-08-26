from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from thank_japan_app.models import FCMDevice, Profile
from firebase_admin import messaging
import firebase_admin
from thank_japan_app.firebase_utils import get_firebase_credentials

class Command(BaseCommand):
    help = '休眠期間（2日、7日、30日）に合わせて通知を送り分ける'

    def handle(self, *args, **options):
        # Firebase初期化
        if not firebase_admin._apps:
            cred = get_firebase_credentials()
            if cred:
                firebase_admin.initialize_app(cred)

        # メッセージ定義
        STEPS = {
            2: {
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
            },
            
            7: {
                'ja': {'title': '休み時間は終わりました。', 'body': 'ゲームやStudyの攻略本を見て、レベルを上げましょう！'},
                'en': {'title': 'Break time is over.', 'body': 'Check the game and study guides to level up your skills!'},
                'vi': {'title': 'Thời gian nghỉ ngơi đã kết thúc.', 'body': 'Hãy xem hướng dẫn chơi và học để nâng cấp trình độ của bạn!'},
                'th': {'title': 'หมดเวลาพักแล้ว!', 'body': 'ไปดูคู่มือการเล่นและการเรียนเพื่อเพิ่มเลเวลกันเถอะ!'},
                'ko': {'title': '휴식 시간은 끝났습니다.', 'body': '게임과 학습 가이드를 확인하고 레벨을 높여보세요!'},
                'zh-hant': {'title': '休息時間結束了。', 'body': '查看遊戲和學習攻略本，讓等級再次提升吧！'},
                'zh-cn': {'title': '休息时间结束了。', 'body': '查看游戏和学习攻略本，让等级再次提升吧！'},
                'fr': {'title': 'La récréation est terminée.', 'body': 'Consultez les guides de jeu et d\'étude pour monter de niveau !'},
                'it': {'title': 'La ricreazione è finita.', 'body': 'Consulta le guide di gioco e di studio per salire di livello!'},
                'es-es': {'title': 'El tiempo de descanso ha terminado.', 'body': '¡Consulta las guías de juego y estudio para subir de nivel!'},
                'es-mx': {'title': 'El tiempo de descanso terminó.', 'body': '¡Checa las guías de juego y estudio para subir de nivel!'},
                'de': {'title': 'Die Pause ist vorbei.', 'body': 'Schau dir die Spiel- und Lernanleitungen an, um dein Level zu steigern!'},
                'pt': {'title': 'O tempo de descanso acabou.', 'body': 'Consulta os guias de jogo e estudo para subires de nível!'},
                'pt-br': {'title': 'O tempo de descanso acabou.', 'body': 'Confira os guias de jogo e estudo para subir de nível!'},
                'en-in': {'title': 'Break time is over!', 'body': 'Check out the study guides and level up your game!'},
            },
            
            30: {
                'ja': {'title': 'お久しぶりです。日本が待っています。', 'body': '今すぐ、修行を再開しましょう！'},
                'en': {'title': 'Long time no see. Japan is waiting for you.', 'body': 'Let\'s resume your journey right now!'},
                'vi': {'title': 'Đã lâu không gặp. Nhật Bản đang chờ bạn.', 'body': 'Hãy bắt đầu lại hành trình ngay bây giờ!'},
                'th': {'title': 'ไม่ได้เจอกันนานเลยนะ ญี่ปุ่นกำลังรอคุณอยู่', 'body': 'กลับมาฝึกฝนกันต่อเถอะ!'},
                'ko': {'title': '오랜만입니다. 일본이 당신을 기다리고 있어요.', 'body': '지금 바로 수행을 재개해 보세요!'},
                'zh-hant': {'title': '好久不見。日本正等著你。', 'body': '現在就重新開始修行吧！'},
                'zh-cn': {'title': '好久不见。日本正等着你。', 'body': '现在就重新开始修行吧！'},
                'fr': {'title': 'Cela fait longtemps. Le Japon vous attend.', 'body': 'Reprenez votre voyage dès maintenant !'},
                'it': {'title': 'È passato molto tempo. Il Giappone ti aspetta.', 'body': 'Riprendi il tuo viaggio adesso!'},
                'es-es': {'title': 'Mucho tiempo sin vernos. Japón te espera.', 'body': '¡Retoma tu camino ahora mismo!'},
                'es-mx': {'title': 'Mucho tiempo. Japón te está esperando.', 'body': '¡Reencuéntrate con tu camino ahora!'},
                'de': {'title': 'Lange nicht gesehen. Japan wartet auf dich.', 'body': 'Setze deine Reise jetzt fort!'},
                'pt': {'title': 'Há quanto tempo. O Japão espera por ti.', 'body': 'Recomeça a tua jornada agora mesmo!'},
                'pt-br': {'title': 'Há quanto tempo. O Japão espera por você.', 'body': 'Recomece sua jornada agora mesmo!'},
                'en-in': {'title': 'Long time no see. Japan is waiting for you.', 'body': 'Resume your legendary journey today!'},
            },
        }

        # Steps run in ascending order: a profile idle long enough to qualify for
        # multiple tiers in one run (e.g. after a missed cron day) will pick up
        # each one it hasn't received yet, in order.
        for days_count in [2, 7, 30]:
            target_date = timezone.localdate() - timedelta(days=days_count)
            # >= N days idle (not "exactly N days ago"), and this tier not sent yet
            # for the current idle streak - last_reminder_step_sent is reset to 0
            # whenever the user is active again (see apply_login_bonus).
            target_profiles = Profile.objects.filter(
                last_bonus_date__lte=target_date,
                last_reminder_step_sent__lt=days_count,
            )

            self.stdout.write(f"Day {days_count}: Targeting {target_profiles.count()} users...")

            for profile in target_profiles:
                devices = FCMDevice.objects.filter(user=profile.user)
                for device in devices:
                    lang = device.lang if device.lang in STEPS[days_count] else 'en'
                    content = STEPS[days_count].get(lang, STEPS[days_count]['en'])

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
                    except Exception:
                        device.delete()

                profile.last_reminder_step_sent = days_count
                profile.save(update_fields=['last_reminder_step_sent'])

        self.stdout.write(self.style.SUCCESS('Successfully processed all steps.'))