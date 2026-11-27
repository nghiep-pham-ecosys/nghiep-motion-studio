<!-- Talk file of the specialty explainer for the study lesson saa-sqs-sns-eventbridge.md (next to this file). -->
<!-- Build: just explainer samples/specialty-2026-11-27/saa-sqs-sns-eventbridge.md --talk samples/specialty-2026-11-27/saa-sqs-sns-eventbridge.talk.md -->
Title: Decoupling on AWS: SQS, SNS and EventBridge, explained
Job: en-2026-11-27-saa-explainer

## Intro {classroom}
Lan: Hello, and welcome to Study notes explained. I'm Lan.
Minh: And I'm Minh. I'm studying for the AWS Solutions Architect Associate exam.
Lan: Both of our voices are AI voices, and the pictures are made with AI.
[points Today | The question: what if one part is slow? | Three services: SQS, SNS, EventBridge | The concepts the exam likes | A worked example: an online shop | The traps, and a quiz]
Minh: This is lesson 2 of my notes, in the domain of resilient architectures.
Lan: Today we answer one question with three services.
Minh: Then we build one small design, look at the traps, and do a quiz.

## The question {cafe}
[still]
Lan: Minh, picture an online shop on its busiest day.
Lan: The website takes orders, and a backend processes them.
Minh: And the backend is slower than the website.
Lan: Right. So what happens when orders arrive faster than the backend can work?
Minh: If the website calls the backend directly, the website waits.
Minh: Or worse, orders are lost when the backend is overloaded.
Lan: That is tight coupling. One slow part slows everything.
Lan: The question of the lesson is this.
Lan: When one part of a system is slow or fails,
Lan: how do the other parts keep running?
Minh: And the AWS answer is decoupling.
Lan: Yes. With queues, and with events.
Minh: And three services do most of that work: SQS, SNS and EventBridge.

## SQS, the queue {desk}
[flow A queue between two parts | Website -> SQS queue -> Order workers]
Lan: Let's start with SQS, the Simple Queue Service.
Lan: The website writes each order to a queue and moves on.
Minh: And the workers take orders from the queue when they are ready.
Lan: Right. Consumers pull from the queue. That is the model of SQS.
Minh: So if the workers are slow, the orders just wait in the queue.
Lan: Exactly. SQS buffers the work, so a slow consumer does not slow the producer.
Minh: And if a worker dies?
Lan: The message is still in the queue, and another worker picks it up.
[points SQS: two kinds of queue | **Standard:** at-least-once delivery, no ordering | **FIFO:** strict order, exactly-once processing | FIFO: 300 messages per second, 3,000 with batching]
Minh: There are two kinds of queue, right?
Lan: Yes. Standard delivers each message at least once, in no fixed order.
Lan: FIFO keeps the order, and processes each message exactly once.
Minh: First in, first out.
Lan: The exam figure for FIFO is 300 messages per second, or 3,000 with batching.
Minh: So when a question says keep the order, no duplicates, I pick FIFO.
Lan: Good. That is the first trap, and we will come back to it.

## SNS, the fan-out {desk}
[flow Fan-out: one message, many subscribers | Order service -> SNS topic -> Warehouse queue, Accounting queue, Email]
Lan: Now SNS, the Simple Notification Service.
Lan: SNS is publish and subscribe. You publish one message to a topic.
Minh: And every subscriber of the topic gets a copy.
Lan: Right. SNS pushes the message. Nobody has to poll.
Minh: What can subscribe?
Lan: Email, SMS, a Lambda function, or an SQS queue.
Minh: So a new order can reach the warehouse, accounting and the customer at once.
Lan: Yes. That pattern is called fan-out: SNS to several SQS queues.
Lan: Each system then has its own queue, and works at its own pace.
Minh: Why not let three systems read one SQS queue?
Lan: Because each message in a queue goes to only one consumer.
Lan: The warehouse would get some orders, and accounting would get the others.
Minh: Ah. So one queue per system, behind one topic.

