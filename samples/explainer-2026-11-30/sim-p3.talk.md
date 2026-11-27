<!-- Talk file of the explainer for the lab-to-prod post 2026-11-30-sim-p3 (the copy next to this file). -->
<!-- Build: just explainer samples/explainer-2026-11-30/2026-11-30-sim-p3 --talk samples/explainer-2026-11-30/sim-p3.talk.md -->
Title: Lab to Prod: 2000 small tanks become a pipe, and the test tolerance
Job: en-2026-11-30-sim-p3-explainer

## Intro {classroom}
Lan: Hello, and welcome to Lab to Prod. I'm Lan.
Minh: And I'm Minh. Today we open one post and the work behind it.
Lan: Both of our voices are AI voices, and the pictures are made with AI.
[image image.png | The post of the day]
Minh: The post is called: 2000 small tanks become a pipe.
Lan: It is part 3 of the Simulation notes.
Lan: The card has 4 rows: 1 tank, 10 tanks, 2000 tanks, and one pipe.
Minh: And one lesson at the bottom: set a test's tolerance from the error term.
[points Today | Two reactors: a stirred tank and a pipe | Why many tanks become a pipe | The test that compares them | How close is close enough? | The limits]
Minh: So what is on the menu?
Lan: First the two reactor models, and why many tanks become a pipe.
Minh: Then the test that compares them, and the hard part: the tolerance.
Lan: And at the end, what the result does not show.

## Two reactors {cafe}
[still]
Minh: Lan, the post is about a small process simulator. What is it?
Lan: It is process-sim-lab, a small Python library for process simulation.
Lan: Each unit of a plant is one function: a reactor, a mixer, a splitter.
Minh: And this post is about two reactors.
Lan: Right. The first is a stirred tank, a CSTR.
Lan: The liquid inside is mixed so well that it is the same everywhere.
Minh: So the outlet has the same concentration as the tank.
Lan: Exactly. The reaction runs at the low, outlet concentration the whole time.
Minh: And the second?
Lan: A plug-flow reactor, a PFR. Think of a long pipe.
Lan: The liquid moves along it in thin slices, and the slices do not mix.
Minh: So each slice reacts on its own, on its way down the pipe.
Lan: Yes. Early in the pipe the concentration is high, so it reacts fast.
Minh: Where does the PFR model come from?
Lan: From a pull request merged on the 17th of November.
Lan: It added a PFR, a mixer and a splitter, with 17 tests.
[points One reaction, A to B | First order: the rate is k times the concentration of A | Isothermal: one temperature | Constant density: a liquid | Conversion X: the share of A that reacted]
Minh: Which reaction do they run?
Lan: One reaction, A to B, first order.
Lan: The rate is k times the concentration of A.
Minh: At one temperature, and in a liquid whose density does not change.
Lan: Right. And the number we watch is the conversion, X.
Lan: The share of A that has turned into B at the outlet.

## Many tanks become a pipe {desk}
[flow Split one tank into N | Feed -> Tank 1 -> Tank 2 -> ... Tank N -> Outlet]
Minh: Now the idea of the post. Take one stirred tank, and split it into N tanks.
Lan: Same total volume. Each small tank gets an equal share of it.
Minh: And they sit in a row, in series. The outlet of one feeds the next.
Lan: Each tank mixes only its own small part.
Lan: So the concentration drops step by step, along the row.
Minh: Like the slices of a pipe.
Lan: Exactly. With many small tanks, the chain behaves like one pipe.
[card Two closed forms | N tanks in series: X = 1 - (1 + k tau / N)^-N | One pipe: X = 1 - exp(-k tau) | As N grows, the first tends to the second]
Minh: And there is a formula for each.
Lan: For N tanks: X is 1 minus, 1 plus k tau over N, to the power minus N.
Lan: For the pipe: X is 1 minus e to the minus k tau.
Minh: Tau is the space time: the volume divided by the flow.
Lan: And as N grows, the first formula tends to the second.
Minh: That is a classic result of reactor design.
Lan: The post links the lecture slides it relies on.
Minh: So two models, written separately, should agree when N is large.
Lan: Yes. And that makes a test.

## The test {desk}
Minh: Let's look at the test. It is in the merged pull request.
[code test_pfr.py 43-55 hl 44 | test_pfr.py: one tank against one pipe]
Lan: First the inputs. k is 0.02 per second.
Lan: The volume is 1 cubic metre, and the flow is 0.01 cubic metres per second.
Minh: So tau is 100 seconds.
Lan: And k times tau is 2. The post writes it as k tau equals 2.
[code test_pfr.py 43-55 hl 51-54 | test_pfr.py: one tank against one pipe]
Minh: Then both models run once, on the same feed.
Lan: The stirred tank gives two thirds. The pipe gives 0.86466.
Minh: So one big tank converts 67 percent, and the pipe about 86.5 percent.
Lan: Right. And the test checks that the pipe beats the tank.
[code test_pfr.py 56-67 hl 58-63 | The chain: 2000 calls to the CSTR model]
Minh: Now the chain. N is 2000.
Lan: The loop calls the stirred-tank model 2000 times.
Lan: Each call gets one 2000th of the volume.
Lan: And the outlet of each call is the feed of the next.
Minh: So the test does not use the series formula. It really chains the tanks.
Lan: Yes. Two units, written separately, check each other.
[code test_pfr.py 56-67 hl 64-67 | Three checks on the chain]
Minh: Then three checks.
Lan: First, the chain must match its own closed form, to 10 decimal places.
Lan: Second, the chain must stay below the pipe.
Lan: Third, the chain must come within 2 times 10 to the minus 4 of the pipe.
Minh: That last number is the tolerance. Where does it come from?
Lan: That is the hard part of the post.

