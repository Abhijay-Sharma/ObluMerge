from calendar import month

from django.shortcuts import render , redirect , reverse
from django.urls import reverse_lazy
from django.views.generic import TemplateView, View, CreateView, UpdateView, DeleteView,ListView  # Imports TemplateView, a built-in Django view for rendering templates.
from .forms import CustomUserCreationForm , InventoryItemForm
from django.contrib.auth import authenticate , login ,logout
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import InventoryItem, Category, MonthlyStockData, DailyStockData
import pandas as pd
import re
import json
from django.core.serializers.json import DjangoJSONEncoder
from sklearn.linear_model import LinearRegression
import numpy as np
from django.shortcuts import get_object_or_404
import calendar
from inventory.mixins import AccountantRequiredMixin
from .utils import fetch_tally_stock
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from django.db.models import Sum
from django.db.models.functions import TruncMonth
import datetime
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from django.db.models import Sum, Max
from tally_voucher.models import VoucherStockItem
from collections import defaultdict
from customer_dashboard.models import Customer
from urllib.parse import quote

logger = logging.getLogger(__name__)


# Create your views here.
class WelcomeView(LoginRequiredMixin,TemplateView):
    template_name = "welcome.html"

class Index(TemplateView):
    template_name = "inventory/index.html"  #Defines a class-based view called Index which will render the inventory/index.html file when called.

class Dashboard(AccountantRequiredMixin, View):
    def get(self,request):
        items = InventoryItem.objects.filter
        return render(request, 'inventory/dashboard.html',{'items':items})

class Dashboard2(AccountantRequiredMixin, View):
    def get(self, request):
        tally_stock = fetch_tally_stock()
        names = tally_stock.keys()
        if not tally_stock:
            tally_stock = {"error": "fetch_tally_stock returned nothing"}

        for name in names:

            quantity = tally_stock[name]["balance"]
            # update all rows with this name
            updated = InventoryItem.objects.filter(name=name).update(quantity=quantity)
            # if none exist, create one
            if updated == 0:
                InventoryItem.objects.create(name=name, quantity=quantity)

            # Now fetch all items to show on dashboard
        items = InventoryItem.objects.all()
        return render(request, 'inventory/dashboard.html', {
            'items': items,
            'tally_stock': {'test': 'HELLO FROM VIEW'},
        })

class CategoryDashboard(AccountantRequiredMixin, View):
    def get(self,request,category):
        items = InventoryItem.objects.filter(category=category)
        return render(request, 'inventory/dashboard.html',{'items':items})

class CategoryListView(AccountantRequiredMixin,ListView):
    queryset = Category.objects.all()
    template_name = 'inventory/category_list.html'
    context_object_name = 'category_list'

# This view handles both displaying the signup form and processing form submissions
class SignUpView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/signup.html'

    def get_success_url(self):
        return reverse('login')

class LogoutView(View):
    template_name = "inventory/logout.html"

    def get(self, request):
        logout(request)  # Logs the user out
        return render(request, self.template_name)  # Shows the logout page

class AddItem(AccountantRequiredMixin, CreateView):
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = 'inventory/item_form.html'
    success_url = reverse_lazy('dashboard')
    def get_context_data(self, **kwargs):
        context=super().get_context_data(**kwargs)
        context['categories'] = Category.objects.all()
        return context
    def form_valid(self, form):
        form.instance.user=self.request.user
        return super().form_valid(form)

class EditItem(AccountantRequiredMixin, UpdateView):
    model = InventoryItem
    form_class=InventoryItemForm
    template_name = 'inventory/item_form.html'
    success_url = reverse_lazy('dashboard')

class DeleteItem(AccountantRequiredMixin, DeleteView):
    model= InventoryItem
    template_name = 'inventory/delete_item.html'
    success_url = reverse_lazy('dashboard')
    context_object_name = 'item'


def extract_numeric(value):
    if isinstance(value, str):
        match = re.search(r"-?\d+\.?\d*", value.replace(',', ''))
        return float(match.group()) if match else 0
    return value if pd.notna(value) else 0


def stock_chart_view(request):
    # Load Excel and clean
    df = pd.read_excel(r"C:\Users\abhij\OneDrive\Desktop\StkGrpSum.xlsx", skiprows=7)
    df.columns = ['Month', 'Inwards_Qty', 'Inwards_Value', 'Outwards_Qty', 'Outwards_Value', 'Closing_Qty',
                  'Closing_Value']
    df = df[df['Month'].notna() & ~df['Month'].str.contains('Opening|Grand', na=False)]

    for col in ['Inwards_Qty', 'Inwards_Value', 'Outwards_Qty', 'Outwards_Value', 'Closing_Qty', 'Closing_Value']:
        df[col] = df[col].apply(extract_numeric)

    df.reset_index(drop=True, inplace=True)

    # Prepare JSON data for Chart.js
    chart_data = {
        'labels': df['Month'].tolist(),
        'inwards_qty': df['Inwards_Qty'].tolist(),
        'outwards_qty': df['Outwards_Qty'].tolist(),
        'closing_qty': df['Closing_Qty'].tolist(),
        'closing_value': df['Closing_Value'].tolist()
    }

    return render(request, 'inventory/chartjs_stock.html', {
        'chart_data': json.dumps(chart_data, cls=DjangoJSONEncoder)
    })


def predict_min_stock_view(request):
    # Load and clean Excel
    df = pd.read_excel(r"C:\Users\abhij\OneDrive\Desktop\StkGrpSum.xlsx", skiprows=7)
    df.columns = ['Month', 'Inwards_Qty', 'Inwards_Value', 'Outwards_Qty', 'Outwards_Value', 'Closing_Qty', 'Closing_Value']
    df = df[df['Month'].notna() & ~df['Month'].str.contains('Opening|Grand', na=False)]

    for col in ['Inwards_Qty', 'Inwards_Value', 'Outwards_Qty', 'Outwards_Value', 'Closing_Qty', 'Closing_Value']:
        df[col] = df[col].apply(extract_numeric)

    df.reset_index(drop=True, inplace=True)
    df['MonthIndex'] = np.arange(len(df)).reshape(-1, 1)

    # Trend-based prediction using Closing_Qty
    model_trend = LinearRegression()
    model_trend.fit(df[['MonthIndex']], df['Closing_Qty'])
    future_months = np.array([len(df), len(df)+1, len(df)+2]).reshape(-1, 1)
    trend_predictions = model_trend.predict(future_months)

    # Demand-based prediction using Outwards_Qty
    model_demand = LinearRegression()
    model_demand.fit(df[['MonthIndex']], df['Outwards_Qty'])
    demand_predictions = model_demand.predict(future_months)

    # Calculate minimum stock suggestions
    min_stock_trend = int(min(trend_predictions))
    min_stock_demand = int(max(demand_predictions))
    min_stock_demand_buffered = int(min_stock_demand * 1.1)

    future_labels = ['Month +' + str(i+1) for i in range(3)]
    trend_pred = list(zip(future_labels, [int(x) for x in trend_predictions]))
    demand_pred = list(zip(future_labels, [int(x) for x in demand_predictions]))

    context = {
        'trend_pred': trend_pred,
        'demand_pred': demand_pred,
        'min_stock_trend': min_stock_trend,
        'min_stock_demand': min_stock_demand_buffered,
        'excel_data': df[['Month', 'Closing_Qty', 'Outwards_Qty']].to_dict(orient='records'),
    }

    return render(request, 'inventory/predict_stock.html', context)


class ShowProductData(AccountantRequiredMixin, ListView):
    template_name = 'inventory/show_product_data.html'
    context_object_name = 'historical_data'

    def get_queryset(self):
        product_id = self.kwargs['pk']
        sort_field = self.request.GET.get('sort', 'date_desc')

        allowed_value_fields = [
            'inwards_quantity', '-inwards_quantity',
            'inwards_value', '-inwards_value',
            'outwards_quantity', '-outwards_quantity',
            'outwards_value', '-outwards_value',
            'closing_quantity', '-closing_quantity',
            'closing_value', '-closing_value',
        ]

        # Set ordering based on sort param
        if sort_field == 'date_asc':
            ordering = ['year', 'month']
        elif sort_field == 'date_desc':
            ordering = ['-year', '-month']
        elif sort_field in allowed_value_fields:
            ordering = [sort_field, '-year', '-month']
        else:
            ordering = ['-year', '-month']  # fallback

        return MonthlyStockData.objects.filter(product_id=product_id).order_by(*ordering)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['product'] = get_object_or_404(InventoryItem, pk=self.kwargs['pk'])
        context['sort'] = self.request.GET.get('sort', 'date_desc')
        return context

