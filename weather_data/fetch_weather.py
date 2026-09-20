import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("WEATHER_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "WEATHER_API_KEY not found. Put it in the .env file."
    )

INPUT_FILE = BASE_DIR / "locations.csv"
OUTPUT_FILE = BASE_DIR / "weather_current_forecast.csv"

API_URL = "https://api.weatherapi.com/v1/forecast.json"


def fetch_location(row):
    location_id = row["Location_ID"]
    requested_lat = float(row["Latitude"])
    requested_lon = float(row["Longitude"])
    query_time = datetime.now().astimezone().isoformat(timespec="seconds")
    params = {
        "key": API_KEY,
        "q": f"{requested_lat},{requested_lon}",
        "days": 2,
        "aqi": "no",
        "alerts": "yes",
    }

    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    location = data["location"]
    current = data["current"]

    # WeatherAPI local observation time
    observation_time = datetime.strptime(
        current["last_updated"], "%Y-%m-%d %H:%M"
    )

    # Collect hourly forecasts from today + tomorrow
    hourly = []

    for day in data["forecast"]["forecastday"]:
        for hour in day["hour"]:
            hour_time = datetime.strptime(
                hour["time"], "%Y-%m-%d %H:%M"
            )

            if hour_time >= observation_time:
                hourly.append({
                    "time": hour_time,
                    "precip_mm": float(hour.get("precip_mm", 0.0)),
                    "chance_of_rain": hour.get("chance_of_rain"),
                })

    # Next 24 hours
    next_24h = [
        h for h in hourly
        if h["time"] < observation_time + timedelta(hours=24)
    ]

    rainfall_1h = next_24h[0]["precip_mm"] if len(next_24h) >= 1 else None

    rainfall_3h = (
        sum(h["precip_mm"] for h in next_24h[:3])
        if len(next_24h) >= 3
        else None
    )

    rainfall_6h = (
        sum(h["precip_mm"] for h in next_24h[:6])
        if len(next_24h) >= 6
        else None
    )

    rainfall_24h = (
        sum(h["precip_mm"] for h in next_24h[:24])
        if len(next_24h) >= 24
        else None
    )

    max_hourly = (
        max(h["precip_mm"] for h in next_24h)
        if next_24h
        else None
    )

    peak_time = None

    if next_24h:
        peak = max(next_24h, key=lambda x: x["precip_mm"])

        if peak["precip_mm"] > 0:
            peak_time = peak["time"].strftime("%Y-%m-%d %H:%M")

    return {
        "Location_ID": location_id,
        "Requested_Latitude": requested_lat,
        "Requested_Longitude": requested_lon,
        "Weather_Query_Time": query_time,
        "Matched_Location": location["name"],
        "Matched_Latitude": location["lat"],
        "Matched_Longitude": location["lon"],

        "Current_Rainfall_mm": current.get("precip_mm"),
        "Current_Rain_Chance_pct": current.get("chance_of_rain"),

        "Forecast_1h_Rainfall_mm": rainfall_1h,
        "Forecast_3h_Rainfall_mm": rainfall_3h,
        "Forecast_6h_Rainfall_mm": rainfall_6h,
        "Forecast_24h_Rainfall_mm": rainfall_24h,

        "Max_Hourly_Rainfall_mm": max_hourly,
        "Peak_Rainfall_Time": peak_time,

        "Observation_Time": current.get("last_updated"),

        "Weather_Source": "WeatherAPI",
        "Data_Quality": (
            "Live WeatherAPI observation + hourly forecast; "
            "matched weather location may differ from requested coordinate"
        ),
    }


def main():
    locations = pd.read_csv(INPUT_FILE)

    results = []

    for index, row in locations.iterrows():
        location_id = row["Location_ID"]

        print(
            f"[{index + 1}/{len(locations)}] "
            f"Fetching {location_id}..."
        )

        try:
            result = fetch_location(row)
            results.append(result)

        except requests.RequestException as exc:
            print(f"  ERROR for {location_id}: {exc}")

        except (KeyError, ValueError, TypeError) as exc:
            print(f"  DATA ERROR for {location_id}: {exc}")

    if not results:
        raise RuntimeError("No weather data was successfully retrieved.")

    output = pd.DataFrame(results)

    output.to_csv(OUTPUT_FILE, index=False)

    print("\nDone!")
    print(f"Saved to: {OUTPUT_FILE}")
    print(f"Rows collected: {len(output)}")


if __name__ == "__main__":
    main()