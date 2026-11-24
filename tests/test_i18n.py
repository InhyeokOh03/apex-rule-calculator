#!/usr/bin/env python3
"""KO/EN 전환 기능 검증 (브라우저 실제 렌더링 기준).

1) KO 회귀 — 커밋본(git HEAD)과 현재본의 화면 텍스트가 상태별로 완전히 동일한지.
2) EN 화면 — html lang·title·description·OG, 누락 키 0, JS 오류 0, 한글 잔존 0.
3) 수치 동일 — 같은 입력에서 EN/KO 계산 결과(window.__last)와 표기 숫자가 같은지.
4) 토글 — KO→EN→KO 왕복 시 KO 문구가 원문 그대로 복원되는지, 주소·저장값.
5) 레이아웃 — 1360/390/320 가로 오버플로 0, 헤더 겹침 없음, 모바일 하단 바 미가림.
6) 스크린샷 — browser/apex_en_*.png 갱신.

실행:
  LD_LIBRARY_PATH=/workspace/tmp/sysroot/usr/lib/x86_64-linux-gnu \\
  PLAYWRIGHT_BROWSERS_PATH=/workspace/.cache/ms-playwright \\
  python3 redwiselab/apex-rule-calculator/tests/test_i18n.py
"""
import functools
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(REPO, "..", ".."))
SHOTS = os.path.join(ROOT, "browser")
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  <- {detail}"))


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory):
    handler = functools.partial(QuietHandler, directory=directory)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def norm(txt):
    return re.sub(r"\s+", " ", (txt or "")).strip()


# ── 상태 주입 ─────────────────────────────────────────────
STATES = [
    dict(name="기본(PA 50K Intraday)", mode="pa", size="50K", type="Intraday",
         start="50,000", balance="50,900", hwm="51,150", net="900", largest="1,000", days="3", rnd=1),
    dict(name="PA 50K EOD 출금불가(보조표기)", mode="pa", size="50K", type="EOD",
         start="50,000", balance="53,000", hwm="53,000", net="3,000", largest="1,500", days="5", rnd=2),
    dict(name="PA 150K 출금가능", mode="pa", size="150K", type="Intraday",
         start="150,000", balance="155,000", hwm="156,200", net="5,000", largest="1,000", days="5", rnd=1),
    dict(name="PA 버퍼 100% 초과(고정)", mode="pa", size="50K", type="Intraday",
         start="50,000", balance="54,000", hwm="54,000", net="0", largest="0", days="0", rnd=1),
    dict(name="PA EOD HWM 경고", mode="pa", size="50K", type="EOD",
         start="50,000", balance="51,000", hwm="50,000", net="0", largest="0", days="0", rnd=1),
    dict(name="PA EOD HWM 공란", mode="pa", size="50K", type="EOD",
         start="50,000", balance="49,500", hwm="", net="0", largest="0", days="0", rnd=1),
    dict(name="PA 레거시 30%", mode="pa", size="50K", type="Intraday",
         start="50,000", balance="52,900", hwm="52,900", net="2,900", largest="1,000", days="5", rnd=3),
    dict(name="PA 손실(임계값 터치)", mode="pa", size="25K", type="Intraday",
         start="25,000", balance="23,900", hwm="25,000", net="0", largest="0", days="0", rnd=1),
    dict(name="평가 50K EOD Tradovate", mode="eval", size="50K", type="EOD",
         start="50,000", balance="50,400", hwm="50,400", platform="free"),
    dict(name="평가 25K Intraday 목표달성", mode="eval", size="25K", type="Intraday",
         start="25,000", balance="26,600", hwm="26,600", platform="stop"),
]


