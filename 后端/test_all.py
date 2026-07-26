"""Phase 1-10 完整回归测试"""
import json
import os
import sys
import urllib.request
import urllib.error
import asyncio
from urllib.parse import quote

BASE = "http://127.0.0.1:8082"
results = {"pass": 0, "fail": 0}


def test(phase, name, path, method="GET", data=None, expected=200, timeout=15):
    url = BASE + path
    body = json.dumps(data, ensure_ascii=False).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.load(resp)
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            result = json.loads(e.read().decode())
        except Exception:
            result = str(e)
    except Exception as e:
        code = 0
        result = str(e)

    ok = code == expected
    results["pass" if ok else "fail"] += 1
    icon = "OK" if ok else "FAIL"
    label = f"[{icon}] [{phase}] {name:32s} -> {code}"
    print(label)
    if not ok:
        print(f"     expected={expected} got={code} resp={str(result)[:120]}")
    return result


def main():
    print("=" * 60)
    print("  CSGOEmpire Full Regression Test - Phase 1~10")
    print("=" * 60)

    # ---- Phase 1-2: Foundation ----
    print("\n--- Phase 1-2: Foundation + Account + Strategy ---")
    test("P1", "health", "/api/health")
    test("P1", "health/", "/api/health/")
    test("P2", "init", "/api/init", "POST", {"master_password": "mp"})
    test("P2", "init/", "/api/init/", "POST", {"master_password": "mp"})
    test("P2", "create ACC_A", "/api/accounts", "POST", {"name": "ACC_A", "api_key": "key_a", "empire_rate": 0.65})
    test("P2", "create ACC_B", "/api/accounts", "POST", {"name": "ACC_B", "api_key": "key_b", "empire_rate": 0.72})
    test("P2", "create ACC_C", "/api/accounts/", "POST", {"name": "ACC_C", "api_key": "key_c", "empire_rate": 0.70})

    r = test("P2", "list accounts", "/api/accounts")
    assert len(r.get("accounts", [])) == 3
    for a in r["accounts"]:
        assert "api_key" not in a, "LEAKED api_key!"

    test("P2", "switch ACC_A", f"/api/accounts/{quote('ACC_A')}/switch", "POST", {})
    r = test("P2", "current account", "/api/accounts/current")
    assert r.get("current_account") == "ACC_A"

    test("P2", "save strategy", "/api/strategy", "POST", {
        "account_name": None, "buff_rate": 0.138, "min_deal_pct": 15.0,
        "max_loss_pct": -5.0, "auto_bid": True, "auto_buy": False,
        "max_bid_usd": 500.0, "max_buy_usd": 500.0, "min_item_price": 5.0,
        "max_item_price": 2000.0, "whitelist": '["*AK-47*","*AWP*"]',
        "blacklist": '["*Safari*"]', "wear_filter": '["Factory New","Minimal Wear","Field-Tested"]',
        "bid_delay_ms": 500,
    })
    r = test("P2", "read strategy", "/api/strategy")
    assert r.get("buff_rate") == 0.138

    test("P2", "delete ACC_C", f"/api/accounts/{quote('ACC_C')}", "DELETE")
    r = test("P2", "verify delete", "/api/accounts")
    assert len(r.get("accounts", [])) == 2

    # ---- Phase 3: Prices (with fake API key, expect 401 from cs2.sh) ----
    print("\n--- Phase 3: Price Service (fake key -> 401 expected) ---")
    test("P3", "single price", "/api/prices?items=AK-47|Redline(FT)&source=buff", expected=401)
    test("P3", "batch price", "/api/prices/batch", "POST",
         {"items": ["AK-47 | Redline (FT)", "AWP | Asiimov (FN)"], "source": "buff"}, expected=401)
    test("P3", "refresh price", "/api/prices/refresh", "POST",
         {"items": ["AK-47 | Redline (FT)"], "source": "buff"}, expected=401)

    # ---- Phase 4: Empire Connection ----
    print("\n--- Phase 4: Empire Connection ---")
    test("P4", "empire status", "/api/empire/status")
    test("P4", "empire connect", "/api/empire/connect", "POST", {}, expected=200)

    # ---- Phase 5: Market Engine ----
    print("\n--- Phase 5: Market Engine ---")
    test("P5", "market status", "/api/market/status")
    test("P5", "market deals", "/api/market/deals?limit=10")
    test("P5", "start engine", "/api/market/start", "POST", {})
    test("P5", "engine running", "/api/market/status")
    test("P5", "stop engine", "/api/market/stop", "POST", {})

    # ---- Phase 6: Auction Engine ----
    print("\n--- Phase 6: Auction Engine ---")
    test("P6", "auction status", "/api/auction/status")
    test("P6", "active auctions", "/api/auction/active")
    test("P6", "history", "/api/auction/history?limit=10")
    test("P6", "start engine", "/api/auction/start", "POST", {})
    test("P6", "running status", "/api/auction/status")
    test("P6", "manual bid", "/api/auction/test_id/bid", "POST", {"amount": 1250.0}, expected=404)
    test("P6", "manual abort", "/api/auction/test_id/abort", "POST", {}, expected=404)
    test("P6", "stop engine", "/api/auction/stop", "POST", {})

    # ---- Phase 7: Trade Executor + Balance ----
    print("\n--- Phase 7: Trade Executor + Balance ---")
    test("P7", "balance", "/api/balance")

    # ---- Phase 8: Chrome Extension Support ----
    print("\n--- Phase 8: Chrome Extension Support ---")
    async def check_ws():
        import websockets
        async with websockets.connect("ws://127.0.0.1:8081"):
            pass
    try:
        asyncio.run(check_ws())
        print("[OK] [P8] WS connect ws://127.0.0.1:8081")
        results["pass"] += 1
    except Exception as e:
        print(f"[FAIL] [P8] WS connect -> {e}")
        results["fail"] += 1

    # ---- Phase 9: Stats + Export/Import + Multi-Account + Relist ----
    print("\n--- Phase 9: Stats + Export/Import + Multi-Account + Relist ---")
    test("P9", "stats today", "/api/stats?period=today")
    test("P9", "stats week", "/api/stats?period=week")
    test("P9", "stats month", "/api/stats?period=month&account_name=ACC_A")

    r = test("P9", "encrypted export", "/api/config/export", "POST", {"master_password": "mp"})
    enc = r.get("encrypted_data", "")
    assert enc and r.get("account_count") == 2

    r = test("P9", "correct import", "/api/config/import", "POST",
             {"encrypted_data": enc, "master_password": "mp"})
    assert r.get("imported_accounts") == 2

    test("P9", "wrong pw import", "/api/config/import", "POST",
         {"encrypted_data": enc, "master_password": "WRONG"}, expected=400)

    # Multi-Account Manager
    from services.multi_account import MultiAccountManager
    assert hasattr(MultiAccountManager, "start_all")
    assert hasattr(MultiAccountManager, "stop_all")
    print("[OK] [P9] MultiAccountManager API ok")
    results["pass"] += 1

    # Relist monitoring (instance attribute, check via init)
    from services.auction_engine import AuctionSnipeEngine
    from unittest.mock import MagicMock
    e = AuctionSnipeEngine(MagicMock(), MagicMock(), MagicMock(), "test", 0.65)
    assert hasattr(e, "_relist_watch"), "missing _relist_watch on instance"
    print("[OK] [P9] Relist monitoring ok")
    results["pass"] += 1

    # ---- Phase 10: Logging + Error Handling ----
    print("\n--- Phase 10: Logging + Error Handling ---")
    log_file = os.path.expanduser("~/.csgoempire-bot/app.log")
    if os.path.exists(log_file):
        size = os.path.getsize(log_file)
        print(f"[OK] [P10] Log file: {log_file} ({size} bytes)")
        results["pass"] += 1
    else:
        print(f"[FAIL] [P10] Log file missing: {log_file}")
        results["fail"] += 1

    test("P10", "404 handling", "/api/nonexistent", expected=404)

    # ---- Summary ----
    print()
    print("=" * 60)
    total = results["pass"] + results["fail"]
    print(f"  Results: {results['pass']}/{total} passed")
    if results["fail"] == 0:
        print("  ALL TESTS PASSED!")
    else:
        print(f"  {results['fail']} test(s) FAILED")
    print("=" * 60)
    return results["fail"] == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
