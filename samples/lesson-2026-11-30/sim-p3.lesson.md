# Lesson 2026-11-30: weekly standup, from the post "2000 small tanks become a pipe"

### (a) Anki — 12 từ

| Từ / cụm | Nghĩa | Câu mẫu |
| --- | --- | --- |
| stirred tank | bể khuấy | One stirred tank converts 67% of the feed. |
| in series | nối tiếp | I chained 2000 tanks in series. |
| plug-flow reactor | thiết bị phản ứng dòng đẩy (dạng ống) | A plug-flow reactor behaves like a pipe. |
| conversion | độ chuyển hoá | The conversion was 0.86453. |
| closed form | nghiệm giải tích (công thức đóng) | Its closed form is one minus e to the minus k tau. |
| to converge | hội tụ | The chain converges to the pipe as N grows. |
| leading term | số hạng chính (bậc thấp nhất) | The gap formula is only the leading term. |
| tolerance | dung sai | The test allows a tolerance of two times ten to the minus four. |
| to predict | dự đoán trước | The formula predicted the gap before the run. |
| loose | lỏng (không chặt) | A loose test would pass with 27 tanks. |
| strict | chặt | Knowing the error lets the test be strict. |
| out of habit | theo thói quen | Don't pick one percent out of habit. |

### (b) Script thu âm (\~200 từ)

`/` = ngắt ngắn, `//` = ngắt dài, **in đậm** = nhấn.

> In my small process simulator, / I have two reactor models. // One is a **stirred tank**. // The other is a **plug-flow reactor**, / which behaves like a pipe. //
>
> Theory says / that many small tanks in a row / should behave like **one pipe**. // So one test chains **two thousand** tanks / and compares the result / with the pipe model. // Two models, / written separately, / check **each other**. //
>
> The hard part / is the **tolerance**. // How close is close enough? //
>
> A short formula / predicts the gap / **before** the test runs. // Take two thousand tanks. // The gap is about **one point three five** times ten to the minus four. // The run gives / about the same number. //
>
> So the test allows **two** times ten to the minus four. // A chain needs at least / **thirteen fifty-four** tanks / to pass. //
>
> Suppose I had picked a round **one percent**, / out of habit. // Then the test would pass / with only **twenty-seven** tanks. // It could no longer tell / two thousand tanks / from twenty-seven. //
>
> Here is my lesson. // When a test compares a model / with a **limit**, / work out how fast it **converges**. // Then set the tolerance / from that.

**5 chỗ khó:** *tolerance* /ˈtɒl.ər.əns/ · *converges* /kənˈvɜː.dʒɪz/ · *separately* /ˈsep.ər.ət.li/ · *reactor* /riˈæk.tər/ · *series* /ˈsɪə.riːz/. Âm cuối cần giữ: tank**s**, chain**s** /tʃeɪnz/, compare**s**, pick**ed** /pɪkt/, predict**s** /prɪˈdɪkts/.

**Đọc số:** 1.35e-4 = *one point three five times ten to the minus four* · 2e-4 = *two times ten to the minus four* · 1354 = *thirteen fifty-four* (hoặc *one thousand three hundred and fifty-four*) · 0.86453 = *zero point eight six four five three*.

**2 bước:**

1. Nghe/đọc theo cả bài 2 lần, ngắt đúng chỗ `/` `//`.
2. Gấp script, kể lại 60–90 giây bằng lời mình (phần quan trọng nhất).

### (c) Standup đầu tuần — mẫu học thuộc (\~90 giây)

Câu hỏi: *"It's Monday. Can you give us your update for the week?"* Khung 3 phần: **last week → this week → blockers**. Mỗi phần 2–3 câu, có một con số.

> **Last week:** I shipped daily comment tools for my blog's channels. I also shipped a monthly report on post performance. And I built a video pipeline. It turns each post into a ten-to-fifteen-minute explainer.
>
> **This week:** three videos a day now go out on a schedule. I'll also collect the first full month of post metrics. They go into the report on December first.
>
> **Blockers:** one. The video platform limits uploads per day, so a few videos go up a day late. It doesn't block anything else, so I just retry the next day.

Câu follow-up nên chuẩn bị: *"How do you know a video is ready to go out?"* → gợi ý: mỗi video qua một bước kiểm tra tự động (độ dài, âm lượng, phụ đề khớp lời nói); mọi con số trên màn hình được người kiểm lại trước khi lên lịch.

### (d) IELTS Speaking — bài mẫu band 7 (chủ đề: sự chính xác và kiểm tra)

