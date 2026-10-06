import csv
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

ORIGIN = "BLR"
DESTINATION = "DEL"
DEPARTURE_DATE = "2026-11-29"

TARGET_AIRLINE = "Akasa Air"
TARGET_DEPARTURE = "13:55"
TARGET_ARRIVAL = "16:45"
TIME_TOLERANCE_MINUTES = 15

PRICE_THRESHOLD = 10500

SERPAPI_KEY = os.environ["SERPAPI_KEY"]
EMAIL_USERNAME = os.environ["EMAIL_USERNAME"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]

HISTORY_FILE = Path("data/prices.csv")


def minutes(time_string):
    # Google Flights can return either HH:MM
    # or YYYY-MM-DD HH:MM
    time_part = time_string.strip().split()[-1]
    h, m = map(int, time_part.split(":")[:2])
    return h * 60 + m


def time_distance(a, b):
    return abs(minutes(a) - minutes(b))


def send_email(match):
    msg = EmailMessage()
    msg["Subject"] = (
        f"🚨 Akasa BLR → DEL fare below ₹{PRICE_THRESHOLD:,} "
        f"— ₹{match['price']:,.0f}"
    )
    msg["From"] = EMAIL_USERNAME
    msg["To"] = EMAIL_TO

    msg.set_content(
        f"""Akasa flight price alert!

Route: Bengaluru (BLR) → New Delhi (DEL)
Date: {DEPARTURE_DATE}
Airline: {match['airline']}
Flight: {match['flight_number']}
Time: {match['departure']} → {match['arrival']}

Current fare: ₹{match['price']:,.0f}
Your target: ₹{PRICE_THRESHOLD:,}

The fare is at or below your target. Check the booking site immediately.
"""
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(EMAIL_USERNAME, EMAIL_APP_PASSWORD)
        smtp.send_message(msg)


def search_flights():
    params = {
        "engine": "google_flights",
        "departure_id": ORIGIN,
        "arrival_id": DESTINATION,
        "outbound_date": DEPARTURE_DATE,
        "type": "2",
        "currency": "INR",
        "hl": "en",
        "gl": "in",
        "api_key": SERPAPI_KEY,
    }

    response = requests.get(
        "https://serpapi.com/search.json",
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def iter_flights(data):
    for group_name in ("best_flights", "other_flights"):
        for offer in data.get(group_name, []):
            yield offer


def find_target_flights(data):
    matches = []

    for offer in iter_flights(data):
        price = offer.get("price")
        if price is None:
            continue

        for flight in offer.get("flights", []):
            airline = flight.get("airline", "")
            departure = flight.get("departure_airport", {}).get("time", "")
            arrival = flight.get("arrival_airport", {}).get("time", "")
            flight_number = flight.get("flight_number", "")

            if TARGET_AIRLINE.lower() not in airline.lower():
                continue

            if not departure or not arrival:
                continue

            if time_distance(departure, TARGET_DEPARTURE) > TIME_TOLERANCE_MINUTES:
                continue

            if time_distance(arrival, TARGET_ARRIVAL) > TIME_TOLERANCE_MINUTES:
                continue

            matches.append({
                "price": float(price),
                "airline": airline,
                "flight_number": flight_number,
                "departure": departure,
                "arrival": arrival,
            })
            break

    return matches


def append_history(match):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    exists = HISTORY_FILE.exists()

    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not exists:
            writer.writerow([
                "checked_at_utc",
                "date",
                "airline",
                "flight_number",
                "departure",
                "arrival",
                "price_inr",
            ])

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            DEPARTURE_DATE,
            match["airline"],
            match["flight_number"],
            match["departure"],
            match["arrival"],
            match["price"],
        ])


def main():
    data = search_flights()
    matches = find_target_flights(data)

    if not matches:
        print(
            f"No matching Akasa flight found for {DEPARTURE_DATE} "
            f"around {TARGET_DEPARTURE}-{TARGET_ARRIVAL}."
        )
        return

    match = min(matches, key=lambda x: x["price"])
    append_history(match)

    print(
        f"Found {match['airline']} {match['flight_number']}: "
        f"{match['departure']} -> {match['arrival']} | "
        f"₹{match['price']:,.0f}"
    )

    if match["price"] <= PRICE_THRESHOLD:
        send_email(match)
        print("Price threshold reached. Email sent.")


if __name__ == "__main__":
    main()
