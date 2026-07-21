"""Weather via Open-Meteo (no API key). Location from config, or IP-located
once and cached in var/location.json. Offline -> friendly BMO excuse."""

import json
import os

import requests

from bmo.router import Plugin, Result

CODES = {
    0: "clear and sunny", 1: "mostly sunny", 2: "partly cloudy", 3: "cloudy",
    45: "foggy", 48: "foggy and frosty", 51: "a little drizzly", 53: "drizzly",
    55: "very drizzly", 61: "a bit rainy", 63: "rainy", 65: "super rainy",
    66: "icy rainy", 67: "icy rainy", 71: "a little snowy", 73: "snowy",
    75: "super snowy", 77: "snowy", 80: "showery", 81: "showery",
    82: "storm-showery", 85: "snow-showery", 86: "snow-showery",
    95: "thunderstormy", 96: "thunderstormy with hail", 99: "thunderstormy with hail",
}


class WeatherPlugin(Plugin):
    name = "weather"
    priority = 35

    def __init__(self, app):
        super().__init__(app)
        self.add(r"\b(weather|forecast)\b|\bhow (hot|cold|warm) is it\b|"
                 r"\bis it going to (rain|snow)\b|\bwhat's it like outside\b",
                 self.weather)

    def _location(self):
        cfg = self.app.cfg
        lat = float(cfg.get("weather", "latitude", 0.0))
        lon = float(cfg.get("weather", "longitude", 0.0))
        name = cfg.get("weather", "location_name", "")
        if lat or lon:
            return lat, lon, name
        cache = cfg.path("var/location.json")
        if os.path.exists(cache):
            with open(cache) as f:
                d = json.load(f)
            return d["lat"], d["lon"], d.get("city", "")
        r = requests.get("https://ipapi.co/json/", timeout=6)   # HTTPS, no key
        d = r.json()
        loc = {"lat": d["latitude"], "lon": d["longitude"], "city": d.get("city", "")}
        with open(cache, "w") as f:
            json.dump(loc, f)
        return loc["lat"], loc["lon"], loc["city"]

    def weather(self, m, text):
        unit = "fahrenheit" if self.app.cfg.get("weather", "fahrenheit", True) else "celsius"
        try:
            lat, lon, name = self._location()
            r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon,
                        "current": "temperature_2m,weather_code",
                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                        "forecast_days": 1, "timezone": "auto",
                        "temperature_unit": unit},
                timeout=8)
            r.raise_for_status()
            d = r.json()
            cur = d["current"]
            daily = d["daily"]
            desc = CODES.get(int(cur["weather_code"]), "mysterious")
            temp = round(cur["temperature_2m"])
            hi = round(daily["temperature_2m_max"][0])
            lo = round(daily["temperature_2m_min"][0])
            rain = daily.get("precipitation_probability_max", [None])[0]
            place = f" in {name}" if name else ""
            speech = (f"Right now{place} it's {temp} degrees and {desc}! "
                      f"Today goes from {lo} up to {hi}.")
            if rain is not None and rain >= 40:
                speech += f" And there's a {round(rain)} percent chance of rain — bring a coat!"
            return Result(speech=speech)
        except Exception:
            return Result(speech="I tried to peek outside through the internet, "
                                 "but I couldn't reach it! No weather for me right now.")
