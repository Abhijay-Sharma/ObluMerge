from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from datetime import date, timedelta
import pandas as pd
import numpy as np
from .tests import get_meta_campaigns, get_meta_insights
from inventory.mixins import AccountantRequiredMixin




class MetaDashboardView(AccountantRequiredMixin, TemplateView):
    template_name = "meta_ads_dashboard/dashboard.html"
    login_url = "login"   # change if your login url name is different



    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = date.today()
        default_from = (today - timedelta(days=30)).isoformat()
        default_to = today.isoformat()

        from_date = self.request.GET.get("from", default_from)
        to_date = self.request.GET.get("to", default_to)
        selected_campaigns = self.request.GET.getlist("campaign")

        campaigns_df = get_meta_campaigns(self.ACCESS_TOKEN, self.AD_ACCOUNT_ID)

        campaigns = []
        if isinstance(campaigns_df, pd.DataFrame) and not campaigns_df.empty:
            campaigns_df["id"] = campaigns_df["id"].astype(str)
            campaigns = campaigns_df.to_dict(orient="records")

        insights_df = pd.DataFrame()
        error_message = None

        if selected_campaigns:
            result = get_meta_insights(
                access_token=self.ACCESS_TOKEN,
                ad_account_id=self.AD_ACCOUNT_ID,
                since_date=from_date,
                until_date=to_date,
                level="campaign",
                time_increment="1",
                save_excel=False
            )

            if isinstance(result, pd.DataFrame):
                result["campaign_id"] = result.get("campaign_id", "").astype(str)
                insights_df = result[result["campaign_id"].isin(selected_campaigns)]

                insights_df = insights_df.replace([np.nan, float("nan")], None)

                nested_columns = [
                    "actions",
                    "cost_per_action_type",
                    "action_values",
                    "purchase_roas"
                ]

                for col in nested_columns:
                    if col in insights_df.columns:
                        insights_df[col] = insights_df[col].apply(
                            lambda v: v if isinstance(v, (list, dict)) else None
                        )

                if insights_df.empty:
                    error_message = "⚠️ No data available for the selected campaigns."

            elif isinstance(result, dict) and "error" in result:
                error_message = result["error"].get("message", "API returned an error.")
            else:
                error_message = "❌ Unexpected response format from Meta API."

        chart_data = insights_df.to_dict(orient="records") if not insights_df.empty else []

        context.update({
            "campaigns": campaigns,
            "selected_campaigns": selected_campaigns,
            "chart_data": chart_data,
            "from_date": from_date,
            "to_date": to_date,
            "error": error_message,
        })

        return context