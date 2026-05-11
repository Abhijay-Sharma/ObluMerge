from django.shortcuts import render

# Create your views here.
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from django.views.generic import TemplateView
from django.db.models import Prefetch
from calendar import monthrange

from customer_dashboard.models import SalesPerson, Customer, CustomerVoucherStatus
from tally_voucher.models import Voucher, VoucherStockItem
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier
from django.contrib.auth.mixins import LoginRequiredMixin
from inventory.mixins import AccountantRequiredMixin
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier, IncentivePaymentStatus
from merger.settings import LOGIN_REDIRECT_URL




# Create your views here.

class IncentiveCalculatorView(TemplateView):
    template_name = 'incentive_calculator/incentive_calculator.html'



# without dynamic incentives enabled
class ASMIncentiveCalculatorView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        # No salesperson selected yet
        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS FOR THIS ASM
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)

        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS (TAX INVOICE ONLY)
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS (ONE ROW PER ITEM)
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        # --------------------------------------------------
        # BUILD ROWS + TOTALS
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            has_incentive = incentive_obj is not None
            incentive_per_unit = (
                incentive_obj.ASM_incentive if has_incentive else Decimal("0.00")
            )

            incentive_amount = (
                si.quantity * incentive_per_unit if has_incentive else Decimal("0.00")
            )

            # ---- ROW DATA ----
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            # ---- TOTALS (ONLY IF INCENTIVE EXISTS) ----
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount

                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx



class ASMIncentiveCalculatorView2(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        # No salesperson selected yet
        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS FOR THIS ASM
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS (TAX INVOICE ONLY)
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS (ONE ROW PER ITEM)
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES (UNCHANGED)
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects
            .select_related("product")
            .prefetch_related("productincentivetier_set")
        }

        # ==================================================
        # 🔹 ADDITION 1:
        # Calculate TOTAL quantity for DYNAMIC products only
        # ==================================================
        dynamic_product_qty = defaultdict(Decimal)

        for si in stock_items:
            if not si.item_id:
                continue

            incentive = incentives.get(si.item_id)
            if incentive and incentive.has_dynamic_price:
                dynamic_product_qty[si.item_id] += si.quantity

        # ==================================================
        # 🔹 ADDITION 2:
        # Resolve FINAL incentive per unit for dynamic products
        # ==================================================
        dynamic_incentive_map = {}

        for product_id, total_qty in dynamic_product_qty.items():
            incentive = incentives[product_id]

            tier = (
                incentive.productincentivetier_set
                .filter(min_quantity__lte=total_qty)
                .order_by("-min_quantity")
                .first()
            )

            if tier:
                dynamic_incentive_map[product_id] = tier.ASM_incentive
            else:
                dynamic_incentive_map[product_id] = Decimal("0.00")

        # --------------------------------------------------
        # BUILD ROWS + TOTALS (LEGACY LOGIC + SMALL CHANGE)
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            if incentive_obj:
                if incentive_obj.has_dynamic_price:
                    incentive_per_unit = dynamic_incentive_map.get(
                        product.id,
                        Decimal("0.00")
                    )
                else:
                    incentive_per_unit = incentive_obj.ASM_incentive
            else:
                incentive_per_unit = Decimal("0.00")

            has_incentive = incentive_per_unit > 0
            incentive_amount = si.quantity * incentive_per_unit

            # ---- ROW DATA ----
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            # ---- TOTALS ----
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount
                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx



class ASMIncentiveCalculatorView3(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        # No salesperson selected yet
        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS FOR THIS ASM
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS (TAX INVOICE ONLY)
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS (ONE ROW PER ITEM)
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES + TIERS
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.prefetch_related(
                "productincentivetier_set"
            )
        }

        # --------------------------------------------------
        # PASS 1: TOTAL QUANTITY PER PRODUCT
        # --------------------------------------------------
        product_quantities = defaultdict(Decimal)

        for si in stock_items:
            if si.item_id:
                product_quantities[si.item_id] += si.quantity

        # --------------------------------------------------
        # RESOLVE FINAL INCENTIVE PER UNIT (FLAT OR TIERED)
        # --------------------------------------------------
        resolved_incentives = {}

        for product_id, total_qty in product_quantities.items():
            incentive = incentives.get(product_id)

            if not incentive:
                continue

            # Flat incentive
            if not incentive.has_dynamic_price:
                resolved_incentives[product_id] = incentive.ASM_incentive
                continue

            # Tier-based incentive
            applicable_tier = (
                incentive.productincentivetier_set
                .filter(min_quantity__lte=total_qty)
                .order_by("-min_quantity")
                .first()
            )

            if applicable_tier:
                resolved_incentives[product_id] = applicable_tier.ASM_incentive
            else:
                resolved_incentives[product_id] = Decimal("0.00")

        # --------------------------------------------------
        # PASS 2: BUILD ROWS + TOTALS
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item

            incentive_per_unit = resolved_incentives.get(
                product.id if product else None,
                Decimal("0.00")
            )

            has_incentive = incentive_per_unit > 0
            incentive_amount = si.quantity * incentive_per_unit

            # ---- ROW DATA ----
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            # ---- TOTALS ----
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount

                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx



