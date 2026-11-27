# Fact check: English for engineers: 2000 small tanks become a pipe

Reviewed-by:
Date: 2026-11-30
Narration: c9f33f196e21

DRAFT for Nghiep's review: nothing below is ticked yet, so the gate's fact-check fails until a person checks each
claim, ticks it and writes their name after `Reviewed-by:`. What the video states as fact: the meaning of each word
(the Vietnamese gloss on screen), the IELTS test format it uses and the story's numbers from the author's own post.
The narration hash is the one `motion-studio lesson` builds from `sim-p3.lesson.md`; a changed lesson or builder
makes it stale (the gate prints the new one).

- [ ] stirred tank: a tank reactor whose contents are mixed so they are uniform, a CSTR ("bể khuấy") - source: https://en.wikipedia.org/wiki/Continuous_stirred-tank_reactor
- [ ] in series: connected one after another ("nối tiếp") - source: https://dictionary.cambridge.org/dictionary/english/series
- [ ] plug-flow reactor: a tubular reactor where the fluid moves as plugs with no mixing along the tube ("thiết bị phản ứng dòng đẩy (dạng ống)") - source: https://en.wikipedia.org/wiki/Plug_flow_reactor_model
- [ ] conversion: the fraction of a reactant that has reacted ("độ chuyển hoá") - source: https://en.wikipedia.org/wiki/Chemical_reactor
- [ ] closed form: an exact formula made of a finite number of standard operations ("nghiệm giải tích (công thức đóng)") - source: https://en.wikipedia.org/wiki/Closed-form_expression
- [ ] to converge: to come closer and closer to a limit ("hội tụ") - source: https://dictionary.cambridge.org/dictionary/english/converge
- [ ] leading term: the term of a series that matters most, here the lowest-order one ("số hạng chính (bậc thấp nhất)") - source: https://en.wikipedia.org/wiki/Asymptotic_expansion
- [ ] tolerance: the amount by which a value may differ and still be accepted ("dung sai") - source: https://dictionary.cambridge.org/dictionary/english/tolerance
- [ ] to predict: to say what will happen before it happens ("dự đoán trước") - source: https://dictionary.cambridge.org/dictionary/english/predict
- [ ] loose: not strict or exact ("lỏng (không chặt)") - source: https://dictionary.cambridge.org/dictionary/english/loose
- [ ] strict: exact, allowing little difference ("chặt") - source: https://dictionary.cambridge.org/dictionary/english/strict
- [ ] out of habit: because you always do it, not because you chose it ("theo thói quen") - source: https://dictionary.cambridge.org/dictionary/english/habit
- [ ] Many equal stirred tanks in series tend to one plug-flow reactor: X_N = 1 - (1 + k tau / N)^-N tends to 1 - exp(-k tau) - source: https://github.com/nghiep-pham-ecosys/process-sim-lab/pull/6 (pfr.py docstring and tests/test_pfr.py)
- [ ] IELTS Speaking Part 2: one minute to prepare with a cue card, then up to two minutes of speaking - source: https://ielts.org/take-a-test/test-types/ielts-academic-test/ielts-academic-format-speaking
- [ ] IELTS Writing Task 2: an essay of at least 250 words in about 40 minutes (the sample has 259 words) - source: https://ielts.org/take-a-test/test-types/ielts-academic-test/ielts-academic-format-writing
- [ ] The four writing criteria named on screen (task response, coherence and cohesion, lexical resource, grammatical range and accuracy) - source: https://ielts.org/take-a-test/test-types/ielts-academic-test/ielts-academic-format-writing
- [ ] The story's numbers (one tank converts 67% of the feed, 2000 tanks give a conversion of 0.86453, a predicted and measured gap of about 1.35e-4, a tolerance of 2e-4 that needs at least 1354 tanks, and a 1% tolerance that passes with 27 tanks) are the author's own results - source: the post "2000 small tanks become a pipe" and its script tanks_in_series.py
