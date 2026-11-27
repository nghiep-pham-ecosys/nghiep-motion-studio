# Lesson 2026-11-30: SAA-C03 lesson 1 — Disaster recovery and high availability

Track: AWS Certified Solutions Architect – Associate (SAA-C03), exam domain "Design Resilient Architectures".

The question of the lesson: **when a Region or a system fails, how much data do you lose (RPO), how long until it
runs again (RTO), and how much do you pay for that?** An exam question gives an RPO, an RTO and a budget; you pick the
cheapest strategy that still meets them.

The AWS figures below were checked against the AWS pages linked next to them on 2026-11-27.

## Concept

### Two numbers

- **RPO (recovery point objective):** the most data you can afford to lose, counted as time before the failure.
- **RTO (recovery time objective):** the longest the system may be down before it runs again.

### Four strategies

| Strategy | RPO / RTO | What runs in the recovery Region | Cost |
| --- | --- | --- | --- |
| **Backup and restore** | RPO in hours, RTO up to a day | only the backups (AWS Backup, snapshots, S3) | lowest |
| **Pilot light** | RPO in minutes, RTO in tens of minutes | the data, replicated and always on; servers **off** (AMIs and IaC ready) | low |
| **Warm standby** | RPO in seconds, RTO in minutes | a scaled-down but **running** copy of the whole system | medium |
| **Multi-site active/active** | RPO and RTO near zero | the full system in several Regions, all serving traffic | highest |

Source: AWS Well-Architected Framework, REL13-BP02,
https://docs.aws.amazon.com/wellarchitected/latest/framework/rel13-bp02.html

Pilot light keeps the **core** (the data) always on and the rest switched off. Warm standby keeps a **scaled-down but
running** copy of everything. The key exam difference: in pilot light you must **start** servers to fail over; in warm
standby you only **scale up**.

### Services by layer

| Layer | How it reaches another Region | Remember for the exam |
| --- | --- | --- |
| S3 | Cross-Region Replication (CRR); **S3 Replication Time Control (RTC)** replicates most objects in seconds, and its SLA covers 99.9% of objects within 15 minutes | a replication time SLA → RTC |
| RDS | a cross-Region read replica, promoted to a standalone primary on failover | the RPO is the replica lag |
| Aurora | **Aurora Global Database**: replication typically under 1 second; AWS states an RPO of 1 second and an RTO of less than 1 minute | RPO in seconds and RTO under a minute for SQL → Global Database |
| DynamoDB | **global tables**: multi-Region and multi-active | active/active for NoSQL |
| EC2 or on-premises servers | **AWS Elastic Disaster Recovery (DRS)**: continuous block-level replication; an RPO of seconds and an RTO of typically 5 to 20 minutes | lift-and-shift DR |
| Central backups | **AWS Backup**, copied to another Region or account; **Vault Lock** in compliance mode so no one can delete them early | ransomware, compliance |
| DNS | **Route 53** health checks with the failover routing policy | moving users to the recovery Region |

Sources:

- S3 RTC: https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-time-control.html and the RTC SLA,
  https://aws.amazon.com/s3/sla-rtc/ (one other AWS page, and many study guides, say 99.99%; the SLA says 99.9%).
- Aurora Global Database: https://aws.amazon.com/rds/aurora/global-database/ and
  https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html
- DynamoDB global tables: https://aws.amazon.com/dynamodb/global-tables/
- AWS DRS: https://docs.aws.amazon.com/drs/latest/userguide/CloudEndure-Concepts.html
- AWS Backup Vault Lock: https://docs.aws.amazon.com/aws-backup/latest/devguide/vault-lock.html
- Route 53 failover: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-types.html

## Worked example

A self-written example: a small web shop. It runs on EC2 behind a load balancer, with an Aurora MySQL database and its
product images in S3. The shop must survive the loss of its main Region.

1. First requirement: RPO 1 hour, RTO 24 hours, lowest cost. **Backup and restore**: AWS Backup takes a backup every
   hour and copies it to the recovery Region. Nothing else runs there.
2. The business tightens the RTO to 30 minutes. Rebuilding everything from backups takes too long. **Pilot light**:
   the database is replicated and always on in the recovery Region; the app servers are off, with AMIs and a
   CloudFormation template ready to start them.
3. Then the RPO must be seconds and the RTO a few minutes. **Warm standby**: Aurora Global Database for the data (RPO
   of 1 second, RTO under 1 minute for the database), S3 CRR with RTC for the images, and a small Auto Scaling group
   already running in the recovery Region.
4. Failover: the Route 53 health checks on the main Region fail, and the failover routing policy sends users to the
   recovery Region. The Auto Scaling group scales out to full size.
5. Ransomware: replication copies a deletion as faithfully as good data. So AWS Backup also copies the backups to a
   separate account, in a vault with Vault Lock in compliance mode.

## Edge cases

- "Lowest cost" and an RTO of several hours → **backup and restore**, not pilot light.
- An RPO in **seconds**, but the answer offers only periodic snapshots → wrong: snapshots give an RPO as long as the
  time between snapshots.
- A read replica in the **same** Region is not DR for a Region outage.
- **Multi-AZ** is high availability inside one Region, not DR across Regions.
- Pilot light: the AMIs, launch templates and IaC (CloudFormation or Terraform) must already be in the recovery
  Region; without them the RTO grows.
- Ransomware or accidental deletion: you need an **immutable** copy in another account (AWS Backup Vault Lock, S3
  Object Lock), not only replication, because replication also copies the deletion.

## Quiz

1. A company needs an RPO of 1 hour and an RTO of 24 hours, at the lowest cost, for an app on EC2 and RDS. Which
   strategy?
   Answer: Backup and restore (AWS Backup, snapshots copied to another Region). The cheapest strategy that still meets
   an RPO of 1 hour and an RTO of 24 hours.
2. A MySQL-compatible database needs an RPO of about 1 second and an RTO under 1 minute if a whole Region fails.
   Which service?
   Answer: Aurora Global Database (MySQL-compatible), failing over to a secondary Region.
3. A global NoSQL app must accept writes in every Region. Which service?
   Answer: DynamoDB global tables.
4. What is the key difference between pilot light and warm standby at failover?
   Answer: Pilot light must start the app servers (from AMIs or IaC); warm standby is already running and only scales
   up.
5. 200 on-premises servers need DR on AWS with an RPO of seconds, without rewriting the apps. Which service?
   Answer: AWS Elastic Disaster Recovery (DRS).
6. S3 objects must reach another Region within 15 minutes, with an SLA behind it. Which feature?
   Answer: S3 Replication Time Control (RTC) on Cross-Region Replication.
7. A team proposes Multi-AZ RDS as DR for a Region outage. Right or wrong, and why?
   Answer: Wrong. Multi-AZ only protects against the loss of one AZ in the same Region; Region DR needs a cross-Region
   read replica or Aurora Global Database.
8. After a ransomware attack deleted the data, the copy in another Region was gone too. What was the design flaw, and
   the fix?
   Answer: Only replication, no separate immutable copy. Add AWS Backup copies to another account with Vault Lock, or
   S3 Object Lock.
9. What does Route 53 use to send users to the recovery Region automatically?
   Answer: Health checks with the failover routing policy.
10. A warm standby runs a small copy. What must happen at failover to carry the full load?
    Answer: Scale out (raise the Auto Scaling desired capacity, and a larger database instance if needed), then move
    DNS.
