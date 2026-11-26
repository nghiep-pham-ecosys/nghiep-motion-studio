# Lesson 2026-11-26: technical interview, from the post "A format check next to the LLM judge"

### (a) Anki — 12 từ

| Từ / cụm | Nghĩa | Câu mẫu |
| --- | --- | --- |
| LLM judge | mô hình dùng để chấm | The LLM judge scores each post from 1 to 5. |
| rubric | bảng tiêu chí chấm | The ending was not in the rubric. |
| rule-based check | kiểm tra bằng luật | A rule-based check calls no model. |
| deterministic | cho cùng kết quả mỗi lần | Rules are deterministic; the judge is not. |
| false negative | bỏ sót lỗi | Missing the question was a false negative. |
| blind spot | điểm mù | The judge has a blind spot outside its rubric. |
| overstated claim | khẳng định quá mức | It flagged an overstated claim about speed. |
| stale | cũ, chưa cập nhật | The Short was stale after the fix. |
| informational | chỉ để tham khảo, không chặn | The check is informational for now. |
| approval gate | cổng duyệt | The approval gate does not read it yet. |
| trade-off | sự đánh đổi | The trade-off is cost against meaning. |
| catch (a bug) | bắt được (lỗi) | Each check catches a different kind of bug. |

### (b) Script thu âm (\~200 từ)

`/` = ngắt ngắn, `//` = ngắt dài, **in đậm** = nhấn.

> Every week / I publish five short posts, / and an **LLM judge** / scores each one / before I approve it. //
>
> In my first week, / the judge passed **all five**. // Its average scores / were **4.4 to 5**. //
>
> Then I added a small **format check** / in Python. // It calls no model. // It only **counts**: / words, / lines, / paragraph length, / and whether the last two paragraphs / contain a **question mark**. //
>
> It found something / the judge had missed. // **Five of five** posts / ended without an open question. // The judge was never asked about endings, / so it never looked. //
>
> The rule has a **limit** too. // A lazy line like / "**Agree?**" / passes it, / because the rule sees a question mark, / not whether the question is open. //
>
> And the judge caught things / my rules skip: / a **stale** video, / and a claim / that was only true / from ten gigabits per second **up**. //
>
> So I keep **both**. // If it can be **counted**, / I check it in code. // I ask the model / only about **meaning**.

**5 chỗ khó:** *judge* /dʒʌdʒ/ · *rubric* /ˈruː.brɪk/ · *deterministic* /dɪˌtɜː.mɪˈnɪs.tɪk/ · *paragraph* /ˈpær.ə.ɡrɑːf/ · *gigabits* /ˈɡɪɡ.ə.bɪts/. Âm cuối cần giữ: post**s**, add**ed** /ˈæd.ɪd/, count**s**, end**ed** /ˈen.dɪd/, look**ed** /lʊkt/.

**3 bước:**

1. Nghe/đọc theo cả bài 2 lần, ngắt đúng chỗ `/` `//`.
2. Gấp script, kể lại 60–90 giây bằng lời mình (phần quan trọng nhất).

### (c) Bài mẫu phỏng vấn kỹ thuật — học thuộc (\~90 giây)

Câu hỏi: *"How do you evaluate LLM outputs before they reach users?"* Khung trả lời kỹ thuật: **cách làm → ví dụ thật có số → đánh đổi → điều sẽ làm tiếp**.

> **Approach:** I use two layers. First, cheap deterministic checks in code: length, format, required fields, anything I can count. Second, an LLM judge with a written rubric for the things that need reading, like accuracy and tone.
>
> **Example:** In my content pipeline, the judge passed five out of five posts in one week. A one-line Python check then showed that all five ended without an open question. The judge missed it because endings were not in its rubric.
>
> **Trade-off:** Rules are free and give the same answer every run, but they only see the surface. A toy "Agree?" still passes my question rule. The judge reads meaning, but it only scores what the rubric names, and it costs a model call.
>
> **Next step:** Right now the format check is informational. Next, I will make it part of the approval gate, and I will add any repeated judge finding to the rubric or, if it can be counted, to the code.

Câu follow-up nên chuẩn bị: *"How do you know the judge itself is reliable?"* → gợi ý: so điểm judge với một bộ nhỏ do người chấm, đo mức đồng ý; cố định prompt và phiên bản model; xem lại các ca judge và luật bất đồng.
### (d) IELTS Speaking — bài mẫu band 7 (cùng chủ đề: kiểm tra chất lượng bằng máy và bằng người)

Band 7 cần: trả lời dài, tự nhiên, có ví dụ; dùng được vài từ ít phổ biến (*rubric, consistent, nuance*); câu phức (*because, which, although, if*); sai sót nhỏ không làm khó hiểu. Không cần giọng bản xứ, cần **rõ** và **liền mạch**.

