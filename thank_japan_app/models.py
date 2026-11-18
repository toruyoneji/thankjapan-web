import random

from django.db import models
from django.db.models import F
from cloudinary.models import CloudinaryField
from thank_japan_app.fields import OptimizedCloudinaryField
from django.contrib.auth.hashers import make_password, check_password
from django.utils.text import slugify
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone


class ThankJapanModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    englishname = models.CharField(max_length=100)
    
    englishname_ja = models.CharField(max_length=150, blank=True, null=True)
    englishname_en_in = models.CharField(max_length=150, blank=True, null=True)
    englishname_zh_cn = models.CharField(max_length=150, blank=True, null=True)
    englishname_zh_hant = models.CharField(max_length=150, blank=True, null=True)
    englishname_ko = models.CharField(max_length=150, blank=True, null=True)
    englishname_fr = models.CharField(max_length=150, blank=True, null=True)
    englishname_de = models.CharField(max_length=150, blank=True, null=True)
    englishname_it = models.CharField(max_length=150, blank=True, null=True)
    englishname_es_es = models.CharField(max_length=150, blank=True, null=True)
    englishname_es_mx = models.CharField(max_length=150, blank=True, null=True)
    englishname_pt = models.CharField(max_length=150, blank=True, null=True)
    englishname_pt_br = models.CharField(max_length=150, blank=True, null=True)
    englishname_th = models.CharField(max_length=150, blank=True, null=True)
    englishname_vi = models.CharField(max_length=150, blank=True, null=True)
    
    jpname = models.CharField(max_length=100)
    kanji_name = models.CharField(max_length=100, blank=True, null=True) 
    katakana_name = models.CharField(max_length=100, blank=True, null=True)
    category = models.CharField(max_length=100)
    image = OptimizedCloudinaryField('image', folder='thankjapan/images')
    timestamp = models.DateTimeField(auto_now_add=True)
    slug = models.SlugField(unique=True, null=False, blank=False)
    description = models.TextField(max_length=1000)
    history = models.TextField(max_length=1000)

    description_en_in = models.TextField(max_length=1500, blank=True, null=True)
    history_en_in = models.TextField(max_length=1500, blank=True, null=True)

    description_ja = models.TextField(max_length=1500, blank=True, null=True)
    history_ja = models.TextField(max_length=1500, blank=True, null=True)

    description_zh_cn = models.TextField(max_length=1500, blank=True, null=True)
    history_zh_cn = models.TextField(max_length=1500, blank=True, null=True)
    description_zh_hant = models.TextField(max_length=1500, blank=True, null=True)
    history_zh_hant = models.TextField(max_length=1500, blank=True, null=True)

    description_ko = models.TextField(max_length=1500, blank=True, null=True)
    history_ko = models.TextField(max_length=1500, blank=True, null=True)

    description_fr = models.TextField(max_length=1500, blank=True, null=True)
    history_fr = models.TextField(max_length=1500, blank=True, null=True)
    description_de = models.TextField(max_length=1500, blank=True, null=True)
    history_de = models.TextField(max_length=1500, blank=True, null=True)
    description_it = models.TextField(max_length=1500, blank=True, null=True)
    history_it = models.TextField(max_length=1500, blank=True, null=True)

    description_es_es = models.TextField(max_length=1500, blank=True, null=True)
    history_es_es = models.TextField(max_length=1500, blank=True, null=True)
    description_es_mx = models.TextField(max_length=1500, blank=True, null=True)
    history_es_mx = models.TextField(max_length=1500, blank=True, null=True)

    description_pt = models.TextField(max_length=1500, blank=True, null=True)
    history_pt = models.TextField(max_length=1500, blank=True, null=True)
    description_pt_br = models.TextField(max_length=1500, blank=True, null=True)
    history_pt_br = models.TextField(max_length=1500, blank=True, null=True)

    description_th = models.TextField(max_length=1500, blank=True, null=True)
    history_th = models.TextField(max_length=1500, blank=True, null=True)
    description_vi = models.TextField(max_length=1500, blank=True, null=True)
    history_vi = models.TextField(max_length=1500, blank=True, null=True)
    
    
    # 日本語（rubyタグ付き）
    example_sentence_ja = models.TextField(blank=True, null=True)
    # ローマ字（wa, o, e ルール適用）
    example_sentence_romaji = models.TextField(blank=True, null=True)


    example_sentence = models.TextField(blank=True, null=True)
    example_sentence_en_in = models.TextField(blank=True, null=True)
    example_sentence_zh_cn = models.TextField(blank=True, null=True)
    example_sentence_zh_hant = models.TextField(blank=True, null=True)
    example_sentence_ko = models.TextField(blank=True, null=True)
    example_sentence_fr = models.TextField(blank=True, null=True)
    example_sentence_de = models.TextField(blank=True, null=True)
    example_sentence_it = models.TextField(blank=True, null=True)
    example_sentence_es_es = models.TextField(blank=True, null=True)
    example_sentence_es_mx = models.TextField(blank=True, null=True)
    example_sentence_pt = models.TextField(blank=True, null=True)
    example_sentence_pt_br = models.TextField(blank=True, null=True)
    example_sentence_th = models.TextField(blank=True, null=True)
    example_sentence_vi = models.TextField(blank=True, null=True)


    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.englishname)
            slug = base_slug
            counter = 1
            while ThankJapanModel.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.category})"
    
