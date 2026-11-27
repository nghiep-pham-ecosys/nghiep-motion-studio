<!-- Talk file of the specialty explainer for the study lesson saa-disaster-recovery.md (next to this file). -->
<!-- Build: just explainer samples/specialty-2026-11-30/saa-disaster-recovery.md --talk samples/specialty-2026-11-30/saa-disaster-recovery.talk.md -->
Title: Disaster recovery on AWS: RPO, RTO and the four strategies, explained
Job: en-2026-11-30-saa-dr-explainer

## Intro {classroom}
Lan: Hello, and welcome to Study notes explained. I'm Lan.
Minh: And I'm Minh. I'm studying for the AWS Solutions Architect Associate exam.
Lan: Both of our voices are AI voices, and the pictures are made with AI.
[points Today | Two numbers: RPO and RTO | Four strategies, from cheap to costly | The services, layer by layer | A worked example: a small web shop | The traps, and a quiz]
Minh: This is lesson 1 of my notes, in the domain of resilient architectures.
Lan: Today we start with two numbers, then four strategies.
Minh: Then the services for each layer, one worked example, the traps and a quiz.

## The question {cafe}
[still]
Lan: Minh, imagine a whole AWS Region goes down.
Minh: Every server, every database in it. Gone for hours.
Lan: Right. What does your business ask you first?
Minh: How much data did we lose?
Lan: And then?
Minh: How long until we are back?
Lan: And the third question comes from the finance team.
Minh: How much will it cost to be ready for that?
Lan: Exactly. That is the whole lesson in three questions.
Lan: An exam question gives you an RPO, an RTO and a budget.
Minh: And I pick the cheapest strategy that still meets them.

## RPO and RTO {desk}
[points Two numbers | **RPO:** the most data you can afford to lose | Counted as time before the failure | **RTO:** the longest the system may be down | Until it runs again]
Lan: Let's define the two numbers. First, RPO, the recovery point objective.
Lan: It is the most data you can afford to lose.
Minh: Counted as time, right?
Lan: Yes. An RPO of 1 hour means you can lose the last hour of writes.
Minh: So it looks backwards, from the moment of the failure.
Lan: Right. Now RTO, the recovery time objective.
Lan: It is the longest the system may be down before it runs again.
Minh: So RTO looks forwards, from the failure to the recovery.
Lan: Exactly. RPO is about data. RTO is about time.
Minh: And smaller numbers cost more money.

## Four strategies {classroom}
[card Four strategies | **Backup and restore:** RPO in hours, RTO up to a day | **Pilot light:** RPO in minutes, RTO in tens of minutes | **Warm standby:** RPO in seconds, RTO in minutes | **Multi-site active/active:** RPO and RTO near zero]
Lan: AWS describes four strategies, in order of cost.
Lan: The cheapest is backup and restore.
Minh: Only the backups sit in the recovery Region.
Lan: Right. AWS Backup, snapshots, and copies in S3.
Lan: The RPO is in hours, and the RTO can be up to a day.
Minh: Because you rebuild everything from the backups.
Lan: Next is pilot light.
Lan: The data is replicated to the recovery Region, and it is always on.
Lan: But the app servers are off. Their images and templates are ready.
Minh: Like the small flame in a gas heater. The core burns, the rest is off.
Lan: Nice. The RPO is in minutes, and the RTO in tens of minutes.
Minh: And the third one?
Lan: Warm standby. A scaled-down copy of the whole system is running.
Lan: It can already take a little traffic.
Lan: The RPO is in seconds, and the RTO in minutes.
Minh: And the last one is the most expensive.
Lan: Multi-site, active/active. The full system runs in several Regions.
Lan: They all serve users at once. RPO and RTO are near zero.
[points Pilot light or warm standby? | Pilot light: the data is on, the servers are off | At failover: **start** the servers | Warm standby: a small copy is running | At failover: only **scale up**]
Minh: Pilot light and warm standby sound alike. What is the key difference?
Lan: The exam loves that one. It is what you do at failover.
Lan: In pilot light, you must start the servers first.
Lan: In warm standby, they are already running. You only scale up.
Minh: So warm standby recovers faster, and costs more every day.
Lan: Exactly.

