from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import asyncio
import os

from database import SessionLocal, init_db, ScalpJournal
from engines import fetch_live_tick_data, scalping_engine

app = FastAPI(title="Gold Scalping System", version="1.0")

@app.on_event("startup")
def startup_event():
    init_db()
    asyncio.create_task(scalper_background_loop())

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def scalper_background_loop():
    """Runs continuously in the background to harvest high-volume trades"""
    while True:
        try:
            db = SessionLocal()
            df = fetch_live_tick_data("XAU/USD")
            signal = scalping_engine(df)
            
            if signal:
                trade_entry = ScalpJournal(
                    action=signal["action"],
                    entry_price=signal["entry"],
                    stop_loss=signal["stop_loss"],
                    take_profit=signal["take_profit"],
                    lot_size=signal["lot_size"],
                    reason=signal["reason"],
                    status="ACTIVE"
                )
                db.add(trade_entry)
                db.commit()
                print(f"[SCALPER EXECUTED]: {signal['action']} at {signal['entry']}")

            db.close()
        except Exception as e:
            print(f"Scalper loop error: {e}")
        
        await asyncio.sleep(10)

@app.get("/", response_class=HTMLResponse)
def read_dashboard(db: Session = Depends(get_db)):
    trades = db.query(ScalpJournal).order_by(ScalpJournal.id.desc()).limit(50).all()
    
    rows = ""
    for t in trades:
        color = "#22c55e" if t.action == "BUY" else "#ef4444"
        rows += f"""
        <tr style="border-bottom: 1px solid #334155;">
            <td style="padding: 12px; color: #cbd5e1;">{t.timestamp}</td>
            <td style="padding: 12px; color: {color}; font-weight: bold;">{t.action}</td>
            <td style="padding: 12px; color: #f8fafc;">${t.entry_price}</td>
            <td style="padding: 12px; color: #f8fafc;">${t.stop_loss}</td>
            <td style="padding: 12px; color: #f8fafc;">${t.take_profit}</td>
            <td style="padding: 12px; color: #f8fafc;">{t.lot_size}</td>
            <td style="padding: 12px; color: #94a3b8;">{t.reason}</td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gold Scalping Dashboard</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background-color: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 30px; margin: 0; }}
            h1 {{ color: #38bdf8; margin-bottom: 5px; }}
            p {{ color: #94a3b8; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 25px; background: #1e293b; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }}
            th {{ background: #334155; padding: 14px; text-align: left; color: #f8fafc; font-size: 14px; }}
        </style>
    </head>
    <body>
        <h1>⚡ Gold High-Frequency Scalping Terminal</h1>
        <p>Status: <strong>Running & Scanning 1-Minute Micro-Movements</strong></p>
        <table>
            <tr>
                <th>Time (UAE)</th>
                <th>Action</th>
                <th>Entry</th>
                <th>Stop Loss (Wide)</th>
                <th>Take Profit</th>
                <th>Lot Size</th>
                <th>Algorithmic Reason</th>
            </tr>
            {rows}
        </table>
    </body>
    </html>
    """
