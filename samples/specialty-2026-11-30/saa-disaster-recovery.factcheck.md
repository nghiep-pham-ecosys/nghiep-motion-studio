# Fact check: Disaster recovery on AWS: RPO, RTO and the four strategies, explained

Reviewed-by:
Date: 2026-11-30
Narration: f3d16551c17a

Source: the study lesson saa-disaster-recovery.md.
Check each claim against its source, then tick it. The builder found every number below in the file and
line it names; check that the sentence uses it in the same sense.

DRAFT for Nghiep's review (written by the lane on 2026-11-27): nothing is ticked, so the gate's fact check fails
until a person checks each claim, ticks it and writes their name after `Reviewed-by:`.

AWS figures, checked on 2026-11-27 against these public AWS pages:

- Strategies (RPO/RTO of backup and restore, pilot light, warm standby, multi-site): https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp02.html
- S3 RTC (most objects in seconds; 99.9 percent within 15 minutes): https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-time-control.html
  and the RTC SLA (credits below a Monthly 15-minute Replication Percentage of 99.9%): https://aws.amazon.com/s3/sla-rtc/
  Conflict: https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-add-config.html says "99.99 percent of
  objects within 15 minutes (backed by a service-level agreement)". The study book said 99.99%; the video uses the SLA's
  99.9% and names the other figure.
- Aurora Global Database (typical lag under 1 second; RPO of 1 second, RTO of less than 1 minute): https://aws.amazon.com/rds/aurora/global-database/
  and https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html
- DynamoDB global tables (multi-Region, multi-active): https://aws.amazon.com/dynamodb/global-tables/
- AWS DRS (continuous block-level replication, RPO of seconds, RTO typically 5-20 minutes): https://docs.aws.amazon.com/drs/latest/userguide/CloudEndure-Concepts.html
- AWS Backup Vault Lock (compliance mode: no one can delete or alter): https://docs.aws.amazon.com/aws-backup/latest/devguide/vault-lock.html
- Route 53 failover routing with health checks: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-types.html

Dropped from the study book because no AWS page states it: the RDS cross-Region replica's "RPO seconds to minutes"
(the lesson says only that the RPO is the replica lag).


