import sys
import os
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

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("payment")

# --- LA MAGIE DES IMPORTS COMME CHEZ LE CHEF ---
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
# On remonte de 3 niveaux pour chopper 'utils/pb/payment' depuis 'payment/src/app.py'
payment_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment'))
sys.path.insert(0, payment_grpc_path)

import payment_pb2 as pb
import payment_pb2_grpc as pb_grpc
# ----------------------------------------------

class PaymentService(pb_grpc.PaymentServiceServicer):
    def __init__(self):
        # Le petit frigo pour stocker les transactions en attente
        self.transactions = {}

    def Prepare(self, request, context):
        logger.info(f"📦 [PREPARE] Reçu pour la transac {request.transaction_id} - Montant: {request.amount}€")
        # Si le biff est là (>0), on valide le ticket
        if request.amount >= 0:
            self.transactions[request.transaction_id] = "PREPARED"
            return pb.PrepareResponse(ok=True)
        return pb.PrepareResponse(ok=False)

    def Commit(self, request, context):
        if request.id in self.transactions:
            logger.info(f"✅ [COMMIT] Transac {request.id} validée ! Le biff est dans la poche.")
            del self.transactions[request.id]
        return pb.Empty()

    def Abort(self, request, context):
        if request.id in self.transactions:
            logger.info(f"❌ [ABORT] Transac {request.id} annulée. On remballe les churros !")
            del self.transactions[request.id]
        return pb.Empty()

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb_grpc.add_PaymentServiceServicer_to_server(PaymentService(), server)
    # Port 50060, le numéro fétiche
    server.add_insecure_port('[::]:50060')
    logger.debug("💳 Service Payment prêt à charbonner sur le port 50060 (Mode Pro)...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()