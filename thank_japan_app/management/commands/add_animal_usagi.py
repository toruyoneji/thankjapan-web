import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand

from thank_japan_app.models import ThankJapanModel

SEED_IMAGE_PATH = os.path.join(os.path.dirname(__file__), 'seed_images', 'usagi.jpg')

SLUG = "rabbit-usagi"  # follows the existing "<english>-<romaji>" pattern (e.g. mouse-nezumi, kaisen-don-kaisendon)

DESCRIPTION_JA = (
    "十二支の4番目の動物として、日本でも古くから親しまれています。"
    "その温和な性格から家内安全や家庭円満の象徴とされてきました。"
    "また、多くの子供を産むことから子孫繁栄の意味も込められています。"
    "ぴょんぴょんと跳ねる姿から「飛躍」「向上」を意味する縁起の良い動物としても知られています。"
)

DESCRIPTION_EN = (
    "As the fourth animal of the Chinese zodiac, rabbits have long been cherished in Japan. "
    "Their gentle nature has made them a symbol of household safety and harmony. "
    "Because they have many offspring, they also represent prosperity for future generations. "
    "Their hopping motion is seen as a symbol of \"leaping forward\" and \"growth,\" "
    "making them an auspicious animal."
)

EXAMPLE_SENTENCE_JA = "今年は、<ruby>卯年<rt>うさぎどし</rt></ruby>です。"


class Command(BaseCommand):
    help = "Insert a single test record for the animal word 'usagi' (Rabbit)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be created without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if ThankJapanModel.objects.filter(slug=SLUG).exists():
            self.stdout.write(self.style.WARNING(
                f"A record with slug='{SLUG}' already exists - aborting to avoid a duplicate."
            ))
            return

        if not os.path.exists(SEED_IMAGE_PATH):
            self.stdout.write(self.style.ERROR(f"Seed image not found at {SEED_IMAGE_PATH}"))
            return

        if dry_run:
            self.stdout.write("Dry run - nothing was written. Would create:")
            self.stdout.write(f"  category=animal slug={SLUG} name=usagi englishname=Rabbit jpname=うさぎ")
            self.stdout.write(f"  image source: {SEED_IMAGE_PATH}")
            return

        obj = ThankJapanModel(
            name="usagi",
            englishname="Rabbit",
            jpname="うさぎ",
            category="animal",
            slug=SLUG,
            description=DESCRIPTION_EN,
            description_ja=DESCRIPTION_JA,
            # placeholder: no dedicated culture/history text supplied yet, field is required.
            history=DESCRIPTION_EN,
            example_sentence_ja=EXAMPLE_SENTENCE_JA,
        )

        with open(SEED_IMAGE_PATH, 'rb') as fh:
            image_bytes = fh.read()

        # CloudinaryField uploads to Cloudinary itself (via cloudinary.uploader) as
        # part of model.save() whenever the assigned value is an UploadedFile instance.
        obj.image = SimpleUploadedFile(
            os.path.basename(SEED_IMAGE_PATH), image_bytes, content_type='image/jpeg'
        )

        obj.save()

        self.stdout.write(self.style.SUCCESS(
            f"Created ThankJapanModel id={obj.id} slug={obj.slug} image_url={obj.image.url}"
        ))
