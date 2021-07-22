#!/usr/bin/env python3

import time
import colorsys
import sys
import ST7735
try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559

from bme280 import BME280
from pms5003 import PMS5003, ReadTimeoutError as pmsReadTimeoutError
from enviroplus import gas
from subprocess import PIPE, Popen
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from fonts.ttf import RobotoMedium as UserFont
import logging
from influxdb import InfluxDBClient
import RPi.GPIO as GPIO


logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

logging.info("""all-in-one.py - Displays readings from all of Enviro plus' sensors

Press Ctrl+C to exit!

""")

# BME280 temperature/pressure/humidity sensor
bme280 = BME280()

# PMS5003 particulate sensor
pms5003 = PMS5003()

# Create ST7735 LCD display class
st7735 = ST7735.ST7735(
    port=0,
    cs=1,
    dc=9,
    backlight=12,
    rotation=270,
    spi_speed_hz=10000000
)

# Initialize display
st7735.begin()

WIDTH = st7735.width
HEIGHT = st7735.height

# Set up canvas and font
img = Image.new('RGB', (WIDTH, HEIGHT), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
font_size = 20
font = ImageFont.truetype(UserFont, font_size)

message = ""

# The position of the top bar
top_pos = 25


# Displays data and text on the 0.96" LCD
def display_text(mode):
    variable, label, unit= modes[mode]
    data = values[variable][-1]
    # Scale the values for the variable between 0 and 1
    vmin = min(values[variable])
    vmax = max(values[variable])
    colours = [(v - vmin + 1) / (vmax - vmin + 1) for v in values[variable]]
    # Format the variable name and value
    message = "{}: {:.1f} {}".format(label[:4], data, unit)
    logging.info(message)
    draw.rectangle((0, 0, WIDTH, HEIGHT), (255, 255, 255))
    for i in range(len(colours)):
        # Convert the values to colours from red to blue
        colour = (1.0 - colours[i]) * 0.6
        r, g, b = [int(x * 255.0) for x in colorsys.hsv_to_rgb(colour, 1.0, 1.0)]
        # Draw a 1-pixel wide rectangle of colour
        draw.rectangle((i, top_pos, i + 1, HEIGHT), (r, g, b))
        # Draw a line graph in black
        line_y = HEIGHT - (top_pos + (colours[i] * (HEIGHT - top_pos))) + top_pos
        draw.rectangle((i, line_y, i + 1, line_y + 1), (0, 0, 0))
    # Write the text at the top in black
    draw.text((0, 0), message, font=font, fill=(0, 0, 0))
    st7735.display(img)


# Get the temperature of the CPU for compensation
def get_cpu_temperature():
    process = Popen(['vcgencmd', 'measure_temp'], stdout=PIPE, universal_newlines=True)
    output, _error = process.communicate()
    return float(output[output.index('=') + 1:output.rindex("'")])


# Tuning factor for compensation. Decrease this number to adjust the
# temperature down, and increase to adjust up
factor = 2.25

cpu_temps = [get_cpu_temperature()] * 5

delay = 0.5  # Debounce the proximity tap
mode = 0     # The starting mode
last_page = 0
light = 1

influx = InfluxDBClient(host="alexandria.local",
                        database="enviro")

influx_json_prototyp = [
        {
            "measurement": "enviroplus",
            "tags": {
                "host": "enviroplus"
            },
            "fields": {
            }
        }
    ]


from collections import deque, defaultdict
#key, label, unit
modes = [
    ("bme280.temp.corrected", "temperature", "C"),
    ("bme280.pressure", "pressure", "hPa"),
    ("bme280.humidity", "humidity", "%"),
    ("ltr559.lux", "light", "Lux"),
    ("mics6814.oxidising", "oxidised", "kO"),
    ("mics6814.reducing", "reduced", "kO"),
    ("mics6814.nh3", "nh3", "kO"),
    ("pms5003.pm010", "pm1", "ug/m3"),
    ("pms5003.pm250", "pm25", "ug/m3"),
    ("pms5003.pm100", "pm10", "ug/m3"),
]
def fixed_deque(maxlen):
    return lambda: deque(maxlen=maxlen)
values = defaultdict(fixed_deque(WIDTH))

# The main loop
try:
    while True:

        try:
            proximity = ltr559.get_proximity()
            lux = ltr559.get_lux()
        except:
            proximity = None
            lux = None
        try:
            pressure = bme280.get_pressure()
            humidity = bme280.get_humidity()

            cpu_temp = get_cpu_temperature()
            # Smooth out with some averaging to decrease jitter
            cpu_temps = cpu_temps[1:] + [cpu_temp]
            avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
            raw_temp = bme280.get_temperature()
            corrected_temp = raw_temp - ((avg_cpu_temp - raw_temp) / factor)
        except:
            pressure = None
            humidity = None
            cpu_temp = None
            avg_cpu_temp = None
            raw_temp = None
            corrected_temp = None
        
        try:
            gas_data = gas.read_all()
            oxidising = gas_data.oxidising / 1000
            reducing = gas_data.reducing / 1000
            nh3 = gas_data.nh3 / 1000
        except:
            oxidising = None
            reducing = None
            nh3 = None

        try:
            data = pms5003.read()
            pm1 = data.pm_ug_per_m3(1.0)
            pm25 = data.pm_ug_per_m3(2.5)
            pm10 = data.pm_ug_per_m3(10)
        except:
            pm1 = None
            pm25 = None
            pm10 = None

        readings = {
                "ltr559.proximity" : proximity,
                "ltr559.lux" : lux,
                "pi.cpu.avg" : avg_cpu_temp,
                "pi.cpu.raw" : cpu_temp,
                "bme280.temp.raw" : raw_temp,
                "bme280.temp.corrected" : corrected_temp,
                "bme280.pressure" : pressure,
                "bme280.humidity" : humidity,
                "mics6814.oxidising" : oxidising,
                "mics6814.reducing" : reducing,
                "mics6814.nh3" : nh3,
                "pms5003.pm010" : pm1,
                "pms5003.pm025" : pm25,
                "pms5003.pm100" : pm10,
                }
       
        print(readings)

        for k, v in readings.items():
            values[k].append(v)
            if v is not None:
                influx_json_prototyp[0]["fields"][k] = float(v)
            else:
                if k in influx_json_prototyp[0]["fields"]:
                    influx_json_prototyp[0]["fields"].pop(k)
        if len(values["bme280.temp.raw"]) >= 2:
            try:
                influx.write_points(influx_json_prototyp)
            except:
                print("Error writing to influx")

        # If the proximity crosses the threshold, toggle the mode
        if proximity > 1500 and time.time() - last_page > delay:
            mode += 1
            mode %= len(modes)
            last_page = time.time()

        #turn off backlight at night
        now = time.localtime()
        if now.tm_hour > 21 or now.tm_hour < 8:
            st7735.set_backlight(GPIO.LOW)
        else:
            st7735.set_backlight(GPIO.HIGH)

        display_text(mode)
        time.sleep(15)


# Exit cleanly
except KeyboardInterrupt:
    sys.exit(0)
