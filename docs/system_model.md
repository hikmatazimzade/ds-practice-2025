# System Model — Distributed Bookshop (Checkpoint 2)

## Overview

The system is a distributed online bookshop built as a set of microservices
communicating via gRPC. A central orchestrator coordinates the full order
processing flow, enforcing causal event ordering through vector clocks and
delegating order execution to a fault-tolerant replica group.

## Services

| Service | Port | Role | Vector Clock Index |
|---|---|---|---|
| Fraud Detection | 50051 | Events d, e | 0 |
| Transaction Verification | 50052 | Events a, b, c | 1 |
| Suggestions | 50053 | Event f | 2 |
| Order Queue | 50055 | FIFO queue | — |
| Order Executor | 50056 | Leader execution | — |

## Vector Clocks

Each service maintains a local vector clock of size 3, indexed as:
- Index 0 — Fraud Detection
- Index 1 — Transaction Verification
- Index 2 — Suggestions

On every event, the receiving service merges the incoming clock with its
local clock (taking the max of each slot) and increments its own slot before
returning the updated clock to the orchestrator. The orchestrator then merges
the returned clock into its own shared clock before calling the next event.
This establishes a mathematically provable causal ordering of all events
across services.

## Event Dependency Order

InitOrder:          [0, 0, 0]
Event a (tx):       [0, 1, 0]
Event b (tx):       [0, 2, 0]
Event c (tx):       [0, 3, 0]
Event d (fraud):    [1, 2, 0]
After merge c+d:    [1, 3, 0]
Event e (fraud):    [2, 3, 0]
Event f (suggest):  [2, 3, 1]


## Leader Election — Bully Algorithm

Three Order Executor replicas (IDs 1, 2, 3) elect a leader on startup:

1. All replicas send ELECTION messages to replicas with higher IDs
2. If a higher-ID replica responds OK, the sender defers
3. If no higher-ID replica responds, the replica declares itself leader
4. The leader broadcasts COORDINATOR to all other replicas
5. Only the leader dequeues and processes orders from the Order Queue

In a healthy system Replica 3 always wins as it has the highest ID.
This ensures mutual exclusion over order processing — only one replica
executes orders at any time.

## Fault Handling

- If any verification event (a–e) returns a failure, the orchestrator
  immediately rejects the order with HTTP 400 and stops processing
- gRPC connection errors are caught per-event and treated as failures
- The Order Queue persists independently of the executor replicas so
  orders are not lost if an executor crashes
- When the transaction service was killed during testing, the orchestrator
  correctly caught the DNS resolution error and returned HTTP 400

## Communication

All inter-service communication uses gRPC with Protocol Buffers over
insecure channels within the Docker network. The frontend communicates
with the orchestrator via REST (HTTP/JSON) on port 8081. All services
run as Docker containers orchestrated by docker-compose, sharing a
single internal network for hostname-based service discovery.