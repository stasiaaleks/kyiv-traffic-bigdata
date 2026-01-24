import os

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("WEATHER_API_KEY")
BASE_URL = "https://api.openweathermap.org/data/2.5"  # placeholder

KYIV_LAT = 50.4501
KYIV_LON = 30.5234


def fetch_data() -> dict:
    raise NotImplementedError