- [ ] "This is lesson 1 of my notes, in the domain of resilient architectures." (intro-6) - source: 1 at saa-disaster-recovery.md:1
- [ ] "An RPO of 1 hour means you can lose the last hour of writes." (rpo-and-rto-6) - source: 1 at saa-disaster-recovery.md:89
- [ ] "RTC SLA: 99.9% of objects within 15 minutes" (services-by-layer panel) - source: 99.9 at saa-disaster-recovery.md:38, 15 at saa-disaster-recovery.md:38
- [ ] "And its SLA covers 99.9 percent of objects within 15 minutes." (services-by-layer-7) - source: 99.9 at saa-disaster-recovery.md:38, 15 at saa-disaster-recovery.md:38
- [ ] "I have seen 99.99 percent in some study guides." (services-by-layer-8) - source: 99.99 at saa-disaster-recovery.md:49
- [ ] "So have I. One AWS page says that too, but the SLA itself says 99.9." (services-by-layer-9) - source: 99.9 at saa-disaster-recovery.md:49
- [ ] "Either way, the exam keyword is 15 minutes, and the answer is RTC." (services-by-layer-10) - source: 15 at saa-disaster-recovery.md:38
- [ ] "Aurora Global Database: replication typically under 1 second" (services-by-layer panel) - source: 1 at saa-disaster-recovery.md:40
- [ ] "AWS states an RPO of 1 second" (services-by-layer panel) - source: 1 at saa-disaster-recovery.md:40
- [ ] "And an RTO of less than 1 minute" (services-by-layer panel) - source: 1 at saa-disaster-recovery.md:40
- [ ] "Typically in under 1 second." (services-by-layer-20) - source: 1 at saa-disaster-recovery.md:40
- [ ] "AWS states an RPO of 1 second, and an RTO of less than 1 minute." (services-by-layer-21) - source: 1 at saa-disaster-recovery.md:40
- [ ] "DRS: RPO of seconds, RTO typically 5 to 20 minutes" (services-by-layer panel) - source: 5 at saa-disaster-recovery.md:42, 20 at saa-disaster-recovery.md:42
- [ ] "Route 53: health checks and failover routing" (services-by-layer panel) - source: 53 at saa-disaster-recovery.md:70
- [ ] "The RPO is seconds, and the RTO is typically 5 to 20 minutes." (services-by-layer-31) - source: 5 at saa-disaster-recovery.md:42, 20 at saa-disaster-recovery.md:42
- [ ] "Route 53." (services-by-layer-39) - source: 53 at saa-disaster-recovery.md:44
- [ ] "Route 53" (a-worked-example panel) - source: 53 at saa-disaster-recovery.md:44
- [ ] "Step 1" (a-worked-example panel) - source: 1 at saa-disaster-recovery.md:1
- [ ] "RPO 1 hour, RTO 24 hours" (a-worked-example panel) - source: 1 at saa-disaster-recovery.md:62, 24 at saa-disaster-recovery.md:62
- [ ] "First requirement: RPO 1 hour, RTO 24 hours, lowest cost." (a-worked-example-5) - source: 1 at saa-disaster-recovery.md:62, 24 at saa-disaster-recovery.md:62
- [ ] "Step 2" (a-worked-example panel) - source: 2 at saa-disaster-recovery.md:64
- [ ] "The RTO drops to 30 minutes" (a-worked-example panel) - source: 30 at saa-disaster-recovery.md:64
- [ ] "Now the business wants an RTO of 30 minutes." (a-worked-example-9) - source: 30 at saa-disaster-recovery.md:64
- [ ] "Step 3" (a-worked-example panel) - source: 3 at saa-disaster-recovery.md:67
- [ ] "Aurora Global Database: RPO 1 second" (a-worked-example panel) - source: 1 at saa-disaster-recovery.md:40
- [ ] "Aurora Global Database for the data, with an RPO of 1 second." (a-worked-example-16) - source: 1 at saa-disaster-recovery.md:40
- [ ] "Route 53 failover routing" (a-worked-example panel) - source: 53 at saa-disaster-recovery.md:44
- [ ] "The Route 53 health checks fail." (a-worked-example-20) - source: 53 at saa-disaster-recovery.md:70
- [ ] "Step 5: ransomware" (a-worked-example card) - source: 5 at saa-disaster-recovery.md:72
- [ ] "Question 1. RPO 1 hour, RTO 24 hours, lowest cost." (quiz-3) - source: 1 at saa-disaster-recovery.md:62, 24 at saa-disaster-recovery.md:62
- [ ] "Question 2. MySQL, RPO about 1 second, RTO under 1 minute." (quiz-6) - source: 2 at saa-disaster-recovery.md:93, 1 at saa-disaster-recovery.md:93
- [ ] "Question 3. A global NoSQL app must accept writes in every Region." (quiz-8) - source: 3 at saa-disaster-recovery.md:96
- [ ] "Question 4. Pilot light or warm standby: what differs at failover?" (quiz-10) - source: 4 at saa-disaster-recovery.md:98
- [ ] "Question 5. 200 on-premises servers, RPO of seconds, no rewrite." (quiz-13) - source: 5 at saa-disaster-recovery.md:42, 200 at saa-disaster-recovery.md:101
- [ ] "Question 6. S3 objects in another Region within 15 minutes, with an SLA." (quiz-15) - source: 6 at saa-disaster-recovery.md:103, 15 at saa-disaster-recovery.md:103
- [ ] "Question 7. Multi-AZ RDS as DR for a Region outage." (quiz-17) - source: 7 at saa-disaster-recovery.md:105
- [ ] "Question 8. Ransomware deleted the data, and the replica too." (quiz-22) - source: 8 at saa-disaster-recovery.md:108
- [ ] "Question 9. How does Route 53 move users to the recovery Region?" (quiz-25) - source: 9 at saa-disaster-recovery.md:112, 53 at saa-disaster-recovery.md:112
- [ ] "Question 10." (quiz-27) - source: 10 at saa-disaster-recovery.md:114
- [ ] The four strategies, their order of cost and their RPO/RTO ranges, as on the card - source: https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp02.html
- [ ] Pilot light must start compute at failover; warm standby already serves reduced traffic and only scales up - source: https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp02.html
- [ ] Replication copies corruption and deletion to the recovery site; an immutable copy in another account is needed - source: https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp02.html
- [ ] Multi-AZ is high availability in one Region; a separate Region protects against a Region outage - source: https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp02.html
- [ ] An RDS cross-Region read replica can be promoted to a standalone primary on failover - source: https://docs.aws.amazon.com/r53recovery/latest/dg/rds-promote-read-replica-block.html
- [ ] The worked example (a self-written web shop) and the quiz are the author's own; their numbers are requirements, not AWS figures - source: saa-disaster-recovery.md