Band 7 cần: trả lời dài, tự nhiên, có ví dụ; dùng được vài từ ít phổ biến (*instinct, cruder, sensible*); câu phức (*because, when, which, rather than*); sai sót nhỏ không làm khó hiểu. Không cần giọng bản xứ, cần **rõ** và **liền mạch**.

**Part 1** (trả lời 2–3 câu)

> **Q: Do you usually check your work carefully?**
> Yes, I'd say I'm quite careful, probably because I was trained as a chemist. In a lab, one small mistake in a measurement can waste a whole week. So checking became a habit. These days I check most things twice, especially numbers.
>
> **Q: Is it important to be on time?**
> I think so. Being late sends a message that you don't value other people's time. That said, I'm a bit more relaxed about it with friends than at work.

**Part 2** — cue card (1 phút chuẩn bị, nói 1,5–2 phút)

*Describe a time when you had to decide how accurate something needed to be. You should say: what you were working on, what you had to decide, how you made the decision, and explain whether it was the right choice.*

> I'd like to talk about a small decision I made while testing a computer model. The model, which I'm building myself, simulates chemical reactors.
>
> I had two separate models that should give almost the same answer in one case. I wanted a test that compares them automatically. The question was: how close is close enough?
>
> My first instinct was a round number, maybe one percent, out of habit. But before doing that, I worked out how big the difference should be in theory. It turned out that a short formula predicts it. And the predicted gap was tiny, far smaller than one percent.
>
> So I set the limit just above that predicted value. When I ran the test, the real difference matched the prediction almost exactly.
>
> I'm convinced it was the right choice. The loose limit would have let a much cruder model pass without anyone noticing. A test that can't fail isn't really a test.

**Part 3** (thảo luận, trả lời 4–5 câu)

> **Q: Why do people sometimes accept results that are "good enough"?**
> Mainly because of time pressure. Checking something properly takes effort. When a result looks reasonable, it's tempting to move on. Sometimes that's perfectly sensible. But small errors can add up, particularly in engineering or medicine.
>
> **Q: Will computers make people less careful?**
> To some extent, yes. When a machine shows many decimal places, people tend to trust it automatically. I'd argue we need to teach one habit. Ask where a number comes from, rather than how precise it looks.

**Từ/cụm "ăn điểm" band 7:** my first instinct was · out of habit · it turned out · far smaller than · I'm convinced · a much cruder model · tempting to move on · perfectly sensible · add up · rather than.

### (e) IELTS Writing Task 2 — bài mẫu band 7 (~260 từ, 40 phút)

*Đề:* **Some people believe that it is better to finish work quickly, even if the quality is lower, while others think quality should always come first. Discuss both views and give your opinion.**

> In many workplaces, employees face a constant trade-off between speed and quality. Some argue that delivering work quickly matters most, even at the cost of quality, while others insist that quality should never be compromised. In my view, the right balance depends on how costly a mistake would be.
>
> Those who favour speed have a valid point. In fast-moving industries, a product that arrives late may be useless, however polished it is. Releasing an early version also allows a team to collect feedback and improve it, which is often more efficient than trying to perfect it in isolation. For example, a team building a mobile app can release a basic version within weeks and fix minor problems after users report them.
>
> On the other hand, there are situations where low quality is simply unacceptable. A bridge, a medical device or a financial system that fails can cause serious harm. The time saved at the start is small compared with the cost of fixing the damage later. Moreover, a reputation for careless work is hard to repair.
>
> Personally, I believe the decision should be based on the consequences of failure. Where errors are cheap and easy to correct, speed should win. A typo in an internal draft, for instance, costs almost nothing to fix. Where they are expensive or dangerous, quality must come first. The key skill is to know in advance which situation you are in.
>
> In conclusion, neither speed nor quality is always more important. Sensible professionals judge the risk of each task and adjust their standards accordingly.

**Vì sao band 7 (để học theo):**

- **Task Response:** quan điểm rõ ngay mở bài ("the right balance depends on how costly a mistake would be"), giữ đến kết bài.
- **Coherence:** mỗi đoạn một ý (speed / quality / ý kiến); nối ý bằng *On the other hand / Moreover / Personally / In conclusion*.
- **Lexical:** *trade-off, at the cost of, compromised, in isolation, accordingly*.
- **Grammar:** câu phức (*however polished it is*, *Where errors are cheap…*), mệnh đề quan hệ (*which is often more efficient…*).
- Ví dụ cụ thể ở đoạn 2 (ứng dụng điện thoại) và đoạn 4 (bản nháp nội bộ); muốn lên band 8: thêm một câu phản biện cho quan điểm đối lập.
