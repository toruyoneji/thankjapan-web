from django.db import models

class ThankJapanModel(models.Model):
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=100)
    description = models.TextField(max_length=1000)
    image = models.ImageField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name + "(" + self.category + ")"