import sys
import os
import threading
import collections
import logging
from concurrent import futures

import grpc
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer

# Setup OpenTelemetry
trace.set_tracer_provider(TracerProvider())
span_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://observability:4318/v1/traces"))
trace.get_tracer_provider().add_span_processor(span_processor)
GrpcInstrumentorServer().instrument()

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)

import order_queue_pb2 as order_queue_pb2
import order_queue_pb2_grpc as order_queue_grpc

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("order_queue")

class OrderQueueService(order_queue_grpc.OrderQueueServiceServicer):
    """
    Thread-safe in-memory FIFO queue for orders.
    Enqueue appends to the right; Dequeue pops from the left.
    """

    def __init__(self):
        self._queue = collections.deque()
        self._lock = threading.Lock()
        logger.debug("Order Queue Service ready.")

    def Enqueue(self, request, context):
        order = request.order
        with self._lock:
            self._queue.append(order)
            size = len(self._queue)
        logger.info(f"[Queue] Enqueued order '{order.order_id}' | Queue size: {size}")
        return order_queue_pb2.EnqueueResponse(success=True, order_id=order.order_id)

    def Dequeue(self, request, context):
        with self._lock:
            if self._queue:
                order = self._queue.popleft()
                size = len(self._queue)
                logger.info(f"[Queue] Dequeued order '{order.order_id}' | Queue size: {size}")
                return order_queue_pb2.DequeueResponse(success=True, order=order)
        # Queue is empty
        return order_queue_pb2.DequeueResponse(success=False)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_queue_grpc.add_OrderQueueServiceServicer_to_server(OrderQueueService(), server)

    port = "50055"
    server.add_insecure_port("[::]:" + port)
    server.start()
    logger.info(f"Order Queue Server started. Listening on port {port}.")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
