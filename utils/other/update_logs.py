import os, glob

def update_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Change all logger.info to logger.debug first
    content = content.replace('logger.info(', 'logger.debug(')

    # Now, restore the ones we want to keep as logger.info
    # Orchestrator
    content = content.replace('logger.debug(f"Received checkout request', 'logger.info(f"Received checkout request')
    content = content.replace('logger.debug(f"[Orchestrator] Phase 5 done. Final VC:', 'logger.info(f"[Orchestrator] Phase 5 done. Final VC:')
    content = content.replace('logger.debug(f"[Orchestrator] Order enqueued:', 'logger.info(f"[Orchestrator] Order enqueued:')
    content = content.replace('logger.debug("Received checkout request', 'logger.info("Received checkout request')

    # Order Executor
    content = content.replace('logger.debug(f"{replica_tag} Processing order', 'logger.info(f"{replica_tag} Processing order')
    content = content.replace('logger.debug(f"{replica_tag} \u2713 Order {order.order_id} COMMITTED successfully', 'logger.info(f"{replica_tag} \u2713 Order {order.order_id} COMMITTED successfully')
    content = content.replace('logger.debug(f"{replica_tag} \u2717 Order {order.order_id} ABORTED', 'logger.info(f"{replica_tag} \u2717 Order {order.order_id} ABORTED')
    content = content.replace('logger.debug(f"{replica_tag} PRE-CHECK FAILED', 'logger.info(f"{replica_tag} PRE-CHECK FAILED')
    content = content.replace('logger.debug(f"[Replica {self.my_id}] *** Starting ELECTION', 'logger.info(f"[Replica {self.my_id}] *** Starting ELECTION')
    content = content.replace('logger.debug(f"[Replica {self.my_id}] *** I am the new LEADER', 'logger.info(f"[Replica {self.my_id}] *** I am the new LEADER')

    # Books Database
    content = content.replace('logger.debug(f"[Replica {self.my_id}] PREPARE {txn}: VOTE-COMMIT', 'logger.info(f"[Replica {self.my_id}] PREPARE {txn}: VOTE-COMMIT')
    content = content.replace('logger.debug(f"[Replica {self.my_id}] COMMIT {txn}', 'logger.info(f"[Replica {self.my_id}] COMMIT {txn}')
    content = content.replace('logger.debug(f"[Replica {self.my_id}] ABORT {txn}', 'logger.info(f"[Replica {self.my_id}] ABORT {txn}')

    # Payment
    content = content.replace('logger.debug(f"\U0001f4e6 [PREPARE]', 'logger.info(f"\U0001f4e6 [PREPARE]')
    content = content.replace('logger.debug(f"\u2705 [COMMIT]', 'logger.info(f"\u2705 [COMMIT]')
    content = content.replace('logger.debug(f"\u274c [ABORT]', 'logger.info(f"\u274c [ABORT]')

    # Queue
    content = content.replace('logger.debug(f"[Queue] Enqueued order', 'logger.info(f"[Queue] Enqueued order')
    content = content.replace('logger.debug(f"[Queue] Dequeued order', 'logger.info(f"[Queue] Dequeued order')

    # Re-enable server started logs
    content = content.replace('logger.debug(f"Books Database listening', 'logger.info(f"Books Database listening')
    content = content.replace('logger.debug("Fraud Detection Server started', 'logger.info("Fraud Detection Server started')
    content = content.replace('logger.debug("Transaction Server started', 'logger.info("Transaction Server started')
    content = content.replace('logger.debug(f"Suggestions gRPC server started', 'logger.info(f"Suggestions gRPC server started')
    content = content.replace('logger.debug(f"Order Queue Server started', 'logger.info(f"Order Queue Server started')
    content = content.replace('logger.debug("\U0001f4b3 Service Payment pr\u00eat a charbonner', 'logger.info("\U0001f4b3 Service Payment pr\u00eat a charbonner')

    # Also handle the rejection cases for Orchestrator to see when it drops a request
    content = content.replace('logger.debug(f"[Orchestrator] Event (b) VerifyUserData \u2014 valid=False', 'logger.info(f"[Orchestrator] Event (b) VerifyUserData \u2014 valid=False')
    content = content.replace('logger.debug(f"[Orchestrator] Event (a) VerifyItems \u2014 valid=False', 'logger.info(f"[Orchestrator] Event (a) VerifyItems \u2014 valid=False')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

for filepath in glob.glob('*/src/app.py', recursive=True):
    update_file(filepath)

print('Log levels updated!')