## Services by layer {desk}
[points S3 and RDS | S3: Cross-Region Replication | S3 RTC: most objects in seconds | RTC SLA: 99.9% of objects within 15 minutes | RDS: a cross-Region read replica | Promote it on failover; the RPO is the replica lag]
Lan: Now the services, one layer at a time. Start with S3.
Lan: Cross-Region Replication copies new objects to a bucket in another Region.
Minh: And when the exam wants a time guarantee?
Lan: Then it is S3 Replication Time Control, or RTC.
Lan: It replicates most objects in seconds.
Lan: And its SLA covers 99.9 percent of objects within 15 minutes.
Minh: I have seen 99.99 percent in some study guides.
Lan: So have I. One AWS page says that too, but the SLA itself says 99.9.
Minh: Either way, the exam keyword is 15 minutes, and the answer is RTC.
Lan: Right. Now RDS. You create a read replica in another Region.
Lan: On failover, you promote it to a standalone primary.
Minh: And the RPO is however far the replica was behind.
Lan: Yes, the replica lag.
[points Aurora and DynamoDB | Aurora Global Database: replication typically under 1 second | AWS states an RPO of 1 second | And an RTO of less than 1 minute | DynamoDB global tables: multi-Region, multi-active]
Minh: And Aurora?
Lan: Aurora Global Database. It replicates at the storage layer.
Lan: Typically in under 1 second.
Lan: AWS states an RPO of 1 second, and an RTO of less than 1 minute.
Minh: So when a question says SQL, RPO in seconds, RTO under a minute.
Lan: Aurora Global Database.
Minh: And for NoSQL?
Lan: DynamoDB global tables. Every Region can take writes. That is multi-active.
[points Servers, backups and DNS | AWS DRS: continuous block-level replication | DRS: RPO of seconds, RTO typically 5 to 20 minutes | AWS Backup: copies to another Region or account | Vault Lock: no one can delete them early | Route 53: health checks and failover routing]
Minh: What about servers, on EC2 or in our own data centre?
Lan: AWS Elastic Disaster Recovery, or DRS.
Lan: It copies the disks block by block, all the time.
Lan: The RPO is seconds, and the RTO is typically 5 to 20 minutes.
Minh: Without rewriting the apps.
Lan: Right. It is lift-and-shift DR.
Minh: And for backups?
Lan: AWS Backup, with copies in another Region or another account.
Lan: Add Vault Lock in compliance mode, and no one can delete them early.
Minh: And how do users reach the recovery Region?
Lan: Route 53. Health checks watch the main Region.
Lan: And the failover routing policy sends users to the backup when they fail.

## A worked example {desk}
[flow A small web shop | Users -> Route 53 -> Load balancer -> EC2 app servers -> Aurora MySQL, S3 images]
Lan: Let's design DR for one small web shop.
Minh: It runs on EC2 behind a load balancer.
Minh: The data is in Aurora MySQL, and the product images are in S3.
Lan: The shop must survive the loss of its main Region.
[points Step 1 | RPO 1 hour, RTO 24 hours | Lowest cost | **Backup and restore** | A backup every hour, copied to the recovery Region]
Minh: First requirement: RPO 1 hour, RTO 24 hours, lowest cost.
Lan: Backup and restore.
Lan: AWS Backup takes a backup every hour, and copies it to the other Region.
Minh: And nothing else runs there.
[points Step 2 | The RTO drops to 30 minutes | Rebuilding from backups is too slow | **Pilot light** | Database replicated and on; app servers off, AMIs and templates ready]
Minh: Now the business wants an RTO of 30 minutes.
Lan: Rebuilding everything from backups takes too long.
Lan: So, pilot light. The database is replicated and always on.
Lan: The app servers are off, but their AMIs and a CloudFormation template are ready.
[points Step 3 | RPO in seconds, RTO a few minutes | **Warm standby** | Aurora Global Database: RPO 1 second | S3 CRR with RTC for the images | A small Auto Scaling group already running]
Minh: Then the RPO must be seconds, and the RTO a few minutes.
Lan: Warm standby.
Lan: Aurora Global Database for the data, with an RPO of 1 second.
Lan: S3 replication with RTC for the images.
Lan: And a small Auto Scaling group already running in the recovery Region.
[flow Failover | Health checks fail -> Route 53 failover routing -> Recovery Region -> Auto Scaling scales out]
Minh: And when the main Region fails?
Lan: The Route 53 health checks fail.
Lan: The failover routing policy sends users to the recovery Region.
Lan: And the Auto Scaling group scales out to full size.
[card Step 5: ransomware | Replication copies a deletion too | Keep backups in a separate account | Vault Lock in compliance mode]
Minh: Is that enough?
Lan: Not against ransomware.
Lan: Replication copies a deletion as faithfully as good data.
Minh: So the copy in the other Region is deleted too.
Lan: Right. So AWS Backup also copies to a separate account.
Lan: In a vault with Vault Lock in compliance mode.