## EventBridge, the router {classroom}
[points EventBridge | An event bus with **filter rules** | Events from AWS services, SaaS apps or a schedule | Filters on the **content** of an event | Schema registry, archive and replay]
Lan: The third service is EventBridge.
Lan: EventBridge is an event bus with rules.
Minh: How is that different from SNS?
Lan: A rule looks at the content of each event, and routes it by that content.
Lan: And the events can come from AWS services, from SaaS apps, or from a schedule.
Minh: Give me an example.
Lan: An EC2 instance changes its state to stopped. AWS emits an event.
Lan: A rule matches that event, and calls a Lambda function.
Minh: So nobody polls EC2 to see if it stopped.
Lan: Right. EventBridge also has a schema registry, and it can archive and replay events.
[card In one line each | SQS buffers work | SNS pushes one message to many | EventBridge routes events by content]
Minh: Let me say it back in one line each.
Minh: SQS buffers work. SNS pushes one message to many subscribers.
Minh: EventBridge routes events by their content.
Lan: Perfect.

## The concepts the exam likes {desk}
[points The visibility timeout | A message being processed is hidden from other consumers | Not deleted in time: it shows up again | Then it is processed twice | Long processing: raise the timeout]
Lan: Now a few concepts the exam loves. First, the visibility timeout.
Lan: When a worker receives a message, the message is hidden from the others.
Minh: For how long?
Lan: For the visibility timeout. The worker must delete the message before it ends.
Minh: And if the worker is still busy when the timeout ends?
Lan: The message shows up again, and another worker processes it too.
Minh: So the same order is processed twice.
Lan: Yes. If processing is long, raise the timeout.
[points Dead-letter queue and long polling | Fails more than **maxReceiveCount** times: moves to the DLQ | So one bad message does not block the queue | Long polling: wait up to 20 s for a message | Fewer empty receives, lower cost]
Minh: What about a message that always fails?
Lan: That is what a dead-letter queue is for.
Lan: After a set number of failed receives, the message moves to the dead-letter queue.
Minh: The setting is the max receive count.
Lan: Right. The bad message waits there to be looked at, and the queue keeps moving.
Minh: And long polling?
Lan: A worker can wait up to 20 seconds for a message.
Lan: It does not have to ask again and again.
Lan: Fewer empty receives, and a lower bill.
[points Delays, size and scaling | Delay queue or message timer: up to 15 minutes | Message size: 256 KB in the exam | AWS raised it to 1 MiB in 2025 | Bigger payloads: S3, plus a pointer in the message | Scale workers on the backlog per instance]
Minh: Can a message wait before it is delivered?
Lan: Yes. A delay queue or a message timer holds it for up to 15 minutes.
Minh: And how big can a message be?
Lan: The exam says 256 kilobytes.
Lan: AWS raised the limit to one mebibyte in 2025, so check before you rely on it.
Minh: And a bigger file?
Lan: Put the file in S3, and send a message that points to it.
Lan: The Extended Client Library does that for you.
Minh: Last one. How do I scale the workers?
Lan: On the queue length: approximate number of messages visible.
Lan: Even better, on the backlog per instance.

## A worked example {desk}
[flow An online shop, decoupled | Website -> Orders queue -> Order workers -> SNS topic -> Warehouse queue, Accounting queue, Email]
Lan: Let's put it all together, in one small online shop.
Minh: The website writes each new order to an SQS queue.
Lan: During a sale, orders pile up in the queue instead of being lost.
Minh: Order workers read the queue at their own pace.
Lan: When an order is accepted, the service publishes one message to an SNS topic.
Minh: The topic fans out to a warehouse queue, an accounting queue and an email.
Lan: Each of them works on its own, and a slow one slows nobody else.
[points The settings | Each worker takes about 2 minutes per message | Visibility timeout: 6 minutes | Dead-letter queue after 3 failed receives | Long polling: wait time seconds = 20 | Scale on the backlog per instance]
Minh: Now the numbers. Each worker takes about 2 minutes per message.
Lan: So the visibility timeout is 6 minutes, well above the processing time.
Minh: A message that fails 3 times moves to the dead-letter queue.
Lan: So one bad order never blocks the others.
Minh: Workers use long polling, with a wait of 20 seconds.
Lan: And the worker fleet scales on the backlog per instance.
[flow And one event rule | EC2 instance stopped -> EventBridge rule -> Lambda function]
Minh: And separately, an EventBridge rule watches EC2.
Lan: When an instance changes to stopped, the rule calls a Lambda function.
Minh: Three services, each doing the one thing it is good at.

