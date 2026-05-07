"""
Order Executor Service — Bully Algorithm for Leader Election + 2PC Coordinator
==============================================================================
Each replica is assigned a unique integer REPLICA_ID via environment variable.
The replica with the highest ID among live replicas becomes the leader.

Bully Algorithm flow:
  1. A replica that detects no leader sends Election(my_id) to all higher-ID replicas.
  2. Any higher-ID replica that receives Election responds alive=True and starts its own election.
  3. If no higher-ID replica responds, the sender broadcasts Coordinator(my_id) to all replicas.
  4. All replicas update their leader_id upon receiving Coordinator.
  5. Non-leaders periodically heartbeat the leader; a timeout triggers a new election.

2PC Coordinator flow (leader only):
  1. Dequeue an order from the order queue.
  2. Read current stock for each book from the books_database primary.
  3. Check that all books have sufficient stock.
  4. Send Prepare to books_database primary AND payment service in parallel.
  5. If both respond with vote_commit=True / ok=True → send Commit to both.
  6. Otherwise → send Abort to both.
"""

import sys
import os
import threading
import time
import uuid
import logging
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

import grpc

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_executor_grpc_path    = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_executor'))
order_queue_grpc_path       = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
books_database_grpc_path    = os.path.abspath(os.path.join(FILE, '../../../utils/pb/books_database'))
payment_grpc_path           = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment'))

sys.path.insert(0, order_executor_grpc_path)
sys.path.insert(0, order_queue_grpc_path)
sys.path.insert(0, books_database_grpc_path)
sys.path.insert(0, payment_grpc_path)

import order_executor_pb2       as order_executor_pb2
import order_executor_pb2_grpc  as order_executor_grpc
import order_queue_pb2          as order_queue_pb2
import order_queue_pb2_grpc     as order_queue_grpc
import books_database_pb2       as books_db_pb2
import books_database_pb2_grpc  as books_db_grpc
import payment_pb2              as payment_pb2
import payment_pb2_grpc         as payment_grpc

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
STARTUP_DELAY       = 5    # seconds to wait before triggering the initial election
ELECTION_TIMEOUT    = 2.0  # seconds to wait for a response to an Election message
HEARTBEAT_INTERVAL  = 3    # seconds between non-leader heartbeat checks
POLL_INTERVAL       = 2    # seconds between leader's dequeue attempts

# External service addresses
DB_PRIMARY_ADDR = "books_database_1:50057"   # primary replica of books_database
PAYMENT_ADDR    = "payment_service:50060"


# ---------------------------------------------------------------------------
# Bully Algorithm state
# ---------------------------------------------------------------------------

class BullyNode:
    """Encapsulates all state and logic for the Bully leader-election algorithm."""

    def __init__(self, my_id: int, replicas: dict):
        self.my_id    = my_id
        self.replicas = replicas
        self.leader_id: int | None = None

        self._lock                 = threading.Lock()
        self._election_in_progress = False

    def start_election(self):
        with self._lock:
            if self._election_in_progress:
                return
            self._election_in_progress = True

        logger.info(f"[Replica {self.my_id}] *** Starting ELECTION ***")

        higher_ids = sorted(rid for rid in self.replicas if rid > self.my_id)
        got_ok = False

        for rid in higher_ids:
            addr = self.replicas[rid]
            try:
                with grpc.insecure_channel(addr) as channel:
                    stub = order_executor_grpc.OrderExecutorServiceStub(channel)
                    resp = stub.Election(
                        order_executor_pb2.ElectionRequest(candidate_id=self.my_id),
                        timeout=ELECTION_TIMEOUT,
                    )
                    if resp.alive:
                        logger.info(f"[Replica {self.my_id}] Replica {rid} responded OK — deferring.")
                        got_ok = True
                        break
            except Exception:
                logger.info(f"[Replica {self.my_id}] Replica {rid} unreachable.")

        if not got_ok:
            self._declare_victory()

        with self._lock:
            self._election_in_progress = False

    def set_leader(self, leader_id: int):
        with self._lock:
            self.leader_id = leader_id
            self._election_in_progress = False

    def is_leader(self) -> bool:
        return self.leader_id == self.my_id

    def _declare_victory(self):
        with self._lock:
            self.leader_id = self.my_id
        logger.info(f"[Replica {self.my_id}] *** I am the new LEADER (ID={self.my_id}) ***")

        for rid, addr in self.replicas.items():
            if rid == self.my_id:
                continue
            try:
                with grpc.insecure_channel(addr) as channel:
                    stub = order_executor_grpc.OrderExecutorServiceStub(channel)
                    stub.Coordinator(
                        order_executor_pb2.CoordinatorRequest(leader_id=self.my_id),
                        timeout=ELECTION_TIMEOUT,
                    )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# gRPC servicer
# ---------------------------------------------------------------------------

