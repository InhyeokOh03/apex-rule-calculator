#!/usr/bin/env python3
"""엑셀(부록 B 계산기) 시나리오 재계산 → JSON 추출.

각 시나리오는 원본 xlsx를 복사해 입력값만 바꾼 뒤, `formulas` 라이브러리로
실제 수식을 재계산하여 기준값을 만든다. 웹앱 검증(test_e2e.py)이 이 값을 쓴다.
"""
import json, os, shutil, sys
import openpyxl

SRC = "downloads/ebook/ebook2_apex/Apex_드로우다운_출금_자동계산기_머니콤파스.xlsx"
OUT_DIR = "redwiselab/apex-rule-calculator/tests"
WORK = os.path.join(OUT_DIR, "work")

# name, type, size, start, dd, minDaily, pointValue, round, records[(date,pnl,peak,payout)]
SCENARIOS = [
    dict(name="S1_excel_demo", type="Intraday", size="50K", start=50000, dd=2000,
         minDaily=200, pv=2, round=1,
         records=[("2026-11-09",500,50650,0),("2026-11-10",200,50900,0),("2026-11-11",0,50750,0),
                  ("2026-11-12",-200,50700,0),("2026-11-13",-600,50000,0),("2026-11-16",1000,51150,0)]),
    dict(name="S2_100k_eod_payout", type="EOD", size="100K", start=100000, dd=3000,
         minDaily=300, pv=2, round=3,
         records=[("2026-11-09",800,103500,0),("2026-11-10",450,102000,0),("2026-11-11",-300,101200,0),
                  ("2026-11-12",1200,104800,0),("2026-11-13",600,103100,2000),
                  ("2026-11-16",350,102600,0),("2026-11-17",900,104200,0)]),
    dict(name="S3_25k_intraday", type="Intraday", size="25K", start=25000, dd=1000,
         minDaily=100, pv=2, round=2,
         records=[("2026-11-09",180,25500,0),("2026-11-10",50,25100,0),("2026-11-11",420,25400,0),
                  ("2026-11-12",-120,25200,0),("2026-11-13",260,25600,0),("2026-11-16",310,25750,0)]),
    dict(name="S4_150k_eligible", type="Intraday", size="150K", start=150000, dd=4000,
         minDaily=300, pv=2, round=1,
         records=[("2026-11-09",1000,152000,0),("2026-11-10",1000,153000,0),("2026-11-11",1000,154200,0),
                  ("2026-11-12",1000,155400,0),("2026-11-13",1000,156200,0)]),
    dict(name="S5_50k_eod_boundary", type="EOD", size="50K", start=50000, dd=2000,
         minDaily=250, pv=2, round=2,
         records=[("2026-11-09",1500,52500,0),("2026-11-10",250,52300,0),("2026-11-11",250,52100,0),
                  ("2026-11-12",250,51900,0),("2026-11-13",750,52600,0)]),
]


def build(sc):
    fn = sc["name"] + ".xlsx"
    path = os.path.join(WORK, fn)
    shutil.copyfile(SRC, path)
    wb = openpyxl.load_workbook(path)
    a = wb["계좌"]
    a["B2"] = sc["type"]
    a["B3"] = sc["start"]
    a["B4"] = sc["dd"]
    a["B9"] = sc["minDaily"]
    a["B12"] = sc["size"]
    a["B13"] = sc["pv"]
    a["B14"] = 5
    a["B15"] = 500
    w = wb["출금"]
    w["B12"] = sc["round"]

    g = wb["기록"]
    # 예시 행 포함 2~101행을 모두 비운 뒤 시나리오 기록을 쥤다
    for r in range(2, 102):
        for col in ("A", "B", "C", "I", "J", "L", "M", "N", "O"):
            g[f"{col}{r}"] = None
    for i, (d, pnl, peak, payout) in enumerate(sc["records"]):
        r = 2 + i
        import datetime
        g[f"A{r}"] = datetime.datetime.strptime(d, "%Y-%m-%d")
        g[f"B{r}"] = pnl
        g[f"C{r}"] = peak
        g[f"O{r}"] = payout
    wb.save(path)
    return path, fn


def recalc(path, fn):
    import formulas
    xl = formulas.ExcelModel().loads(path).finish()
    sol = xl.calculate()
    key = fn.upper()

    def get(sheet, cell):
        suffix = f"{sheet}'!{cell}"
        for k, v in sol.items():
            if k.endswith(suffix) or k.startswith(f"'[{key}]{sheet}'!{cell}"):
                try:
                    return v.value[0, 0]
                except Exception:
                    try:
                        return v.value
                    except Exception:
                        return v
        return None

    out = {c: get("출금", c) for c in ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10",
                                       "B11", "B13", "B14", "B15", "B16", "B19"]}
    acc = {c: get("계좌", c) for c in ["B5", "B6", "B7", "B8", "B11", "B16"]}
    rows = []
    for r in range(2, 102):
        d = get("기록", f"D{r}")
        if d is None or d == "":
            break
        rows.append({k: get("기록", f"{k}{r}") for k in ["B", "C", "D", "E", "F", "G", "H"]})
    return out, acc, rows


def main():
    os.makedirs(WORK, exist_ok=True)
    results = []
    for sc in SCENARIOS:
        path, fn = build(sc)
        out, acc, rows = recalc(path, fn)
        # 앱 입력용 집계값: HWM = max(장중 고점, 마감) or max(마감)
        hwm = max([sc["start"]] + [
            max(r["C"], r["D"]) if sc["type"] == "Intraday" else r["D"] for r in rows])
        rec = dict(sc)
        rec.pop("records")
        rec.update({
            "excel": out, "acc": acc, "rows": rows, "hwm": hwm,
            "last": rows[-1] if rows else None,
        })
        results.append(rec)
        print(f"[ok] {sc['name']}  잔고={out['B7']}  일관성={out['B5']}  가능={out['B9']}")
    with open(os.path.join(OUT_DIR, "excel_expected.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