class ShowProductStockHistory(AccountantRequiredMixin, ListView):      # for date model
    template_name='inventory/product_stock_history.html'
    context_object_name = 'stock_data'

    def get_queryset(self):
        product_id = self.kwargs['pk']
        sort_field = self.request.GET.get('sort', 'date')

        return DailyStockData.objects.filter(product_id=product_id).order_by(sort_field)

    def get_context_data(self, *, object_list = ..., **kwargs):
        context = super().get_context_data(**kwargs)
        context['product'] = get_object_or_404(InventoryItem, pk=self.kwargs['pk'])
        return context



def stock_chart_view_2(request, pk):
    Monthly_Stock_Data=MonthlyStockData.objects.filter(product_id=pk)
    Month=[]
    Inwards_Qty=[]
    Inwards_Value=[]
    Outwards_Qty=[]
    Outwards_Value=[]
    Closing_Qty=[]
    Closing_Value=[]
    for data in Monthly_Stock_Data[::-1]:
        print("I am here",data)
        Month.append(calendar.month_name[data.month]+" "+str(data.year))
        if data.inwards_quantity:
            Inwards_Qty.append(data.inwards_quantity)
        else:
            Inwards_Qty.append(0)
        if data.inwards_value:
            Inwards_Value.append(data.inwards_value)
        else:
            Inwards_Value.append(0)
        if data.outwards_quantity:
            Outwards_Qty.append(data.outwards_quantity)
        else:
            Outwards_Qty.append(0)
        if data.outwards_value:
            Outwards_Value.append(data.outwards_value)
        else:
            Outwards_Value.append(0)
        Closing_Qty.append(data.closing_quantity)
        Closing_Value.append(data.closing_value)


    # Prepare JSON data for Chart.js
    chart_data = {
        'labels': Month,
        'inwards_qty': Inwards_Qty,
        'outwards_qty': Outwards_Qty,
        'inwards_value': Inwards_Value,
        'outwards_value': Outwards_Value,
        'closing_qty': Closing_Qty,
        'closing_value': Closing_Value
    }

    return render(request, 'inventory/chartjs_stock.html', {
        'chart_data': json.dumps(chart_data, cls=DjangoJSONEncoder)
    })

def stock_chart_view_3(request, pk):
    Daily_Stock_Data=DailyStockData.objects.filter(product_id=pk)
    Dates=[]
    Inwards_Qty=[]
    Inwards_Value=[]
    Outwards_Qty=[]
    Outwards_Value=[]
    Closing_Qty=[]
    Closing_Value=[]

    for data in Daily_Stock_Data[::-1]:
        print("I am here", data)
        Dates.append(data.date)
        if data.inwards_quantity:
            Inwards_Qty.append(data.inwards_quantity)
        else:
            Inwards_Qty.append(0)
        if data.inwards_value:
            Inwards_Value.append(data.inwards_value)
        else:
            Inwards_Value.append(0)
        if data.outwards_quantity:
            Outwards_Qty.append(data.outwards_quantity)
        else:
            Outwards_Qty.append(0)
        if data.outwards_value:
            Outwards_Value.append(data.outwards_value)
        else:
            Outwards_Value.append(0)
        Closing_Qty.append(data.closing_quantity)
        Closing_Value.append(data.closing_value)

    # Prepare JSON data for Chart.js
    chart_data = {
        'labels': Dates,
        'inwards_qty': Inwards_Qty,
        'outwards_qty': Outwards_Qty,
        'inwards_value': Inwards_Value,
        'outwards_value': Outwards_Value,
        'closing_qty': Closing_Qty,
        'closing_value': Closing_Value
    }

    return render(request, 'inventory/chartjs_stock.html', {
        'chart_data': json.dumps(chart_data, cls=DjangoJSONEncoder)
    })
def predict_min_stock_2(request, pk):
    Monthly_Stock_Data=MonthlyStockData.objects.filter(product_id=pk)
    Month=[]
    Inwards_Qty=[]
    Inwards_Value=[]
    Outwards_Qty=[]
    Outwards_Value=[]
    Closing_Qty=[]
    Closing_Value=[]
    print(Monthly_Stock_Data)
    for data in Monthly_Stock_Data[::-1]:
        Month.append(calendar.month_name[data.month]+" "+str(data.year))
        if data.inwards_quantity:
            Inwards_Qty.append(data.inwards_quantity)
        else:
            Inwards_Qty.append(0)
        if data.inwards_value:
            Inwards_Value.append(data.inwards_value)
        else:
            Inwards_Value.append(0)
        if data.outwards_quantity:
            Outwards_Qty.append(data.outwards_quantity)
        else:
            Outwards_Qty.append(0)
        if data.outwards_value:
            Outwards_Value.append(data.outwards_value)
        else:
            Outwards_Value.append(0)
        Closing_Qty.append(data.closing_quantity)
        Closing_Value.append(data.closing_value)

    # Build DataFrame from lists
    data = {
        'Month': Month,
        'Inwards_Qty': Inwards_Qty,
        'Inwards_Value': Inwards_Value,
        'Outwards_Qty': Outwards_Qty,
        'Outwards_Value': Outwards_Value,
        'Closing_Qty': Closing_Qty,
        'Closing_Value': Closing_Value
    }
    df = pd.DataFrame(data)

    df.reset_index(drop=True, inplace=True)
    df['MonthIndex'] = np.arange(len(df)).reshape(-1, 1)

    # Trend-based prediction using Closing_Qty
    model_trend = LinearRegression()
    model_trend.fit(df[['MonthIndex']], df['Closing_Qty'])
    future_months = np.array([len(df), len(df) + 1, len(df) + 2]).reshape(-1, 1)
    trend_predictions = model_trend.predict(future_months)

    # Demand-based prediction using Outwards_Qty
    model_demand = LinearRegression()
    model_demand.fit(df[['MonthIndex']], df['Outwards_Qty'])
    demand_predictions = model_demand.predict(future_months)

    # Calculate minimum stock suggestions
    min_stock_trend = int(min(trend_predictions))
    min_stock_demand = int(max(demand_predictions))
    min_stock_demand_buffered = int(min_stock_demand * 1.1)

    future_labels = ['Month +' + str(i + 1) for i in range(3)]
    trend_pred = list(zip(future_labels, [int(x) for x in trend_predictions]))
    demand_pred = list(zip(future_labels, [int(x) for x in demand_predictions]))

    context = {
        'trend_pred': trend_pred,
        'demand_pred': demand_pred,
        'min_stock_trend': min_stock_trend,
        'min_stock_demand': min_stock_demand_buffered,
        'excel_data': df[['Month', 'Closing_Qty', 'Outwards_Qty']].to_dict(orient='records'),
    }

    return render(request, 'inventory/predict_stock.html', context)

