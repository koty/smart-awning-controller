#!/usr/bin/env python3
"""
ひさし制御・状態管理モジュール
2台のひさし (-d 1, -d 2) へのコマンド送信と、現在の開閉状態 (state.json) を管理します。
"""

import os
import sys
import time
import json
import logging
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("HISASHI_CONFIG", os.path.join(BASE_DIR, "config.json"))
STATE_FILE = os.environ.get("HISASHI_STATE", os.path.join(BASE_DIR, "state.json"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("hisashi_ctl")


def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> Dict[str, Any]:
    default_state = {
        "current_position": "UNKNOWN",  # "OPEN", "CLOSED", "UNKNOWN"
        "last_action": None,
        "last_action_time": None,
        "last_reason": None,
        "rain_locked": False,
        "last_rain_time": None,
        "updated_at": datetime.now().isoformat()
    }
    if not os.path.exists(STATE_FILE):
        save_state(default_state)
        return default_state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            # デフォルトキー補完
            for k, v in default_state.items():
                if k not in state:
                    state[k] = v
            return state
    except Exception as e:
        logger.warning(f"Failed to read state file: {e}. Resetting to default.")
        save_state(default_state)
        return default_state


def save_state(state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.now().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def send_somfy_command(action: str, device_id: int, config: Dict[str, Any], dry_run: bool = False) -> bool:
    """
    1台のひさしに対して somfy_fifo.py を実行します。
    action: "DOWN" (広げる/OPEN) または "UP" (しまう/CLOSE)
    """
    hw = config.get("hardware", {})
    python_bin = hw.get("python_bin", "/usr/bin/python3")
    script_path = hw.get("script_path", "/home/koty/hisashi/somfy_fifo.py")
    mod = hw.get("mod", "FSK")
    sweep = hw.get("sweep", True)

    cmd = [python_bin, script_path, action, "-d", str(device_id)]
    if mod:
        cmd.extend(["--mod", mod])
    if sweep:
        cmd.append("--sweep")

    logger.info(f"Executing command for device {device_id}: {' '.join(cmd)}")

    if dry_run:
        logger.info(f"[DRY-RUN] Would execute: {' '.join(cmd)}")
        return True

    try:
        res = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=15
        )
        logger.info(f"Device {device_id} response: {res.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed for device {device_id} with code {e.returncode}: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error executing command for device {device_id}: {e}")
        return False


def open_hisashi(reason: str = "MANUAL", force: bool = False, dry_run: bool = False) -> bool:
    """
    すべてのひさしを広げます (DOWNコマンド)。
    """
    config = load_config()
    state = load_state()

    # 雨ロックチェック
    if state.get("rain_locked") and not force:
        logger.warning(f"Opening blocked by rain_lock (Reason: {state.get('last_reason')}). Use --force to override.")
        return False

    if state.get("current_position") == "OPEN" and not force:
        logger.info("Hisashi is already OPEN. Skipping.")
        return True

    logger.info(f"Opening all hisashi (Reason: {reason})")
    hw = config.get("hardware", {})
    device_ids = hw.get("device_ids", [1, 2])
    interval = hw.get("device_interval_seconds", 2.0)

    success_all = True
    for i, dev_id in enumerate(device_ids):
        if i > 0:
            time.sleep(interval)
        ok = send_somfy_command("DOWN", dev_id, config, dry_run=dry_run)
        if not ok:
            success_all = False

    if success_all:
        state["current_position"] = "OPEN"
        state["last_action"] = "OPEN"
        state["last_action_time"] = datetime.now().isoformat()
        state["last_reason"] = reason
        state["rain_locked"] = False
        save_state(state)
        logger.info("All hisashi opened successfully.")
        return True
    else:
        logger.error("Failed to open some hisashi.")
        return False


def close_hisashi(reason: str = "MANUAL", is_rain: bool = False, force: bool = False, dry_run: bool = False) -> bool:
    """
    すべてのひさしをしまいます (UPコマンド)。
    """
    config = load_config()
    state = load_state()

    if state.get("current_position") == "CLOSED" and not force:
        logger.info("Hisashi is already CLOSED. Skipping.")
        if is_rain:
            state["rain_locked"] = True
            state["last_rain_time"] = datetime.now().isoformat()
            save_state(state)
        return True

    logger.info(f"Closing all hisashi (Reason: {reason})")
    hw = config.get("hardware", {})
    device_ids = hw.get("device_ids", [1, 2])
    interval = hw.get("device_interval_seconds", 2.0)

    success_all = True
    for i, dev_id in enumerate(device_ids):
        if i > 0:
            time.sleep(interval)
        ok = send_somfy_command("UP", dev_id, config, dry_run=dry_run)
        if not ok:
            success_all = False

    if success_all:
        state["current_position"] = "CLOSED"
        state["last_action"] = "CLOSE"
        state["last_action_time"] = datetime.now().isoformat()
        state["last_reason"] = reason
        if is_rain:
            state["rain_locked"] = True
            state["last_rain_time"] = datetime.now().isoformat()
        save_state(state)
        logger.info("All hisashi closed successfully.")
        return True
    else:
        logger.error("Failed to close some hisashi.")
        return False


def unlock_rain() -> None:
    """雨ロックを手動解除します。"""
    state = load_state()
    state["rain_locked"] = False
    save_state(state)
    logger.info("Rain lock released.")


def get_status_info() -> Dict[str, Any]:
    from sun_calc import get_schedule_times
    from weather_checker import check_rain_risk

    config = load_config()
    state = load_state()
    sched = get_schedule_times(config)
    is_risk, rain_reason, weather_data = check_rain_risk(config)

    return {
        "state": state,
        "schedule": sched,
        "weather": {
            "rain_risk": is_risk,
            "reason": rain_reason,
            "provider": weather_data.get("provider"),
            "current_rain_mm": weather_data.get("current_rain_mm"),
            "forecast_max_mm": weather_data.get("max_forecast_rain_mm")
        }
    }


def main():
    parser = argparse.ArgumentParser(description="Hisashi (Somfy Awning) Controller")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # open command
    open_parser = subparsers.add_parser("open", help="Open (DOWN) all hisashi")
    open_parser.add_argument("--reason", default="MANUAL", help="Reason for opening")
    open_parser.add_argument("--force", action="store_true", help="Force command even if already open or rain locked")
    open_parser.add_argument("--dry-run", action="store_true", help="Simulate execution without sending RF signals")

    # close command
    close_parser = subparsers.add_parser("close", help="Close (UP) all hisashi")
    close_parser.add_argument("--reason", default="MANUAL", help="Reason for closing")
    close_parser.add_argument("--rain", action="store_true", help="Mark as rain close (enables rain lock)")
    close_parser.add_argument("--force", action="store_true", help="Force command even if already closed")
    close_parser.add_argument("--dry-run", action="store_true", help="Simulate execution without sending RF signals")

    # unlock-rain command
    subparsers.add_parser("unlock-rain", help="Unlock rain protection")

    # status command
    subparsers.add_parser("status", help="Show current status, schedule, and weather")

    args = parser.parse_args()

    if args.command == "open":
        ok = open_hisashi(reason=args.reason, force=args.force, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)
    elif args.command == "close":
        ok = close_hisashi(reason=args.reason, is_rain=args.rain, force=args.force, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)
    elif args.command == "unlock-rain":
        unlock_rain()
        print("Rain lock released.")
    elif args.command == "status":
        info = get_status_info()
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
