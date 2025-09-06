# quotations/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.template.loader import get_template
from django.views.generic import CreateView
from xhtml2pdf import pisa
from django.forms import modelformset_factory
from django.http import JsonResponse

from .forms import QuotationForm, QuotationItemForm, CustomerCreateForm , ProductForm, ProductPriceTierFormSet
from .models import Quotation, QuotationItem, Customer, ProductCategory, Product
from django.contrib.auth.decorators import login_required
import traceback
from django.views import View

from django.urls import reverse

from inventory.mixins import AccountantRequiredMixin
from django.contrib.auth.mixins import LoginRequiredMixin

from django.views.generic import ListView

from django.contrib import messages

# Create the modelformset for multiple product rows
QuotationItemFormSet = modelformset_factory(
    QuotationItem,
    form=QuotationItemForm,
    extra=1,
    can_delete=True
)

class CreateQuotationView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        quotation_form = QuotationForm(user=request.user)
        formset = QuotationItemFormSet(
            request.POST or None,
            queryset=QuotationItem.objects.none(),
            form_kwargs={"user": request.user}
        )

        if request.user.is_accountant:
            customers = Customer.objects.all()
        else:
            customers = Customer.objects.filter(created_by=request.user)
        categories = ProductCategory.objects.all().order_by("name")

        return render(request, 'quotations/create_quotation.html', {
            'quotation_form': quotation_form,
            'formset': formset,
            'customers': customers,
            'categories': categories,
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

        if request.user.is_accountant:
            customers = Customer.objects.all()
        else:
            customers = Customer.objects.filter(created_by=request.user)
        categories = ProductCategory.objects.all().order_by("name")

        # In case forms are not valid, re-render the form with errors
        return render(request, 'quotations/create_quotation.html', {
            'quotation_form': quotation_form,
            'formset': formset,
            'customers': customers,
            'categories': categories,
        })

def get_products_by_category(request):
    category_id = request.GET.get("category_id")
    products = []
    if category_id:
        products = Product.objects.filter(category_id=category_id).values("id", "name")

    return JsonResponse({"products": list(products)})



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
    customer_id = request.GET.get("id")
    customer = get_object_or_404(Customer, id=customer_id)

    return JsonResponse({
        "id": customer.id,
        "name": customer.name,
        "address": customer.address,
        "city": customer.city,
        "state": customer.state,
        "pincode": customer.pincode,
        "mobile": customer.mobile,
        "email": customer.email,
    })


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
        if user.is_accountant or user.is_superuser:
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
        if user.is_accountant:
            # Accountants can see everything
            qs = Quotation.objects.all()
        else:
            # Normal users (viewers) see only their own
            return Quotation.objects.filter(created_by=user)

        # Get filters from query params
        created_by = self.request.GET.get("created_by")
        customer = self.request.GET.get("customer")
        start_date = self.request.GET.get("start_date")
        end_date = self.request.GET.get("end_date")
        sort_by = self.request.GET.get("sort_by")

        if created_by:
            qs = qs.filter(created_by=created_by)

        if customer:
            qs = qs.filter(customer_name=customer)

        if start_date and end_date:
            qs = qs.filter(date_created__range=[start_date, end_date])

        # Sorting
        if sort_by == "date_desc":
            qs = qs.order_by("-date_created")
        elif sort_by == "date_asc":
            qs = qs.order_by("date_created")
        elif sort_by == "customer":
            qs = qs.order_by("customer_name")

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from django.contrib.auth import get_user_model

        User=get_user_model()

        context["users"] = User.objects.all()  # For "Created By" dropdown
        context["customers"] = (
            Quotation.objects.values_list("customer_name", flat=True).distinct()
        )
        return context




class EditProductView(AccountantRequiredMixin,View):
    def get(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        form = ProductForm(instance=product)
        tier_formset = ProductPriceTierFormSet(instance=product)
        return render(request, "quotations/edit_product.html", {
            "form": form,
            "tier_formset": tier_formset,
            "product": product,
        })

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        form = ProductForm(request.POST, instance=product)
        tier_formset = ProductPriceTierFormSet(request.POST, instance=product)

        if form.is_valid() and tier_formset.is_valid():
            if not form.has_changed() and not tier_formset.has_changed():
                messages.info(request, "No changes were made.")
                return redirect("edit_product", pk=product.pk)

            form.save()
            tier_formset.save()
            messages.success(request, f"Product '{product.name}' updated successfully.")
            return redirect("product_list")

        # Show validation errors
        messages.error(request, "There were errors in the form. Please check below.")
        return render(request, "quotations/edit_product.html", {
            "form": form,
            "tier_formset": tier_formset,
            "product": product,
        })


class ProductListView(LoginRequiredMixin, ListView):
    model = Product
    template_name = "quotations/product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        return Product.objects.all()


class CreateProductView(AccountantRequiredMixin, View):
    def get(self, request):
        form = ProductForm()
        tier_formset = ProductPriceTierFormSet()
        return render(request, "quotations/create_product.html", {
            "form": form,
            "tier_formset": tier_formset,
        })

    def post(self, request):
        form = ProductForm(request.POST)
        tier_formset = ProductPriceTierFormSet(request.POST)

        if form.is_valid() and tier_formset.is_valid():
            product = form.save(commit=False)
            product.save()
            tier_formset.instance = product
            tier_formset.save()

            messages.success(request, f"✅ Product '{product.name}' created successfully.")
            return redirect("product_list")  # or "edit_product" if you want to go there directly

        # Show errors
        messages.error(request, "❌ There were errors in the form. Please fix them below.")
        return render(request, "quotations/create_product.html", {
            "form": form,
            "tier_formset": tier_formset,
        })