@login_required
def predict_min_stock_from_daily(request, pk):
    product = get_object_or_404(InventoryItem, pk=pk)

    # Query and annotate monthly summaries
    daily_data = DailyStockData.objects.filter(product_id=pk)

    # Convert QuerySet to DataFrame
    df = pd.DataFrame.from_records(
        daily_data.values('date', 'inwards_quantity', 'inwards_value', 'outwards_quantity', 'outwards_value', 'closing_quantity', 'closing_value')
    )

    if df.empty:
        return render(request, 'inventory/predict_stock.html', {
            'error': 'No stock data available for this product.'
        })

    # Convert to datetime
    df['date'] = pd.to_datetime(df['date'])

    # Add month & year columns
    df['month'] = df['date'].dt.month
    df['year'] = df['date'].dt.year

    # Group by month & year
    monthly_summary = df.groupby(['year', 'month']).agg({
        'inwards_quantity': 'sum',
        'inwards_value': 'sum',
        'outwards_quantity': 'sum',
        'outwards_value': 'sum',
        'closing_quantity': 'last',  # Get last available closing
        'closing_value': 'last',
    }).reset_index()

    # Month label
    monthly_summary['Month'] = monthly_summary.apply(
        lambda row: f"{calendar.month_name[int(row['month'])]} {int(row['year'])}",
        axis=1
    )

    # Reset index for ML
    monthly_summary.reset_index(drop=True, inplace=True)
    monthly_summary['MonthIndex'] = np.arange(len(monthly_summary)).reshape(-1, 1)

    # Trend-based prediction using Closing_Qty
    model_trend = LinearRegression()
    model_trend.fit(monthly_summary[['MonthIndex']], monthly_summary['closing_quantity'])
    future_months = np.array([len(monthly_summary), len(monthly_summary)+1, len(monthly_summary)+2]).reshape(-1, 1)
    trend_predictions = model_trend.predict(future_months)

    # Demand-based prediction using Outwards_Qty
    model_demand = LinearRegression()
    model_demand.fit(monthly_summary[['MonthIndex']], monthly_summary['outwards_quantity'])
    demand_predictions = model_demand.predict(future_months)

    # Suggested min stock
    min_stock_trend = int(min(trend_predictions))
    min_stock_demand = int(max(demand_predictions))
    min_stock_demand_buffered = int(min_stock_demand * 1.1)

    # Labels
    future_labels = ['Month +' + str(i + 1) for i in range(3)]
    trend_pred = list(zip(future_labels, [int(x) for x in trend_predictions]))
    demand_pred = list(zip(future_labels, [int(x) for x in demand_predictions]))

    context = {
        'product': product,
        'trend_pred': trend_pred,
        'demand_pred': demand_pred,
        'min_stock_trend': min_stock_trend,
        'min_stock_demand': min_stock_demand_buffered,
        'excel_data': monthly_summary[['Month', 'closing_quantity', 'outwards_quantity']].to_dict(orient='records'),
    }

    return render(request, 'inventory/predict_stock_daily.html', context)


class PredictMinStockView(AccountantRequiredMixin,TemplateView):
    template_name = "inventory/predict_stock_daily.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        product = get_object_or_404(InventoryItem, pk=pk)

        daily_data = DailyStockData.objects.filter(product_id=pk)

        df = pd.DataFrame.from_records(
            daily_data.values(
                "date",
                "inwards_quantity",
                "inwards_value",
                "outwards_quantity",
                "outwards_value",
                "closing_quantity",
                "closing_value"
            )
        )

        if df.empty:
            context["error"] = "No stock data available for this product."
            return context

        # Date parsing and monthly grouping
        df["date"] = pd.to_datetime(df["date"])
        df["month"] = df["date"].dt.month
        df["year"] = df["date"].dt.year

        monthly_summary = df.groupby(["year", "month"]).agg({
            "inwards_quantity": "sum",
            "inwards_value": "sum",
            "outwards_quantity": "sum",
            "outwards_value": "sum",
            "closing_quantity": "last",
            "closing_value": "last",
        }).reset_index()

        monthly_summary["Month"] = monthly_summary.apply(
            lambda row: f"{calendar.month_name[int(row['month'])]} {int(row['year'])}",
            axis=1
        )

        monthly_summary.reset_index(drop=True, inplace=True)
        monthly_summary["MonthIndex"] = np.arange(len(monthly_summary)).reshape(-1, 1)

        # Trend-based prediction
        model_trend = LinearRegression()
        model_trend.fit(monthly_summary[["MonthIndex"]], monthly_summary["closing_quantity"])
        future_months = np.array([len(monthly_summary), len(monthly_summary)+1, len(monthly_summary)+2]).reshape(-1, 1)
        trend_predictions = model_trend.predict(future_months)

        # Demand-based prediction
        model_demand = LinearRegression()
        model_demand.fit(monthly_summary[["MonthIndex"]], monthly_summary["outwards_quantity"])
        demand_predictions = model_demand.predict(future_months)

        min_stock_trend = int(min(trend_predictions))
        min_stock_demand = int(max(demand_predictions))
        min_stock_demand_buffered = int(min_stock_demand * 1.1)

        # Generate month names for future predictions
        last_year = int(monthly_summary.iloc[-1]["year"])
        last_month = int(monthly_summary.iloc[-1]["month"])
        last_date = datetime.date(last_year, last_month, 1)
        future_dates = [last_date + relativedelta(months=i+1) for i in range(3)]
        future_labels = [f"{calendar.month_name[d.month]} {d.year}" for d in future_dates]

        trend_pred = list(zip(future_labels, [int(x) for x in trend_predictions]))
        demand_pred = list(zip(future_labels, [int(x) for x in demand_predictions]))

        context.update({
            "product": product,
            "trend_pred": trend_pred,
            "demand_pred": demand_pred,
            "min_stock_trend": min_stock_trend,
            "min_stock_demand": min_stock_demand_buffered,
            "excel_data": monthly_summary[["Month", "closing_quantity", "outwards_quantity"]].to_dict(orient="records"),
        })

        return context

def search_items(request):
    query = request.GET.get('item')
    payload = []

    if query:
        items = InventoryItem.objects.filter(
            Q(name__icontains=query) | Q(category__name__icontains=query)
        )
        for item in items:
            payload.append([item.name, item.id])

    return JsonResponse({'status': 200, 'data': payload})


class InventoryReportView(AccountantRequiredMixin,TemplateView):
    template_name = 'inventory/inventory_report.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get all categories with their related products
        categories = Category.objects.prefetch_related('inventoryitem_set').all()

        context['categories'] = categories
        return context



class MonthlyStockChartView(AccountantRequiredMixin,TemplateView):
    template_name = "inventory/chartjs_stock_month.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        product = InventoryItem.objects.get(pk=pk)

        # Group stock data by month
        qs = (
            DailyStockData.objects
            .filter(product=product)
            .annotate(month=TruncMonth('date'))
            .values('month')
            .annotate(
                inwards_qty=Sum('inwards_quantity'),
                outwards_qty=Sum('outwards_quantity'),
                inwards_value=Sum('inwards_value'),
                outwards_value=Sum('outwards_value'),
                closing_qty=Sum('closing_quantity'),
                closing_value=Sum('closing_value'),
            )
            .order_by('month')
        )

        # Prepare data for Chart.js
        labels = []
        inwards_qty, outwards_qty = [], []
        inwards_value, outwards_value = [], []
        closing_qty, closing_value = [], []

        for entry in qs:
            labels.append(entry["month"].strftime("%Y-%m"))
            inwards_qty.append(entry["inwards_qty"] or 0)
            outwards_qty.append(entry["outwards_qty"] or 0)
            inwards_value.append(entry["inwards_value"] or 0)
            outwards_value.append(entry["outwards_value"] or 0)
            closing_qty.append(entry["closing_qty"] or 0)
            closing_value.append(entry["closing_value"] or 0)

        chart_data = {
            "labels": labels,
            "inwards_qty": inwards_qty,
            "outwards_qty": outwards_qty,
            "inwards_value": inwards_value,
            "outwards_value": outwards_value,
            "closing_qty": closing_qty,
            "closing_value": closing_value,
        }

        context["product"] = product
        context["chart_data"] = json.dumps(chart_data, cls=DjangoJSONEncoder)
        return context



class LowStockReportView(TemplateView):
    template_name = "inventory/low_stock_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categories = Category.objects.prefetch_related('inventoryitem_set').all()
        context['categories'] = categories
        return context



class DailyStockChartView(AccountantRequiredMixin, TemplateView):
    template_name = "inventory/chartjs_stock_day.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        product = get_object_or_404(InventoryItem, pk=pk)

        qs = DailyStockData.objects.filter(product=product).order_by("date")

        labels, inwards_qty, outwards_qty = [], [], []
        inwards_value, outwards_value = [], []
        closing_qty, closing_value = [], []

        for entry in qs:
            labels.append(entry.date.strftime("%Y-%m-%d"))
            inwards_qty.append(entry.inwards_quantity or 0)
            outwards_qty.append(entry.outwards_quantity or 0)
            inwards_value.append(entry.inwards_value or 0)
            outwards_value.append(entry.outwards_value or 0)
            closing_qty.append(entry.closing_quantity or 0)
            closing_value.append(entry.closing_value or 0)

        chart_data = {
            "labels": labels,
            "inwards_qty": inwards_qty,
            "outwards_qty": outwards_qty,
            "inwards_value": inwards_value,
            "outwards_value": outwards_value,
            "closing_qty": closing_qty,
            "closing_value": closing_value,
        }

        context["product"] = product
        context["chart_data"] = json.dumps(chart_data, cls=DjangoJSONEncoder)
        return context


