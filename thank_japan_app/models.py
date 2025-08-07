from django.db import models
from cloudinary.models import CloudinaryField


class ThankJapanModel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=100)
    description = models.TextField(max_length=1000)
    image = CloudinaryField('image',folder='thankjapan/images') 
    background_image = CloudinaryField('background_image', folder='thankjapan/backgrounds/', blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name + "(" + self.category + ")"