## The traps {exam}
[card Exam traps | Order, no duplicates: **SQS FIFO** | One event, many systems: **SNS fan-out to SQS** | Filter on content, SaaS events: **EventBridge** | Processed twice: **visibility timeout** too short | Replay a stream: **Kinesis**, not SQS]
Lan: Now the traps. I describe a question, and you name the answer.
Minh: Ready.
Lan: The question says: keep the order, and no duplicates.
Minh: SQS FIFO, not Standard.
Lan: One event, and several systems that each process it independently.
Minh: SNS fan-out to several SQS queues. Not many consumers on one queue.
Lan: Filter on the content of events, or events from a SaaS app.
Minh: EventBridge.
Lan: Messages are processed twice.
Minh: The visibility timeout is shorter than the processing time.
Lan: And a large data stream that many consumers read again.
Minh: That is Kinesis Data Streams. SQS is for work to be done.

## Quiz {exam}
Lan: Now a quiz. Answer aloud in the pause, then listen to Minh.
Lan: Question 1. Orders spike, the backend is slow, and orders are lost. [pause 8]
Minh: Put an SQS queue between the web tier and the backend, to buffer the load.
Lan: Question 2. A bank must process transactions in order per account. [pause 8]
Minh: SQS FIFO, with the account number as the message group ID.
Lan: Question 3. A new order must reach the warehouse, accounting and email. [pause 8]
Minh: An SNS topic that fans out to 3 SQS queues.
Lan: Question 4. Each message takes 2 minutes, and messages are processed twice. [pause 8]
Minh: The visibility timeout is too short. Raise it, to 6 minutes for example.
Lan: Question 5. A few bad messages make the workers fail again and again. [pause 8]
Minh: Configure a dead-letter queue, with a max receive count of 3.
Lan: Question 6. Workers keep getting empty responses, and the bill is high. [pause 8]
Minh: Turn on long polling, with a wait time of 20 seconds.
Lan: Question 7. Run a Lambda function whenever an EC2 instance stops. [pause 8]
Minh: An EventBridge rule on the EC2 state-change event, with Lambda as target.
Lan: Question 8. A message needs to carry a 2 MB file. [pause 8]
Minh: Store the file in S3, and send the path in the message.
Lan: Question 9. Which metric should scale the workers with the waiting work? [pause 8]
Minh: Approximate number of messages visible, or better, the backlog per instance.
Lan: Question 10. Three apps must read the last 7 days of clicks again. [pause 8]
Minh: Kinesis Data Streams. It keeps data for 24 hours by default.
Minh: And up to 365 days. SQS deletes a message once it is processed.

## Recap {classroom}
[card Recap | SQS buffers work | SNS pushes one message to many | EventBridge routes by content | Visibility timeout above processing time | DLQ for bad messages, long polling for cost]
Lan: Let's recap.
Minh: Decoupling keeps the system running when one part is slow.
Lan: SQS buffers work. SNS pushes one message to many subscribers.
Minh: EventBridge routes events by their content.
Lan: Set the visibility timeout above the processing time.
Minh: Use a dead-letter queue for bad messages, and long polling to save cost.
Lan: And for a stream that many apps read again, think Kinesis.
Minh: Thanks, Lan. I'm ready for the practice questions.
Lan: Thanks for watching. See you in the next lesson.
