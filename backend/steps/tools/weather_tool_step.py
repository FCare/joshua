import logging
from typing import Dict, Any, Optional
import requests
from .base_tool_step import BaseToolStep

logger = logging.getLogger(__name__)


class WeatherToolStep(BaseToolStep):
    """Outil météo utilisant l'API Open-Meteo (gratuite, sans clé API)"""
    
    def __init__(self, name: str = "WeatherTool", config: Optional[Dict] = None):
        super().__init__(name, config)
        
        logger.info("🌤️ WeatherTool initialisé avec Open-Meteo (API gratuite)")
    
    def _create_tool_definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Obtient les informations météorologiques actuelles et prévisions pour une ville",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "Nom de la ville (ex: 'Paris', 'New York', 'Tokyo')"
                        },
                        "include_forecast": {
                            "type": "boolean",
                            "description": "Inclure les prévisions sur 7 jours",
                            "default": False
                        },
                        "temperature_unit": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                            "description": "Unité de température",
                            "default": "celsius"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
    
    def _execute_tool(self, parameters: Dict[str, Any]) -> Any:
        """Exécute la requête météo avec Open-Meteo"""
        location = parameters.get("location", "")
        include_forecast = parameters.get("include_forecast", False)
        temperature_unit = parameters.get("temperature_unit", "celsius")
        
        logger.info(f"🌤️ Requête météo Open-Meteo: {location}")
        
        try:
            # 1. Géocoder la ville pour obtenir les coordonnées
            coords = self._geocode_location(location)
            if "error" in coords:
                return coords
                
            lat, lon, city_name, country = coords["lat"], coords["lon"], coords["name"], coords["country"]
            
            # 2. Obtenir la météo
            weather_data = self._get_weather_data(lat, lon, include_forecast, temperature_unit)
            if "error" in weather_data:
                return weather_data
            
            # 3. Formater la réponse
            result = {
                "location": city_name,
                "country": country,
                "coordinates": {"latitude": lat, "longitude": lon},
                "current": weather_data["current"],
                "units": {
                    "temperature": "°C" if temperature_unit == "celsius" else "°F",
                    "wind_speed": "km/h",
                    "precipitation": "mm"
                }
            }
            
            if include_forecast and "forecast" in weather_data:
                result["forecast"] = weather_data["forecast"]
            
            logger.info(f"✅ Météo récupérée pour {city_name}: {result['current']['temperature']}{result['units']['temperature']}")
            return result
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération météo: {e}")
            return {"error": f"Erreur lors de la récupération météo: {str(e)}"}
    
    def _geocode_location(self, location: str) -> Dict:
        """Géocode une ville avec l'API Open-Meteo"""
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {
                "name": location,
                "count": 1,
                "language": "fr",
                "format": "json"
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("results") and len(data["results"]) > 0:
                    result = data["results"][0]
                    return {
                        "lat": result["latitude"],
                        "lon": result["longitude"],
                        "name": result["name"],
                        "country": result.get("country", "")
                    }
                else:
                    return {"error": f"Ville '{location}' non trouvée"}
            else:
                return {"error": f"Erreur géocodage: {response.status_code}"}
                
        except Exception as e:
            return {"error": f"Erreur géocodage: {str(e)}"}
    
    def _get_weather_data(self, lat: float, lon: float, include_forecast: bool, temperature_unit: str) -> Dict:
        """Récupère les données météo avec Open-Meteo"""
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
                "temperature_unit": temperature_unit,
                "wind_speed_unit": "kmh",
                "precipitation_unit": "mm",
                "timezone": "auto"
            }
            
            if include_forecast:
                params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
                params["forecast_days"] = 7
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # Formater les données actuelles
                current = data.get("current", {})
                result = {
                    "current": {
                        "temperature": current.get("temperature_2m"),
                        "feels_like": current.get("apparent_temperature"),
                        "humidity": current.get("relative_humidity_2m"),
                        "precipitation": current.get("precipitation"),
                        "wind_speed": current.get("wind_speed_10m"),
                        "wind_direction": current.get("wind_direction_10m"),
                        "weather_code": current.get("weather_code"),
                        "description": self._get_weather_description(current.get("weather_code", 0))
                    }
                }
                
                # Ajouter les prévisions si demandées
                if include_forecast and "daily" in data:
                    daily = data["daily"]
                    forecasts = []
                    
                    for i in range(min(7, len(daily.get("time", [])))):
                        forecasts.append({
                            "date": daily["time"][i],
                            "temperature_max": daily["temperature_2m_max"][i],
                            "temperature_min": daily["temperature_2m_min"][i],
                            "precipitation": daily["precipitation_sum"][i],
                            "wind_speed": daily["wind_speed_10m_max"][i],
                            "weather_code": daily["weather_code"][i],
                            "description": self._get_weather_description(daily["weather_code"][i])
                        })
                    
                    result["forecast"] = forecasts
                
                return result
            else:
                return {"error": f"Erreur API météo: {response.status_code}"}
                
        except Exception as e:
            return {"error": f"Erreur récupération météo: {str(e)}"}
    
    def _get_weather_description(self, weather_code: int) -> str:
        """Convertit le code météo Open-Meteo en description française"""
        weather_descriptions = {
            0: "Ciel dégagé",
            1: "Principalement dégagé",
            2: "Partiellement nuageux",
            3: "Couvert",
            45: "Brouillard",
            48: "Brouillard givrant",
            51: "Bruine légère",
            53: "Bruine modérée",
            55: "Bruine dense",
            56: "Bruine verglaçante légère",
            57: "Bruine verglaçante dense",
            61: "Pluie légère",
            63: "Pluie modérée",
            65: "Pluie forte",
            66: "Pluie verglaçante légère",
            67: "Pluie verglaçante forte",
            71: "Chute de neige légère",
            73: "Chute de neige modérée",
            75: "Chute de neige forte",
            77: "Grains de neige",
            80: "Averses de pluie légères",
            81: "Averses de pluie modérées",
            82: "Averses de pluie violentes",
            85: "Averses de neige légères",
            86: "Averses de neige fortes",
            95: "Orage",
            96: "Orage avec grêle légère",
            99: "Orage avec grêle forte"
        }
        return weather_descriptions.get(weather_code, "Conditions inconnues")
    
    def cleanup(self):
        """Nettoie les ressources de l'outil météo"""
        logger.info(f"🧹 Nettoyage de l'outil météo {self.name}")
        super().cleanup()