class ThankJapanPremium(models.Model):
    name = models.CharField(max_length=100, unique=True)
    englishname = models.CharField(max_length=200)
    
    englishname_ja = models.CharField(max_length=250, blank=True, null=True)
    englishname_en_in = models.CharField(max_length=250, blank=True, null=True)
    englishname_zh_cn = models.CharField(max_length=250, blank=True, null=True)
    englishname_zh_hant = models.CharField(max_length=250, blank=True, null=True)
    englishname_ko = models.CharField(max_length=250, blank=True, null=True)
    englishname_fr = models.CharField(max_length=250, blank=True, null=True)
    englishname_de = models.CharField(max_length=250, blank=True, null=True)
    englishname_it = models.CharField(max_length=250, blank=True, null=True)
    englishname_es_es = models.CharField(max_length=250, blank=True, null=True)
    englishname_es_mx = models.CharField(max_length=250, blank=True, null=True)
    englishname_pt = models.CharField(max_length=250, blank=True, null=True)
    englishname_pt_br = models.CharField(max_length=250, blank=True, null=True)
    englishname_th = models.CharField(max_length=250, blank=True, null=True)
    englishname_vi = models.CharField(max_length=250, blank=True, null=True)
    
    jpname = models.CharField(max_length=200)
    romaji = models.CharField(max_length=200)
    kanji_name = models.CharField(max_length=100, blank=True, null=True) 
    katakana_name = models.CharField(max_length=100, blank=True, null=True)
    slug = models.SlugField(unique=True, max_length=255, blank=True)
    image = OptimizedCloudinaryField('image', folder='thankjapan/premium')
    category = models.CharField(max_length=100)
    jlpt_level = models.CharField(max_length=10, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    

    description = models.TextField(max_length=2000)
    history = models.TextField(max_length=2000)

    description_en_in = models.TextField(max_length=2000, blank=True, null=True)
    history_en_in = models.TextField(max_length=2000, blank=True, null=True)

    description_ja = models.TextField(max_length=2000, blank=True, null=True)
    history_ja = models.TextField(max_length=2000, blank=True, null=True)

    description_zh_cn = models.TextField(max_length=2000, blank=True, null=True)
    history_zh_cn = models.TextField(max_length=2000, blank=True, null=True)
    description_zh_hant = models.TextField(max_length=2000, blank=True, null=True)
    history_zh_hant = models.TextField(max_length=2000, blank=True, null=True)

    description_ko = models.TextField(max_length=2000, blank=True, null=True)
    history_ko = models.TextField(max_length=2000, blank=True, null=True)

    description_fr = models.TextField(max_length=2000, blank=True, null=True)
    history_fr = models.TextField(max_length=2000, blank=True, null=True)
    description_de = models.TextField(max_length=2000, blank=True, null=True)
    history_de = models.TextField(max_length=2000, blank=True, null=True)
    description_it = models.TextField(max_length=2000, blank=True, null=True)
    history_it = models.TextField(max_length=2000, blank=True, null=True)

    description_es_es = models.TextField(max_length=2000, blank=True, null=True)
    history_es_es = models.TextField(max_length=2000, blank=True, null=True)
    description_es_mx = models.TextField(max_length=2000, blank=True, null=True)
    history_es_mx = models.TextField(max_length=2000, blank=True, null=True)

    description_pt = models.TextField(max_length=2000, blank=True, null=True)
    history_pt = models.TextField(max_length=2000, blank=True, null=True)
    description_pt_br = models.TextField(max_length=2000, blank=True, null=True)
    history_pt_br = models.TextField(max_length=2000, blank=True, null=True)

    description_th = models.TextField(max_length=2000, blank=True, null=True)
    history_th = models.TextField(max_length=2000, blank=True, null=True)
    description_vi = models.TextField(max_length=2000, blank=True, null=True)
    history_vi = models.TextField(max_length=2000, blank=True, null=True)
    
    
    # 以下を ThankJapanPremium クラス内に追加
    example_sentence_ja = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_romaji = models.CharField(max_length=500, blank=True, null=True)
    example_sentence = models.TextField(max_length=1000, blank=True, null=True) # English
    
    example_sentence_en_in = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_zh_cn = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_zh_hant = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_ko = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_fr = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_de = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_it = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_es_es = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_es_mx = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_pt = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_pt_br = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_th = models.TextField(max_length=1000, blank=True, null=True)
    example_sentence_vi = models.TextField(max_length=1000, blank=True, null=True)
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.englishname)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.jlpt_level or 'No Level'}] {self.englishname}"


