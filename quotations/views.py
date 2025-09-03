# quotations/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import get_template
from django.views.generic import CreateView
from xhtml2pdf import pisa
from django.forms import modelformset_factory
from django.http import JsonResponse

from .forms import QuotationForm, QuotationItemForm, CustomerCreateForm
from .models import Quotation, QuotationItem, Customer
from django.contrib.auth.decorators import login_required
import traceback
from django.views import View

from django.urls import reverse

from inventory.mixins import AccountantRequiredMixin
from django.contrib.auth.mixins import LoginRequiredMixin

from django.views.generic import ListView

# Create the modelformset for multiple product rows
QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    extra=1,
    can_delete=True
)

class CreateQuotationView(AccountantRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        quotation_form = QuotationForm(user=request.user)
        formset = QuotationItemFormSet(
            request.POST or None,
            queryset=QuotationItem.objects.none(),
            form_kwargs={"user": request.user}
        )

        customers = Customer.objects.all()

        return render(request, 'quotations/create_quotation.html', {
            'quotation_form': quotation_form,
            'formset': formset,
            'customers': customers
        })

    def post(self, request, *args, **kwargs):
        quotation_form = QuotationForm(request.POST, user=request.user)
        formset = QuotationItemFormSet(
            request.POST or None,
            queryset=QuotationItem.objects.none(),
            form_kwargs={"user": request.user}
        )

        if quotation_form.is_valid() and formset.is_valid():
            quotation = quotation_form.save(commit=False)  # ✅ don't save yet

            if not request.user.is_accountant:
                quotation.created_by = request.user.username

            quotation.save()  # ✅ now safe to save

            for form in formset:
                if form.cleaned_data and form.cleaned_data.get('product'):
                    item = form.save(commit=False)
                    item.quotation = quotation
                    item.save()
            return redirect('quotation_detail', pk=quotation.pk)

        # In case forms are not valid, re-render the form with errors
        return render(request, 'quotations/create_quotation.html', {
            'quotation_form': quotation_form,
            'formset': formset
        })

@login_required
def quotation_detail(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    return render(request, 'quotations/quotation_detail.html', {
        'quotation': quotation,
        'id': pk
    })



def home(request):
    return render(request, 'quotations/home.html')

def get_customer(request):
    customer_id = request.GET.get('id')
    try:
        customer = Customer.objects.get(id=customer_id)
        return JsonResponse({
            'name': customer.name,
            'address': customer.address,
            'state': customer.state
        })
    except Customer.DoesNotExist:
        return JsonResponse({'error': 'Customer not found'}, status=404)


class CustomerCreateView(LoginRequiredMixin,CreateView):
    template_name = 'quotations/customer_create.html'
    form_class = CustomerCreateForm

    def form_valid(self, form):
        customer = form.save(commit=False)
        customer.created_by = self.request.user  # 👈 set logged-in user
        customer.save()
        return super().form_valid(form)


    def get_success_url(self):
        return reverse('customer_list')



class CustomerListView(LoginRequiredMixin, ListView):
    model = Customer
    template_name = "quotations/customer_list.html"
    context_object_name = "customers"

    def get_queryset(self):
        user = self.request.user
        if user.groups.filter(name="Accountants").exists() or user.is_superuser:
            # Accountants and admins see all customers
            return Customer.objects.all()
        else:
            # Normal users only see their own customers
            return Customer.objects.filter(created_by=user)


class QuotationListView(LoginRequiredMixin, ListView):
    model = Quotation
    template_name = "quotations/quotations_list.html"
    context_object_name = "quotations"

    def get_queryset(self):
        user = self.request.user
        if user.groups.filter(name="Accountant").exists():
            # Accountants can see everything
            return Quotation.objects.all()
        else:
            # Normal users (viewers) see only their own
            return Quotation.objects.filter(created_by=user)