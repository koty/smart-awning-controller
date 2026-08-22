"""
日の出・日の入り計算モジュール (NOAA Solar Calculations アルゴリズム)
外部ライブラリ不要で、標準ライブラリ (math, datetime) のみで動作します。
"""

import math
from datetime import datetime, date, time, timezone, timedelta
from typing import Tuple, Optional, Dict, Any


def calculate_sun_times(lat: float, lon: float, target_date: Optional[date] = None, tz_offset_hours: float = 9.0) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    指定した緯度・経度・日付（現地時間）における日の出・日の入り時刻を計算します。
    現地時間の target_date 当日の日の出と日の入りを正確に返します。

    :param lat: 緯度 (北緯は正, 南緯は負)
    :param lon: 経度 (東経は正, 西経は負)
    :param target_date: 現地時間の日付 (Noneの場合は今日)
    :param tz_offset_hours: タイムゾーンのUTCオフセット (日本標準時 JST は +9.0)
    :return: (日の出 datetime (タイムゾーン付き), 日の入り datetime (タイムゾーン付き))
    """
    if target_date is None:
        target_date = datetime.now().date()

    tz = timezone(timedelta(hours=tz_offset_hours))
    # 対象日の正午 (現地時間) を基準にユリウス日または day of year を算出
    n = target_date.timetuple().tm_yday
    lng_hour = lon / 15.0

    t_rise = n + ((6.0 - lng_hour) / 24.0)
    t_set = n + ((18.0 - lng_hour) / 24.0)

    results = []
    for t, is_rise in [(t_rise, True), (t_set, False)]:
        # Mean anomaly
        M = (0.9856 * t) - 3.289
        
        # Sun's true longitude
        L = (M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634) % 360.0
        
        # Sun's right ascension
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360.0
        Lquadrant = (math.floor(L / 90.0)) * 90.0
        RAquadrant = (math.floor(RA / 90.0)) * 90.0
        RA = (RA + (Lquadrant - RAquadrant)) / 15.0

        # Sun's declination
        sinDec = 0.39782 * math.sin(math.radians(L))
        cosDec = math.cos(math.asin(sinDec))

        # Sun's local hour angle for zenith = 90.8333 deg (official sunrise/sunset)
        zenith = 90.8333
        cosH = (math.cos(math.radians(zenith)) - (sinDec * math.sin(math.radians(lat)))) / (cosDec * math.cos(math.radians(lat)))

        if cosH > 1.0 or cosH < -1.0:
            results.append(None)
            continue

        H = (360.0 - math.degrees(math.acos(cosH))) if is_rise else math.degrees(math.acos(cosH))
        H = H / 15.0

        # Local mean time
        T = H + RA - (0.06571 * t) - 6.622

        # UT hours (0.0 - 24.0)
        UT = (T - lng_hour) % 24.0

        # 現地時間に変換したとき target_date の該当時刻になるように datetime を組み立てる
        local_hours = UT + tz_offset_hours
        day_offset = int(local_hours // 24)
        local_hours = local_hours % 24.0

        hour = int(local_hours)
        minute = int((local_hours - hour) * 60)
        second = int(round((((local_hours - hour) * 60) - minute) * 60))
        if second >= 60:
            second = 0
            minute += 1
        if minute >= 60:
            minute = 0
            hour += 1
        if hour >= 24:
            hour = 0
            day_offset += 1

        dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, second, tzinfo=tz)
        results.append(dt)

    return results[0], results[1]


def get_schedule_times(config: Dict[str, Any], target_date: Optional[date] = None) -> Dict[str, Any]:
    """
    設定に基づいて、当日のオープン予定時刻とクローズ予定時刻を算出します。
    """
    if target_date is None:
        target_date = datetime.now().date()

    loc = config.get("location", {})
    lat = loc.get("latitude", 35.6895)
    lon = loc.get("longitude", 139.6917)
    sched = config.get("schedule", {})
    mode = sched.get("mode", "sun_relative")
    tz = timezone(timedelta(hours=9.0))

    sunrise_dt, sunset_dt = calculate_sun_times(lat, lon, target_date)

    open_dt = None
    close_dt = None

    if mode in ("sun_relative", "hybrid") and sunrise_dt and sunset_dt:
        sr_offset = sched.get("sunrise_offset_minutes", 60)
        ss_offset = sched.get("sunset_offset_minutes", -60)
        open_dt = sunrise_dt + timedelta(minutes=sr_offset)
        close_dt = sunset_dt + timedelta(minutes=ss_offset)

    if mode == "fixed":
        fixed_open = sched.get("fixed_open_time")
        fixed_close = sched.get("fixed_close_time")
        if fixed_open:
            h, m = map(int, fixed_open.split(":"))
            open_dt = datetime(target_date.year, target_date.month, target_date.day, h, m, 0, tzinfo=tz)
        if fixed_close:
            h, m = map(int, fixed_close.split(":"))
            close_dt = datetime(target_date.year, target_date.month, target_date.day, h, m, 0, tzinfo=tz)

    # ガード時刻の適用 (earliest_open_time / latest_close_time)
    earliest_open = sched.get("earliest_open_time")
    if earliest_open and open_dt:
        h, m = map(int, earliest_open.split(":"))
        earliest_dt = datetime(target_date.year, target_date.month, target_date.day, h, m, 0, tzinfo=tz)
        if open_dt < earliest_dt:
            open_dt = earliest_dt

    latest_close = sched.get("latest_close_time")
    if latest_close and close_dt:
        h, m = map(int, latest_close.split(":"))
        latest_dt = datetime(target_date.year, target_date.month, target_date.day, h, m, 0, tzinfo=tz)
        if close_dt > latest_dt:
            close_dt = latest_dt

    return {
        "date": target_date.isoformat(),
        "sunrise": sunrise_dt.isoformat() if sunrise_dt else None,
        "sunset": sunset_dt.isoformat() if sunset_dt else None,
        "open_time": open_dt.isoformat() if open_dt else None,
        "close_time": close_dt.isoformat() if close_dt else None,
        "mode": mode
    }


if __name__ == "__main__":
    import json
    
    cfg = {
        "location": {"latitude": 35.6895, "longitude": 139.6917},
        "schedule": {"mode": "sun_relative", "sunrise_offset_minutes": 60, "sunset_offset_minutes": -60}
    }
    res = get_schedule_times(cfg)
    print(json.dumps(res, indent=2, ensure_ascii=False))
