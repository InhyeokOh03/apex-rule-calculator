#!/usr/bin/env python3
"""웹앱 ↔ 엑셀 교차검증 + Instinct 검수 피드백 반영 검증 (브라우저 실제 렌더링 기준).

1) tests/excel_expected.json (엑셀 수식 재계산 결과)을 읽어 5개 시나리오 × 19항목 = 95개 대조.
2) HWM 보정 케이스(EOD/Intraday 분리)를 공식 규칙에서 직접 계산한 기대값과 대조.
3) Instinct 피드백 항목(출금 불가 표기·라벨·카피·비활성 규정·기준일)의 화면 문구 확인.
4) 390px 모바일에서 하단 고정 바가 입력란을 가리지 않는지 확인.

실행:
  LD_LIBRARY_PATH=/workspace/tmp/sysroot/usr/lib/x86_64-linux-gnu \
  PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \
  python3 redwiselab/apex-rule-calculator/tests/test_e2e.py
"""
import json, os, sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "../../.."))
PAGE = "file://" + os.path.join(ROOT, "redwiselab/apex-rule-calculator/index.html")
SHOTS = os.path.join(ROOT, "browser")
sys.path.insert(0, HERE)

# HWM 보정 케이스 — 공식 규칙에서 직접 계산한 기대값
#   Intraday: hwm = max(입력 HWM, 현재 잔고, 시작 잔고)      (장중 고점 ≥ 현재 잔고)
#   EOD     : hwm = max(입력 HWM, 시작 잔고)                 (마감 잔고 기준, 경고만)
#   임계값   = min(max(시작-DD, hwm-DD), 시작+100), 버퍼 = 잔고 - 임계값
HWM_CASES = [
    dict(name="C1_intraday_hwm_below_balance", mode="pa", size="50K", type="Intraday",
         start=50000, balance=51000, hwm=50000, net=0, largest=0, days=0, round=1,
         expect=dict(threshold=49000, buffer=2000, bufferPct=1.0, hwm=51000,
                     correctedBy="현재 잔고", belowBalance=False),
         status=["보정했습니다", "현재 잔고"]),
    dict(name="C2_eod_hwm_below_balance", mode="pa", size="50K", type="EOD",
         start=50000, balance=51000, hwm=50000, net=0, largest=0, days=0, round=1,
         expect=dict(threshold=48000, buffer=3000, bufferPct=1.5, hwm=50000,
                     correctedBy=None, belowBalance=True),
         status=["현재 잔고보다 낮습니다", "EOD 마감 잔고"]),
    dict(name="C3_eod_blank_hwm", mode="pa", size="50K", type="EOD",
         start=50000, balance=49500, hwm=None, net=0, largest=0, days=0, round=1,
         expect=dict(threshold=48000, buffer=1500, bufferPct=0.75, hwm=50000,
                     correctedBy="시작 잔고", belowBalance=False),
         status=["보정했습니다", "시작 잔고"]),
]


def fmt(n):
    return f"{round(n):,}"


def compare(sc):
    ex, acc, last = sc["excel"], sc["acc"], sc["last"]
    start = sc["start"]
    pairs = [
        ("threshold", last["F"]),
        ("buffer", last["G"]),
        ("bufferPct", last["H"]),
        ("safetyLine", start + acc["B7"]),
        ("minRequest", acc["B8"]),
        ("dailyCap", acc["B11"]),
        ("net", ex["B3"]),
        ("largest", float(ex["B4"])),
        ("ratio", ex["B5"] if isinstance(ex["B5"], (int, float)) else None),
        ("days", float(ex["B2"])),
        ("cap", float(ex["B10"])),
        ("safetyExcess", ex["B11"]),
        ("applyCap", ex["B13"]),
        ("remainingRounds", float(ex["B14"])),
        ("cumCap", ex["B15"]),
        ("requiredNet", ex["B16"] if isinstance(ex["B16"], (int, float)) else None),
    ]
    bools = [
        ("consPass", ex["B6"] == "통과"),
        ("balOk", ex["B8"] == "통과"),
        ("eligible", ex["B9"] == "가능"),
    ]
    return pairs, bools


