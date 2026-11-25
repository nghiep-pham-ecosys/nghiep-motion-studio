# The stirred-tank reactor (CSTR) in process-sim-lab

33 scenes, 1763 words (about 11.8 min at 150 words per minute).

## Intro

**Narrator** (intro): Welcome. Today we open one small unit of process-sim-lab. It is an open source process simulator, written in Python. The unit is the CSTR, the continuous stirred-tank reactor. Most chemical engineers meet this reactor first. Its code is short enough to read in one sitting. By the end, you will know the model and the numbers in its tests. You will also know how to size a tank for a target conversion.

**Guest** (ask-look): Before the math, what does a stirred-tank reactor actually look like?

## The tank

**Narrator** (tank): Picture a tank with a stirrer. A liquid feed flows in at a steady rate. The same flow leaves through the outlet. Inside the tank, the reaction turns A into B as the liquid flows. Water, or any other solvent, just passes through. The stirrer is so strong that every point in the tank is the same. That is the key idea of this reactor: perfect mixing.

**Narrator** (mixing): Perfect mixing has a surprising consequence. The liquid that leaves is exactly the liquid inside the tank. So the outlet concentration of A is the concentration everywhere. A fresh drop of feed is diluted at once into the pool. In that pool, most of A has already reacted. Keep this picture in mind. It explains almost everything that follows.

**Guest** (ask-assume): Does the model in process-sim-lab assume anything else?

## Assumptions

**Narrator** (assumptions): Yes, it makes five assumptions. The reactor runs at steady state, so nothing changes with time. It is isothermal, so the temperature is fixed. The volumetric flow, Q, is constant. That is because the liquid density does not change. There is one reaction, from A to B, and it does not run backwards. One mole of B forms for every mole of A that reacts. And the reaction is first order. Its rate is k times the concentration of A.

## Space time

**Narrator** (tau): The first number the code computes is the space time, called tau. Tau is the volume, V, divided by the volumetric flow, Q. Its unit is seconds. You can read it as the average time a drop spends in the tank. A bigger tank, or a slower flow, gives the reaction more time.

## The mole balance

**Narrator** (balance): Now we write the mole balance for A, the reactant. At steady state, the flow in equals the flow out, plus what reacts. In symbols, F A zero equals F A, plus k times C A times the volume. F A zero is the molar flow of A in the feed. F A is the flow of A that leaves. The tank is well mixed, so C A is simply F A divided by the flow. Put that into the balance, and it solves in one line. F A equals F A zero, divided by one plus k tau.

**Guest** (ask-conversion): And how much of A actually turns into B?

**Narrator** (conversion): That is the conversion, called X. It is the fraction of the feed A that reacts. From the balance, X equals k tau, divided by one plus k tau. The product k tau has a name of its own: the Damkohler number. It compares the speed of the reaction with the time in the tank.

**Narrator** (damkohler): Notice that the conversion depends only on the Damkohler number. A Damkohler number of one gives exactly half conversion. A Damkohler number of five gives five sixths. Two reactors can differ a lot in size and in rate. They still behave the same when their Damkohler numbers match. That is why the code returns the Damkohler number with the conversion. It tells you where you are on the curve.

## The code

**Narrator** (code-func): Here is the heart of the function, isothermal C S T R first order. It computes tau as the volume over the volumetric flow. The Damkohler number is the rate constant times tau. The conversion is the Damkohler number over one plus itself. Next, the reacted flow is the feed flow of A times the conversion. The code takes that amount away from A, the reactant. It adds the same amount to B, the product. The outlet keeps the temperature and the pressure of the feed. The function returns a small result object with four fields. They are the outlet, the conversion, the space time and the Damkohler number.

**Guest** (ask-bad): What happens if I pass a volume of zero by mistake?

**Narrator** (checks): The function refuses it before any math runs. The rate constant must be zero or more. The volume and the volumetric flow must both be above zero. The reactant must be in the feed. The reactant and the product must be different components. Each bad input raises a value error with a clear message. A test checks every one of these cases.

## A worked example

**Narrator** (example): Now the example from the documentation and the tests. The feed has ten moles per second of A. It also has fifty moles per second of water. The temperature is three hundred and thirty kelvin. The rate constant is zero point zero one per second. The volume is two cubic meters. The volumetric flow is zero point zero zero four cubic meters per second.

**Guest** (ask-guess): Let me guess the space time. Two divided by zero point zero zero four. Is it five hundred seconds?

