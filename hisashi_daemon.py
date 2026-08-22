#!/usr/bin/env python3
"""
ひさし自動制御常駐デーモン (hisashi_daemon.py)
- 日の出1時間後 / 日の入り1時間前（または指定時刻）の自動開閉
- YOLP / Open-Meteo API による雨雲接近の定期監視 (5分毎) と緊急収納
- ログ出力 (hisashi.log) および systemd サービス対応
"""

import os
import sys
import time
import json
import signal
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, date, timezone, timedelta
from typing import Dict, Any, Optional

import sun_calc
import weather_checker
import hisashi_ctl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.environ.get("HISASHI_LOG", os.path.join(BASE_DIR, "hisashi.log"))
running = True


def setup_logger():
    logger = logging.getLogger("hisashi_daemon")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # コンソール出力
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # ファイル出力 (最大5MB, 3世代ローテーション)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


logger = setup_logger()


def signal_handler(signum, frame):
    global running
    logger.info(f"Received signal {signum}. Shutting down hisashi daemon gracefully...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class HisashiScheduler:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.config = hisashi_ctl.load_config()
        self.last_weather_check_time = 0
        self.current_schedule_date = None
        self.schedule_info = {}
        self.daily_opened = False
        self.daily_closed = False
        self.tz = timezone(timedelta(hours=9.0))

    def reload_config(self):
        try:
            self.config = hisashi_ctl.load_config()
        except Exception as e:
            logger.error(f"Failed to reload config: {e}")

    def update_daily_schedule_if_needed(self, now_dt: datetime):
        today = now_dt.date()
        if self.current_schedule_date != today:
            self.current_schedule_date = today
            self.schedule_info = sun_calc.get_schedule_times(self.config, today)
            self.daily_opened = False
            self.daily_closed = False

            # 日付が変わったタイミングで前日の雨ロックをリセット
            state = hisashi_ctl.load_state()
            if state.get("rain_locked"):
                state["rain_locked"] = False
                hisashi_ctl.save_state(state)
                logger.info("New day arrived. Reset rain_locked status.")

            logger.info("=== Updated Daily Schedule ===")
            logger.info(f"Date:       {self.schedule_info.get('date')}")
            logger.info(f"Sunrise:    {self.schedule_info.get('sunrise')}")
            logger.info(f"Sunset:     {self.schedule_info.get('sunset')}")
            logger.info(f"Open Time:  {self.schedule_info.get('open_time')}")
            logger.info(f"Close Time: {self.schedule_info.get('close_time')}")
            logger.info("===============================")

    def check_weather_and_act(self, now_dt: datetime):
        daemon_cfg = self.config.get("daemon", {})
        interval = daemon_cfg.get("weather_check_interval_seconds", 300)
        now_ts = time.time()

        if now_ts - self.last_weather_check_time < interval:
            return

        self.last_weather_check_time = now_ts
        state = hisashi_ctl.load_state()

        is_risk, reason, wdata = weather_checker.check_rain_risk(self.config)
        logger.info(f"[Weather Check] Risk: {is_risk} | {reason} | Provider: {wdata.get('provider')}")

        if is_risk:
            # 雨が降っている/近づいている場合
            if state.get("current_position") != "CLOSED":
                logger.warning(f"Rain detected! Closing hisashi immediately. (Reason: {reason})")
                hisashi_ctl.close_hisashi(reason=f"AUTO_RAIN: {reason}", is_rain=True, dry_run=self.dry_run)
            else:
                # 既に閉じていても雨ロックを付与
                if not state.get("rain_locked"):
                    state["rain_locked"] = True
                    state["last_rain_time"] = datetime.now().isoformat()
                    hisashi_ctl.save_state(state)
        else:
            # 雨が止んでいる場合、自動再オープン設定があるなら処理
            reopen = daemon_cfg.get("reopen_after_rain", False)
            if reopen and state.get("rain_locked") and state.get("current_position") == "CLOSED":
                # 開放時間帯内かチェック
                open_str = self.schedule_info.get("open_time")
                close_str = self.schedule_info.get("close_time")
                if open_str and close_str:
                    open_dt = datetime.fromisoformat(open_str)
                    close_dt = datetime.fromisoformat(close_str)
                    if open_dt <= now_dt < close_dt:
                        logger.info("Rain has stopped and currently within open hours. Re-opening hisashi.")
                        hisashi_ctl.open_hisashi(reason="REOPEN_AFTER_RAIN", force=True, dry_run=self.dry_run)

    def check_schedule_and_act(self, now_dt: datetime):
        open_str = self.schedule_info.get("open_time")
        close_str = self.schedule_info.get("close_time")
        if not open_str or not close_str:
            return

        open_dt = datetime.fromisoformat(open_str)
        close_dt = datetime.fromisoformat(close_str)

        state = hisashi_ctl.load_state()
        curr_pos = state.get("current_position")
        rain_locked = state.get("rain_locked", False)

        # 1. オープン判定 (open_time <= now < close_time)
        if open_dt <= now_dt < close_dt:
            if not self.daily_opened and curr_pos != "OPEN":
                if rain_locked:
                    logger.warning("Schedule open time reached, but rain_locked is active. Skipping open.")
                else:
                    # オープン前に念のため雨雲チェック
                    is_risk, reason, _ = weather_checker.check_rain_risk(self.config)
                    if is_risk:
                        logger.warning(f"Schedule open time reached, but rain risk detected ({reason}). Will not open.")
                    else:
                        logger.info(f"Scheduled OPEN time reached ({open_dt.strftime('%H:%M:%S')}). Opening hisashi.")
                        ok = hisashi_ctl.open_hisashi(reason="SCHEDULE_OPEN", dry_run=self.dry_run)
                        if ok:
                            self.daily_opened = True

        # 2. クローズ判定 (now >= close_time または now < open_dt (夜間・未明))
        if now_dt >= close_dt:
            if not self.daily_closed and curr_pos != "CLOSED":
                logger.info(f"Scheduled CLOSE time reached ({close_dt.strftime('%H:%M:%S')}). Closing hisashi.")
                ok = hisashi_ctl.close_hisashi(reason="SCHEDULE_CLOSE", is_rain=False, dry_run=self.dry_run)
                if ok:
                    self.daily_closed = True

    def run_step(self):
        self.reload_config()
        now_dt = datetime.now(self.tz)
        self.update_daily_schedule_if_needed(now_dt)
        self.check_weather_and_act(now_dt)
        self.check_schedule_and_act(now_dt)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Hisashi Automation Daemon")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without sending actual RF commands")
    parser.add_argument("--once", action="store_true", help="Run one evaluation step and exit")
    args = parser.parse_args()

    logger.info("Starting Hisashi Automation Daemon...")
    if args.dry_run:
        logger.info("[MODE] DRY-RUN enabled. No RF signals will be sent.")

    scheduler = HisashiScheduler(dry_run=args.dry_run)

    if args.once:
        scheduler.run_step()
        logger.info("Single evaluation completed.")
        return

    daemon_cfg = scheduler.config.get("daemon", {})
    loop_interval = daemon_cfg.get("loop_interval_seconds", 30)

    while running:
        try:
            scheduler.run_step()
        except Exception as e:
            logger.error(f"Error in daemon loop: {e}", exc_info=True)

        # 待機 (1秒刻みでシグナル即時反応)
        for _ in range(int(loop_interval)):
            if not running:
                break
            time.sleep(1)

    logger.info("Hisashi Daemon stopped.")


if __name__ == "__main__":
    main()
