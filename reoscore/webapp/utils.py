def parse_float_locale(value, default=None):
    if value is None:
        return default

    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return default

    raw = str(value).strip()
    if not raw:
        return default

    raw = raw.replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")

    try:
        return float(raw)
    except Exception:
        return default


def parse_int_locale(value, default=None, min_value=None):
    parsed = parse_float_locale(value, default=None)
    if parsed is None:
        return default

    try:
        parsed_int = int(parsed)
    except Exception:
        return default

    if min_value is not None and parsed_int < min_value:
        return min_value
    return parsed_int
