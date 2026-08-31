from django.core.management.base import BaseCommand
from django.utils import timezone

from thank_japan_app.models import DailyQuestion


class Command(BaseCommand):
    help = "Select (or report) today's shared Daily Question (JST) from the normal-difficulty word pool."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be selected without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = timezone.localdate()  # TIME_ZONE='Asia/Tokyo' -> this is already JST "today"

        if dry_run:
            existing = DailyQuestion.objects.filter(date=today).select_related('word').first()
            if existing:
                self.stdout.write(
                    f"Dry run - DailyQuestion for {today} already exists: "
                    f"{existing.word.englishname} (slug={existing.word.slug})"
                )
                return
            # Dry run must not write, so just preview a candidate without saving.
            self.stdout.write(f"Dry run - no DailyQuestion exists yet for {today} (would select one on real run).")
            return

        dq, created = DailyQuestion.objects.get_or_create_for_date(today)

        if dq is None:
            self.stdout.write(self.style.ERROR(
                "No candidate words found for the normal-difficulty pool - nothing selected."
            ))
            return

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created DailyQuestion for {today}: {dq.word.englishname} (slug={dq.word.slug})"
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"DailyQuestion for {today} already exists (word={dq.word.englishname}, "
                f"slug={dq.word.slug}) - not overwriting."
            ))
