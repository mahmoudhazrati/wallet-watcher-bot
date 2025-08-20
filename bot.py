import os, json, time, requests
from datetime import datetime, timezone

STATE_FILE = "state.json"

ETHERSCAN_KEY   = os.getenv("ETHERSCAN_KEY", "").strip()
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID", "").strip()
WALLETS_RAW     = os.getenv("WALLETS", "")
INIT_MODE       = os.getenv("INIT_MODE", "silent").lower()  # "silent" یا "notify"

def short(addr: str) -> str:
    if not addr:
        return "—"
    a = addr.lower()
    return f"{a[:6]}...{a[-4:]}"

def to_eth(wei: str) -> float:
    try:
        return int(wei) / 1e18
    except:
        return 0.0

def to_amount(value: str, decimals: str) -> float:
    try:
        d = int(decimals) if decimals else 0
        return int(value) / (10 ** d) if d >= 0 else 0.0
    except:
        return 0.0

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"wallets": {}, "updated_at": None}

def save_state(state):
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def etherscan_get(module, action, **params):
    url = "https://api.etherscan.io/api"
    payload = {"module": module, "action": action, "apikey": ETHERSCAN_KEY}
    payload.update(params)
    r = requests.get(url, params=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    # status "0" با پیام "No transactions found" خطا نیست
    if data.get("status") == "0" and data.get("message") != "No transactions found":
        raise RuntimeError(f"Etherscan error: {data}")
    return data.get("result", [])

def fetch_normal_txs(addr, limit=10):
    return etherscan_get("account", "txlist", address=addr, page=1, offset=limit, sort="desc")

def fetch_token_txs(addr, limit=10):
    return etherscan_get("account", "tokentx", address=addr, page=1, offset=limit, sort="desc")

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram creds missing")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        ok = r.json().get("ok", False)
        if not ok:
            print("Telegram error:", r.text)
        return ok
    except Exception as e:
        print("Telegram exception:", e)
        return False

def fmt_time(ts: str) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except:
        return "—"

def process_wallet(addr: str, state: dict):
    addr_l = addr.lower()
    sw = state["wallets"].setdefault(addr_l, {})

    # Normal transactions (ETH/contract calls)
    normals = fetch_normal_txs(addr_l)
    time.sleep(0.3)  # احترام به محدودیت نرخ
    normal_hashes = [t.get("hash") for t in normals if t.get("hash")]
    last_normal = sw.get("normal")

    new_normals = []
    if normal_hashes:
        if not last_normal:
            # اولین اجرا
            if INIT_MODE == "notify":
                new_normals = normals  # همه را خبر بده
        else:
            if last_normal in normal_hashes:
                idx = normal_hashes.index(last_normal)
                new_normals = normals[:idx]  # موارد جدیدتر از آخرین مشاهده
            else:
                new_normals = normals  # نتونستیم پیدا کنیم → همه را جدید فرض کن

        # ارسال به ترتیب قدیم→جدید
        for t in reversed(new_normals):
            frm = (t.get("from") or "").lower()
            to  = (t.get("to") or "").lower()
            val = to_eth(t.get("value", "0"))
            hsh = t.get("hash")
            text = (
                "🔔 <b>تراکنش جدید روی اتریوم</b>\n"
                f"👛 کیف‌پول: <code>{short(addr_l)}</code>\n"
                f"از: <code>{short(frm)}</code> → به: <code>{short(to)}</code>\n"
                f"مقدار: <b>{val:.6f} ETH</b>\n"
                f"⏱ {fmt_time(t.get('timeStamp','0'))}\n"
                f"🔗 https://etherscan.io/tx/{hsh}"
            )
            send_telegram(text)

        # به‌روزرسانی سرشاخه
        sw["normal"] = normal_hashes[0]

    # Token transfers (ERC-20)
    tokens = fetch_token_txs(addr_l)
    time.sleep(0.3)
    token_hashes = [t.get("hash") for t in tokens if t.get("hash")]
    last_token = sw.get("token")

    new_tokens = []
    if token_hashes:
        if not last_token:
            if INIT_MODE == "notify":
                new_tokens = tokens
        else:
            if last_token in token_hashes:
                idx = token_hashes.index(last_token)
                new_tokens = tokens[:idx]
            else:
                new_tokens = tokens

        for t in reversed(new_tokens):
            frm = (t.get("from") or "").lower()
            to  = (t.get("to") or "").lower()
            sym = (t.get("tokenSymbol") or "TOKEN").upper()
            amt = to_amount(t.get("value", "0"), t.get("tokenDecimal","0"))
            hsh = t.get("hash")
            text = (
                "🔔 <b>انتقال توکن (ERC-20)</b>\n"
                f"👛 کیف‌پول: <code>{short(addr_l)}</code>\n"
                f"توکن: <b>{sym}</b>\n"
                f"از: <code>{short(frm)}</code> → به: <code>{short(to)}</code>\n"
                f"مقدار: <b>{amt:g} {sym}</b>\n"
                f"⏱ {fmt_time(t.get('timeStamp','0'))}\n"
                f"🔗 https://etherscan.io/tx/{hsh}"
            )
            send_telegram(text)

        sw["token"] = token_hashes[0]

def main():
    if not ETHERSCAN_KEY:
        print("❌ ETHERSCAN_KEY تنظیم نشده")
        return
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_TOKEN/TELEGRAM_CHAT_ID تنظیم نشده")
        return

    wallets = [w.strip() for w in WALLETS_RAW.split(",") if w.strip()]
    if not wallets:
        print("❌ لیست آدرس‌ها خالیه. متغیر WALLETS را تنظیم کن.")
        return

    state = load_state()
    for w in wallets:
        try:
            process_wallet(w, state)
        except Exception as e:
            print(f"⚠️ خطا در پردازش {w}: {e}")
        time.sleep(0.2)

    save_state(state)
    print("✓ انجام شد.")

if __name__ == "__main__":
    main()
