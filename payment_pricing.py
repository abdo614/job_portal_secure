"""
إدارة أسعار الخدمات بالـ USD وتحويلها إلى عملة المستخدم.
الأسعار محفوظة في التخزين المشفر ويمكن تعديلها من لوحة الإدارة.
"""
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_PRICING = {
    "job_posting_usd": 5.00,
    "application_usd": 5.00,
    "contact_unlock_usd": 5.00,
}

# معدلات تجريبية قابلة للتعديل من لوحة الإدارة.
# الرقم يعني: 1 USD = كم وحدة من العملة المحلية.
DEFAULT_RATES = {
    "SAR": 3.75, "AED": 3.6725, "KWD": 0.306, "QAR": 3.64,
    "OMR": 0.3845, "BHD": 0.376, "EGP": 49.0, "JOD": 0.709,
    "LBP": 89500.0, "MAD": 9.2, "DZD": 130.0, "TND": 2.9,
    "LYD": 6.4, "SDG": 600.0, "IQD": 1310.0, "YER": 535.0,
    "SYP": 1000000.0, "USD": 1.0, "TRY": 41.0,
}

COUNTRY_CURRENCIES = {
    "السعودية":"SAR", "مصر":"EGP", "الإمارات":"AED", "الكويت":"KWD",
    "قطر":"QAR", "عُمان":"OMR", "البحرين":"BHD", "الأردن":"JOD",
    "لبنان":"LBP", "المغرب":"MAD", "الجزائر":"DZD", "تونس":"TND",
    "ليبيا":"LYD", "السودان":"SDG", "العراق":"IQD", "اليمن":"YER",
    "سوريا":"SYP", "فلسطين":"USD", "تركيا":"TRY", "USA":"USD",
    "الولايات المتحدة":"USD",
}

CURRENCY_NAMES = {
    "USD":"دولار أمريكي", "SAR":"ريال سعودي", "AED":"درهم إماراتي",
    "KWD":"دينار كويتي", "QAR":"ريال قطري", "OMR":"ريال عماني",
    "BHD":"دينار بحريني", "EGP":"جنيه مصري", "JOD":"دينار أردني",
    "LBP":"ليرة لبنانية", "MAD":"درهم مغربي", "DZD":"دينار جزائري",
    "TND":"دينار تونسي", "LYD":"دينار ليبي", "SDG":"جنيه سوداني",
    "IQD":"دينار عراقي", "YER":"ريال يمني", "SYP":"ليرة سورية", "TRY":"ليرة تركية",
}

CURRENCY_SYMBOLS = {
    "USD":"$", "SAR":"ر.س", "AED":"د.إ", "KWD":"د.ك", "QAR":"ر.ق",
    "OMR":"ر.ع", "BHD":"د.ب", "EGP":"ج.م", "JOD":"د.أ", "LBP":"ل.ل",
    "MAD":"د.م", "DZD":"دج", "TND":"د.ت", "LYD":"د.ل", "SDG":"ج.س",
    "IQD":"ع.ع", "YER":"ر.ي", "SYP":"ل.س", "TRY":"₺",
}

ZERO_DECIMAL_CURRENCIES = {"SYP", "LBP", "IQD", "DZD", "YER", "SDG"}

LIVE_RATES_URL = "https://cdn.moneyconvert.net/api/latest.json"
LIVE_RATES_TTL_SECONDS = 300


def _fetch_live_rates():
    """Fetch latest USD-based rates from MoneyConvert (updated about every 5 minutes)."""
    import json as _json
    import time as _time
    from urllib.request import Request, urlopen
    req = Request(LIVE_RATES_URL, headers={"User-Agent": "ArabJobs/1.0"})
    with urlopen(req, timeout=8) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    rates = data.get("rates") or {}
    return {str(k).upper(): float(v) for k, v in rates.items() if float(v) > 0}, int(_time.time())


