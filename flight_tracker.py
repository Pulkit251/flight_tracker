
import csv
import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import requests


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SERPAPI_KEY = os.environ["SERPAPI_KEY"]
EMAIL_USERNAME = os.environ["EMAIL_USERNAME"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO = os.environ["EMAIL_TO"]

SERPAPI_URL = "https://serpapi.com/search.json"

# Departure times are interpreted in the departure airport's local time.
# DBR is in India, so these are IST times.
TIME_WINDOWS = {
    "morning": (5 * 60, 12 * 60),
    "afternoon": (12 * 60, 17 * 60),
    "evening": (17 * 60, 24 * 60),
}

TRIPS = [
    {
        "name": "Darbhanga to Delhi",
        "origin": "DBR",
        "destination": "DEL",
        "date": "2026-11-15",
        "max_price": 6500,
        "windows": ("morning", "afternoon", "evening"),
        "exclude_airlines": ("spicejet",),
        "history_file": "prices_dbr_del.csv",
    },
]

DATA_DIR = Path("data")
ALERT_STATE_FILE = DATA_DIR / "alert_state.json"

CSV_COLUMNS = [
    "checked_at_utc",
    "trip",
    "date",
    "origin",
    "destination",
    "airline",
    "flight_number",
    "departure",
    "arrival",
    "price_inr",
]


# --------------------------------------------------
# DATE AND TIME HELPERS
# --------------------------------------------------

def parse_datetime(value):
    """Parse common SerpAPI flight date/time formats."""

    value = str(value).strip()

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        pass

    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%H:%M",
        "%I:%M %p",
    )

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unrecognized flight time: {value!r}")


def in_preferred_window(time_string, windows):
    """Check whether a flight departs in the configured time windows."""

    try:
        departure = parse_datetime(time_string)
        departure_minutes = departure.hour * 60 + departure.minute
    except (ValueError, TypeError):
        return False

    for window in windows:
        if window not in TIME_WINDOWS:
            print(f"WARNING: Unknown departure window: {window}")
            continue

        start, end = TIME_WINDOWS[window]

        if start <= departure_minutes < end:
            return True

    return False


# --------------------------------------------------
# FLIGHT SEARCH
# --------------------------------------------------

def search_flights(trip):
    params = {
        "engine": "google_flights",
        "departure_id": trip["origin"],
        "arrival_id": trip["destination"],
        "outbound_date": trip["date"],
        "type": "2",
        "currency": "INR",
        "hl": "en",
        "gl": "in",
        "api_key": SERPAPI_KEY,
    }

    print("Calling SerpAPI...")

    response = requests.get(
        SERPAPI_URL,
        params=params,
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):
        raise RuntimeError(data["error"])

    return data


def airport_code(airport):
    return str((airport or {}).get("id") or "").upper()


def find_flights(data, trip):
    """Return sorted matching direct flights."""

    matches = []
    stats = {
        "offers": 0,
        "connecting": 0,
        "wrong_airport": 0,
        "wrong_date": 0,
        "excluded_airline": 0,
        "wrong_time": 0,
        "missing_price": 0,
    }

    excluded_airlines = trip.get("exclude_airlines", ())

    for group in ("best_flights", "other_flights"):
        for offer in data.get(group, []):
            stats["offers"] += 1

            legs = offer.get("flights", [])

            # Direct flights only.
            if len(legs) != 1:
                stats["connecting"] += 1
                continue

            leg = legs[0]

            airline = str(leg.get("airline") or "Unknown").strip()

            if any(
                excluded in airline.lower()
                for excluded in excluded_airlines
            ):
                stats["excluded_airline"] += 1
                continue

            dep = leg.get("departure_airport") or {}
            arr = leg.get("arrival_airport") or {}

            if (
                airport_code(dep) != trip["origin"]
                or airport_code(arr) != trip["destination"]
            ):
                stats["wrong_airport"] += 1
                continue

            departure = dep.get("time", "")
            arrival = arr.get("time", "")

            if not departure or not arrival:
                stats["wrong_date"] += 1
                continue

            try:
                departure_dt = parse_datetime(departure)
            except (ValueError, TypeError):
                stats["wrong_date"] += 1
                continue

            if departure_dt.strftime("%Y-%m-%d") != trip["date"]:
                stats["wrong_date"] += 1
                continue

            if not in_preferred_window(
                departure,
                trip["windows"],
            ):
                stats["wrong_time"] += 1
                continue

            price = offer.get("price")

            if price is None:
                stats["missing_price"] += 1
                continue

            try:
                price = float(price)
            except (ValueError, TypeError):
                stats["missing_price"] += 1
                continue

            matches.append({
                "price": price,
                "airline": airline,
                "flight_number": (
                    leg.get("flight_number") or "Unknown"
                ),
                "departure": departure,
                "arrival": arrival,
            })

    # Sort cheapest first.
    matches.sort(key=lambda item: item["price"])

    print(f"Offers inspected: {stats['offers']}")
    print(f"Connecting flights excluded: {stats['connecting']}")
    print(f"Wrong airports: {stats['wrong_airport']}")
    print(f"Wrong dates: {stats['wrong_date']}")
    print(f"Excluded airlines: {stats['excluded_airline']}")
    print(f"Outside departure windows: {stats['wrong_time']}")
    print(f"Missing/invalid prices: {stats['missing_price']}")
    print(f"Matching direct flights: {len(matches)}")

    return matches


# --------------------------------------------------
# PRICE HISTORY
# --------------------------------------------------

