import sys
import os
import dns.resolver
from geopy.geocoders import Nominatim
import grpc
from concurrent import futures
import logging

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

# Configuration gRPC pour remonter vers utils/pb
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
utils_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, utils_path)

import transaction_verification_pb2 as pb
import transaction_verification_pb2_grpc as pb_grpc

# Logging setup (comme ton pote)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("transaction_verification")

# --- CONFIGURATION ---
MY_INDEX = 1 # [fraud=0, transaction=1, suggestions=2]
NUM_SERVICES = 3
orders_cache = {} # Cache in-memory : { order_id: { "data": ..., "vc": [0,0,0] } }

geolocator = Nominatim(user_agent="ds_practice_verifier")
DISPOSABLE_DOMAINS = ["yopmail.com", "tempmail.com", "guerrillamail.com"]

# --- FONCTION DE MERGE (Comme ton pote) ---
def merge_and_increment(local_vc, incoming_vc):
    merged = [max(local_vc[i], incoming_vc[i]) for i in range(NUM_SERVICES)]
    merged[MY_INDEX] += 1
    return merged

# --- SERVICE ---
class TransactionVerificationService(pb_grpc.TransactionVerificationServiceServicer):

    def InitOrder(self, request, context):
        """Initialise le cache comme le service Suggestions."""
        orders_cache[request.order_id] = {
            "data": request,
            "vc": [0] * NUM_SERVICES
        }
        logger.debug(f"[OrderID: {request.order_id}] Initialized in cache.")
        return pb.InitOrderResponse(success=True)

    def VerifyItems(self, request, context):
        """Événement (a)"""
        order_id = request.order_id
        if order_id not in orders_cache:
            return pb.VerifyResponse(is_valid=False, message="Order not found")

        entry = orders_cache[order_id]
        entry["vc"] = merge_and_increment(entry["vc"], list(request.vector_clock))

        is_valid = entry["data"].itemsCount > 0
        logger.debug(f"[OrderID: {order_id}] Event (a) - VC: {entry['vc']}")
        return pb.VerifyResponse(is_valid=is_valid, message="Items OK", vector_clock=entry["vc"])

    def VerifyUserData(self, request, context):
        """Événement (b)"""
        order_id = request.order_id
        if order_id not in orders_cache:
            return pb.VerifyResponse(is_valid=False, message="Order not found")

        entry = orders_cache[order_id]
        entry["vc"] = merge_and_increment(entry["vc"], list(request.vector_clock))

        # Logique de vérification simplifiée
        data = entry["data"]
        is_valid = bool(data.name.strip() and "@" in data.contact)

        logger.debug(f"[OrderID: {order_id}] Event (b) - VC: {entry['vc']}")
        return pb.VerifyResponse(is_valid=is_valid, message="User Data OK", vector_clock=entry["vc"])

    def VerifyCreditCard(self, request, context):
        """Événement (c)"""
        order_id = request.order_id
        if order_id not in orders_cache:
            return pb.VerifyResponse(is_valid=False, message="Order not found")

        entry = orders_cache[order_id]
        entry["vc"] = merge_and_increment(entry["vc"], list(request.vector_clock))

        card = entry["data"].creditCard.replace(" ", "")
        is_valid = len(card) >= 13 and entry["data"].cvv.isdigit()

        logger.debug(f"[OrderID: {order_id}] Event (c) - VC: {entry['vc']}")
        return pb.VerifyResponse(is_valid=is_valid, message="Card OK", vector_clock=entry["vc"])

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_TransactionVerificationServiceServicer_to_server(TransactionVerificationService(), server)
    server.add_insecure_port("[::]:50052")
    server.start()
    logger.info("Transaction Server started on port 50052")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()