class ASMIncentiveCalculatorView4(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.prefetch_related(
                "productincentivetier_set"
            )
        }

        # --------------------------------------------------
        # PASS 1: TOTAL QUANTITY PER PRODUCT
        # --------------------------------------------------
        product_quantities = defaultdict(Decimal)

        for si in stock_items:
            if si.item_id:
                product_quantities[si.item_id] += si.quantity

        # --------------------------------------------------
        # RESOLVE INCENTIVES
        # --------------------------------------------------
        resolved_incentives = {}

        for product_id, total_qty in product_quantities.items():
            incentive = incentives.get(product_id)
            if not incentive:
                continue

            if not incentive.has_dynamic_price:
                resolved_incentives[product_id] = incentive.ASM_incentive
                continue

            tier = (
                incentive.productincentivetier_set
                .filter(min_quantity__lte=total_qty)
                .order_by("-min_quantity")
                .first()
            )

            resolved_incentives[product_id] = (
                tier.ASM_incentive if tier else Decimal("0.00")
            )

        # --------------------------------------------------
        # BUILD ROWS + TOTALS
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            incentive_per_unit = resolved_incentives.get(
                product.id if product else None,
                Decimal("0.00")
            )

            # 🔑 IMPORTANT FIX
            has_incentive = incentive_obj is not None

            incentive_amount = si.quantity * incentive_per_unit

            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            if incentive_amount > 0:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount
                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx




class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator_paid.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH TAX INVOICE VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH PAYMENT STATUS (IMPORTANT)
        # --------------------------------------------------
        voucher_status_map = {
            cvs.voucher_id: cvs
            for cvs in CustomerVoucherStatus.objects.filter(
                voucher__in=vouchers
            )
        }

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            voucher_status = voucher_status_map.get(si.voucher_id)

            is_fully_paid = bool(
                voucher_status and voucher_status.is_fully_paid
            )

            has_incentive = incentive_obj is not None and is_fully_paid

            incentive_per_unit = (
                incentive_obj.ASM_incentive
                if has_incentive
                else Decimal("0.00")
            )

            incentive_amount = (
                si.quantity * incentive_per_unit
                if has_incentive
                else Decimal("0.00")
            )

            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": incentive_obj is not None,
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
            })

            # --------------------------------------------------
            # TOTALS — ONLY FULLY PAID
            # --------------------------------------------------
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount
                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx









class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator_paid2.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH TAX INVOICE VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH PAYMENT STATUS
        # --------------------------------------------------
        voucher_status_map = {
            cvs.voucher_id: cvs
            for cvs in CustomerVoucherStatus.objects.filter(
                voucher__in=vouchers
            )
        }

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        # --------------------------------------------------
        # TRACKING MAPS
        # --------------------------------------------------
        rows = []

        total_quantity_map = {}   # paid + unpaid
        paid_quantity_map = {}    # only paid
        product_map = {}          # product_id → product

        # --------------------------------------------------
        # BUILD ROW DATA + QUANTITY MAPS
        # --------------------------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            product_id = product.id
            product_map[product_id] = product

            voucher_status = voucher_status_map.get(si.voucher_id)
            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)

            # ---------- TOTAL QUANTITY (ALL) ----------
            total_quantity_map.setdefault(product_id, Decimal("0.00"))
            total_quantity_map[product_id] += si.quantity

            # ---------- PAID QUANTITY ONLY ----------
            if is_fully_paid:
                paid_quantity_map.setdefault(product_id, Decimal("0.00"))
                paid_quantity_map[product_id] += si.quantity

            # ---------- ROW DISPLAY ----------
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
            })

        # --------------------------------------------------
        # PRELOAD TIERS
        # --------------------------------------------------
        tiers_map = {}
        for tier in ProductIncentiveTier.objects.select_related("Product_Incentive"):
            pid = tier.Product_Incentive.product_id
            tiers_map.setdefault(pid, []).append(tier)

        # --------------------------------------------------
        # APPLY DYNAMIC PRICING PER PRODUCT
        # --------------------------------------------------
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for product_id, total_qty in total_quantity_map.items():
            product = product_map[product_id]
            paid_qty = paid_quantity_map.get(product_id, Decimal("0.00"))
            incentive_obj = incentives.get(product_id)

            if not incentive_obj:
                continue

            incentive_rate = incentive_obj.ASM_incentive
            applied_tier = None

            # ---------- DYNAMIC TIER LOGIC ----------
            if incentive_obj.has_dynamic_price:
                tiers = sorted(
                    tiers_map.get(product_id, []),
                    key=lambda t: t.min_quantity,
                    reverse=True
                )

                for tier in tiers:
                    if total_qty >= tier.min_quantity:
                        incentive_rate = tier.ASM_incentive
                        applied_tier = tier
                        break

            incentive_amount = paid_qty * incentive_rate
            grand_total_incentive += incentive_amount

            product_totals[product.name] = {
                "total_qty": total_qty,
                "paid_qty": paid_qty,
                "rate": incentive_rate,
                "tier": applied_tier.min_quantity if applied_tier else None,
                "incentive": incentive_amount,
                "has_dynamic": incentive_obj.has_dynamic_price,
            }

        # --------------------------------------------------
        # CONTEXT
        # --------------------------------------------------
        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx

# this view marks all the products which have incentive as green
class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator_paid2.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH TAX INVOICE VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH PAYMENT STATUS
        # --------------------------------------------------
        voucher_status_map = {
            cvs.voucher_id: cvs
            for cvs in CustomerVoucherStatus.objects.filter(
                voucher__in=vouchers
            )
        }

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        # --------------------------------------------------
        # TRACKING MAPS
        # --------------------------------------------------
        rows = []

        total_quantity_map = {}   # paid + unpaid
        paid_quantity_map = {}    # only paid
        product_map = {}          # product_id → product
        total_sales = Decimal("0.00")

        # --------------------------------------------------
        # BUILD ROW DATA + QUANTITY MAPS
        # --------------------------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            product_id = product.id
            product_map[product_id] = product
            #new change
            has_incentive = product_id in incentives
            total_sales += Decimal(str(si.amount))

            voucher_status = voucher_status_map.get(si.voucher_id)
            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)

            # ---------- TOTAL QUANTITY (ALL) ----------
            total_quantity_map.setdefault(product_id, Decimal("0.00"))
            total_quantity_map[product_id] += si.quantity

            # ---------- PAID QUANTITY ONLY ----------
            if is_fully_paid:
                paid_quantity_map.setdefault(product_id, Decimal("0.00"))
                paid_quantity_map[product_id] += si.quantity

            # ---------- ROW DISPLAY ----------
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_id": si.voucher.id,  # ✅ ADD THIS
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "amount": si.amount,
                "has_incentive": has_incentive,  # ✅ ADD THIS
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
            })

        # --------------------------------------------------
        # PRELOAD TIERS
        # --------------------------------------------------
        tiers_map = {}
        for tier in ProductIncentiveTier.objects.select_related("Product_Incentive"):
            pid = tier.Product_Incentive.product_id
            tiers_map.setdefault(pid, []).append(tier)

        # --------------------------------------------------
        # APPLY DYNAMIC PRICING PER PRODUCT
        # --------------------------------------------------
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for product_id, total_qty in total_quantity_map.items():
            product = product_map[product_id]
            paid_qty = paid_quantity_map.get(product_id, Decimal("0.00"))
            incentive_obj = incentives.get(product_id)

            if not incentive_obj:
                continue

            incentive_rate = incentive_obj.ASM_incentive
            applied_tier = None

            # ---------- DYNAMIC TIER LOGIC ----------
            if incentive_obj.has_dynamic_price:
                tiers = sorted(
                    tiers_map.get(product_id, []),
                    key=lambda t: t.min_quantity,
                    reverse=True
                )

                for tier in tiers:
                    if total_qty >= tier.min_quantity:
                        incentive_rate = tier.ASM_incentive
                        applied_tier = tier
                        break

            incentive_amount = paid_qty * incentive_rate
            grand_total_incentive += incentive_amount

            product_totals[product.name] = {
                "total_qty": total_qty,
                "paid_qty": paid_qty,
                "rate": incentive_rate,
                "tier": applied_tier.min_quantity if applied_tier else None,
                "incentive": incentive_amount,
                "has_dynamic": incentive_obj.has_dynamic_price,
            }

        # --------------------------------------------------
        # CONTEXT
        # --------------------------------------------------
        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive
        ctx["total_sales"] = total_sales

        return ctx