class DeadStockDashboardView(AccountantRequiredMixin, TemplateView):
    template_name = "inventory/dead_stock_dashboard.html"

    def get(self, request):

        # -------------------------
        # DATE FILTER
        # -------------------------
        from_date = request.GET.get('from')
        to_date = request.GET.get('to')

        if not from_date or not to_date:
            to_date = datetime.date.today()
            from_date = to_date.replace(month=max(1, to_date.month - 3))
        else:
            from_date = datetime.datetime.strptime(from_date, "%Y-%m-%d").date()
            to_date = datetime.datetime.strptime(to_date, "%Y-%m-%d").date()

        # -------------------------
        # SOLD PRODUCTS
        # -------------------------
        sold_products = DailyStockData.objects.filter(
            date__range=(from_date, to_date),
            outwards_quantity__gt=0
        ).values_list("product_id", flat=True).distinct()

        dead_stock_items = InventoryItem.objects.exclude(id__in=sold_products).select_related("category")
        # dead_stock_items = InventoryItem.objects.exclude(id__in=sold_products)


        # -------------------------
        # LAST SOLD DATE
        # -------------------------
        last_sales = DailyStockData.objects.filter(
            outwards_quantity__gt=0
        ).values("product_id").annotate(last_sold=Max("date"))

        last_sold_map = {x['product_id']: x['last_sold'] for x in last_sales}

        # -------------------------
        # LAST CUSTOMER (TAX INVOICE)
        # -------------------------
        last_customers_raw = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice"
            )
            .values(
                "item_id",
                "item_name_text",
                "voucher__party_name",
                "voucher__date",
                "voucher_id"
            )
            .order_by("-voucher__date")
        )

        last_customer_map = {}
        last_customer_vid_map = {}

        for row in last_customers_raw:
            if row["item_id"]:
                if row["item_id"] not in last_customer_map:
                    last_customer_map[row["item_id"]] = row["voucher__party_name"]
                    last_customer_vid_map[row["item_id"]] = row["voucher_id"]

            elif row["item_name_text"]:
                try:
                    item = InventoryItem.objects.get(name__iexact=row["item_name_text"].strip())
                    if item.id not in last_customer_map:
                        last_customer_map[item.id] = row["voucher__party_name"]
                        last_customer_vid_map[item.id] = row["voucher_id"]
                except InventoryItem.DoesNotExist:
                    pass

        # -------------------------
        # ✅ CATEGORY WISE GROUPING
        # -------------------------
        category_data = defaultdict(list)

        # flat list for original summary card / templates that expect `dead_stock`
        data = []
        total_dead_value = 0  # placeholder; keep as 0 unless you want actual valuation


        for item in dead_stock_items:
            if not item.quantity or item.quantity <= 0:
                continue

            last_sold = last_sold_map.get(item.id)
            last_customer = last_customer_map.get(item.id)
            voucher_id = last_customer_vid_map.get(item.id)

            customer_link = reverse("voucher_detail", args=[voucher_id]) if voucher_id else None
            category_name = item.category.name if item.category else "Uncategorized"

            entry = {
                "name": item.name,
                "quantity": item.quantity,
                "last_sold": last_sold,
                "last_customer": last_customer,
                "customer_link": customer_link,
            }

            # add to flat list and category grouping
            data.append(entry)
            category_data[category_name].append(entry)

            # optionally compute value if you have a cost field, e.g. item.cost_price
            # if getattr(item, "cost_price", None):
            #     total_dead_value += (item.cost_price or 0) * item.quantity

        context = {
            "dead_stock": data,                        # preserves previous template variable
            "category_data": dict(category_data),      # new accordion data
            "from_date": from_date,
            "to_date": to_date,
            "total_dead_value": round(total_dead_value, 2),
            "total_dead_products": len(data),
        }

        return render(request, self.template_name, context)


class SalesComparisonDashboardView(AccountantRequiredMixin, View):
    template_name = "inventory/sales_comparison_dashboard.html"

    def get(self, request):

        from_date_str = request.GET.get("from")
        to_date_str = request.GET.get("to")

        if not from_date_str or not to_date_str:
            to_date = datetime.date.today()
            from_date = to_date - timedelta(days=30)
        else:
            from_date = datetime.datetime.strptime(from_date_str, "%Y-%m-%d").date()
            to_date = datetime.datetime.strptime(to_date_str, "%Y-%m-%d").date()

        days_diff = (to_date - from_date).days
        prev_from = from_date - timedelta(days=days_diff)
        prev_to = from_date - timedelta(days=1)

        # ✅ Fetch current & previous sales grouped by product and category
        current_sales = (
            DailyStockData.objects
            .filter(date__range=[from_date, to_date])
            .values("product__name", "product__category__name")
            .annotate(total_sold=Sum("outwards_quantity"))
        )

        previous_sales = (
            DailyStockData.objects
            .filter(date__range=[prev_from, prev_to])
            .values("product__name", "product__category__name")
            .annotate(total_sold=Sum("outwards_quantity"))
        )

        prev_dict = {p["product__name"]: p["total_sold"] for p in previous_sales}

        # ✅ Group by category
        category_data = {}
        comparison = []   # <<<<<<<<<<<< NEW LIST

        for item in current_sales:
            cat = item["product__category__name"] or "Uncategorized"
            name = item["product__name"]
            curr = item["total_sold"] or 0
            prev = prev_dict.get(name, 0)
            diff = curr - prev
            percent_change = ((curr - prev) / prev * 100) if prev > 0 else None

            product_data = {
                "name": name,
                "current": curr,
                "previous": prev,
                "difference": diff,
                "percent_change": round(percent_change, 2) if percent_change else "N/A",
                "trend": "down" if diff < 0 else "up"
            }

            # Add to category
            category_data.setdefault(cat, []).append(product_data)

            # Add to global comparison list
            comparison.append(product_data)  # <<<<<<<<<< NEW

        return render(request, self.template_name, {
            "category_data": category_data,
            "comparison": comparison,  # <<<<<<<<<< PASS TO TEMPLATE
            "from_date": from_date,
            "to_date": to_date,
        })

        return render(request, self.template_name, {
            "category_data": category_data,
            "from_date": from_date,
            "to_date": to_date,
        })




def get_inventory_by_category(request):
    category_id = request.GET.get("category_id")
    items = InventoryItem.objects.filter(category_id=category_id).values("id", "name")
    return JsonResponse({"products": list(items)})


