from flask import Flask
import datetime
import pytz
from weather_logic import send_daily_weather_email  # we'll move your working logic here

app = Flask(__name__)


@app.route("/")
def home():
    return "Motorbike Weather Assistant is running!"


@app.route("/run")
def run_script():
    now = datetime.datetime.now(pytz.timezone("Europe/London"))
    if now.weekday() < 5:  # Monday to Friday
        send_daily_weather_email()
        return "✅ Weather check executed and email sent."
    else:
        return "ℹ️ Weekend: No email sent."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
