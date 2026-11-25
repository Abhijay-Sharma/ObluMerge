import datetime
from datetime import timedelta
import logging

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone

from inventory.models import InventoryItem, Category, ParentCategory
from inventory.views import PurchaseOrderView
from inventory.utils import (
    get_current_stage,
    get_expected_arrival_date,
    get_days_in_current_stage,
    get_remaining_days,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Evaluates inventory stock runway & demand forecasting, then automatically sends "
        "an alert email for 'Order Now' (Urgent) and 'Order Soon' (Need to Order) items to "
        "Nitin, Abhijay, Accounts, Operations, and Swasti."
    )

    # ── Target Recipients ───────────────────────────────────────────────────────
    DEFAULT_TO_EMAILS = [

    ]
    DEFAULT_CC_EMAILS = [
        "abhijay.obluhc@gmail.com",
        "swasti.obluhc@gmail.com",
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--category",
            type=int,
            help="Optional Category ID to restrict the alert to a specific category.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the evaluation and print results to the terminal without sending an email.",
        )
        parser.add_argument(
            "--force-send",
            action="store_true",
            help="Send the email even if there are 0 items requiring an order.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        force_send = options.get("force_send", False)
        category_id = options.get("category")

        self.stdout.write(self.style.NOTICE("🔍 Scanning inventory and calculating order projections..."))

        today = datetime.date.today()
        ninety_days_ago = today - timedelta(days=90)

        # ── 1. Preload Voucher & PO Tracking Data ──────────────────────────────
        sales_map, credit_note_map, internal_customer_map, po_map, gst_map = (
            PurchaseOrderView._preload_voucher_data()
        )
        tracking_item_map = PurchaseOrderView._preload_tracking_data()

        # ── 2. Filter Inventory Items ──────────────────────────────────────────
        items_qs = (
            InventoryItem.objects
            .select_related("category", "category__parent")
            .order_by("category__name", "name")
        )
        if category_id:
            items_qs = items_qs.filter(category_id=category_id)

        urgent_items = []  # 🔴 Order Now
        warn_items = []  # 🟡 Order Soon
        total_scanned = 0

        # ── 3. Run PO Planner Projection Algorithm ─────────────────────────────
        for item in items_qs:
            total_scanned += 1
            iid = item.id
            current_stock = float(item.quantity or 0)

            # Net Sales (Tally Tax Invoices minus Credit Notes)
            item_sales = sales_map.get(iid, [])
            item_credit_notes = credit_note_map.get(iid, [])
            item_sales = PurchaseOrderView._net_sales(item_sales, item_credit_notes)

            # Remove Internal Sales
            item_internal = internal_customer_map.get(iid, [])
            item_sales = PurchaseOrderView._remove_internal_sales(item_sales, item_internal)

            # Dead stock check (>90 days without sale)
            is_dead = not any(
                row["date"] and row["date"] >= ninety_days_ago
                for row in item_sales
            )

            # Landing date for new products
            landing_date = None
            if item.is_new_product:
                item_gst_rows = gst_map.get(iid, [])
                valid_dates = [row["date"] for row in item_gst_rows if row.get("date")]
                if valid_dates:
                    landing_date = min(valid_dates)

            # Average daily consumption
            if item.is_new_product and landing_date:
                avg_daily = PurchaseOrderView._avg_daily_from_landing_date(item_sales, landing_date)
            else:
                avg_daily = PurchaseOrderView._avg_daily_from_tally(item_sales)

            # Growth rate calculations
            growth_1y, growth_3m = PurchaseOrderView._growth_windows(item_sales)
            rate_1y = (growth_1y / 12 / 100) if growth_1y is not None else None
            rate_3m = (growth_3m / 3 / 100) if growth_3m is not None else None

            if rate_1y is not None and rate_3m is not None:
                monthly_growth_rate = 0.20 * rate_1y + 0.80 * rate_3m
            elif rate_3m is not None:
                monthly_growth_rate = rate_3m
            elif rate_1y is not None:
                monthly_growth_rate = rate_1y
            else:
                monthly_growth_rate = 0.0

            monthly_growth_rate = min(monthly_growth_rate, 0.40)

            # Sales forecast (next 3 months)
            pred_m1, pred_m2, pred_m3 = PurchaseOrderView._forecast_next_3_months(
                avg_daily, growth_1y, growth_3m, is_dead
            )

            # Tracking & Incoming Shipments
            tracking_details = []
            tracking_items = tracking_item_map.get(iid, [])

            for tracking_item in tracking_items:
                po = tracking_item.purchase_order
                current_stage = get_current_stage(po)
                days_in_stage = get_days_in_current_stage(po)

                system_eta = get_expected_arrival_date(po)
                if po.manual_eta:
                    incoming_date = po.manual_eta
                    remaining_days = max(0, (po.manual_eta - today).days)
                else:
                    incoming_date = system_eta
                    remaining_days = get_remaining_days(po)

                ordered_qty = float(tracking_item.ordered_quantity or 0)
                arrived_qty = float(tracking_item.arrived_quantity or 0)
                pending_qty = max(0, ordered_qty - arrived_qty)

                if pending_qty > 0:
                    tracking_details.append({
                        "po_number": getattr(getattr(po, "tally_voucher", None), "voucher_number", "-"),
                        "arrived_qty": arrived_qty,
                        "incoming_qty": pending_qty,
                        "stage": current_stage.stage.name if current_stage else "-",
                        "days_in_stage": float(days_in_stage) if days_in_stage is not None else None,
                        "remaining_days": float(remaining_days) if remaining_days is not None else None,
                        "eta": incoming_date,
                        "type": "pending",
                    })

            incoming_shipments = []
            total_in_transit = 0
            for t in tracking_details:
                arrived_qty = float(t.get("arrived_qty") or 0)
                pending_qty = float(t.get("incoming_qty") or 0)
                total_in_transit += pending_qty

                if arrived_qty > 0:
                    incoming_shipments.append({
                        "incoming_qty": arrived_qty,
                        "arrival_day": 0,
                        "po_number": t["po_number"],
                        "stage": "Arrived",
                        "eta": today,
                        "remaining_days": 0,
                        "days_in_stage": t.get("days_in_stage"),
                        "type": "arrived",
                    })

                if pending_qty > 0 and t.get("eta"):
                    arrival_day = max(0, (t["eta"] - today).days)
                    incoming_shipments.append({
                        "incoming_qty": pending_qty,
                        "arrival_day": arrival_day,
                        "po_number": t["po_number"],
                        "stage": t.get("stage"),
                        "eta": t["eta"],
                        "remaining_days": t.get("remaining_days"),
                        "days_in_stage": t.get("days_in_stage"),
                        "type": "pending",
                    })

            # Core Order Calculation
            delivery_days = item.expected_delivery_days or 30
            calc = PurchaseOrderView._calc_order(
                current_stock=current_stock,
                avg_daily=avg_daily,
                delivery_days=delivery_days,
                incoming_shipments=incoming_shipments,
                pred_m1=pred_m1,
                pred_m2=pred_m2,
                pred_m3=pred_m3,
                monthly_growth_rate=monthly_growth_rate,
                moq=item.minimum_order_quantity,
                is_dead=is_dead,
            )

            urgency = calc.get("order_urgency")
            order_final = calc.get("order_final", 0)
            runway_days = calc.get("runway_days")
            runway_months = calc.get("runway_months")

            item_dict = {
                "id": item.id,
                "name": item.name,
                "category": item.category.name if item.category else "-",
                "unit": item.unit or "pcs",
                "current_stock": int(current_stock),
                "in_transit": int(total_in_transit),
                "order_final": int(order_final),
                "delivery_days": delivery_days,
                "runway_days": runway_days,
                "runway_months": f"{runway_months:.1f}m" if runway_months is not None else "0m",
                "moq_note": calc.get("moq_note"),
                "order_urgency": urgency,
                "avg_daily": round(avg_daily, 2),
            }

            if urgency == "urgent" and order_final > 0:
                urgent_items.append(item_dict)
            elif urgency == "warn" and order_final > 0:
                warn_items.append(item_dict)

        # ── 4. Terminal Summary ────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(f"\n📦 Finished scanning {total_scanned} products."))
        self.stdout.write(self.style.ERROR(f"🔴 Order Now (Urgent): {len(urgent_items)}"))
        self.stdout.write(self.style.WARNING(f"🟡 Order Soon (Need to Order): {len(warn_items)}"))

        if not urgent_items and not warn_items and not force_send:
            self.stdout.write(self.style.SUCCESS("✅ No products require reorder at this time. Email skipped."))
            return

        if dry_run:
            self.stdout.write(self.style.NOTICE("\n[DRY RUN] Email will not be sent. Listing items below:"))
            for item in urgent_items:
                self.stdout.write(
                    f"  🔴 [URGENT] {item['name']} | Stock: {item['current_stock']} | Rec Order: {item['order_final']} | Runway: {item['runway_months']}")
            for item in warn_items:
                self.stdout.write(
                    f"  🟡 [SOON]   {item['name']} | Stock: {item['current_stock']} | Rec Order: {item['order_final']} | Runway: {item['runway_months']}")
            return

        # ── 5. Compose and Send Email ──────────────────────────────────────────
        self.stdout.write(self.style.NOTICE("\n📧 Sending notification email..."))
        self.send_order_notification_email(urgent_items, warn_items, total_scanned)

    def send_order_notification_email(self, urgent_items, warn_items, total_scanned):
        today_str = datetime.date.today().strftime("%d %b %Y")

        subject = (
            f"🚨 PO Alert ({today_str}): "
            f"{len(urgent_items)} Order Now | {len(warn_items)} Order Soon"
        )

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "Oblu Procurement <proforma@oblutools.com>")
        to_emails = self.DEFAULT_TO_EMAILS
        cc_emails = self.DEFAULT_CC_EMAILS

        # Plain-text version
        text_lines = [
            f"OBLU HEALTHCARE — PURCHASE ORDER ALERT",
            f"Date: {today_str}",
            f"Monitored Products Scanned: {total_scanned}",
            f"🔴 Urgent (Order Now): {len(urgent_items)}",
            f"🟡 Need to Order Soon: {len(warn_items)}",
            "=" * 60,
            "",
        ]

        if urgent_items:
            text_lines.append("🔴 URGENT ORDERS (ACTION REQUIRED NOW):")
            for u in urgent_items:
                text_lines.append(
                    f"• {u['name']} ({u['category']}) | Stock: {u['current_stock']} | In Transit: {u['in_transit']} | "
                    f"Rec Order: {u['order_final']} {u['unit']} | Runway: {u['runway_months']} (Lead Time: {u['delivery_days']}d)"
                )
            text_lines.append("")

        if warn_items:
            text_lines.append("🟡 NEED TO ORDER SOON:")
            for w in warn_items:
                text_lines.append(
                    f"• {w['name']} ({w['category']}) | Stock: {w['current_stock']} | In Transit: {w['in_transit']} | "
                    f"Rec Order: {w['order_final']} {w['unit']} | Runway: {w['runway_months']} (Lead Time: {w['delivery_days']}d)"
                )
            text_lines.append("")

        text_lines.extend([
            "=" * 60,
            "🔗 Open Purchase Order Planner: https://oblutools.com/inventory/purchase-order/",
        ])
        text_content = "\n".join(text_lines)

        # HTML version
        html_content = self.generate_html_email(urgent_items, warn_items, today_str, total_scanned)

        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email,
                to=to_emails,
                cc=cc_emails,
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)

            self.stdout.write(self.style.SUCCESS(f"✅ Alert email successfully sent!"))
            self.stdout.write(f"   • TO: {', '.join(to_emails)}")
            self.stdout.write(f"   • CC: {', '.join(cc_emails)}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Failed to send email: {e}"))
            logger.error(f"PO Alert Management Command Email Error: {e}", exc_info=True)

    def generate_html_email(self, urgent_items, warn_items, date_str, total_scanned):
        """Builds a responsive, styled HTML email table."""

        def render_rows(items, badge_color, badge_text):
            if not items:
                return """
                <tr>
                    <td colspan="6" style="padding: 12px; text-align: center; color: #64748b; font-size: 13px;">
                        No items in this category.
                    </td>
                </tr>
                """
            rows_html = ""
            for item in items:
                moq_html = f"<div style='font-size:11px; color:#d97706;'>MOQ: {item['moq_note']}</div>" if item.get(
                    "moq_note") else ""
                transit_html = f"<span style='color:#0284c7; font-weight:600;'>{item['in_transit']}</span>" if item[
                                                                                                                   'in_transit'] > 0 else "<span style='color:#94a3b8;'>0</span>"

                rows_html += f"""
                <tr style="border-bottom: 1px solid #e2e8f0; font-size: 13px;">
                    <td style="padding: 10px 12px; font-weight: 600; color: #1e293b;">
                        {item['name']}
                        <div style="font-size: 11px; font-weight: 400; color: #64748b;">{item['category']}</div>
                    </td>
                    <td style="padding: 10px 12px; text-align: right; color: #334155;">{item['current_stock']} {item['unit']}</td>
                    <td style="padding: 10px 12px; text-align: right;">{transit_html}</td>
                    <td style="padding: 10px 12px; text-align: right; font-weight: 700; color: #0f172a;">
                        <span style="background: #f1f5f9; padding: 3px 8px; border-radius: 4px;">{item['order_final']} {item['unit']}</span>
                        {moq_html}
                    </td>
                    <td style="padding: 10px 12px; text-align: center; color: #475569; font-weight: 600;">{item['runway_months']}</td>
                    <td style="padding: 10px 12px; text-align: center;">
                        <span style="display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 700; background: {badge_color}; color: #ffffff;">
                            {badge_text}
                        </span>
                    </td>
                </tr>
                """
            return rows_html

        urgent_table_rows = render_rows(urgent_items, "#dc2626", "ORDER NOW")
        warn_table_rows = render_rows(warn_items, "#d97706", "ORDER SOON")

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>PO Action Required</title>
        </head>
        <body style="margin: 0; padding: 20px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
          <div style="max-width: 820px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">

            <!-- Header -->
            <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: #ffffff; padding: 24px; border-bottom: 3px solid #3b82f6;">
              <h2 style="margin: 0 0 6px 0; font-size: 20px; font-weight: 700; letter-spacing: -0.02em;">
                📦 Purchase Order Reorder Notification
              </h2>
              <p style="margin: 0; font-size: 13px; color: #94a3b8;">
                Automated Inventory Planner Digest &bull; <strong>{date_str}</strong>
              </p>
            </div>

            <!-- Summary Cards -->
            <div style="padding: 18px 24px; background: #f1f5f9; border-bottom: 1px solid #e2e8f0;">
              <table style="width: 100%; border-collapse: collapse;">
                <tr>
                  <td style="padding: 6px; width: 33%;">
                    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; text-align: center;">
                      <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 700;">Monitored Items</div>
                      <div style="font-size: 22px; font-weight: 800; color: #0f172a; margin-top: 2px;">{total_scanned}</div>
                    </div>
                  </td>
                  <td style="padding: 6px; width: 33%;">
                    <div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px; text-align: center;">
                      <div style="font-size: 11px; text-transform: uppercase; color: #dc2626; font-weight: 700;">🔴 Order Now</div>
                      <div style="font-size: 22px; font-weight: 800; color: #dc2626; margin-top: 2px;">{len(urgent_items)}</div>
                    </div>
                  </td>
                  <td style="padding: 6px; width: 33%;">
                    <div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 12px; text-align: center;">
                      <div style="font-size: 11px; text-transform: uppercase; color: #d97706; font-weight: 700;">🟡 Order Soon</div>
                      <div style="font-size: 22px; font-weight: 800; color: #d97706; margin-top: 2px;">{len(warn_items)}</div>
                    </div>
                  </td>
                </tr>
              </table>
            </div>

            <!-- Main Content -->
            <div style="padding: 24px;">

              <!-- Urgent Section -->
              <div style="margin-bottom: 28px;">
                <div style="display: flex; align-items: center; margin-bottom: 12px;">
                  <h3 style="margin: 0; font-size: 15px; font-weight: 700; color: #b91c1c;">
                    🚨 Urgent: Order Now ({len(urgent_items)} Items)
                  </h3>
                </div>
                <div style="border: 1px solid #fecaca; border-radius: 8px; overflow: hidden;">
                  <table style="width: 100%; border-collapse: collapse; text-align: left;">
                    <thead>
                      <tr style="background: #fef2f2; color: #991b1b; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;">
                        <th style="padding: 10px 12px;">Product / Category</th>
                        <th style="padding: 10px 12px; text-align: right;">Stock</th>
                        <th style="padding: 10px 12px; text-align: right;">In Transit</th>
                        <th style="padding: 10px 12px; text-align: right;">Rec. Order</th>
                        <th style="padding: 10px 12px; text-align: center;">Runway</th>
                        <th style="padding: 10px 12px; text-align: center;">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {urgent_table_rows}
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- Order Soon Section -->
              <div style="margin-bottom: 28px;">
                <div style="display: flex; align-items: center; margin-bottom: 12px;">
                  <h3 style="margin: 0; font-size: 15px; font-weight: 700; color: #b45309;">
                    ⚠️ Need to Order Soon ({len(warn_items)} Items)
                  </h3>
                </div>
                <div style="border: 1px solid #fde68a; border-radius: 8px; overflow: hidden;">
                  <table style="width: 100%; border-collapse: collapse; text-align: left;">
                    <thead>
                      <tr style="background: #fffbeb; color: #92400e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;">
                        <th style="padding: 10px 12px;">Product / Category</th>
                        <th style="padding: 10px 12px; text-align: right;">Stock</th>
                        <th style="padding: 10px 12px; text-align: right;">In Transit</th>
                        <th style="padding: 10px 12px; text-align: right;">Rec. Order</th>
                        <th style="padding: 10px 12px; text-align: center;">Runway</th>
                        <th style="padding: 10px 12px; text-align: center;">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {warn_table_rows}
                    </tbody>
                  </table>
                </div>
              </div>

              <!-- Action Call to Action Button -->
              <div style="text-align: center; margin-top: 32px; padding: 16px 0; border-top: 1px dashed #cbd5e1;">
                <a href="https://oblutools.com/inventory/purchase-order/" 
                   style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; padding: 12px 28px; font-size: 14px; font-weight: 700; border-radius: 6px; box-shadow: 0 2px 4px rgba(37,99,235,0.2);">
                  Open Purchase Order Planner &rarr;
                </a>
              </div>

            </div>

            <!-- Footer -->
            <div style="background: #f8fafc; padding: 16px 24px; font-size: 11px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0;">
              This notification was generated by the Oblu Tools automated inventory management system.<br>
              Recipients: operations@obluhc.com, accounts@obluhc.com, nitin.a@obluhc.com, abhijay.obluhc@gmail.com, swasti.obluhc@gmail.com
            </div>

          </div>
        </body>
        </html>
        """
        return html