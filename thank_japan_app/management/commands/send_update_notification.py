from django.core.management.base import BaseCommand
from thank_japan_app.models import FCMDevice
from firebase_admin import messaging
import firebase_admin
from thank_japan_app.firebase_utils import get_firebase_credentials


class Command(BaseCommand):
    help = '登録済みの全デバイスに「アップデートが利用可能です」というプッシュ通知を手動送信する'

    UPDATE_MESSAGES = {
        'ja': {'title': '新しいバージョンが利用可能です', 'body': 'アプリをお使いの方は、ストアから更新をご確認ください。'},
        'en': {'title': 'A New Version Is Available', 'body': "If you're using the app, please check the store for updates."},
        'vi': {'title': 'Đã có phiên bản mới', 'body': 'Nếu bạn đang dùng ứng dụng, vui lòng kiểm tra cập nhật trên cửa hàng ứng dụng.'},
        'th': {'title': 'มีเวอร์ชันใหม่แล้ว', 'body': 'หากคุณใช้แอป กรุณาตรวจสอบการอัปเดตในสโตร์'},
        'ko': {'title': '새 버전이 출시되었습니다', 'body': '앱을 사용 중이시라면 스토어에서 업데이트를 확인해 주세요.'},
        'zh-hant': {'title': '有新版本可用', 'body': '若您使用本應用程式，請至商店確認更新。'},
        'zh-cn': {'title': '有新版本可用', 'body': '若您使用本应用程序，请前往商店确认更新。'},
        'fr': {'title': 'Une nouvelle version est disponible', 'body': "Si vous utilisez l'application, merci de vérifier les mises à jour sur le store."},
        'it': {'title': 'È disponibile una nuova versione', 'body': "Se usi l'app, controlla gli aggiornamenti nello store."},
        'es-es': {'title': 'Hay una nueva versión disponible', 'body': 'Si usas la app, por favor comprueba las actualizaciones en la tienda.'},
        'es-mx': {'title': 'Hay una nueva versión disponible', 'body': 'Si usas la app, por favor revisa las actualizaciones en la tienda.'},
        'de': {'title': 'Eine neue Version ist verfügbar', 'body': 'Wenn du die App nutzt, prüfe bitte im Store auf Updates.'},
        'pt': {'title': 'Está disponível uma nova versão', 'body': 'Se usas a app, verifica as atualizações na loja.'},
        'pt-br': {'title': 'Uma nova versão está disponível', 'body': 'Se você usa o app, verifique as atualizações na loja.'},
        'en-in': {'title': 'A New Version Is Available', 'body': "If you're using the app, please check the store for updates."},
    }

    def handle(self, *args, **options):
        if not firebase_admin._apps:
            cred = get_firebase_credentials()
            if cred:
                firebase_admin.initialize_app(cred)

        devices = FCMDevice.objects.all()
        target_count = devices.count()

        self.stdout.write(f'{target_count} 件の登録デバイスに更新通知を送信しようとしています。')
        confirm = input('本当に送信しますか? (y/N): ').strip().lower()
        if confirm != 'y':
            self.stdout.write(self.style.WARNING('キャンセルしました。通知は送信されていません。'))
            return

        sent_count = 0
        failed_count = 0
        for device in devices:
            lang = device.lang if device.lang in self.UPDATE_MESSAGES else 'en'
            content = self.UPDATE_MESSAGES[lang]

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
                failed_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'完了: 送信成功 {sent_count} 件 / 無効トークンを削除 {failed_count} 件'
        ))
