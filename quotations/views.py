# quotations/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from django.forms import modelformset_factory
from weasyprint import HTML
from .forms import QuotationForm, QuotationItemForm
from .models import Quotation, QuotationItem
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
        quotation_form = QuotationForm(request.POST)
        formset = QuotationItemFormSet(request.POST, queryset=QuotationItem.objects.none())

        if quotation_form.is_valid() and formset.is_valid():
            quotation = quotation_form.save()

            for form in formset:
                if form.cleaned_data and form.cleaned_data.get('product'):
                    item = form.save(commit=False)
                    item.quotation = quotation
                    item.save()

            return redirect('quotation_detail', pk=quotation.pk)
    else:
        quotation_form = QuotationForm()
        formset = QuotationItemFormSet(queryset=QuotationItem.objects.none())

    return render(request, 'quotations/create_quotation.html', {
        'quotation_form': quotation_form,
        'formset': formset,
    })

@login_required
def quotation_detail(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    return render(request, 'quotations/quotation_detail.html', {
        'quotation': quotation,
        'id': pk
    })

@login_required
def quotation_pdf(request, pk):
    try:
        quotation = get_object_or_404(Quotation, pk=pk)
        template = get_template('quotations/pdf_template.html')
        html = template.render({'quotation': quotation})

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename=quotation_{pk}.pdf'

        HTML(string=html, base_url=request.build_absolute_uri('/')).write_pdf(response)
        return response

    except Exception as e:
        print(f"🛑 PDF Generation Error (Quotation {pk}): {e}")
        traceback.print_exc()
        return HttpResponse("PDF generation failed", status=500)

def home(request):
    return render(request, 'quotations/home.html')


# add discount percentage per sheet
#Quotation number field,