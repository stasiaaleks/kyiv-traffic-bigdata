import os

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("WEATHER_API_KEY")
BASE_URL = "https://placeholder.com"


KYIV_BOUNDS = (50.3, 50.6, 30.2, 30.8)


def fetch_data() -> dict:
    raise NotImplementedError