class ThankJapanBackgroundModel(models.Model):
    PAGE_CHOICES = [
        ('top', 'トップ画面'),
        ('quiz_menu', 'クイズ選択画面'),
        ('game', 'ゲーム画面'),
        ('result', '結果画面'),
        ('study_select', '学習選択画面'),
        ('study', '学習詳細画面'),
        ('region', '地域探索画面'),
    ]

    page_type = models.CharField(max_length=20, choices=PAGE_CHOICES, blank=True, null=True)
    background_image = OptimizedCloudinaryField('background_image', folder='thankjapan/backgrounds/', blank=True, null=True)
    sound = CloudinaryField(
        'sound',
        folder='thankjapan/sounds/',
        resource_type='video',  
        blank=True,
        null=True,
    )

    def __str__(self):
        if self.background_image:
            return str(self.background_image.url)
        return "No Background Image"
    
    
    
class Player(models.Model):
    username = models.CharField(max_length=50, unique=True)
    email = models.EmailField(max_length=255, unique=True, null=True, blank=True) 
    country = models.CharField(max_length=50, blank=True, null=True)
    password = models.CharField(max_length=128) 
    total_score = models.PositiveIntegerField(default=0)
    last_score = models.PositiveIntegerField(default=0) 
    
    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password) 

    def __str__(self):
        return f"{self.username} ({self.total_score}pt)"

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    is_premium = models.BooleanField(default=False)
    premium_expires_at = models.DateTimeField(blank=True, null=True)
    is_trial = models.BooleanField(default=False)
    trial_used = models.BooleanField(default=False)
    country = models.CharField(max_length=50, blank=True, null=True)
    privacy_policy_version = models.CharField(max_length=10, default="2026-2")
    paypal_subscription_id = models.CharField(max_length=50, blank=True, null=True)
    google_play_purchase_token = models.CharField(max_length=255, blank=True, null=True)
    total_score = models.PositiveIntegerField(default=0)
    last_score = models.PositiveIntegerField(default=0)
    last_bonus_date = models.DateField(null=True, blank=True)
    streak_count = models.IntegerField(default=0)
    last_reminder_step_sent = models.PositiveSmallIntegerField(default=0)
    viewed_word_count = models.PositiveIntegerField(default=0)
    review_prompt_completed = models.BooleanField(default=False)
    review_prompt_dismissed_until = models.DateField(null=True, blank=True)
    best_combo = models.PositiveIntegerField(default=0)
    games_played = models.PositiveIntegerField(default=0)
    daily_question_notify = models.BooleanField(default=False)
    trial_ended_popup_dismissed = models.BooleanField(default=False)
    review_prompt_shown = models.BooleanField(default=False)
    # Anchor date for the monthly +50pt continuation reward (see
    # PremiumRenewalReward) - set once, the first time this profile is seen
    # as a real paying subscriber (is_premium=True, is_trial=False), not
    # necessarily the true original contract date. No historical contract
    # start date is recorded anywhere for accounts that were already past
    # their trial before this feature shipped, so for those the anchor is
    # effectively "the day this feature started tracking them" rather than
    # their real signup day - an accepted, deliberate simplification.
    premium_paid_started_at = models.DateTimeField(null=True, blank=True)
    # The day (JST) this user last submitted the Daily Question, regardless
    # of correct/incorrect. Used to gate the "1 per day" limit for logged-in
    # users - see daily_question_view in views.py. Session-only tracking
    # (the original design) turned out to not survive a session/cookie
    # discontinuity, most commonly when a user reaches /daily/ through
    # different language-prefixed entry points that ended up on separate
    # storage contexts (e.g. distinct "Add to Home Screen" PWA installs on
    # iOS each get their own isolated cookie jar even for the same origin) -
    # 2026-09-17: reported as "answered in Vietnamese, then could answer
    # again after closing and reopening in Thai". A DB-backed, user-identity
    # keyed flag is immune to that; guests still fall back to session since
    # they have no other stable identity.
    last_daily_question_answered_date = models.DateField(null=True, blank=True)
    # Set the first (and only) time the post-registration "here's your
    # Learned Words page" intro card is shown on the top page - a DB flag
    # rather than session/cookie so it stays a true once-per-lifetime, not
    # once-per-browser (see TopView.get_context_data and top-main-v2.html).
    learned_words_intro_shown = models.BooleanField(default=False)

    @property
    def has_premium_access(self):
        if not self.is_premium:
            return False
        # premium_expires_at is unset for accounts granted premium before this
        # field existed (and for admin-granted premium) - treat "unknown
        # expiry" as still valid rather than instantly revoking their access.
        if self.premium_expires_at is not None and self.premium_expires_at < timezone.now():
            return False
        return True

    def __str__(self):
        return f"{self.user.username}'s Profile"
    
    # models.py の Profile クラス内に追加
    @property
    def rank_info(self):
        s = self.total_score
        if s >= 2000: return {"emoji": "🗾", "name": "Legend"}
        if s >= 1200: return {"emoji": "👑", "name": "General"}
        if s >= 900:  return {"emoji": "🐎", "name": "Shogun"}
        if s >= 650:  return {"emoji": "🏯", "name": "Daimyo"}
        if s >= 450:  return {"emoji": "🚩", "name": "Hatamoto"}
        if s >= 300:  return {"emoji": "⚔️", "name": "Samurai"}
        if s >= 200:  return {"emoji": "🥷", "name": "Ninja"}
        if s >= 100:  return {"emoji": "🚣", "name": "Ronin"}
        if s >= 50:   return {"emoji": "🎒", "name": "Traveler"}
        return {"emoji": "🌾", "name": "Villager"}

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if not hasattr(instance, 'profile'):
        Profile.objects.create(user=instance)
    instance.profile.save()
    