def fill_state(pg, mode, size, typ, start, balance, hwm, net=0, largest=0, days=0, rnd=1):
    """입력 필드에 상태를 넣는다. hwm=None이면 빈칸(0으로 파싱)."""
    pg.click(f'[data-mode="{mode}"]')
    pg.click(f'[data-size="{size}"]')
    pg.click(f'[data-type="{typ}"]')
    pg.fill("#start", fmt(start))
    pg.fill("#balance", fmt(balance))
    pg.fill("#hwm", "" if hwm is None else fmt(hwm))
    if mode == "pa":
        pg.fill("#cycleNet", fmt(net))
        pg.fill("#largestDay", fmt(largest))
        pg.fill("#qualDays", str(int(days)))
        pg.select_option("#round", str(rnd))
    pg.wait_for_timeout(120)


def run_hwm_cases(pg):
    rows, fails, matched = [], 0, 0
    for c in HWM_CASES:
        fill_state(pg, c["mode"], c["size"], c["type"], c["start"], c["balance"], c["hwm"],
                   c["net"], c["largest"], c["days"], c["round"])
        got = pg.evaluate("window.__last")
        diffs = []
        checks = [
            ("threshold", c["expect"]["threshold"], got["threshold"]),
            ("buffer", c["expect"]["buffer"], got["buffer"]),
            ("bufferPct", c["expect"]["bufferPct"], got["bufferPct"]),
            ("hwm", c["expect"]["hwm"], got["hwm"]),
            ("hwmCorrectedBy", c["expect"]["correctedBy"], got["hwmCorrectedBy"]),
            ("hwmBelowBalance", c["expect"]["belowBalance"], got["hwmBelowBalance"]),
        ]
        for key, want, have in checks:
            ok = (want == have) if isinstance(want, (str, bool)) or want is None \
                else abs(float(have) - float(want)) < 0.01
            if ok:
                matched += 1
            else:
                diffs.append(f"{key}: app={have} expected={want}")

        status_txt = pg.eval_on_selector("#hwmStatus", "el => el.hidden ? '' : el.innerText")
        ok_status = all(s in status_txt for s in c["status"])
        if ok_status:
            matched += 1
        else:
            diffs.append(f"status: '{status_txt}' missing {c['status']}")

        if diffs:
            fails += 1
        rows.append((c["name"], "PASS" if not diffs else "FAIL", diffs))
        print(f"[{'PASS' if not diffs else 'FAIL'}] {c['name']:32s} "
              f"threshold={got['threshold']:,.0f} buffer={got['buffer']:,.0f} "
              f"hwm={got['hwm']:,.0f} corrected={got['hwmCorrectedBy']} warn={got['hwmBelowBalance']}")
        for d in diffs:
            print("      -", d)
    return matched, 7 * len(HWM_CASES), fails, rows


