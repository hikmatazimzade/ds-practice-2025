"""
Books Database Service — Primary-Backup replicated KV store + 2PC participant.

Replication model
-----------------
* Three replicas (REPLICA_ID = 1, 2, 3); replica 1 is the primary.
* Clients send Read/Write to the primary. Read is served locally.
  A Write is forwarded synchronously to every backup (Replicate RPC) before
  the primary acknowledges — strong (synchronous) primary-backup.
* Backups reject direct Write requests; they only accept Replicate (from the
  primary) and the 2PC participant RPCs (Prepare/Commit/Abort).

2PC participant
---------------
* Prepare: lock the listed titles, validate stock, write to a tentative log.
  Vote COMMIT if every change is satisfiable; otherwise VOTE-ABORT and release
  locks. The primary additionally Prepares each backup so they can apply the
  same change atomically.
* Commit: apply the tentative changes, release locks. On the primary, the
  commit is replicated to backups too.
* Abort: drop the tentative changes, release locks (idempotent).
"""

import os
import sys
import threading
from concurrent import futures

import grpc

# Make the generated pb modules importable.
FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")
PB_PATH = os.path.abspath(os.path.join(FILE, "../../../utils/pb/books_database"))
sys.path.insert(0, PB_PATH)

import books_database_pb2 as pb
import books_database_pb2_grpc as pb_grpc

# ---- Configuration -------------------------------------------------------

PRIMARY_ID = 1                     # replica 1 is the primary
PORT       = "50057"               # all replicas listen on the same port
RPC_TIMEOUT = 3.0                  # seconds for inter-replica RPCs

# Initial book inventory — every replica boots with the same data.
INITIAL_STOCK = {
    "Learning Python":         10,
    "JavaScript - The Good Parts": 5,
    "Domain-Driven Design":    7,
    "Clean Code":              8,
}


# ---- Service implementation ---------------------------------------------