#claimed vouchers only monthly reports and dynamic prices correction
class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ---------------------------------
        # BASIC FILTERS
        # ---------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")
        month_picker = self.request.GET.get("month_picker")


        today = date.today()
        if month_picker:
            year, month = map(int, month_picker.split("-"))
        else:
            year, month = today.year, today.month

        ctx["year"] = year
        ctx["month"] = month

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
                "dynamic_group_qty": Decimal("0.00"),
                "dynamic_rate_used": None,
            })
            return ctx

        year = ctx["year"]
        month = ctx["month"]

        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # ---------------------------------
        # ALL TAX INVOICES (PAID + UNPAID)
        # ---------------------------------
        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[start_date, end_date],
        )

        vouchers = Voucher.objects.filter(
            id__in=voucher_statuses.values_list("voucher_id", flat=True)
        )

        voucher_status_map = {cvs.voucher_id: cvs for cvs in voucher_statuses}

        # ---------------------------------
        # STOCK ITEMS
        # ---------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # ---------------------------------
        # INCENTIVES
        # ---------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        rows = []

        product_map = {}
        paid_quantity_map = {}
        dynamic_products = set()

        total_sales = Decimal("0.00")

        # ---------------------------------
        # BUILD ROWS + MAPS
        # ---------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            product_id = product.id
            product_map[product_id] = product

            incentive_obj = incentives.get(product_id)
            has_incentive = product_id in incentives
            has_dynamic = bool(incentive_obj and incentive_obj.has_dynamic_price)

            if has_dynamic:
                dynamic_products.add(product_id)

            total_sales += Decimal(str(si.amount))

            voucher_status = voucher_status_map.get(si.voucher_id)

            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)
            is_partially_paid = bool(voucher_status and voucher_status.is_partially_paid)
            is_unpaid = bool(voucher_status and voucher_status.is_unpaid)

            # ---- PAID QTY ONLY FOR INCENTIVE ----
            if is_fully_paid:
                paid_quantity_map.setdefault(product_id, Decimal("0.00"))
                paid_quantity_map[product_id] += si.quantity

            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_id": si.voucher.id,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "amount": si.amount,
                "has_incentive": has_incentive,   # ✅ for yellow/green
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": is_partially_paid,
                "is_unpaid": is_unpaid,
            })

        # ---------------------------------
        # DYNAMIC GROUP QTY (PAID ONLY)
        # ---------------------------------
        dynamic_group_qty = sum(
            paid_quantity_map.get(pid, Decimal("0.00"))
            for pid in dynamic_products
        )

        if dynamic_group_qty < 500:
            dynamic_rate_used = Decimal("0.00")
        elif dynamic_group_qty >= 3000:
            dynamic_rate_used = Decimal("4.00")
        else:
            dynamic_rate_used = None  # use base rates

        # ---------------------------------
        # PRODUCT TOTALS
        # ---------------------------------
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for product_id, paid_qty in paid_quantity_map.items():
            product = product_map[product_id]
            incentive_obj = incentives.get(product_id)

            if not incentive_obj:
                continue

            if incentive_obj.has_dynamic_price:
                if dynamic_rate_used is not None:
                    rate = dynamic_rate_used
                else:
                    rate = incentive_obj.ASM_incentive
            else:
                rate = incentive_obj.ASM_incentive

            incentive_amount = paid_qty * rate
            grand_total_incentive += incentive_amount

            product_totals[product.name] = {
                "paid_qty": paid_qty,
                "rate": rate,
                "incentive": incentive_amount,
                "has_dynamic": incentive_obj.has_dynamic_price,
            }

        # ---------------------------------
        # CONTEXT
        # ---------------------------------
        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive
        ctx["total_sales"] = total_sales
        ctx["dynamic_group_qty"] = dynamic_group_qty
        ctx["dynamic_rate_used"] = dynamic_rate_used

        return ctx


