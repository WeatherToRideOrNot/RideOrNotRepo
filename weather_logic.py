import os
import requests
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from openai import OpenAI

# Location midpoint between home and work (Cusworth and Bentley)
LAT = 53.5305
LON = -1.1469

# Fixed forecast times matching OpenWeatherMap data blocks (to avoid mismatch)
COMMUTE_TARGETS = ["06:00", "09:00", "12:00", "15:00", "18:00"]

# Safe thresholds
MIN_TEMP = 5  # °C
MAX_WIND = 20  # m/s (~45 mph)
BAD_WEATHER = ["snow", "thunderstorm", "hail"]
DANGEROUS_RAIN = ["heavy intensity rain", "very heavy rain", "extreme rain"]

# Setup OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def fetch_weather_forecast():
    api_key = os.getenv("OWM_API_KEY")
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={LAT}&lon={LON}&appid={api_key}&units=metric"
    print(f"📡 Requesting forecast from OpenWeatherMap...")
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        print("✅ Forecast received.")
        return response.json()["list"]
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching forecast: {e}")
        return None


def match_time_slots(forecast, target_times):
    # Find forecast entries exactly matching target times
    results = {}
    for entry in forecast:
        time_str = entry["dt_txt"]  # format: "YYYY-MM-DD HH:MM:SS"
        time_obj = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        time_only = time_obj.strftime("%H:%M")
        if time_only in target_times:
            results[time_only] = entry
    return results


def simplify_forecast(entry):
    return {
        "description": entry["weather"][0]["description"],
        "temp": entry["main"]["temp"],
        "wind": entry["wind"]["speed"],
        "visibility": entry.get("visibility", 10000),
        "rain": entry.get("rain", {}).get(
            "3h", 0)  # rain volume in mm over last 3h, default 0 if none
    }


def summarize_forecast(slots):
    return {time: simplify_forecast(data) for time, data in slots.items()}


def assess_weather_conditions(summary):
    safe = True
    reasons = []

    for time, data in summary.items():
        temp = data["temp"]
        wind = data["wind"]
        weather = data["description"].lower()
        visibility = data["visibility"]
        rain = data["rain"]

        if temp < MIN_TEMP:
            reasons.append(f"{time}: Temperature too low ({temp}°C)")
            safe = False
        if wind > MAX_WIND:
            reasons.append(f"{time}: Wind speed too high ({wind} m/s)")
            safe = False
        if any(bad in weather for bad in BAD_WEATHER + DANGEROUS_RAIN):
            reasons.append(f"{time}: Dangerous weather - {weather}")
            safe = False
        if visibility < 3000:
            reasons.append(f"{time}: Poor visibility ({visibility} m)")
            safe = False
        # Consider heavy rain threshold 2mm per 3 hours as risky (adjustable)
        if rain >= 2.0:
            reasons.append(f"{time}: Heavy rain ({rain} mm)")
            safe = False

    return safe, reasons


def get_day_type():
    return "weekday" if datetime.datetime.today().weekday() < 5 else "weekend"


def ai_generate_summary(summary, safe, reasons, is_weekday):
    date_str = datetime.datetime.now().strftime("%A %d %B %Y")

    # Aggregate conditions info for the whole day
    temps = [data["temp"] for data in summary.values()]
    winds = [data["wind"] for data in summary.values()]
    rains = [data["rain"] for data in summary.values()]
    weathers = [data["description"] for data in summary.values()]

    avg_temp = round(sum(temps) / len(temps), 1) if temps else None
    max_wind = max(winds) if winds else None
    total_rain = round(sum(rains), 1) if rains else 0

    # Compose natural sounding summary
    rain_desc = "no rain"
    if total_rain > 5:
        rain_desc = "heavy rain expected"
    elif total_rain > 0:
        rain_desc = "some light rain expected"

    weather_desc = ", ".join(set(weathers))

    prompt = f"""
You are a helpful motorbike safety assistant.

Today is {date_str}.

The weather forecast shows temperatures around {avg_temp}°C, maximum wind speeds up to {max_wind} m/s, and {rain_desc}.
Weather conditions include: {weather_desc}.

If it is safe to ride, generate a friendly, natural summary explaining why, mentioning temperature, wind, rain, and visibility.
If it is not safe, briefly explain the main safety concerns from these reasons:
{'; '.join(reasons)}

Keep the tone calm and factual. Do not break down each time slot individually.
At the end of your message, append either [SAFE] or [NOT SAFE] on a new line.

"""

    print("🧠 Sending summary request to OpenAI...")
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{
                "role":
                "system",
                "content":
                "You help a motorbike commuter decide whether to ride based on weather."
            }, {
                "role": "user",
                "content": prompt
            }],
            temperature=0.7)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"(AI summary failed): {e}"


def send_email(subject, body):
    email_from = os.getenv("EMAIL_FROM")
    email_to = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASSWORD")

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        print("📧 Sending email...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(email_from, password)
            server.send_message(msg)
        print("✅ Email sent.")
    except Exception as e:
        print(f"❌ Email failed: {e}")


def main():
    day_type = get_day_type()
    forecast_data = fetch_weather_forecast()

    if not forecast_data:
        send_email("Ride Assistant Error", "Could not retrieve forecast.")
        return

    slots = match_time_slots(forecast_data, COMMUTE_TARGETS)
    if not slots:
        send_email("Ride Assistant Error", "No matching forecast slots found.")
        return

    summary = summarize_forecast(slots)
    safe, reasons = assess_weather_conditions(summary)

    ai_message = ai_generate_summary(summary,
                                     safe,
                                     reasons,
                                     is_weekday=(day_type == "weekday"))

    decision_line = ai_message.strip().splitlines()[-1].strip()
    if decision_line == "[SAFE]":
        subject = "🏍️ Ride Today: Yes"
        ai_message = ai_message.rsplit("\n",
                                       1)[0].strip()  # Remove last line tag
    elif decision_line == "[NOT SAFE]":
        subject = "⚠️ Ride Today: No"
        ai_message = ai_message.rsplit("\n", 1)[0].strip()
    else:
        subject = "⚠️ Ride Today: Unclear"
        ai_message += "\n\n(Note: Ride safety could not be determined automatically.)"

    send_email(subject, ai_message)


def send_daily_weather_email():
    main()
