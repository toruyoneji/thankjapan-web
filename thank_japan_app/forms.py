from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from .models import Player
import re

User = get_user_model()

def validform(value):
    if re.search(r'[!<>&%$#:;"\'=\?\\*\+\|/0-9|]', value):
        raise ValidationError(f'{value} is Invalid characters are included.', code='error_msg')
    
    

class AnswerForm(forms.Form):
    answer = forms.CharField(label="Can you name what's in this image? Use romaji to answer.",max_length=50,
                             error_messages={'required': 'required'},
                             widget=forms.TextInput(attrs={'size': '40', 
                                                           'style': 'height: 30px; font-size: 1.5rem;',
                                                           'placeholder': 'Answer Name'}),
                             validators=[validform])
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        


class ContactForm(forms.Form):
    name = forms.CharField(
        max_length=50, 
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Your Name'})
    )
    title = forms.CharField(
        max_length=100, 
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Subject/Title'})
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'your-email@example.com'})
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={'placeholder': 'Your message here...'}),
        required=True
    )
    
class UsernameForm(forms.Form):
    username = forms.CharField(
        label="Username",
        max_length=50,
        widget=forms.TextInput(attrs={'placeholder': 'Enter your username'})
    )
    country = forms.CharField(
        label="Country",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Country (optional)'})
    )


