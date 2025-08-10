#!/usr/bin/env python3
import os, socket, sys

def check_dir(path: str) -> bool:
    return bool(path) and os.path.isdir(path)

def check_tcp(host: str | None, port: str | int | None, timeout: float = 2.0) -> bool:
    if not host or not port:
        return True
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False

errors: list[str] = []

watch = os.getenv('WATCH_FOLDER')
if not check_dir(watch):
    errors.append(f"WATCH_FOLDER missing or not a directory: {watch!r}")

pg_h = os.getenv('POSTGRES_HOST')
pg_p = os.getenv('POSTGRES_PORT', '5432')
if pg_h and not check_tcp(pg_h, pg_p):
    errors.append('Postgres unreachable')

mqtt_h = os.getenv('MQTT_HOST')
mqtt_p = os.getenv('MQTT_PORT', '1883')
if mqtt_h and not check_tcp(mqtt_h, mqtt_p):
    errors.append('MQTT unreachable')

if errors:
    print('; '.join(errors))
    sys.exit(1)

print('OK')
sys.exit(0)
