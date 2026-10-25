"""Shared defaults for the separate absent-GPS order destination."""

MISSING_GPS_DEFAULTS = {
    "enable_missing_gps_set_data_upload": True,
    "set_data_missing_gps_done_flag": "0",
    "set_data_missing_gps_id_grafik": "1004501000",
    "set_data_missing_gps_bukva_template": "HOF1-{stop_number}",
}


def missing_gps_setting(settings, name):
    if hasattr(settings, name):
        return getattr(settings, name)
    # Preserved old installations start with their own unserved destination,
    # but never inherit the final (usually 1973) flag.
    legacy = {
        "set_data_missing_gps_id_grafik": "set_data_unserved_id_grafik",
        "set_data_missing_gps_bukva_template": "set_data_unserved_bukva_template",
    }
    if name in legacy and hasattr(settings, legacy[name]):
        return getattr(settings, legacy[name])
    return MISSING_GPS_DEFAULTS[name]
