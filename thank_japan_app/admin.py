from django.contrib import admin
from .models import ThankJapanModel, ThankJapanBackgroundModel
import logging

logger = logging.getLogger(__name__)

class ThankJapanAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    
    def save_model(self, request, obj, form, change):
        try:
            obj.save()
        except ValidationError as e:
            logger.error(f"Validation error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
    
class ThankJapanBackgroundAdmin(admin.ModelAdmin):
    list_display = ('id', 'background_image')
    
admin.site.register(ThankJapanModel, ThankJapanAdmin)
admin.site.register(ThankJapanBackgroundModel, ThankJapanBackgroundAdmin)

