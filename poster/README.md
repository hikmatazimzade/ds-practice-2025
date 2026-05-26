# MediDrone: Emergency Response Management System via Drones

**Team Members:** Tofig Movsumov, Arthur Marie, Hikmat Azim
**Topic:** Distributed Commit Protocols (Lecture 11 Challenge)

---

## 1. Background & System Model

### Background
Our application, **MediDrone**, is a rapid medical response system. When an emergency is reported (e.g., cardiac arrest), the system must coordinate the dispatch of a drone equipped with a defibrillator.
**The Critical Challenge:** The operation must be atomic. We cannot charge the patient's insurance without the certainty that a drone has been successfully reserved and locked for this specific mission.

### System Model
The system is based on a modern **Microservices** architecture:
- **Coordinator:** Incident Management Service.
- **Participants:** Fleet Service (Drones) and Payment/Insurance Service.
- **Communication:** Synchronous or asynchronous network requests between entities.

---

## 2. Challenge Analysis: Distributed Commit

To ensure all services agree on the success or failure of a transaction, we explore three mechanisms.

### Mechanism 1: Two-Phase Commit (2PC)
*Origin: Lecture 11*
- **Operation:** 1. **Voting Phase:** The coordinator asks the Fleet and Payment services if they are ready.
  2. **Decision Phase:** If both reply "Yes", the coordinator sends the validation order (Commit).
- **Major Drawback:** It is a **blocking** protocol. If the coordinator fails after the voting phase, the drones remain locked unnecessarily, which is unacceptable in an emergency situation.

### Mechanism 2: Saga Pattern
*Origin: Independent Research (Non-lecture)*
- **Operation:** The transaction is broken down into local steps. If the Fleet service reserves a drone but the Payment fails, the system triggers a **compensating action** (the drone is released immediately via a reverse request).
- **Advantage:** Non-blocking. The system remains highly available and fluid.

### Mechanism 3: Three-Phase Commit (3PC)
*Origin: Technical Complement*
- **Operation:** Adds a "Pre-Commit" step to eliminate the blocking issue of the 2PC.
- **Advantage:** More robust to failures than the 2PC.
- **Drawback:** Slower due to the increased number of network messages required.

---

## 3. Comparison & Justification

| Criterion | 2PC (Course) | Saga (Research) | 3PC |
| :--- | :--- | :--- | :--- |
| **Atomicity** | Strict (ACID) | Eventual | Strict |
| **Availability** | Low (Blocking) | Very High | High |
| **Performance** | Medium | Excellent | Low (Latency) |

### Final Choice Justification
For **MediDrone**, we have chosen the **Saga Pattern**.
In an emergency context, **availability** is more important than immediate consistency. If a drone cannot be reserved, the Saga allows the system to try another service provider without blocking the entire system, unlike the 2PC.

---

## 4. References
- Flores, H. (2025). *Lecture 11: Modern system architectures*. University of Tartu.
- Richardson, C. (2018). *Microservices Patterns: With examples in Java (Saga Pattern)*.
