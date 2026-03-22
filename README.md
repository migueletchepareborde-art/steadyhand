# SteadyHand

**Dispositivo wearable de estabilización activa para temblor de Parkinson**

Miguel Ángel Etchepareborde & Xoan Barreiro — Buenos Aires, Argentina — Hackathon 2026

---

## Inicio rápido — uso del sistema

> Para el paciente, médico o familiar que recibe el dispositivo ya configurado.

**1. Configurar el WiFi del dispositivo**

- En el celular o PC, conectarse a la red WiFi llamada **SteadyHand** (sin contraseña)
- Abrir el navegador y entrar a **http://192.168.4.1/setup**
- Ingresar el nombre y contraseña del WiFi local → tocar **Guardar y conectar**
- El dispositivo se reinicia solo y queda conectado a la red

> Para cambiar de red en cualquier momento repetir este paso — el AP **SteadyHand** siempre está disponible.

**2. Abrir el monitor**

- Abrir el archivo `index.html` en Chrome o Firefox
- Escribir la IP del dispositivo en el campo superior (la IP aparece en pantalla al arrancar, ej: `192.168.0.9`)
- Hacer click en **Conectar**

**3. Monitorear**

- El gráfico muestra la frecuencia del temblor en Hz, actualizado cada 15 segundos
- El historial se guarda automáticamente en el navegador por 7 días
- Desde el panel de parámetros se puede ajustar la sensibilidad del dispositivo en tiempo real

---

## Qué es

SteadyHand es una muñequera/guante electrónico que detecta el temblor involuntario de Parkinson (4–7 Hz) y aplica resistencia mecánica activa en tiempo real para estabilizar la mano, sin cirugía y sin fármacos. El costo de fabricación es menor a $80 USD.

---

## Cómo funciona

El acelerómetro MMA7361 mide la aceleración de la mano en los ejes X e Y continuamente a 100 Hz. La Raspberry Pi Pico W separa la señal en dos canales usando filtros EMA (Exponential Moving Average):

- **Canal lento** (alpha = 0.02) — sigue la posición e inclinación voluntaria de la mano
- **Canal rápido** (vib = raw - posición) — aísla la vibración de alta frecuencia, es decir, el temblor

La salida de cada servo se calcula como:

```
servo = 90° + (posición × gain_pos) + (vibración × gain_vib)
```

Dos servos SG90 conectados a un sistema de bandas elásticas antagonistas aplican resistencia proporcional al temblor detectado, en ambas direcciones, con un solo motor por eje.

---

## Hardware

| Componente | Detalle | Pin |
|---|---|---|
| Microcontrolador | Raspberry Pi Pico W (RP2040 + CYW43439 WiFi) | — |
| Sensor | Acelerómetro analógico MMA7361 eje X | GP26 (ADC0) |
| Sensor | Acelerómetro analógico MMA7361 eje Y | GP27 (ADC1) |
| Actuador X | Servo SG90 | GP15 (PWM) |
| Actuador Y | Servo SG90 | GP14 (PWM) |
| Transmisión | Doble banda elástica antagonista con polea central | — |

### Conexiones MMA7361

| MMA7361 | Pico W | Nota |
|---|---|---|
| VCC | 3V3 (pin 36) | ⚠ Solo 3.3V |
| GND | GND | |
| XOUT | GP26 | ADC0 |
| YOUT | GP27 | ADC1 |
| SLEEP | 3V3 | Mantener HIGH |

### Conexiones SG90

| SG90 | Pico W | Nota |
|---|---|---|
| Naranja (señal X) | GP15 | PWM 50 Hz |
| Naranja (señal Y) | GP14 | PWM 50 Hz |
| Rojo | VBUS (pin 40) | 5V desde USB |
| Marrón | GND | |

---

## Instalación y desarrollo

> Para quien quiere replicar, modificar o entender el proyecto a nivel técnico.

