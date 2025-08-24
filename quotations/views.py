# quotations/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from django.forms import modelformset_factory
from django.http import JsonResponse

from .forms import QuotationForm, QuotationItemForm
from .models import Quotation, QuotationItem, Customer
from django.contrib.auth.decorators import login_required
import traceback

from django.urls import reverse


# Create the modelformset for multiple product rows
QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    extra=1,
    can_delete=True
)

@login_required
def create_quotation(request):
    if request.method == 'POST':
        quotation_form = QuotationForm(request.POST,user=request.user)
        formset = QuotationItemFormSet(request.POST, queryset=QuotationItem.objects.none())

        if quotation_form.is_valid() and formset.is_valid():
            quotation = quotation_form.save()

            # if not accountant, override created_by with logged-in username
            if not request.user.is_accountant:
                quotation.created_by = request.user.username

            for form in formset:
                if form.cleaned_data and form.cleaned_data.get('product'):
                    item = form.save(commit=False)
                    item.quotation = quotation
                    item.save()
            return redirect('quotation_detail', pk=quotation.pk)
    else:
        quotation_form = QuotationForm(user=request.user)
        formset = QuotationItemFormSet(queryset=QuotationItem.objects.none())

    customers = Customer.objects.all()

    return render(request, 'quotations/create_quotation.html', {
        'quotation_form': quotation_form,
        'formset': formset,
        'customers': customers
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


# add discount percentage per sheet
#Quotation number field,