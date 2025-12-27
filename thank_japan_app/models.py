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
    category = models.CharField(max_length=100)
    description = models.TextField(max_length=1000)
    history = models.TextField(max_length=1000)
    image = CloudinaryField('image', folder='thankjapan/images')
    timestamp = models.DateTimeField(auto_now_add=True)
    slug = models.SlugField(unique=True, null=False, blank=False)

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

class ThankJapanBackgroundModel(models.Model):
    background_image = CloudinaryField('background_image', folder='thankjapan/backgrounds/', blank=True, null=True)

    def __str__(self):
        if self.background_image:
            return str(self.background_image.url)
        return "No Background Image"

class Player(models.Model):
    username = models.CharField(max_length=50, unique=True)
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
    total_score = models.PositiveIntegerField(default=0)
    last_score = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.user.username}'s Profile"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if not hasattr(instance, 'profile'):
        Profile.objects.create(user=instance)
    instance.profile.save()