# it is getting all data from tally vouchers for time being we have removed all use of dailystcokdata model
class DeadStockDashboardView(AccountantRequiredMixin, TemplateView):
    template_name = "inventory/dead_stock_dashboard2.html"

    def get(self, request):

        # -------------------------
        # DATE FILTER
        # -------------------------
        from_date = request.GET.get('from')
        to_date = request.GET.get('to')

        if not from_date or not to_date:
            to_date = datetime.date.today()
            from_date = to_date.replace(month=max(1, to_date.month - 3))
        else:
            from_date = datetime.datetime.strptime(from_date, "%Y-%m-%d").date()
            to_date = datetime.datetime.strptime(to_date, "%Y-%m-%d").date()

        # -------------------------
        # ✅ SOLD PRODUCTS (FROM TALLY TAX INVOICE)
        # -------------------------
        sold_products_set = set()

        sold_rows = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice",
                voucher__date__range=(from_date, to_date)
            )
            .values("item_id", "item_name_text")
        )

        for row in sold_rows:
            if row["item_id"]:
                sold_products_set.add(row["item_id"])

            elif row["item_name_text"]:
                try:
                    item = InventoryItem.objects.get(
                        name__iexact=row["item_name_text"].strip()
                    )
                    sold_products_set.add(item.id)
                except InventoryItem.DoesNotExist:
                    pass

        dead_stock_items = InventoryItem.objects.exclude(
            id__in=sold_products_set
        ).select_related("category")
        # dead_stock_items = InventoryItem.objects.exclude(id__in=sold_products)




        # -------------------------
        # LAST CUSTOMER (TAX INVOICE)
        # -------------------------
        last_customers_raw = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice"
            )
            .values(
                "item_id",
                "item_name_text",
                "voucher__party_name",
                "voucher__date",
                "voucher_id"
            )
            .order_by("-voucher__date")
        )

        last_customers_raw = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice"
            )
            .values(
                "item_id",
                "item_name_text",
                "voucher__party_name",
                "voucher__date",
                "voucher_id"
            )
            .order_by("-voucher__date")  # latest first
        )

        product_last_sold_map = {}
        product_customers_map = defaultdict(list)

        # -------------------------
        # ✅ CUSTOMER → SALESPERSON LOOKUP
        # -------------------------
        customer_salesperson_map = {}

        customers = Customer.objects.select_related("salesperson").all()

        for c in customers:
            if c.name:
                customer_salesperson_map[c.name.strip().lower()] = (
                    c.salesperson.name if c.salesperson else None
                )

        for row in last_customers_raw:
            product_id = None

            # --- resolve product id ---
            if row["item_id"]:
                product_id = row["item_id"]

            elif row["item_name_text"]:
                try:
                    item = InventoryItem.objects.get(
                        name__iexact=row["item_name_text"].strip()
                    )
                    product_id = item.id
                except InventoryItem.DoesNotExist:
                    continue

            if not product_id:
                continue

            customer_name = row["voucher__party_name"]
            voucher_id = row["voucher_id"]
            voucher_date = row["voucher__date"]

            # -----------------------
            # ✅ LAST SOLD DATE (latest voucher date wins automatically because ordering is DESC)
            # -----------------------
            if product_id not in product_last_sold_map:
                product_last_sold_map[product_id] = voucher_date

            # -----------------------
            # ✅ CUSTOMER LIST
            # -----------------------
            already_added = any(
                c["name"] == customer_name
                for c in product_customers_map[product_id]
            )
            if already_added:
                continue

            salesperson_name = customer_salesperson_map.get(
                customer_name.strip().lower()
            )

            product_customers_map[product_id].append({
                "name": customer_name,
                "voucher_id": voucher_id,
                "link": reverse("voucher_detail", args=[voucher_id]) if voucher_id else None,
                "salesperson": salesperson_name,  # 👈 added
            })

        # -------------------------
        # ✅ CATEGORY WISE GROUPING
        # -------------------------
        category_data = defaultdict(list)

        # flat list for original summary card / templates that expect `dead_stock`
        data = []
        total_dead_value = 0  # placeholder; keep as 0 unless you want actual valuation


        for item in dead_stock_items:
            if not item.quantity or item.quantity <= 0:
                continue

            last_sold = product_last_sold_map.get(item.id)
            customers = product_customers_map.get(item.id, [])
            category_name = item.category.name if item.category else "Uncategorized"

            entry = {
                "name": item.name,
                "quantity": item.quantity,
                "last_sold": last_sold,
                "customers": customers,  # 👈 list now
            }

            # add to flat list and category grouping
            data.append(entry)
            category_data[category_name].append(entry)

            # optionally compute value if you have a cost field, e.g. item.cost_price
            # if getattr(item, "cost_price", None):
            #     total_dead_value += (item.cost_price or 0) * item.quantity

        context = {
            "dead_stock": data,                        # preserves previous template variable
            "category_data": dict(category_data),      # new accordion data
            "from_date": from_date,
            "to_date": to_date,
            "total_dead_value": round(total_dead_value, 2),
            "total_dead_products": len(data),
        }

        return render(request, self.template_name, context)


# this dead stock view is same as above but with ability to send mail
class DeadStockDashboardView(AccountantRequiredMixin, TemplateView):
    template_name = "inventory/dead_stock_dashboard_3mail.html"

    def get(self, request):

        # -------------------------
        # DATE FILTER
        # -------------------------
        from_date = request.GET.get('from')
        to_date = request.GET.get('to')

        if not from_date or not to_date:
            to_date = datetime.date.today()
            from_date = to_date.replace(month=max(1, to_date.month - 3))
        else:
            from_date = datetime.datetime.strptime(from_date, "%Y-%m-%d").date()
            to_date = datetime.datetime.strptime(to_date, "%Y-%m-%d").date()

        # -------------------------
        # ✅ SOLD PRODUCTS (FROM TALLY TAX INVOICE)
        # -------------------------
        sold_products_set = set()

        sold_rows = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice",
                voucher__date__range=(from_date, to_date)
            )
            .values("item_id", "item_name_text")
        )

        for row in sold_rows:
            if row["item_id"]:
                sold_products_set.add(row["item_id"])

            elif row["item_name_text"]:
                try:
                    item = InventoryItem.objects.get(
                        name__iexact=row["item_name_text"].strip()
                    )
                    sold_products_set.add(item.id)
                except InventoryItem.DoesNotExist:
                    pass

        dead_stock_items = InventoryItem.objects.exclude(
            id__in=sold_products_set
        ).select_related("category")
        # dead_stock_items = InventoryItem.objects.exclude(id__in=sold_products)




        # -------------------------
        # LAST CUSTOMER (TAX INVOICE)
        # -------------------------
        last_customers_raw = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice"
            )
            .values(
                "item_id",
                "item_name_text",
                "voucher__party_name",
                "voucher__date",
                "voucher_id"
            )
            .order_by("-voucher__date")
        )

        last_customers_raw = (
            VoucherStockItem.objects.filter(
                voucher__voucher_type__iexact="Tax Invoice"
            )
            .values(
                "item_id",
                "item_name_text",
                "voucher__party_name",
                "voucher__date",
                "voucher_id"
            )
            .order_by("-voucher__date")  # latest first
        )

        product_last_sold_map = {}
        product_customers_map = defaultdict(list)

        # -------------------------
        # ✅ CUSTOMER → SALESPERSON LOOKUP
        # -------------------------
        customer_salesperson_map = {}

        customers = Customer.objects.select_related("salesperson").all()

        for c in customers:
            if c.name:
                customer_salesperson_map[c.name.strip().lower()] = {
                    "salesperson": c.salesperson.name if c.salesperson else None,
                    "email": (
                        c.salesperson.user.email
                        if c.salesperson and c.salesperson.user and c.salesperson.user.email
                        else None
                    )
                }

        for row in last_customers_raw:
            product_id = None
            product_name = None
            # --- resolve product id ---
            if row["item_id"]:
                product_id = row["item_id"]
                try:
                    product_name = InventoryItem.objects.get(id=product_id).name
                except InventoryItem.DoesNotExist:
                    product_name = None

            elif row["item_name_text"]:
                try:
                    item = InventoryItem.objects.get(
                        name__iexact=row["item_name_text"].strip()
                    )
                    product_id = item.id
                    product_name = item.name
                except InventoryItem.DoesNotExist:
                    continue

            if not product_id:
                continue

            customer_name = row["voucher__party_name"]
            voucher_id = row["voucher_id"]
            voucher_date = row["voucher__date"]

            # -----------------------
            # ✅ LAST SOLD DATE (latest voucher date wins automatically because ordering is DESC)
            # -----------------------
            if product_id not in product_last_sold_map:
                product_last_sold_map[product_id] = voucher_date

            # -----------------------
            # ✅ CUSTOMER LIST
            # -----------------------
            already_added = any(
                c["name"] == customer_name
                for c in product_customers_map[product_id]
            )
            if already_added:
                continue

            customer_info = customer_salesperson_map.get(
                customer_name.strip().lower(),
                {}
            )

            salesperson_name = customer_info.get("salesperson")
            salesperson_email = customer_info.get("email")


            voucher_link = reverse("voucher_detail", args=[voucher_id]) if voucher_id else ""

            mail_link = None
            if salesperson_email and voucher_date:
                subject = f"Dead Stock Follow-up: {row.get('item_name_text') or ''}"

                body = (
                    f"Hi {salesperson_name},\n\n"
                    f"The product '{product_name}' has not been sold since {voucher_date}.\n"
                    f"Customer '{customer_name}' had previously purchased this product.\n"
                    f"Last voucher: {'https://oblutools.com' + voucher_link if voucher_link else ''}\n\n"
                    f"Please try to reconnect with this customer to promote this product again.\n\n"
                    f"Thanks."
                )

                mail_link = (
                    f"mailto:{salesperson_email}"
                    f"?subject={quote(subject)}"
                    f"&body={quote(body)}"
                )

            product_customers_map[product_id].append({
                "name": customer_name,
                "voucher_id": voucher_id,
                "link": voucher_link,
                "salesperson": salesperson_name,
                "salesperson_email": salesperson_email,
                "mail_link": mail_link,  # ✅ THIS IS WHAT TEMPLATE NEEDS
            })

        # -------------------------
        # ✅ CATEGORY WISE GROUPING
        # -------------------------
        category_data = defaultdict(list)

        # flat list for original summary card / templates that expect `dead_stock`
        data = []
        total_dead_value = 0  # placeholder; keep as 0 unless you want actual valuation


        for item in dead_stock_items:
            if not item.quantity or item.quantity <= 0:
                continue

            last_sold = product_last_sold_map.get(item.id)
            customers = product_customers_map.get(item.id, [])
            category_name = item.category.name if item.category else "Uncategorized"

            entry = {
                "name": item.name,
                "quantity": item.quantity,
                "last_sold": last_sold,
                "customers": customers,  # 👈 list now
                "product_name": item.name,  # explicit for mail template
            }

            # add to flat list and category grouping
            data.append(entry)
            category_data[category_name].append(entry)

            # optionally compute value if you have a cost field, e.g. item.cost_price
            # if getattr(item, "cost_price", None):
            #     total_dead_value += (item.cost_price or 0) * item.quantity

        context = {
            "dead_stock": data,                        # preserves previous template variable
            "category_data": dict(category_data),      # new accordion data
            "from_date": from_date,
            "to_date": to_date,
            "total_dead_value": round(total_dead_value, 2),
            "total_dead_products": len(data),
        }

        return render(request, self.template_name, context)







