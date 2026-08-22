# Smart Awning Controller (smart-awning-controller)

Somfy製電動ひさし・オーニング（2台: `-d 1`, `-d 2`）の日の出・日没連動自動開閉および雨雲接近時緊急格納システムです。

---

## 🌟 主な機能

1. **日の出・日没連動**:
   - 緯度・経度から毎日正確な日の出・日の入り時刻をオフライン計算（NOAAアルゴリズム）。
   - デフォルト: **日の出 1時間後 にオープン (DOWN)** / **日の入り 1時間前 にクローズ (UP)**。
2. **雨雲レーダー監視 (5分毎)**:
   - Yahoo! YOLP（または Open-Meteo フォールバック）により、現在の降雨検知または直近30分以内の雨雲接近を検知して自動で緊急クローズ (UP)。
   - 雨で閉じた後は「雨ロック」がかかり、雨が止むまで/翌朝まで自動オープンを安全に抑止。
3. **指定時刻・スケジュール柔軟設定**:
   - 固定時刻指定（例: 朝07:30オープン / 夕方18:00クローズ）
   - ガード時刻指定（例: 朝06:30より前には開けない / 夜18:00には必ず閉める）
4. **RF信号干渉防止**:
   - 1台目（`-d 1`）送信後、指定秒数（例: 2秒）のウェイトを置いて2台目（`-d 2`）を順次送信。
5. **手動CLI操作**:
   - コマンドラインからワンライナーで開閉・状態確認・雨ロック解除が可能。

---

## 🚀 セットアップ

### 1. リポジトリのクローンと依存パッケージの導入

```bash
git clone https://github.com/YOUR_USERNAME/smart-awning-controller.git
cd smart-awning-controller
sudo apt update
sudo apt install -y python3 python3-requests
```

### 2. 設定ファイルの作成

```bash
cp config.example.json config.json
```

`config.json` を開き、お住まいの地域の緯度・経度および各設定を編集してください。

```json
{
  "location": {
    "latitude": 35.6812,
    "longitude": 139.7671,
    "comment": "お住まいの地域の緯度・経度を設定してください"
  },
  "yolp": {
    "appid": "YOUR_YOLP_APPID_HERE",
    "rain_threshold_now": 0.0,
    "rain_threshold_forecast": 0.0,
    "forecast_lookahead_minutes": 30
  },
  "schedule": {
    "mode": "sun_relative",
    "sunrise_offset_minutes": 60,
    "sunset_offset_minutes": -60,
    "earliest_open_time": null,
    "latest_close_time": null,
    "fixed_open_time": null,
    "fixed_close_time": null
  },
  "hardware": {
    "python_bin": "/usr/bin/python3",
    "script_path": "/path/to/hisashi/somfy_fifo.py",
    "device_ids": [1, 2],
    "mod": "FSK",
    "sweep": true,
    "device_interval_seconds": 2.0
  },
  "daemon": {
    "weather_check_interval_seconds": 300,
    "loop_interval_seconds": 30,
    "reopen_after_rain": false
  }
}
```

> [!TIP]
> * **Yahoo! YOLP AppID**: Yahoo! JAPAN デベロッパーネットワークで取得した Client ID を入力してください。環境変数 `YOLP_APPID` でも指定可能です。未設定の場合は Open-Meteo（無料・登録不要API）に自動フォールバックします。
> * **ハードウェアドライバ**: `script_path` には、Somfy 送信ドライバ（`somfy_fifo.py`）のパスを指定してください。

---

## 📖 手動操作コマンド (`hisashi_ctl.py`)

### 1. 現在のステータス・スケジュール・天気確認
```bash
python3 hisashi_ctl.py status
```

### 2. ひさしを開く (DOWN)
```bash
python3 hisashi_ctl.py open
```

### 3. ひさしを閉じる (UP)
```bash
python3 hisashi_ctl.py close
```

### 4. 雨ロックを手動解除
```bash
python3 hisashi_ctl.py unlock-rain
```

### 5. RF送信なしのシミュレーション (`--dry-run`)
```bash
python3 hisashi_ctl.py open --dry-run
python3 hisashi_ctl.py close --dry-run
```

---

## 🔄 自動化常駐デーモンの起動方法

### フォアグラウンドでのテスト実行
```bash
python3 hisashi_daemon.py
```

### systemd サービス登録 (OS起動時に自動実行)
```bash
# hisashi.service の WorkingDirectory と ExecStart をご自身の環境のパスに合わせて編集してください
sudo cp hisashi.service /etc/systemd/system/

# systemd のリロードと有効化・起動
sudo systemctl daemon-reload
sudo systemctl enable --now hisashi.service

# 動作状況の確認
sudo systemctl status hisashi.service

# ログの確認
journalctl -u hisashi.service -f
# または
tail -f hisashi.log
```

---

## 📄 ライセンス

MIT License