**Narrator** (example-numbers): Exactly, five hundred seconds. Multiply by the rate constant, and the Damkohler number is five. The conversion is five over six, about eighty three point three percent. The test checks this value to twelve decimal places. It checks the outlet flows of A and B the same way.

**Narrator** (outlet): What leaves the tank? The flow of A drops from ten to about one point six seven moles per second. B rises from zero to about eight point three three. The water passes through unchanged, at fifty. The total stays at sixty moles per second. That is because one mole of A makes one mole of B. Nothing is created or lost.

## The tests

**Narrator** (tests): The tests are short and worth reading. The first test compares the conversion with the analytic value. A second test checks that moles are conserved. It also checks that the temperature and the pressure stay the same. A third checks that a rate constant of zero gives no conversion. In that case there is no B at all. A fourth checks that a larger tank gives a higher conversion. But the conversion never reaches one hundred percent. The last test feeds in bad inputs and expects an error each time.

## Sizing a tank

**Guest** (ask-design): In a real plant I start from the target. How big must the tank be for ninety percent conversion?

**Narrator** (design): Turn the formula around. X is k tau over one plus k tau. Solving for k tau gives X over one minus X as the answer. For ninety percent, that is zero point nine over zero point one. So the Damkohler number must be nine. The rate constant is zero point zero one per second. So tau must be nine hundred seconds. The flow is still zero point zero zero four cubic meters per second. So the volume is three point six cubic meters.

**Narrator** (diminishing): Now push the target to ninety nine percent. The Damkohler number jumps to ninety nine. The volume jumps to thirty nine point six cubic meters. That is eleven times the volume for nine more percent of conversion. The last few percent are expensive in a stirred tank.

## CSTR versus PFR

**Guest** (ask-why): Why is the stirred tank so expensive at the end?

**Narrator** (curve): Because of perfect mixing. The whole tank sits at the outlet concentration, which is low. So the reaction runs slowly everywhere. Compare it with the plug flow reactor, or P F R for short. It is also a unit in process-sim-lab. In a plug flow reactor, the liquid moves like a piston. The concentration falls slowly along the tube. Its conversion is one minus e to the power of minus k tau. On this chart, look at a Damkohler number of five. The stirred tank reaches eighty three percent. The plug flow reactor reaches about ninety nine point three percent.

**Narrator** (series): There is a middle way: several stirred tanks in a row. Split the same total Damkohler number of five over N equal tanks. Two tanks give about ninety one point eight percent. Three tanks give about ninety four point seven percent. As N grows, the series of tanks tends to the plug flow result. The plug flow reactor tests in process-sim-lab check this limit.

## In a flowsheet

**Guest** (ask-flowsheet): Can I use the stirred tank inside a bigger process?

**Narrator** (flowsheet): Yes. In a flowsheet, the unit type is called cstr. It has one inlet and one outlet. The worked example of process-sim-lab joins four units. They are a mixer, the stirred tank, a flash drum and a purge splitter. Part of the vapour from the drum goes back to the mixer. That is the recycle stream. The solver tears that recycle stream. Then it iterates until the flows stop changing.

**Narrator** (flowsheet-numbers): In that example, the rate constant is zero point zero two three per second. The volume is two cubic meters. The flow is zero point zero two cubic meters per second. So tau is one hundred seconds, and the Damkohler number is two point three. The conversion in one pass is two point three over three point three. That is about seventy percent. The recycle brings unreacted A back. Five point six moles per second of A come in. Four point six of them react. So the plant converts about eighty two percent overall.

**Narrator** (agent): The same unit is also a tool for an AI agent. The agent layer of process-sim-lab offers it as a tool named cstr. A language model can call the exact function instead of guessing. One of the evaluation cases is the example from this video. It expects a space time of five hundred seconds. It expects a Damkohler number of five, and five sixths conversion.

## Limits and recap

**Guest** (ask-limits): So what can this model not do?

**Narrator** (limits): It is simple on purpose. It has no energy balance. So a hot, exothermic reaction is out of scope. It handles one first order reaction, not a network of reactions. And it assumes a constant density. For those cases you need a bigger model. But these few lines go a long way for learning. They also help you check a design by hand.

**Narrator** (recap): Let us recap. Tau is the volume over the flow. The Damkohler number is k times tau. The conversion is the Damkohler number over one plus itself. Perfect mixing makes one stirred tank weaker than a plug flow reactor. Several tanks in series close the gap.

**Narrator** (outro): The code, the tests and the documentation are all open. You can find them in the process-sim-lab repository on GitHub. Try changing the volume in the example. Predict the conversion before you run it. Thanks for watching.