class ASMIncentiveCalculatorPaidOnlyView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # 1. IDENTITY & PERMISSION CHECK
        # Find the salesperson profile linked to this login
        user_salesperson_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        # Decide who appears in the dropdown and who we are looking at
        if logged_in_user.is_superuser or logged_in_user.is_accountant:
            # Admins can see everyone
            allowed_salespersons_list = SalesPerson.objects.all().order_by("name")
            requested_id = self.request.GET.get("salesperson")
            # If admin picked someone from dropdown, use that, else default to themselves
            target_salesperson = allowed_salespersons_list.filter(
                id=requested_id).first() if requested_id else user_salesperson_profile
        else:
            # Regular user can ONLY see themselves
            target_salesperson = user_salesperson_profile
            allowed_salespersons_list = SalesPerson.objects.filter(
                id=target_salesperson.id) if target_salesperson else SalesPerson.objects.none()

        # 2. INITIALIZE CONTEXT DEFAULTS
        context.update({
            "salespersons": allowed_salespersons_list,
            "selected_salesperson": target_salesperson,
            "rows": [],
            "product_totals": {},
            "category_summary": {},
            "grand_total_incentive": Decimal("0.00"),  # Total Potential
            "payable_incentive": Decimal("0.00"),  # Total Realized (Paid by Customer)
            "unpayable_incentive": Decimal("0.00"),  # Total Pending (Unpaid Invoices)
            "total_sales": Decimal("0.00"),
            "dynamic_group_qty": Decimal("0.00"),
            "dynamic_rate_used": Decimal("0.00"),
        })

        # 3. VALIDATE FILTERS
        selected_month_picker = self.request.GET.get("month_picker")
        if not target_salesperson or not selected_month_picker:
            return context

        # 4. DATE RANGE SETUP
        try:
            year, month = map(int, selected_month_picker.split("-"))
            month_start = date(year, month, 1)
            month_end = date(year, month, monthrange(year, month)[1])
            context.update({"year": year, "month": month})
        except (ValueError, TypeError):
            return context

        # 5. FETCH DATA & RULES
        # Map rules by name for high-speed robust matching (fixes "Non-Incentive" labels)
        all_rules = ProductIncentive.objects.select_related("product", "category").all()
        name_based_rule_map = {rule.product.name.strip().lower(): rule for rule in all_rules}

        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=target_salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[month_start, month_end]
        )

        if not voucher_statuses.exists():
            return context

        unique_voucher_ids = voucher_statuses.values_list("voucher_id", flat=True).distinct()
        voucher_status_mapping = {vs.voucher_id: vs for vs in voucher_statuses}
        stock_items_list = VoucherStockItem.objects.filter(voucher_id__in=unique_voucher_ids).select_related("voucher",
                                                                                                             "item").prefetch_related(
            "voucher__rows")

        # 6. DATA PRE-PROCESSING
        transaction_log_rows = []
        monthly_calculation_queue = []
        processed_vouchers_set = set()
        total_monthly_revenue = Decimal("0.00")

        for item in stock_items_list:
            if not item.item: continue

            product_name_key = item.item.name.strip().lower()
            rule_config = name_based_rule_map.get(product_name_key)
            status_obj = voucher_status_mapping.get(item.voucher_id)

            # Robust payment check: Is the checkbox checked OR is the balance 0?
            is_invoice_cleared = bool(status_obj and (status_obj.is_fully_paid or status_obj.unpaid_amount == 0))

            # Revenue Total (Ledger amount calculation)
            if item.voucher_id not in processed_vouchers_set:
                processed_vouchers_set.add(item.voucher_id)
                party_row = item.voucher.rows.filter(ledger__icontains=item.voucher.party_name.strip()).first()
                total_monthly_revenue += Decimal(str(party_row.amount if party_row else (item.voucher.amount or 0)))

            if rule_config:
                unit_price = Decimal(str(item.amount)) / Decimal(str(item.quantity)) if item.quantity > 0 else Decimal(
                    '0')
                monthly_calculation_queue.append({
                    'name': item.item.name,
                    'rule': rule_config,
                    'qty': Decimal(str(item.quantity)),
                    'is_paid': is_invoice_cleared,
                    'unit_p': unit_price,
                    'cat': rule_config.category.name if rule_config.category else "Other"
                })

            transaction_log_rows.append({
                "date": item.voucher.date,
                "customer": item.voucher.party_name,
                "voucher_no": item.voucher.voucher_number,
                "product": item.item.name,
                "quantity": item.quantity,
                "amount": item.amount,
                "is_fully_paid": is_invoice_cleared,
                "has_incentive": rule_config is not None,
                "voucher_id": item.voucher.id,
                "customer_id": status_obj.customer_id if status_obj else None,
            })

        # 7. PERFORMANCE THRESHOLD (The 0/3/4 Rate)
        total_dynamic_volume = sum([
            s['qty'] * s['rule'].pack_size_multiplier
            for s in monthly_calculation_queue if s['rule'].has_dynamic_price
        ])

        if total_dynamic_volume < 500:
            active_rate = Decimal("0.00")
        elif total_dynamic_volume < 3000:
            active_rate = Decimal("3.00")
        else:
            active_rate = Decimal("4.00")

        # 8. FINAL AGGREGATION (Splitting into Potential vs. Payable)
        payout_breakdown_table = {}
        category_item_summary = {}
        grand_total_potential = Decimal("0.00")
        realized_payable_total = Decimal("0.00")

        for sale in monthly_calculation_queue:
            rule = sale['rule']

            # Category Physical Item Count
            category_item_summary[sale['cat']] = category_item_summary.get(sale['cat'], Decimal('0')) + sale['qty']

            # Use Dynamic Rate or Fixed Base Rate
            asm_base_rate, _ = rule.get_effective_rates
            applied_rate = active_rate if rule.has_dynamic_price else asm_base_rate

            # MSP Check (Protection)
            if rule.msp > 0 and sale['unit_p'] < rule.msp: continue

            # Final Row Math
            row_value = sale['qty'] * rule.pack_size_multiplier * applied_rate

            grand_total_potential += row_value
            if sale['is_paid']:
                realized_payable_total += row_value

            # Build data for the Breakdown Table
            prod_name = sale['name']
            if prod_name not in payout_breakdown_table:
                payout_breakdown_table[prod_name] = {"paid_qty": 0, "rate": applied_rate, "potential_payout": 0,
                                                     "ready_payout": 0}

            payout_breakdown_table[prod_name]["paid_qty"] += sale['qty']
            payout_breakdown_table[prod_name]["potential_payout"] += row_value
            payout_breakdown_table[prod_name]["ready_payout"] += row_value if sale['is_paid'] else 0

        # 9. RETURN FINAL DATA
        context.update({
            "rows": transaction_log_rows,
            "product_totals": payout_breakdown_table,
            "category_summary": category_item_summary,
            "grand_total_incentive": grand_total_potential,
            "payable_incentive": realized_payable_total,
            "unpayable_incentive": grand_total_potential - realized_payable_total,
            "total_sales": total_monthly_revenue,
            "dynamic_group_qty": total_dynamic_volume,
            "dynamic_rate_used": active_rate,
        })
        return context


