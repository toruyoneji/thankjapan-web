from django.contrib import admin
from .models import ThankJapanModel

class ThankJapanAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    
    
admin.site.register(ThankJapanModel, ThankJapanAdmin)

