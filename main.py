"""
SteadyHand — Raspberry Pi Pico W  v4.0
Miguel Ángel Etchepareborde & Xoan Barreiros — Buenos Aires

CONTROL: Migración desde Arduino C
  - Dos servos (X e Y) con doble canal EMA (lento=posición, rápido=vibración)
  - Acelerómetro MMA7361 en GP26 (X) y GP27 (Y)
  - Frecuencia de temblor por zero-crossing sobre vibración XY
  - Promedio de frecuencia cada 15 segundos → buffer horario

CONECTIVIDAD:
  - AP "SteadyHand" SIEMPRE activo (192.168.4.1)
  - Si existe wifi.json → modo dual AP+STA
  - Portal de configuración en /setup (sin Thonny)

ENDPOINTS:
  GET  /setup        → formulario WiFi
  POST /save         → guarda wifi.json y reinicia
  GET  /forget       → borra wifi.json y reinicia
  GET  /status       → estado del dispositivo
  GET  /data/live    → señales en tiempo real (JSON)
  GET  /data/hour    → buffer circular última hora (JSON chunked)
  GET  /config       → parámetros actuales
  POST /config       → actualizar parámetros
"""

import network
import uasyncio as asyncio
from machine import ADC, PWM, Pin, reset
import ujson
import time
import os
import gc
import usocket as socket

# ─────────────────────────────────────────────────────────────────
#  sqrt sin importar math
# ─────────────────────────────────────────────────────────────────
def _sqrt(x):
    if x <= 0.0:
        return 0.0
    r = x
    for _ in range(8):
        r = (r + x / r) * 0.5
    return r

# ─────────────────────────────────────────────────────────────────
#  CONSTANTES
# ─────────────────────────────────────────────────────────────────
AP_SSID   = "SteadyHand"
AP_IP     = "192.168.4.1"
WIFI_FILE = "wifi.json"

# ─────────────────────────────────────────────────────────────────
#  PINES
#  MMA7361: XOUT→GP26, YOUT→GP27
#  Servo X→GP15,  Servo Y→GP14
# ─────────────────────────────────────────────────────────────────
ADC_X_PIN   = 26
ADC_Y_PIN   = 27
SERVO_X_PIN = 15
SERVO_Y_PIN = 14
LED_PIN     = "LED"

# ─────────────────────────────────────────────────────────────────
#  PARÁMETROS — equivalentes directos al código Arduino C
#  Ajustables en caliente vía POST /config
# ─────────────────────────────────────────────────────────────────
config = {
    # Canal lento EMA — posición / gravedad  (alpha_pos en C)
    "alpha_pos":  0.02,
    # Canal rápido EMA — vibración / temblor (alpha_vib en C)
    "alpha_vib":  0.2,
    # Ganancia compensación vibración        (gain_vib en C)
    "gain_vib":   1.0,
    # Ganancia seguimiento posición          (gain_pos en C)
    "gain_pos":   0.3,
    # Offset ADC en reposo (16-bit)
    # Arduino 10-bit OFFSET=337 → Pico 16-bit: 337/1023 * 65535 ≈ 21588
    "offset_x":   21588,
    "offset_y":   21588,
}

# ─────────────────────────────────────────────────────────────────
#  BUFFERS Y ESTADO
# ─────────────────────────────────────────────────────────────────
BUF_HOUR = 240
buf_hour = []

live = {
    "raw_x":      0.0,   # señal cruda centrada en 0, escalada -100..100
    "raw_y":      0.0,
    "pos_x":      0.0,   # canal lento X (posición/gravedad)
    "pos_y":      0.0,
    "vib_x":      0.0,   # canal rápido X (vibración/temblor)
    "vib_y":      0.0,
    "amplitude":  0.0,   # módulo de vibración XY
    "freq_hz":    0.0,   # promedio de frecuencia del último periodo de 15s
    "servo_x":    90,
    "servo_y":    90,
    "correction": 0.0,
    "uptime_s":   0,
    "ap_ip":      AP_IP,
    "sta_ip":     None,
    "sta_ssid":   None,
    "sta_status": "sin_red",
}

# ─────────────────────────────────────────────────────────────────
#  HARDWARE
# ─────────────────────────────────────────────────────────────────
adc_x = ADC(Pin(ADC_X_PIN))
adc_y = ADC(Pin(ADC_Y_PIN))
led   = Pin(LED_PIN, Pin.OUT)
pwm_x = PWM(Pin(SERVO_X_PIN)); pwm_x.freq(50)
pwm_y = PWM(Pin(SERVO_Y_PIN)); pwm_y.freq(50)

def _ns(angle):
    return int(500_000 + (max(0, min(180, angle)) / 180.0) * 2_000_000)

