from django import template

register = template.Library()

@register.filter
def format_category(value):
    if not value:
        return value
    
    mapping = {
        'householditems': 'Household Items',
        'dailyactions': 'Daily Actions',
        'dailyconversation': 'Daily Conversation',
        'businessjapanese': 'Business Japanese',
        'livinginjapan': 'Living in Japan',
        'medicalemergency': 'Medical Emergency',
        'realestaterules': 'Real Estate Rules',
        'tourismetiquette': 'Tourism Etiquette',
        'foodculture': 'Food Culture',
        'traditionalarts': 'Traditional Arts',
        'slang': 'Slang',
        
    }

    
    val_lower = str(value).lower()
    if val_lower in mapping:
        return mapping[val_lower]
    
    
    return val_lower.title()