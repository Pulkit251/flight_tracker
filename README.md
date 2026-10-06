# Akasa BLR → DEL Flight Price Tracker — Email Version

Tracks the Akasa Air flight matching:

- BLR → DEL
- 29 November 2026
- Around 13:55 → 16:45
- Fare target: ₹8,000 or below

The GitHub Actions workflow checks every 3 hours and sends an email when the
fare is at or below ₹8,000.

## 1. SerpApi

Create an account at:

https://serpapi.com/

Get your API key.

The workflow uses the Google Flights API through SerpApi.

## 2. Gmail App Password

If you use Gmail:

1. Enable 2-Step Verification on your Google account.
2. Open your Google Account security settings.
3. Create an App Password.
4. Use the generated 16-character password as `EMAIL_APP_PASSWORD`.

Do NOT use your normal Gmail password.

The sender account is the Gmail account represented by `EMAIL_USERNAME`.

## 3. GitHub Secrets

In your repository:

Settings → Secrets and variables → Actions → New repository secret

Add:

SERPAPI_KEY
EMAIL_USERNAME
EMAIL_APP_PASSWORD
EMAIL_TO

Example:

EMAIL_USERNAME = yourgmail@gmail.com
EMAIL_TO = yourpersonalemail@example.com

The password is stored only as a GitHub Secret and is not placed in the Python source.

## 4. Upload files

Repository structure:

.github/workflows/track-flight.yml
flight_tracker.py
data/

## 5. Test it

Go to:

Actions → Track Akasa Flight Price → Run workflow

Check the workflow log.

## 6. Automatic checks

The workflow runs every 3 hours.

At each check it records the observed fare in:

data/prices.csv

When the fare is <= ₹8,000, it sends an email.

## Important

This records prices from the moment the tracker starts. It cannot reconstruct
historical prices from before the tracker was running.

Also, a fare returned by Google Flights may change before checkout. Treat the
email as an alert to check the booking price immediately.
