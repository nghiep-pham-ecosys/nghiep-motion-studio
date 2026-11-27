# Fact check: Lab to Prod: 2000 small tanks become a pipe, and the test tolerance

Reviewed-by:
Date: 2026-11-30
Narration: 577055325863

Source: the lab-to-prod post 2026-11-30-sim-p3 (https://nghiep-pham-ecosys.github.io/lab-to-prod/notes/2026-11-30-sim-p3/) and its merged work.
Check each claim against its source, then tick it. The builder found every number below in the file and
line it names; check that the sentence uses it in the same sense.

DRAFT for Nghiep's review (written by the lane on 2026-11-27): nothing is ticked, so the gate's fact check fails
until a person checks each claim, ticks it and writes their name after `Reviewed-by:`. The post folder is the
lab-to-prod copy at e96530b; test_pfr.py is tests/test_pfr.py of process-sim-lab at the merge commit of PR #6
(6c8aa2e). `python tanks_in_series.py` was run on 2026-11-27 and its output equals tanks_in_series.expected.
The lines marked "by hand" hold numbers the voice says in words (the number guard does not see them) or a number
the guard matched to a line with another meaning.


- [ ] The work behind the post: psl-pfr-mixer-splitter in process-sim-lab, merged 2026-11-17: An isothermal first-order PFR (X = 1 - exp(-k tau)), a mixer and a splitter, with 17 tests on hand-derived values; 2000 CSTRs in series come within 2e-4 of the PFR. - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/pull/6
- [ ] Code card: test_pfr.py lines 43-55, shown unchanged - source: test_pfr.py:43
- [ ] Code card: test_pfr.py lines 56-67, shown unchanged - source: test_pfr.py:56
- [ ] Code card: tanks_in_series.py lines 14-25, shown unchanged - source: tanks_in_series.py:14
- [ ] Output card: tanks_in_series.expected lines 1-8 equal a fresh run of its script - source: tanks_in_series.expected:1
- [ ] Output card: tanks_in_series.expected lines 1-10 equal a fresh run of its script - source: tanks_in_series.expected:1
- [ ] "The post is called: 2000 small tanks become a pipe." (intro-6) - source: 2000 at post.md:6
- [ ] "It is part 3 of the Simulation notes." (intro-7) - source: 3 at post.md:87
- [ ] "The card has 4 rows: 1 tank, 10 tanks, 2000 tanks, and one pipe." (intro-8) - source: 4 at post.md:12, 1 at post.md:12, 10 at post.md:12, 2000 at post.md:12
- [ ] "From a pull request merged on the 17th of November." (two-reactors-20) - source: 17 at work.yaml (psl-pfr-mixer-splitter):5
- [ ] "It added a PFR, a mixer and a splitter, with 17 tests." (two-reactors-21) - source: 17 at work.yaml (psl-pfr-mixer-splitter):5
- [ ] "Tank 1" (many-tanks-become-a-pipe panel) - source: 1 at post.md:12
- [ ] "Tank 2" (many-tanks-become-a-pipe panel) - source: 2 at post.md:12
- [ ] "N tanks in series: X = 1 - (1 + k tau / N)^-N" (many-tanks-become-a-pipe card) - source: 1 at post.md:12
- [ ] "One pipe: X = 1 - exp(-k tau)" (many-tanks-become-a-pipe card) - source: 1 at post.md:31
- [ ] "For N tanks: X is 1 minus, 1 plus k tau over N, to the power minus N." (many-tanks-become-a-pipe-13) - source: 1 at post.md:37
- [ ] "For the pipe: X is 1 minus e to the minus k tau." (many-tanks-become-a-pipe-14) - source: 1 at post.md:37
- [ ] "k is 0.02 per second." (the-test-4) - source: 0.02 at tanks_in_series.py:4
- [ ] "The volume is 1 cubic metre, and the flow is 0.01 cubic metres per second." (the-test-5) - source: 1 at post.md:12, 0.01 at test_pfr.py:16
- [ ] "So tau is 100 seconds." (the-test-6) - source: 100 at post.md:37
- [ ] "And k times tau is 2. The post writes it as k tau equals 2." (the-test-7) - source: 2 at post.md:12
- [ ] "The pipe gives 0.86466." (the-test-10) - source: 0.86466 at post.md:12
- [ ] "So one big tank converts 67 percent, and the pipe about 86.5 percent." (the-test-11) - source: 67 at post.md:63, 86.5 at post.md:63
- [ ] "The chain: 2000 calls to the CSTR model" (the-test panel) - source: 2000 at post.md:35
- [ ] "N is 2000." (the-test-15) - source: 2000 at post.md:5
- [ ] "The loop calls the stirred-tank model 2000 times." (the-test-16) - source: 2000 at post.md:12
- [ ] "Each call gets one 2000th of the volume." (the-test-17) - source: 2000 at post.md:35
- [ ] "First, the chain must match its own closed form, to 10 decimal places." (the-test-24) - source: 10 at post.md:12
- [ ] "Third, the chain must come within 2 times 10 to the minus 4 of the pipe." (the-test-26) - source: 2 at post.md:39, 10 at post.md:12, 4 at work.yaml (psl-pfr-mixer-splitter):5
- [ ] "Gap to the pipe: about (k tau)^2 exp(-k tau) / (2N)" (how-close-is-close-enoug card) - source: 2 at post.md:37
- [ ] "At k tau = 2 and N = 2000: about 1.35e-4" (how-close-is-close-enoug card) - source: 2 at post.md:37, 2000 at post.md:37, 1 at post.md:33, 4 at post.md:37
- [ ] "It shrinks as 1 over N" (how-close-is-close-enoug card) - source: 1 at post.md:87
- [ ] "It shrinks as 1 over N." (how-close-is-close-enoug-9) - source: 1 at post.md:87
- [ ] "It says about one point four times ten to the minus four, for 2000 tanks." (how-close-is-close-enoug-11) - source: 2000 at post.md:29
- [ ] "tanks_in_series.py: chains of 1 to 2000 tanks" (how-close-is-close-enoug panel) - source: 1 at post.md:12, 2000 at post.md:12
- [ ] "It runs the chain for 1, 2, 5, 10, 100 and 2000 tanks." (how-close-is-close-enoug-15) - source: 1 at post.md:33, 2 at post.md:77, 5 at tanks_in_series.py:19, 10 at post.md:12, 100 at post.md:77, 2000 at post.md:77
- [ ] "1 tank first." (how-close-is-close-enoug-19) - source: 1 at post.md:12
- [ ] "X is 0.666667, two thirds." (how-close-is-close-enoug-20) - source: 0.666667 at tanks_in_series.expected:3
- [ ] "So for 1 tank, the formula is off." (how-close-is-close-enoug-25) - source: 1 at post.md:12
- [ ] "At 10 tanks, X is 0.838494." (how-close-is-close-enoug-30) - source: 10 at post.md:12, 0.838494 at tanks_in_series.expected:6
- [ ] "At 100 tanks, the gap is 0.0027, as the formula says." (how-close-is-close-enoug-32) - source: 100 at post.md:37, 0.0027 at post.md:37
- [ ] "And at 2000 tanks, X is 0.864529." (how-close-is-close-enoug-33) - source: 2000 at post.md:86, 0.864529 at tanks_in_series.expected:8
- [ ] "And the formula gives the same, to 3 digits." (how-close-is-close-enoug-35) - source: 3 at post.md:87
- [ ] "Conversion X at k tau = 2" (how-close-is-close-enoug panel) - source: 2 at post.md:12
- [ ] "1 tank: 0.667 ()" (how-close-is-close-enoug panel) - source: 1 at post.md:12, 0.667 at post.md:12
- [ ] "10 tanks: 0.838 ()" (how-close-is-close-enoug panel) - source: 10 at post.md:12, 0.838 at post.md:12
- [ ] "2000 tanks: 0.86453 ()" (how-close-is-close-enoug panel) - source: 2000 at post.md:5, 0.86453 at post.md:12
- [ ] "PFR: 0.86466 ()" (how-close-is-close-enoug panel) - source: 0.86466 at post.md:23
- [ ] "Conversion X at k tau = 2: full bar 1" (how-close-is-close-enoug panel) - source: 2 at post.md:12, 1 at post.md:12
- [ ] "Most of the gain comes from the first 10 tanks." (how-close-is-close-enoug-38) - source: 10 at post.md:12
- [ ] "And 2000 tanks sit just below the pipe." (how-close-is-close-enoug-40) - source: 2000 at post.md:6
- [ ] "The test allows 2 times 10 to the minus 4." (setting-the-tolerance-2) - source: 2 at post.md:39, 10 at post.md:12, 4 at post.md:39
- [ ] "That is just above the predicted gap for 2000 tanks." (setting-the-tolerance-3) - source: 2000 at post.md:29
- [ ] "With 2 times 10 to the minus 4, a chain needs at least 1354 tanks to pass." (setting-the-tolerance-7) - source: 2 at post.md:39, 10 at post.md:12, 4 at post.md:39, 1354 at post.md:39
- [ ] "And with a round tolerance, like 0.01?" (setting-the-tolerance-8) - source: 0.01 at tanks_in_series.py:4
- [ ] "Any chain of 27 tanks or more passes." (setting-the-tolerance-9) - source: 27 at tanks_in_series.expected:9
- [ ] "The test itself chains 2000 tanks" (setting-the-tolerance panel) - source: 2000 at post.md:29
- [ ] "Tolerance 0.01: 27 (27 tanks)" (setting-the-tolerance panel) - source: 0.01 at tanks_in_series.py:4, 27 at tanks_in_series.expected:9
- [ ] "Tolerance 2e-4: 1354 (1354 tanks)" (setting-the-tolerance panel) - source: 4 at post.md:37, 1354 at tanks_in_series.expected:10
- [ ] "The shortest chain that passes: full bar 2000" (setting-the-tolerance panel) - source: 2000 at tanks_in_series.py:28
- [ ] "So with 0.01, that check cannot tell 2000 tanks from 27." (setting-the-tolerance-10) - source: 0.01 at test_pfr.py:35, 2000 at tanks_in_series.py:28, 27 at post.md:39
- [ ] "And with 2 times 10 to the minus 4, it can." (setting-the-tolerance-13) - source: 2 at post.md:57, 10 at post.md:86, 4 at work.yaml (psl-pfr-mixer-splitter):5
- [ ] "We saw that it is off for 1 tank." (the-limits-8) - source: 1 at post.md:12
- [ ] "Here it works because N is 2000." (the-limits-11) - source: 2000 at post.md:5
- [ ] "One test chains 2000 tank models and compares with the pipe" (recap card) - source: 2000 at post.md:35
- [ ] "The error term predicts the gap: about 1.35e-4" (recap card) - source: 1 at post.md:29, 4 at post.md:29
- [ ] "Tolerance 2e-4 needs 1354 tanks; 0.01 passes 27" (recap card) - source: 4 at tanks_in_series.expected:10, 1354 at tanks_in_series.expected:10, 0.01 at test_pfr.py:35, 27 at tanks_in_series.expected:9
- [ ] "So one test chains 2000 calls to the tank model, and compares with the pipe." (recap-3) - source: 2000 at post.md:35
- [ ] "For 2000 tanks, about one point three five times ten to the minus four." (recap-6) - source: 2000 at post.md:29
- [ ] "So the tolerance is 2 times 10 to the minus 4. A chain needs 1354 tanks." (recap-7) - source: 2 at post.md:39, 10 at post.md:12, 4 at post.md:39, 1354 at post.md:39
- [ ] "A round 0.01 would pass with 27." (recap-8) - source: 0.01 at test_pfr.py:35, 27 at post.md:57
- [ ] By hand: "It says about one point four times ten to the minus four, for 2000 tanks." The test comment says "= 1.4e-4 for N = 2000" - source: test_pfr.py:57
- [ ] By hand: "The real gap to the pipe is one point nine eight times ten to the minus one." (1.98e-01 for N = 1) - source: tanks_in_series.expected:3
- [ ] By hand: "It says two point seven one times ten to the minus one. Too big." (the leading term 2.71e-01 for N = 1) - source: tanks_in_series.expected:3
- [ ] By hand: "The gap is one point three five times ten to the minus four." and "about one point three five times ten to the minus four" (1.35e-04 for N = 2000; the post says "about 1.35e-4") - source: tanks_in_series.expected:8, post.md:29
- [ ] By hand: "The stirred tank gives two thirds." (CSTR X = 2/3 at Da = 2) - source: test_pfr.py:51, tanks_in_series.expected:3
- [ ] By hand: "to 10 decimal places" is the check assertAlmostEqual(..., places=10), not the post's "10 tanks" the guard matched - source: test_pfr.py:65
- [ ] By hand: the tolerance "0.01" is the script's 1e-02 and the post's "round 1e-2", not the flow Q = 0.01 the guard matched - source: tanks_in_series.expected:9, post.md:39
- [ ] By hand: "2 times 10 to the minus 4" is the test's delta=2e-4 - source: test_pfr.py:67, post.md:39
- [ ] By hand: "And the formula gives the same, to 3 digits." (1.35e-04 and 1.35e-04 at N = 2000) - source: tanks_in_series.expected:8
- [ ] By hand: "The post links the lecture slides it relies on." (two UCSB reactor-engineering slide decks in the post's sources) - source: post.md:9-10
