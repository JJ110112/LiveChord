"""
LiveChord 回應性測試 — 使用 Playwright 測試前端頁面載入與 API 回應
在 BTC 和弦偵測期間，所有頁面和 API 必須在合理時間內回應
"""

import time
import json
import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8800"
TIMEOUT_PAGE = 10000   # 頁面載入超時 (ms)
TIMEOUT_API = 5000     # API 回應超時 (ms)
ACCEPTABLE_MS = 3000   # 可接受的回應時間上限 (ms)

results = []

def record(name, elapsed_ms, status, detail=""):
    ok = "PASS" if elapsed_ms < ACCEPTABLE_MS and status in (200, "loaded") else "FAIL"
    results.append({"test": name, "ms": round(elapsed_ms), "status": status, "result": ok, "detail": detail})
    icon = "OK" if ok == "PASS" else "XX"
    print(f"  [{icon}] {name}: {elapsed_ms:.0f}ms (status={status}) {detail}")


def test_api_endpoints():
    """測試 API 端點回應時間"""
    print("\n=== API Endpoint Tests ===")
    endpoints = [
        ("GET /", "Homepage HTML"),
        ("GET /player", "Player HTML"),
        ("GET /admin", "Admin HTML"),
        ("GET /api/auto/status", "Worker status"),
        ("GET /api/chords/stats", "Chord stats"),
        ("GET /api/settings", "Settings"),
        ("GET /api/library/scan/status", "Scan status"),
        ("GET /api/chords/batch-detect/status", "Batch detect status"),
        ("GET /api/auto/log?limit=10", "Activity log"),
    ]

    for method_path, label in endpoints:
        method, path = method_path.split(" ", 1)
        url = BASE + path
        try:
            t0 = time.perf_counter()
            resp = requests.get(url, timeout=TIMEOUT_API / 1000)
            elapsed = (time.perf_counter() - t0) * 1000
            record(label, elapsed, resp.status_code)
        except requests.Timeout:
            record(label, TIMEOUT_API, "TIMEOUT")
        except requests.ConnectionError:
            record(label, 0, "CONN_ERROR", "Server not reachable")
        except Exception as e:
            record(label, 0, "ERROR", str(e))


def test_concurrent_api():
    """模擬 admin 頁面同時發出 4 個 polling 請求"""
    print("\n=== Concurrent Polling Test (simulates admin 3s poll) ===")
    import concurrent.futures

    poll_endpoints = [
        "/api/auto/status",
        "/api/chords/stats",
        "/api/library/scan/status",
        "/api/chords/batch-detect/status",
    ]

    def fetch(path):
        t0 = time.perf_counter()
        try:
            resp = requests.get(BASE + path, timeout=TIMEOUT_API / 1000)
            return path, (time.perf_counter() - t0) * 1000, resp.status_code
        except Exception as e:
            return path, (time.perf_counter() - t0) * 1000, str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, ep) for ep in poll_endpoints]
        for f in concurrent.futures.as_completed(futures):
            path, ms, status = f.result()
            record(f"Concurrent {path}", ms, status)


def test_playwright_pages():
    """使用 Playwright 測試完整頁面載入（含 JS 執行）"""
    print("\n=== Playwright Page Load Tests ===")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        pages_to_test = [
            ("/", "Index page load"),
            ("/player", "Player page load"),
            ("/admin", "Admin page load"),
        ]

        for path, label in pages_to_test:
            page = context.new_page()
            try:
                t0 = time.perf_counter()
                resp = page.goto(BASE + path, timeout=TIMEOUT_PAGE, wait_until="domcontentloaded")
                elapsed = (time.perf_counter() - t0) * 1000
                status = resp.status if resp else "no_response"
                record(label, elapsed, status)
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000
                err_type = type(e).__name__
                record(label, elapsed, "ERROR", f"{err_type}: {e}")
            finally:
                page.close()

        # Test: 頁面載入後 JS 能否正常 fetch API
        print("\n=== Playwright JS Fetch Tests ===")
        page = context.new_page()
        try:
            page.goto(BASE + "/", timeout=TIMEOUT_PAGE, wait_until="domcontentloaded")
            time.sleep(0.5)

            # 測試從頁面內 fetch API
            for api_path, label in [("/api/auto/status", "JS fetch auto/status"),
                                     ("/api/settings", "JS fetch settings")]:
                t0 = time.perf_counter()
                result = page.evaluate(f"""async () => {{
                    try {{
                        const r = await fetch('{api_path}', {{signal: AbortSignal.timeout(5000)}});
                        return {{status: r.status, ok: r.ok}};
                    }} catch(e) {{
                        return {{status: 'error', message: e.message}};
                    }}
                }}""")
                elapsed = (time.perf_counter() - t0) * 1000
                record(label, elapsed, result.get("status", "unknown"))
        except Exception as e:
            record("JS fetch test", 0, "ERROR", str(e))
        finally:
            page.close()

        browser.close()


def test_rapid_requests():
    """快速連續請求測試 — 模擬多使用者同時操作"""
    print("\n=== Rapid Sequential Requests (10x GET /) ===")
    times = []
    for i in range(10):
        try:
            t0 = time.perf_counter()
            resp = requests.get(BASE + "/", timeout=TIMEOUT_API / 1000)
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)
        except Exception:
            times.append(TIMEOUT_API)

    avg = sum(times) / len(times)
    p95 = sorted(times)[int(len(times) * 0.95)]
    record("10x GET / avg", avg, 200 if avg < ACCEPTABLE_MS else "SLOW")
    record("10x GET / p95", p95, 200 if p95 < ACCEPTABLE_MS else "SLOW")


def print_report():
    print("\n" + "=" * 70)
    print("  LIVECHORD RESPONSIVENESS TEST REPORT")
    print("=" * 70)

    passed = sum(1 for r in results if r["result"] == "PASS")
    failed = sum(1 for r in results if r["result"] == "FAIL")

    print(f"\n  Total: {len(results)} tests | PASS: {passed} | FAIL: {failed}\n")

    if failed > 0:
        print("  FAILED TESTS:")
        for r in results:
            if r["result"] == "FAIL":
                print(f"    - {r['test']}: {r['ms']}ms (status={r['status']}) {r['detail']}")
        print()

    print("  ALL RESULTS:")
    print(f"  {'Test':<40} {'Time':>8} {'Status':>10} {'Result':>6}")
    print(f"  {'-'*40} {'-'*8} {'-'*10} {'-'*6}")
    for r in results:
        print(f"  {r['test']:<40} {r['ms']:>6}ms {str(r['status']):>10} {r['result']:>6}")

    print("\n" + "=" * 70)
    return failed == 0


if __name__ == "__main__":
    print("LiveChord Responsiveness Test")
    print(f"Target: {BASE}")
    print(f"Acceptable response time: < {ACCEPTABLE_MS}ms")

    # Quick connectivity check
    try:
        requests.get(BASE + "/api/auto/status", timeout=3)
        print("Server is reachable.\n")
    except Exception:
        print("ERROR: Server is not reachable at " + BASE)
        print("Please start the server first.")
        exit(1)

    test_api_endpoints()
    test_concurrent_api()
    test_playwright_pages()
    test_rapid_requests()

    all_passed = print_report()
    exit(0 if all_passed else 1)
