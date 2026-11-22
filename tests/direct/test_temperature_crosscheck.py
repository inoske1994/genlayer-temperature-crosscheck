import json

CONTRACT = "contracts/temperature_crosscheck.py"
STATION_PATTERN = r".*brightsky\.dev.*"
MODEL_PATTERN = r".*open-meteo\.com.*"

DAY = "2026-08-01"


def station_payload(day=DAY, hourly_max=28.7, hours=24, offset=0.0):
    rows = []
    for hour in range(hours):
        stamp = day + "T" + (("0" + str(hour))[-2:]) + ":00:00+00:00"
        value = round(hourly_max - offset, 1)
        rows.append({"timestamp": stamp, "temperature": value, "precipitation": 0.0})
    return {"weather": rows, "sources": [{"id": 1, "dwd_station_id": "10637"}]}


def model_payload(day=DAY, daily_max=25.2):
    return {"daily": {"time": [day], "temperature_2m_max": [daily_max]}}


def install(direct_vm, station=None, model=None, station_status=200, model_status=200):
    direct_vm.clear_mocks()
    direct_vm.mock_web(
        STATION_PATTERN,
        {"method": "GET", "status": station_status, "body": json.dumps(station or station_payload())},
    )
    direct_vm.mock_web(
        MODEL_PATTERN,
        {"method": "GET", "status": model_status, "body": json.dumps(model or model_payload())},
    )


def test_agreeing_sources(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=23.9), model_payload(daily_max=23.8))

    record_id = contract.record("50.11", "8.68", DAY, 15)
    assert record_id == "1"

    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["station_max_tenths"] == 239
    assert assessment["model_max_tenths"] == 238
    assert assessment["delta_tenths"] == 1
    assert assessment["verdict"] == "AGREE"
    assert assessment["station_hours"] == 24
    assert assessment["day_basis"] == "UTC calendar day"
    assert assessment["station_source"] == "brightsky:dwd"
    assert assessment["model_source"] == "open-meteo:archive:era5"


def test_diverging_sources(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=28.7), model_payload(daily_max=25.2))

    contract.record("50.11", "8.68", DAY, 15)
    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["delta_tenths"] == 35
    assert assessment["verdict"] == "DIVERGE"


def test_tolerance_boundary_is_inclusive(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=30.0), model_payload(daily_max=28.5))

    contract.record("50.11", "8.68", DAY, 15)
    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["delta_tenths"] == 15
    assert assessment["verdict"] == "AGREE"


def test_zero_tolerance_requires_exact_match(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=30.0), model_payload(daily_max=30.0))

    contract.record("50.11", "8.68", DAY, 0)
    assert json.loads(contract.get_record("1"))["assessment"]["verdict"] == "AGREE"


def test_negative_temperatures(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=-3.4), model_payload(daily_max=-2.9))

    contract.record("50.11", "8.68", DAY, 10)
    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["station_max_tenths"] == -34
    assert assessment["model_max_tenths"] == -29
    assert assessment["delta_tenths"] == 5
    assert assessment["verdict"] == "AGREE"


def test_negative_coordinates_are_canonicalised(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm)
    contract.record("-33.90", "-70.66", DAY, 15)
    assert contract.get_key(0) == "-33.90|-70.66|2026-08-01|15"
    assessment = json.loads(contract.get_record("1"))["assessment"]
    assert assessment["lat"] == "-33.90"
    assert assessment["lon"] == "-70.66"


def test_incomplete_station_day_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hours=10))
    with direct_vm.expect_revert("only 10 observations"):
        contract.record("50.11", "8.68", DAY, 15)
    assert contract.count() == 0


def test_station_without_that_day_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(day="2026-07-30"))
    with direct_vm.expect_revert("no observation for this day"):
        contract.record("50.11", "8.68", DAY, 15)
    assert contract.count() == 0


def test_model_answering_another_day_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, model=model_payload(day="2026-07-30"))
    with direct_vm.expect_revert("different day"):
        contract.record("50.11", "8.68", DAY, 15)
    assert contract.count() == 0