**Part 1** (trả lời 2–3 câu)

> **Q: Do you use technology to check your work?**
> Yes, quite a lot. When I write code, I run automatic tests before I share anything, and when I write in English, I use a grammar checker. It saves time, although I still read everything once more myself.
>
> **Q: Do you prefer working alone or in a team?**
> It depends on the task. For deep work like debugging, I prefer working alone because I can focus. But for reviewing work, a team is better, since other people notice mistakes that I have stopped seeing.

**Part 2** — cue card (1 phút chuẩn bị, nói 1,5–2 phút)

*Describe a time when a simple tool found a problem that others had missed. You should say: what the tool was, what problem it found, why others missed it, and explain how you felt about it.*

> I'd like to talk about a small checking tool I added to my writing pipeline a few weeks ago.
>
> Every week I publish five short technical posts, and before I approve them, an AI model reads each one and gives it a score. In my first week, all five posts got high scores, so I assumed they were fine.
>
> Then I wrote a very simple script, just a few lines of Python, which counts things like words, line length and question marks. To my surprise, it showed that **none** of the five posts ended with a question for the reader. That matters on social media, because a question at the end invites people to reply.
>
> The reason the AI judge missed it is quite interesting. It was never asked to look at the ending. It only scored the things on its list, such as accuracy and tone, so it simply didn't notice.
>
> Honestly, I felt a bit embarrassed at first, because I had trusted the scores too much. But mostly I felt pleased, because I learned a useful lesson: if something can be counted, a simple rule is cheaper and more reliable than a clever model. Now I use both, and each one catches what the other misses.

**Part 3** (thảo luận, trả lời 4–5 câu)

> **Q: Will computers replace humans in checking the quality of work?**
> I don't think they will replace humans completely, at least not in the near future. Computers are excellent at checking things that are clear and measurable, like spelling, numbers or formats, and they never get tired. However, quality often depends on meaning and context, for example whether an argument is fair or whether a claim is exaggerated. In those cases, human judgement is still essential. So I'd say the most likely future is a partnership, where machines handle the routine checks and people focus on the subtle ones.
>
> **Q: Is it a problem if people trust automatic scores too much?**
> Yes, it can be. A score looks objective, so people tend to accept it without asking how it was produced. But every score is based on certain criteria, and anything outside those criteria is invisible. In my view, organisations should regularly compare automatic scores with human reviews, so they can see what the system misses.

**Từ/cụm "ăn điểm" band 7:** to my surprise · invites people to reply · it simply didn't notice · I had trusted the scores too much · clear and measurable · human judgement is still essential · a partnership · tend to accept it without asking · invisible.

### (e) IELTS Writing Task 2 — bài mẫu band 7 (~280 từ, 40 phút)

*Đề:* **Some people believe that automated systems will soon be able to judge the quality of work better than humans. To what extent do you agree or disagree?**

> In many workplaces, software is increasingly used to assess the quality of written texts, code and other products. Some people argue that such automated systems will soon judge quality better than humans. While I agree that machines are superior in certain respects, I believe that human judgement will remain necessary for the foreseeable future.
>
> On the one hand, automated systems have clear advantages. They are fast, inexpensive and, above all, consistent. A program that checks the length of a document or the format of a report will give exactly the same answer every time, whereas human reviewers may be tired, distracted or biased. For example, a simple script can check thousands of articles for missing information in a few seconds, a task that would take a person several days. In such measurable areas, machines already outperform people.
>
> On the other hand, quality is not only a matter of measurable features. Whether an argument is convincing, whether a claim is exaggerated or whether a tone suits its audience depends on meaning and context. Even advanced AI models only evaluate what they have been instructed to look for, so anything outside their criteria may go unnoticed. Moreover, if people rely on automatic scores without questioning them, serious errors can pass through unchecked.
>
> In conclusion, although automated systems are more efficient and consistent than humans at checking measurable aspects of work, I do not believe they will soon replace human judgement entirely. The most effective approach is to combine the two, letting machines handle routine checks while people focus on meaning and nuance.

**Vì sao band 7 (để học theo):**

- **Task Response:** nêu rõ quan điểm ngay mở bài ("While I agree… I believe…") và giữ đến kết bài; mỗi đoạn thân có ý chính + ví dụ.
- **Coherence:** nối ý bằng *On the one hand / On the other hand / Moreover / In conclusion*; mỗi đoạn một ý.
- **Lexical:** *superior in certain respects, consistent, outperform, go unnoticed, nuance, for the foreseeable future*.
- **Grammar:** câu phức với *whereas, although, if, so*; mệnh đề quan hệ rút gọn (*a task that would take…*).
- Muốn lên band 8: ví dụ cụ thể hơn và phản biện sâu hơn (ví dụ: con người có thể dùng máy để giảm thiên vị của chính mình).