class Achievement(models.Model):
    """One row per badge a user has unlocked. The badge catalog itself (codes,
    thresholds, which Profile field each checks) lives in achievements.py as
    plain Python, not in the DB, so adding a new badge never needs a migration."""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=50)
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'code')

    def __str__(self):
        return f"{self.user.username} - {self.code}"


class WeeklyScore(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    score = models.PositiveIntegerField(default=0)
    country = models.CharField(max_length=50, blank=True, null=True)
    week_start = models.DateField()

    class Meta:
        unique_together = ('user', 'week_start')
        ordering = ['-score']

    def __str__(self):
        return f"{self.user.username} - {self.week_start} ({self.score}pt)"

    @classmethod
    def get_current_week_start(cls):
        from django.utils import timezone
        import datetime
        today = timezone.now().date()
        return today - datetime.timedelta(days=today.weekday())



class PremiumRenewalReward(models.Model):
    """One row per +50pt continuation-benefit grant (see
    Profile.premium_paid_started_at) - both the audit trail shown on the
    account settings page and the actual double-grant guard: period_start
    is the specific monthly anniversary a grant corresponds to, and the
    (user, period_start) uniqueness is what makes granting safe to attempt
    redundantly from both the daily batch and the PayPal webhook - whichever
    gets there first wins, the other's insert is simply rejected."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='premium_renewal_rewards')
    period_start = models.DateTimeField()
    points = models.PositiveIntegerField(default=50)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'period_start'], name='unique_premium_renewal_reward_per_period')
        ]
        ordering = ['-granted_at']

    def __str__(self):
        return f"{self.user.username} +{self.points}pt ({self.period_start.date()})"


class FCMDevice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    token = models.TextField(unique=True)
    lang = models.CharField(max_length=10, default='ja')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} ({self.lang})"


# Premium-table categories that are free for everyone to browse/review (see
# thank_japan_app.views: game_play's mode=single handling and
# ImgPremiumDetailView.get_object, neither of which gates them behind
# is_premium). Shared with views.py's _build_tile_game_questions so both the
# main game and 今日の一問 agree on what counts as "free preview" content -
# defined here (not views.py) since views.py already imports from this
# module and the reverse would be circular.
FREE_PREVIEW_PREMIUM_CATEGORIES = ['DailyConversation', 'slang', 'TourismEtiquette', 'Entertainment', 'RomanceRelationships']


class DailyQuestionManager(models.Manager):
    # Mirrors DIFFICULTY_SETTINGS['normal'] in thank_japan_app/views.py so
    # "today's question" always matches the site-wide notion of normal difficulty.
    NORMAL_CATEGORY_FILTER = ['cook', 'food', 'culture', 'body', 'live', 'work', 'dailyactions']
    NORMAL_LENGTH_REGEX = r'^.{1,9}$'
    RECENT_EXCLUSION_DAYS = 30  # avoid repeating a word used in roughly the last month

    def get_or_create_for_date(self, date):
        """Return (DailyQuestion, created) for `date`, selecting a word on first call.

        Single source of truth for both the daily selection batch and the
        page view's on-the-fly fallback, so they can never pick differently.

        2026-09-11: candidates now come from two tables - ThankJapanModel
        (as before) and the free-preview ThankJapanPremium categories - each
        candidate is picked with equal probability regardless of which table
        it's from, matching the same policy just adopted for the main game's
        non-premium pool (see _build_tile_game_questions). A chosen
        ThankJapanModel word is saved to `word`; a chosen ThankJapanPremium
        word is saved to `premium_word` - `word` stays null for those rows,
        `premium_word` stays null for classic ThankJapanModel-selected rows.
        """
        existing = self.filter(date=date).select_related('word', 'premium_word').first()
        if existing:
            return existing, False

        free_pool_ids = list(ThankJapanModel.objects.filter(
            category__in=self.NORMAL_CATEGORY_FILTER,
            name__iregex=self.NORMAL_LENGTH_REGEX,
        ).exclude(image='').values_list('id', flat=True))
        premium_pool_ids = list(ThankJapanPremium.objects.filter(
            category__in=FREE_PREVIEW_PREMIUM_CATEGORIES,
            name__iregex=self.NORMAL_LENGTH_REGEX,
        ).exclude(image='').values_list('id', flat=True))

        recent = self.order_by('-date')[:self.RECENT_EXCLUSION_DAYS]
        recent_free_ids = {i for i in recent.values_list('word_id', flat=True) if i}
        recent_premium_ids = {i for i in recent.values_list('premium_word_id', flat=True) if i}

        free_candidates = [pk for pk in free_pool_ids if pk not in recent_free_ids]
        premium_candidates = [pk for pk in premium_pool_ids if pk not in recent_premium_ids]
        combined = [('free', pk) for pk in free_candidates] + [('premium', pk) for pk in premium_candidates]
        if not combined:
            # Recency exclusion emptied both pools - ignore it entirely
            # rather than refuse to select anything (same fallback the
            # single-table version always used).
            combined = [('free', pk) for pk in free_pool_ids] + [('premium', pk) for pk in premium_pool_ids]

        if not combined:
            return None, False

        model_type, chosen_id = random.choice(combined)
        if model_type == 'free':
            return self.get_or_create(date=date, defaults={'word': ThankJapanModel.objects.get(id=chosen_id)})
        return self.get_or_create(date=date, defaults={'premium_word': ThankJapanPremium.objects.get(id=chosen_id)})


class DailyQuestion(models.Model):
    date = models.DateField(unique=True)  # JST基準の日付
    # Exactly one of word/premium_word is set per row - both nullable so
    # either table can be the source (see FREE_PREVIEW_PREMIUM_CATEGORIES
    # above and get_or_create_for_date). Use `selected_word` rather than
    # either field directly.
    word = models.ForeignKey(ThankJapanModel, on_delete=models.CASCADE, related_name='daily_appearances', null=True, blank=True)
    premium_word = models.ForeignKey(ThankJapanPremium, on_delete=models.CASCADE, related_name='daily_appearances', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DailyQuestionManager()

    @property
    def selected_word(self):
        return self.word or self.premium_word

    def __str__(self):
        return f"{self.date} - {self.selected_word.englishname}"


class DailyActivitySummary(models.Model):
    """One row per (date, activity_type) - a running aggregate, not a
    per-event log (2026-09-12, replacing the short-lived GameActivityLog
    which recorded one row per submission and was judged an unbounded-growth/
    privacy risk before it ever left QA). `record()` is called only from
    inside game_submit_answer/daily_question_view's POST handler, i.e. only
    once a real tile submission has actually gone through, never on a bare
    page view - this is the ground-truth usage log real_usage_report reads
    from, since GA4/Cloudflare-visible traffic can be inflated by crawlers
    that never complete that interaction. No per-user/per-event data is
    kept - just same-day running totals, updated via F() so concurrent
    submissions on the same day can never clobber each other's increment."""
    ACTIVITY_DAILY_QUESTION = 'daily_question'
    ACTIVITY_MAIN_GAME = 'main_game'
    ACTIVITY_CHOICES = [
        (ACTIVITY_DAILY_QUESTION, 'Daily Question'),
        (ACTIVITY_MAIN_GAME, 'Main Game'),
    ]

    date = models.DateField(db_index=True)
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_CHOICES)
    total_count = models.PositiveIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    guest_count = models.PositiveIntegerField(default=0)
    # Per-game funnel counts (2026-11-19) - only ever set on the
    # ACTIVITY_MAIN_GAME row. total_count above counts *answers*, not games,
    # so these are what show how many games were started vs. actually
    # finished (reached the result screen). Covers timeattack + first_taste
    # (same scope as total_count); first_taste_* is the subset for the
    # first-visit guest trial on its own. See record_game_started/
    # record_game_completed - each fires at most once per game.
    games_started = models.PositiveIntegerField(default=0)
    games_completed = models.PositiveIntegerField(default=0)
    first_taste_started = models.PositiveIntegerField(default=0)
    first_taste_completed = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('date', 'activity_type')
        ordering = ['-date']

    @property
    def member_count(self):
        return self.total_count - self.guest_count

    @classmethod
    def record(cls, activity_type, is_guest, is_correct):
        today = timezone.localdate()
        obj, _ = cls.objects.get_or_create(date=today, activity_type=activity_type)
        cls.objects.filter(pk=obj.pk).update(
            total_count=F('total_count') + 1,
            correct_count=F('correct_count') + (1 if is_correct else 0),
            guest_count=F('guest_count') + (1 if is_guest else 0),
        )

    @classmethod
    def _record_game_funnel(cls, field, first_taste_field, is_first_taste):
        today = timezone.localdate()
        obj, _ = cls.objects.get_or_create(date=today, activity_type=cls.ACTIVITY_MAIN_GAME)
        updates = {field: F(field) + 1}
        if is_first_taste:
            updates[first_taste_field] = F(first_taste_field) + 1
        cls.objects.filter(pk=obj.pk).update(**updates)

    @classmethod
    def record_game_started(cls, is_first_taste):
        cls._record_game_funnel('games_started', 'first_taste_started', is_first_taste)

    @classmethod
    def record_game_completed(cls, is_first_taste):
        cls._record_game_funnel('games_completed', 'first_taste_completed', is_first_taste)

    def __str__(self):
        return f"{self.date} - {self.get_activity_type_display()} ({self.total_count})"