class ASMIncentivePaidUnpaidView(AccountantRequiredMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_admin.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # 1. FILTERS & DATE SETUP
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")
        salesperson_id = self.request.GET.get("salesperson")
        month_picker = self.request.GET.get("month_picker")

        today = date.today()
        if month_picker:
            year, month = map(int, month_picker.split("-"))
        else:
            year, month = today.year, today.month
        ctx.update({"year": year, "month": month})

        # Initialize defaults
        ctx.update({
            "rows": [], "product_totals": {}, "grand_total_incentive": Decimal("0.00"),
            "unpaid_months_data": [], "total_unpaid_grand": Decimal("0.00"),
            "category_summary": {}, "total_sales": Decimal("0.00")
        })

        if not salesperson_id:
            return ctx

        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])
        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # 2. FETCH DATA & TOTAL SALES
        v_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[start_date, end_date],
        )
        v_ids = v_statuses.values_list("voucher_id", flat=True).distinct()
        v_status_map = {cvs.voucher_id: cvs for cvs in v_statuses}

        # Calculate real total sales
        ctx["total_sales"] = VoucherStockItem.objects.filter(
            voucher_id__in=v_ids
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        stock_items = VoucherStockItem.objects.filter(voucher_id__in=v_ids).select_related("voucher", "item")

        # 3. MATCHING LOGIC (Cache by Name)
        all_configs = ProductIncentive.objects.select_related("product", "category").all()
        incentive_map = {cfg.product.name.strip().lower(): cfg for cfg in all_configs}

        # 4. PROCESSING CURRENT MONTH
        rows, monthly_sales_calc = [], []
        dynamic_product_keys = set()

        for si in stock_items:
            if not si.item: continue
            p_name_key = si.item.name.strip().lower()
            config = incentive_map.get(p_name_key)
            status = v_status_map.get(si.voucher_id)
            is_fully_paid = bool(status and (status.is_fully_paid or status.unpaid_amount == 0))

            if config:
                if config.has_dynamic_price: dynamic_product_keys.add(p_name_key)
                unit_p = Decimal(str(si.amount)) / Decimal(str(si.quantity)) if si.quantity > 0 else Decimal('0')
                monthly_sales_calc.append({
                    'name': si.item.name, 'config': config, 'qty': Decimal(str(si.quantity)),
                    'is_paid': is_fully_paid, 'unit_p': unit_p, 'key': p_name_key
                })

            payout_rec = IncentivePaymentStatus.objects.filter(voucher_status__voucher_id=si.voucher_id).first()
            rows.append({
                "date": si.voucher.date, "customer": si.voucher.party_name,
                "voucher_id": si.voucher.id, "voucher_no": si.voucher.voucher_number,
                "product": si.item.name, "quantity": si.quantity, "amount": si.amount,
                "is_fully_paid": is_fully_paid, "has_incentive": config is not None,
                "payout_done": bool(payout_rec and payout_rec.is_paid_to_asm and config is not None),
                "payout_amount": payout_rec.amount_frozen if payout_rec else 0,
            })

        # 5. DYNAMIC RATE CALCULATION
        total_dynamic_qty = sum(
            [Decimal(str(s['qty'])) * s['config'].pack_size_multiplier for s in monthly_sales_calc if
             s['config'].has_dynamic_price])
        if total_dynamic_qty < 500:
            group_rate = Decimal("0.00")
        elif total_dynamic_qty < 3000:
            group_rate = Decimal("3.00")
        else:
            group_rate = Decimal("4.00")

        # 6. AGGREGATE CURRENT MONTH
        product_totals, category_summary, grand_total = {}, {}, Decimal("0.00")
        for s in monthly_sales_calc:
            if not s['is_paid']: continue  # Payout card only shows Paid items

            cfg = s['config']
            if cfg.category:
                category_summary[cfg.category.name] = category_summary.get(cfg.category.name, Decimal('0')) + s['qty']

            asm_base, _ = cfg.get_effective_rates
            current_rate = group_rate if cfg.has_dynamic_price else asm_base
            if cfg.msp > 0 and s['unit_p'] < cfg.msp: continue

            val = s['qty'] * cfg.pack_size_multiplier * current_rate
            grand_total += val

            if s['name'] not in product_totals:
                product_totals[s['name']] = {"incentive": 0, "rate": current_rate, "paid_qty": 0,
                                             "multiplier": cfg.pack_size_multiplier, "is_pack": cfg.is_special_pack}
            product_totals[s['name']]["incentive"] += val
            product_totals[s['name']]["paid_qty"] += s['qty']

        # 7. OUTSTANDING ENGINE (Performance Aware)
        fiscal_start_date = date(year if month >= 4 else year - 1, 4, 1)
        unpaid_months_data, total_unpaid_grand = [], Decimal("0.00")

        all_year_vouchers = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson, voucher_date__range=[fiscal_start_date, end_date],
            voucher_type__iexact="TAX INVOICE"
        ).select_related('voucher')

        if all_year_vouchers.exists():
            month_bundles = {}
            for v_status in all_year_vouchers:
                m_num = v_status.voucher_date.month
                is_payout_done = IncentivePaymentStatus.objects.filter(voucher_status=v_status).exists()
                month_bundles.setdefault(m_num, {'all_items': [], 'unpaid_items': []})
                items = VoucherStockItem.objects.filter(voucher=v_status.voucher)
                for itm in items:
                    month_bundles[m_num]['all_items'].append(itm)
                    if not is_payout_done: month_bundles[m_num]['unpaid_items'].append(itm)

            for m_num in sorted(month_bundles.keys(), key=lambda x: (x < 4, x)):
                data = month_bundles[m_num]
                m_dyn_qty, m_unpaid_sum = Decimal('0.00'), Decimal('0.00')
                for item in data['all_items']:
                    cfg = incentive_map.get(item.item.name.strip().lower())
                    if cfg and cfg.has_dynamic_price:
                        m_dyn_qty += Decimal(str(item.quantity)) * cfg.pack_size_multiplier
                m_rate = Decimal('4.00') if m_dyn_qty >= 3000 else (
                    Decimal('3.00') if m_dyn_qty >= 500 else Decimal('0.00'))
                for item in data['unpaid_items']:
                    cfg = incentive_map.get(item.item.name.strip().lower())
                    if cfg:
                        rate = m_rate if cfg.has_dynamic_price else cfg.get_effective_rates[0]
                        m_unpaid_sum += (Decimal(str(item.quantity)) * cfg.pack_size_multiplier) * rate
                if m_unpaid_sum > 0:
                    unpaid_months_data.append({'month': date(2000, m_num, 1).strftime('%b'), 'amount': m_unpaid_sum})
                    total_unpaid_grand += m_unpaid_sum

        ctx.update({
            "rows": rows, "product_totals": product_totals, "grand_total_incentive": grand_total,
            "dynamic_group_qty": total_dynamic_qty, "dynamic_rate_used": group_rate,
            "category_summary": category_summary, "unpaid_months_data": unpaid_months_data,
            "total_unpaid_grand": total_unpaid_grand
        })
        return ctx

    def post(self, request, *args, **kwargs):
        self.request.GET = request.POST
        action = request.POST.get("action")
        salesperson_id = request.POST.get("salesperson")
        month_picker = request.POST.get("month_picker")

        all_configs = ProductIncentive.objects.select_related("product").all()
        incentive_map = {cfg.product.name.strip().lower(): cfg for cfg in all_configs}

        if action == "unpay":
            v_id = request.POST.get("voucher_id")
            IncentivePaymentStatus.objects.filter(voucher_status__voucher_id=v_id).delete()
            messages.warning(request, "Payout record removed.")
        else:
            context = self.get_context_data()
            rows = context.get('rows', [])
            target_v_id = request.POST.get("voucher_id")
            processed_count = 0
            for r in rows:
                if action == "pay_single" and str(r['voucher_id']) != str(target_v_id): continue
                already_paid = IncentivePaymentStatus.objects.filter(
                    voucher_status__voucher_id=r['voucher_id']).exists()
                if already_paid or not r['is_fully_paid'] or not r['has_incentive']: continue

                config = incentive_map.get(r['product'].strip().lower())
                rate = context['dynamic_rate_used'] if config.has_dynamic_price else config.get_effective_rates[0]
                frozen_val = (Decimal(str(r['quantity'])) * config.pack_size_multiplier) * Decimal(str(rate))

                IncentivePaymentStatus.objects.update_or_create(
                    voucher_status=CustomerVoucherStatus.objects.get(voucher_id=r['voucher_id']),
                    defaults={'is_paid_to_asm': True, 'paid_at': timezone.now(), 'paid_by': request.user,
                              'amount_frozen': frozen_val}
                )
                processed_count += 1
            if processed_count > 0: messages.success(request, f"Processed {processed_count} items.")

        return redirect(f"{request.path}?salesperson={salesperson_id}&month_picker={month_picker}")


class RSMTeamIncentiveDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/rsm_team_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        logged_in_user = self.request.user
        user_sp_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        is_privileged_user = (
                logged_in_user.is_accountant or
                logged_in_user.groups.filter(name='Accountant').exists()
        )
        target_rsm_names = ["Ankush", "Bhavya"]

        if is_privileged_user:
            allowed_rsms = SalesPerson.objects.filter(
                name__in=target_rsm_names,
                manager__isnull=True
            ).order_by("name")
        elif user_sp_profile and user_sp_profile.name in target_rsm_names and user_sp_profile.manager is None:
            allowed_rsms = SalesPerson.objects.filter(id=user_sp_profile.id)
        else:
            allowed_rsms = SalesPerson.objects.none()


        ctx["rsms"] = allowed_rsms
        rsm_id = self.request.GET.get("rsm")
        month_picker = self.request.GET.get("month_picker")

        if not is_privileged_user:
            rsm_id = user_sp_profile.id if user_sp_profile else None

        if not rsm_id or not month_picker:
            return ctx

        if not allowed_rsms.filter(id=rsm_id).exists():
            return ctx

        year, month = map(int, month_picker.split("-"))
        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])
        rsm_user = get_object_or_404(SalesPerson, id=rsm_id)
        ctx.update({"selected_rsm": rsm_user, "year": year, "month": month})

        incentive_rules = {cfg.product.name.strip().lower(): cfg for cfg in
                           ProductIncentive.objects.select_related("product", "category").all()}

        team_report = []
        team_category_summary = {}

        # GRAND TOTAL BUCKETS for the RSM
        grand_rsm_paid = Decimal("0.00")
        grand_rsm_pending = Decimal("0.00")

        for asm in rsm_user.team_members.all():
            # FETCH ALL VOUCHERS (Removed is_fully_paid=True filter here)
            all_vouchers = CustomerVoucherStatus.objects.filter(
                sold_by=asm, voucher_type__iexact="TAX INVOICE",
                voucher_date__range=[start_date, end_date]
            ).select_related('voucher')

            asm_total_sheets_vol = Decimal("0.00")
            asm_category_summary = {}
            temp_item_list = []

            for vs in all_vouchers:
                is_paid = bool(vs.is_fully_paid or vs.unpaid_amount == 0)
                items = VoucherStockItem.objects.filter(voucher=vs.voucher).select_related('item')

                for si in items:
                    cfg = incentive_rules.get(si.item.name.strip().lower())
                    if cfg:
                        true_qty = Decimal(str(si.quantity)) * cfg.pack_size_multiplier
                        if cfg.has_dynamic_price:
                            asm_total_sheets_vol += true_qty

                        if cfg.category:
                            cat_name = cfg.category.name
                            physical_qty = Decimal(str(si.quantity))
                            asm_category_summary[cat_name] = asm_category_summary.get(cat_name,
                                                                                      Decimal('0')) + physical_qty
                            team_category_summary[cat_name] = team_category_summary.get(cat_name,
                                                                                        Decimal('0')) + physical_qty

                        temp_item_list.append({
                            'si': si, 'cfg': cfg, 'true_qty': true_qty, 'is_paid': is_paid
                        })

            # Rate Logic (Based on Total Potential Volume)
            rsm_sheet_rate = Decimal('1.00') if asm_total_sheets_vol >= 1000 else Decimal('0.00')
            if asm_total_sheets_vol < 500:
                asm_m_rate = Decimal('0.00')
            elif asm_total_sheets_vol < 3000:
                asm_m_rate = Decimal('3.00')
            else:
                asm_m_rate = Decimal('4.00')

            # ASM BUCKETS
            asm_paid = Decimal('0')
            asm_pending = Decimal('0')
            rsm_from_asm_paid = Decimal('0')
            rsm_from_asm_pending = Decimal('0')
            detailed_products = []

            for entry in temp_item_list:
                si, cfg, t_qty, is_paid = entry['si'], entry['cfg'], entry['true_qty'], entry['is_paid']
                asm_base, rsm_base = cfg.get_effective_rates

                final_asm_rate = asm_m_rate if cfg.has_dynamic_price else asm_base
                final_rsm_rate = rsm_sheet_rate if cfg.has_dynamic_price else rsm_base

                unit_price = Decimal(str(si.amount)) / Decimal(str(si.quantity)) if si.quantity > 0 else 0

                # Math
                asm_val = t_qty * final_asm_rate if not (cfg.msp > 0 and unit_price < cfg.msp) else 0
                rsm_val = t_qty * final_rsm_rate

                if is_paid:
                    asm_paid += asm_val
                    rsm_from_asm_paid += rsm_val
                else:
                    asm_pending += asm_val
                    rsm_from_asm_pending += rsm_val

                detailed_products.append({
                    'name': si.item.name, 'qty': si.quantity, 'true_qty': t_qty,
                    'asm_incentive': asm_val, 'rsm_incentive': rsm_val,
                    'is_sheet': cfg.has_dynamic_price, 'is_paid': is_paid
                })

            if temp_item_list:
                team_report.append({
                    'asm_name': asm.name,
                    'total_sheets': asm_total_sheets_vol,
                    'asm_paid': asm_paid,
                    'asm_pending': asm_pending,
                    'rsm_paid': rsm_from_asm_paid,
                    'rsm_pending': rsm_from_asm_pending,
                    'items': detailed_products,
                    'asm_categories': asm_category_summary
                })
                grand_rsm_paid += rsm_from_asm_paid
                grand_rsm_pending += rsm_from_asm_pending

        ctx.update({
            "team_report": team_report,
            "grand_rsm_paid": grand_rsm_paid,
            "grand_rsm_pending": grand_rsm_pending,
            "grand_rsm_potential": grand_rsm_paid + grand_rsm_pending,
            "team_category_summary": team_category_summary
        })
        return ctx


class ProductIncentiveListView(TemplateView):
    template_name = "incentive_calculator/product_incentive_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["products"] = (
            ProductIncentive.objects
            .select_related("product")
            .order_by("product__name")
        )

        return ctx


