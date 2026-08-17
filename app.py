import os
from datetime import datetime, timezone
from threading import Lock
from flask import Flask, jsonify, request

app = Flask(__name__)

MODEL = "BW-AI-001"
ALLOWED_SYMBOLS = {"EURUSD", "XAUUSD"}

MODE = os.getenv("BWTRADERS_MODE", "DEMO").upper()
if MODE not in {"DEMO", "LIVE"}:
    MODE = "DEMO"

LIVE_ENABLED = os.getenv("BWTRADERS_LIVE_ENABLED", "false").lower() == "true"
API_KEY = os.getenv("BWTRADERS_API_KEY", "")

MIN_CONFIDENCE = float(os.getenv("BWTRADERS_MIN_CONFIDENCE", "98"))
MIN_SCORE = float(os.getenv("BWTRADERS_MIN_SCORE", "90"))

RISK_PER_TRADE = float(os.getenv("BWTRADERS_RISK_PER_TRADE", "0.5"))
DAILY_LOSS_LIMIT = float(os.getenv("BWTRADERS_DAILY_LOSS_LIMIT", "2"))
MAX_DRAWDOWN = float(os.getenv("BWTRADERS_MAX_DRAWDOWN", "5"))
LOSS_STREAK_LIMIT = int(os.getenv("BWTRADERS_LOSS_STREAK_LIMIT", "3"))

DAILY_TARGET = float(os.getenv("BWTRADERS_DAILY_TARGET", "400"))
EXTENSION_ENABLED = (
    os.getenv("BWTRADERS_EXTENSION_ENABLED", "true").lower() == "true"
)

lock = Lock()

state = {
    "mode": MODE,
    "bot_state": "PAUSED",
    "auto_trading": False,
    "mt5_connected": False,
    "account": {},
    "daily_pnl": 0.0,
    "open_trades": 0,
    "loss_streak": 0,
    "drawdown": 0.0,
    "target_reached": False,
    "last_signal": None,
    "last_trade": None,
    "pending_command": None,
    "command_id": 0,
    "updated_at": None,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def authorized():
    if not API_KEY:
        return True

    supplied = request.headers.get("X-API-Key", "")

    if not supplied:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()

    return supplied == API_KEY


def auth_error():
    if not authorized():
        return jsonify({
            "success": False,
            "error": "Unauthorized"
        }), 401

    return None


def get_balance():
    try:
        return float(state["account"].get("balance") or 0)
    except (TypeError, ValueError):
        return 0.0


def risk_allows(confidence, score):
    if state["bot_state"] != "RUNNING":
        return False, "Bot is not running."

    if not state["auto_trading"]:
        return False, "Automatic execution is disabled."

    if state["loss_streak"] >= LOSS_STREAK_LIMIT:
        return False, "Loss-streak limit reached."

    if state["drawdown"] >= MAX_DRAWDOWN:
        return False, "Maximum drawdown reached."

    balance = get_balance()

    if balance > 0:
        daily_loss_pct = (-state["daily_pnl"] / balance) * 100

        if daily_loss_pct >= DAILY_LOSS_LIMIT:
            return False, "Daily loss limit reached."

    if confidence < MIN_CONFIDENCE:
        return False, "Minimum AI confidence not met."

    # The $400 target is a milestone, NOT a shutdown.
    if state["target_reached"]:

        if not EXTENSION_ENABLED:
            return False, "Target reached and extension is disabled."

        if score < MIN_SCORE:
            return False, "Post-target score requirement not met."

    return True, "Risk and signal requirements passed."


# ============================================================
# BASIC SERVICE
# ============================================================

@app.get("/")
def home():
    return jsonify({
        "service": "BWTraders AI backend",
        "model": MODEL,
        "status": "ONLINE",
        "mode": state["mode"],
        "live_enabled": LIVE_ENABLED
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": now(),
        "mode": state["mode"],
        "live_enabled": LIVE_ENABLED
    })


# ============================================================
# DASHBOARD STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    with lock:

        return jsonify({
            "bot": "BWTraders AI",
            "model": MODEL,
            "mode": state["mode"],
            "state": state["bot_state"],
            "auto_trading": state["auto_trading"],

            "mt5": (
                "CONNECTED"
                if state["mt5_connected"]
                else "WAITING"
            ),

            "account": state["account"],

            "daily_pnl": state["daily_pnl"],
            "daily_target": DAILY_TARGET,
            "target_reached": state["target_reached"],

            "extension_enabled": EXTENSION_ENABLED,
            "continue_after_target": True,

            "open_trades": state["open_trades"],
            "loss_streak": state["loss_streak"],
            "drawdown": state["drawdown"],

            "minimum_confidence": MIN_CONFIDENCE,
            "minimum_score": MIN_SCORE,

            "risk_per_trade": RISK_PER_TRADE,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "max_drawdown": MAX_DRAWDOWN,
            "loss_streak_limit": LOSS_STREAK_LIMIT,

            "live_enabled": LIVE_ENABLED,

            "last_signal": state["last_signal"],
            "last_trade": state["last_trade"],

            "updated_at": state["updated_at"]
        })


# ============================================================
# BOT CONTROLS
# ============================================================

@app.post("/api/bot/start")
def start_bot():

    error = auth_error()

    if error:
        return error

    with lock:

        if state["mode"] == "LIVE" and not LIVE_ENABLED:

            return jsonify({
                "success": False,
                "error": "LIVE trading is disabled on the server."
            }), 403

        state["bot_state"] = "RUNNING"
        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "state": "RUNNING",
        "mode": state["mode"]
    })


