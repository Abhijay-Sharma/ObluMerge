from django import forms  # Django's form library
from .models import User
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm, UsernameField
from .models import InventoryItem, Category



# Custom registration form that extends the default UserCreationForm
User=get_user_model()
class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username",)
        field_classes = {'username': UsernameField}

class InventoryItemForm(forms.ModelForm):
    category=forms.ModelChoiceField(queryset=Category.objects.all(),initial=0)
    class Meta:
        model=InventoryItem
        fields=['name','quantity','category']