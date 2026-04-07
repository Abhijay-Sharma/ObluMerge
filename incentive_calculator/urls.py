from django.contrib import admin
from django.urls import path
from . import views

app_name = "incentive_calculator"

urlpatterns = [
    path("", views.IncentiveCalculatorView.as_view(), name="incentive_calculator"),
    path(
        "asm/",
        views.ASMIncentiveCalculatorView.as_view(),
        name="asm_incentive_calculator",
    ),
    path(
        "asm/paid-only/",
        views.ASMIncentiveCalculatorPaidOnlyView.as_view(),
        name="asm_incentive_paid_only",
    ),
    path(
        "product-incentives/",
        views.ProductIncentiveListView.as_view(),
        name="product_incentive_list",
    ),
    path('admin-incentive-control/', views.ASMIncentivePaidUnpaidView.as_view(), name='admin_incentive_control'),


    path('rsm-team-dashboard/', views.RSMTeamIncentiveDashboardView.as_view(), name='rsm_team_dashboard'),
]