def refresh_live_rates(storage, force=False):
    """Refresh stored rates automatically; never fails the app if provider is unavailable."""
    try:
        raw = storage.encryption.decrypt_file("payment_pricing") or {}
    except Exception:
        raw = {}
    updated = int(raw.get("rates_updated_at") or 0)
    import time as _time
    if not force and (_time.time() - updated) < LIVE_RATES_TTL_SECONDS:
        return dict(raw.get("rates") or DEFAULT_RATES), updated, False
    try:
        live, fetched_at = _fetch_live_rates()
        merged = dict(DEFAULT_RATES)
        merged.update(raw.get("rates") or {})
        # Only use live values for currencies supported by our pricing UI.
        for code in DEFAULT_RATES:
            if code in live:
                merged[code] = live[code]
        storage.encryption.encrypt_file("payment_pricing", {
            "pricing": dict(DEFAULT_PRICING, **(raw.get("pricing") or {})),
            "rates": merged,
            "rates_updated_at": fetched_at,
            "rates_source": "MoneyConvert.net"
        })
        return merged, fetched_at, True
    except Exception:
        return dict(raw.get("rates") or DEFAULT_RATES), updated, False


def _load(storage):
    try:
        data = storage.encryption.decrypt_file("payment_pricing") or {}
    except Exception:
        data = {}
    pricing = dict(DEFAULT_PRICING)
    pricing.update(data.get("pricing") or {})
    rates = dict(DEFAULT_RATES)
    rates.update(data.get("rates") or {})
    return pricing, rates

def load_settings(storage):
    pricing, rates = _load(storage)
    rates, updated_at, _ = refresh_live_rates(storage)
    return {"pricing": pricing, "rates": rates, "rates_updated_at": updated_at, "rates_source": "MoneyConvert.net"}

def save_settings(storage, pricing=None, rates=None):
    old_pricing, old_rates = _load(storage)
    if pricing is not None:
        old_pricing.update({k: float(v) for k, v in pricing.items() if k in DEFAULT_PRICING})
    if rates is not None:
        for k, v in rates.items():
            if k in DEFAULT_RATES and float(v) > 0:
                old_rates[k] = float(v)
    ok = storage.encryption.encrypt_file("payment_pricing", {
        "pricing": old_pricing, "rates": old_rates,
        "rates_updated_at": int(__import__("time").time()),
        "rates_source": "MoneyConvert.net"
    })
    return ok, {"pricing": old_pricing, "rates": old_rates}

def user_currency(user):
    return COUNTRY_CURRENCIES.get(str((user or {}).get("country", "")).strip(), "USD")

def format_local(amount, currency):
    currency = str(currency).upper()
    decimals = 0 if currency in ZERO_DECIMAL_CURRENCIES else 2
    if decimals == 0:
        value = int(Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return f"{value:,} {CURRENCY_SYMBOLS.get(currency, currency)}"
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{value:,.2f} {CURRENCY_SYMBOLS.get(currency, currency)}"

def usd_to_local(usd, currency, rates):
    return float(Decimal(str(usd)) * Decimal(str(rates.get(currency, 1.0))))

def service_prices(storage, user=None):
    pricing, rates = _load(storage)
    rates, _, _ = refresh_live_rates(storage)
    currency = user_currency(user)
    out = {}
    for key, usd in pricing.items():
        local = usd_to_local(usd, currency, rates)
        out[key] = {
            "usd": float(usd), "currency": currency, "localAmount": local,
            "formatted": format_local(local, currency),
            "usdFormatted": f"{float(usd):.2f} USD"
        }
    return {"currency": currency, "currencyName": CURRENCY_NAMES.get(currency, currency),
            "symbol": CURRENCY_SYMBOLS.get(currency, currency), "prices": out}

def usd_cents(storage, service_key):
    pricing, _ = _load(storage)
    return int(Decimal(str(pricing.get(service_key, 5.0))) * 100)
