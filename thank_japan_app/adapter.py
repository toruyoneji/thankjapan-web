from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from .models import Player  

class MySocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        
        user = super().save_user(request, sociallogin, form)
        
        
        code = request.META.get('HTTP_CF_IPCOUNTRY')
        
        if code:
            
            country_map = {
                'JP': '🇯🇵 Japan',
                'KR': '🇰🇷 Korea',
                'TW': '🇹🇼 Taiwan',
                'US': '🇺🇸 USA',
                'DE': '🇩🇪 Germany',
                'FR': '🇫🇷 France',
                'PK': '🇵🇰 Pakistan',
                'BR': '🇧🇷 Brazil',
                'IN': '🇮🇳 India',
                'VN': '🇻🇳 Vietnam',
                'TH': '🇹🇭 Thailand',
                'GB': '🇬🇧 United Kingdom',
                'CA': '🇨🇦 Canada',
                'AU': '🇦🇺 Australia',
                'ID': '🇮🇩 Indonesia',
                'PH': '🇵🇭 Philippines',
                'ES': '🇪🇸 Spain',
                'IT': '🇮🇹 Italy',
                'MX': '🇲🇽 Mexico',
                'RU': '🇷🇺 Russia',
                'SG': '🇸🇬 Singapore',
                'MY': '🇲🇾 Malaysia',
                'HK': '🇭🇰 Hong Kong',
                'NL': '🇳🇱 Netherlands',
                'SE': '🇸🇪 Sweden',
                'CH': '🇨🇭 Switzerland',
                'TR': '🇹🇷 Turkey',
                'SA': '🇸🇦 Saudi Arabia',
                'AE': '🇦🇪 UAE',
                'EG': '🇪🇬 Egypt',
                'NG': '🇳🇬 Nigeria',
                'ZA': '🇿🇦 South Africa',
                'CI': '🇨🇮 Côte d\'Ivoire',
                'MG': '🇲🇬 Madagascar',
                'NZ': '🇳🇿 New Zealand',
                'AR': '🇦🇷 Argentina',
                'CL': '🇨🇱 Chile',
                'CO': '🇨🇴 Colombia',
                'PE': '🇵🇪 Peru',
                'PL': '🇵🇱 Poland',
                'UA': '🇺🇦 Ukraine',
                'GR': '🇬🇷 Greece',
                'AT': '🇦🇹 Austria',
                'BE': '🇧🇪 Belgium',
                'DK': '🇩🇰 Denmark',
                'FI': '🇫🇮 Finland',
                'NO': '🇳🇴 Norway',
                'IE': '🇮🇪 Ireland',
                'PT': '🇵🇹 Portugal',
            }
            
            display_name = country_map.get(code, code)
            
            
            user.profile.country = display_name
            user.profile.save()

            
            player = Player.objects.filter(username=user.username).first()
            if player:
                player.country = display_name
                player.save()
            
        return user