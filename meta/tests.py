import requests
import pandas as pd
import json
from datetime import datetime




def get_meta_insights(access_token, ad_account_id, since_date, until_date,
                      level="campaign", time_increment="1", save_excel=False):
    """
    Fetches Meta Ads Insights with valid fields (v19+ API).
    Includes clicks, cost, impressions, conversions, and ROAS metrics.
    """
    url = f"https://graph.facebook.com/v19.0/act_{ad_account_id}/insights"

    # ✅ Valid field set for Ads Insights API (v19+)
    fields = [
        "date_start,date_stop",
        "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name",
        "objective,attribution_setting,buying_type",
        "impressions,reach,frequency,clicks,unique_clicks,ctr,unique_ctr",
        "spend,cpc,cpm,cpp,account_currency",
        "inline_link_clicks,unique_inline_link_clicks",
        "actions,action_values,conversions,conversion_values,purchase_roas",
        "cost_per_action_type,cost_per_conversion"
    ]

    params = {
        "fields": ",".join(fields),
        "level": level,
        "time_increment": time_increment,
        "time_range": json.dumps({"since": since_date, "until": until_date}),
        "access_token": access_token,
        "limit": 500
    }

    print("⏳ Fetching data from Meta Graph API...")

    all_data = []
    next_page = True
    response = requests.get(url, params=params)
    data = response.json()

    # Pagination loop
    while next_page:
        if "error" in data:
            print("❌ API Error:", data["error"]["message"])
            return data

        if "data" not in data or len(data["data"]) == 0:
            print("⚠️ No data found for the given range.")
            return data

        all_data.extend(data["data"])

        # Pagination handling
        if "paging" in data and "next" in data["paging"]:
            next_url = data["paging"]["next"]
            response = requests.get(next_url)
            data = response.json()
        else:
            next_page = False

    df = pd.DataFrame(all_data)
    print(f"✅ Retrieved {len(df)} rows of data.")

    # 💾 Optional: Save to Excel
    # if save_excel:
    #     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    #     filename = f"meta_insights_{level}_{since_date}_to_{until_date}_{timestamp}.xlsx"
    #     df.to_excel(filename, index=False)
    #     print(f"💾 Data saved to: {filename}")
    #
    return df
    #

# 🧠 Example usage
if __name__ == "__main__":
    ACCESS_TOKEN = "EAAIwc9LtQ6IBP8XFbQKbt9rjblr5xdcivuYqrLNevAZBwmGAlfjwuSmycUsbYFf4SF0nnZBEczlKySKGTjYSACUx5lrtNZCpOo3zbZBnGM9SSC1IJq36hvOD9XRus265KYA4FZBk9srEX8XelEBtC5sgezZAiPTbtThvKK3i6xbgWk0WNncvekIXwC0DuB523ODuEscmsdf5zPRXSaUXbpr0IaeSLPSWcBOTTTKwBOZAQDtyBZBTeRgTOOpbux7jGF1jxDgORg5uBWAoJfVyZBQZDZD"
    AD_ACCOUNT_ID = "1103061437498849"

    df = get_meta_insights(
        access_token=ACCESS_TOKEN,
        ad_account_id=AD_ACCOUNT_ID,
        since_date="2025-05-12",
        until_date="2025-11-10",
        level="campaign",
        time_increment="1",
        save_excel=True,
    )

    # print(df.head())



def get_meta_campaigns(access_token, ad_account_id):
    """
    Fetch all campaigns for the given ad account.
    Returns campaign_id, name, status, objective, and start/end dates.
    """
    url = f"https://graph.facebook.com/v19.0/act_{ad_account_id}/campaigns"
    params = {
        "fields": "id,name,status,objective,start_time,stop_time",
        "access_token": access_token,
        "limit": 200
    }

    print("⏳ Fetching campaign list from Meta Graph API...")

    all_campaigns = []
    next_page = True
    response = requests.get(url, params=params)
    data = response.json()

    while next_page:
        if "error" in data:
            print("❌ API Error:", data["error"]["message"])
            return data

        if "data" not in data:
            print("⚠️ No campaigns found.")
            return data

        all_campaigns.extend(data["data"])

        if "paging" in data and "next" in data["paging"]:
            next_url = data["paging"]["next"]
            response = requests.get(next_url)
            data = response.json()
        else:
            next_page = False

    df = pd.DataFrame(all_campaigns)
    print(f"✅ Retrieved {len(df)} campaigns.")

    return df


if __name__ == "__main__":
    ACCESS_TOKEN = "EAAIwc9LtQ6IBP8XFbQKbt9rjblr5xdcivuYqrLNevAZBwmGAlfjwuSmycUsbYFf4SF0nnZBEczlKySKGTjYSACUx5lrtNZCpOo3zbZBnGM9SSC1IJq36hvOD9XRus265KYA4FZBk9srEX8XelEBtC5sgezZAiPTbtThvKK3i6xbgWk0WNncvekIXwC0DuB523ODuEscmsdf5zPRXSaUXbpr0IaeSLPSWcBOTTTKwBOZAQDtyBZBTeRgTOOpbux7jGF1jxDgORg5uBWAoJfVyZBQZDZD"
    AD_ACCOUNT_ID = "1103061437498849"

    # 🔹 Step 1: Fetch campaign list
    campaigns_df = get_meta_campaigns(ACCESS_TOKEN, AD_ACCOUNT_ID)
    print(campaigns_df[["id", "name", "status"]])

    # 🔹 Step 2: Pick one campaign (example)
    campaign_id = campaigns_df.iloc[0]["id"]

    # 🔹 Step 3: Fetch insights for that campaign
    insights_df = get_meta_insights(
        access_token=ACCESS_TOKEN,
        ad_account_id=AD_ACCOUNT_ID,
        since_date="2025-05-12",
        until_date="2025-11-10",
        level="campaign",
        time_increment="1",
        save_excel=True
    )

    print(insights_df.head())