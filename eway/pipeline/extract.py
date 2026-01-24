import os

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("EWAY_API_KEY")
BASE_URL = "https://api.eway.in.ua"  # placeholder


def fetch_data() -> dict:
    raise NotImplementedError
