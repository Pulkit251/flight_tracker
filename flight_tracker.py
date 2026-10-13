import csv
import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import requests

SERPAPI_KEY = os.environ["SERPAPI_KEY"]
EMAIL_USERNAME = os.environ["EMAIL_USERNAME"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]

# Local departure times in India (inclusive start, exclusive end).
TIME_WINDOWS = {
    "morning": (5 * 60, 12 * 60),
    "afternoon": (12 * 60, 17 * 60),
    "evening": (17 * 60, 24 * 60),
}

TRIPS = [
    {
        "name": "Navi Mumbai to Bengaluru",
        "origin": "NMI",
        "destination": "BLR",
        "date": "2026-11-23",
        "max_price": 5500,
        "windows": ("morning", "evening"),
        "history_file": "prices_nmi_blr.csv",
    },
    {
        "name": "Bengaluru to Navi Mumbai (return)",
        "origin": "BLR",
        "destination": "NMI",
        "date": "2026-11-27",
        "max_price": 6000,
        "windows": ("afternoon", "evening"),
        "history_file": "prices_blr_nmi.csv",
    },
]

DATA_DIR = Path("data")
ALERT_STATE_FILE = DATA_DIR / "alert_state.json"


def parse_minutes(value):
    """Parse SerpAPI times in 24-hour or 12-hour formats."""
    from datetime import datetime

    value = str(value).strip()
    candidates = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%H:%M",
        "%I:%M %p",
    )
    for fmt in candidates:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            continue
    raise ValueError(f"Unrecognized flight time format: {value!r}")


def in_preferred_window(time_string, windows):
    try:
        departure = parse_minutes(time_string)
    except (ValueError, IndexError):
        return False
    return any(
        TIME_WINDOWS[window][0] <= departure < TIME_WINDOWS[window][1]
        for window in windows
    )


def search_flights(trip):
    params = {
        "engine": "google_flights",
        "departure_id": trip["origin"],
        "arrival_id": trip["destination"],
        "outbound_date": trip["date"],
        "type": "2",  # One-way search
        "currency": "INR",
        "hl": "en",
        "gl": "in",
        "api_key": SERPAPI_KEY,
    }
    response = requests.get(
        "https://serpapi.com/search.json", params=params, timeout=60
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


def airport_code(airport):
    return (airport or {}).get("id", "").upper()


def find_flights(data, trip):
    matches = []
    for group in ("best_flights", "other_flights"):
        for offer in data.get(group, []):
            legs = offer.get("flights", [])
            if len(legs) != 1:  # Direct flights only
                continue
            leg = legs[0]
            dep = leg.get("departure_airport") or {}
            arr = leg.get("arrival_airport") or {}

            # Strict airport-code checks prevent silently mixing airports.
            if airport_code(dep) != trip["origin"]:
                continue
            if airport_code(arr) != trip["destination"]:
                continue

            departure = dep.get("time", "")
            arrival = arr.get("time", "")
            if not departure or not arrival:
                continue
            if not departure.startswith(trip["date"]):
                continue
            if not in_preferred_window(departure, trip["windows"]):
                continue

            price = offer.get("price")
            if price is None:
                continue
            try:
                price = float(price)
            except (ValueError, TypeError):
                continue

            matches.append({
                "price": price,
                "airline": leg.get("airline", "Unknown"),
                "flight_number": leg.get("flight_number", "Unknown"),
                "departure": departure,
                "arrival": arrival,
            })
    return sorted(matches, key=lambda item: item["price"])


def append_history(trip, match):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history_file = DATA_DIR / trip["history_file"]
    is_new = not history_file.exists() or history_file.stat().st_size == 0
    with history_file.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow([
                "checked_at_utc", "trip", "date", "origin", "destination",
                "airline", "flight_number", "departure", "arrival", "price_inr",
            ])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(), trip["name"], trip["date"],
            trip["origin"], trip["destination"], match["airline"],
            match["flight_number"], match["departure"], match["arrival"],
            match["price"],
        ])


def google_flights_url(trip):
    search = (
        f"Flights from {trip['origin']} to {trip['destination']} "
        f"on {trip['date']}"
    )
    return "https://www.google.com/travel/flights?q=" + quote(search)


def send_email(trip, match):
    message = EmailMessage()
    message["Subject"] = (
        f"Flight alert: {trip['origin']} → {trip['destination']} "
        f"₹{match['price']:,.0f} (target ₹{trip['max_price']:,})"
    )
    message["From"] = EMAIL_USERNAME
    message["To"] = EMAIL_TO
    message.set_content(
        f"{trip['name']} flight price alert!\n\n"
        f"Route: {trip['origin']} → {trip['destination']}\n"
        f"Date: {trip['date']}\n"
        f"Airline: {match['airline']}\n"
        f"Flight: {match['flight_number']}\n"
        f"Departure: {match['departure']}\n"
        f"Arrival: {match['arrival']}\n"
        f"Current fare: ₹{match['price']:,.0f}\n"
        f"Maximum target: ₹{trip['max_price']:,}\n\n"
        f"Google Flights search: {google_flights_url(trip)}\n\n"
        "Recheck the final fare, baggage and availability before booking.\n"
    )
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(EMAIL_USERNAME, EMAIL_APP_PASSWORD)
        smtp.send_message(message)


def load_alert_state():
    if ALERT_STATE_FILE.exists():
        try:
            return json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            print("Warning: alert state unreadable; starting fresh.")
    return {}


def save_alert_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = ALERT_STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(ALERT_STATE_FILE)


def main():
    state = load_alert_state()
    failed = False

    for trip in TRIPS:
        print(
            f"\nChecking {trip['name']}: {trip['origin']} → "
            f"{trip['destination']} ({trip['date']})"
        )
        try:
            data = search_flights(trip)
            matches = find_flights(data, trip)
            if not matches:
                print("No direct flights found in the preferred departure windows.")
                continue

            cheapest = matches[0]
            append_history(trip, cheapest)
            print(
                f"Cheapest: {cheapest['airline']} {cheapest['flight_number']} "
                f"at {cheapest['departure']} — ₹{cheapest['price']:,.0f}"
            )

            eligible = [item for item in matches if item["price"] <= trip["max_price"]]
            if not eligible:
                print("Price above threshold. No email.")
                continue

            match = eligible[0]
            key = (
                f"{trip['origin']}-{trip['destination']}-{trip['date']}-"
                f"{match['flight_number']}-{match['departure']}"
            )
            previous = state.get(key)
            # Alert once at a given price; send again only if that fare drops.
            if previous is not None and match["price"] >= float(previous):
                print("Already alerted for this flight at this or a lower price.")
                continue

            send_email(trip, match)
            state[key] = match["price"]
            save_alert_state(state)
            print("Threshold reached. Email sent.")

        except (
            requests.RequestException, RuntimeError, smtplib.SMTPException,
            OSError, ValueError,
        ) as exc:
            failed = True
            print(f"ERROR for {trip['name']}: {exc}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