## The traps {exam}
[card Exam traps | Lowest cost, RTO of hours: **backup and restore** | RPO in seconds: not periodic snapshots | Same-Region read replica: not Region DR | **Multi-AZ** is HA, not DR across Regions | Ransomware: an **immutable** copy in another account]
Lan: Now the traps. I describe a question, and you name the answer.
Minh: Ready.
Lan: Lowest cost, and an RTO of several hours.
Minh: Backup and restore. Not pilot light.
Lan: An RPO in seconds, and the answer offers periodic snapshots.
Minh: Wrong. A snapshot gives an RPO as long as the time between snapshots.
Lan: A read replica in the same Region, as DR for a Region outage.
Minh: Wrong. It is in the same Region, so it fails with it.
Lan: Multi-AZ as DR across Regions.
Minh: Wrong too. Multi-AZ is high availability inside one Region.
Lan: Pilot light, but the AMIs and templates are not in the recovery Region.
Minh: Then the RTO grows, because you must build them first.
Lan: And ransomware, or a deletion by mistake.
Minh: An immutable copy in another account. Vault Lock or S3 Object Lock.

## Quiz {exam}
Lan: Now a quiz. Answer aloud in the pause, then listen to Minh.
Lan: Question 1. RPO 1 hour, RTO 24 hours, lowest cost. Which strategy? [pause 8]
Minh: Backup and restore, with backups copied to another Region.
Lan: Question 2. MySQL, RPO about 1 second, RTO under 1 minute. [pause 8]
Minh: Aurora Global Database, failing over to a secondary Region.
Lan: Question 3. A global NoSQL app must accept writes in every Region. [pause 8]
Minh: DynamoDB global tables.
Lan: Question 4. Pilot light or warm standby: what differs at failover? [pause 8]
Minh: Pilot light must start the servers. Warm standby only scales up.
Lan: Question 5. 200 on-premises servers, RPO of seconds, no rewrite. [pause 8]
Minh: AWS Elastic Disaster Recovery.
Lan: Question 6. S3 objects in another Region within 15 minutes, with an SLA. [pause 8]
Minh: S3 Replication Time Control, on Cross-Region Replication.
Lan: Question 7. Multi-AZ RDS as DR for a Region outage. Right or wrong? [pause 8]
Minh: Wrong. Multi-AZ protects one Region. Use a cross-Region replica.
Lan: Question 8. Ransomware deleted the data, and the replica too. The fix? [pause 8]
Minh: An immutable copy: AWS Backup in another account with Vault Lock.
Lan: Question 9. How does Route 53 move users to the recovery Region? [pause 8]
Minh: Health checks, with the failover routing policy.
Lan: Question 10. A warm standby must carry the full load. What happens? [pause 8]
Minh: Scale out: raise the Auto Scaling desired capacity, then move DNS.

## Recap {classroom}
[card Recap | RPO: how much data you lose | RTO: how long you are down | Backup and restore, pilot light, warm standby, multi-site | Pick the cheapest one that meets both | Replication is not a backup]
Lan: Let's recap.
Minh: RPO is how much data you lose. RTO is how long you are down.
Lan: Four strategies: backup and restore, pilot light, warm standby, multi-site.
Minh: Pick the cheapest one that still meets both numbers.
Lan: Pilot light starts its servers. Warm standby only scales up.
Minh: Aurora Global Database for SQL, and DynamoDB global tables for NoSQL.
Lan: And replication is not a backup. Keep an immutable copy in another account.
Minh: Thanks, Lan. I'm ready for the practice questions.
Lan: Thanks for watching. See you in the next lesson.