def test_station_host_error_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_status=503)
    with direct_vm.expect_revert("station host answered with status 503"):
        contract.record("50.11", "8.68", DAY, 15)
    assert contract.count() == 0


def test_model_host_error_reverts(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, model_status=502)
    with direct_vm.expect_revert("model host answered with status 502"):
        contract.record("50.11", "8.68", DAY, 15)
    assert contract.count() == 0


def test_input_validation(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm)

    with direct_vm.expect_revert("day must look like"):
        contract.record("50.11", "8.68", "2026-02-30", 15)
    with direct_vm.expect_revert("day must look like"):
        contract.record("50.11", "8.68", "01-08-2026", 15)
    with direct_vm.expect_revert("two decimals"):
        contract.record("50.1", "8.68", DAY, 15)
    with direct_vm.expect_revert("two decimals"):
        contract.record("50,11", "8.68", DAY, 15)
    with direct_vm.expect_revert("latitude"):
        contract.record("91.00", "8.68", DAY, 15)
    with direct_vm.expect_revert("longitude"):
        contract.record("50.11", "180.01", DAY, 15)
    with direct_vm.expect_revert("tolerance"):
        contract.record("50.11", "8.68", DAY, 201)


def test_same_inputs_are_rejected_and_other_tolerance_is_allowed(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=28.7), model_payload(daily_max=25.2))

    contract.record("50.11", "8.68", DAY, 15)
    with direct_vm.expect_revert("already recorded"):
        contract.record("50.11", "8.68", DAY, 15)

    contract.record("50.11", "8.68", DAY, 40)
    first = json.loads(contract.get_record("1"))["assessment"]
    second = json.loads(contract.get_record("2"))["assessment"]
    assert first["delta_tenths"] == second["delta_tenths"] == 35
    assert first["verdict"] == "DIVERGE"
    assert second["verdict"] == "AGREE"
    assert contract.count() == 2


def test_index_and_count(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm)
    contract.record("50.11", "8.68", DAY, 15)
    contract.record("52.52", "13.40", DAY, 15)

    assert contract.count() == 2
    assert contract.get_key(0) == "50.11|8.68|2026-08-01|15"
    assert contract.get_key(1) == "52.52|13.40|2026-08-01|15"
    assert contract.get_key(9) == ""
    assert contract.get_record("9") == "{}"
    assert contract.get_record("x") == "{}"


def test_validator_accepts_a_genuine_leader(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=28.7), model_payload(daily_max=25.2))
    contract.record("50.11", "8.68", DAY, 15)
    assert direct_vm.run_validator() is True


def test_validator_rejects_a_tampered_station_value(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=28.7), model_payload(daily_max=25.2))
    contract.record("50.11", "8.68", DAY, 15)
    genuine = json.loads(contract.get_record("1"))["assessment"]

    forged = dict(genuine)
    forged["station_max_tenths"] = 252
    forged["delta_tenths"] = 0
    forged["verdict"] = "AGREE"
    assert direct_vm.run_validator(leader_result=json.dumps(forged, sort_keys=True)) is False


def test_validator_rejects_a_flipped_verdict(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=28.7), model_payload(daily_max=25.2))
    contract.record("50.11", "8.68", DAY, 15)
    genuine = json.loads(contract.get_record("1"))["assessment"]

    forged = dict(genuine)
    forged["verdict"] = "AGREE"
    assert direct_vm.run_validator(leader_result=json.dumps(forged, sort_keys=True)) is False


def test_validator_rejects_a_different_source(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm, station_payload(hourly_max=28.7), model_payload(daily_max=25.2))
    contract.record("50.11", "8.68", DAY, 15)
    genuine = json.loads(contract.get_record("1"))["assessment"]

    forged = dict(genuine)
    forged["station_source"] = "another-station-network"
    assert direct_vm.run_validator(leader_result=json.dumps(forged, sort_keys=True)) is False


def test_validator_rejects_leader_error(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT)
    install(direct_vm)
    contract.record("50.11", "8.68", DAY, 15)
    assert direct_vm.run_validator(leader_error=RuntimeError("host unavailable")) is False
