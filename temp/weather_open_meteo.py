from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

print("start")

mcp = FastMCP("weather")

# -----------------------------
# API endpoints
# -----------------------------
GEOCODING_API = "https://nominatim.openstreetmap.org/search"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"

HEADERS = {
    "User-Agent": "mcp-weather-app/1.0"
}


# -----------------------------
# Utils
# -----------------------------
async def geocode_city(city: str) -> dict[str, float] | None:
    """Convert city name to latitude & longitude."""
    params = {
        "q": city,
        "format": "json",
        "limit": 1
    }

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(GEOCODING_API, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()

            if not data:
                return None

            return {
                "latitude": float(data[0]["lat"]),
                "longitude": float(data[0]["lon"])
            }
        except Exception:
            return None


async def get_weather_by_coords(lat: float, lon: float) -> dict[str, Any] | None:
    """Get 7-day forecast from Open-Meteo."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "windspeed_10m_max"
        ],
        "timezone": "auto"
    }

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(WEATHER_API, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None


def format_forecast(data: dict[str, Any]) -> str:
    daily = data["daily"]

    result = []
    for i in range(len(daily["time"])):
        day = f"""
Дата: {daily['time'][i]}
Макс. температура: {daily['temperature_2m_max'][i]} °C
Мин. температура: {daily['temperature_2m_min'][i]} °C
Осадки: {daily['precipitation_sum'][i]} мм
Макс. ветер: {daily['windspeed_10m_max'][i]} км/ч
"""
        result.append(day)

    return "\n---\n".join(result)


# -----------------------------
# MCP TOOLS
# -----------------------------
@mcp.tool()
async def get_week_forecast_by_city(city: str) -> str:
    """
    Get 7-day weather forecast for any city in the world.

    Args:
        city: City name (e.g. Moscow, Berlin, New York)
    """
    coords = await geocode_city(city)
    if not coords:
        return f"Не удалось определить координаты города: {city}"

    weather = await get_weather_by_coords(
        coords["latitude"],
        coords["longitude"]
    )

    if not weather:
        return "Не удалось получить прогноз погоды."

    return format_forecast(weather)


@mcp.tool()
async def get_week_forecast_by_coords(latitude: float, longitude: float) -> str:
    """
    Get 7-day weather forecast by coordinates.

    Args:
        latitude: Latitude
        longitude: Longitude
    """
    weather = await get_weather_by_coords(latitude, longitude)

    if not weather:
        return "Не удалось получить прогноз погоды."

    return format_forecast(weather)


# -----------------------------
# Run server
# -----------------------------
if __name__ == "__main__":
    print("RUN MCP WEATHER...")
    mcp.run(transport="stdio")