### Archivos del proyecto

```
steadyhand/
├── main.py              ← Firmware del Pico W (MicroPython)
├── setup.html           ← Portal de configuración WiFi (se sube al Pico)
├── index.html           ← Dashboard web (se abre en el navegador del PC)
└── Pagina_Web_Principal.html  ← Sitio web del proyecto
```

---

### Instalar en el Pico W

### 1. Flashear MicroPython

1. Descargar el firmware: https://micropython.org/download/RPI_PICO_W/
2. Mantener el botón **BOOTSEL** presionado al conectar el USB
3. Arrastrar el archivo `.uf2` al disco `RPI-RP2` que aparece

### 2. Subir los archivos

Con **Thonny** (recomendado):

```
Herramientas → Opciones → Intérprete → MicroPython (Raspberry Pi Pico)
```

Para cada archivo (`main.py` y `setup.html`):

```
Fichero → Abrir → buscar el archivo → Fichero → Guardar como → Dispositivo MicroPython
```

También se puede usar `mpremote`:

```bash
pip install mpremote
mpremote connect auto cp main.py :main.py
mpremote connect auto cp setup.html :setup.html
```

---

### Conectar el dispositivo a WiFi (desde Thonny)

El Pico W siempre levanta su propio punto de acceso WiFi llamado **SteadyHand** (sin contraseña). Para conectarlo a una red:

1. Conectarse a la red **SteadyHand** desde el celular o PC
2. Abrir el navegador y entrar a `http://192.168.4.1/setup`
3. Ingresar el nombre y contraseña del WiFi deseado
4. Tocar **Guardar y conectar** — el Pico reinicia y se conecta
5. La nueva IP aparece en la consola de Thonny: `[STA] Conectado → http://192.168.X.X`

Para cambiar de red en cualquier momento: volver a conectarse a **SteadyHand** y repetir el proceso. El AP siempre está activo independientemente del estado de la conexión al router.

---

### Usar el dashboard web

1. Abrir `index.html` en el navegador (Chrome o Firefox)
2. Escribir la IP del Pico en el campo superior (ej: `192.168.0.9`)
3. Hacer click en **Conectar**

El dashboard actualiza cada 15 segundos y muestra:

- **Frecuencia del temblor** en Hz (promedio del período)
- **Uptime** del dispositivo
- **Gráfico** con 3 vistas: última hora, hoy, semana
- **Parámetros ajustables** en tiempo real sin reiniciar el Pico
- **Historial** guardado en el navegador por 7 días, exportable como CSV

---

### API del Pico W

Todos los endpoints responden JSON con CORS habilitado.

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/setup` | Portal de configuración WiFi (HTML) |
| POST | `/save` | Guardar credenciales WiFi y reiniciar |
| GET | `/forget` | Olvidar red WiFi y reiniciar |
| GET | `/status` | Estado del dispositivo |
| GET | `/data/live` | Señales en tiempo real |
| GET | `/data/hour` | Buffer última hora (240 muestras × 15s) |
| GET | `/config` | Parámetros actuales |
| POST | `/config` | Actualizar parámetros |

### Ejemplo `/data/live`

```json
{
  "raw_x": 1.23,
  "raw_y": -0.45,
  "pos_x": 0.98,
  "pos_y": -0.41,
  "vib_x": 0.25,
  "vib_y": -0.04,
  "amplitude": 0.253,
  "freq_hz": 5.1,
  "servo_x": 94,
  "servo_y": 89,
  "correction": 4.4,
  "uptime_s": 142,
  "ap_ip": "192.168.4.1",
  "sta_ip": "192.168.0.9",
  "sta_ssid": "MiRed",
  "sta_status": "conectado"
}
```

### Ejemplo POST `/config`

```bash
curl -X POST http://192.168.0.9/config \
  -H "Content-Type: application/json" \
  -d '{"gain_vib": 1.2, "gain_pos": 0.25}'
