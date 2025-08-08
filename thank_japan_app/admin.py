from django.contrib import admin
from .models import ThankJapanModel, ThankJapanBackgroundModel

class ThankJapanAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    
class ThankJapanBackgroundAdmin(admin.ModelAdmin):
    list_display = ('background_image')
    
admin.site.register(ThankJapanModel, ThankJapanAdmin)
admin.site.register(ThankJapanBackgroundModel, ThankJapanBackgroundAdmin)

