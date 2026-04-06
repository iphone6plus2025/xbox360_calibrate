#!/usr/bin/env python3
"""
Xbox 360 calibration proxy via uinput.
Reads real controller, corrects axis offsets, outputs virtual device.
"""

import evdev
from evdev import UInput, ecodes as e
import signal
import sys
import time

# === КАЛИБРОВКА ===
# Значения из оригинального скрипта
CALIBRATION = {
    e.ABS_X:  (-25952, 8800, 32767),   # левый стик горизонталь
    e.ABS_Y:  (-32768,  800, 32443),   # левый стик вертикаль
    e.ABS_RX: (-32768,  600, 32767),   # правый стик горизонталь
    e.ABS_RY: (-32768, -700, 32767),   # правый стик вертикаль
}

DEADZONE = 3000
TRIGGER_THRESHOLD = 10
DEVICE_PATH = "/dev/input/event14"

ALL_BUTTONS = [
    e.BTN_A, e.BTN_B, e.BTN_X, e.BTN_Y,
    e.BTN_TL, e.BTN_TR, e.BTN_TL2, e.BTN_TR2,
    e.BTN_SELECT, e.BTN_START, e.BTN_MODE,
    e.BTN_THUMBL, e.BTN_THUMBR,
]


def calibrate(value, phys_min, phys_center, phys_max):
    """Линейно масштабирует физическое значение в -32767..+32767 с deadzone."""
    if abs(value - phys_center) <= DEADZONE:
        return 0
    if value < phys_center:
        if phys_center == phys_min:
            return 0
        scaled = (value - phys_center) / (phys_center - phys_min)
        return int(scaled * 32767)
    else:
        if phys_max == phys_center:
            return 0
        scaled = (value - phys_center) / (phys_max - phys_center)
        return int(scaled * 32767)


def trigger_release(ui, code):
    """Форсировать отпускание триггера: 1 → syn → 0 → syn."""
    ui.write(e.EV_ABS, code, 1)
    ui.syn()
    time.sleep(0.02)
    ui.write(e.EV_ABS, code, 0)
    ui.syn()
    time.sleep(0.02)


def force_zero_triggers(ui):
    for code in (e.ABS_Z, e.ABS_RZ):
        trigger_release(ui, code)


def release_all(ui):
    try:
        for btn in ALL_BUTTONS:
            ui.write(e.EV_KEY, btn, 0)
        for axis in [e.ABS_X, e.ABS_Y, e.ABS_RX, e.ABS_RY,
                     e.ABS_HAT0X, e.ABS_HAT0Y]:
            ui.write(e.EV_ABS, axis, 0)
        ui.syn()
        time.sleep(0.05)
        force_zero_triggers(ui)
    except Exception:
        pass


def sync_state(gamepad, ui):
    try:
        for axis, cal in CALIBRATION.items():
            info = gamepad.absinfo(axis)
            new_val = calibrate(info.value, *cal)
            ui.write(e.EV_ABS, axis, new_val)
        ui.syn()
        force_zero_triggers(ui)
    except Exception as ex:
        print(f"[WARN] sync_state: {ex}")


def main():
    try:
        gamepad = evdev.InputDevice(DEVICE_PATH)
    except FileNotFoundError:
        print(f"Устройство {DEVICE_PATH} не найдено!")
        print("Найди правильный путь командой: evtest")
        sys.exit(1)

    print(f"Читаю: {gamepad.name}")
    print(f"Deadzone: ±{DEADZONE}  Trigger threshold: {TRIGGER_THRESHOLD}")

    # Копируем capabilities прямо с реального устройства
    # чтобы границы осей (min/max) были правильными
    capabilities = gamepad.capabilities()
    capabilities.pop(e.EV_FF, None)
    capabilities.pop(e.EV_SYN, None)

    ui = UInput(
        capabilities,
        name="Xbox 360 Calibrated",
        vendor=0x045e,
        product=0x028e,
        version=0x110,
    )

    print(f"Виртуальный контроллер создан: Xbox 360 Calibrated\n")

    trigger_state = {e.ABS_Z: 0, e.ABS_RZ: 0}

    def shutdown(sig, frame):
        print("\nОстановка — сброс всех кнопок...")
        release_all(ui)
        try:
            gamepad.ungrab()
        except Exception:
            pass
        ui.close()
        gamepad.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Читаем состояние ДО grab
    print("Начальное состояние осей (raw → откалибровано):")
    initial_state = {}
    for axis in CALIBRATION:
        initial_state[axis] = gamepad.absinfo(axis).value

    gamepad.grab()

    # Отправить начальное откалиброванное состояние стиков
    for axis, cal in CALIBRATION.items():
        raw = initial_state[axis]
        cal_val = calibrate(raw, *cal)
        ui.write(e.EV_ABS, axis, cal_val)
        name = e.ABS[axis]
        status = "OK" if cal_val == 0 else f"ВНИМАНИЕ: {cal_val}"
        print(f"  {name:10s}: raw={raw:7d} → cal={cal_val:7d}  {status}")
    ui.syn()

    # Принудительно сбросить триггеры через 1→0
    print("\n  Обнуление триггеров (1→0)...")
    force_zero_triggers(ui)
    print("  LT/RT → 0  OK")

    ui.write(e.EV_ABS, e.ABS_HAT0X, 0)
    ui.write(e.EV_ABS, e.ABS_HAT0Y, 0)
    ui.syn()

    print("\nНажми Ctrl+C для остановки\n")

    try:
        for event in gamepad.read_loop():

            if event.type == e.EV_SYN:
                if event.code == e.SYN_DROPPED:
                    print("[WARN] SYN_DROPPED — пересинхронизация")
                    sync_state(gamepad, ui)
                else:
                    ui.syn()

            elif event.type == e.EV_ABS:
                if event.code in CALIBRATION:
                    phys_min, phys_center, phys_max = CALIBRATION[event.code]
                    new_value = calibrate(event.value, phys_min, phys_center, phys_max)
                    ui.write(e.EV_ABS, event.code, new_value)

                elif event.code in (e.ABS_Z, e.ABS_RZ):
                    val = event.value if event.value > TRIGGER_THRESHOLD else 0
                    prev = trigger_state[event.code]
                    if val == 0 and prev > 0:
                        trigger_release(ui, event.code)
                    elif val != prev:
                        ui.write(e.EV_ABS, event.code, val)
                    trigger_state[event.code] = val

                else:
                    ui.write(e.EV_ABS, event.code, event.value)

            elif event.type == e.EV_KEY:
                ui.write(e.EV_KEY, event.code, event.value)

    except OSError as ex:
        print(f"Устройство отключено: {ex}")
        release_all(ui)
        try:
            gamepad.ungrab()
        except Exception:
            pass
        ui.close()


if __name__ == "__main__":
    main()
