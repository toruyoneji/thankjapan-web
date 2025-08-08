from django.db import models
from cloudinary.models import CloudinaryField

class ThankJapanModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=100)
    description = models.TextField(max_length=1000)
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





