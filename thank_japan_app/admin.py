from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import ThankJapanModel, ThankJapanBackgroundModel, Player, Profile
import logging
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profile'

class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)

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
    
    def save_model(self, request, obj, form, change):
        try:
            obj.save()
        except ValidationError as e:
            logger.error(f"Validation error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            
class PlayerAdmin(admin.ModelAdmin):
    list_display = ('username', 'country', 'last_score')

admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(ThankJapanModel, ThankJapanAdmin)
admin.site.register(ThankJapanBackgroundModel, ThankJapanBackgroundAdmin)
admin.site.register(Player, PlayerAdmin)
admin.site.register(Profile)