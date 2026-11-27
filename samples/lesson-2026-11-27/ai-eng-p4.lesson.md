# Lesson 2026-11-27: technical interview, from the post "Put the title back in the chunk"

### (a) Anki — 12 từ

| Từ / cụm | Nghĩa | Câu mẫu |
| --- | --- | --- |
| retrieval | truy xuất (tìm đoạn liên quan) | Retrieval decides what the model can read. |
| chunk | đoạn cắt từ tài liệu | The page was cut into 28 chunks. |
| rank | thứ hạng | The right chunk ranked 14th. |
| keyword match | khớp từ khoá | BM25 is a keyword match, not a meaning match. |
| embedding | vector biểu diễn nghĩa | Embeddings compare meaning, not exact words. |
| vector database | cơ sở dữ liệu vector | I did not need a vector database for this test. |
| top-k | k kết quả đầu | A top-3 cut would still miss it. |
| toy corpus | bộ tài liệu mẫu tự tạo | It is a toy corpus of seven made-up pages. |
| to cut off | cắt mất | Chunking cut the title off. |
| at no cost | không tốn chi phí | The fix works at no cost. |
| benchmark | phép đo chuẩn | One question is a demo, not a benchmark. |
| to close the gap | thu hẹp khoảng cách | Embeddings might close the gap. |

### (b) Script thu âm (\~200 từ)

`/` = ngắt ngắn, `//` = ngắt dài, **in đậm** = nhấn.

> I'm writing a course module / about **retrieval** / for RAG. // Retrieval is the step / that finds the right pieces of text / before the model answers. //
>
> I used **BM25**, / a classic keyword method, / on a **toy** handbook. // It has seven made-up pages, / cut into **twenty-eight chunks**. //
>
> My test question asked / how long a pH meter can go / before it needs **calibrating** again. // The answer was in the handbook, / but it ranked **fourteenth** / out of twenty-eight. //
>
> Why? // The right chunk said **when** to repeat the step, / but it never **named** the meter. // The page **title** did, / and chunking **cut it off**. //
>
> The fix costs **nothing**. // Before indexing, / I copy the title and the section name / into **every** chunk. // Same question, / same BM25: / the right chunk moved / to rank **four**. //
>
> Four is still not one. // BM25 matches **words**, / not **meaning**. // So "how long" and "frequency" / look different to it. //
>
> My lesson? // Before you add a **vector database**, / measure the rank / of the **right** chunk.

**5 chỗ khó:** *retrieval* /rɪˈtriː.vəl/ · *calibrating* /ˈkæl.ɪ.breɪ.tɪŋ/ · *chunk* /tʃʌŋk/ · *frequency* /ˈfriː.kwən.si/ · *database* /ˈdeɪ.tə.beɪs/. Âm cuối cần giữ: chunk**s**, rank**ed** /ræŋkt/, page**s** /ˈpeɪ.dʒɪz/, match**es** /ˈmætʃ.ɪz/, cut **it** off (nối âm: /kʌ.tɪ.tɒf/).

**2 bước:**

1. Nghe/đọc theo cả bài 2 lần, ngắt đúng chỗ `/` `//`.
2. Gấp script, kể lại 60–90 giây bằng lời mình (phần quan trọng nhất).

### (c) Bài mẫu phỏng vấn kỹ thuật — học thuộc (\~90 giây)

Câu hỏi: *"How would you debug a RAG system that gives wrong answers?"* Khung trả lời kỹ thuật: **cách làm → ví dụ thật có số → đánh đổi → điều sẽ làm tiếp**.

> **Approach:** I start by separating retrieval from generation. For a few failing questions, I check where the right chunk ranks. If it isn't in the top results, the model never saw it. Then a better prompt won't help.
>
> **Example:** In one of my own tests, I used BM25 on a toy handbook of seven made-up pages. It ranked the right chunk fourteenth out of twenty-eight. The chunk never named the instrument. The page title did, and chunking had cut it off. I copied the title and section name into every chunk. That moved it to fourth, at no cost.
>
> **Trade-off:** That fix is free and needs no vector database. But BM25 still matches words, not meaning. "How long" and "frequency" look different to it. So a top-3 cut would still miss the chunk. Embeddings might close the gap, but they add a model to run.
>
> **Next step:** I'd keep those failing questions as a small regression set. I'd measure the rank of the right chunk before and after every change. Only then would I try embeddings, so I know what they actually add.

Câu follow-up nên chuẩn bị: *"How do you know a retrieval fix works beyond one question?"* → gợi ý: dựng một bộ câu hỏi nhỏ có sẵn chunk đúng, đo thứ hạng của chunk đúng (hoặc tỉ lệ nằm trong top-k) trước và sau khi sửa; một câu hỏi trên bộ tài liệu mẫu chỉ là demo, không phải benchmark.
### (d) IELTS Speaking — bài mẫu band 7 (cùng chủ đề: tìm kiếm thông tin)

