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

from django.urls import reverse

from inventory.mixins import AccountantRequiredMixin


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
    else:
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


class CustomerCreateView(AccountantRequiredMixin,CreateView):
    template_name = 'quotations/customer_create.html'
    form_class = CustomerCreateForm

    def get_success_url(self):
        return reverse('quotations:home')

# add discount percentage per sheet
#Quotation number field,