def fill(pg, st):
    pg.click(f'[data-mode="{st["mode"]}"]')
    pg.click(f'[data-size="{st["size"]}"]')
    pg.click(f'[data-type="{st["type"]}"]')
    pg.fill("#start", st["start"])
    pg.fill("#balance", st["balance"])
    pg.fill("#hwm", st["hwm"])
    if st["mode"] == "pa":
        pg.fill("#cycleNet", st["net"])
        pg.fill("#largestDay", st["largest"])
        pg.fill("#qualDays", st["days"])
        pg.select_option("#round", str(st["rnd"]))
        pg.select_option("#consistency", "0.3" if st["name"] == "PA 레거시 30%" else "0.5")
    else:
        pg.select_option("#platform", st.get("platform", "stop"))
    pg.wait_for_timeout(120)


def snapshot(pg):
    return {
        "main": norm(pg.eval_on_selector("main", "el => el.innerText")),
        "bar": norm(pg.eval_on_selector(".mobilebar", "el => el.innerText")),
        "title": pg.title(),
    }


def hangul_nodes(pg):
    return pg.evaluate("""() => {
      const out = [];
      const visible = el => {
        if (!el) return false;
        if (el.closest('script,style')) return false;
        const r = el.getClientRects();
        return r.length > 0;
      };
      const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = walk.nextNode())){
        if (!/[가-힣]/.test(n.nodeValue)) continue;
        if (!visible(n.parentElement)) continue;
        const p = n.parentElement;
        out.push(p.tagName + (p.id ? '#' + p.id : '') + ' :: ' + n.nodeValue.trim().slice(0, 80));
      }
      document.querySelectorAll('option').forEach(o => {
        if (/[가-힣]/.test(o.textContent) && o.closest('select').getClientRects().length) out.push('option :: ' + o.textContent);
      });
      document.querySelectorAll('[aria-label]').forEach(el => {
        if (/[가-힣]/.test(el.getAttribute('aria-label')) && el.getClientRects().length) out.push('aria :: ' + el.getAttribute('aria-label'));
      });
      return out;
    }""")


NUM_IDS = ["#outBuffer", "#outBufferSub", "#outThreshold", "#outBalance", "#outMnq", "#outSafety",
           "#outSafetyD", "#outMinReq", "#outRisk", "#outRiskD", "#outDaily", "#outApplyCap", "#outApplyUnit",
           "#outCap", "#outExcess", "#outRemaining", "#outCum", "#capLabel", "#outTarget", "#outEvalThreshold",
           "#outDll", "#evalProgressText", "#evalRemainText", "#mbV", "#chkDaysV", "#chkConsV", "#chkBalV",
           "#hwmStatus", "#capsLabel"]


def display_numbers(pg):
    """화면에 출력된 숫자 토큰만 뽑는다(언어에 따라 단위·문장만 달라져야 함)."""
    raw = pg.evaluate("""(ids) => {
      const out = {};
      for (const s of ids){ const el = document.querySelector(s); out[s] = el ? el.textContent : null; }
      return out;
    }""", NUM_IDS)
    tok = lambda txt: re.findall(r"−?\$[\d,]+|\d+(?:\.\d+)?%|\d[\d,]*", txt or "")
    out = {k: tok(v) for k, v in raw.items()}
    # 상한 표는 셀 단위로 비교(1회 / Payout 1 처럼 라벨이 붙어 숫자가 붙는 것 방지)
    cells = pg.evaluate("() => Array.from(document.querySelectorAll('#capsTable td, #capsTable th')).map(el => el.textContent)")
    out["#capsTable:cell"] = [tok(c) for c in cells]
    return out


NUMERIC_FIELDS = ["threshold", "buffer", "bufferPct", "safetyLine", "minRequest", "riskLimit",
                  "dailyCap", "cap", "safetyExcess", "applyCap", "cumCap", "remainingRounds",
                  "minDaily", "hwm", "dd", "start", "balance"]


def compare_numbers(ko, en, label):
    diffs = []
    for k in NUMERIC_FIELDS:
        a, b = ko.get(k), en.get(k)
        if a is None and b is None:
            continue
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(float(a) - float(b)) > 1e-9:
                diffs.append(f"{k}: ko={a} en={b}")
        elif a != b:
            diffs.append(f"{k}: ko={a} en={b}")
    for k in ("round", "size", "type", "mode", "eligible", "consPass", "balOk", "daysOk"):
        if ko.get(k) != en.get(k):
            diffs.append(f"{k}: ko={ko.get(k)} en={en.get(k)}")
    return diffs


