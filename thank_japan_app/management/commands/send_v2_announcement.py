from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string

class Command(BaseCommand):
    help = '全ユーザーにV2完成の多言語メールを送信する'

    def handle(self, *args, **options):
        EMAIL_CONTENT = {
            'ja': {
                'subject': '【ThankJapan】V2完成！「攻略ガイド」を公開しました',
                'body': 'お待たせしました。V2が遂に完成！\n新機能「攻略ガイド」で修行を再開し、レベルを上げましょう。\n今すぐチェック：https://www.thankjapan.com/?lang=ja'
            },
            'en': {
                'subject': '【ThankJapan】V2 is Live! New "Strategy Guide" Inside',
                'body': 'The wait is over. V2 is here!\nMaster the cards with our new Strategy Guide and level up your skills.\nCheck it out now: https://www.thankjapan.com/?lang=en'
            },
            'vi': {
                'subject': '【ThankJapan】V2 đã hoàn tất! Xem ngay "Hướng dẫn chiến lược"',
                'body': 'V2 đã chính thức ra mắt!\nHọc tập hiệu quả hơn với Hướng dẫn chiến lược mới và nâng cấp trình độ của bạn.\nXem ngay: https://www.thankjapan.com/?lang=vi'
            },
            'fr': {
                'subject': '【ThankJapan】La V2 est là ! Nouveau "Guide de Stratégie"',
                'body': 'L\'attente est terminée. La V2 est enfin disponible !\nReprenez l\'entraînement avec le Guide de Stratégie et montez de niveau.\nDécouvrir maintenant : https://www.thankjapan.com/?lang=fr'
            },
            'it': {
                'subject': '【ThankJapan】V2 è online! Nuova "Guida Strategica"',
                'body': 'L\'attesa è finita. V2 è qui!\nDomina le carte con la nostra nuova Guida Strategica e sali di livello.\nInizia ora: https://www.thankjapan.com/?lang=it'
            },
            'pt': {
                'subject': '【ThankJapan】V2 disponível! Novo "Guia de Estratégia"',
                'body': 'A espera acabou. A V2 chegou!\nDomina as cartas com o nosso novo Guia de Estratégia e sobe de nível.\nComeça agora: https://www.thankjapan.com/?lang=pt'
            },
            'zh-hant': {
                'subject': '【ThankJapan】V2 正式發布！全新「攻略指南」已上線',
                'body': '讓您久等了，V2 終於完成！\n透過全新攻略指南重啟修行，提升您的等級。\n立即查看：https://www.thankjapan.com/?lang=zh-hant'
            },
            'zh-cn': {
                'subject': '【ThankJapan】V2 正式发布！全新「攻略指南」已上线',
                'body': '让您久等了，V2 终于完成！\n通过全新攻略指南重启修行，提升您的等级。\n立即查看：https://www.thankjapan.com/?lang=zh-cn'
            },
            'ko': {
                'subject': '【ThankJapan】V2 완성! 새로운 \'공략 가이드\' 공개',
                'body': '오래 기다리셨습니다. V2가 드디어 완성되었습니다!\n새로운 공략 가이드와 함께 수행을 재개하고 레벨을 높여보세요.\n지금 확인하기: https://www.thankjapan.com/?lang=ko'
            },
            'es-es': {
                'subject': '【ThankJapan】¡V2 disponible! Nueva "Guía de Estrategia"',
                'body': 'La espera ha terminado. ¡V2 ya está aquí!\nDomina las cartas con nuestra nueva Guía de Estrategia y sube de nivel.\nEmpieza ahora: https://www.thankjapan.com/?lang=es-es'
            },
            'de': {
                'subject': '【ThankJapan】V2 ist da! Neuer "Strategieleitfaden" veröffentlicht',
                'body': 'Das Warten hat ein Ende. V2 ist hier!\nMeistern Sie die Karten mit unserem neuen Strategieleitfaden und steigen Sie im Level auf.\nJetzt starten: https://www.thankjapan.com/?lang=de'
            },
            'th': {
                'subject': '【ThankJapan】V2 เสร็จสมบูรณ์แล้ว! เปิดตัว "คู่มือกลยุทธ์" ใหม่ล่าสุด',
                'body': 'สิ้นสุดการรอคอย V2 มาแล้ว!\nมาฝึกฝนและเพิ่มเลเวลด้วยคู่มือกลยุทธ์ใหม่ของเรากันเถอะ\nเริ่มเลย: https://www.thankjapan.com/?lang=th'
            },
            'pt-br': {
                'subject': '【ThankJapan】V2 disponível! Novo "Guia de Estratégia"',
                'body': 'A espera acabou. A V2 chegou!\nDomine as cartas com o nosso novo Guia de Estratégia e suba de nível.\nComece agora: https://www.thankjapan.com/?lang=pt-br'
            },
            'es-mx': {
                'subject': '【ThankJapan】¡V2 listo! Nueva "Guía de Estrategia" disponible',
                'body': 'Se acabó la espera. ¡V2 ya está aquí!\nDomina las cartas con nuestra nueva Guía de Estrategia y sube de nivel.\nEmpieza ahora: https://www.thankjapan.com/?lang=es-mx'
            },
            'en-in': {
                'subject': '【ThankJapan】V2 is Live! Master the New "Strategy Guide"',
                'body': 'The wait is over. V2 is here!\nCheck out the new Strategy Guide and take your skills to the next level.\nStart now: https://www.thankjapan.com/?lang=en-in'
            }
        }

        users = User.objects.all()
        sent_count = 0

        for user in users:
            if not user.email:
                continue
            
            
            lang = 'en' # デフォルト
            if hasattr(user, 'profile') and user.profile.country:
                
                pass 
            
            content = EMAIL_CONTENT.get(lang, EMAIL_CONTENT['en'])

            try:
                send_mail(
                    content['subject'],
                    content['body'],
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                sent_count += 1
            except Exception as e:
                self.stdout.write(f"Failed to send to {user.email}: {e}")

        self.stdout.write(self.style.SUCCESS(f'Successfully sent {sent_count} announcement emails.'))