def set_sx(a): a = max(0, min(180, int(a))); pwm_x.duty_ns(_ns(a)); return a
def set_sy(a): a = max(0, min(180, int(a))); pwm_y.duty_ns(_ns(a)); return a

# El MMA7361 devuelve valores 0-65535 (16-bit del Pico)
# Centramos en 0 restando el offset, igual que el Arduino hacía analogRead - OFFSET
def rdx(): return adc_x.read_u16() - config["offset_x"]
def rdy(): return adc_y.read_u16() - config["offset_y"]

# ─────────────────────────────────────────────────────────────────
#  DETECCIÓN DE FRECUENCIA — zero-crossing + promedio 15s
#  Solo cuenta cruces cuando la amplitud supera el umbral de ruido.
#  En reposo el ADC flota y genera cruces falsos → se ignoran.
# ─────────────────────────────────────────────────────────────────
_zc_last = 0
_zc_prev = 0
_freq_acc = []
_freq_pub = 0.0    # valor publicado en /data/live y guardado en buf_hour

# Umbral mínimo de amplitud para considerar un cruce válido.
# 3000 unidades de 16-bit ≈ 4.5% de escala = ignora ruido eléctrico del ADC.
# Subir si en reposo sigue marcando frecuencia. Bajar si no detecta temblor real.
NOISE_THRESHOLD = 3000

def update_zc(vib, now_ms):
    global _zc_last, _zc_prev
    sign = 1 if vib >= 0 else -1
    if sign > 0 and _zc_prev < 0:
        dt = time.ticks_diff(now_ms, _zc_last)
        # Solo acumular si hay movimiento real (no ruido) y frecuencia válida (1-10 Hz)
        if 50 < dt < 500 and abs(vib) > NOISE_THRESHOLD:
            _freq_acc.append(1000.0 / (2.0 * dt))
        _zc_last = now_ms
    _zc_prev = sign

def flush_freq():
    global _freq_pub, _freq_acc
    if _freq_acc:
        _freq_pub = round(sum(_freq_acc) / len(_freq_acc), 2)
    else:
        _freq_pub = 0.0   # sin movimiento real → frecuencia 0
    _freq_acc = []
    return _freq_pub

# ─────────────────────────────────────────────────────────────────
#  LOOP DE CONTROL (100 Hz)
#  Traducción directa del loop() de Arduino:
#
#  C:  rawX = analogRead(PIN_X) - OFFSET_X;
#  Py: raw_x = rdx()
#
#  C:  posX = alpha_pos*rawX + (1-alpha_pos)*posX;
#  Py: pos_x = a_pos*raw_x + (1-a_pos)*pos_x
#
#  C:  vibX = rawX - posX;
#  Py: vib_x = raw_x - pos_x
#
#  C:  outX = 90 + (posX*gain_pos) + (vibX*gain_vib);
#  Py: out_x = 90 + (pos_x*g_pos) + (vib_x*g_vib)
#
#  C:  outX = constrain(outX, 0, 180);
#  Py: out_x = max(0, min(180, out_x))
# ─────────────────────────────────────────────────────────────────
async def control_loop():
    pos_x = 0.0
    pos_y = 0.0
    t0        = time.ticks_ms()
    last_snap = 0

    while True:
        now  = time.ticks_ms()
        a_pos = config["alpha_pos"]
        g_pos = config["gain_pos"]
        g_vib = config["gain_vib"]

        # 1. Leer — centrado en 0
        raw_x = rdx()
        raw_y = rdy()

        # 2. Canal lento (posición/gravedad)
        pos_x = a_pos * raw_x + (1.0 - a_pos) * pos_x
        pos_y = a_pos * raw_y + (1.0 - a_pos) * pos_y

        # 3. Canal rápido (vibración/temblor)
        vib_x = raw_x - pos_x
        vib_y = raw_y - pos_y

        # 4. Salida de cada servo (centrada en 90°)
        out_x = 90.0 + (pos_x * g_pos) + (vib_x * g_vib)
        out_y = 90.0 + (pos_y * g_pos) + (vib_y * g_vib)

        # 5. Limitar (constrain)
        out_x = max(0.0, min(180.0, out_x))
        out_y = max(0.0, min(180.0, out_y))

        # 6. Escribir servos
        ax = set_sx(out_x)
        ay = set_sy(out_y)

        # 7. Amplitud y frecuencia
        amplitude = _sqrt(vib_x * vib_x + vib_y * vib_y)
        update_zc(vib_x, now)   # acumula zero-crossings

        # Corrección como % de desviación máxima del centro
        corr = round(max(abs(ax - 90), abs(ay - 90)) / 90.0 * 100.0, 1)

        # Escala de 16-bit a unidades legibles (-100..100)
        sc = 655.35
        live["raw_x"]     = round(raw_x / sc, 2)
        live["raw_y"]     = round(raw_y / sc, 2)
        live["pos_x"]     = round(pos_x / sc, 2)
        live["pos_y"]     = round(pos_y / sc, 2)
        live["vib_x"]     = round(vib_x / sc, 2)
        live["vib_y"]     = round(vib_y / sc, 2)
        live["amplitude"] = round(amplitude / sc, 3)
        live["freq_hz"]   = _freq_pub
        live["servo_x"]   = ax
        live["servo_y"]   = ay
        live["correction"]= corr
        live["uptime_s"]  = time.ticks_diff(now, t0) // 1000

        # 8. Snapshot + promedio de frecuencia cada 15s
        if time.ticks_diff(now, last_snap) >= 15000:
            last_snap = now
            freq_avg  = flush_freq()
            live["freq_hz"] = freq_avg
            snap = [now, freq_avg, round(amplitude / sc, 3), ax, ay, corr]
            buf_hour.append(snap)
            if len(buf_hour) > BUF_HOUR:
                buf_hour.pop(0)

        led.value(1 if amplitude > 1000 else 0)
        await asyncio.sleep_ms(10)

