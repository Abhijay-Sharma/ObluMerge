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
from dateutil.relativedelta import relativedelta

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


class PredictMinStockView(TemplateView):
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