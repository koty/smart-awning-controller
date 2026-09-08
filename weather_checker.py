"""
雨雲・降水判定モジュール
Yahoo! JAPAN YOLP 気象情報API (および Open-Meteo フォールバック) を使用して
現在地におけるリアルタイムの雨量と、直近の雨雲接近を判定します。
"""

import urllib.request
import urllib.parse
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("hisashi.weather")


def fetch_json_with_retry(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0, max_retries: int = 3, retry_delay: float = 2.0) -> Dict[str, Any]:
    """
    指定URLからJSONを取得します。一時的な通信エラーや名前解決エラー時はリトライします。
    """
    req_headers = {"User-Agent": "HisashiAutomator/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"HTTP request failed (attempt {attempt}/{max_retries}) for {url.split('?')[0]}: {e}. Retrying in {retry_delay:.1f}s...")
                time.sleep(retry_delay)
            else:
                logger.error(f"HTTP request failed after {max_retries} attempts for {url.split('?')[0]}: {e}")
                raise last_error

    raise RuntimeError("Unexpected end of retry loop")


def check_yolp_weather(lat: float, lon: float, appid: str, lookahead_minutes: int = 30) -> Dict[str, Any]:
    """
    Yahoo! YOLP 気象情報APIから降雨データを取得して解析します。
    """
    coords = f"{lon},{lat}"
    url = f"https://map.yahooapis.jp/weather/V1/place?coordinates={coords}&appid={appid}&output=json"

    data = fetch_json_with_retry(url, timeout=10.0, max_retries=3, retry_delay=2.0)

    features = data.get("Feature", [])
    if not features:
        raise ValueError("YOLP API returned no features")

    weather_list = features[0].get("Property", {}).get("WeatherList", {}).get("Weather", [])
    if not weather_list:
        raise ValueError("YOLP API returned empty WeatherList")

    current_rain = 0.0
    forecast_rain_list = []
    max_forecast_rain = 0.0

    # 最初の observation を現在雨量とする
    for item in weather_list:
        w_type = item.get("Type")
        date_str = item.get("Date")  # e.g. "202608191800"
        rainfall = float(item.get("Rainfall", 0.0))

        if w_type == "observation":
            current_rain = rainfall
        elif w_type == "forecast":
            forecast_rain_list.append({"date": date_str, "rainfall": rainfall})

    # lookahead_minutes 以内の予測雨量の最大値を算出 (デフォルト10分刻み)
    max_items = max(1, lookahead_minutes // 10)
    for f in forecast_rain_list[:max_items]:
        if f["rainfall"] > max_forecast_rain:
            max_forecast_rain = f["rainfall"]

    return {
        "provider": "YOLP",
        "current_rain_mm": current_rain,
        "max_forecast_rain_mm": max_forecast_rain,
        "lookahead_minutes": lookahead_minutes,
        "details": weather_list,
        "error": False
    }


def check_open_meteo_weather(lat: float, lon: float, lookahead_minutes: int = 30) -> Dict[str, Any]:
    """
    Open-Meteo API (無料・キー不要) の降水情報をフォールバックとして取得します。
    """
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=precipitation,rain,showers&hourly=precipitation&timezone=Asia%2FTokyo"
    data = fetch_json_with_retry(url, timeout=10.0, max_retries=3, retry_delay=2.0)

    current_rain = float(data.get("current", {}).get("precipitation", 0.0))
    # hourly precipitation (next 1-2 hours)
    hourly = data.get("hourly", {}).get("precipitation", [])
    max_forecast_rain = 0.0
    if hourly:
        # 今後数時間の最大雨量
        max_forecast_rain = max(hourly[:2])

    return {
        "provider": "Open-Meteo",
        "current_rain_mm": current_rain,
        "max_forecast_rain_mm": float(max_forecast_rain),
        "lookahead_minutes": lookahead_minutes,
        "details": [],
        "error": False
    }


def check_rain_risk(config: Dict[str, Any]) -> Tuple[Optional[bool], str, Dict[str, Any]]:
    """
    設定に基づいて雨のリスク（雨が降っている、または雨雲が近づいているか）を判定します。

    :return: (is_rain_risk, reason_text, raw_data)
             is_rain_risk:
               - True: 雨リスクあり (降雨検知または雨雲接近)
               - False: 安全 (降雨なし確認済み)
               - None: 判定不能 (API通信エラー等のため安全確認不可)
    """
    loc = config.get("location", {})
    lat = loc.get("latitude", 35.6895)
    lon = loc.get("longitude", 139.6917)
    yolp_cfg = config.get("yolp", {})
    appid = os.environ.get("YOLP_APPID") or yolp_cfg.get("appid", "")
    lookahead = yolp_cfg.get("forecast_lookahead_minutes", 30)
    thresh_now = yolp_cfg.get("rain_threshold_now", 0.0)
    thresh_forecast = yolp_cfg.get("rain_threshold_forecast", 0.0)

    res = None
    errors = []
    if appid:
        try:
            res = check_yolp_weather(lat, lon, appid, lookahead)
        except Exception as e:
            errors.append(f"YOLP: {e}")
            logger.warning(f"YOLP weather API failed: {e}. Falling back to Open-Meteo.")

    if res is None:
        try:
            res = check_open_meteo_weather(lat, lon, lookahead)
        except Exception as e:
            errors.append(f"Open-Meteo: {e}")
            err_details = "; ".join(errors)
            logger.error(f"Open-Meteo weather API also failed: {e}")
            # フェイルセーフ: 安全確認が取れないため is_risk に None を返す
            return None, f"Weather check failed ({err_details})", {
                "provider": None,
                "current_rain_mm": 0.0,
                "max_forecast_rain_mm": 0.0,
                "error": True,
                "error_details": err_details
            }

    cur_rain = res["current_rain_mm"]
    fore_rain = res["max_forecast_rain_mm"]

    if cur_rain > thresh_now:
        reason = f"降雨を検知しました (現在雨量: {cur_rain} mm/h)"
        return True, reason, res

    if fore_rain > thresh_forecast:
        reason = f"雨雲の接近を検知しました ({lookahead}分以内の最大予測雨量: {fore_rain} mm/h)"
        return True, reason, res

    return False, f"降雨なし (現在: {cur_rain} mm/h, {lookahead}分以内予測: {fore_rain} mm/h)", res


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    default_cfg = os.environ.get(
        "HISASHI_CONFIG",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    )
    with open(default_cfg, "r", encoding="utf-8") as f:
        config = json.load(f)

    is_risk, reason, data = check_rain_risk(config)
    print(f"Rain Risk: {is_risk}")
    print(f"Reason:    {reason}")
    print(f"Provider:  {data.get('provider')}")
    print(f"Current:   {data.get('current_rain_mm')} mm/h")
    print(f"Forecast:  {data.get('max_forecast_rain_mm')} mm/h")
