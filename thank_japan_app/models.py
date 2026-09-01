import random

from django.db import models
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
    
    

class FCMDevice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    token = models.TextField(unique=True)
    lang = models.CharField(max_length=10, default='ja')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} ({self.lang})"


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
        """
        existing = self.filter(date=date).select_related('word').first()
        if existing:
            return existing, False

        pool = ThankJapanModel.objects.filter(
            category__in=self.NORMAL_CATEGORY_FILTER,
            name__iregex=self.NORMAL_LENGTH_REGEX,
        )
        recent_word_ids = list(
            self.order_by('-date')[:self.RECENT_EXCLUSION_DAYS].values_list('word_id', flat=True)
        )
        candidates = pool.exclude(id__in=recent_word_ids)
        if not candidates.exists():
            candidates = pool

        candidate_ids = list(candidates.values_list('id', flat=True))
        if not candidate_ids:
            return None, False

        word = ThankJapanModel.objects.get(id=random.choice(candidate_ids))
        return self.get_or_create(date=date, defaults={'word': word})


class DailyQuestion(models.Model):
    date = models.DateField(unique=True)  # JST基準の日付
    word = models.ForeignKey(ThankJapanModel, on_delete=models.CASCADE, related_name='daily_appearances')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DailyQuestionManager()

    def __str__(self):
        return f"{self.date} - {self.word.englishname}"

