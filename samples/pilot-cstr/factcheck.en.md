# Fact check: The stirred-tank reactor (CSTR) in process-sim-lab (English)

Reviewed-by: lane S15-ms-13 (Claude Code); Nghiep reviews the video before upload
Date: 2026-11-25
Narration: 577bf041387a

Every number in the narration and on screen, checked against process-sim-lab at commit 30f2b12 (read-only checkout)
and recomputed with `isothermal_cstr_first_order` and Python's `math` in the lane. Derived numbers name the formula
they come from.

- [x] process-sim-lab is an open source (MIT) process simulator written in Python, and the CSTR is one of its units - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/README.md
- [x] Model assumptions: steady state, isothermal, constant volumetric flow Q (no density change), one irreversible reaction A -> B formed 1:1, first-order rate r = k * C_A - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/src/process_sim_lab/units/cstr.py
- [x] Space time tau = V / Q in seconds; mole balance F_A0 - F_A - k * C_A * V = 0 with C_A = F_A / Q gives F_A = F_A0 / (1 + k * tau) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] Conversion X = k * tau / (1 + k * tau) and Damkohler number Da = k * tau - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] Da = 1 gives X = 1/2 and Da = 5 gives X = 5/6 (from X = Da / (1 + Da)) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] The function computes tau, Da and X, subtracts F_A0 * X from A, adds it to B, keeps the feed's temperature and pressure, and returns outlet, conversion, space_time and damkohler (four fields) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/src/process_sim_lab/units/cstr.py
- [x] Input checks: rate_constant >= 0, volume > 0, volumetric_flow > 0, reactant in the feed, reactant != product; each raises ValueError and test_rejects_bad_inputs covers all five - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/tests/test_cstr.py
- [x] Example: feed A 10 mol/s and W 50 mol/s at 330 K, k = 0.01 1/s, V = 2 m3, Q = 0.004 m3/s - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] Example result: tau = 2 / 0.004 = 500 s, Da = 5, X = 5/6 = 0.8333 (83.3 %) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/tests/test_cstr.py
- [x] The test checks X to 12 decimal places, and the outlet A (10/6) and B (50/6) to 12 places too - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/tests/test_cstr.py
- [x] Outlet: A 10/6 = 1.67 mol/s, B 50/6 = 8.33 mol/s, W 50 mol/s; total 60 mol/s in and out - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/evals/cases/01-cstr-first-order.json
- [x] Five tests: analytic conversion; moles conserved with T and P kept; zero rate gives zero conversion and no B; larger volume gives higher conversion but below 1; bad inputs raise - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/tests/test_cstr.py
- [x] Sizing for X = 0.9: Da = X / (1 - X) = 9, tau = 9 / 0.01 = 900 s, V = 900 * 0.004 = 3.6 m3 (inverted from X = Da / (1 + Da), recomputed in the lane) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] Sizing for X = 0.99: Da = 99, V = 99 / 0.01 * 0.004 = 39.6 m3, which is 11 times 3.6 m3 (recomputed in the lane) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] PFR is also a unit; its conversion is X = 1 - exp(-k * tau), so at Da = 5 X = 0.9933 (99.3 %) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/pfr.md
- [x] N equal CSTRs in series: X_N = 1 - (1 + Da / N)^-N tends to 1 - exp(-Da); with Da = 5: N = 2 gives 0.918, N = 3 0.947, N = 5 0.969, N = 10 0.983 (recomputed in the lane), and the PFR tests check this limit - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/tests/test_pfr.py
- [x] In a flowsheet the unit type is cstr with one inlet and one outlet - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
- [x] Worked example: mixer, CSTR, flash drum and purge splitter; 80 % of the vapour is recycled to the mixer; the recycle is the tear stream - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/flowsheet-worked-example.md
- [x] Worked example reactor: k = 0.023 1/s, V = 2 m3, Q = 0.02 m3/s, tau = 100 s, Da = 2.3, X per pass = 2.3 / 3.3 = 0.697 (about 70 %) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/flowsheet-worked-example.md
- [x] Worked example overall: feed A 5.6, reacted 4.6, so 4.6 / 5.6 = 0.82 overall (about 82 %) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/flowsheet-worked-example.md
- [x] The agent layer offers the unit as the tool cstr, and evaluation case 01 is this example (tau 500 s, Da 5, X 5/6) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/evals/cases/01-cstr-first-order.json
- [x] Out of scope: no energy balance (isothermal), one first-order reaction, constant density - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/blob/30f2b12f177b50a69b54a2dd7739b7ad5f169cf2/docs/units/cstr.md
