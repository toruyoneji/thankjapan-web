from django.core.management.base import BaseCommand
from thank_japan_app.models import FCMDevice
from firebase_admin import messaging
import firebase_admin
from thank_japan_app.firebase_utils import get_firebase_credentials


class Command(BaseCommand):
    help = (
        '登録済みの全デバイスに「ゲームが大きく生まれ変わった」お知らせを'
        '一度限りで手動送信する(2026-09 タイルパズル・リニューアルの告知)'
    )

    ANNOUNCEMENT_MESSAGES = {
        'ja': {'title': 'ゲームが大きく生まれ変わりました！🀄', 'body': '新しいタイルパズルで日本語を学ぼう。今すぐチェック！'},
        'en': {'title': 'The game just got a huge upgrade!🀄', 'body': "Learn Japanese with our brand-new tile puzzle. Check it out now!"},
        'ko': {'title': '게임이 완전히 새로워졌어요!🀄', 'body': '새로운 타일 퍼즐로 일본어를 배워보세요. 지금 확인해보세요!'},
        'zh-hant': {'title': '遊戲全面升級啦！🀄', 'body': '用全新的拼貼益智遊戲學日文，立即體驗！'},
        'zh-cn': {'title': '游戏全面升级啦！🀄', 'body': '用全新的拼贴益智游戏学日语，立即体验！'},
        'de': {'title': 'Das Spiel hat ein riesiges Upgrade bekommen!🀄', 'body': 'Lerne Japanisch mit unserem brandneuen Kachel-Puzzle. Jetzt ausprobieren!'},
        'fr': {'title': 'Le jeu vient de connaître une immense évolution !🀄', 'body': 'Apprenez le japonais avec notre tout nouveau puzzle de tuiles. Découvrez-le maintenant !'},
        'es-es': {'title': '¡El juego acaba de recibir una gran mejora!🀄', 'body': 'Aprende japonés con nuestro nuevo puzzle de fichas. ¡Pruébalo ahora!'},
        'es-mx': {'title': '¡El juego tuvo una gran mejora!🀄', 'body': 'Aprende japonés con nuestro nuevo rompecabezas de fichas. ¡Pruébalo ahora!'},
        'it': {'title': 'Il gioco ha ricevuto un enorme aggiornamento!🀄', 'body': 'Impara il giapponese con il nostro nuovissimo puzzle a tessere. Provalo ora!'},
        'pt': {'title': 'O jogo teve uma grande atualização!🀄', 'body': 'Aprende japonês com o nosso novo puzzle de peças. Experimenta já!'},
        'pt-br': {'title': 'O jogo teve uma grande atualização!🀄', 'body': 'Aprenda japonês com nosso novo quebra-cabeça de peças. Experimente agora!'},
        'vi': {'title': 'Trò chơi vừa được nâng cấp lớn!🀄', 'body': 'Học tiếng Nhật với trò chơi xếp ô chữ hoàn toàn mới. Thử ngay!'},
        'th': {'title': 'เกมเพิ่งได้รับการอัปเกรดครั้งใหญ่!🀄', 'body': 'เรียนภาษาญี่ปุ่นด้วยเกมต่อไทล์ใหม่ล่าสุด ลองเลยตอนนี้!'},
        'en-in': {'title': 'The game just got a huge upgrade!🀄', 'body': "Learn Japanese with our brand-new tile puzzle. Check it out now!"},
    }

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show target device count and per-locale breakdown without sending or prompting.',
        )
        parser.add_argument(
            '--yes', action='store_true',
            help=(
                'Skip the interactive y/N confirmation prompt. Piping "y" into '
                'heroku run\'s stdin is unreliable (see project memory on this) - '
                'use this flag instead for a non-interactive confirmed send.'
            ),
        )

    def handle(self, *args, **options):
        devices = FCMDevice.objects.all()
        target_count = devices.count()

        if options['dry_run']:
            self.stdout.write(f'対象デバイス数: {target_count}件(送信は行いません)')
            return

        if not firebase_admin._apps:
            cred = get_firebase_credentials()
            if cred:
                firebase_admin.initialize_app(cred)

        self.stdout.write(f'{target_count} 件の登録デバイスにゲームリニューアル告知を送信しようとしています。')
        if options['yes']:
            self.stdout.write('--yes が指定されたため、確認プロンプトをスキップします。')
        else:
            confirm = input('本当に送信しますか? (y/N): ').strip().lower()
            if confirm != 'y':
                self.stdout.write(self.style.WARNING('キャンセルしました。通知は送信されていません。'))
                return

        sent_count = 0
        failed_count = 0
        for device in devices:
            lang = device.lang if device.lang in self.ANNOUNCEMENT_MESSAGES else 'en'
            content = self.ANNOUNCEMENT_MESSAGES[lang]

            message = messaging.Message(
                notification=messaging.Notification(
                    title=content['title'],
                    body=content['body'],
                ),
                token=device.token,
                data={'url': f'/game/start/?lang={lang}'}
            )

            try:
                messaging.send(message)
                sent_count += 1
            except Exception:
                device.delete()
                failed_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'完了: 送信成功 {sent_count} 件 / 無効トークンを削除 {failed_count} 件'
        ))