class SiteVisitCounter(models.Model):
    """One row per calendar date - a running daily page-view count, not a
    per-request log (same 1-row-per-day pattern as DailyActivitySummary
    above). Powers the header's all-time "X people have visited" badge.
    No user/session/IP/user-agent is ever recorded, just a same-day running
    total updated via F() so concurrent requests on the same day can't
    clobber each other's increment - at most ~365 rows/year regardless of
    traffic volume. Deliberately counts every real page view including bot
    traffic (unlike DailyActivitySummary's ground-truth gameplay counts) -
    this is a "site has real, sizeable traffic" impression number, not an
    analytics metric, so bot inflation is acceptable here (see
    VisitCounterMiddleware, thank_japan_app/middleware.py)."""
    date = models.DateField(unique=True, db_index=True)
    visit_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-date']

    @classmethod
    def record_visit(cls):
        today = timezone.localdate()
        obj, _ = cls.objects.get_or_create(date=today)
        cls.objects.filter(pk=obj.pk).update(visit_count=F('visit_count') + 1)

    @classmethod
    def cumulative_total(cls):
        """All-time sum across every day's row, cached briefly so the
        header badge (rendered on every page, for every visitor) doesn't
        run a SUM() query per request."""
        from django.core.cache import cache
        from django.db.models import Sum
        cache_key = 'site_visit_cumulative_total'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        total = cls.objects.aggregate(total=Sum('visit_count'))['total'] or 0
        cache.set(cache_key, total, 300)
        return total

    def __str__(self):
        return f"{self.date} - {self.visit_count}"


