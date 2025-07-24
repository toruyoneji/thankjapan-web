from django import forms
#from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from django.core.mail import send_mail

User = get_user_model()

class DeleteUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'password']
        
class CompanyForm(forms.Form):
    
    username = forms.CharField(label="UserName",max_length=50,
                               error_messages={'required':'required'},
                               widget=forms.TextInput(attrs={'size': '30', 'style': 'height: 30px'}))
    
    email = forms.EmailField(label="Email", error_messages={'required': 'requeired'},
                             widget=forms.TextInput(attrs={'size': '40', 'style': 'height: 30px'}))
    
    title = forms.CharField(label="Title", max_length=50, required=False,
                            widget=forms.TextInput(attrs={'size': '40', 'style': 'height: 30px;'}))
    
    message = forms.CharField(label="Message", widget=forms.Textarea, required=False)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['username'].widget.attrs['placeholder'] = "name"
        self.fields['email'].widget.attrs['placeholder'] = "sample@example.com"
        self.fields['title'].widget.attrs['placeholder'] = "title"
        self.fields['message'].widget.attrs['placeholder'] = "message..."
        
        
    def send_email(self):
        send_mail(
            subject=f"Contact from {self.cleaned_data['username']}",
            message=self.cleaned_data['message'],
            from_email=self.cleaned_data['email'],
            recipient_list=['your_destination@example.com'],
        )
        
class AnswerForm(forms.Form):
    answer = forms.CharField(label="What's name?",max_length=100,
                             error_messages={'required': 'required'},
                             widget=forms.TextInput(attrs={'size': '40', 'style': 'height: 30px; font-size: 1.5rem;'}))
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['answer'].widget.attrs['placeholder'] = 'Answer Name'