@app.post("/api/bot/pause")
def pause_bot():

    error = auth_error()

    if error:
        return error

    with lock:

        state["bot_state"] = "PAUSED"
        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "state": "PAUSED"
    })


@app.post("/api/bot/emergency")
def emergency():

    error = auth_error()

    if error:
        return error

    with lock:

        state["bot_state"] = "EMERGENCY"
        state["auto_trading"] = False

        state["command_id"] += 1

        state["pending_command"] = {
            "command": "CLOSE_ALL",
            "id": state["command_id"],
            "mode": state["mode"],
            "reason": "Emergency stop requested.",
            "created_at": now()
        }

        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "state": "EMERGENCY",
        "command": "CLOSE_ALL"
    })


@app.post("/api/auto-trading")
def set_auto_trading():

    error = auth_error()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    enabled = bool(data.get("enabled", False))

    with lock:

        if enabled and state["bot_state"] == "EMERGENCY":
            state["bot_state"] = "PAUSED"

        state["auto_trading"] = enabled
        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "auto_trading": state["auto_trading"],
        "state": state["bot_state"]
    })


# ============================================================
# DEMO / LIVE MODE
# ============================================================

@app.get("/api/mode")
def get_mode():

    return jsonify({
        "mode": state["mode"],
        "live_enabled": LIVE_ENABLED,
        "server_live_capable": LIVE_ENABLED
    })


@app.post("/api/mode")
def change_mode():

    error = auth_error()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    requested = str(
        data.get("mode", "")
    ).upper()

    if requested not in {"DEMO", "LIVE"}:

        return jsonify({
            "success": False,
            "error": "Mode must be DEMO or LIVE"
        }), 400

    with lock:

        if requested == "LIVE":

            if not LIVE_ENABLED:

                return jsonify({
                    "success": False,
                    "error": (
                        "LIVE is locked on the server. "
                        "Enable it only after DEMO testing."
                    )
                }), 403

            if not state["mt5_connected"]:

                return jsonify({
                    "success": False,
                    "error": (
                        "LIVE requires a connected MT5 bridge."
                    )
                }), 409

        state["mode"] = requested
        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "mode": state["mode"],
        "live_enabled": LIVE_ENABLED
    })


# ============================================================
# AI SIGNAL
# ============================================================