```

---

### Parámetros de control

| Parámetro | Descripción | Default | Rango |
|---|---|---|---|
| `alpha_pos` | Velocidad del filtro lento (posición/gravedad) | 0.02 | 0.01–0.2 |
| `alpha_vib` | Velocidad del filtro rápido (vibración) | 0.2 | 0.05–0.5 |
| `gain_vib` | Cuánto compensa el temblor | 1.0 | 0.0–3.0 |
| `gain_pos` | Cuánto sigue la inclinación de la mano | 0.3 | 0.0–1.0 |
| `offset_x` | Punto de reposo ADC eje X (16-bit) | 21588 | calibrar |
| `offset_y` | Punto de reposo ADC eje Y (16-bit) | 21588 | calibrar |

### Calibración del offset

Con el dispositivo en reposo horizontal, leer `raw_x` y `raw_y` de `/data/live`. Si no son 0, ajustar `offset_x` y `offset_y` sumándoles el valor leído:

```bash
# Si raw_x = 2.3 y offset_x actual = 21588
# nuevo offset_x = 21588 + (2.3 × 655.35) ≈ 23100
curl -X POST http://192.168.0.9/config \
  -d '{"offset_x": 23100}'
```

---

### Algoritmo de detección de frecuencia

La frecuencia del temblor se calcula por **zero-crossing** sobre el canal de vibración `vib_x`:

1. Cada vez que la señal cruza de negativo a positivo se mide el tiempo desde el cruce anterior
2. La frecuencia instantánea es `f = 1000 / (2 × dt_ms)` Hz
3. Solo se acumulan cruces con `50 < dt < 500 ms` (rango válido: 1–10 Hz) y amplitud `|vib_x| > 500` unidades (filtro de ruido)
4. Cada 15 segundos se publica el promedio de todas las frecuencias acumuladas

---

### Consumo de RAM estimado

| Componente | RAM aprox. |
|---|---|
| MicroPython runtime | ~80 KB |
| uasyncio + WiFi stack | ~40 KB |
| Buffer última hora (240 snapshots) | ~8 KB |
| Estado + config | ~2 KB |
| **Libre disponible** | **~130 KB** |

El Pico W tiene 264 KB de RAM total.

---

### Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| Frecuencia siempre distinta de 0 sin sensor | Pines ADC flotantes captan ruido | Normal — con el sensor conectado desaparece |
| Frecuencia siempre 0 con sensor | Umbral de ruido demasiado alto | Bajar `500` en `update_zc` |
| Servo vibra en reposo | `gain_vib` muy alto | Reducir `gain_vib` a 0.5–0.8 |
| Servo no responde | `gain_vib` muy bajo o offset mal calibrado | Calibrar offset, subir `gain_vib` |
| Dashboard dice "Sin señal" | IP incorrecta o diferente red | Verificar IP en Thonny, misma red WiFi |
| MemoryError en Thonny | RAM insuficiente | Ya resuelto en v4.0 con chunked transfer |
| Error de conexión en setup | Contraseña menor a 8 caracteres | WPA2 requiere mínimo 8 caracteres |
| Red SteadyHand no aparece | Pico no booteo correctamente | Desconectar y reconectar USB |

---

### Tecnologías

- **MicroPython** — firmware del Pico W
- **uasyncio** — concurrencia entre control (100 Hz) y servidor HTTP
- **HTML/CSS/JS vanilla** — dashboard web sin frameworks ni dependencias externas
- **Canvas API** — gráficos en tiempo real sin librerías
- **localStorage** — historial semanal en el navegador sin servidor

---

## Autores

**Miguel Ángel Etchepareborde** — Hardware · Raspberry Pi · Vibe Coding · Electrónica · Impresión 3D

**Xoan Barreiro** — Hardware · Prototipado · Robótica · Mecánica

Buenos Aires, Argentina — 2026