def main():
    os.makedirs(SHOTS, exist_ok=True)

    # 커밋본(HEAD)을 임시 폴더에 풀어 KO 회귀 비교 기준으로 쓴다.
    base_dir = tempfile.mkdtemp(prefix="apex_head_")
    try:
        head = subprocess.run(["git", "show", "HEAD:index.html"], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        print("git HEAD:index.html 을 읽지 못했습니다:", e)
        return 2
    open(os.path.join(base_dir, "index.html"), "w", encoding="utf-8").write(head)

    httpd_cur, port_cur = serve(REPO)
    httpd_base, port_base = serve(base_dir)
    cur_ko = f"http://127.0.0.1:{port_cur}/index.html"
    cur_en = cur_ko + "?lang=en"
    base_url = f"http://127.0.0.1:{port_base}/index.html"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ── 1) KO 회귀 (커밋본 vs 현재본) ──
            ctx_a = browser.new_context(viewport={"width": 1360, "height": 1000})
            pg_head = ctx_a.new_page(); pg_head.goto(base_url); pg_head.wait_for_timeout(250)
            ctx_b = browser.new_context(viewport={"width": 1360, "height": 1000})
            pg_cur = ctx_b.new_page(); pg_cur.goto(cur_ko); pg_cur.wait_for_timeout(250)

            regress_fail = []
            for st in STATES:
                fill(pg_head, st); fill(pg_cur, st)
                a, b = snapshot(pg_head), snapshot(pg_cur)
                if a["main"] != b["main"]:
                    # 어느 문장이 달라졌는지 힌트
                    sa, sb = set(a["main"].split(" ")), set(b["main"].split(" "))
                    diff = list(sa ^ sb)[:6]
                    regress_fail.append(f"{st['name']}: {diff}")
                if a["bar"] != b["bar"] or a["title"] != b["title"]:
                    regress_fail.append(f"{st['name']}: 바/타이틀 불일치")
            record(f"KO 회귀 {len(STATES)}개 상태 커밋본과 동일", not regress_fail, str(regress_fail)[:400])

            # KO 기본값(파라미터 없음)은 한국어
            record("기본 진입은 한국어", pg_cur.evaluate("document.documentElement.lang") == "ko"
                   and "프랍 룰 계산기" in pg_cur.title(),
                   f"{pg_cur.evaluate('document.documentElement.lang')} / {pg_cur.title()}")

            # ── 2) EN 화면 ──
            ctx_en = browser.new_context(viewport={"width": 1360, "height": 1100}, device_scale_factor=2)
            pg = ctx_en.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
            pg.goto(cur_en); pg.wait_for_timeout(350)

            record("?lang=en 이 언어 속성·제목 반영",
                   pg.evaluate("document.documentElement.lang") == "en"
                   and pg.title().startswith("Apex Rule Calculator"),
                   f"{pg.evaluate('document.documentElement.lang')} / {pg.title()}")
            record("EN JS 오류 없음", not errors, str(errors)[:300])
            record("EN 누락 번역 키 없음", pg.evaluate("window.ApexI18n.missing()") == [],
                   str(pg.evaluate("window.ApexI18n.missing()"))[:300])

            meta = pg.evaluate("""() => ({
                desc: document.querySelector('meta[name=description]').content,
                ogT: document.querySelector('meta[property="og:title"]').content,
                ogD: document.querySelector('meta[property="og:description"]').content,
                ogL: document.querySelector('meta[property="og:locale"]').content,
                ogLAlt: document.querySelector('meta[property="og:locale:alternate"]').content,
                ogU: document.querySelector('meta[property="og:url"]').content,
                hreflang: Array.from(document.querySelectorAll('link[rel=alternate][hreflang]')).map(l => l.hreflang + '=' + l.href)
            })""")
            record("EN 메타(description·OG·locale·url)",
                   "Apex Trader Funding" in meta["desc"] and meta["ogT"].startswith("Apex Rule Calculator")
                   and "drawdown" in meta["ogD"].lower() and meta["ogL"] == "en_US"
                   and meta["ogLAlt"] == "ko_KR" and meta["ogU"].endswith("?lang=en")
                   and len(meta["hreflang"]) == 3,
                   json.dumps(meta, ensure_ascii=False)[:400])

            # 한글 잔존 0 (PA·평가·레거시 상태 모두)
            leftover = []
            for st in STATES + [dict(STATES[6], name="레거시")]:
                fill(pg, st)
                leftover += hangul_nodes(pg)
            record("EN 화면 한글 잔존 0", not leftover, str(leftover)[:400])

            # ── 3) 수치 동일 (EN vs KO) ──
            num_fail = []
            for st in STATES:
                fill(pg_cur, st)
                ko_last = pg_cur.evaluate("window.__last")
                fill(pg, st)
                en_last = pg.evaluate("window.__last")
                d = compare_numbers(ko_last, en_last, st["name"])
                if d:
                    num_fail.append(f"{st['name']}: {d}")
                # 화면에 찍힌 숫자 토큰이 요소 단위로 같은지
                ko_nums = display_numbers(pg_cur)
                en_nums = display_numbers(pg)
                for key in NUM_IDS + ["#capsTable:cell"]:
                    if ko_nums[key] != en_nums[key]:
                        num_fail.append(f"{st['name']} {key}: ko={ko_nums[key]} en={en_nums[key]}")
            record(f"EN/KO 계산·표기 수치 동일 ({len(STATES)}개 상태)", not num_fail, str(num_fail)[:400])

            # ── 4) 토글 왕복 ──
            ko_before = snapshot(pg_cur)   # 토글 직전(현재 입력 상태) 한국어 화면
            pg_cur.click('[data-lang="en"]'); pg_cur.wait_for_timeout(200)
            record("토글 EN: 속성·주소 갱신",
                   pg_cur.evaluate("document.documentElement.lang") == "en"
                   and "lang=en" in pg_cur.url
                   and pg_cur.eval_on_selector('[data-lang="en"]', "el => el.getAttribute('aria-pressed')") == "true",
                   f"{pg_cur.url} / {pg_cur.evaluate('document.documentElement.lang')}")
            pg_cur.click('[data-lang="ko"]'); pg_cur.wait_for_timeout(200)
            ko_after = snapshot(pg_cur)
            import difflib
            _d = [f"{tag}: {' '.join(ko_before['main'].split()[i1:i2])[:80]} -> {' '.join(ko_after['main'].split()[j1:j2])[:80]}"
                  for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ko_before["main"].split(), ko_after["main"].split()).get_opcodes() if tag != "equal"]
            record("토글 KO: 문구 원문 그대로 복원",
                   ko_after["main"] == ko_before["main"] and ko_after["title"] == ko_before["title"]
                   and "lang=en" not in pg_cur.url,
                   f"url={pg_cur.url} title={ko_before['title']!r}->{ko_after['title']!r} diff={_d[:4]}")
            record("KO 복원 후 저장값 ko",
                   pg_cur.evaluate("localStorage.getItem('redwise.apex.lang.v1')") == "ko",
                   str(pg_cur.evaluate("localStorage.getItem('redwise.apex.lang.v1')")))

            # 새로고침(파라미터 없음) → 저장된 언어 유지
            pg_cur.click('[data-lang="en"]'); pg_cur.wait_for_timeout(150)
            pg_cur.goto(cur_ko); pg_cur.wait_for_timeout(300)
            record("EN 선택 후 재방문 시 EN 유지",
                   pg_cur.evaluate("document.documentElement.lang") == "en",
                   pg_cur.evaluate("document.documentElement.lang"))
            pg_cur.click('[data-lang="ko"]'); pg_cur.wait_for_timeout(150)

            # KO/EN 토글 버튼 상태
            record("토글 버튼 KO/EN 두 개·눌림 상태",
                   len(pg_cur.query_selector_all("[data-lang]")) == 2
                   and pg_cur.eval_on_selector('[data-lang="ko"]', "el => el.getAttribute('aria-pressed')") == "true",
                   "")

            # ── 5) 레이아웃 ──
            layout_fail = []
            for w, h in ((1360, 1000), (390, 844), (320, 720)):
                pg.set_viewport_size({"width": w, "height": h}); pg.wait_for_timeout(200)
                over = pg.evaluate("document.documentElement.scrollWidth - innerWidth")
                if over > 0:
                    layout_fail.append(f"{w}px 가로 오버플로 {over}px")
                head = pg.evaluate("""() => {
                  const r = s => { const e = document.querySelector(s); const b = e.getBoundingClientRect();
                    return {l: b.left, r: b.right, vis: getComputedStyle(e).display !== 'none'}; };
                  return {brand: r('.brand'), lang: r('.langseg'), theme: r('#themeBtn'),
                          link: r('.top-link'), vw: innerWidth};
                }""")
                parts = [head["brand"], head["lang"], head["theme"]] + ([head["link"]] if head["link"]["vis"] else [])
                parts.sort(key=lambda p: p["l"])
                for a, b in zip(parts, parts[1:]):
                    if a["r"] > b["l"] + 1:
                        layout_fail.append(f"{w}px 헤더 겹침 {a['r']:.0f}>{b['l']:.0f}")
                if head["lang"]["vis"] is False:
                    layout_fail.append(f"{w}px 언어 토글 숨김")
                # 모바일 하단 바가 푸터를 가리지 않는지
                if w <= 940:
                    geo = pg.evaluate("""() => {
                      window.scrollTo(0, document.body.scrollHeight);
                      const bar = document.querySelector('.mobilebar').getBoundingClientRect();
                      const f = document.querySelector('footer').getBoundingClientRect();
                      return {barTop: bar.top, footerBottom: f.bottom, barH: bar.height};
                    }""")
                    if geo["footerBottom"] > geo["barTop"] + 1:
                        layout_fail.append(f"{w}px 푸터가 하단 바에 가림")
                pg.evaluate("window.scrollTo(0, 0)")
            record("EN 레이아웃(1360·390·320) 오버플로/겹침 없음", not layout_fail, str(layout_fail)[:300])

            # ── 6) 스크린샷 ──
            pg.set_viewport_size({"width": 1360, "height": 1100})
            pg.goto(cur_en); pg.wait_for_timeout(400)
            pg.screenshot(path=os.path.join(SHOTS, "apex_en_pc.png"), full_page=True)
            pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(SHOTS, "apex_en_mobile.png"), full_page=True)
            record("스크린샷 저장(EN PC·모바일)", True, "browser/apex_en_pc.png, browser/apex_en_mobile.png")

            # ── 7) 영문 정적 엔트리(en/) 메타 ──
            en_entry = os.path.join(REPO, "en", "index.html")
            if os.path.exists(en_entry):
                html = open(en_entry, encoding="utf-8").read()
                ok = ('lang="en"' in html and 'og:title' in html and 'og:description' in html
                      and 'canonical' in html and '?lang=en' in html)
                record("en/ 정적 엔트리 메타", ok, "en/index.html")
            else:
                record("en/ 정적 엔트리 메타", False, "en/index.html 없음")

            browser.close()
    finally:
        httpd_cur.shutdown()
        httpd_base.shutdown()
        shutil.rmtree(base_dir, ignore_errors=True)

    ok = sum(1 for _, o, _ in RESULTS if o)
    total = len(RESULTS)
    print()
    print(f"i18n 검증 {ok}/{total} 통과")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
