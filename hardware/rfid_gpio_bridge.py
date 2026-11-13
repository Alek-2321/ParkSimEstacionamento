import os
import threading
import time

import requests
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522

API = os.getenv("PARKING_API", "http://127.0.0.1:8000").rstrip("/")
GREEN_LED = int(os.getenv("GREEN_LED_PIN", "31"))
RED_LED = int(os.getenv("RED_LED_PIN", "29"))
BUZZER = int(os.getenv("BUZZER_PIN", "33"))
DEBOUNCE = float(os.getenv("RFID_DEBOUNCE_SECONDS", "3"))
BUZZER_ENABLED = os.getenv("ENABLE_BUZZER", "1").lower() in ("1", "true", "yes", "sim")

GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)

for pin in (GREEN_LED, RED_LED):
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

if BUZZER_ENABLED:
    GPIO.setup(BUZZER, GPIO.OUT, initial=GPIO.LOW)

reader = SimpleMFRC522()
http = requests.Session()
last_card_uid = None
last_card_time = 0.0


def beep(count=1, duration=0.10, pause=0.08):
    if not BUZZER_ENABLED:
        return

    for _ in range(count):
        GPIO.output(BUZZER, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(BUZZER, GPIO.LOW)
        time.sleep(pause)


def signal_result(result):
    action = (result or {}).get("action")

    if action == "entry_registered":
        GPIO.output(GREEN_LED, GPIO.HIGH)
        beep(1)
        time.sleep(0.5)
        GPIO.output(GREEN_LED, GPIO.LOW)

    elif action == "payment_required":
        GPIO.output(RED_LED, GPIO.HIGH)
        beep(2)
        time.sleep(0.5)
        GPIO.output(RED_LED, GPIO.LOW)

    elif action == "exit_authorized":
        GPIO.output(GREEN_LED, GPIO.HIGH)
        beep(2, 0.14, 0.08)
        time.sleep(1)
        GPIO.output(GREEN_LED, GPIO.LOW)

    else:
        GPIO.output(RED_LED, GPIO.HIGH)
        beep(3, 0.12, 0.07)
        time.sleep(0.7)
        GPIO.output(RED_LED, GPIO.LOW)


def post_card(uid):
    try:
        response = http.post(
            f"{API}/api/card/read",
            json={"uid": str(uid)},
            timeout=5
        )

        response.raise_for_status()
        result = response.json()

        print(
            f"[RFID] {uid}: {result.get('message', result)}",
            flush=True
        )

        signal_result(result)

    except requests.HTTPError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)

        print(f"[RFID] Operação recusada: {detail}", flush=True)
        signal_result(None)

    except requests.RequestException as exc:
        print(
            f"[RFID] Falha de comunicação com a API: {exc}",
            flush=True
        )

        signal_result(None)


def read_cards_forever():
    global last_card_uid, last_card_time

    can_read = True

    while True:
        try:
            read_method = getattr(reader, "read_id_no_block", None)

            if read_method is not None:
                uid_value = read_method()

                if uid_value is None:
                    can_read = True

                elif can_read:
                    uid = str(uid_value).strip()
                    now = time.monotonic()

                    if uid and (
                        uid != last_card_uid
                        or now - last_card_time >= DEBOUNCE
                    ):
                        post_card(uid)
                        last_card_uid = uid
                        last_card_time = now

                    can_read = False

            else:
                uid_value, _ = reader.read()
                uid = str(uid_value).strip()
                now = time.monotonic()

                if uid and (
                    uid != last_card_uid
                    or now - last_card_time >= DEBOUNCE
                ):
                    post_card(uid)
                    last_card_uid = uid
                    last_card_time = now

                time.sleep(DEBOUNCE)

        except Exception as exc:
            print(f"[RFID] Erro de leitura: {exc}", flush=True)
            time.sleep(1)


def update_leds():
    try:
        response = http.get(f"{API}/api/state", timeout=2)
        response.raise_for_status()

        state = response.json()

        pending = state.get("payment_pending", 0) > 0
        authorized = state.get("authorized_count", 0) > 0

        GPIO.output(RED_LED, GPIO.HIGH if pending else GPIO.LOW)
        GPIO.output(GREEN_LED, GPIO.HIGH if authorized else GPIO.LOW)

    except requests.RequestException:
        pass


print("[HARDWARE] ParkSIM RFID iniciado.", flush=True)
print(
    f"[HARDWARE] LED verde BOARD {GREEN_LED}; LED vermelho BOARD {RED_LED}; buzzer BOARD {BUZZER}.",
    flush=True
)

threading.Thread(
    target=read_cards_forever,
    daemon=True,
    name="rfid-reader"
).start()

try:
    while True:
        update_leds()
        time.sleep(1)

except KeyboardInterrupt:
    print("[HARDWARE] Encerrando leitor.", flush=True)

finally:
    GPIO.output(RED_LED, GPIO.LOW)
    GPIO.output(GREEN_LED, GPIO.LOW)

    if BUZZER_ENABLED:
        GPIO.output(BUZZER, GPIO.LOW)

    GPIO.cleanup()