from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

class MySocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        
        user = super().save_user(request, sociallogin, form)
        
        country_code = request.META.get('HTTP_CF_IPCOUNTRY')
        
        if country_code:
            
            user.profile.country = country_code
            user.profile.save()
            
        return user