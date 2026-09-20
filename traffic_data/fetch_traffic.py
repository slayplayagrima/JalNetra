import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("MAPPLS_API_KEY")

if not API_KEY:
    raise ValueError("MAPPLS_API_KEY not found in .env")

# Fixed Gurugram reference point
ORIGIN_LON = 77.0266
ORIGIN_LAT = 28.4595

BASE_URL = "https://route.mappls.com/route/direction"

locations = pd.read_csv("locations.csv")

# Support your existing ID column
locations = locations.rename(columns={"ID": "Location_ID"})

results = []

for _, row in locations.iterrows():

    location_id = row["Location_ID"]
    lat = row["Latitude"]
    lon = row["Longitude"]

    coordinates = f"{ORIGIN_LON},{ORIGIN_LAT};{lon},{lat}"

    common_params = {
        "access_token": API_KEY,
        "region": "ind",
        "rtype": 0
    }

    try:
        # -----------------------------------
        # 1. Normal route (without traffic)
        # -----------------------------------
        adv_url = f"{BASE_URL}/route_adv/driving/{coordinates}"

        adv_response = requests.get(
            adv_url,
            params=common_params,
            timeout=20
        )

        adv_data = adv_response.json()

        if adv_data.get("code", "").lower() != "ok":
            raise Exception(f"route_adv error: {adv_data}")

        normal_route = adv_data["routes"][0]

        normal_duration = normal_route["duration"]
        distance = normal_route["distance"]

        # -----------------------------------
        # 2. Live traffic route
        # -----------------------------------
        eta_url = f"{BASE_URL}/route_eta/driving/{coordinates}"

        eta_response = requests.get(
            eta_url,
            params=common_params,
            timeout=20
        )

        eta_data = eta_response.json()

        if eta_data.get("code", "").lower() != "ok":
            raise Exception(f"route_eta error: {eta_data}")

        traffic_route = eta_data["routes"][0]

        traffic_duration = traffic_route["duration"]

        # -----------------------------------
        # 3. Derived traffic features
        # -----------------------------------

        delay_seconds = max(
            0,
            traffic_duration - normal_duration
        )

        if normal_duration > 0:
            congestion_ratio = traffic_duration / normal_duration
        else:
            congestion_ratio = None

        if traffic_duration > 0:
            avg_speed_kmh = (
                distance / traffic_duration
            ) * 3.6
        else:
            avg_speed_kmh = None

        # Simple derived category
        if congestion_ratio is None:
            traffic_level = "Unknown"
        elif congestion_ratio < 1.10:
            traffic_level = "Low"
        elif congestion_ratio < 1.30:
            traffic_level = "Moderate"
        elif congestion_ratio < 1.60:
            traffic_level = "High"
        else:
            traffic_level = "Severe"

        results.append({
            "Location_ID": location_id,
            "Latitude": lat,
            "Longitude": lon,

            "Route_Distance_m": round(distance, 2),

            "Normal_Duration_sec": round(normal_duration, 2),
            "Traffic_Duration_sec": round(traffic_duration, 2),

            "Traffic_Delay_sec": round(delay_seconds, 2),
            "Congestion_Ratio": round(congestion_ratio, 3)
                if congestion_ratio is not None else None,

            "Traffic_Avg_Speed_kmh": round(avg_speed_kmh, 2)
                if avg_speed_kmh is not None else None,

            "Traffic_Level": traffic_level,

            "Traffic_Source": "Mappls Route ETA + Route ADV",
            "Data_Quality": "Live traffic-derived route feature",

            "Time_Observed": pd.Timestamp.now(
                tz="Asia/Kolkata"
            ).isoformat()
        })

        print(
            f"{location_id}: "
            f"{traffic_level} | "
            f"delay={delay_seconds:.1f}s"
        )

    except Exception as e:

        print(f"{location_id}: ERROR -> {e}")

        results.append({
            "Location_ID": location_id,
            "Latitude": lat,
            "Longitude": lon,
            "Route_Distance_m": None,
            "Normal_Duration_sec": None,
            "Traffic_Duration_sec": None,
            "Traffic_Delay_sec": None,
            "Congestion_Ratio": None,
            "Traffic_Avg_Speed_kmh": None,
            "Traffic_Level": "Unknown",
            "Traffic_Source": "Mappls Route ETA + Route ADV",
            "Data_Quality": f"API error: {str(e)[:100]}",
            "Time_Observed": pd.Timestamp.now(
                tz="Asia/Kolkata"
            ).isoformat()
        })

    # Avoid hammering API
    time.sleep(0.2)


df = pd.DataFrame(results)

df.to_csv(
    "traffic_data.csv",
    index=False
)

print("\nDONE!")
print(f"Saved {len(df)} rows to traffic_data.csv")