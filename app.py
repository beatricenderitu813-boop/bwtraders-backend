from flask import Flask, jsonify
from datetime import datetime, timezone

app = Flask(__name__)

BOT_NAME = "BWTraders AI"
MODEL = "BW-AI-001"

@app.get("/")
def home():
    return jsonify({
        "name": BOT_NAME,
        "model": MODEL,
        "status": "ONLINE",
        "mode": "DEMO",
        "message": "BWTraders backend is running"
    })

@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

@app.get("/api/status")
def status():
    return jsonify({
        "bot": BOT_NAME,
        "model": MODEL,
        "mode": "DEMO",
        "mt5": "WAITING",
        "broker_server": "JustMarkets-Demo3",
        "symbols": ["EURUSD", "XAUUSD"],
        "minimum_confidence": 98,
        "risk_per_trade": 0.5,
        "daily_loss_limit": 2,
        "max_drawdown": 5,
        "loss_streak_limit": 3
    })

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
      )
