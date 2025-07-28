from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
import re

User = get_user_model()

def validform(value):
    if re.search(r'[!<>&%$#:;"\'=\?\\*\+\|/0-9|]', value):
        raise ValidationError(f'{value} is Invalid characters are included.', code='error_msg')
    
    

class AnswerForm(forms.Form):
    answer = forms.CharField(label="What's name?",max_length=50,
                             error_messages={'required': 'required'},
                             widget=forms.TextInput(attrs={'size': '40', 
                                                           'style': 'height: 30px; font-size: 1.5rem;',
                                                           'placeholder': 'Answer Name'}),
                             validators=[validform])
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        