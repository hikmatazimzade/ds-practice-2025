"""
Order Executor Service — Bully Algorithm for Leader Election
============================================================
Each replica is assigned a unique integer REPLICA_ID via environment variable.
The replica with the highest ID among live replicas becomes the leader.

Bully Algorithm flow:
  1. A replica that detects no leader sends Election(my_id) to all higher-ID replicas.
  2. Any higher-ID replica that receives Election responds alive=True and starts its own election.
  3. If no higher-ID replica responds, the sender broadcasts Coordinator(my_id) to all replicas.
  4. All replicas update their leader_id upon receiving Coordinator.
  5. Non-leaders periodically heartbeat the leader; a timeout triggers a new election.
"""

import sys
import os
import threading
import time
from concurrent import futures

import grpc

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_executor_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_executor'))
order_queue_grpc_path    = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_executor_grpc_path)
sys.path.insert(0, order_queue_grpc_path)

import order_executor_pb2 as order_executor_pb2
import order_executor_pb2_grpc as order_executor_grpc
import order_queue_pb2 as order_queue_pb2
import order_queue_pb2_grpc as order_queue_grpc

# Tuning constants
STARTUP_DELAY      = 5    # seconds to wait before triggering the initial election
ELECTION_TIMEOUT   = 2.0  # seconds to wait for a response to an Election message
HEARTBEAT_INTERVAL = 3    # seconds between non-leader heartbeat checks
POLL_INTERVAL      = 2    # seconds between leader's dequeue attempts


# ---------------------------------------------------------------------------
# Bully Algorithm state
# ---------------------------------------------------------------------------

class BullyNode:
    """Encapsulates all state and logic for the Bully leader-election algorithm."""

    def __init__(self, my_id: int, replicas: dict):
        """
        Args:
            my_id:    This replica's integer ID.
            replicas: {id (int) -> "host:port" (str)} for ALL replicas (including self).
        """
        self.my_id    = my_id
        self.replicas = replicas
        self.leader_id: int | None = None

        self._lock                 = threading.Lock()
        self._election_in_progress = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_election(self):
        """
        Start (or join) an election.
        Sends Election to all higher-ID replicas; if none respond, declare victory.
        """
        with self._lock:
            if self._election_in_progress:
                return
            self._election_in_progress = True

        print(f"[Replica {self.my_id}] *** Starting ELECTION ***")

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
                        print(f"[Replica {self.my_id}] Replica {rid} responded OK — deferring.")
                        got_ok = True
                        break
            except Exception:
                print(f"[Replica {self.my_id}] Replica {rid} unreachable.")

        if not got_ok:
            self._declare_victory()

        with self._lock:
            self._election_in_progress = False

    def set_leader(self, leader_id: int):
        """Called when a Coordinator message is received."""
        with self._lock:
            self.leader_id = leader_id
            self._election_in_progress = False

    def is_leader(self) -> bool:
        return self.leader_id == self.my_id

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _declare_victory(self):
        """No higher-ID replica responded — this node wins and broadcasts."""
        with self._lock:
            self.leader_id = self.my_id
        print(f"[Replica {self.my_id}] *** I am the new LEADER (ID={self.my_id}) ***")

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
                pass  # That replica may be down; it will start its own election later


# ---------------------------------------------------------------------------
# gRPC servicer (handles incoming messages from other replicas)
# ---------------------------------------------------------------------------

class OrderExecutorServicer(order_executor_grpc.OrderExecutorServiceServicer):

    def __init__(self, node: BullyNode):
        self.node = node

    def Election(self, request, context):
        """A lower-ID replica is starting an election — we take it over."""
        print(
            f"[Replica {self.node.my_id}] ELECTION received from Replica {request.candidate_id}."
            f" Taking over."
        )
        # Respond immediately, then start our own election in a background thread
        threading.Thread(target=self.node.start_election, daemon=True).start()
        return order_executor_pb2.ElectionResponse(alive=True)

    def Coordinator(self, request, context):
        """A higher-ID replica has won — accept the new leader."""
        print(
            f"[Replica {self.node.my_id}] COORDINATOR: Replica {request.leader_id} is the new leader."
        )
        self.node.set_leader(request.leader_id)
        return order_executor_pb2.CoordinatorResponse(acknowledged=True)

    def Heartbeat(self, request, context):
        """Liveness probe from a non-leader replica."""
        return order_executor_pb2.HeartbeatResponse(alive=True)


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def heartbeat_loop(node: BullyNode):
    """
    Non-leaders periodically send a heartbeat to the current leader.
    If the leader doesn't respond, trigger a new election.
    """
    while True:
        time.sleep(HEARTBEAT_INTERVAL)

        leader = node.leader_id

        if leader is None:
            # No leader known yet — start an election
            threading.Thread(target=node.start_election, daemon=True).start()
            continue

        if leader == node.my_id:
            continue  # I'm the leader; no need to heartbeat myself

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
            print(
                f"[Replica {node.my_id}] Leader {leader} is DOWN — starting new election."
            )
            node.leader_id = None
            threading.Thread(target=node.start_election, daemon=True).start()


def order_processing_loop(node: BullyNode, queue_stub):
    """
    The elected leader polls the order queue and executes each order.
    Non-leaders skip their turn.
    """
    while True:
        time.sleep(POLL_INTERVAL)

        if not node.is_leader():
            continue

        try:
            resp = queue_stub.Dequeue(order_queue_pb2.DequeueRequest(), timeout=3.0)
            if resp.success:
                order = resp.order
                item_names = [item.name for item in order.items]
                print(
                    f"[Replica {node.my_id}] Order is being executed... "
                    f"Order ID: {order.order_id} | Items: {item_names}"
                )
        except Exception:
            # Queue empty or unreachable — silently continue
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def serve():
    my_id = int(os.getenv("REPLICA_ID", "1"))

    # ALL_REPLICAS format: "1:order_executor_1:50056,2:order_executor_2:50056,..."
    all_replicas_env = os.getenv(
        "ALL_REPLICAS",
        f"{my_id}:localhost:50056",
    )
    replicas = {}
    for entry in all_replicas_env.split(","):
        parts = entry.strip().split(":")
        rid, host, port = int(parts[0]), parts[1], parts[2]
        replicas[rid] = f"{host}:{port}"

    print(f"[Replica {my_id}] Starting up. Known replicas: {replicas}")

    node = BullyNode(my_id=my_id, replicas=replicas)

    # --- Start inter-replica gRPC server ---
    grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_executor_grpc.add_OrderExecutorServiceServicer_to_server(
        OrderExecutorServicer(node), grpc_server
    )
    grpc_server.add_insecure_port("[::]:50056")
    grpc_server.start()
    print(f"[Replica {my_id}] Inter-replica gRPC server listening on :50056")

    # --- Connect to the Order Queue ---
    queue_channel = grpc.insecure_channel("order_queue:50055")
    queue_stub = order_queue_grpc.OrderQueueServiceStub(queue_channel)

    # --- Wait for all replicas to start before triggering initial election ---
    print(f"[Replica {my_id}] Waiting {STARTUP_DELAY}s for peers to start...")
    time.sleep(STARTUP_DELAY)

    # Kick off the initial election in a background thread
    threading.Thread(target=node.start_election, daemon=True).start()

    # Start heartbeat monitor
    threading.Thread(target=heartbeat_loop, args=(node,), daemon=True).start()

    # Start order processing loop
    threading.Thread(target=order_processing_loop, args=(node, queue_stub), daemon=True).start()

    grpc_server.wait_for_termination()


if __name__ == '__main__':
    serve()
