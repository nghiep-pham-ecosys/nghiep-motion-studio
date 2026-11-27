# Lesson 2026-11-27: SAA-C03 lesson 2 — Decoupling with SQS, SNS and EventBridge

Track: AWS Certified Solutions Architect – Associate (SAA-C03), exam domain "Design Resilient Architectures", as in
lesson 1.

The question of the lesson: **when one part of a system is slow or fails, how do the other parts keep running?**
The AWS answer is decoupling, with queues and events.

## Concept

### Three services

| Service | Model | Use it when | Remember for the exam |
| --- | --- | --- | --- |
| **SQS** | a queue; consumers **pull** (poll) | buffering load, processing later, not losing work when a consumer dies | Standard: at-least-once delivery, no ordering; FIFO: strict order, exactly-once processing, 300 messages per second (3,000 with batching) |
| **SNS** | publish/subscribe; SNS **pushes** | one message to many places (email, SMS, Lambda, SQS) | **Fan-out:** SNS to several SQS queues, so each system processes on its own |
| **EventBridge** | an event bus with **filter rules** | reacting to the content of an event, events from AWS services or SaaS apps, schedules | Filtering on content, schema registry, archive and replay |

In one line each: SQS **buffers** work, so a slow consumer does not slow the producer. SNS **pushes** one message to
many subscribers. EventBridge **routes** events by their content.

### Concepts the exam likes

- **Visibility timeout:** a message being processed is hidden from other consumers; if it is not deleted before the
  timeout ends, it shows up again and is processed twice. Long processing: raise the timeout.
- **Dead-letter queue (DLQ):** a message that fails more than `maxReceiveCount` times moves to the DLQ to be looked
  at later, so it does not block the queue.
- **Long polling** (`WaitTimeSeconds` up to 20 s): fewer empty receives, lower cost.
- **Delay queue / message timer:** holds a message back for up to 15 minutes.
- **Message size:** the exam says at most 256 KB (AWS raised the limit to 1 MiB in 2025, announced 4 August 2025:
  https://aws.amazon.com/about-aws/whats-new/2025/08/amazon-sqs-max-payload-size-1mib). Bigger payloads go to S3 and the message carries a pointer (the Extended Client Library).
- **Auto Scaling on queue length:** the metric `ApproximateNumberOfMessagesVisible`, or better a backlog per instance.

## Worked example

A self-written example: the order flow of a small online shop.

1. The web front end writes each new order to an **SQS** queue. During a sale, orders pile up in the queue instead of
   being lost, and the order workers read them at their own pace.
2. When an order is accepted, the order service publishes one message to an **SNS** topic. The topic fans out to a
   warehouse queue, an accounting queue and an email subscription, and each one works on its own.
3. Each worker takes about 2 minutes per message, so the queue's visibility timeout is 6 minutes. Without that, a
   message would show up again while it is still being processed.
4. A message that fails 3 times moves to a **dead-letter queue** (`maxReceiveCount` = 3), so one bad order never
   blocks the others.
5. Workers receive with long polling (`WaitTimeSeconds` = 20), so idle workers do not pay for empty receives.
6. The worker fleet scales on the backlog per instance, computed from `ApproximateNumberOfMessagesVisible`.
7. Separately, an **EventBridge** rule matches the EC2 event "instance state changed to stopped" and invokes a Lambda
   function that records it.

## Edge cases

- "Keep the order, no duplicates" → **SQS FIFO**, not Standard.
- "One event, several systems that process it independently" → **SNS fan-out to several SQS queues**, not several
  consumers reading one SQS queue (each message goes to only one consumer).
- "Filter on content, events from SaaS apps or AWS services" → **EventBridge**.
- "Messages are processed twice" → the visibility timeout is shorter than the processing time.
- Kinesis Data Streams is for large **data streams** that can be read again by many consumers at once; SQS is for
  **work to be done**.

## Quiz

1. A web shop gets a sudden spike of orders, the backend is slow, and orders are lost under load. What is the cheapest
   way not to lose them?
   Answer: Put an SQS queue between the web tier and the backend to buffer the load; the backend reads at its own pace.
2. A banking system must process transactions in order for each account, with no duplicates. What do you use?
   Answer: SQS FIFO, with the account number as the MessageGroupId (order within each group, no duplicates).
3. A new order must go at the same time to the warehouse, to accounting and to the customer by email. Which design?
   Answer: An SNS topic that fans out to 3 SQS queues (or SQS queues plus an email subscription).
4. A worker takes 2 minutes per message, but messages are processed twice. Why, and how do you fix it?
   Answer: The visibility timeout is shorter than 2 minutes, so the message shows up again for another worker. Raise
   the visibility timeout (to 6 minutes, for example) or extend it while processing.
5. A few badly formatted messages make the workers fail again and again and block the other messages. What do you do?
   Answer: Configure a dead-letter queue with a maxReceiveCount (3, for example).
6. SQS costs are high because workers call ReceiveMessage all the time and get empty responses. How do you fix it?
   Answer: Turn on long polling (WaitTimeSeconds = 20).
7. You want to run a Lambda function whenever an EC2 instance changes to stopped. Which service?
   Answer: An EventBridge rule that matches the EC2 state-change event and targets the Lambda function.
8. A message needs to carry a 2 MB file. What is the right way?
   Answer: Store the file in S3 and send an SQS message with its path (the SQS Extended Client Library).
9. The number of EC2 workers must grow and shrink with the work waiting. Which metric do you use?
   Answer: ApproximateNumberOfMessagesVisible (better: the backlog per instance) with target tracking.
10. Three different analytics apps must read the last 7 days of a click stream again. SQS or Kinesis?
    Answer: Kinesis Data Streams: it keeps data for up to 365 days (24 h by default), and many consumers read it
    again independently; SQS deletes a message once it is processed.