def run_ui_checks(pg):
    """Instinct 피드백 항목의 실제 화면 문구·표시 상태 확인."""
    out = []

    def add(name, ok, detail=""):
        out.append((name, bool(ok), detail))

    # U1: 출금 불가 + 상한 900 → "요건 충족 시 최대 $900" 보조 표기
    fill_state(pg, "pa", "50K", "EOD", 50000, 53000, 53000, net=3000, largest=1500, days=5, rnd=2)
    m = pg.evaluate("""() => {
        const money = document.querySelector('#payoutMoney');
        const big = document.querySelector('#outApplyCap');
        const lead = document.querySelector('#outApplyLead');
        const unit = document.querySelector('#outApplyUnit');
        const muted = getComputedStyle(document.querySelector('.mini-row .k')).color;
        return {state: money.dataset.state, big: big.innerText, leadHidden: lead.hidden,
                lead: lead.innerText, unit: unit.innerText,
                bigColor: getComputedStyle(big).color, mutedColor: muted,
                eligible: window.__last.eligible, applyCap: window.__last.applyCap};
    }""")
    add("U1 불가 시 상한 보조 표기", (m["eligible"] is False and m["state"] == "est"
        and m["big"] == "$900" and m["leadHidden"] is False and m["lead"] == "요건 충족 시 최대"
        and "지금은 신청 불가" in m["unit"] and m["bigColor"] == m["mutedColor"]), str(m))

    # U2: 출금 가능 → 금액 그대로 표시
    fill_state(pg, "pa", "150K", "Intraday", 150000, 155000, 156200, net=5000, largest=1000, days=5, rnd=1)
    m2 = pg.evaluate("""() => {
        const money = document.querySelector('#payoutMoney');
        const big = document.querySelector('#outApplyCap');
        const lead = document.querySelector('#outApplyLead');
        const unit = document.querySelector('#outApplyUnit');
        return {state: money.dataset.state, big: big.innerText, leadHidden: lead.hidden,
                unit: unit.innerText, eligible: window.__last.eligible, applyCap: window.__last.applyCap};
    }""")
    add("U2 가능 시 금액 표시", (m2["eligible"] is True and m2["state"] == "ok"
        and m2["big"] == "$900" and m2["leadHidden"] is True
        and m2["unit"] == "이번 회차 신청 가능 상한"), str(m2))

    # U3: 라벨 명확화
    lbl = pg.evaluate("""() => ({
        cycle: document.querySelector('label[for=cycleNet]').innerText,
        largest: document.querySelector('label[for=largestDay]').innerText,
        cons: document.querySelector('#chkConsD').innerText})""")
    add("U3 라벨 '마지막 출금 이후 순이익'", "마지막 출금 이후 순이익" in lbl["cycle"], lbl["cycle"])
    add("U3 라벨 '그 기간 가장 큰 수익일'", "그 기간 가장 큰 수익일" in lbl["largest"], lbl["largest"])
    add("U3 일관성 설명 기간 명시", "마지막 출금 이후 순이익" in lbl["cons"], lbl["cons"])

    # U4: 100% 초과 구간 카피 (임계값 고정)
    fill_state(pg, "pa", "50K", "Intraday", 50000, 54000, 54000, net=0, largest=0, days=0, rnd=1)
    sub1 = pg.eval_on_selector("#outBufferSub", "el => el.innerText")
    add("U4 버퍼 초과 카피(고정)", sub1 == "한도 이상 여유 (DD 대비 +$1,900 · 임계값 고정됨)", sub1)

    # U5: 100% 초과 + 임계값 미고정 (EOD는 마감 최고값만 따라가므로 임계값이 정지선 전일 수 있음)
    fill_state(pg, "pa", "50K", "EOD", 50000, 52500, 51000, net=0, largest=0, days=0, rnd=1)
    sub2 = pg.eval_on_selector("#outBufferSub", "el => el.innerText")
    add("U5 버퍼 초과 카피(미고정)", sub2 == "한도 이상 여유 (DD 대비 +$1,500)", sub2)

    # U6: PA 비활성 규정 한 줄
    note = pg.eval_on_selector("#inactiveNote", "el => [el.hidden, el.innerText]")
    add("U6 PA 비활성 규정 안내", (note[0] is False and "롤링 30일" in note[1]
        and "$50" in note[1] and "영구 폐쇄" in note[1]), str(note))

    # U7: 결과 카드 근처 기준일·대시보드 문구
    rn = pg.eval_on_selector("#resultNotice", "el => el.innerText")
    add("U7 결과 영역 기준일·최종 판단", ("2026-11-22" in rn and "Apex 대시보드" in rn), rn)

    # U8: 회차 상한 표 캡션의 미확인 세부조건 완화 문구
    cap = pg.eval_on_selector("#capCaption", "el => el.innerText")
    add("U8 Tradovate 세부조건 완화", ("Tradovate" in cap and "Apex 대시보드" in cap), cap)

    # U9: 평가 모드 DLL 옆 "(평가 기준)"
    fill_state(pg, "eval", "50K", "EOD", 50000, 50400, 50400)
    dll = pg.evaluate("""() => {
        const foot = document.querySelector('#evalBlocks .hero-foot').innerText;
        return {foot: foot, dll: document.querySelector('#outDll').innerText};
    }""")
    add("U9 평가 DLL '(평가 기준)'", ("(평가 기준)" in dll["foot"] and dll["dll"] != ""), str(dll))

    # U10: 평가 모드 일관성 단정 문구 제거
    acts = pg.eval_on_selector("#evalActions", "el => el.innerText")
    add("U10 평가 일관성 문구 완화", ("평가에는 일관성 규칙이 없" not in acts and "계좌 약관" in acts),
        acts[:160].replace("\n", " "))

    # U11: 보정 없는 정상 입력에서는 보정 안내 숨김
    fill_state(pg, "pa", "50K", "Intraday", 50000, 50900, 51150, net=900, largest=1000, days=3, rnd=1)
    hid = pg.eval_on_selector("#hwmStatus", "el => el.hidden")
    add("U11 보정 없을 때 안내 숨김", hid is True, f"hidden={hid}")

    # U12: 일관성 정확히 50% 경계 — "$0 더 필요" 대신 미달 설명
    fill_state(pg, "pa", "50K", "EOD", 50000, 53000, 53000, net=3000, largest=1500, days=5, rnd=2)
    bd = pg.evaluate("""() => ({cons: document.querySelector('#chkConsD').innerText,
                                  acts: document.querySelector('#actions').innerText})""")
    add("U12 정확히 50% 경계 카피", ("$0 더 필요" not in bd["cons"] and "미달" in bd["cons"]
        and "정확히 같습니다" in bd["acts"]), str(bd)[:200])

    return out