@app.get("/api/signal/<symbol>")
def get_signal(symbol):

    symbol = symbol.upper()

    if symbol not in ALLOWED_SYMBOLS:

        return jsonify({
            "error": "Unsupported symbol"
        }), 404

    with lock:

        signal = state["last_signal"]

        if not signal or signal.get("symbol") != symbol:

            return jsonify({
                "symbol": symbol,
                "trend": "WAITING",
                "signal": "WAIT",
                "confidence": 0,
                "score": 0
            })

        return jsonify(signal)


@app.post("/api/signal")
def receive_signal():

    error = auth_error()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    symbol = str(
        data.get("symbol", "")
    ).upper()

    signal = str(
        data.get("signal", "WAIT")
    ).upper()

    if symbol not in ALLOWED_SYMBOLS:

        return jsonify({
            "success": False,
            "error": "Unsupported symbol"
        }), 400

    if signal not in {"BUY", "SELL", "WAIT"}:

        return jsonify({
            "success": False,
            "error": "Signal must be BUY, SELL or WAIT"
        }), 400

    try:

        confidence = float(
            data.get("confidence", 0)
        )

        score = float(
            data.get("score", confidence)
        )

    except (TypeError, ValueError):

        return jsonify({
            "success": False,
            "error": "Invalid confidence or score"
        }), 400

    with lock:

        state["last_signal"] = {

            "symbol": symbol,

            "trend": str(
                data.get("trend", "WAITING")
            ).upper(),

            "signal": signal,

            "confidence": confidence,

            "score": score,

            "h1": data.get("h1"),

            "m15": data.get("m15"),

            "m5": data.get("m5"),

            "price": data.get("price"),

            "timestamp": now()
        }

        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "signal": state["last_signal"]
    })


# ============================================================
# AUTOMATIC TRADE REQUEST
# ============================================================

@app.post("/api/trade-request")
def trade_request():

    error = auth_error()

    if error:
        return error

    data = request.get_json(silent=True) or {}

    try:

        symbol = str(
            data["symbol"]
        ).upper()

        action = str(
            data["action"]
        ).upper()

        volume = float(
            data["volume"]
        )

        stop_loss = float(
            data["stop_loss"]
        )

        take_profit = float(
            data["take_profit"]
        )

        confidence = float(
            data.get("confidence", 0)
        )

        score = float(
            data.get("score", confidence)
        )

    except (KeyError, TypeError, ValueError):

        return jsonify({
            "success": False,
            "error": "Invalid trade request."
        }), 400

    if symbol not in ALLOWED_SYMBOLS:

        return jsonify({
            "success": False,
            "error": "Unsupported symbol"
        }), 400

    if action not in {"BUY", "SELL"}:

        return jsonify({
            "success": False,
            "error": "Action must be BUY or SELL"
        }), 400

    with lock:

        allowed, reason = risk_allows(
            confidence,
            score
        )

        if not allowed:

            return jsonify({
                "success": False,
                "error": reason
            }), 403

        state["command_id"] += 1

        command = {

            "command": "OPEN",

            "id": state["command_id"],

            "symbol": symbol,

            "action": action,

            "volume": volume,

            "stop_loss": stop_loss,

            "take_profit": take_profit,

            "confidence": confidence,

            "score": score,

            "reason": data.get(
                "reason",
                "BWTraders AI qualified signal"
            ),

            "mode": state["mode"],

            "created_at": now()
        }

        state["pending_command"] = command

        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "queued": True,
        "command": command
    })


# ============================================================
# MT5 COMMAND BRIDGE
# ============================================================

@app.get("/mt5/command")
def mt5_command():

    error = auth_error()

    if error:
        return error

    with lock:

        command = state["pending_command"]

        if command is None:

            return jsonify({
                "command": "WAIT",
                "mode": state["mode"],
                "timestamp": now()
            })

        state["pending_command"] = None

        state["updated_at"] = now()

        return jsonify(command)


@app.post("/mt5/status")
def receive_mt5_status():

    error = auth_error()

    if error:
        return error

    data = request.get_json(
        silent=True
    ) or {}

    with lock:

        state["mt5_connected"] = bool(
            data.get("connected", False)
        )

        if isinstance(
            data.get("account"),
            dict
        ):

            state["account"] = data["account"]

        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "connected": state["mt5_connected"],
        "mode": state["mode"]
    })