def append_history(trip, match):
    """Save only the cheapest matching flight per execution."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    history_file = DATA_DIR / trip["history_file"]

    is_new = (
        not history_file.exists()
        or history_file.stat().st_size == 0
    )

    with history_file.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:

        writer = csv.writer(handle)

        if is_new:
            writer.writerow(CSV_COLUMNS)

        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            trip["name"],
            trip["date"],
            trip["origin"],
            trip["destination"],
            match["airline"],
            match["flight_number"],
            match["departure"],
            match["arrival"],
            match["price"],
        ])

    print(f"Price history saved: {history_file}")


# --------------------------------------------------
# ALERT STATE
# --------------------------------------------------

def load_alert_state():
    if not ALERT_STATE_FILE.exists():
        return {}

    try:
        state = json.loads(
            ALERT_STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(state, dict):
            raise ValueError("Alert state must be a JSON object")

        return state

    except (ValueError, OSError) as exc:
        # Do not silently reset alert state. Doing so could
        # generate duplicate emails for previously alerted fares.
        raise RuntimeError(
            f"Unable to read alert state: {exc}"
        ) from exc


def save_alert_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temporary = ALERT_STATE_FILE.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )

    temporary.replace(ALERT_STATE_FILE)


def alert_key(trip, match):
    """Identify a particular flight consistently."""

    return (
        f"{trip['origin']}-{trip['destination']}-"
        f"{trip['date']}-"
        f"{match['airline']}-"
        f"{match['flight_number']}-"
        f"{match['departure']}"
    )


# --------------------------------------------------
# EMAIL
# --------------------------------------------------

def google_flights_url(trip):
    search = (
        f"Flights from {trip['origin']} to "
        f"{trip['destination']} on {trip['date']}"
    )

    return (
        "https://www.google.com/travel/flights?q="
        + quote(search)
    )


def send_email(trip, match, previous_price=None):
    message = EmailMessage()

    message["Subject"] = (
        f"Flight alert: {trip['origin']} → "
        f"{trip['destination']} "
        f"₹{match['price']:,.0f}"
    )

    message["From"] = EMAIL_USERNAME
    message["To"] = EMAIL_TO

    previous_text = (
        f"Previous alerted price: ₹{previous_price:,.0f}\n"
        if previous_price is not None
        else "This is the first qualifying price alert.\n"
    )

    message.set_content(
        f"Darbhanga to Delhi flight price alert!\n\n"
        f"Route: {trip['origin']} → {trip['destination']}\n"
        f"Date: {trip['date']}\n"
        f"Airline: {match['airline']}\n"
        f"Flight: {match['flight_number']}\n"
        f"Departure: {match['departure']}\n"
        f"Arrival: {match['arrival']}\n\n"
        f"Current fare: ₹{match['price']:,.0f}\n"
        f"Target fare: ₹{trip['max_price']:,}\n"
        f"{previous_text}\n"
        f"Google Flights:\n{google_flights_url(trip)}\n\n"
        "Please verify the final fare, baggage allowance "
        "and availability before booking.\n"
    )

    print("Connecting to Gmail SMTP...")

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30,
    ) as smtp:

        smtp.login(
            EMAIL_USERNAME,
            EMAIL_APP_PASSWORD,
        )

        smtp.send_message(message)

    print("Email sent successfully.")


# --------------------------------------------------
# MAIN EXECUTION
# --------------------------------------------------

def process_trip(trip, state):
    print("\n" + "=" * 55)
    print(f"Checking: {trip['name']}")
    print(
        f"Route: {trip['origin']} → {trip['destination']}"
    )
    print(f"Travel date: {trip['date']}")
    print(f"Target price: ₹{trip['max_price']:,}")
    print("=" * 55)

    data = search_flights(trip)

    matches = find_flights(data, trip)

    if not matches:
        print("No matching direct flights found.")
        return

    # Cheapest matching flight.
    cheapest = matches[0]

    print("\nCHEAPEST MATCHING FLIGHT")
    print(f"Airline: {cheapest['airline']}")
    print(f"Flight: {cheapest['flight_number']}")
    print(f"Departure: {cheapest['departure']}")
    print(f"Arrival: {cheapest['arrival']}")
    print(f"Price: ₹{cheapest['price']:,.0f}")

    # Save history even when price is above target.
    append_history(trip, cheapest)

    if cheapest["price"] > trip["max_price"]:
        print(
            f"Price above target by "
            f"₹{cheapest['price'] - trip['max_price']:,.0f}."
        )
        print("No email will be sent.")
        return

    key = alert_key(trip, cheapest)

    previous_price = state.get(key)

    if previous_price is not None:
        previous_price = float(previous_price)

        if cheapest["price"] >= previous_price:
            print(
                f"Already alerted at ₹{previous_price:,.0f} "
                f"or lower. Current price: "
                f"₹{cheapest['price']:,.0f}"
            )
            return

        print(
            f"PRICE DROP DETECTED: "
            f"₹{previous_price:,.0f} → "
            f"₹{cheapest['price']:,.0f}"
        )

    else:
        print("First qualifying fare detected!")

    # Send email first. Update alert state only after
    # successful delivery through the SMTP server.
    send_email(
        trip,
        cheapest,
        previous_price,
    )

    state[key] = cheapest["price"]

    save_alert_state(state)

    print("Alert state updated successfully.")


def main():
    state = load_alert_state()
    failed = False

    for trip in TRIPS:
        try:
            process_trip(trip, state)

        except (
            requests.RequestException,
            RuntimeError,
            smtplib.SMTPException,
            OSError,
            ValueError,
            KeyError,
        ) as exc:

            failed = True

            print(
                f"ERROR while checking {trip['name']}: "
                f"{type(exc).__name__}: {exc}"
            )

    if failed:
        raise SystemExit(1)

    print("\nAll flight checks completed successfully.")


if __name__ == "__main__":
    main()