## How close is close enough? {classroom}
[still]
Minh: Lan, why not pick a round tolerance? Most tests do.
Lan: Because a round number says nothing about this problem.
Lan: The post asks first: how big should the gap be?
[card The error term | Gap to the pipe: about (k tau)^2 exp(-k tau) / (2N) | At k tau = 2 and N = 2000: about 1.35e-4 | It shrinks as 1 over N]
Lan: There is a short formula for it, the leading error term.
Lan: k tau squared, times e to the minus k tau, divided by 2N.
Minh: So double the tanks, and the gap halves.
Lan: Right. It shrinks as 1 over N.
Minh: And the test comment predicts it too.
Lan: It says about one point four times ten to the minus four, for 2000 tanks.
[code tanks_in_series.py 14-25 hl 20-23 | tanks_in_series.py: chains of 1 to 2000 tanks]
Minh: And the post has a small script that measures it.
Lan: Same inputs as the test. Same chain of tanks, one tank at a time.
Lan: It runs the chain for 1, 2, 5, 10, 100 and 2000 tanks.
Minh: And prints the real gap next to the formula.
Lan: It needs only the Python standard library.
[output tanks_in_series.expected 1-8 hl 3 | python tanks_in_series.py]
Minh: Let's read the output. 1 tank first.
Lan: X is 0.666667, two thirds.
Lan: The real gap to the pipe is one point nine eight times ten to the minus one.
Minh: And the formula?
Lan: It says two point seven one times ten to the minus one. Too big.
Minh: So for 1 tank, the formula is off.
Lan: Yes. It is only the leading term. It works when N is large.
[output tanks_in_series.expected 1-8 hl 6-8 | python tanks_in_series.py]
Minh: And for more tanks?
Lan: At 10 tanks, X is 0.838494. The gap and the formula are already close.
Minh: At 100 tanks, the gap is 0.0027, as the formula says.
Lan: And at 2000 tanks, X is 0.864529.
Lan: The gap is one point three five times ten to the minus four.
Minh: And the formula gives the same, to 3 digits.
Lan: So the gap was known before the test ran.
[chart Conversion X at k tau = 2 | max 1 | 1 tank = 0.667 | 10 tanks = 0.838 | 2000 tanks = 0.86453 | PFR = 0.86466 | note The rows of the post's card, from tanks_in_series.py]
Minh: Here it is as a chart, with the rows of the post's card.
Lan: Most of the gain comes from the first 10 tanks.
Minh: After that, the bars barely move.
Lan: And 2000 tanks sit just below the pipe.

## Setting the tolerance {desk}
[output tanks_in_series.expected 1-10 hl 9-10 | python tanks_in_series.py]
Minh: So, back to the tolerance. The test allows 2 times 10 to the minus 4.
Lan: That is just above the predicted gap for 2000 tanks.
Minh: What does that buy?
Lan: The script answers that. It finds the shortest chain each tolerance accepts.
Lan: With 2 times 10 to the minus 4, a chain needs at least 1354 tanks to pass.
Minh: And with a round tolerance, like 0.01?
Lan: Any chain of 27 tanks or more passes.
[chart The shortest chain that passes | max 2000 | Tolerance 0.01 = 27 (27 tanks) | Tolerance 2e-4 = 1354 (1354 tanks) | note The test itself chains 2000 tanks]
Minh: So with 0.01, that check cannot tell 2000 tanks from 27.
Lan: Right. A loose tolerance makes the comparison almost blind.
Minh: And with 2 times 10 to the minus 4, it can.
Lan: Yes. Now the tolerance says something about the model.
[points The lesson | Work out how fast the model converges | Predict the gap before the test runs | Set the tolerance just above it | Not a round number out of habit]
Minh: So the lesson of the post?
Lan: When a test compares a model with a limit, work out how fast it converges.
Lan: Then set the tolerance from that, not from habit.

## The limits {cafe}
[points The limits | Isothermal: one temperature | First order: one simple rate law | Constant density: a liquid | The gap formula is only the leading term]
Minh: Is the result general?
Lan: No. The post lists its limits plainly.
Lan: The reactor is isothermal. Heat effects are left out.
Lan: The reaction is first order, and the density is constant.
Minh: And the gap formula?
Lan: It is only the leading term. We saw that it is off for 1 tank.
Minh: So it is a good guide for long chains, not for short ones.
Lan: Right. Here it works because N is 2000.
[still]
Minh: I like that the test knew the size of the gap before it ran.
Lan: Yes. A test with a predicted gap can fail for the right reasons.
Minh: And a loose one can pass for the wrong ones.

## Recap {classroom}
[card Recap | Many stirred tanks in series tend to one pipe | One test chains 2000 tank models and compares with the pipe | The error term predicts the gap: about 1.35e-4 | Tolerance 2e-4 needs 1354 tanks; 0.01 passes 27]
Lan: Let's recap.
Minh: Many small stirred tanks in a row behave like one pipe.
Lan: So one test chains 2000 calls to the tank model, and compares with the pipe.
Minh: Two models, written separately, check each other.
Lan: A short formula predicts the gap before the test runs.
Minh: For 2000 tanks, about one point three five times ten to the minus four.
Lan: So the tolerance is 2 times 10 to the minus 4. A chain needs 1354 tanks.
Minh: A round 0.01 would pass with 27.
Lan: The script is next to the post. Run it, and try other values of k tau.
Minh: And tell us: when you compare two solvers, how do you pick the tolerance?
Lan: Thanks for watching Lab to Prod. See you next time.
Minh: Bye for now.