class BooksDatabaseService(pb_grpc.BooksDatabaseServiceServicer):
    """Single-instance replica state. All mutating paths take ``_lock``."""

    def __init__(self, my_id: int, replicas: dict):
        self.my_id    = my_id
        self.replicas = replicas                 # {id: "host:port"}
        self.is_primary = (my_id == PRIMARY_ID)

        self._lock     = threading.Lock()
        self._store    = dict(INITIAL_STOCK)     # title -> stock
        self._locked   = set()                   # titles held by an active txn
        self._pending  = {}                      # txn_id -> [(title, delta), ...]

        role = "PRIMARY" if self.is_primary else "BACKUP"
        print(f"[Replica {my_id}/{role}] ready. Peers: {replicas}")

    # ------------------------------------------------------------------
    # Normal operations
    # ------------------------------------------------------------------

    def Read(self, request, context):
        # Reads are served locally — every replica has an up-to-date copy.
        with self._lock:
            stock = self._store.get(request.title)
        found = stock is not None
        print(f"[Replica {self.my_id}] Read('{request.title}') -> "
              f"{'stock=' + str(stock) if found else 'NOT FOUND'}")
        return pb.ReadResponse(found=found, stock=stock or 0)

    def Write(self, request, context):
        # Only the primary accepts client writes.
        if not self.is_primary:
            print(f"[Replica {self.my_id}] Rejecting Write — not primary.")
            return pb.WriteResponse(success=False)

        # Apply locally, then replicate to every backup before acknowledging.
        with self._lock:
            self._store[request.title] = request.stock
        print(f"[Primary] Write '{request.title}' = {request.stock}")

        ok = self._replicate_to_backups(request)
        return pb.WriteResponse(success=ok)

    def Replicate(self, request, context):
        # Inter-replica RPC: primary tells a backup to mirror a write.
        with self._lock:
            self._store[request.title] = request.stock
        print(f"[Replica {self.my_id}] Replicated '{request.title}' = {request.stock}")
        return pb.WriteResponse(success=True)

    # ------------------------------------------------------------------
    # 2PC participant operations
    # ------------------------------------------------------------------

    def Prepare(self, request, context):
        """Vote COMMIT iff every change is valid AND no title is locked."""
        txn = request.transaction_id

        with self._lock:
            # If we already prepared this txn, just answer the same vote.
            if txn in self._pending:
                return pb.PrepareResponse(vote_commit=True, reason="already prepared")

            titles = [c.title for c in request.changes]

            # Conflict check: any of these titles already in another txn?
            busy = [t for t in titles if t in self._locked]
            if busy:
                return pb.PrepareResponse(
                    vote_commit=False,
                    reason=f"titles locked by another txn: {busy}",
                )

            # Validity check: stock cannot go below zero after applying deltas.
            for change in request.changes:
                current = self._store.get(change.title, 0)
                if current + change.delta < 0:
                    return pb.PrepareResponse(
                        vote_commit=False,
                        reason=f"insufficient stock for '{change.title}'",
                    )

            # Reserve: lock titles and remember the tentative change set.
            self._locked.update(titles)
            self._pending[txn] = [(c.title, c.delta) for c in request.changes]

        # Primary fans the Prepare out to backups — all must agree.
        if self.is_primary:
            if not self._fanout_prepare(request):
                # A backup voted abort; release locally and propagate abort.
                self._abort_local(txn)
                self._fanout_abort(txn)
                return pb.PrepareResponse(
                    vote_commit=False,
                    reason="a backup voted abort",
                )

        print(f"[Replica {self.my_id}] PREPARE {txn}: VOTE-COMMIT")
        return pb.PrepareResponse(vote_commit=True, reason="")

    def Commit(self, request, context):
        txn = request.transaction_id
        self._commit_local(txn)
        if self.is_primary:
            self._fanout_commit(txn)
        return pb.CommitResponse(success=True)

    def Abort(self, request, context):
        txn = request.transaction_id
        self._abort_local(txn)
        if self.is_primary:
            self._fanout_abort(txn)
        return pb.AbortResponse(success=True)

    # ------------------------------------------------------------------
    # Local txn helpers
    # ------------------------------------------------------------------

    def _commit_local(self, txn: str):
        with self._lock:
            changes = self._pending.pop(txn, None)
            if changes is None:
                return  # unknown / already finished
            for title, delta in changes:
                self._store[title] = self._store.get(title, 0) + delta
                self._locked.discard(title)
        print(f"[Replica {self.my_id}] COMMIT {txn}: {changes}")

    def _abort_local(self, txn: str):
        with self._lock:
            changes = self._pending.pop(txn, None)
            if changes is None:
                return
            for title, _ in changes:
                self._locked.discard(title)
        print(f"[Replica {self.my_id}] ABORT {txn}")

    # ------------------------------------------------------------------
    # Inter-replica fan-out (primary only)
    # ------------------------------------------------------------------

    def _backup_stubs(self):
        """Yield (rid, stub) for every backup peer."""
        for rid, addr in self.replicas.items():
            if rid == self.my_id:
                continue
            channel = grpc.insecure_channel(addr)
            yield rid, pb_grpc.BooksDatabaseServiceStub(channel)

    def _replicate_to_backups(self, write_req) -> bool:
        for rid, stub in self._backup_stubs():
            try:
                stub.Replicate(write_req, timeout=RPC_TIMEOUT)
            except Exception as e:
                print(f"[Primary] Replicate -> {rid} FAILED: {e}")
                return False
        return True

    def _fanout_prepare(self, prepare_req) -> bool:
        for rid, stub in self._backup_stubs():
            try:
                resp = stub.Prepare(prepare_req, timeout=RPC_TIMEOUT)
                if not resp.vote_commit:
                    print(f"[Primary] Backup {rid} voted ABORT: {resp.reason}")
                    return False
            except Exception as e:
                print(f"[Primary] Prepare -> {rid} FAILED: {e}")
                return False
        return True

    def _fanout_commit(self, txn: str):
        for rid, stub in self._backup_stubs():
            try:
                stub.Commit(pb.CommitRequest(transaction_id=txn),
                            timeout=RPC_TIMEOUT)
            except Exception as e:
                print(f"[Primary] Commit -> {rid} FAILED: {e}")

    def _fanout_abort(self, txn: str):
        for rid, stub in self._backup_stubs():
            try:
                stub.Abort(pb.AbortRequest(transaction_id=txn),
                           timeout=RPC_TIMEOUT)
            except Exception as e:
                print(f"[Primary] Abort -> {rid} FAILED: {e}")


# ---- Entry point ---------------------------------------------------------

def parse_replicas(env: str) -> dict:
    # "1:books_database_1:50057,2:books_database_2:50057,..."
    out = {}
    for entry in env.split(","):
        rid, host, port = entry.strip().split(":")
        out[int(rid)] = f"{host}:{port}"
    return out


def serve():
    my_id    = int(os.getenv("REPLICA_ID", "1"))
    replicas = parse_replicas(os.getenv(
        "ALL_REPLICAS",
        f"{my_id}:localhost:{PORT}",
    ))

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_BooksDatabaseServiceServicer_to_server(
        BooksDatabaseService(my_id, replicas), server,
    )
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    print(f"[Replica {my_id}] Books Database listening on :{PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