Band 7 cần: trả lời dài, tự nhiên, có ví dụ; dùng được vài từ ít phổ biến (*outdated, in practice, arguably*); câu phức (*because, which, although, if*); sai sót nhỏ không làm khó hiểu. Không cần giọng bản xứ, cần **rõ** và **liền mạch**.

**Part 1** (trả lời 2–3 câu)

> **Q: How do you usually search for information?**
> Mostly online, with a search engine. For technical topics, I often go straight to the official documentation. Search results can be outdated, you see. If it's something important, I check at least two sources.
>
> **Q: Did you use libraries a lot when you were a student?**
> Yes, quite often, especially at university. Many of the books we needed were too expensive to buy. So the library was the only option. I actually miss how quiet it was.

**Part 2** — cue card (1 phút chuẩn bị, nói 1,5–2 phút)

*Describe a time when you could not find a piece of information you needed. You should say: what you were looking for, where you looked, why it was difficult to find, and explain how you finally solved the problem.*

> I'd like to describe a problem I had while building a search feature for a course.
>
> I was testing a question about a laboratory instrument, a pH meter. I wanted to know how often it needs to be recalibrated. I knew the answer was in a handbook I had prepared. So I expected my search tool to find it immediately.
>
> To my surprise, the correct passage appeared only fourteenth in the list. That meant that, in practice, nobody would ever read it.
>
> It was difficult to find because of the way the document had been divided. The passage never mentioned the instrument by name. The name only appeared in the page title. When the pages were cut into pieces, the title was separated from the text.
>
> In the end, the solution was surprisingly simple. I added the page title and the section name to every piece of text. Then the right passage jumped to fourth place. It taught me that missing information is often there. It is just not connected to the words people actually search for.

**Part 3** (thảo luận, trả lời 4–5 câu)

> **Q: Has the internet made people better at finding information?**
> In some ways, yes. People can find facts in seconds that used to take hours in a library. However, I'd argue it has made us worse at judging information. Search results all look equally convincing. As a result, many people accept the first answer without checking its source. So we're faster, but not necessarily more accurate.
>
> **Q: Should schools teach students how to search?**
> Definitely. Searching well is a skill, not something people are born with. Students should learn to choose keywords and compare sources. They should also recognise when a page is trying to sell something. These skills will matter even more as AI tools start answering our questions. After all, a confident answer is not always a correct one.

**Từ/cụm "ăn điểm" band 7:** go straight to · outdated · to my surprise · in practice · was separated from · surprisingly simple · I'd argue · equally convincing · not necessarily · recognise when.

### (e) IELTS Writing Task 2 — bài mẫu band 7 (~260 từ, 40 phút)

*Đề:* **Some people think that the ability to find information quickly is more important than the ability to remember it. To what extent do you agree or disagree?**

> In the age of search engines and AI assistants, almost any fact can be found within seconds. As a result, some people believe that knowing how to find information has become more valuable than remembering it. I largely agree with this view, although I believe that a certain amount of memorised knowledge remains essential.
>
> There are strong reasons to value the skill of finding information. Knowledge in many fields changes rapidly, so facts that are memorised today may be outdated tomorrow. A professional who can locate, compare and evaluate up-to-date sources is therefore more reliable than one who depends only on memory. Moreover, the amount of available information is now far too large for anyone to remember, which makes efficient searching a practical necessity rather than a luxury.
>
> Nevertheless, memory still plays an important role. To search effectively, people need enough background knowledge to choose the right keywords and to recognise whether an answer makes sense. For instance, an engineer who has never learned basic chemistry may not notice that a search result contains an impossible value. Without this foundation, a fast search can simply produce a fast mistake. In addition, some situations, such as emergencies or job interviews, simply do not allow time to look things up.
>
> In conclusion, while the ability to find information quickly is arguably the more important skill today, it cannot fully replace memory. The most capable people combine a solid base of remembered knowledge with the ability to search, check and update it efficiently. Schools should therefore train both abilities, not choose between them.

**Vì sao band 7 (để học theo):**

- **Task Response:** quan điểm rõ ngay mở bài ("I largely agree… although…"), giữ đến kết bài; mỗi đoạn thân có ý chính + ví dụ.
- **Coherence:** nối ý bằng *There are strong reasons / Moreover / Nevertheless / For instance / In addition / In conclusion*; mỗi đoạn một ý.
- **Lexical:** *outdated, locate, evaluate, a practical necessity rather than a luxury, recognise, arguably*.
- **Grammar:** mệnh đề quan hệ (*which makes…*), bị động (*facts that are memorised*), câu điều kiện ẩn (*may not notice that…*).
- Muốn lên band 8: một ví dụ cụ thể hơn ở đoạn 2 và một câu phản biện cho quan điểm đối lập.