class OrderExecutorServicer(order_executor_grpc.OrderExecutorServiceServicer):

    def __init__(self, node: BullyNode):
        self.node = node

    def Election(self, request, context):
        logger.info(
            f"[Replica {self.node.my_id}] ELECTION received from Replica {request.candidate_id}."
            f" Taking over."
        )
        threading.Thread(target=self.node.start_election, daemon=True).start()
        return order_executor_pb2.ElectionResponse(alive=True)

    def Coordinator(self, request, context):
        logger.info(
            f"[Replica {self.node.my_id}] COORDINATOR: Replica {request.leader_id} is the new leader."
        )
        self.node.set_leader(request.leader_id)
        return order_executor_pb2.CoordinatorResponse(acknowledged=True)

    def Heartbeat(self, request, context):
        return order_executor_pb2.HeartbeatResponse(alive=True)


# ---------------------------------------------------------------------------
# 2PC Coordinator logic
# ---------------------------------------------------------------------------

def execute_order_2pc(node: BullyNode, order, db_stub, pay_stub):
    """
    Run the full 2-Phase Commit protocol for a single order.

    Phase 1 — Prepare:
      - Read current stock for every book from the database.
      - Verify that each book has enough stock for the requested quantity.
      - Send Prepare (with stock deltas) to database primary in parallel with
        Prepare (with total amount) to payment service.

    Phase 2 — Commit or Abort:
      - If all participants vote COMMIT → send Commit to all.
      - Otherwise → send Abort to all.
    """
    txn_id = str(uuid.uuid4())
    replica_tag = f"[Replica {node.my_id}][TXN {txn_id[:8]}]"

    logger.info(f"{replica_tag} Processing order {order.order_id} | Items: {[i.name for i in order.items]}")

    # ------------------------------------------------------------------
    # Pre-check: read stock for every item
    # ------------------------------------------------------------------
    stock_changes = []   # list of books_db_pb2.StockChange
    total_amount  = 0.0
    abort_reason  = None

    for item in order.items:
        try:
            read_resp = db_stub.Read(
                books_db_pb2.ReadRequest(title=item.name),
                timeout=3.0,
            )
        except Exception as e:
            abort_reason = f"DB read failed for '{item.name}': {e}"
            break

        if not read_resp.found:
            abort_reason = f"Book '{item.name}' not found in database."
            break

        quantity = getattr(item, 'quantity', 1)  # default 1 if proto has no quantity field

        if read_resp.stock < quantity:
            abort_reason = (
                f"Insufficient stock for '{item.name}': "
                f"requested {quantity}, available {read_resp.stock}."
            )
            break

        stock_changes.append(
            books_db_pb2.StockChange(title=item.name, delta=-quantity)
        )
        # Use item price if available, otherwise assume 0
        total_amount += getattr(item, 'price', 0.0) * quantity

    if abort_reason:
        logger.info(f"{replica_tag} PRE-CHECK FAILED — {abort_reason}. Order aborted (no 2PC needed).")
        return

    # ------------------------------------------------------------------
    # Phase 1: Prepare (parallel)
    # ------------------------------------------------------------------
    logger.info(f"{replica_tag} Phase 1 — sending Prepare to DB and Payment...")

    db_vote     = False
    pay_vote    = False
    db_reason   = ""

    def prepare_db():
        nonlocal db_vote, db_reason
        try:
            resp = db_stub.Prepare(
                books_db_pb2.PrepareRequest(
                    transaction_id=txn_id,
                    changes=stock_changes,
                ),
                timeout=5.0,
            )
            db_vote   = resp.vote_commit
            db_reason = resp.reason
        except Exception as e:
            db_reason = str(e)

    def prepare_payment():
        nonlocal pay_vote
        try:
            resp = pay_stub.Prepare(
                payment_pb2.PaymentRequest(
                    transaction_id=txn_id,
                    amount=total_amount,
                    user_id=getattr(order, 'user_id', 'unknown'),
                ),
                timeout=5.0,
            )
            pay_vote = resp.ok
        except Exception as e:
            logger.info(f"{replica_tag} Payment Prepare error: {e}")

    t_db  = threading.Thread(target=prepare_db)
    t_pay = threading.Thread(target=prepare_payment)
    t_db.start();  t_pay.start()
    t_db.join();   t_pay.join()

    logger.info(
        f"{replica_tag} Prepare votes — DB: {'COMMIT' if db_vote else 'ABORT'} "
        f"({db_reason}), Payment: {'COMMIT' if pay_vote else 'ABORT'}"
    )

    # ------------------------------------------------------------------
    # Phase 2: Commit or Abort (parallel)
    # ------------------------------------------------------------------
    if db_vote and pay_vote:
        logger.info(f"{replica_tag} Phase 2 — both voted COMMIT. Sending Commit...")

        def commit_db():
            try:
                db_stub.Commit(books_db_pb2.CommitRequest(transaction_id=txn_id), timeout=5.0)
                logger.info(f"{replica_tag} DB Commit acknowledged.")
            except Exception as e:
                logger.info(f"{replica_tag} DB Commit error: {e}")

        def commit_payment():
            try:
                pay_stub.Commit(payment_pb2.TransactionId(id=txn_id), timeout=5.0)
                logger.info(f"{replica_tag} Payment Commit acknowledged.")
            except Exception as e:
                logger.info(f"{replica_tag} Payment Commit error: {e}")

        t1 = threading.Thread(target=commit_db)
        t2 = threading.Thread(target=commit_payment)
        t1.start(); t2.start()
        t1.join();  t2.join()

        logger.info(f"{replica_tag} ✓ Order {order.order_id} COMMITTED successfully.")

    else:
        logger.info(f"{replica_tag} Phase 2 — vote ABORT. Sending Abort to all participants...")

        def abort_db():
            try:
                db_stub.Abort(books_db_pb2.AbortRequest(transaction_id=txn_id), timeout=5.0)
                logger.info(f"{replica_tag} DB Abort acknowledged.")
            except Exception as e:
                logger.info(f"{replica_tag} DB Abort error: {e}")

        def abort_payment():
            try:
                pay_stub.Abort(payment_pb2.TransactionId(id=txn_id), timeout=5.0)
                logger.info(f"{replica_tag} Payment Abort acknowledged.")
            except Exception as e:
                logger.info(f"{replica_tag} Payment Abort error: {e}")

        t1 = threading.Thread(target=abort_db)
        t2 = threading.Thread(target=abort_payment)
        t1.start(); t2.start()
        t1.join();  t2.join()

        logger.info(f"{replica_tag} ✗ Order {order.order_id} ABORTED.")


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def heartbeat_loop(node: BullyNode):
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        leader = node.leader_id

        if leader is None:
            threading.Thread(target=node.start_election, daemon=True).start()
            continue

        if leader == node.my_id:
            continue

        addr = node.replicas.get(leader)
        if addr is None:
            node.leader_id = None
            continue

        try:
            with grpc.insecure_channel(addr) as channel:
                stub = order_executor_grpc.OrderExecutorServiceStub(channel)
                stub.Heartbeat(
                    order_executor_pb2.HeartbeatRequest(sender_id=node.my_id),
                    timeout=2.0,
                )
        except Exception:
            logger.info(f"[Replica {node.my_id}] Leader {leader} is DOWN — starting new election.")
            node.leader_id = None
            threading.Thread(target=node.start_election, daemon=True).start()


