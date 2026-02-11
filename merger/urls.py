"""
URL configuration for merger project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path , include
from django.contrib.auth.views import LoginView, LogoutView
from inventory.views import SignUpView, WelcomeView
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/',LoginView.as_view() ,name='login'),
    path('logout/',LogoutView.as_view() ,name='logout'),
    # 🔐 Password reset
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(),
        name='password_reset'
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(),
        name='password_reset_done'
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(),
        name='password_reset_confirm'
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(),
        name='password_reset_complete'
    ),
    path('inventory/', include('inventory.urls')),
    path('signup/',SignUpView.as_view(),name='signup'),
    path('',WelcomeView.as_view(),name='welcome'),
    path('quotations/',include('quotations.urls')),
    path('customers/',include('customer_dashboard.urls')),
    path('proforma/', include('proforma_invoice.urls')),
    path('vouchers/', include('tally_voucher.urls')),
    path('incentives/', include('incentive_calculator.urls')),
]