import datetime
import numpy as np
from collections import defaultdict
from dateutil.relativedelta import relativedelta
from datetime import timedelta

from django.shortcuts import render
from django.http import JsonResponse
from django.views import View
from django.db.models import Sum, Q

from inventory.models import InventoryItem, Category
from tally_voucher.models import Voucher, VoucherStockItem

# ─────────────────────────────────────────────────────────────────────────────
# Mixin — replace with your actual mixin
# ─────────────────────────────────────────────────────────────────────────────
from inventory.mixins import AccountantRequiredMixin   # adjust import as needed


# ─────────────────────────────────────────────────────────────────────────────
# API: Top-5 customers for a product  (called by modal via fetch)
# ─────────────────────────────────────────────────────────────────────────────
class TopCustomersAPIView(AccountantRequiredMixin, View):
    """
    GET /inventory/purchase-order/top-customers/?item_id=<id>

    Returns JSON:
    {
      "customers": [
        {
          "name": "ABC Corp",
          "total_qty": 340,
          "monthly": [
            {"month": "2025-04", "qty": 120},
            ...
          ]
        },
        ...
      ]
    }
    """

    def get(self, request):
        item_id = request.GET.get("item_id")
        if not item_id:
            return JsonResponse({"error": "item_id required"}, status=400)

        # All TAX INVOICE rows for this item
        qs = (
            VoucherStockItem.objects
            .filter(
                voucher__voucher_type__iexact="TAX INVOICE",
                item_id=item_id,
            )
            .select_related("voucher")
            .values("voucher__party_name", "voucher__date", "quantity")
        )

        # Aggregate by customer
        customer_data = defaultdict(lambda: {"total": 0.0, "months": defaultdict(float)})
        for row in qs:
            name = row["voucher__party_name"] or "Unknown"
            qty  = float(row["quantity"] or 0)
            month_key = row["voucher__date"].strftime("%Y-%m") if row["voucher__date"] else "Unknown"
            customer_data[name]["total"]          += qty
            customer_data[name]["months"][month_key] += qty

        # Sort by total, take top 5
        top5 = sorted(customer_data.items(), key=lambda x: x[1]["total"], reverse=True)[:5]

        result = []
        for name, data in top5:
            monthly = sorted(
                [{"month": m, "qty": round(q, 2)} for m, q in data["months"].items()],
                key=lambda x: x["month"],
            )
            result.append({
                "name":      name,
                "total_qty": round(data["total"], 2),
                "monthly":   monthly,
            })

        return JsonResponse({"customers": result})


