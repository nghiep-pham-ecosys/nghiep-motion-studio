<!-- Talk file of the explainer for the lab-to-prod post 2026-11-27-ai-eng-p4 (the copy next to this file). -->
<!-- Build: just explainer samples/explainer-2026-11-27/2026-11-27-ai-eng-p4 --talk samples/explainer-2026-11-27/ai-eng-p4.talk.md -->
Title: Lab to Prod: BM25 that runs locally, and the title it lost

## Intro {classroom}
Lan: Hello, and welcome to Lab to Prod. I'm Lan.
Minh: And I'm Minh. Today we open one post and the work behind it.
Lan: Both of our voices are AI voices, and the pictures are made with AI.
[image image.png | The post of the day]
Minh: The post is called: RAG without a vector database.
Minh: BM25 that runs locally.
Lan: It is part 4 of the AI engineering notes.
Lan: And the card says it in one line: put the title back in the chunk.
[points Today | The problem: a search that missed | How BM25 scores a chunk | The fix: two lines of code | The number, and the toy script | The limit]
Minh: So what is on the menu?
Lan: First the problem, then how BM25 scores a chunk.
Minh: Then the fix, which is only two lines of code.
Lan: Then the measured number, and what it does not show.

## The problem {cafe}
[still]
Minh: Lan, what is RAG, in one sentence?
Lan: Retrieval-augmented generation.
Lan: Before the model answers, you search your own documents.
Lan: Then you give the best pieces to the model, with the question.
Minh: And those pieces are chunks.
Lan: Right. You cut each document into small chunks, and you search the chunks.
Minh: Most tutorials search with embeddings and a vector database.
Lan: This work does not. It uses BM25, a classic keyword method.
Lan: It runs in the Python standard library, on a laptop.
Lan: No API key, no embedding model, no vector database.
Minh: Where does the work come from?
Lan: From a RAG module of a Claude course, written in Vietnamese.
Lan: The module was merged on the sixteenth of November.
[flow One retrieval step | Question -> BM25 over 28 chunks -> Top 3 chunks -> The model answers]
Minh: So a question comes in, and BM25 ranks every chunk.
Lan: The best chunks go to the model, and the model writes the answer.
Minh: And the test documents?
Lan: A toy example: a made-up lab handbook.
Lan: It has 7 pages, cut into 28 chunks.
Minh: What was the test question?
Lan: How long until a pH meter needs calibrating again.
[chart Where BM25 ranked the right chunk | max 28 | Chunk text only = 14 (rank 14 of 28) | note Measured on the course module; its toy corpus is in Vietnamese]
Minh: So where did BM25 put the chunk with the answer?
Lan: At rank 14 of 28.
Minh: That is far from the top. If the search keeps only a few, the model never sees it.
[still]
Minh: But the chunk did hold the answer?
Lan: It did. It said when to repeat the step.
Lan: But it never named the step, and it never named the meter.
Minh: So where were those words?
Lan: In the page title. And chunking cut the title off.
Minh: So the chunk answers the question, but shares almost no words with it.
Lan: Exactly. And BM25 only matches words that are in the chunk.
Minh: That sounds like a problem every RAG system can have.
Lan: It is. A chunk cut out of its page loses the words of its headings.

## How BM25 scores {desk}
Minh: Let's look at the code. The post comes with a small public script.
Lan: It is a smaller English toy that shows the same effect.
Lan: The course module itself is in Vietnamese, so the post shows this one.
[code bm25_title.py 14-22 hl 21 | A made-up handbook, one part of it]
Minh: The handbook is a Python dictionary.
Minh: Each document has sections, and each section has paragraphs.
Lan: Look at the highlighted line. It is the schedule of the rain gauge calibration.
Lan: It says: repeat after every 500 mm of water.
Lan: Also after moving the funnel, and each season.
Minh: That is the right answer to a question about how often to calibrate.
Lan: Yes. But read it again. It never says rain gauge, and it never says calibration.
[code bm25_title.py 38-49 | Words and chunks]
Minh: Next, the words. The tokens function lowercases the text.
Minh: Then a regular expression pulls out the words.
Lan: And the chunks function makes one chunk per paragraph.
Lan: Each chunk keeps its title and section beside it, but not inside its text.
Minh: So BM25 sees only the paragraph.
Lan: Right. That detail is the whole story.
[code bm25_title.py 56-68 hl 65-66 | bm25_scores(): one score per chunk]
Minh: Now the scoring. This is BM25 in thirteen lines.
Lan: For each chunk, it loops over the words of the query.
Lan: A query word that is not in the chunk adds nothing.
Minh: And a word that is in the chunk?
Lan: It adds two things multiplied together.
Lan: First, the inverse document frequency: how rare the word is.
Lan: A word found in only a few chunks counts for more.
Minh: So common words count for less.
Lan: Yes. Second, how often the word appears in this chunk.
Lan: But that count saturates. The tenth repeat adds much less than the first.
Minh: That is the k1 parameter, set to 1.5 here.
Lan: And b, set to 0.75, scales the count by the length of the chunk.
Lan: A long chunk is not rewarded just for being long.
Minh: And no model, no embedding, no network call.
Lan: Just counting, a logarithm, and a division.
[still]
Minh: So BM25 is good at one thing: matching the words you typed.
Lan: And blind to one thing: words that are missing from the chunk.
Minh: Even when the meaning is right there.

