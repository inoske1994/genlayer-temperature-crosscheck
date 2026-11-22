# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""TemperatureCrossCheck: does a weather station agree with a weather model?

The contract reads the daily maximum temperature for one coordinate and one UTC
day from two independent publishers, a DWD station series behind Bright Sky and
the ERA5 archive behind Open-Meteo, and stores both numbers with their delta.

A caller supplies a tolerance in tenths of a degree. Code derives the verdict
from the two recorded integers, so the record shows not only who said what but
also how far the two sources were apart. That distance is the basis risk a
weather linked agreement takes on when it settles on model output instead of a
station.

No language model is involved. Validators re-run the whole computation from
their own fetches and must reproduce every field exactly.
"""

from genlayer import *
import json

STATION_URL = "https://api.brightsky.dev/weather?lat={lat}&lon={lon}&date={day}"
MODEL_URL = (
    "https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
    "&start_date={day}&end_date={day}&daily=temperature_2m_max&timezone=UTC"
)
STATION_SOURCE = "brightsky:dwd"
MODEL_SOURCE = "open-meteo:archive:era5"
DAY_UTC = "UTC calendar day"
MIN_STATION_HOURS = 20
MAX_TOLERANCE_TENTHS = 200
FIRST_DAY_YEAR = 2000


def _is_day(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    if not (value[0:4] + value[5:7] + value[8:10]).isdigit():
        return False
    year = int(value[0:4])
    month = int(value[5:7])
    day = int(value[8:10])
    if year < FIRST_DAY_YEAR or month < 1 or month > 12 or day < 1:
        return False
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[1] = 29
    return day <= days_in_month[month - 1]


def _coord_hundredths(value: str) -> int:
    text = value.strip()
    sign = 1
    if text.startswith("-"):
        sign = -1
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 2:
        raise gl.vm.UserError("coordinates need two decimals, for example 50.11")
    whole = parts[0]
    fraction = parts[1]
    if len(whole) < 1 or len(whole) > 3 or not whole.isdigit():
        raise gl.vm.UserError("coordinates need two decimals, for example 50.11")
    if len(fraction) != 2 or not fraction.isdigit():
        raise gl.vm.UserError("coordinates need two decimals, for example 50.11")
    return sign * (int(whole) * 100 + int(fraction))


def _coord_text(hundredths: int) -> str:
    sign = ""
    if hundredths < 0:
        sign = "-"
    value = hundredths
    if value < 0:
        value = -value
    fraction = value % 100
    padding = "0" + str(fraction)
    return sign + str(value // 100) + "." + padding[-2:]


def _tenths(value) -> int:
    """Turn a temperature such as 23.9 into the integer 239."""
    text = "%.1f" % value
    whole = text[0:-2]
    fraction = text[-1:]
    if whole == "-0":
        return -int(fraction)
    return int(whole + fraction)


def _station_max(payload: dict, day: str) -> list:
    rows = payload.get("weather")
    if not isinstance(rows, list):
        raise gl.vm.UserError("station answer has no weather list")
    highest = None
    observed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = row.get("timestamp")
        if not isinstance(stamp, str) or stamp[0:10] != day:
            continue
        value = row.get("temperature")
        if value is None or not isinstance(value, (int, float)):
            continue
        observed = observed + 1
        if highest is None or value > highest:
            highest = value
    if highest is None:
        raise gl.vm.UserError("station has no observation for this day")
    return [highest, observed]


def _model_max(payload: dict, day: str) -> int:
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise gl.vm.UserError("model answer has no daily block")
    times = daily.get("time")
    values = daily.get("temperature_2m_max")
    if not isinstance(times, list) or not isinstance(values, list):
        raise gl.vm.UserError("model answer has no daily maximum")
    if len(times) == 0 or len(values) == 0:
        raise gl.vm.UserError("model answer is empty")
    if times[0] != day:
        raise gl.vm.UserError("model answered for a different day")
    value = values[0]
    if value is None or not isinstance(value, (int, float)):
        raise gl.vm.UserError("model has no value for this day")
    return value


class TemperatureCrossCheck(gl.Contract):
    next_id: u256
    records: TreeMap[str, str]
    order: DynArray[str]

    def __init__(self):
        self.next_id = u256(1)

    @gl.public.write
    def record(self, lat: str, lon: str, day: str, tolerance_tenths: u256) -> str:
        if not _is_day(day):
            raise gl.vm.UserError("day must look like 2026-08-01")
        lat_hundredths = _coord_hundredths(lat)
        lon_hundredths = _coord_hundredths(lon)
        if lat_hundredths < -9000 or lat_hundredths > 9000:
            raise gl.vm.UserError("latitude is outside -90 to 90")
        if lon_hundredths < -18000 or lon_hundredths > 18000:
            raise gl.vm.UserError("longitude is outside -180 to 180")
        tolerance = int(tolerance_tenths)
        if tolerance < 0 or tolerance > MAX_TOLERANCE_TENTHS:
            raise gl.vm.UserError("tolerance must be between 0 and 200 tenths of a degree")

        lat_text = _coord_text(lat_hundredths)
        lon_text = _coord_text(lon_hundredths)
        record_key = lat_text + "|" + lon_text + "|" + day + "|" + str(tolerance)
        if self.records.get(record_key, "") != "":
            raise gl.vm.UserError("this coordinate, day and tolerance are already recorded")

        station_url = STATION_URL.replace("{lat}", lat_text).replace("{lon}", lon_text).replace(
            "{day}", day
        )
        model_url = MODEL_URL.replace("{lat}", lat_text).replace("{lon}", lon_text).replace(
            "{day}", day
        )

        def leader_fn() -> str:
            station_response = gl.nondet.web.get(station_url)
            if station_response.status != 200:
                raise gl.vm.UserError(
                    "station host answered with status " + str(station_response.status)
                )
            try:
                station_payload = json.loads(station_response.body.decode("utf-8", "replace"))
            except Exception:
                raise gl.vm.UserError("station answer is not valid JSON")
            if not isinstance(station_payload, dict):
                raise gl.vm.UserError("station answer is not a JSON object")

            model_response = gl.nondet.web.get(model_url)
            if model_response.status != 200:
                raise gl.vm.UserError(
                    "model host answered with status " + str(model_response.status)
                )
            try:
                model_payload = json.loads(model_response.body.decode("utf-8", "replace"))
            except Exception:
                raise gl.vm.UserError("model answer is not valid JSON")
            if not isinstance(model_payload, dict):
                raise gl.vm.UserError("model answer is not a JSON object")

            station = _station_max(station_payload, day)
            station_hours = station[1]
            if station_hours < MIN_STATION_HOURS:
                raise gl.vm.UserError(
                    "station has only " + str(station_hours) + " observations for this day"
                )

            station_max = _tenths(station[0])
            model_max = _tenths(_model_max(model_payload, day))
            delta = model_max - station_max
            if delta < 0:
                delta = -delta
            if delta > tolerance:
                verdict = "DIVERGE"
            else:
                verdict = "AGREE"

            return json.dumps(
                {
                    "lat": lat_text,
                    "lon": lon_text,
                    "day": day,
                    "day_basis": DAY_UTC,
                    "station_max_tenths": station_max,
                    "station_hours": station_hours,
                    "model_max_tenths": model_max,
                    "delta_tenths": delta,
                    "tolerance_tenths": tolerance,
                    "verdict": verdict,
                    "station_source": STATION_SOURCE,
                    "model_source": MODEL_SOURCE,
                },
                sort_keys=True,
            )

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            try:
                proposed = json.loads(leaders_res.calldata)
                checked = json.loads(leader_fn())
            except Exception:
                return False
            if not isinstance(proposed, dict):
                return False
            return proposed == checked

        result = json.loads(gl.vm.run_nondet(leader_fn, validator_fn))

        record_id = str(int(self.next_id))
        record = {
            "id": record_id,
            "record_key": record_key,
            "assessment": result,
        }
        self.records[record_key] = json.dumps(record, sort_keys=True)
        self.order.append(record_key)
        self.next_id = u256(int(self.next_id) + 1)
        return record_id

    @gl.public.view
    def get_record(self, record_id: str) -> str:
        index = record_id.strip()
        if not index.isdigit():
            return "{}"
        position = int(index) - 1
        if position < 0 or position >= len(self.order):
            return "{}"
        return self.records.get(self.order[position], "{}")

    @gl.public.view
    def count(self) -> u32:
        return len(self.order)

    @gl.public.view
    def get_key(self, position: int) -> str:
        if position < 0 or position >= len(self.order):
            return ""
        return self.order[position]
