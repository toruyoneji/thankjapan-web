from django import forms
#from django.contrib.auth.models import User
from django.contrib.auth import get_user_model

User = get_user_model()

class DeleteUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'password']
