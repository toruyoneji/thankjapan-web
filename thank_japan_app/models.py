from django.db import models
from cloudinary.models import CloudinaryField

class ThankJapanModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    englishname = models.CharField(max_length=100)
    jpname = models.CharField(max_length=100)
    category = models.CharField(max_length=100)
    description = models.TextField(max_length=1000)
    history = models.TextField(max_length=1000)
    image = CloudinaryField('image', folder='thankjapan/images')
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.category})"


class ThankJapanBackgroundModel(models.Model):
    background_image = CloudinaryField('background_image', folder='thankjapan/backgrounds/', blank=True, null=True)
    

    def __str__(self):
        if self.background_image:
            return str(self.background_image.url)
        else:
            return "No Background Image"


class Player(models.Model):
    username = models.CharField(max_length=50, unique=True)
    country = models.CharField(max_length=50, blank=True, null=True)
    total_score = models.PositiveIntegerField(default=0)
    last_score = models.PositiveIntegerField(default=0)  

    def __str__(self):
        return f"{self.username} ({self.total_score}pt)"