def order_processing_loop(node: BullyNode, queue_stub, db_stub, pay_stub):
    """
    The elected leader polls the order queue and runs 2PC for each order.
    Non-leaders skip their turn.
    """
    while True:
        time.sleep(POLL_INTERVAL)

        if not node.is_leader():
            continue

        try:
            resp = queue_stub.Dequeue(order_queue_pb2.DequeueRequest(), timeout=3.0)
            if resp.success:
                execute_order_2pc(node, resp.order, db_stub, pay_stub)
        except Exception:
            # Queue empty or unreachable — silently continue
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve():
    my_id = int(os.getenv("REPLICA_ID", "1"))

    all_replicas_env = os.getenv(
        "ALL_REPLICAS",
        f"{my_id}:localhost:50056",
    )
    replicas = {}
    for entry in all_replicas_env.split(","):
        parts = entry.strip().split(":")
        rid, host, port = int(parts[0]), parts[1], parts[2]
        replicas[rid] = f"{host}:{port}"

    logger.info(f"[Replica {my_id}] Starting up. Known replicas: {replicas}")

    node = BullyNode(my_id=my_id, replicas=replicas)

    # --- Start inter-replica gRPC server ---
    grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_executor_grpc.add_OrderExecutorServiceServicer_to_server(
        OrderExecutorServicer(node), grpc_server
    )
    grpc_server.add_insecure_port("[::]:50056")
    grpc_server.start()
    logger.info(f"[Replica {my_id}] Inter-replica gRPC server listening on :50056")

    # --- Connect to external services ---
    queue_channel = grpc.insecure_channel("order_queue:50055")
    queue_stub    = order_queue_grpc.OrderQueueServiceStub(queue_channel)

    db_channel    = grpc.insecure_channel(DB_PRIMARY_ADDR)
    db_stub       = books_db_grpc.BooksDatabaseServiceStub(db_channel)

    pay_channel   = grpc.insecure_channel(PAYMENT_ADDR)
    pay_stub      = payment_grpc.PaymentServiceStub(pay_channel)

    # --- Wait for peers before triggering initial election ---
    logger.info(f"[Replica {my_id}] Waiting {STARTUP_DELAY}s for peers to start...")
    time.sleep(STARTUP_DELAY)

    threading.Thread(target=node.start_election, daemon=True).start()
    threading.Thread(target=heartbeat_loop, args=(node,), daemon=True).start()
    threading.Thread(
        target=order_processing_loop,
        args=(node, queue_stub, db_stub, pay_stub),
        daemon=True,
    ).start()

    grpc_server.wait_for_termination()


if __name__ == '__main__':
    serve()