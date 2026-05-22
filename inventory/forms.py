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
        fields=['name','quantity','category','min_quantity']



from django import forms
from .models import (
    PurchaseOrderTracking,
    PurchaseOrderTrackingItem,
    PurchaseOrderStage,
    PurchaseOrderStageLog,
)


class PurchaseOrderTrackingForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderTracking
        fields = ["remarks"]


class PurchaseOrderTrackingItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderTrackingItem
        fields = ["arrived_quantity", "remarks"]


class PurchaseOrderStageForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderStage
        fields = ["name", "estimated_days", "is_final_stage", "sort_order", "is_active"]


class PurchaseOrderStageLogForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderStageLog
        fields = ["stage", "entered_at", "exit_datetime", "manual_days_at_stage", "remarks"]
        widgets = {
            "entered_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "exit_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }