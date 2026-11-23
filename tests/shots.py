#!/usr/bin/env python3
"""screenshots/ 갱신 — PC(다크/라이트)·모바일(390px) 주요 상태 캡처.

실행:
  LD_LIBRARY_PATH=/workspace/tmp/sysroot/usr/lib/x86_64-linux-gnu \
  PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
  python3 redwiselab/apex-rule-calculator/tests/shots.py
"""
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "../../.."))
PAGE = "file://" + os.path.join(ROOT, "redwiselab/apex-rule-calculator/index.html")
SHOTS = os.path.join(ROOT, "redwiselab/apex-rule-calculator/screenshots")


def shot(pg, name, full=False):
    path = os.path.join(SHOTS, name)
    pg.screenshot(path=path, full_page=full)
    print("saved", name)


def set_state(pg, mode, size, typ, start, bal, hwm, net=0, largest=0, days=0, rnd=1):
    pg.click(f'[data-mode="{mode}"]')
    pg.click(f'[data-size="{size}"]')
    pg.click(f'[data-type="{typ}"]')
    pg.fill("#start", f"{start:,}")
    pg.fill("#balance", f"{bal:,}")
    pg.fill("#hwm", f"{hwm:,}" if hwm is not None else "")
    if mode == "pa":
        pg.fill("#cycleNet", f"{net:,}")
        pg.fill("#largestDay", f"{largest:,}")
        pg.fill("#qualDays", str(int(days)))
        pg.select_option("#round", str(rnd))
    pg.wait_for_timeout(250)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()

        # ── PC (1360) ──
        pg = b.new_page(viewport={"width": 1360, "height": 1000}, device_scale_factor=2)
        pg.goto(PAGE)
        pg.wait_for_timeout(300)

        # 기본 데모 (PA 50K Intraday)
        set_state(pg, "pa", "50K", "Intraday", 50000, 50900, 51150, 900, 1000, 3, 1)
        pg.evaluate("window.scrollTo(0,0)")
        shot(pg, "01-pc-dark-full.png", full=True)
        shot(pg, "02-pc-hero.png")

        # 출금 가능 상태 (150K Intraday)
        set_state(pg, "pa", "150K", "Intraday", 150000, 155000, 156200, 5000, 1000, 5, 1)
        pg.locator(".result").screenshot(path=os.path.join(SHOTS, "03-pc-payout-eligible.png"))
        print("saved 03-pc-payout-eligible.png")

        # 출금 불가 + 상한 보조 표기 (50K EOD, 정확히 50%)
        set_state(pg, "pa", "50K", "EOD", 50000, 53000, 53000, 3000, 1500, 5, 2)
        shot(pg, "08-pc-payout-ineligible.png")

        # HWM 경고 (EOD, 입력 HWM < 현재 잔고) — 입력 패널
        set_state(pg, "pa", "50K", "EOD", 50000, 53000, 52600, 3000, 1500, 5, 2)
        pg.locator("section.panel").screenshot(path=os.path.join(SHOTS, "12-pc-hwm-status.png"))
        print("saved 12-pc-hwm-status.png")

        # 라이트 테마
        pg.click("#themeBtn")
        pg.wait_for_timeout(250)
        set_state(pg, "pa", "50K", "Intraday", 50000, 50900, 51150, 900, 1000, 3, 1)
        pg.evaluate("window.scrollTo(0,0)")
        shot(pg, "04-pc-light.png", full=True)

        # 평가 모드 (라이트, EOD → DLL 표기 확인)
        set_state(pg, "eval", "50K", "EOD", 50000, 51500, 51500)
        pg.evaluate("window.scrollTo(0,0)")
        shot(pg, "05-pc-eval-light.png", full=True)
        pg.close()

        # ── 모바일 (390 × 844) ──
        mp = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                        is_mobile=True, has_touch=True)
        mp.goto(PAGE)
        mp.wait_for_timeout(300)
        set_state(mp, "pa", "50K", "Intraday", 50000, 50900, 51150, 900, 1000, 3, 1)
        shot(mp, "06-mobile-dark-full.png", full=True)

        # 결과 영역
        mp.locator(".result").screenshot(path=os.path.join(SHOTS, "07-mobile-result.png"))
        print("saved 07-mobile-result.png")

        # 입력 패널 하단(마지막 버튼·힌트) — 하단 바와 겹치는지 육안 확인용
        mp.evaluate("document.querySelector('#demoBtn').scrollIntoView({block:'center'})")
        mp.wait_for_timeout(200)
        shot(mp, "09-mobile-input-bottom.png")

        # 출금 불가 상태의 금액 표기 (보조 스타일)
        set_state(mp, "pa", "50K", "EOD", 50000, 53000, 53000, 3000, 1500, 5, 2)
        mp.evaluate("document.querySelector('#payoutMoney').scrollIntoView({block:'center'})")
        mp.wait_for_timeout(200)
        shot(mp, "10-mobile-payout-ineligible.png")

        # 문서 최하단 — 푸터가 바에 가리지 않는지
        mp.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        mp.wait_for_timeout(200)
        shot(mp, "11-mobile-bottom.png")
        mp.close()

        b.close()


if __name__ == "__main__":
    main()