# ─────────────────────────────────────────────────────────────────
#  DNS
# ─────────────────────────────────────────────────────────────────
async def dns_server():
    ip_bytes = bytes([int(x) for x in AP_IP.split(".")])
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("0.0.0.0", 53)); udp.setblocking(False)
    print("[DNS] Puerto 53 activo")
    while True:
        try:
            data, addr = udp.recvfrom(512)
            tid  = data[:2]
            resp = (tid + b'\x81\x80' + data[4:6] + b'\x00\x01'
                    + b'\x00\x00\x00\x00' + data[12:]
                    + b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x1e\x00\x04' + ip_bytes)
            udp.sendto(resp, addr)
        except:
            pass
        await asyncio.sleep_ms(10)

# ─────────────────────────────────────────────────────────────────
#  WIFI
# ─────────────────────────────────────────────────────────────────
def load_wifi():
    try:
        with open(WIFI_FILE) as f: return ujson.load(f)
    except: return None

def save_wifi(ssid, pw):
    with open(WIFI_FILE,"w") as f: ujson.dump({"ssid":ssid,"password":pw},f)

def forget_wifi():
    try: os.remove(WIFI_FILE)
    except: pass

def start_ap():
    ap = network.WLAN(network.AP_IF)
    ap.active(True); ap.config(ssid=AP_SSID, security=0)
    t = 10
    while not ap.active() and t > 0: time.sleep(0.2); t -= 1
    print(f"[AP] '{AP_SSID}'  →  http://{AP_IP}")

def try_sta(ssid, pw):
    live["sta_status"] = "conectando"; live["sta_ssid"] = ssid
    print(f"[STA] Conectando a '{ssid}'", end="")
    sta = network.WLAN(network.STA_IF); sta.active(True); sta.connect(ssid, pw)
    t = 20
    while not sta.isconnected() and t > 0: print(".",end=""); time.sleep(1); t -= 1
    if sta.isconnected():
        ip = sta.ifconfig()[0]; live["sta_ip"] = ip; live["sta_status"] = "conectado"
        print(f"\n[STA] Conectado  →  http://{ip}"); return True
    sta.active(False); live["sta_status"] = "fallo"; live["sta_ip"] = None
    print(f"\n[STA] Fallo"); return False

# ─────────────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────────────
CORS = ("Access-Control-Allow-Origin: *\r\n"
        "Access-Control-Allow-Methods: GET,POST,OPTIONS\r\n"
        "Access-Control-Allow-Headers: Content-Type\r\n")

def rj(status, obj):
    gc.collect()
    body = ujson.dumps(obj).encode()
    return (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n{CORS}Connection: close\r\n\r\n"
           ).encode() + body

def rr(loc): return f"HTTP/1.1 302 Found\r\nLocation: {loc}\r\nConnection: close\r\n\r\n".encode()

async def write_buf(writer, buf):
    gc.collect()
    writer.write((f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                  f"{CORS}Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n").encode())
    writer.write(b"1\r\n[\r\n")
    for i, s in enumerate(buf):
        gc.collect()
        c = ujson.dumps(s) + ("," if i < len(buf)-1 else "") + "\n"
        writer.write(f"{hex(len(c))[2:]}\r\n{c}\r\n".encode())
        await writer.drain()
    writer.write(b"1\r\n]\r\n0\r\n\r\n")
    await writer.drain()

async def handle(reader, writer):
    try:
        line = await asyncio.wait_for(reader.readline(), 5.0)
        line = line.decode().strip()
        if not line: return
        cl = 0
        while True:
            h = await asyncio.wait_for(reader.readline(), 3.0)
            hs = h.decode().strip()
            if hs.lower().startswith("content-length:"): cl = int(hs.split(":")[1].strip())
            if hs == "": break
        body = b""
        if cl > 0: body = await asyncio.wait_for(reader.read(cl), 3.0)
        parts  = line.split(" ")
        method = parts[0] if parts else "GET"
        path   = parts[1].split("?")[0] if len(parts) > 1 else "/"
        print(f"[REQ] {method} {path}")

        if method == "OPTIONS":
            writer.write(rj("204 No Content", {}))

        elif path in ("/", "/setup") and method == "GET":
            try:
                size = os.stat("setup.html")[6]
                writer.write((f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                              f"Content-Length: {size}\r\nConnection: close\r\n\r\n").encode())
                with open("setup.html","rb") as f:
                    while True:
                        chunk = f.read(512)
                        if not chunk: break
                        writer.write(chunk); await writer.drain()
            except Exception as e:
                writer.write(rj("500 Internal Server Error", {"err": str(e)}))

        elif path == "/save" and method == "POST":
            try:
                raw = body.decode('utf-8','ignore').strip()
                params = {}
                for pair in raw.split("&"):
                    pair = pair.strip()
                    if "=" in pair and len(pair) < 200:
                        k, v = pair.split("=",1)
                        v = v.replace("+"," ")
                        for e,c in [("%21","!"),("%40","@"),("%2F","/"),("%3A",":"),("%5F","_"),("%2D","-"),("%2E",".")]:
                            v = v.replace(e,c).replace(e.lower(),c)
                        if k.strip() in ("ssid","password"): params[k.strip()] = v
                ssid = params.get("ssid","").strip(); pw = params.get("password","")
                print(f"[Setup] ssid='{ssid}' len={len(pw)}")
                if not ssid or len(pw) < 8:
                    writer.write(rr("/setup"))
                else:
                    save_wifi(ssid, pw)
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n<html><body style='background:#07080b;color:#00ffe0;font-family:sans-serif;text-align:center;padding:40px'><h2>Guardado</h2><p style='color:#94a3b8'>Reiniciando...</p></body></html>")
                    await writer.drain(); writer.close(); await writer.wait_closed()
                    await asyncio.sleep(2); reset(); return
            except Exception as e:
                print(f"[Setup] Error: {e}"); writer.write(rr("/setup"))

        elif path == "/forget":
            forget_wifi(); writer.write(rj("200 OK",{"ok":True}))
            await writer.drain(); writer.close(); await writer.wait_closed()
            await asyncio.sleep(1); reset(); return

        elif path == "/status":
            writer.write(rj("200 OK",{
                "device":"SteadyHand Pico W v4.0","ap_ip":AP_IP,
                "sta_ip":live["sta_ip"],"sta_ssid":live["sta_ssid"],
                "sta_status":live["sta_status"],"uptime_s":live["uptime_s"],
                "buf_hour":len(buf_hour)}))

        elif path == "/data/live":
            writer.write(rj("200 OK", live))

        elif path == "/data/hour":
            await write_buf(writer, buf_hour); return

        elif path == "/config" and method == "GET":
            writer.write(rj("200 OK", config))

        elif path == "/config" and method == "POST":
            try:
                u = ujson.loads(body.decode())
                for k,v in u.items():
                    if k in config: config[k] = float(v)
                writer.write(rj("200 OK",{"ok":True,"config":config}))
            except Exception as e:
                writer.write(rj("400 Bad Request",{"ok":False,"err":str(e)}))

        else:
            writer.write(rj("404 Not Found",{"err":"not found"}))

        await writer.drain()

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        import sys; print(f"[HTTP] {e}"); sys.print_exception(e)
    finally:
        try: writer.close(); await writer.wait_closed()
        except: pass

async def start_server():
    srv = await asyncio.start_server(handle, "0.0.0.0", 80)
    print("[HTTP] Puerto 80 activo")
    async with srv: await srv.wait_closed()

# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────
async def main():
    print("="*50)
    print("  SteadyHand Pico W v4.0")
    print("  Doble servo XY · Frecuencia promedio 15s")
    print("="*50)
    start_ap()
    creds = load_wifi()
    if creds:
        print(f"[WiFi] Credenciales para '{creds['ssid']}'")
        if not try_sta(creds["ssid"], creds["password"]):
            print("[WiFi] Fallo. Solo AP activo.")
    else:
        print(f"[WiFi] Sin credenciales → http://{AP_IP}/setup")
    gc.collect()
    print(f"[Mem] RAM libre: {gc.mem_free()} bytes")
    asyncio.create_task(control_loop())
    asyncio.create_task(dns_server())
    asyncio.create_task(start_server())
    print("[Sistema] Listo.")
    while True: await asyncio.sleep(30)

asyncio.run(main())