def run_mobile_checks(pg):
    """390px: 하단 고정 바가 콘텐츠/입력란을 가리지 않는지."""
    out = []
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_timeout(200)
    fill_state(pg, "pa", "50K", "EOD", 50000, 53000, 53000, net=3000, largest=1500, days=5, rnd=2)

    geom = pg.evaluate("""() => {
        const bar = document.querySelector('.mobilebar').getBoundingClientRect();
        const main = getComputedStyle(document.querySelector('main'));
        window.scrollTo(0, document.body.scrollHeight);
        const f = document.querySelector('footer').getBoundingClientRect();
        return {barH: bar.height, barTop: bar.top, mainPB: parseFloat(main.paddingBottom),
                footerBottom: f.bottom, vh: innerHeight,
                scrollY: window.scrollY, docH: document.body.scrollHeight};
    }""")
    out.append(("M1 하단 바 표시·높이", geom["barH"] > 40, str({k: geom[k] for k in ("barH", "mainPB", "vh")})))
    out.append(("M2 본문 하단 여백 ≥ 바 높이", geom["mainPB"] >= geom["barH"],
                f"padding-bottom={geom['mainPB']} barH={geom['barH']}"))
    out.append(("M3 최하단에서 푸터 미가림", geom["footerBottom"] <= geom["barTop"] + 1,
                f"footerBottom={geom['footerBottom']:.0f} barTop={geom['barTop']:.0f}"))

    sels = ["#start", "#balance", "#hwm", "#cycleNet", "#largestDay", "#qualDays",
            "#round", "#consistency", "#demoBtn"]
    rows = pg.evaluate("""(sels) => {
        const bar = () => document.querySelector('.mobilebar').getBoundingClientRect().top;
        const out = [];
        for (const s of sels){
            const el = document.querySelector(s);
            if (!el || el.offsetParent === null) continue;
            el.scrollIntoView({block: 'center'});
            const r = el.getBoundingClientRect();
            out.push([s, r.top, r.bottom, bar()]);
        }
        return out;
    }""", sels)
    worst = [r for r in rows if r[2] > r[3] - 2]
    out.append((f"M4 입력란 {len(rows)}개 바 위로 노출", len(worst) == 0, str(worst)[:200]))
    return out


