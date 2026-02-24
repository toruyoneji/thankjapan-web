from django.db import models
from cloudinary.models import CloudinaryField
from django.contrib.auth.hashers import make_password, check_password
from django.utils.text import slugify
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class ThankJapanModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    englishname = models.CharField(max_length=100)
    jpname = models.CharField(max_length=100)
    kanji_name = models.CharField(max_length=100, blank=True, null=True) 
    katakana_name = models.CharField(max_length=100, blank=True, null=True)
    category = models.CharField(max_length=100)
    image = CloudinaryField('image', folder='thankjapan/images')
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
    jpname = models.CharField(max_length=200)
    romaji = models.CharField(max_length=200)
    kanji_name = models.CharField(max_length=100, blank=True, null=True) 
    katakana_name = models.CharField(max_length=100, blank=True, null=True)
    slug = models.SlugField(unique=True, max_length=255, blank=True)
    image = CloudinaryField('image', folder='thankjapan/premium')
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
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.englishname)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.jlpt_level or 'No Level'}] {self.englishname}"


class ThankJapanBackgroundModel(models.Model):
    background_image = CloudinaryField('background_image', folder='thankjapan/backgrounds/', blank=True, null=True)

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
    country = models.CharField(max_length=50, blank=True, null=True)
    privacy_policy_version = models.CharField(max_length=10, default="2026-2")
    paypal_subscription_id = models.CharField(max_length=50, blank=True, null=True)
    total_score = models.PositiveIntegerField(default=0)
    last_score = models.PositiveIntegerField(default=0)
    last_bonus_date = models.DateField(null=True, blank=True)

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