# ─────────────────────────────────────────────────────────────────────────────
# Main Purchase Order View
# ─────────────────────────────────────────────────────────────────────────────
class PurchaseOrderView(AccountantRequiredMixin, View):
    template_name = "inventory/purchase_order.html"

    # ─────────────────────────────────────────────────────────────────────────
    # Pre-load ALL voucher data once, slice it per item in the loop
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _preload_voucher_data():
        """
        Returns three dicts keyed by item_id (int):
          sales_rows   : list of {"date": date, "qty": float}   — TAX INVOICE
          po_rows      : list of {"date": date, "qty": float, "voucher_number": str, "party": str}
                         — PURCHASE ORDER vouchers
          gst_rows     : list of {"date": date, "qty": float, "voucher_number": str, "party": str}
                         — GST PURCHASE (received stock)
        """
        APRIL_2025 = datetime.date(2025, 4, 1)

        sales_map = defaultdict(list)
        po_map    = defaultdict(list)
        gst_map   = defaultdict(list)

        # ── All TAX INVOICE stock rows (from April 2025 onwards for sales calc)
        # We also need older data for 1-year growth, so pull everything and filter in Python
        for row in (
            VoucherStockItem.objects
            .filter(voucher__voucher_type__iexact="TAX INVOICE")
            .select_related("voucher")
            .values("item_id", "quantity", "voucher__date")
        ):
            iid = row["item_id"]
            if not iid:
                continue
            sales_map[iid].append({
                "date": row["voucher__date"],
                "qty":  float(row["quantity"] or 0),
            })

        # ── PURCHASE ORDER vouchers (PO we make, from Dec 2024 onward)
        for row in (
            VoucherStockItem.objects
            .filter(voucher__voucher_type__iexact="PURCHASE ORDER")
            .select_related("voucher")
            .values("item_id", "quantity", "voucher__date",
                    "voucher__voucher_number", "voucher__party_name")
        ):
            iid = row["item_id"]
            if not iid:
                continue
            po_map[iid].append({
                "date":           row["voucher__date"],
                "qty":            float(row["quantity"] or 0),
                "voucher_number": row["voucher__voucher_number"],
                "party":          row["voucher__party_name"],
            })

        # ── GST PURCHASE (received/booked into stock)
        for row in (
            VoucherStockItem.objects
            .filter(
                voucher__voucher_type__in=[
                    "GST PURCHASE", "Purchase", "gst purchase", "purchase"
                ]
            )
            .select_related("voucher")
            .values("item_id", "quantity", "voucher__date",
                    "voucher__voucher_number", "voucher__party_name")
        ):
            iid = row["item_id"]
            if not iid:
                continue
            gst_map[iid].append({
                "date":           row["voucher__date"],
                "qty":            float(row["quantity"] or 0),
                "voucher_number": row["voucher__voucher_number"],
                "party":          row["voucher__party_name"],
            })

        return sales_map, po_map, gst_map

    # ─────────────────────────────────────────────────────────────────────────
    # Monthly sales aggregation (from tally data)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _monthly_sales(sales_rows, from_date=None):
        """
        Returns sorted list of {"month": "YYYY-MM", "qty": float}
        Optional from_date to filter.
        """
        by_month = defaultdict(float)
        for row in sales_rows:
            d = row["date"]
            if not d:
                continue
            if from_date and d < from_date:
                continue
            by_month[d.strftime("%Y-%m")] += row["qty"]
        return [{"month": m, "qty": q} for m, q in sorted(by_month.items())]

    # ─────────────────────────────────────────────────────────────────────────
    # Average daily sales (tally data, from April 2025)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _avg_daily_from_tally(sales_rows):
        """
        Avg daily = total qty sold since April 2025 / number of days since April 2025.
        """
        APRIL_2025 = datetime.date(2025, 4, 1)
        today      = datetime.date.today()
        total_qty  = sum(
            row["qty"] for row in sales_rows
            if row["date"] and row["date"] >= APRIL_2025
        )
        days_elapsed = (today - APRIL_2025).days or 1
        return round(total_qty / days_elapsed, 4)

    # ─────────────────────────────────────────────────────────────────────────
    # Growth windows
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _growth_windows(sales_rows):
        """
        growth_1y : last 12 months total vs previous 12 months total (all tally data)
        growth_3m : last  3 months total vs previous  3 months total (tally data)

        Returns (growth_1y, growth_3m) as % floats or None.
        """
        if not sales_rows:
            return None, None

        today   = datetime.date.today()
        by_month = defaultdict(float)
        for row in sales_rows:
            if row["date"]:
                by_month[row["date"].strftime("%Y-%m")] += row["qty"]

        def _sum_window(offset_start, count):
            total = 0.0
            for i in range(offset_start, offset_start + count):
                key = (today.replace(day=1) - relativedelta(months=i)).strftime("%Y-%m")
                total += by_month.get(key, 0)
            return total

        r1y = _sum_window(0,  12);  p1y = _sum_window(12, 12)
        r3m = _sum_window(0,   3);  p3m = _sum_window(3,   3)

        growth_1y = round((r1y - p1y) / p1y * 100, 1) if p1y else None
        growth_3m = round((r3m - p3m) / p3m * 100, 1) if p3m else None
        return growth_1y, growth_3m

    # ─────────────────────────────────────────────────────────────────────────
    # Sales forecast (growth-adjusted)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _forecast_next_3_months(avg_daily, growth_1y, growth_3m, is_dead):
        """
        Blended monthly growth = average of growth_1y/12 (monthly equiv) and growth_3m/3.
        Apply compounding to avg_daily → project M+1, M+2, M+3 monthly totals.
        Returns (pred_m1, pred_m2, pred_m3) or (None, None, None).
        """
        if is_dead or avg_daily <= 0:
            return None, None, None

        # Convert annual / 3-month growth to per-month growth rates
        rates = []
        if growth_1y is not None:
            rates.append(growth_1y / 12 / 100)   # monthly equiv of yearly growth
        if growth_3m is not None:
            rates.append(growth_3m / 3 / 100)    # monthly equiv of 3-month growth

        if not rates:
            # No growth data — flat forecast
            base = round(avg_daily * 30)
            return base, base, base

        monthly_growth = sum(rates) / len(rates)  # blended rate

        base_monthly = avg_daily * 30
        m1 = max(0, round(base_monthly * (1 + monthly_growth)))
        m2 = max(0, round(base_monthly * (1 + monthly_growth) ** 2))
        m3 = max(0, round(base_monthly * (1 + monthly_growth) ** 3))
        return m1, m2, m3

    # ─────────────────────────────────────────────────────────────────────────
    # Order calculation
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_order(
        current_stock,
        avg_daily,
        delivery_days,
        incoming_qty,          # from open PO (purchase order voucher)
        incoming_date,         # expected arrival date of that PO
        pred_m1, pred_m2, pred_m3,
        monthly_growth_rate,   # decimal e.g. 0.03 for 3%
        moq,
        is_dead,
    ):
        """
        ALGORITHM
        ─────────
        Inputs:
          • current_stock
          • incoming_qty / incoming_date  (open PO, if any within delivery window)
          • avg_daily                     (from tally, April 2025 onwards)
          • monthly_growth_rate           (blended from 1y + 3m growth)
          • delivery_days                 (lead time for this product)
          • pred_m1/m2/m3                 (growth-adjusted monthly forecast)
          • 20% buffer stock (hardcoded)
          • moq

        Steps:
          1. Project daily consumption with growth for each future day.
          2. Stack current_stock + incoming_qty (if arriving before new batch).
          3. Find the day stock (with buffer) runs out → "runway".
          4. Reorder point = runway_day − delivery_days.
             If reorder_point <= 0 → order NOW.
          5. Calculate qty needed to cover 3 months from the reorder-arrival date,
             adjusted for growth, plus 20% buffer, minus incoming (if arriving after).
          6. Round up to MOQ if needed.

        Returns a dict with all intermediate values for template rendering + graphing.
        """
        BUFFER = 1.20   # 20% safety buffer

        today = datetime.date.today()

        if is_dead:
            return {
                "is_dead":           True,
                "order_recommended": 0,
                "order_final":       0,
                "order_urgency":     "dead",
                "moq_note":          None,
                "order_lasts_months": None,
                "runway_days":       None,
                "reorder_point_days": None,
                "graph_data":        [],
                "calc_steps":        {"note": "Product is dead stock — no order recommended."},
            }

        # ── Step 1: daily growth-adjusted demand projection (180 days horizon)
        horizon = 180
        daily_demand = []
        for day in range(horizon):
            month_offset = day // 30
            rate = (1 + monthly_growth_rate) ** month_offset
            daily_demand.append(avg_daily * rate)

        # ── Step 2: determine when incoming PO arrives relative to today
        incoming_arrives_in = None   # days from today
        if incoming_qty > 0 and incoming_date:
            incoming_arrives_in = max(0, (incoming_date - today).days)

        # ── Step 3: simulate stock level day-by-day to find runway
        stock = float(current_stock)
        runway_days = None

        graph_data = []   # for chart: day → {stock, demand_per_day}
        incoming_added = False

        for day in range(horizon):
            # Add incoming stock on its arrival day
            if (
                not incoming_added and
                incoming_arrives_in is not None and
                day >= incoming_arrives_in
            ):
                stock += incoming_qty
                incoming_added = True

            demand = daily_demand[day]
            stock -= demand
            buffer_threshold = demand * 30 * 3 * BUFFER  # 3-month buffered demand

            graph_data.append({
                "day":         day,
                "stock":       round(max(0, stock), 1),
                "buffer_line": round(buffer_threshold, 1),
                "demand":      round(demand, 2),
            })

            # Runway = first day stock goes below 0 (without buffer first)
            if runway_days is None and stock <= 0:
                runway_days = day
                break

        if runway_days is None:
            runway_days = horizon  # Stock lasts beyond horizon

        # ── Step 4: reorder point (days from today)
        reorder_point_days = runway_days - delivery_days

        # ── Step 5: should we order now?
        order_now = reorder_point_days <= 0

        # Arrival date of NEW batch if ordered today
        new_batch_arrival_day = delivery_days

        # Stock on hand when new batch would arrive (simulate without new order)
        stock_at_arrival = float(current_stock)
        for d in range(new_batch_arrival_day):
            if (
                incoming_arrives_in is not None and
                d == incoming_arrives_in and
                incoming_qty > 0
            ):
                stock_at_arrival += incoming_qty
            stock_at_arrival -= daily_demand[d] if d < len(daily_demand) else avg_daily
        stock_at_arrival = max(0, stock_at_arrival)

        # Demand for 3 months AFTER new batch arrives (with buffer)
        demand_3m_after = 0.0
        if pred_m1 is not None:
            demand_3m_after = (pred_m1 + pred_m2 + pred_m3) * BUFFER
            demand_source = f"forecast {pred_m1}+{pred_m2}+{pred_m3} × 1.20 buffer"
        else:
            demand_3m_after = avg_daily * 90 * BUFFER
            demand_source = f"flat avg {round(avg_daily,2)}/day × 90 × 1.20 buffer"

        demand_3m_after = round(demand_3m_after)

        # Incoming that arrives AFTER new batch (still helps)
        incoming_after_new_batch = 0
        if (
            incoming_qty > 0 and
            incoming_arrives_in is not None and
            incoming_arrives_in > new_batch_arrival_day and
            not incoming_added
        ):
            incoming_after_new_batch = incoming_qty

        shortfall = max(0, demand_3m_after - stock_at_arrival - incoming_after_new_batch)

        # ── Step 6: urgency
        if not order_now and shortfall == 0:
            order_recommended = 0
            order_urgency     = "ok"
        elif shortfall == 0:
            order_recommended = 0
            order_urgency     = "ok"
        else:
            order_recommended = shortfall
            order_urgency     = "urgent" if reorder_point_days <= 0 else "warn"

        # ── MOQ check
        moq_note    = None
        order_final = order_recommended
        if moq and order_recommended > 0 and order_recommended < moq:
            moq_note    = moq
            order_final = moq   # round up to MOQ

        # ── How long will order last (months)
        order_lasts_months = None
        if avg_daily > 0 and order_final > 0:
            total_after = stock_at_arrival + order_final + incoming_after_new_batch
            order_lasts_months = round(total_after / (avg_daily * 30), 1)

        # ── Runway in months (current trajectory)
        runway_months = round(runway_days / 30, 1) if runway_days < horizon else None

        calc_steps = {
            "current_stock":          current_stock,
            "avg_daily":              avg_daily,
            "delivery_days":          delivery_days,
            "monthly_growth_rate_pct": round(monthly_growth_rate * 100, 2),
            "incoming_qty":           incoming_qty,
            "incoming_arrives_in":    incoming_arrives_in,
            "incoming_date":          incoming_date,
            "stock_at_arrival":       stock_at_arrival,
            "demand_3m_after":        demand_3m_after,
            "demand_source":          demand_source,
            "shortfall":              shortfall,
            "order_urgency":          order_urgency,
            "moq":                    moq,
            "moq_note":               moq_note,
            "order_final":            order_final,
            "order_lasts_months":     order_lasts_months,
            "runway_days":            runway_days,
            "runway_months":          runway_months,
            "reorder_point_days":     reorder_point_days,
            "order_now":              order_now,
            "pred_m1":                pred_m1,
            "pred_m2":                pred_m2,
            "pred_m3":                pred_m3,
            "buffer_pct":             20,
            "incoming_after_new_batch": incoming_after_new_batch,
        }

        return {
            "is_dead":            False,
            "order_recommended":  order_recommended,
            "order_final":        order_final,
            "order_urgency":      order_urgency,
            "moq_note":           moq_note,
            "order_lasts_months": order_lasts_months,
            "runway_days":        runway_days,
            "runway_months":      runway_months,
            "reorder_point_days": reorder_point_days,
            "graph_data":         graph_data[:90],  # send 90 days to template
            "calc_steps":         calc_steps,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # GET
    # ─────────────────────────────────────────────────────────────────────────

    def get(self, request):
        today            = datetime.date.today()
        ninety_days_ago  = today - timedelta(days=90)
        APRIL_2025       = datetime.date(2025, 4, 1)

        categories           = Category.objects.all().order_by("name")
        selected_category_id = request.GET.get("category")
        hide_dead            = request.GET.get("hide_dead") == "1"

        if not selected_category_id:
            return render(request, self.template_name, {
                "categories": categories, "products": None,
                "selected_category_id": None,
            })

        # ── Pre-load all voucher data (one DB hit per type)
        sales_map, po_map, gst_map = PurchaseOrderView._preload_voucher_data()

        items = (
            InventoryItem.objects
            .filter(category_id=selected_category_id)
            .select_related("category")
            .order_by("name")
        )

        products_data = []

        for item in items:
            iid           = item.id
            current_stock = float(item.quantity or 0)

            # ── Tally sales rows for this item
            item_sales = sales_map.get(iid, [])

            # Dead stock = no TAX INVOICE sale in last 90 days
            is_dead = not any(
                row["date"] and row["date"] >= ninety_days_ago
                for row in item_sales
            )

            if hide_dead and is_dead:
                continue

            # ── Avg daily (tally, April 2025 onwards)
            avg_daily = PurchaseOrderView._avg_daily_from_tally(item_sales)

            # ── Growth
            growth_1y, growth_3m = PurchaseOrderView._growth_windows(item_sales)

            # Blended monthly growth rate (decimal)
            rates = []
            if growth_1y is not None:
                rates.append(growth_1y / 12 / 100)
            if growth_3m is not None:
                rates.append(growth_3m / 3 / 100)
            monthly_growth_rate = sum(rates) / len(rates) if rates else 0.0

            # ── Forecast
            pred_m1, pred_m2, pred_m3 = PurchaseOrderView._forecast_next_3_months(
                avg_daily, growth_1y, growth_3m, is_dead
            )

            # ── PO data (purchase order vouchers we made)
            item_po_rows = sorted(
                po_map.get(iid, []),
                key=lambda r: r["date"] or datetime.date.min,
                reverse=True,
            )
            latest_po   = item_po_rows[0] if item_po_rows else None

            # Incoming stock: latest PO where expected delivery is still in future
            incoming_qty  = 0.0
            incoming_date = None
            if latest_po and item.expected_delivery_days:
                expected_dt = latest_po["date"] + timedelta(days=item.expected_delivery_days)
                if expected_dt > today:
                    incoming_qty  = latest_po["qty"]
                    incoming_date = expected_dt

            # ── GST purchase data (received stock)
            item_gst_rows = sorted(
                gst_map.get(iid, []),
                key=lambda r: r["date"] or datetime.date.min,
                reverse=True,
            )
            latest_gst = item_gst_rows[0] if item_gst_rows else None

            # ── Core order calculation
            delivery_days = item.expected_delivery_days or 30
            calc = PurchaseOrderView._calc_order(
                current_stock       = current_stock,
                avg_daily           = avg_daily,
                delivery_days       = delivery_days,
                incoming_qty        = incoming_qty,
                incoming_date       = incoming_date,
                pred_m1             = pred_m1,
                pred_m2             = pred_m2,
                pred_m3             = pred_m3,
                monthly_growth_rate = monthly_growth_rate,
                moq                 = item.minimum_order_quantity,
                is_dead             = is_dead,
            )

            # ── Stock runway (months current stock lasts at flat avg)
            months_of_stock = (
                round(current_stock / (avg_daily * 30), 1)
                if avg_daily > 0 else None
            )
            is_overstocked = months_of_stock is not None and months_of_stock >= 9

            products_data.append({
                "id":                     iid,
                "name":                   item.name,
                "unit":                   item.unit or "",
                "category":               item.category.name if item.category else "—",
                "current_stock":          current_stock,
                "avg_daily":              round(avg_daily, 2),
                "growth_1y":              growth_1y,
                "growth_3m":              growth_3m,
                "monthly_growth_rate_pct": round(monthly_growth_rate * 100, 2),
                "pred_m1":                pred_m1,
                "pred_m2":                pred_m2,
                "pred_m3":                pred_m3,
                "is_dead":                is_dead,
                # PO data
                "po_rows":                item_po_rows,        # all POs (for modal)
                "latest_po_qty":          latest_po["qty"]  if latest_po else None,
                "latest_po_date":         latest_po["date"] if latest_po else None,
                "latest_po_number":       latest_po["voucher_number"] if latest_po else None,
                # GST purchase data
                "gst_rows":               item_gst_rows,       # all GST purchases (for modal)
                "latest_gst_qty":         latest_gst["qty"]  if latest_gst else None,
                "latest_gst_date":        latest_gst["date"] if latest_gst else None,
                # Transit
                "incoming_qty":           incoming_qty,
                "incoming_date":          incoming_date,
                "expected_delivery_days": item.expected_delivery_days,
                # Order calc
                "order_recommended":      calc["order_recommended"],
                "order_final":            calc["order_final"],
                "order_urgency":          calc["order_urgency"],
                "moq_note":               calc["moq_note"],
                "moq":                    item.minimum_order_quantity,
                "order_lasts_months":     calc["order_lasts_months"],
                "runway_days":            calc.get("runway_days"),
                "runway_months":          calc.get("runway_months"),
                "reorder_point_days":     calc.get("reorder_point_days"),
                "graph_data":             calc.get("graph_data", []),
                "calc_steps":             calc["calc_steps"],
                # Overstock
                "months_of_stock":        months_of_stock,
                "is_overstocked":         is_overstocked,
                "graph_data_json": json.dumps(calc.get("graph_data", [])),
                "graph_meta_json": json.dumps({
                    "reorder_point": calc["calc_steps"].get("reorder_point_days"),
                    "delivery_days": calc["calc_steps"].get("delivery_days"),
                    "incoming_arrives_in": calc["calc_steps"].get("incoming_arrives_in"),
                    "has_incoming": int(incoming_qty or 0),
                }),
            })

        return render(request, self.template_name, {
            "categories":           categories,
            "selected_category_id": int(selected_category_id),
            "products":             products_data,
            "today":                today,
            "hide_dead":            hide_dead,
        })