@app.post("/mt5/account")
def receive_mt5_account():

    error = auth_error()

    if error:
        return error

    data = request.get_json(
        silent=True
    ) or {}

    with lock:

        state["account"] = {

            "login": data.get("login"),

            "server": data.get("server"),

            "balance": data.get("balance"),

            "equity": data.get("equity"),

            "margin": data.get("margin"),

            "free_margin": data.get(
                "free_margin"
            ),

            "currency": data.get(
                "currency"
            )
        }

        state["mt5_connected"] = True

        state["updated_at"] = now()

    return jsonify({
        "success": True,
        "account": state["account"]
    })


@app.post("/mt5/trade-result")
def receive_trade_result():

    error = auth_error()

    if error:
        return error

    data = request.get_json(
        silent=True
    ) or {}

    try:

        pnl = float(
            data.get("pnl", 0) or 0
        )

    except (TypeError, ValueError):

        pnl = 0.0

    with lock:

        state["daily_pnl"] += pnl

        if pnl < 0:

            state["loss_streak"] += 1

        elif pnl > 0:

            state["loss_streak"] = 0

        balance = get_balance()

        try:

            equity = float(
                state["account"].get(
                    "equity"
                ) or balance
            )

        except (TypeError, ValueError):

            equity = balance

        if balance > 0:

            state["drawdown"] = max(
                0.0,
                (
                    (balance - equity)
                    / balance
                ) * 100.0
            )

        if state["daily_pnl"] >= DAILY_TARGET:

            state["target_reached"] = True

        state["last_trade"] = {

            "symbol": data.get(
                "symbol"
            ),

            "action": data.get(
                "action"
            ),

            "volume": data.get(
                "volume"
            ),

            "pnl": pnl,

            "ticket": data.get(
                "ticket"
            ),

            "status": data.get(
                "status"
            ),

            "mode": data.get(
                "mode"
            ),

            "timestamp": data.get(
                "timestamp"
            ) or now()
        }

        state["updated_at"] = now()

    return jsonify({

        "success": True,

        "daily_pnl": state[
            "daily_pnl"
        ],

        "target_reached": state[
            "target_reached"
        ],

        "continue_after_target": (
            EXTENSION_ENABLED
        )
    })


# ============================================================
# MARKET DATA
# ============================================================

@app.post("/mt5/market")
def receive_market_data():

    error = auth_error()

    if error:
        return error

    data = request.get_json(
        silent=True
    ) or {}

    symbol = str(
        data.get("symbol", "")
    ).upper()

    with lock:

        state["mt5_connected"] = True

        state["updated_at"] = now()

    return jsonify({

        "success": True,

        "received": True,

        "symbol": symbol,

        "timeframe": str(
            data.get(
                "timeframe",
                "M5"
            )
        ).upper(),

        "bid": data.get(
            "bid"
        ),

        "ask": data.get(
            "ask"
        ),

        "price": data.get(
            "price"
        ),

        "timestamp": data.get(
            "timestamp"
        )
    })


# ============================================================
# INFORMATION
# ============================================================

@app.get("/info")
def info():

    return jsonify({

        "project": "BWTraders",

        "model": MODEL,

        "mode": state["mode"],

        "live_enabled": LIVE_ENABLED,

        "markets": sorted(
            ALLOWED_SYMBOLS
        ),

        "workflow": {

            "trend": "H1",

            "setup": "M15",

            "entry": "M5"
        },

        "minimum_confidence":
            MIN_CONFIDENCE,

        "minimum_score":
            MIN_SCORE,

        "daily_target":
            DAILY_TARGET,

        "extension_enabled":
            EXTENSION_ENABLED,

        "continue_after_target":
            True,

        "risk": {

            "risk_per_trade":
                RISK_PER_TRADE,

            "daily_loss_limit":
                DAILY_LOSS_LIMIT,

            "max_drawdown":
                MAX_DRAWDOWN,

            "loss_streak_limit":
                LOSS_STREAK_LIMIT
        }
    })


if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
                )