## The fix {desk}
Minh: So, Lan, what was the fix?
Lan: It costs nothing.
Lan: Before indexing, copy the title and the section name into each chunk.
[code bm25_title.py 52-53 | with_context(): the fix]
Minh: Here it is. Two lines.
Lan: The chunk text now starts with: Document, the title. Section, the section name.
Lan: Then a new line, and the chunk itself.
Minh: So the chunk about the schedule now says rain gauge calibration, and schedule.
Lan: Right. The words the question uses are back in the chunk.
Minh: And BM25 itself does not change at all?
Lan: Not one line. Same scorer, same question. Only the text that goes in changes.
[code bm25_title.py 74-89 hl 82-83 | The test: same query, two kinds of chunk text]
Minh: Here is the main part of the script.
Minh: It builds the chunks, then scores them twice.
Lan: Once with the chunk text only, and once with the title and section first.
Lan: Then it prints where the right chunk landed, and the top 3.
Minh: And it does all of that with the standard library.
Lan: Yes. You can run it on any laptop with Python.

## The number {classroom}
[chart Where BM25 ranked the right chunk | max 28 | Chunk text only = 14 (rank 14 of 28) | Title and section first = 4 (rank 4 of 28) | note Measured on the course module; its toy corpus is in Vietnamese]
Lan: Now the measured number, on the course module.
Lan: With the chunk text only, the right chunk was at rank 14 of 28.
Minh: And with the title and section copied in?
Lan: Rank 4 of 28. Same question, same BM25.
Minh: From 14 to 4, with two lines of code.
Lan: The question and the heading were in Vietnamese.
Lan: The post quotes them in translation.
[output bm25_title.expected 1-9 hl 1 | python bm25_title.py]
Minh: And the public toy script? What does it print?
Lan: It has 9 chunks, and the query asks how often a rain gauge needs calibration.
[output bm25_title.expected 1-9 hl 2-5 | python bm25_title.py]
Minh: With the chunk text only, the right chunk scores 0.
Lan: None of the query words is in it, so BM25 cannot see it at all.
Minh: The top 3 are a cleaning chunk, a thermometer chunk and a battery chunk.
[output bm25_title.expected 1-9 hl 6-9 | python bm25_title.py]
Lan: With the title and section first, the right chunk is at rank 4 of 9.
Minh: So it went from invisible to rank 4.
Lan: Yes. The same effect as in the course module, on a smaller toy.
Minh: But wait. Why only rank 4? The title words are in it now.
Lan: Look at the top of the list. The thermometer schedule scores 3.31.
Lan: Its text says: check how often the reading drifts.
Minh: How often. The same two words as the query.
Lan: And the battery chunk says: how often you replace it.
Lan: Those two words are rare in this handbook, so they weigh a lot.
Minh: And rain gauge calibration is now in many chunks, so it weighs less.
Lan: Exactly. Rarity is what BM25 rewards.

## The limit {cafe}
[points The limit | Rank 4 still misses a top-3 cut | The question said "how long", the heading said "frequency" | BM25 matches words, not meaning | A top 5 would include it | One question on a toy corpus is a demo, not a benchmark]
Minh: So is the problem solved?
Lan: Not fully. Rank 4 still misses a top-3 cut.
Lan: If the search sends only the top 3 chunks, the model still misses it.
Minh: Why is it not first?
Lan: The question said how long. The heading said frequency.
Lan: To BM25, those are different words.
Minh: BM25 matches words, not meaning.
Lan: Right. A top 5 would include it.
Minh: Would embeddings close the gap?
Lan: They might. But the post says it plainly: that has not been measured yet.
Minh: I like that. No claim without a number behind it.
Lan: And one more limit. This is one question on a toy corpus.
Lan: The post calls it a demo, not a benchmark.
Minh: So the result is a direction, not a proof.
Lan: Yes. To trust it, you would test many questions on real documents.
[still]
Minh: Still, the fix is free, and it is quick to test.
Lan: Yes. It is the first thing to try, and the rank tells you if it helped.

## Recap {classroom}
[card The lesson | Put the title back in every chunk | Measure the rank of the right chunk | Then decide on a vector database]
Lan: Let's recap.
Minh: BM25 runs locally, with no API key and no vector database.
Lan: A chunk cut out of its page loses the words of its title.
Minh: Copying the title and section into each chunk brought them back.
Lan: On the course module, the right chunk moved from rank 14 to rank 4 of 28.
Minh: And rank 4 still misses a top-3 cut. Words are not meaning.
Lan: So the lesson is: before you add a vector database, put the title back.
Lan: Then measure the rank of the right chunk.
Minh: The script is next to the post. Run it, and change the query.
Lan: And tell us: in your RAG setups, what helped retrieval most?
Minh: More context per chunk, a larger top-k, or embeddings?
Lan: Thanks for watching Lab to Prod. See you next time.
Minh: Bye for now.