class WordReviewLog(models.Model):
    """One row per (user, word) the user has correctly reviewed from a word-
    detail page ("single" mode) - not one row per review event, following
    the same growth/privacy lesson as DailyActivitySummary/GameActivityLog
    above: reviewing the same word many times just bumps review_count and
    last_reviewed_at on the existing row instead of growing without bound.
    Written only from game_submit_answer's mode='single' + correct branch,
    and only for logged-in users - guests have no identity to attach a row
    to, and normal multi-question game modes don't write here at all."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='word_review_logs')
    # Exactly one of word/premium_word is set per row, same dual-nullable-FK
    # shape as DailyQuestion above - use `selected_word` rather than either
    # field directly.
    word = models.ForeignKey(ThankJapanModel, on_delete=models.CASCADE, related_name='review_logs', null=True, blank=True)
    premium_word = models.ForeignKey(ThankJapanPremium, on_delete=models.CASCADE, related_name='review_logs', null=True, blank=True)
    review_count = models.PositiveIntegerField(default=1)
    first_reviewed_at = models.DateTimeField(auto_now_add=True)
    last_reviewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'word'], name='unique_word_review_log_free'),
            models.UniqueConstraint(fields=['user', 'premium_word'], name='unique_word_review_log_premium'),
        ]
        ordering = ['-last_reviewed_at']

    @property
    def selected_word(self):
        return self.word or self.premium_word

    @classmethod
    def record_review(cls, user, word=None, premium_word=None):
        obj, created = cls.objects.get_or_create(user=user, word=word, premium_word=premium_word)
        if not created:
            cls.objects.filter(pk=obj.pk).update(review_count=F('review_count') + 1)
        return obj

    def __str__(self):
        return f"{self.user.username} - {self.selected_word.englishname} (x{self.review_count})"