def main():
    scenarios = json.load(open(os.path.join(HERE, "excel_expected.json")))
    rows_out = []
    collected = []
    fails = 0
    matched = 0
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1360, "height": 1000}, device_scale_factor=2)
        pg.goto(PAGE)
        pg.wait_for_timeout(300)

        for sc in scenarios:
            pg.click('[data-mode="pa"]')
            pg.click(f'[data-size="{sc["size"]}"]')
            pg.click(f'[data-type="{sc["type"]}"]')
            pg.fill("#start", fmt(sc["start"]))
            pg.fill("#balance", fmt(sc["excel"]["B7"]))
            pg.fill("#hwm", fmt(sc["hwm"]))
            pg.fill("#cycleNet", fmt(sc["excel"]["B3"]))
            pg.fill("#largestDay", fmt(sc["excel"]["B4"]))
            pg.fill("#qualDays", str(int(sc["excel"]["B2"])))
            pg.select_option("#round", str(sc["round"]))
            pg.wait_for_timeout(150)
            got = pg.evaluate("window.__last")

            ex, acc = sc["excel"], sc["acc"]
            last_f, last_g, last_h = sc["last"]["F"], sc["last"]["G"], sc["last"]["H"]
            pairs, bools = compare(sc)
            diffs = []
            for key, want in pairs:
                have = got.get(key)
                if want is None or have is None:
                    if want != have:
                        diffs.append(f"{key}: app={have} excel={want}")
                    else:
                        matched += 1
                    continue
                if abs(float(have) - float(want)) > 0.01:
                    diffs.append(f"{key}: app={have} excel={want}")
                else:
                    matched += 1
            for key, want in bools:
                if bool(got.get(key)) != bool(want):
                    diffs.append(f"{key}: app={got.get(key)} excel={want}")
                else:
                    matched += 1

            status = "PASS" if not diffs else "FAIL"
            if diffs:
                fails += 1
            rows_out.append((sc["name"], sc["size"], sc["type"], status, diffs))
            collected.append({"name": sc["name"], "size": sc["size"], "type": sc["type"],
                              "round": sc["round"], "app": got,
                              "excel": {"threshold": last_f, "buffer": last_g, "bufferPct": last_h,
                                        "minRequest": acc["B8"], "ratio": ex["B5"],
                                        "consPass": ex["B6"], "balOk": ex["B8"],
                                        "eligible": ex["B9"], "applyCap": ex["B13"],
                                        "cap": ex["B10"], "cumCap": ex["B15"]}})
            print(f"[{status}] {sc['name']:22s} {sc['size']:5s} {sc['type']:9s} "
                  f"threshold={got['threshold']:,.0f} buffer={got['buffer']:,.0f} "
                  f"route={got['eligible']} cap={got['applyCap']:,.0f}")
            for d in diffs:
                print("      -", d)

            if sc["name"] in ("S1_excel_demo", "S4_150k_eligible", "S5_50k_eod_boundary"):
                pg.locator(".layout").screenshot(path=os.path.join(SHOTS, f"apex_{sc['name']}.png"))

        excel_matched = matched
        print()
        hwm_matched, hwm_total, hwm_fails, hwm_rows = run_hwm_cases(pg)
        print()
        ui = run_ui_checks(pg)
        for name, ok, detail in ui:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  <- {detail}"))
        print()
        mob = run_mobile_checks(pg)
        for name, ok, detail in mob:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  <- {detail}"))

        b.close()

    json.dump(collected, open(os.path.join(HERE, "app_actual.json"), "w"), ensure_ascii=False, indent=1)
    ui_ok = sum(1 for _, ok, _ in ui if ok)
    mob_ok = sum(1 for _, ok, _ in mob if ok)
    total_matched = excel_matched + hwm_matched + ui_ok + mob_ok
    total = 95 + hwm_total + len(ui) + len(mob)
    summary = {"matched": total_matched, "total": total,
               "groups": {
                   "excel": [excel_matched, 95],
                   "hwm": [hwm_matched, hwm_total],
                   "ui": [ui_ok, len(ui)],
                   "mobile": [mob_ok, len(mob)],
               }}
    json.dump(summary, open(os.path.join(HERE, "summary.json"), "w"), ensure_ascii=False, indent=1)
    print()
    print(f"엑셀 대조 {excel_matched}/95 · HWM 보정 {hwm_matched}/{hwm_total} · "
          f"UI 문구 {ui_ok}/{len(ui)} · 모바일 {mob_ok}/{len(mob)}")
    print(f"총 {total_matched}/{total} 일치 · 실패 시나리오 {fails + hwm_fails}개")
    ok = (excel_matched == 95 and hwm_matched == hwm_total and ui_ok == len(ui) and mob_ok == len(mob))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
